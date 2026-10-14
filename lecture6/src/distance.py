"""
lecture6/src/distance.py — Haversine great-circle distance in nautical miles.

Public API
----------
haversine_nm(lat1, lon1, lat2, lon2) -> float
    Great-circle distance in nautical miles between two points on a sphere,
    using the haversine formula and mean Earth radius R = 3440.065 nm.

distance_nm(code_a, code_b) -> float
    Convenience wrapper: looks up IATA-like airport codes in AIRPORT_COORDS
    and returns haversine_nm for the pair.  Raises KeyError (with a clear
    message including the unknown code) for any unrecognised code.

Constants
---------
EARTH_RADIUS_NM : float
    Mean Earth radius in nautical miles = 3440.065 nm.
    Source: this value is derived from the mean Earth radius of 6371.0 km
    (IAU 2012 / GRS80 mean) divided by 1.852 km/nm:
        6371.0 / 1.852 ≈ 3440.065 nm.
    The classic geodesy definition of the nautical mile (1 arcminute of
    arc) implies R = 21600/(2π) = 3437.75 nm; the difference (~0.067%)
    amounts to ≈ 0.1 nm on a 162 nm leg.

AIRPORT_COORDS : dict[str, tuple[float, float]]
    Mapping of IATA-like airport code -> (latitude_deg, longitude_deg).
    Coordinates are airport reference points (ARP) from OurAirports.com
    (open data, derived from FAA NASR).  Covers the six airports that
    appear in lecture6/tests/fixtures/tiny_demand.csv:
        BOS, JFK, ORD, SFO, DEN, PHX.

References
----------
* Haversine formula: Sinnott, R.W. (1984) "Virtues of the Haversine".
  Sky and Telescope 68(2):158.
* Airport coordinates: https://ourairports.com (KBOS, KJFK, KORD, KSFO, KDEN, KPHX)
* Earth radius: IAU 2012 nominal mean radius 6371.0 km; 1 nm := 1852 m exactly (BIPM).
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_NM: float = 3440.065
"""Mean Earth radius in nautical miles (6371.0 km / 1.852 km·nm⁻¹ = 3440.065 nm)."""

# Airport reference-point coordinates (WGS-84 decimal degrees).
# Source: OurAirports.com open database, derived from FAA NASR.
# Covers the airports present in lecture6/tests/fixtures/tiny_demand.csv.
AIRPORT_COORDS: dict[str, tuple[float, float]] = {
    "BOS": (42.3643, -71.0052),   # Logan International, Boston MA
    "JFK": (40.6398, -73.7789),   # John F. Kennedy International, New York NY
    "ORD": (41.9786, -87.9072),   # O'Hare International, Chicago IL
    "SFO": (37.6188, -122.3750),  # San Francisco International, San Francisco CA
    "DEN": (39.8561, -104.6737),  # Denver International, Denver CO
    "PHX": (33.4373, -112.0078),  # Phoenix Sky Harbor International, Phoenix AZ
}


# ---------------------------------------------------------------------------
# Core formula
# ---------------------------------------------------------------------------

def haversine_nm(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
    *,
    radius_nm: float = EARTH_RADIUS_NM,
) -> float:
    """Return the great-circle distance in nautical miles between two points.

    Uses the haversine formula, which is numerically stable for both very
    short and antipodal distances.

    Parameters
    ----------
    lat1, lon1 : float
        Latitude and longitude of the first point in decimal degrees.
    lat2, lon2 : float
        Latitude and longitude of the second point in decimal degrees.
    radius_nm : float, optional
        Sphere radius in nautical miles.  Defaults to EARTH_RADIUS_NM = 3440.065 nm.

    Returns
    -------
    float
        Great-circle distance in nautical miles, in [0, π·radius_nm].

    Notes
    -----
    The haversine formula:
        a = sin²(Δφ/2) + cos φ₁ · cos φ₂ · sin²(Δλ/2)
        d = 2 · R · arcsin(√a)
    where φ = latitude, λ = longitude, R = sphere radius.

    Formula reference: Sinnott (1984), Sky and Telescope 68(2):158.
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)

    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    # clamp to [0, 1] to guard against floating-point overflow at exactly antipodal points
    a = min(1.0, max(0.0, a))
    return radius_nm * 2.0 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Convenience lookup
# ---------------------------------------------------------------------------

def distance_nm(code_a: str, code_b: str) -> float:
    """Return the great-circle distance in nautical miles between two airports.

    Parameters
    ----------
    code_a, code_b : str
        IATA-like airport codes (case-sensitive; use uppercase, e.g. 'BOS').

    Returns
    -------
    float
        Great-circle distance in nautical miles.

    Raises
    ------
    KeyError
        If either *code_a* or *code_b* is not present in AIRPORT_COORDS.
        The error message names the unknown code explicitly.

    Examples
    --------
    >>> round(distance_nm("BOS", "JFK"), 1)
    162.1
    >>> distance_nm("BOS", "BOS")
    0.0
    """
    if code_a not in AIRPORT_COORDS:
        raise KeyError(
            f"Unknown airport code: '{code_a}'. "
            f"Available codes: {sorted(AIRPORT_COORDS)}"
        )
    if code_b not in AIRPORT_COORDS:
        raise KeyError(
            f"Unknown airport code: '{code_b}'. "
            f"Available codes: {sorted(AIRPORT_COORDS)}"
        )
    lat1, lon1 = AIRPORT_COORDS[code_a]
    lat2, lon2 = AIRPORT_COORDS[code_b]
    return haversine_nm(lat1, lon1, lat2, lon2)
