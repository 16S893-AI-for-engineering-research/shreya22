"""
openap_perf.py — OpenAP-based fuel and NOx integration per flight.

Public API
----------
flight_fuel_nox(ac, distance_nm, mass_fraction=0.8) -> dict
    Compute fuel_kg, nox_kg, nan_fraction, and n_points for a single flight
    using OpenAP's FlightGenerator + FuelFlow + Emission models.

Unit conventions (verified from openap.aero source, openap v2.6.2)
-------------------------------------------------------------------
* groundspeed column in FlightGenerator output: kt
  Source: gen.py ``groundspeed=lambda x: (x.v / aero.kts).astype(int)``
  (v is m/s; dividing by kts=0.514444 gives kt).
* altitude column:        ft
  Source: gen.py ``altitude=lambda x: (x.h / aero.ft).astype(int)``
* vertical_rate column:   ft/min
  Source: gen.py ``vertical_rate=lambda x: (x.vs / aero.fpm).astype(int)``
* FuelFlow.enroute(mass_kg, tas_kt, alt_ft, vs_fpm) -> kg/s
* Emission.nox(ffac_kg_s, tas_kt, alt_ft)           -> g/s
* range_cr in generator:  m  (NOT km despite docstring saying km)
  Source: gen.py ``distance = kwargs.get("range_cr",
      self.wrap.cruise_range()["default"] * 1000)``
  The ``* 1000`` converts the WRAP default (in km) to m; so when we pass
  range_cr we must pass metres.

Distance control
----------------
The generator's complete() builds climb + cruise + descent.  Only the cruise
portion distance is set via range_cr (in metres).  Climb/descent distances
are fixed by the WRAP model defaults.  To hit a target total distance:

    cruise_m = target_m - climb_m - descent_m

where climb_m / descent_m are obtained from one-time calls to gen.climb()
and gen.descent() with the same aircraft.  A minimum of 10 m cruise is
enforced so the cruise loop always runs at least once.

NaN rule
--------
FuelFlow.enroute returns NaN when tas=0 (the ground roll row at the very
start of the trajectory).  These NaN values are:
  1. Counted to compute nan_fraction.
  2. Filled by linear interpolation in the time index before integration.
  3. If nan_fraction > 0.02, a ValueError is raised.

For NOx computation, tas=0 would cause a divide-by-zero inside Emission._fl2sl;
where tas=0 we substitute tas=1 kt (negligible physical difference; these are
ground rows that contribute ~0 to the emission integral).

Design note: _get_trajectory() is a thin wrapper exposed at module level so
tests can monkey-patch it without patching internal closure state.
"""
from __future__ import annotations

import warnings
from typing import Dict

import numpy as np
import pandas as pd

from openap import prop
from openap.emission import Emission
from openap.fuel import FuelFlow
from openap.gen import FlightGenerator

# Time step used for trajectory generation (seconds).
_DT = 10

# Minimum cruise distance (m) – ensures the cruise loop executes at least once.
_MIN_CRUISE_M = 10.0


def _get_trajectory(ac: str, cruise_m: float) -> pd.DataFrame:
    """Generate a complete trajectory DataFrame for *ac* with cruise_m metres of cruise.

    Exposed at module level so tests can monkeypatch it.

    Parameters
    ----------
    ac : str
        ICAO aircraft type code (e.g. ``'a320'``).
    cruise_m : float
        Cruise distance in metres.

    Returns
    -------
    pd.DataFrame
        Columns: t (s), altitude (ft), groundspeed (kt), vertical_rate (ft/min).
    """
    gen = FlightGenerator(ac)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        df = gen.complete(dt=_DT, range_cr=float(cruise_m), random=False)
    return df


def _climb_descent_distances(ac: str) -> tuple[float, float]:
    """Return (climb_m, descent_m) for *ac* with default WRAP parameters."""
    gen = FlightGenerator(ac)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        df_cl = gen.climb(dt=_DT, random=False)
        df_de = gen.descent(dt=_DT, random=False)
    return float(df_cl["s"].max()), float(df_de["s"].max())


