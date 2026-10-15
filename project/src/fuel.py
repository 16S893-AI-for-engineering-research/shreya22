"""
fuel.py — Simple fuel proxy for hub-and-spoke vs point-to-point comparison.

Formula
-------
    fuel_kg(distance_nm, cls) = COEFFICIENTS_KG_PER_NM[cls] * distance_nm

where cls ∈ {'short', 'medium', 'long'} is derived from the stage length via
stage_length_class().

Stage-length class boundaries (documented here and tested exactly)
------------------------------------------------------------------
    short  :  0 ≤ distance_nm < 500
    medium :  500 ≤ distance_nm ≤ 1500
    long   :  distance_nm > 1500

Boundary convention: 500 is medium (not short); 1500 is medium (not long).

Units
-----
All distance arguments are in nautical miles (nm).  The function name and
parameter name include the unit suffix ``_nm`` to make this explicit.
Output is labelled ``_kg``.

⚠️  PLACEHOLDER COEFFICIENTS — NOT SOURCED PERFORMANCE DATA ⚠️
-----------------------------------------------------------------
The values in COEFFICIENTS_KG_PER_NM are *illustrative proxies* chosen to
give order-of-magnitude plausible fuel burn rates.  They are NOT derived
from aircraft manufacturer data, ICAO performance tables, or any empirical
dataset.  Do NOT use these numbers for any operational, regulatory, or
publication purpose.

Rough rationale for placeholder values
(purely for internal cross-checks; not a citation):
  short  ~70 kg/nm  — small narrow-body at ~10 t/hr, ~450 kt → ~22 kg/nm per
                       seat is roughly 70 total; order-of-magnitude stand-in.
  medium ~90 kg/nm  — medium narrow-body slightly heavier load.
  long   ~120 kg/nm — wide-body heavier fuel load per nm at cruise.
Again: these numbers are placeholders.
"""
from __future__ import annotations

from typing import Dict

# ── Stage-length class boundaries (nm) ─────────────────────────────────────
# short  :  [0, SHORT_MAX_NM)        i.e. distance_nm < 500
# medium :  [SHORT_MAX_NM, LONG_MIN_NM]  i.e. 500 ≤ distance_nm ≤ 1500
# long   :  (LONG_MIN_NM, ∞)         i.e. distance_nm > 1500
SHORT_MAX_NM: float = 500.0   # exclusive upper bound for 'short'
LONG_MIN_NM: float = 1500.0   # inclusive upper bound for 'medium' / exclusive lower for 'long'

# ── PLACEHOLDER coefficient table ──────────────────────────────────────────
# See module docstring for the ⚠️  disclaimer.
# Keys: stage-length class name.  Values: kg of fuel per nautical mile.
COEFFICIENTS_KG_PER_NM: Dict[str, float] = {
    "short":  70.0,   # ⚠️ illustrative placeholder
    "medium": 90.0,   # ⚠️ illustrative placeholder
    "long":  120.0,   # ⚠️ illustrative placeholder
}


def stage_length_class(distance_nm: float) -> str:
    """Return the stage-length class for *distance_nm* nautical miles.

    Parameters
    ----------
    distance_nm : float
        Great-circle stage length in nautical miles.  Must be ≥ 0.

    Returns
    -------
    str
        One of ``'short'``, ``'medium'``, or ``'long'``.

    Raises
    ------
    ValueError
        If *distance_nm* is negative.

    Class boundaries (exact)
    ------------------------
    * short  :  0 ≤ distance_nm < 500
    * medium :  500 ≤ distance_nm ≤ 1500
    * long   :  distance_nm > 1500
    """
    if distance_nm < 0.0:
        raise ValueError(
            f"distance_nm must be non-negative; got {distance_nm!r} (negative distance is physically meaningless)"
        )
    if distance_nm < SHORT_MAX_NM:          # < 500  → short
        return "short"
    if distance_nm <= LONG_MIN_NM:          # 500 ≤ d ≤ 1500 → medium
        return "medium"
    return "long"                           # > 1500 → long


def fuel_kg(distance_nm: float, cls: str) -> float:
    """Compute proxy fuel burn in kilograms for a flight leg.

    Formula: ``fuel_kg = COEFFICIENTS_KG_PER_NM[cls] * distance_nm``

    Parameters
    ----------
    distance_nm : float
        Stage length in nautical miles.  Must be ≥ 0.
    cls : str
        Stage-length class: ``'short'``, ``'medium'``, or ``'long'``.
        Use :func:`stage_length_class` to derive this from a distance.

    Returns
    -------
    float
        Proxy fuel burn in kilograms.  Zero when *distance_nm* is zero.
        Always non-negative.

    Raises
    ------
    ValueError
        If *distance_nm* is negative.
    KeyError
        If *cls* is not a recognised stage-length class.

    Notes
    -----
    ⚠️  The underlying coefficients are ILLUSTRATIVE PLACEHOLDERS.
    See module docstring for full disclaimer.
    """
    if distance_nm < 0.0:
        raise ValueError(
            f"distance_nm must be non-negative; got {distance_nm!r} (negative distance is physically meaningless)"
        )
    # KeyError is intentionally propagated if cls is unrecognised;
    # callers may also catch ValueError per the public contract.
    coeff = COEFFICIENTS_KG_PER_NM[cls]    # raises KeyError for unknown class
    return coeff * distance_nm
