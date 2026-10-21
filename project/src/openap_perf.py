"""
openap_perf.py — OpenAP-based fuel and NOx integration per flight.

Public API
----------
flight_fuel_nox(ac, distance_nm, mass_fraction=0.8) -> dict
    Compute fuel_kg, nox_kg, nan_fraction, n_points, trajectory_model,
    fuel_model, and used_synonym for a single flight using OpenAP's
    FlightGenerator + FuelFlow + Emission models.

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

Synonym fallback
----------------
Some aircraft types in fleet.BTS_TO_OPENAP have no native WRAP kinematic model
or no native drag polar in OpenAP.  flight_fuel_nox handles this transparently:

  * FlightGenerator: try native (use_synonym=False) first; on ValueError,
    retry with use_synonym=True.  The resolved WRAP model is recorded from
    the WRAP synonym table and returned as trajectory_model.
  * FuelFlow: try native (use_synonym=False) first; on ValueError, retry with
    use_synonym=True.  The resolved drag-polar model is looked up from the
    OpenAP drag-polar synonym table and returned as fuel_model.
  * used_synonym (bool): True if either trajectory_model != ac or
    fuel_model != ac.

A synonym means OpenAP substitutes another aircraft's model; the substitution
is a modelling approximation.  The returned dict makes it visible.

Synonym resolution tables (from openap/data/*/synonym.csv, openap v2.6.2):
  WRAP synonyms:  a20n->a320, a21n->a320, a359->b789, b38m->b737, b39m->b737,
                  b772->b77w, b773->b77w, crj9->e190, e145->e190, e170->e190,
                  e75l->e190  (and others not targeted by BTS_TO_OPENAP)
  Drag synonyms:  a21n->a20n, b39m->b38m, b763->b752, b773->b77w,
                  crj9->e75l, e145->e75l, e170->e75l  (and others)

Design note: _get_trajectory() and _climb_descent_distances() are thin wrappers
exposed at module level so tests can monkey-patch them without patching internal
closure state.  They now accept an optional *use_synonym* parameter.
"""
from __future__ import annotations

import glob
import os
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

# ---------------------------------------------------------------------------
# Synonym resolution helpers (read from installed openap data at import time).
# These tables let us determine *which model was actually used* without having
# to inspect openap internals at runtime.
# ---------------------------------------------------------------------------

def _find_openap_data_dir() -> str:
    """Locate the openap data directory from the installed package."""
    import openap as _openap
    return os.path.join(os.path.dirname(os.path.realpath(_openap.__file__)), "data")


def _load_synonym_table(sub_path: str) -> Dict[str, str]:
    """Return {orig: new} synonym mapping from an openap synonym CSV."""
    fpath = os.path.join(_find_openap_data_dir(), sub_path)
    df = pd.read_csv(fpath)
    return dict(zip(df["orig"].str.lower(), df["new"].str.lower()))


def _load_available_set(sub_dir: str, extension: str) -> frozenset:
    """Return the set of aircraft codes for which native files exist."""
    pattern = os.path.join(_find_openap_data_dir(), sub_dir, f"*{extension}")
    codes = {os.path.basename(p)[: -len(extension)].lower() for p in glob.glob(pattern)}
    codes.discard("_synonym")
    return frozenset(codes)


# Pre-load at import time (cost: two small CSV reads + glob, ~1 ms).
_WRAP_SYNONYMS: Dict[str, str] = _load_synonym_table("wrap/_synonym.csv")
_DRAG_SYNONYMS: Dict[str, str] = _load_synonym_table("dragpolar/_synonym.csv")
_WRAP_AVAILABLE: frozenset = _load_available_set("wrap", ".txt")
_DRAG_AVAILABLE: frozenset = _load_available_set("dragpolar", ".yml")


def _resolve_wrap_model(ac: str) -> tuple[str, bool]:
    """Return (resolved_wrap_model, used_synonym) for *ac*.

    Returns the aircraft code as-is with used_synonym=False if a native WRAP
    file exists; otherwise looks up the synonym table and returns
    (synonym_target, True).  Returns (ac, False) if no synonym is found --
    the caller will then raise when FlightGenerator is constructed.
    """
    ac_lc = ac.lower()
    if ac_lc in _WRAP_AVAILABLE:
        return ac_lc, False
    resolved = _WRAP_SYNONYMS.get(ac_lc)
    if resolved is not None:
        return resolved, True
    return ac_lc, False  # will raise when FlightGenerator is called


