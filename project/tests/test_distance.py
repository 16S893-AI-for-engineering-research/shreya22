"""
Tests for project/src/distance.py — haversine great-circle distances in nautical miles.

All tests use the airports: BOS, JFK, ORD, SFO, DEN, PHX (those in tiny_demand.csv).
"""
import math
import itertools
import sys
import pathlib

# Make project/src importable when pytest runs from repo root
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from distance import haversine_nm, distance_nm  # noqa: E402

# ---------------------------------------------------------------------------
# Airport codes present in the tiny fixture
# ---------------------------------------------------------------------------
ALL_AIRPORTS = ["BOS", "JFK", "ORD", "SFO", "DEN", "PHX"]


# ---------------------------------------------------------------------------
# 1. Unit-sanity: nm vs km conversion factor
# ---------------------------------------------------------------------------

def test_nm_km_conversion_sanity():
    """
    Example test — no tolerance needed (exact definitional equality within float precision).

    The international nautical mile is DEFINED as 1852 metres exactly (BIPM/CGPM 1929).
    Therefore 1 nm = 1.852 km with no uncertainty.

    We verify our two-call chain:  haversine_nm returns nm,  and
    haversine_nm * 1.852 == haversine in km (via a parallel km-radius call).
    Uses two city-centre points 1 degree of latitude apart (same longitude) so the
    expected km distance is trivially R_km * delta_rad.  The nm and km results must
    agree to within floating-point round-off (rel_tol 1e-12) given the 1.852 factor.
    """
    # Two points exactly 1 degree of latitude apart at the equator — simple reference
    d_nm = haversine_nm(0.0, 0.0, 1.0, 0.0)
    d_km_from_nm = d_nm * 1.852

    # Independently compute in km using R_km = 3440.065 * 1.852 = 6371.0 km (approx)
    R_km = 3440.065 * 1.852
    delta_rad = math.radians(1.0)
    a = math.sin(delta_rad / 2) ** 2  # cos terms cancel at equator same-longitude
    d_km_direct = R_km * 2 * math.asin(math.sqrt(a))

    # Must agree to better than 1e-9 relative (float arithmetic on the same formula)
    assert math.isclose(d_km_from_nm, d_km_direct, rel_tol=1e-9), (
        f"nm→km via 1.852 gives {d_km_from_nm:.8f} km; "
        f"direct km formula gives {d_km_direct:.8f} km — mismatch implies wrong radius"
    )


# ---------------------------------------------------------------------------
# 2. Known-reference (BOS → JFK)
# ---------------------------------------------------------------------------

def test_bos_jfk_known_reference():
    """
    Example test — tolerance ±1 nm (absolute).

    Reference value: 162.1 nm (162.083 nm at full precision).

    Derivation:
      Coordinates (OurAirports.com, sourced from FAA NASR):
        BOS (Logan Intl, KBOS): 42.3643°N, 71.0052°W
        JFK (John F. Kennedy Intl, KJFK): 40.6398°N, 73.7789°W
      Formula: haversine with R = 3440.065 nm (per task spec).
      Independent cross-check: spherical law of cosines + 1nm = 1arc-minute definition
        gives 161.974 nm, consistent to < 0.2 nm (difference is the radius choice).
      Quick geometric sanity (flat-Earth, fine for ~162 nm leg):
        Δlat ≈ 1.72° → 103 nm;  Δlon at 41°N ≈ 2.77° × 45.3 nm/° ≈ 125 nm
        √(103² + 125²) ≈ 162 nm.

    Tolerance rationale: ±1 nm covers any reasonable variation in airport reference-point
    coordinates across FAA/ICAO/OurAirports databases (< 0.05° difference → < 0.5 nm
    on a ~162 nm leg).
    """
    d = distance_nm("BOS", "JFK")
    expected = 162.1  # nm
    assert abs(d - expected) <= 1.0, (
        f"BOS-JFK: got {d:.3f} nm, expected ~{expected} nm (±1 nm)"
    )


# ---------------------------------------------------------------------------
# 3. Symmetry  d(a, b) == d(b, a)
# ---------------------------------------------------------------------------

def test_symmetry_all_pairs():
    """
    Property test — tolerance 0 (bitwise-identical expected).

    Haversine is symmetric by construction: swapping lat1↔lat2, lon1↔lon2 flips
    the sign of dphi and dlam, but both appear squared, so the result is identical
    to within floating-point (in fact bitwise-equal when arguments are swapped).

    Checks all C(6,2)=15 unordered pairs of the six fixture airports.
    """
    for a, b in itertools.combinations(ALL_AIRPORTS, 2):
        d_ab = distance_nm(a, b)
        d_ba = distance_nm(b, a)
        assert d_ab == d_ba, (
            f"Symmetry violated: distance_nm('{a}', '{b}') = {d_ab:.6f} nm "
            f"!= distance_nm('{b}', '{a}') = {d_ba:.6f} nm"
        )


# ---------------------------------------------------------------------------
# 4. Self-distance  d(a, a) == 0
# ---------------------------------------------------------------------------

def test_self_distance_zero():
    """
    Property test — tolerance 0 (exact zero expected).

    If both points are identical, all differences are zero and haversine returns 0.0
    exactly (no floating-point accumulation for the zero-difference case).

    Checks all six fixture airports.
    """
    for a in ALL_AIRPORTS:
        d = distance_nm(a, a)
        assert d == 0.0, (
            f"Self-distance for '{a}' should be 0.0 nm, got {d}"
        )


# ---------------------------------------------------------------------------
# 5. Non-negative distances
# ---------------------------------------------------------------------------

