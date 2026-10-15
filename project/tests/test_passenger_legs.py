"""
Tests for project/src/legs.py: passenger_legs(routes) and total_passengers(routes).

passenger_legs(routes):
    sum over each RouteFlow of  passengers * (len(route) - 1).
    Raises ValueError for any route with fewer than 2 nodes.

total_passengers(routes):
    sum of passengers over all RouteFlows.

Import scaffolding mirrors test_route_invariants.py.
"""
from __future__ import annotations

import itertools
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
from legs import passenger_legs, total_passengers  # noqa: E402

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"

# Known airports used in the property sweep (same set as distance.py)
_AIRPORTS = ["BOS", "JFK", "ORD", "SFO", "DEN", "PHX"]
_PROJECT_HUBS = ["JFK", "ORD"]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _demand_from_fixture():
    return load_demand(str(_FIXTURE))


def _p2p(demand=None):
    if demand is None:
        demand = _demand_from_fixture()
    return build_routes_point_to_point(demand)


def _hs(hubs=None, demand=None, assign=None):
    if demand is None:
        demand = _demand_from_fixture()
    if hubs is None:
        hubs = _PROJECT_HUBS
    return build_routes_hub_spoke(demand, hubs=hubs, assign=assign)


# ---------------------------------------------------------------------------
# Basic contract / example tests
# ---------------------------------------------------------------------------


def test_passenger_legs_empty_list():
    """
    Example test — exact equality (sum over empty sequence is 0.0).

    passenger_legs([]) must return 0.0 (or integer-zero); vacuously true.
    Cannot catch: wrong non-zero default.
    """
    assert passenger_legs([]) == 0.0


def test_total_passengers_empty_list():
    """
    Example test — exact equality.

    total_passengers([]) must return 0.0; vacuously true.
    """
    assert total_passengers([]) == 0.0


def test_passenger_legs_single_direct_route():
    """
    Example test — exact equality (no floating-point arithmetic beyond
    addition/multiplication of exact floats).

    A single direct route O->D has len(route)==2 → 1 leg → legs == passengers.
    """
    rf = RouteFlow("BOS", "JFK", ("BOS", "JFK"), 50.0)
    assert passenger_legs([rf]) == 50.0


def test_total_passengers_single_route():
    """
    Example test — exact equality.

    total_passengers of a single record returns that record's passengers.
    """
    rf = RouteFlow("BOS", "JFK", ("BOS", "JFK"), 75.3)
    assert total_passengers([rf]) == 75.3


def test_passenger_legs_two_leg_route():
    """
    Example test — exact equality.

    A route with 3 nodes (O->H->D) has 2 legs → legs == 2 * passengers.
    """
    rf = RouteFlow("BOS", "SFO", ("BOS", "JFK", "SFO"), 40.0)
    assert passenger_legs([rf]) == 80.0


def test_passenger_legs_three_leg_route():
    """
    Example test — exact equality.

    A route with 4 nodes has 3 legs → legs == 3 * passengers.
    """
    rf = RouteFlow("BOS", "PHX", ("BOS", "JFK", "ORD", "PHX"), 20.0)
    assert passenger_legs([rf]) == 60.0


def test_passenger_legs_rejects_single_node_route():
    """
    Example test — no tolerance; tests error path.

    A route with only 1 node has 0 segments — physically meaningless.
    passenger_legs must raise ValueError identifying the bad record.
    Cannot catch: implementations that silently return 0 instead of raising.
    """
    bad = RouteFlow("BOS", "BOS", ("BOS",), 10.0)
    with pytest.raises(ValueError):
        passenger_legs([bad])


def test_passenger_legs_rejects_empty_route():
    """
    Example test — no tolerance; tests error path.

    An empty route tuple is also invalid.
    passenger_legs must raise ValueError.
    """
    bad = RouteFlow("BOS", "JFK", (), 5.0)
    with pytest.raises(ValueError):
        passenger_legs([bad])


# ---------------------------------------------------------------------------
# Real builder output — tiny fixture
# ---------------------------------------------------------------------------