def _resolve_drag_model(ac: str) -> tuple[str, bool]:
    """Return (resolved_drag_polar_model, used_synonym) for *ac*.

    Returns the aircraft code as-is with used_synonym=False if a native drag
    polar file exists; otherwise looks up the synonym table and returns
    (synonym_target, True).
    """
    ac_lc = ac.lower()
    if ac_lc in _DRAG_AVAILABLE:
        return ac_lc, False
    resolved = _DRAG_SYNONYMS.get(ac_lc)
    if resolved is not None:
        return resolved, True
    return ac_lc, False  # will raise when FuelFlow is called


# ---------------------------------------------------------------------------
# Trajectory wrappers (exposed for monkeypatching in tests).
# ---------------------------------------------------------------------------

def _get_trajectory(ac: str, cruise_m: float, use_synonym: bool = False) -> pd.DataFrame:
    """Generate a complete trajectory DataFrame for *ac* with cruise_m metres of cruise.

    Exposed at module level so tests can monkeypatch it.

    Parameters
    ----------
    ac : str
        ICAO aircraft type code (e.g. ``'a320'``).
    cruise_m : float
        Cruise distance in metres.
    use_synonym : bool
        If True, allow OpenAP to substitute a synonym WRAP model.

    Returns
    -------
    pd.DataFrame
        Columns: t (s), altitude (ft), groundspeed (kt), vertical_rate (ft/min).
    """
    gen = FlightGenerator(ac, use_synonym=use_synonym)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        df = gen.complete(dt=_DT, range_cr=float(cruise_m), random=False)
    return df