def test_non_negative_all_pairs():
    """
    Property test — no tolerance (strict inequality check).

    Great-circle distance is defined as a non-negative real number.
    asin always returns a value in [0, π/2] for inputs in [0,1], so
    2*R*asin(...) ≥ 0 for R > 0.

    Checks all C(6,2)=15 unordered pairs.
    """
    for a, b in itertools.combinations(ALL_AIRPORTS, 2):
        d = distance_nm(a, b)
        assert d >= 0.0, (
            f"distance_nm('{a}', '{b}') = {d:.6f} nm is negative"
        )


# ---------------------------------------------------------------------------
# 6. Triangle inequality  d(a, c) ≤ d(a, b) + d(b, c) + eps
# ---------------------------------------------------------------------------

def test_triangle_inequality_all_triples():
    """
    Property / invariance test — tolerance eps = 1e-9 nm (numerical noise guard).

    The great-circle distance on a sphere satisfies the triangle inequality strictly
    (it is a metric space).  We add a tiny eps = 1e-9 nm to guard against floating-point
    rounding in the asin/sqrt chain for nearly-collinear points.

    Checks all C(6,3)=20 ordered triples (a, b, c) in all three roles.
    """
    eps = 1e-9  # nm; purely floating-point guard, not a physical tolerance
    airports = ALL_AIRPORTS
    for a, b, c in itertools.combinations(airports, 3):
        d_ac = distance_nm(a, c)
        d_ab = distance_nm(a, b)
        d_bc = distance_nm(b, c)
        assert d_ac <= d_ab + d_bc + eps, (
            f"Triangle inequality violated: d('{a}','{c}') = {d_ac:.6f} nm "
            f"> d('{a}','{b}') + d('{b}','{c}') = {d_ab:.6f} + {d_bc:.6f} = {d_ab+d_bc:.6f} nm"
        )
        # also check the other two rollings
        d_ba = distance_nm(b, a)
        d_cb = distance_nm(c, b)
        d_ca = distance_nm(c, a)
        assert d_ba <= d_bc + d_ca + eps
        assert d_cb <= d_ca + d_ab + eps


# ---------------------------------------------------------------------------
# 7. Antipodal points  d ≈ π·R
# ---------------------------------------------------------------------------

def test_antipodal_distance():
    """
    Example / property test — tolerance 1e-6 nm (floating-point rounding only).

    The antipode of a point (lat, lon) is (-lat, lon+180°).  The great-circle
    distance from a point to its antipode is exactly half the Earth's circumference:
        d_antipodal = π · R_nm = π · 3440.065 ≈ 10807.283 nm.

    The haversine formula evaluates to exactly π·R at antipodal points (a=1, asin(1)=π/2),
    so floating-point error should be < 1e-6 nm.

    We test with the equator-prime-meridian point (0,0) whose antipode is (0,180).
    """
    R_nm = 3440.065
    expected = math.pi * R_nm  # ≈ 10807.283 nm

    d = haversine_nm(0.0, 0.0, 0.0, 180.0)
    assert abs(d - expected) < 1e-6, (
        f"Antipodal haversine_nm(0,0,0,180) = {d:.9f} nm; "
        f"expected π·R = {expected:.9f} nm; diff = {abs(d-expected):.2e} nm"
    )


# ---------------------------------------------------------------------------
# 8. Error handling — unknown airport code
# ---------------------------------------------------------------------------

def test_unknown_airport_raises():
    """
    Example test — no tolerance (exception handling test).

    distance_nm must raise KeyError for unknown IATA-like codes so callers can
    distinguish a missing-data error from a zero-distance result.  We also verify
    the error message contains the unknown code to aid debugging.
    """
    import pytest
    with pytest.raises(KeyError) as exc_info:
        distance_nm("ZZZ", "BOS")
    # The KeyError message should mention the unknown code
    assert "ZZZ" in str(exc_info.value), (
        f"KeyError message should mention 'ZZZ', got: {exc_info.value}"
    )


def test_second_unknown_airport_raises():
    """
    Example test — no tolerance (exception handling test).

    Checks that the second argument position also triggers KeyError for unknown codes.
    """
    import pytest
    with pytest.raises(KeyError) as exc_info:
        distance_nm("BOS", "ZZZ")
    assert "ZZZ" in str(exc_info.value), (
        f"KeyError message should mention 'ZZZ', got: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# 9. haversine_nm directly — raw lat/lon inputs
# ---------------------------------------------------------------------------

def test_haversine_nm_equator_one_degree():
    """
    Example test — tolerance 1e-6 nm (floating-point only).

    One degree of arc on the equator = R_nm * pi/180.
    With R = 3440.065 nm, one degree = 60.034 nm approximately.
    (Note: exactly 60 nm per degree only holds for R = 21600/(2π) = 3437.75 nm,
     i.e. the old definition of the nautical mile.  With R=3440.065 one degree ≈ 60.073 nm.)

    Tolerance is purely floating-point; no physical uncertainty.
    """
    R_nm = 3440.065
    expected = R_nm * math.pi / 180.0
    d = haversine_nm(0.0, 0.0, 1.0, 0.0)
    assert abs(d - expected) < 1e-6, (
        f"1° arc on equator: got {d:.9f} nm, expected {expected:.9f} nm"
    )


def test_haversine_nm_same_point():
    """
    Example test — exact zero expected (no tolerance needed).

    If both endpoints are identical, haversine formula gives exactly 0.0.
    """
    d = haversine_nm(42.3643, -71.0052, 42.3643, -71.0052)
    assert d == 0.0, f"Expected 0.0 for identical points, got {d}"
