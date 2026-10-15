"""
t2_check.py — Compare OpenAP synthetic fuel vs BTS Form 41 Schedule T2.

Public API
----------
t2_fuel_per_nm(t2_df) -> dict
    Parse a BTS T2 DataFrame and return per-aircraft-type fuel efficiency
    (kg/nm), mean stage length (nm), and departure count.

compare_openap(t2_df, bts_to_openap, fuel_fn) -> dict
    Compare T2 fuel efficiency against OpenAP estimates.  Uses dependency
    injection for the per-flight fuel function so tests do not need OpenAP.

Unit conversion constants (exported for tests)
----------------------------------------------
_GAL_TO_L      : float  — US gallon to litre (3.785411784 L/gal, NIST SP 811)
_JET_A_KG_PER_L: float  — Jet-A density assumed 0.80 kg/L (mid-range;
                            ASTM D1655 range 0.775–0.840 kg/L)
_SM_TO_NM      : float  — statute mile to nautical mile conversion factor
                            (1/1.150779)

T2 data notes
-------------
BTS Form 41 Schedule T2 column AIRCRAFT_FUELS_921 reports US gallons of
fuel consumed.  This field is only populated on SERVICE_CLASS == 'Z' rows
('All services' aggregate); F/L/etc rows have NaN.  Using non-Z rows gives
no fuel data.

Filter applied by t2_fuel_per_nm:
  1. SERVICE_CLASS == 'Z'              (all-services aggregate rows only)
  2. AIRCRAFT_CONFIG == 1             (passenger-configured aircraft)
  3. AIRCRAFT_FUELS_921 > 0 and not NaN
  4. REV_ACRFT_MILES_FLOWN_410 > 0   (at least some revenue miles)

If no rows survive filter (1), a ValueError is raised — this prevents
a silent empty return when the caller passes the wrong SERVICE_CLASS.

Aggregation:
  Per aircraft type (AIRCRAFT_TYPE as integer, normalised to 3-digit str):
    fuel_gal   = sum(AIRCRAFT_FUELS_921)         [US gallons]
    fuel_kg    = fuel_gal * _GAL_TO_L * _JET_A_KG_PER_L
    miles      = sum(REV_ACRFT_MILES_FLOWN_410)  [statute miles]
    nm         = miles * _SM_TO_NM               [nautical miles]
    departures = sum(REV_ACRFT_DEP_PERF_510)     [departure count]
    kg_per_nm  = fuel_kg / nm
    stage_nm   = nm / departures

Limitations
-----------
* T2 AIRCRAFT_FUELS_921 includes taxi fuel and APU fuel, which OpenAP
  synthetic trajectories do not model.  This accounts for ~5–15 % of block
  fuel (ICAO Doc 9889).
* Non-revenue flying (ferry/positioning) inflates T2 fuel relative to
  scheduled departures.
* Jet-A density 0.80 kg/L is the midpoint; ±3.2 % from ASTM range.
* OpenAP uses WRAP kinematic model with fixed mass = 0.8 × MTOW; real
  average payload varies.

Reference
---------
BTS TranStats Schedule T2 URL:
  https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FIH&QO_fu146_anzr=Nv4%20Pn44vr4%20f7zzn4B
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Unit conversion constants
# ---------------------------------------------------------------------------

#: US gallon to litre (NIST SP 811, 2008 — exact definition)
_GAL_TO_L: float = 3.785411784

#: Jet-A density assumed 0.80 kg/L (reviewer probe; ASTM D1655 range 0.775–0.840)
_JET_A_KG_PER_L: float = 0.80

#: Statute mile to nautical mile: 1 sm = 1609.344 m; 1 nm = 1852 m
#: Factor = 1/1.150779... = 1609.344/1852
_SM_TO_NM: float = 1.0 / 1.150779


# ---------------------------------------------------------------------------
# t2_fuel_per_nm
# ---------------------------------------------------------------------------

def t2_fuel_per_nm(
    t2_df: pd.DataFrame,
) -> Dict[str, Dict]:
    """Parse BTS T2 DataFrame and return per-type fuel efficiency.

    Parameters
    ----------
    t2_df:
        DataFrame loaded from a BTS Schedule T2 CSV.  Must have columns:
        SERVICE_CLASS, AIRCRAFT_CONFIG, AIRCRAFT_TYPE,
        AIRCRAFT_FUELS_921, REV_ACRFT_MILES_FLOWN_410, REV_ACRFT_DEP_PERF_510.

    Returns
    -------
    dict keyed by BTS aircraft type code (zero-padded 3-digit string), value:
      {
        "kg_per_nm"   : float,  # total fuel (kg) / total nm flown
        "stage_nm"    : float,  # mean stage length = total nm / departures
        "departures"  : float,  # total revenue aircraft departures performed
      }

    Raises
    ------
    ValueError
        If no SERVICE_CLASS == 'Z' rows are found in t2_df.  This prevents
        a silent empty return when the wrong SERVICE_CLASS is passed.
    """
    # Step 1: select Z-class rows only (all-services aggregate)
    z_mask = t2_df["SERVICE_CLASS"] == "Z"
    if not z_mask.any():
        raise ValueError(
            "No SERVICE_CLASS == 'Z' rows found in t2_df.  "
            "AIRCRAFT_FUELS_921 is only populated on Z (all-services) rows; "
            "F, L, and other SERVICE_CLASS values have NaN fuel.  "
            "Pass the full T2 DataFrame (with Z rows) rather than a filtered subset."
        )

    # Step 2: apply all filters
    df = t2_df[z_mask].copy()
    df = df[df["AIRCRAFT_CONFIG"] == 1].copy()

    # Coerce numeric columns
    for col in ("AIRCRAFT_FUELS_921", "REV_ACRFT_MILES_FLOWN_410",
                "REV_ACRFT_DEP_PERF_510", "AIRCRAFT_TYPE"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df[df["AIRCRAFT_FUELS_921"].notna() & (df["AIRCRAFT_FUELS_921"] > 0)].copy()
    df = df[df["REV_ACRFT_MILES_FLOWN_410"] > 0].copy()

    # Normalise AIRCRAFT_TYPE to zero-padded 3-digit string
    df["AIRCRAFT_TYPE"] = df["AIRCRAFT_TYPE"].apply(
        lambda x: str(int(x)).zfill(3)
    )

    # Step 3: aggregate per aircraft type
    result: Dict[str, Dict] = {}
    for ac_type, group in df.groupby("AIRCRAFT_TYPE"):
        fuel_gal = float(group["AIRCRAFT_FUELS_921"].sum())
        miles = float(group["REV_ACRFT_MILES_FLOWN_410"].sum())
        departures = float(group["REV_ACRFT_DEP_PERF_510"].sum())

        fuel_kg = fuel_gal * _GAL_TO_L * _JET_A_KG_PER_L
        nm = miles * _SM_TO_NM
        kg_per_nm = fuel_kg / nm
        stage_nm = nm / departures

        result[str(ac_type)] = {
            "kg_per_nm": kg_per_nm,
            "stage_nm": stage_nm,
            "departures": departures,
        }

    return result


# ---------------------------------------------------------------------------
# compare_openap
# ---------------------------------------------------------------------------

def compare_openap(
    t2_df: pd.DataFrame,
    bts_to_openap: Dict[str, Optional[str]],
    fuel_fn: Callable,
    mass_fraction: float = 0.8,
) -> Dict[str, Dict]:
    """Compare BTS T2 fuel efficiency with OpenAP synthetic estimates.

    For each aircraft type present in both ``t2_df`` (after filtering) and
    ``bts_to_openap`` (with a non-None OpenAP code), calls ``fuel_fn`` at the
    T2 mean stage length and computes the ratio OpenAP/T2.

    Parameters
    ----------
    t2_df:
        DataFrame from BTS Schedule T2.  Passed directly to t2_fuel_per_nm.
    bts_to_openap:
        Mapping from BTS aircraft type code (str) to OpenAP code (str) or
        None (unmapped).  Types mapped to None are silently skipped.
    fuel_fn:
        Callable with signature ``(ac: str, distance_nm: float,
        mass_fraction: float) -> dict`` where the dict has key ``fuel_kg``.
        For real use, pass ``openap_perf.flight_fuel_nox``.
        For tests, pass a fake/mock.
    mass_fraction:
        Mass as fraction of MTOW passed to fuel_fn.  Default 0.8.

    Returns
    -------
    dict keyed by BTS aircraft type code (str), value:
      {
        "t2_kg_per_nm"    : float,  # BTS T2 fuel efficiency
        "openap_kg_per_nm": float,  # OpenAP fuel efficiency at T2 stage length
        "ratio"           : float,  # openap_kg_per_nm / t2_kg_per_nm
        "stage_nm"        : float,  # mean stage length (from T2)
        "departures"      : float,  # departure count (from T2)
      }

    Notes
    -----
    * fuel_fn is called exactly once per aircraft type (at the mean stage_nm).
    * Types that raise an exception in fuel_fn are silently skipped and not
      included in the result (e.g. aircraft not available in OpenAP).
    * This is a MAGNITUDE CHECK, not a calibration.  OpenAP is not tuned to T2.
    """
    t2_metrics = t2_fuel_per_nm(t2_df)

    result: Dict[str, Dict] = {}
    for bts_code, entry in t2_metrics.items():
        openap_code = bts_to_openap.get(bts_code)
        if openap_code is None:
            # Not in mapping or mapped to None (unmapped)
            continue

        stage_nm = entry["stage_nm"]
        try:
            fuel_result = fuel_fn(openap_code, stage_nm, mass_fraction)
        except Exception:
            # Aircraft not available in OpenAP, or other model error; skip
            continue

        openap_kg_per_nm = fuel_result["fuel_kg"] / stage_nm
        t2_kg_per_nm = entry["kg_per_nm"]
        ratio = openap_kg_per_nm / t2_kg_per_nm

        result[bts_code] = {
            "t2_kg_per_nm": t2_kg_per_nm,
            "openap_kg_per_nm": openap_kg_per_nm,
            "ratio": ratio,
            "stage_nm": stage_nm,
            "departures": entry["departures"],
        }

    return result