def _climb_descent_distances(ac: str, use_synonym: bool = False) -> tuple[float, float]:
    """Return (climb_m, descent_m) for *ac* with default WRAP parameters."""
    gen = FlightGenerator(ac, use_synonym=use_synonym)
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
        ``'b738'``, ``'a333'``).  If the native WRAP or drag-polar model is
        unavailable, a synonym is used as a fallback (see module docstring).
    distance_nm : float
        Target total great-circle distance in nautical miles.  Must be > 0.
        The generator cannot produce exactly this distance because climb/descent
        distances are fixed by WRAP defaults; actual distance may differ by
        <= 1 %.
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
            Typically <= 0.005 (one ground roll row out of ~300+).
        n_points : int
            Number of trajectory rows (time steps) used.
        trajectory_model : str
            The WRAP model actually used for the trajectory.  Equals *ac*
            when no synonym was needed; otherwise the synonym target
            (e.g. ``'b737'`` for ``'b38m'``).
        fuel_model : str
            The drag-polar model actually used inside FuelFlow.  Equals *ac*
            when no synonym was needed; otherwise the synonym target
            (e.g. ``'b38m'`` for ``'b39m'``, ``'b752'`` for ``'b763'``).
        used_synonym : bool
            True if either trajectory_model != ac or fuel_model != ac.
            A modelling approximation flag -- callers should note this when
            interpreting results for synonym aircraft types.

    Raises
    ------
    ValueError
        If nan_fraction > 0.02 (more than 2 % of rows are NaN), which
        indicates a physics model failure beyond isolated ground-roll points.
    ValueError
        If distance_nm <= 0 or mass_fraction not in (0, 1].
    """
    if distance_nm <= 0:
        raise ValueError(f"distance_nm must be positive; got {distance_nm!r}")
    if not (0.0 < mass_fraction <= 1.0):
        raise ValueError(
            f"mass_fraction must be in (0, 1]; got {mass_fraction!r}"
        )

    ac_lc = ac.lower()

    # -- Resolve synonym models from lookup tables --------------------------
    # These are pure-dict lookups that tell us which model *will* be used.
    # The FlightGenerator and FuelFlow calls below confirm this at runtime.
    traj_model, traj_needs_synonym = _resolve_wrap_model(ac_lc)
    fuel_model, fuel_needs_synonym = _resolve_drag_model(ac_lc)
    used_synonym: bool = traj_needs_synonym or fuel_needs_synonym

    # -- Aircraft properties -----------------------------------------------
    # prop.aircraft also uses a synonym table; pass use_synonym=True to handle
    # types whose YAML file may be absent (belt-and-suspenders).
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        acdict = prop.aircraft(ac_lc, use_synonym=True)
    mtow: float = float(acdict["limits"]["MTOW"])
    mass: float = mass_fraction * mtow

    # -- Compute cruise distance that achieves the target total -------------
    target_m: float = distance_nm * 1852.0
    # Try native FlightGenerator first; fall back to synonym on ValueError.
    try:
        climb_m, descent_m = _climb_descent_distances(ac_lc, use_synonym=False)
    except ValueError:
        climb_m, descent_m = _climb_descent_distances(ac_lc, use_synonym=True)
        used_synonym = True
    cruise_m: float = max(target_m - climb_m - descent_m, _MIN_CRUISE_M)

    # -- Generate trajectory -----------------------------------------------
    # Try native FlightGenerator first; fall back to synonym on ValueError.
    try:
        df: pd.DataFrame = _get_trajectory(ac_lc, cruise_m, use_synonym=False)
    except ValueError:
        df = _get_trajectory(ac_lc, cruise_m, use_synonym=True)
        used_synonym = True

    n_points: int = len(df)

    # -- Extract state columns ---------------------------------------------
    # Units verified from openap.gen source:
    #   groundspeed: kt   (v / aero.kts)
    #   altitude:    ft   (h / aero.ft)
    #   vertical_rate: ft/min  (vs / aero.fpm)
    tas: np.ndarray = df["groundspeed"].values.astype(float)   # kt
    alt: np.ndarray = df["altitude"].values.astype(float)      # ft
    vs: np.ndarray = df["vertical_rate"].values.astype(float)  # ft/min
    t: np.ndarray = df["t"].values.astype(float)               # s

    # -- Fuel flow (kg/s) -- try native FuelFlow, fall back to synonym -----
    try:
        ff_model = FuelFlow(ac_lc, use_synonym=False)
    except (ValueError, FileNotFoundError):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            ff_model = FuelFlow(ac_lc, use_synonym=True)
        used_synonym = True

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        fuelflow: np.ndarray = ff_model.enroute(
            mass=mass, tas=tas, alt=alt, vs=vs
        )

    # -- NaN handling -------------------------------------------------------
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

    # -- NOx rate (g/s) -----------------------------------------------------
    # Substitute tas=1 kt where tas=0 to avoid divide-by-zero in _fl2sl.
    # These are ground-roll rows that contribute ~0 to the emission integral.
    tas_safe: np.ndarray = np.where(tas == 0.0, 1.0, tas)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        em_model = Emission(ac_lc, use_synonym=True)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        nox_rate: np.ndarray = em_model.nox(
            ffac=fuelflow, tas=tas_safe, alt=alt
        )

    # -- Integrate with actual time steps -----------------------------------
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
        "trajectory_model": traj_model,
        "fuel_model": fuel_model,
        "used_synonym": used_synonym,
    }


def trajectory_for_distance(ac: str, distance_nm: float) -> pd.DataFrame:
    """Return the synthetic trajectory that ``flight_fuel_nox`` integrates.

    Public helper (NW-28) so callers can draw the same climb/cruise/descent
    profile that the fuel number comes from, without reaching into private
    functions.  Uses the same cruise-distance computation and the same
    native-then-synonym fallback as ``flight_fuel_nox``.

    Returns a DataFrame with (at least) columns ``t`` (s), ``altitude`` (ft),
    ``groundspeed`` (kt) and ``s`` (m flown since brake release).
    """
    if distance_nm <= 0:
        raise ValueError(f"distance_nm must be positive; got {distance_nm!r}")
    ac_lc = ac.lower()
    try:
        climb_m, descent_m = _climb_descent_distances(ac_lc, use_synonym=False)
    except ValueError:
        climb_m, descent_m = _climb_descent_distances(ac_lc, use_synonym=True)
    cruise_m = max(distance_nm * 1852.0 - climb_m - descent_m, _MIN_CRUISE_M)
    try:
        return _get_trajectory(ac_lc, cruise_m, use_synonym=False)
    except ValueError:
        return _get_trajectory(ac_lc, cruise_m, use_synonym=True)
