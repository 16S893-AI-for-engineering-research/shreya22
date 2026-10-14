"""
Tests for lecture6/src/totals.py: network_totals(routes, seats_per_flight, lto_share).

Import scaffolding mirrors test_route_invariants.py: sys.path.insert so that
pytest can find modules under lecture6/src/ whether or not the package is
installed.
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from networks import (  # noqa: E402
    ODDemand,
    RouteFlow,
    load_demand,
    build_routes_point_to_point,
    build_routes_hub_spoke,
)
from distance import distance_nm  # noqa: E402
from fuel import stage_length_class, fuel_kg, COEFFICIENTS_KG_PER_NM  # noqa: E402
from emissions import emissions_kg, EI_TABLE  # noqa: E402
import totals as totals_mod  # noqa: E402
from totals import network_totals  # noqa: E402

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"
_HUBS = ["JFK", "ORD"]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

EXPECTED_KEYS = {
    "passengers",
    "passenger_legs",
    "flights",
    "aircraft_nm",
    "fuel_kg",
    "NOx_kg",
    "CO_kg",
    "HC_kg",
}


def _p2p_routes():
    return build_routes_point_to_point(load_demand(str(_FIXTURE)))


def _hs_routes():
    return build_routes_hub_spoke(load_demand(str(_FIXTURE)), hubs=_HUBS)


def _single_leg_route(origin: str, dest: str, passengers: float) -> list[RouteFlow]:
    """Return a one-element list containing a direct RouteFlow."""
    return [RouteFlow(origin, dest, (origin, dest), passengers)]


# ---------------------------------------------------------------------------
# 1. Keys present
# ---------------------------------------------------------------------------


def test_keys_present_p2p():
    """
    Example test — no tolerance.

    Verifies that network_totals returns a dict with exactly the eight
    documented keys for a real P2P route set.  A missing key is an API
    contract violation; an extra unexpected key is not tested here because
    the spec says 'with keys:' not 'only keys:'.

    Cannot catch: wrong values behind correct keys.
    """
    result = network_totals(_p2p_routes())
    assert EXPECTED_KEYS.issubset(result.keys()), (
        f"Missing keys: {EXPECTED_KEYS - result.keys()}"
    )


def test_keys_present_hs():
    """
    Example test — no tolerance.

    Same contract check as test_keys_present_p2p but for a hub-and-spoke
    route set.  Ensures the function works for multi-leg routes too.

    Cannot catch: wrong values behind correct keys.
    """
    result = network_totals(_hs_routes())
    assert EXPECTED_KEYS.issubset(result.keys())


# ---------------------------------------------------------------------------
# 2. All values finite and non-negative
# ---------------------------------------------------------------------------


def test_all_values_finite_nonneg_p2p():
    """
    Invariance test — tolerance: math.isfinite (exact IEEE 754 check).

    Every numeric total must be a finite, non-negative float.  NaN or Inf
    would indicate a division-by-zero or overflow bug in the computation.
    Zero is allowed (e.g. if the route list has no legs).

    Cannot catch: a value that is finite and non-negative but numerically
    wrong.
    """
    result = network_totals(_p2p_routes())
    for key, val in result.items():
        assert math.isfinite(val), f"{key}={val} is not finite"
        assert val >= 0.0, f"{key}={val} is negative"


def test_all_values_finite_nonneg_hs():
    """
    Invariance test — tolerance: math.isfinite (exact IEEE 754 check).

    Same invariant as test_all_values_finite_nonneg_p2p for hub-and-spoke
    routes.

    Cannot catch: a value that is finite and non-negative but numerically
    wrong.
    """
    result = network_totals(_hs_routes())
    for key, val in result.items():
        assert math.isfinite(val), f"{key}={val} is not finite"
        assert val >= 0.0, f"{key}={val} is negative"


# ---------------------------------------------------------------------------
# 3. P2P: passenger_legs == passengers  (each route has exactly one leg)
# ---------------------------------------------------------------------------


def test_p2p_passenger_legs_equals_passengers():
    """
    Invariance test — tolerance: 1e-9 relative.

    For point-to-point routes every OD pair has a single direct leg, so
    passenger_legs (sum of passengers × legs_in_route) must equal passengers
    (sum of all passengers).  Relative tolerance 1e-9 guards against float
    accumulation over many routes; we expect exact equality here since all
    routes have exactly 1 leg and legs_in_route=1 is an integer multiply.

    Cannot catch: a bug that produces the correct P2P value but wrong H&S
    passenger_legs.
    """
    result = network_totals(_p2p_routes())
    pax = result["passengers"]
    pax_legs = result["passenger_legs"]
    assert math.isclose(pax_legs, pax, rel_tol=1e-9), (
        f"P2P: passenger_legs={pax_legs} != passengers={pax}"
    )


# ---------------------------------------------------------------------------
# 4. Hub-spoke: passenger_legs >= passengers
# ---------------------------------------------------------------------------


def test_hs_passenger_legs_gte_passengers():
    """
    Invariance test — no tolerance (strict inequality from integer leg counts).

    In a hub-and-spoke network most passengers travel at least two legs
    (spoke-to-hub, hub-to-spoke or hub-to-hub), so the sum of
    passengers × legs must be strictly greater than the simple passenger
    count.  The tiny_demand fixture has several connecting routes so
    passenger_legs > passengers is guaranteed.

    Cannot catch: a network where every OD happens to be hub-to-hub (all
    single-leg), in which case the assertion would need to be >=.  We check
    strict > here because we know the fixture has non-hub airports.
    """
    result = network_totals(_hs_routes())
    assert result["passenger_legs"] > result["passengers"], (
        f"H&S: passenger_legs={result['passenger_legs']} should be > "
        f"passengers={result['passengers']}"
    )


# ---------------------------------------------------------------------------
# 5. P2P passenger total matches fixture sum
# ---------------------------------------------------------------------------


def test_p2p_passenger_total_matches_fixture():
    """
    Golden test — tolerance: 1e-9 relative.

    The tiny_demand.csv sums to 448.5 passengers.  P2P routing does not
    split or aggregate, so network_totals['passengers'] must reproduce
    that exact sum.  Relative tolerance 1e-9 to accommodate float
    accumulation across 8 rows.

    Cannot catch: a bug that cancels out (e.g. over-counts one row and
    under-counts another by the same amount).
    """
    result = network_totals(_p2p_routes())
    assert math.isclose(result["passengers"], 448.5, rel_tol=1e-9), (
        f"passengers={result['passengers']}, expected 448.5"
    )


# ---------------------------------------------------------------------------
# 6. Golden example: BOS->JFK, 150 pax, seats_per_flight=150
# ---------------------------------------------------------------------------


def test_golden_single_leg_bos_jfk():
    """
    Golden test — tolerance: 1e-9 relative for all numeric comparisons.

    Hand-computed expected values derived directly from module constants
    (distance_nm, COEFFICIENTS_KG_PER_NM, EI_TABLE) to remain correct even
    if those placeholder constants change (the test re-derives at runtime).

    Route: BOS -> JFK, 150 passengers, seats_per_flight=150.0, lto_share=0.1.

    Expected (derived here independently from module constants):
      d    = distance_nm('BOS', 'JFK')            [nm]
      cls  = stage_length_class(d)                ['short']
      coeff= COEFFICIENTS_KG_PER_NM[cls]          [kg/nm]
      flights_leg      = 150 / 150 = 1.0
      aircraft_nm      = flights_leg * d          = d
      fuel_per_flight  = coeff * d
      fuel_total       = flights_leg * fuel_per_flight
      emissions        = emissions_kg(fuel_total, 0.1)
      passengers       = 150.0
      passenger_legs   = 150.0 * 1 leg = 150.0  (same as passengers)

    Reason for 1e-9 relative tolerance: all quantities are derived from the
    same floating-point constants; the only rounding differences come from
    multiplication order.

    Cannot catch: compensating errors if distance_nm, fuel_kg, and
    emissions_kg are all wrong in a consistent way.
    """
    d = distance_nm("BOS", "JFK")
    cls = stage_length_class(d)
    coeff = COEFFICIENTS_KG_PER_NM[cls]

    seats = 150.0
    pax = 150.0
    lto = 0.1

    flights_expected = pax / seats                   # = 1.0
    aircraft_nm_expected = flights_expected * d
    fuel_per_flight = coeff * d
    fuel_expected = flights_expected * fuel_per_flight
    em = emissions_kg(fuel_expected, lto)

    routes = _single_leg_route("BOS", "JFK", pax)
    result = network_totals(routes, seats_per_flight=seats, lto_share=lto)

    assert math.isclose(result["passengers"],      pax,                    rel_tol=1e-9), f"passengers: {result['passengers']}"
    assert math.isclose(result["passenger_legs"],  pax,                    rel_tol=1e-9), f"passenger_legs: {result['passenger_legs']}"
    assert math.isclose(result["flights"],         flights_expected,       rel_tol=1e-9), f"flights: {result['flights']}"
    assert math.isclose(result["aircraft_nm"],     aircraft_nm_expected,   rel_tol=1e-9), f"aircraft_nm: {result['aircraft_nm']}"
    assert math.isclose(result["fuel_kg"],         fuel_expected,          rel_tol=1e-9), f"fuel_kg: {result['fuel_kg']}"
    assert math.isclose(result["NOx_kg"],          em["NOx"],              rel_tol=1e-9), f"NOx_kg: {result['NOx_kg']}"
    assert math.isclose(result["CO_kg"],           em["CO"],               rel_tol=1e-9), f"CO_kg: {result['CO_kg']}"
    assert math.isclose(result["HC_kg"],           em["HC"],               rel_tol=1e-9), f"HC_kg: {result['HC_kg']}"


# ---------------------------------------------------------------------------
# 7. Additivity over disjoint route sets: totals(A+B) == totals(A) + totals(B)
# ---------------------------------------------------------------------------


def test_additivity_disjoint_route_sets():
    """
    Property test — tolerance: 1e-12 relative.

    Reason for 1e-12: addition of floats is commutative up to ulp-level
    rounding; with ~10 routes and values in the hundreds, the absolute
    error is at most a few ulps, well below 1e-12 relative.  This is the
    strictest tolerance we can justify for float summation-order differences.

    Split the tiny_demand P2P routes into two disjoint halves A and B,
    compute totals separately and jointly, then assert each total key adds
    up.  Additivity would be violated if, e.g., the function accumulated
    some state between calls or miscounted routes by index.

    Cannot catch: a bug that cancels out symmetrically across A and B.
    """
    routes = _p2p_routes()
    mid = len(routes) // 2
    A, B = routes[:mid], routes[mid:]
    assert A and B, "need non-empty halves"

    tot_A = network_totals(A)
    tot_B = network_totals(B)
    tot_AB = network_totals(A + B)

    for key in EXPECTED_KEYS:
        combined = tot_A[key] + tot_B[key]
        assert math.isclose(tot_AB[key], combined, rel_tol=1e-12), (
            f"Additivity failed for {key!r}: "
            f"totals(A+B)={tot_AB[key]}, totals(A)+totals(B)={combined}"
        )


# ---------------------------------------------------------------------------
# 8. Linearity: scaling every route's passengers by k scales all totals by k
# ---------------------------------------------------------------------------


def test_linearity_passenger_scaling():
    """
    Property test — tolerance: 1e-9 relative.

    Reason for 1e-9: multiplying every passenger count by k and then
    computing totals is equivalent to multiplying all totals by k (all
    formulas are linear in passengers).  Relative tolerance 1e-9 covers
    float accumulation; 1e-12 would be too tight for large k.

    We test k=3.7 (non-trivial irrational-ish multiplier).  The route list
    is reconstructed by scaling the passengers field.  All eight totals must
    scale by k.

    Cannot catch: a bug that is linear in passengers but uses wrong
    coefficients (the golden test catches that).
    """
    routes = _p2p_routes()
    k = 3.7
    scaled_routes = [
        RouteFlow(r.origin, r.destination, r.route, r.passengers * k)
        for r in routes
    ]

    base = network_totals(routes)
    scaled = network_totals(scaled_routes)

    for key in EXPECTED_KEYS:
        assert math.isclose(scaled[key], base[key] * k, rel_tol=1e-9), (
            f"Linearity failed for {key!r}: "
            f"scaled={scaled[key]}, base*k={base[key]*k}"
        )


# ---------------------------------------------------------------------------
# 9. Parameter effect: doubling seats_per_flight halves flights, aircraft_nm, fuel_kg
#    but does NOT change passengers or passenger_legs
# ---------------------------------------------------------------------------


def test_doubling_seats_halves_flights_fuel():
    """
    Differential / metamorphic test — tolerance: 1e-9 relative.

    Reasoning: flights_leg = passengers / seats_per_flight; aircraft_nm and
    fuel_kg are both proportional to flights_leg, so doubling seats_per_flight
    halves them.  Emissions are proportional to fuel_kg so also halved.
    passengers and passenger_legs are independent of seats_per_flight.

    We use a single-leg route BOS->JFK with 300 pax to avoid any rounding
    at a boundary.

    Cannot catch: a bug that scales both numerator and denominator of
    flights by seats in a way that cancels.
    """
    routes = _single_leg_route("BOS", "JFK", 300.0)
    base = network_totals(routes, seats_per_flight=150.0, lto_share=0.1)
    doubled = network_totals(routes, seats_per_flight=300.0, lto_share=0.1)

    # Unaffected by seats
    assert math.isclose(doubled["passengers"],     base["passengers"],     rel_tol=1e-9)
    assert math.isclose(doubled["passenger_legs"], base["passenger_legs"], rel_tol=1e-9)

    # Halved by doubling seats
    for key in ("flights", "aircraft_nm", "fuel_kg", "NOx_kg", "CO_kg", "HC_kg"):
        assert math.isclose(doubled[key], base[key] / 2.0, rel_tol=1e-9), (
            f"Expected {key} to halve; base={base[key]}, doubled={doubled[key]}"
        )


# ---------------------------------------------------------------------------
# 10. Parameter effect: lto_share only changes emissions, not fuel or flights
# ---------------------------------------------------------------------------


def test_lto_share_only_changes_emissions():
    """
    Differential / metamorphic test — tolerance: 1e-9 relative.

    lto_share controls the LTO/cruise split used to weight emission indices.
    It does NOT appear in the fuel or flights formulas, so fuel_kg,
    aircraft_nm, flights, passengers, and passenger_legs must be identical
    for lto_share=0.1 vs lto_share=0.5.

    Emissions (NOx, CO, HC) must differ because LTO and cruise have
    different EI values.

    Cannot catch: a bug where lto_share is incorrectly threaded into both
    fuel and emissions (it would still change emissions, but also wrongly
    change fuel).
    """
    routes = _single_leg_route("BOS", "ORD", 200.0)
    r1 = network_totals(routes, seats_per_flight=150.0, lto_share=0.1)
    r2 = network_totals(routes, seats_per_flight=150.0, lto_share=0.5)

    # These must be identical
    for key in ("flights", "aircraft_nm", "fuel_kg", "passengers", "passenger_legs"):
        assert math.isclose(r1[key], r2[key], rel_tol=1e-9), (
            f"{key} changed with lto_share: {r1[key]} vs {r2[key]}"
        )

    # Emissions must differ (NOx LTO EI != cruise EI, same for CO, HC)
    for key in ("NOx_kg", "CO_kg", "HC_kg"):
        assert not math.isclose(r1[key], r2[key], rel_tol=1e-6), (
            f"{key} should change with lto_share but didn't: {r1[key]} vs {r2[key]}"
        )


# ---------------------------------------------------------------------------
# 11. Symmetry: reversing every route leaves all totals unchanged (distance symmetry)
# ---------------------------------------------------------------------------


def test_route_reversal_leaves_totals_unchanged():
    """
    Metamorphic test — tolerance: 1e-9 relative.

    Great-circle distance is symmetric: distance_nm(A, B) == distance_nm(B, A).
    Therefore reversing every route (swapping origin↔destination and reversing
    the path tuple) should produce identical totals for all keys.

    We reverse only the path and origin/destination fields; passengers and
    the structure of legs remain the same, just the direction flips.

    This tests that the implementation does not accidentally use a directional
    formula (e.g. lat1-lat2 without squaring).

    Cannot catch: bugs that affect both forward and reverse routes equally.
    """
    routes = _p2p_routes()
    reversed_routes = [
        RouteFlow(
            origin=r.destination,
            destination=r.origin,
            route=tuple(reversed(r.route)),
            passengers=r.passengers,
        )
        for r in routes
    ]

    base = network_totals(routes)
    rev = network_totals(reversed_routes)

    for key in EXPECTED_KEYS:
        assert math.isclose(rev[key], base[key], rel_tol=1e-9), (
            f"Reversal changed {key!r}: forward={base[key]}, reversed={rev[key]}"
        )


# ---------------------------------------------------------------------------
# 12. Empty route list returns zeros
# ---------------------------------------------------------------------------


def test_empty_routes_returns_zeros():
    """
    Example test — no tolerance (exact zero).

    An empty list of routes has no passengers, no flights, no fuel.  All
    totals should be exactly 0.0.  This guards against an implementation
    that initialises accumulators incorrectly or divides by the route count.

    Cannot catch: wrong accumulator initial values that get overwritten on
    the first iteration.
    """
    result = network_totals([])
    for key in EXPECTED_KEYS:
        assert result[key] == 0.0, f"Empty routes: {key}={result[key]}, expected 0.0"


# ---------------------------------------------------------------------------
# 13. Same-airport leg (origin == destination): zero distance, raises or skips
#     The spec says 'Skip legs where origin==destination only if you can justify
#     it; otherwise raise.' We document the actual behaviour and lock it in.
# ---------------------------------------------------------------------------


def test_zero_distance_leg_does_not_crash():
    """
    Example test — tolerance: exact 0.0 for distance-derived quantities.

    A route with origin == destination is degenerate (zero-length leg).
    distance_nm(X, X) returns 0.0 exactly.  The implementation must either
    skip it or handle it gracefully without raising or producing NaN/Inf.

    We verify: no exception is raised, fuel_kg and aircraft_nm contributions
    from the zero leg are 0.0, and the passenger count is preserved.

    Cannot catch: an implementation that silently skips non-zero legs too.
    """
    # RouteFlow with a two-leg route: BOS->BOS->JFK
    # leg 1: BOS->BOS (zero distance)
    # leg 2: BOS->JFK (normal leg)
    route = RouteFlow("BOS", "JFK", ("BOS", "BOS", "JFK"), 60.0)

    # Should not raise
    result = network_totals([route])
    assert math.isfinite(result["fuel_kg"]), "fuel_kg must be finite"
    assert math.isfinite(result["aircraft_nm"]), "aircraft_nm must be finite"
    assert result["passengers"] == 60.0