def test_p2p_legs_equal_passengers_on_fixture():
    """
    Invariance test (property) on real builder output — tolerance 1e-9
    (relative to total, ~448 pax; absolute difference must be < 1e-6).

    Reason: In point-to-point every OD has exactly 1 leg, so
    passenger_legs == total_passengers exactly (no float accumulation
    beyond direct pass-through of input values).

    Cannot catch: bugs that affect only hub-and-spoke routing.
    """
    routes = _p2p()
    legs = passenger_legs(routes)
    pax = total_passengers(routes)
    assert abs(legs - pax) < 1e-9, (
        f"P2P legs should equal passengers exactly; got legs={legs}, pax={pax}"
    )


def test_hs_legs_ge_passengers_on_fixture():
    """
    Invariance test on real builder output — no tolerance needed (strict >=).

    In the hub-and-spoke network every route has at least 1 leg (>=2 nodes),
    so passenger_legs >= total_passengers always holds.

    Cannot catch: bugs where legs accidentally equal passengers for all ODs
    when some ODs are routed via a hub (would be caught by the strictly-greater test).
    """
    routes = _hs()
    assert passenger_legs(routes) >= total_passengers(routes)


def test_hs_legs_strictly_greater_when_interior_hub_exists_fixture():
    """
    Invariance test on real builder output — tolerance 1e-9 absolute.

    Reason: The tiny fixture includes ODs (e.g., BOS->ORD, DEN->PHX)
    where neither endpoint is the same hub, so the route has an interior hub
    node → 2 legs per passenger → legs > passengers.  We verify the aggregate
    inequality is strict.

    Cannot catch: a bug that accidentally makes P2P and hub-and-spoke produce
    the same legs total (would require all routes to be direct hub->hub).
    """
    routes = _hs()
    legs = passenger_legs(routes)
    pax = total_passengers(routes)
    assert legs > pax + 1e-9, (
        f"Hub-spoke legs should be strictly > passengers when interior hubs exist; "
        f"legs={legs}, pax={pax}"
    )


def test_hs_legs_minus_pax_equals_pax_times_interior_hubs_fixture():
    """
    Differential test on real builder output — tolerance 1e-9 absolute.

    Reason: For each OD routed via k interior hubs, the extra legs =
    passengers * k.  Summed over all ODs:
        passenger_legs - total_passengers = sum_OD( passengers * interior_hubs )
    This algebraic identity must hold exactly (integer interior_hub counts,
    floats only from the passenger values themselves).

    Tolerance 1e-9 absorbs any floating-point summation-order differences.
    Cannot catch: a sign error that doubles the interior contribution.
    """
    routes = _hs()
    legs = passenger_legs(routes)
    pax = total_passengers(routes)
    extra_legs_expected = sum(
        r.passengers * (len(r.route) - 2)   # interior nodes = total nodes - 2
        for r in routes
    )
    assert abs((legs - pax) - extra_legs_expected) < 1e-9, (
        f"legs - pax = {legs - pax}, expected {extra_legs_expected}"
    )


def test_hub_to_hub_od_has_legs_equal_passengers_fixture():
    """
    Invariance test (example + property) on real builder output — tolerance 1e-9.

    When O and D are both hubs the route has exactly 2 nodes (route length 2)
    → 1 leg → legs == passengers.  This is the 'no interior hub' edge case
    where 'strictly greater' must NOT be asserted.

    The tiny fixture has JFK->ORD and ORD->JFK, both hub-to-hub ODs.
    We verify each individually: legs == passengers (exactly 1 leg each).

    Cannot catch: bugs where non-hub-to-hub routes accidentally get route
    length 2 as well.
    """
    routes = _hs()
    hub_to_hub = [r for r in routes if r.origin in _PROJECT_HUBS and r.destination in _PROJECT_HUBS]
    assert len(hub_to_hub) >= 1, "Expected at least one hub-to-hub OD in fixture"
    for r in hub_to_hub:
        legs = passenger_legs([r])
        pax = r.passengers
        assert abs(legs - pax) < 1e-9, (
            f"Hub-to-hub {r.origin}->{r.destination}: legs={legs} should == pax={pax}"
        )


# ---------------------------------------------------------------------------
# Property sweep: all ordered airport pairs, various hub sets
# ---------------------------------------------------------------------------


def _make_demand_all_pairs(passengers: float = 100.0) -> list:
    """Return one ODDemand per ordered pair of distinct airports."""
    return [
        ODDemand(o, d, passengers)
        for o, d in itertools.permutations(_AIRPORTS, 2)
    ]