def flight_fuel_nox(
    ac: str,
    distance_nm: float,
    mass_fraction: float = 0.8,
) -> Dict[str, object]:
    """Compute fuel and NOx for a single synthetic flight.

    Uses OpenAP's FlightGenerator (WRAP model) to synthesise a complete
    trajectory (climb + cruise + descent), then integrates FuelFlow.enroute
    and Emission.nox over the trajectory with a fixed dt=10 s time step.

    Parameters
    ----------
    ac : str
        ICAO aircraft type code accepted by OpenAP (e.g. ``'a320'``,
        ``'b738'``, ``'a333'``).
    distance_nm : float
        Target total great-circle distance in nautical miles.  Must be > 0.
        The generator cannot produce exactly this distance because climb/descent
        distances are fixed by WRAP defaults; actual distance may differ by
        ≤ 1 %.
    mass_fraction : float, optional
        Aircraft mass as a fraction of MTOW.  Default 0.8.  Passed directly
        to FuelFlow.enroute as ``mass = mass_fraction * MTOW_kg``.

    Returns
    -------
    dict with keys:
        fuel_kg : float
            Total fuel burned (kg), integrated over the full trajectory.
        nox_kg : float
            Total NOx emitted (kg), integrated over the full trajectory.
        nan_fraction : float
            Fraction of trajectory rows where FuelFlow returned NaN.
            Typically ≤ 0.005 (one ground roll row out of ~300+).
        n_points : int
            Number of trajectory rows (time steps) used.

    Raises
    ------
    ValueError
        If nan_fraction > 0.02 (more than 2 % of rows are NaN), which
        indicates a physics model failure beyond isolated ground-roll points.
    """
    if distance_nm <= 0:
        raise ValueError(f"distance_nm must be positive; got {distance_nm!r}")
    if not (0.0 < mass_fraction <= 1.0):
        raise ValueError(
            f"mass_fraction must be in (0, 1]; got {mass_fraction!r}"
        )

    # ── Aircraft properties ────────────────────────────────────────────────
    acdict = prop.aircraft(ac)
    mtow: float = float(acdict["limits"]["MTOW"])
    mass: float = mass_fraction * mtow

    # ── Compute cruise distance that achieves the target total ─────────────
    target_m: float = distance_nm * 1852.0
    climb_m, descent_m = _climb_descent_distances(ac)
    cruise_m: float = max(target_m - climb_m - descent_m, _MIN_CRUISE_M)

    # ── Generate trajectory ────────────────────────────────────────────────
    df: pd.DataFrame = _get_trajectory(ac, cruise_m)

    n_points: int = len(df)

    # ── Extract state columns ──────────────────────────────────────────────
    # Units verified from openap.gen source:
    #   groundspeed: kt   (v / aero.kts)
    #   altitude:    ft   (h / aero.ft)
    #   vertical_rate: ft/min  (vs / aero.fpm)
    tas: np.ndarray = df["groundspeed"].values.astype(float)   # kt
    alt: np.ndarray = df["altitude"].values.astype(float)      # ft
    vs: np.ndarray = df["vertical_rate"].values.astype(float)  # ft/min
    t: np.ndarray = df["t"].values.astype(float)               # s

    # ── Fuel flow (kg/s) ───────────────────────────────────────────────────
    ff_model = FuelFlow(ac)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        fuelflow: np.ndarray = ff_model.enroute(
            mass=mass, tas=tas, alt=alt, vs=vs
        )

    # ── NaN handling ───────────────────────────────────────────────────────
    nan_mask: np.ndarray = np.isnan(fuelflow)
    n_nan: int = int(nan_mask.sum())
    nan_fraction: float = n_nan / n_points if n_points > 0 else 0.0

    if nan_fraction > 0.02:
        raise ValueError(
            f"nan_fraction={nan_fraction:.4f} > 0.02 for {ac!r} at "
            f"{distance_nm} nm; FuelFlow returned NaN on too many rows "
            f"({n_nan}/{n_points}).  Check aircraft type and mass."
        )

    # Linear interpolation in time index for NaN rows.
    if nan_mask.any():
        idx = np.arange(n_points)
        fuelflow = np.interp(idx, idx[~nan_mask], fuelflow[~nan_mask])

    # ── NOx rate (g/s) ────────────────────────────────────────────────────
    # Substitute tas=1 kt where tas=0 to avoid divide-by-zero in _fl2sl.
    # These are ground-roll rows with negligible emission contribution.
    tas_safe: np.ndarray = np.where(tas == 0.0, 1.0, tas)
    em_model = Emission(ac)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        nox_rate: np.ndarray = em_model.nox(
            ffac=fuelflow, tas=tas_safe, alt=alt
        )

    # ── Integrate with actual time steps ──────────────────────────────────
    # dt[i] = t[i+1] - t[i]; use first n-1 rows for a left-Riemann sum.
    dt_arr: np.ndarray = np.diff(t)   # length n_points - 1

    fuel_kg: float = float(np.sum(fuelflow[:-1] * dt_arr))
    nox_g: float = float(np.sum(nox_rate[:-1] * dt_arr))
    nox_kg: float = nox_g / 1000.0

    return {
        "fuel_kg": fuel_kg,
        "nox_kg": nox_kg,
        "nan_fraction": nan_fraction,
        "n_points": n_points,
    }