def _count_interior_hubs_for_route(route: tuple, hubs: list) -> int:
    """Return the number of interior (non-endpoint) hub nodes in a route."""
    if len(route) <= 2:
        return 0
    hub_set = set(h.upper() for h in hubs)
    return sum(1 for node in route[1:-1] if node in hub_set)


@pytest.mark.parametrize(
    "hub_set",
    [
        ["JFK"],                      # single hub
        ["JFK", "ORD"],               # project hubs (two)
        ["JFK", "ORD", "DEN"],        # three hubs
    ],
    ids=["single_hub", "two_hubs", "three_hubs"],
)
def test_legs_ge_passengers_all_pairs(hub_set):
    """
    Property test — no tolerance (exact comparison on integer-multiple floats).

    For every ordered pair of the 6 known airports and every hub set listed,
    passenger_legs(routes) >= total_passengers(routes).

    Reason: passenger * (len(route)-1) >= passenger * 1 = passenger since
    every valid route has >= 2 nodes (>= 1 leg).

    Cannot catch: off-by-one in the per-route leg count that only manifests
    for specific route topologies not present in this sweep.
    """
    demand = _make_demand_all_pairs()
    routes = build_routes_hub_spoke(demand, hubs=hub_set)
    assert passenger_legs(routes) >= total_passengers(routes)


@pytest.mark.parametrize(
    "hub_set",
    [
        ["JFK"],
        ["JFK", "ORD"],
        ["JFK", "ORD", "DEN"],
    ],
    ids=["single_hub", "two_hubs", "three_hubs"],
)
def test_strictly_greater_when_interior_hub_exists_all_pairs(hub_set):
    """
    Property test — tolerance 1e-9 absolute on the final sum.

    For an all-pairs demand over 6 airports, at least one OD pair must have
    an interior hub node (e.g., PHX->SFO via JFK), so legs > passengers
    strictly.  We assert that the strict inequality holds for the aggregate.

    Tolerance 1e-9: the expected extra legs is many hundreds of passengers;
    this guards against a degenerate hub assignment where no interior hubs exist.

    Cannot catch: a bug that affects only individual OD legs while the aggregate
    accidentally maintains the strict inequality.
    """
    demand = _make_demand_all_pairs()
    routes = build_routes_hub_spoke(demand, hubs=hub_set)
    legs = passenger_legs(routes)
    pax = total_passengers(routes)
    # Confirm at least one route has an interior hub
    has_interior = any(len(r.route) > 2 for r in routes)
    assert has_interior, f"hub_set={hub_set}: expected some routes with interior hubs"
    assert legs > pax + 1e-9, (
        f"hub_set={hub_set}: legs={legs} should be strictly > pax={pax}"
    )


@pytest.mark.parametrize(
    "hub_set",
    [
        ["JFK"],
        ["JFK", "ORD"],
        ["JFK", "ORD", "DEN"],
    ],
    ids=["single_hub", "two_hubs", "three_hubs"],
)
def test_hub_to_hub_routes_have_legs_equal_passengers_all_pairs(hub_set):
    """
    Property test — tolerance 1e-9 per route.

    For all OD pairs where both origin AND destination are in hub_set, the
    route must have exactly 2 nodes (direct hub-to-hub), so
    legs == passengers for that route.  'Strictly greater' must NOT be
    asserted for these routes.

    This makes explicit the edge case in the acceptance criteria.

    Cannot catch: bugs where a hub-to-hub OD is accidentally given an interior
    routing node that happens to not be in hub_set.
    """
    demand = _make_demand_all_pairs()
    routes = build_routes_hub_spoke(demand, hubs=hub_set)
    hub_set_upper = set(h.upper() for h in hub_set)
    hub_to_hub_routes = [
        r for r in routes
        if r.origin in hub_set_upper and r.destination in hub_set_upper
    ]
    for r in hub_to_hub_routes:
        legs = passenger_legs([r])
        assert abs(legs - r.passengers) < 1e-9, (
            f"hub_set={hub_set}: hub-to-hub {r.origin}->{r.destination} "
            f"legs={legs} should == passengers={r.passengers}"
        )


@pytest.mark.parametrize(
    "hub_set",
    [
        ["JFK"],
        ["JFK", "ORD"],
        ["JFK", "ORD", "DEN"],
    ],
    ids=["single_hub", "two_hubs", "three_hubs"],
)
def test_legs_minus_pax_equals_pax_times_interior_hubs_all_pairs(hub_set):
    """
    Differential / algebraic identity test — tolerance 1e-9 absolute.

    Reason: legs - passengers = sum_routes( passengers * interior_hub_count ).
    This identity holds because each interior hub adds exactly one extra leg
    per passenger.  This is independent of the hub assignment function.

    Tolerance 1e-9 chosen to absorb floating-point summation reordering;
    the true error is O(n * machine_epsilon * max_pax) ≈ 30 * 1e-15 * 100 ≈ 3e-12.

    Cannot catch: bugs where interior_hub_count is counted correctly but
    the per-passenger contribution is wrong by a constant factor.
    """
    demand = _make_demand_all_pairs()
    routes = build_routes_hub_spoke(demand, hubs=hub_set)
    legs = passenger_legs(routes)
    pax = total_passengers(routes)
    expected_extra = sum(r.passengers * _count_interior_hubs_for_route(r.route, hub_set)
                         for r in routes)
    assert abs((legs - pax) - expected_extra) < 1e-9, (
        f"hub_set={hub_set}: legs-pax={legs-pax}, expected {expected_extra}"
    )


def test_p2p_legs_equal_passengers_all_pairs():
    """
    Property test — tolerance 1e-9 absolute.

    In a point-to-point network every route has exactly 2 nodes → 1 leg →
    passenger_legs == total_passengers exactly.

    This is the converse of 'strictly greater for hub-and-spoke'.

    Cannot catch: bugs that only affect hub-and-spoke leg counting.
    """
    demand = _make_demand_all_pairs()
    routes = build_routes_point_to_point(demand)
    legs = passenger_legs(routes)
    pax = total_passengers(routes)
    assert abs(legs - pax) < 1e-9, (
        f"P2P: legs={legs} should equal pax={pax}"
    )


# ---------------------------------------------------------------------------
# Metamorphic tests
# ---------------------------------------------------------------------------


def test_metamorphic_scaling_passengers_scales_legs():
    """
    Metamorphic test — tolerance 1e-9 * k (scales with the multiplier).

    If all passengers are multiplied by k, passenger_legs must also multiply
    by k.  This holds because passenger_legs is linear in passengers.

    We test k in {0, 1, 2, 7, 0.5, 100} on the tiny fixture hub-and-spoke routes.

    Tolerance: 1e-9 * k to remain proportional; at k=100 the accumulated
    float error is still < 1e-7, well below any physically meaningful difference.

    Cannot catch: additive bugs that are independent of the passenger scale
    (e.g., a constant offset per route).
    """
    base_demand = _demand_from_fixture()
    base_routes = _hs(demand=base_demand)
    base_legs = passenger_legs(base_routes)

    for k in [0.0, 1.0, 2.0, 7.0, 0.5, 100.0]:
        scaled_demand = [ODDemand(r.origin, r.destination, r.passengers * k)
                         for r in base_demand]
        scaled_routes = _hs(demand=scaled_demand)
        scaled_legs = passenger_legs(scaled_routes)
        expected = base_legs * k
        assert abs(scaled_legs - expected) < max(1e-9, 1e-9 * k), (
            f"Scaling by k={k}: legs={scaled_legs}, expected={expected}"
        )


def test_metamorphic_scaling_passengers_scales_total_passengers():
    """
    Metamorphic test — tolerance 1e-9 * k.

    If all passengers are multiplied by k, total_passengers must also multiply
    by k (trivially linear; tests that total_passengers sums correctly).

    Cannot catch: bugs where total_passengers double-counts some routes.
    """
    base_demand = _demand_from_fixture()
    base_routes = _hs(demand=base_demand)
    base_pax = total_passengers(base_routes)

    for k in [0.0, 1.0, 3.0, 0.25]:
        scaled_demand = [ODDemand(r.origin, r.destination, r.passengers * k)
                         for r in base_demand]
        scaled_routes = _hs(demand=scaled_demand)
        scaled_pax = total_passengers(scaled_routes)
        expected = base_pax * k
        assert abs(scaled_pax - expected) < max(1e-9, 1e-9 * k), (
            f"Scaling by k={k}: pax={scaled_pax}, expected={expected}"
        )


def test_metamorphic_additivity_disjoint_od():
    """
    Metamorphic test — tolerance 1e-9 absolute.

    Adding a disjoint OD (one not present in the original demand) adds its
    own passenger-legs to the total; the existing routes are unaffected.

    Formally:  legs(A ∪ {new_od}) = legs(A) + legs({new_od})

    We add BOS->DEN (not in the tiny fixture) with 99.0 passengers,
    routed via JFK hub → BOS->JFK->DEN (2 legs → 198 passenger-legs).

    Tolerance 1e-9: pure float addition with known inputs.

    Cannot catch: bugs that cause interactions between routes (would violate
    the independence of RouteFlow records in the sum).
    """
    # Base routes
    base_demand = _demand_from_fixture()
    base_routes = _hs(demand=base_demand)
    base_legs = passenger_legs(base_routes)
    base_pax = total_passengers(base_routes)

    # New disjoint OD: BOS->DEN is NOT in the fixture
    new_od = ODDemand("BOS", "DEN", 99.0)
    new_route_only = build_routes_hub_spoke([new_od], hubs=_PROJECT_HUBS)
    new_legs = passenger_legs(new_route_only)
    new_pax = total_passengers(new_route_only)

    # Combined
    combined_demand = list(base_demand) + [new_od]
    combined_routes = _hs(demand=combined_demand)
    combined_legs = passenger_legs(combined_routes)
    combined_pax = total_passengers(combined_routes)

    assert abs(combined_legs - (base_legs + new_legs)) < 1e-9, (
        f"Additivity: combined_legs={combined_legs}, expected={base_legs + new_legs}"
    )
    assert abs(combined_pax - (base_pax + new_pax)) < 1e-9, (
        f"Additivity pax: combined_pax={combined_pax}, expected={base_pax + new_pax}"
    )


def test_metamorphic_additivity_disjoint_od_p2p():
    """
    Metamorphic test — tolerance 1e-9 absolute.

    Same additivity check as test_metamorphic_additivity_disjoint_od but for
    point-to-point routes.  Adding a disjoint OD adds exactly its passengers
    to both total_passengers and passenger_legs (since P2P has 1 leg/route).

    Cannot catch: bug where the P2P builder merges or deduplicates ODs.
    """
    base_demand = _demand_from_fixture()
    base_routes = _p2p(demand=base_demand)
    base_legs = passenger_legs(base_routes)

    extra_pax = 77.0
    new_od = ODDemand("BOS", "DEN", extra_pax)  # not in fixture
    combined_routes = _p2p(demand=list(base_demand) + [new_od])
    combined_legs = passenger_legs(combined_routes)

    assert abs(combined_legs - (base_legs + extra_pax)) < 1e-9, (
        f"P2P additivity: combined_legs={combined_legs}, expected={base_legs + extra_pax}"
    )


# ---------------------------------------------------------------------------
# Strictly-greater explicitly guarded by interior-hub check
# ---------------------------------------------------------------------------


def test_strictly_greater_only_when_interior_hub_exists_guarded():
    """
    Property test — tolerance 1e-9; documents the 'strictly greater only when
    interior hub exists' rule explicitly.

    We build a demand list where:
    (a) Some ODs have an interior hub → legs > passengers for those routes.
    (b) Some ODs are hub-to-hub → legs == passengers for those routes.

    For each route we assert:
    - If len(route) > 2 (interior hub exists): legs > pax for that route.
    - If len(route) == 2 (no interior hub): legs == pax for that route.

    The aggregate assertion is NOT used here; this is per-route.

    Cannot catch: a route that has len==2 but assigns the wrong passengers.
    """
    demand = _demand_from_fixture()
    routes = _hs(demand=demand)

    for r in routes:
        legs_single = passenger_legs([r])
        if len(r.route) > 2:
            # Interior hub(s) exist — strictly more legs than passengers
            assert legs_single > r.passengers + 1e-9, (
                f"{r.origin}->{r.destination} route={r.route}: "
                f"legs={legs_single} should be > pax={r.passengers} (interior hub)"
            )
        else:
            # len(route) == 2: direct segment, no interior hub → legs == passengers
            assert abs(legs_single - r.passengers) < 1e-9, (
                f"{r.origin}->{r.destination} route={r.route}: "
                f"legs={legs_single} should == pax={r.passengers} (direct)"
            )
