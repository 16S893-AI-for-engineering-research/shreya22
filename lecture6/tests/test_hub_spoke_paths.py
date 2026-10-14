"""Tests for build_routes_hub_spoke using hubs.hub_of as single source of truth.

This file verifies:
  - build_routes_hub_spoke delegates hub-assignment to hubs.hub_of (no duplicate rule).
  - Per-OD re-aggregation equals original demand (abs 1e-9).
  - Correct path structure: [O,H,D] when H(O)==H(D), compressed when O or D is hub.
  - [O,H(O),H(D),D] when H(O)!=H(D), same compression.
  - Routes satisfy invariants.check_endpoints and check_nonnegative.
  - Interior nodes are always hubs.
  - Default assign (None), explicit assign mapping BOS->ORD and SFO->JFK, three hubs.
  - Differential test: new build_routes_hub_spoke vs an independent reference
    implementation written here, over all ordered airport pairs and several hub sets.

Case-handling note:
  The old build_routes_hub_spoke closure did ``assign[node]`` without uppercasing
  lookup keys; hubs.hub_of normalises assign keys to uppercase.  A test probing
  lower-case assign keys is included; if the refactored code correctly delegates
  to hubs.hub_of, it will pass case-insensitive assign keys transparently.
  (The old closure did NOT; this is the only deliberate behaviour change and it
  is documented here.)

Test kinds used: example, invariance, property, differential, metamorphic.
"""
from __future__ import annotations

import pathlib
import sys
from collections import defaultdict
from itertools import permutations
from typing import Dict, Iterable, List, Optional, Tuple

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from hubs import hub_of  # noqa: E402
from invariants import check_endpoints, check_nonnegative  # noqa: E402
from networks import (  # noqa: E402
    ODDemand,
    RouteFlow,
    build_routes_hub_spoke,
    load_demand,
)

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"

# All airports that appear in tiny_demand.csv
_ALL_AIRPORTS = ("BOS", "JFK", "ORD", "SFO", "DEN", "PHX")

# ---------------------------------------------------------------------------
# Reference implementation (independent, written in this test file)
# ---------------------------------------------------------------------------


def _reference_build_hub_spoke(
    demand: Iterable[ODDemand],
    hubs: Iterable[str],
    assign: Optional[Dict[str, str]] = None,
) -> List[RouteFlow]:
    """Minimal reference implementation of build_routes_hub_spoke.

    Written entirely in terms of hubs.hub_of; used only in differential tests.
    This has NO shared code with networks.py — it is an independently written
    oracle based purely on the specification.

    Rules (verbatim from spec):
    - H(O)==H(D): [O, H(O), D] compressed — drop H when O==H or D==H.
    - H(O)!=H(D): [O, H(O), H(D), D] compressed — drop first interior if O==H(O),
      drop last interior if D==H(D).
    - One RouteFlow per OD, passengers unchanged.
    """
    hubs_tuple = tuple(h.upper() for h in hubs)
    routes: List[RouteFlow] = []
    for rec in demand:
        ho = hub_of(rec.origin, hubs_tuple, assign)
        hd = hub_of(rec.destination, hubs_tuple, assign)
        if ho == hd:
            path: List[str] = [rec.origin]
            if rec.origin != ho:
                path.append(ho)
            if rec.destination != ho:  # ho==hd, so also != hd
                path.append(rec.destination)
        else:
            path = [rec.origin]
            if rec.origin != ho:
                path.append(ho)
            if hd not in path:  # avoid duplicate if ho==hd (can't happen here)
                path.append(hd)
            if rec.destination != hd:
                path.append(rec.destination)
        routes.append(
            RouteFlow(rec.origin, rec.destination, tuple(path), rec.passengers)
        )
    return routes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _demand_from_csv() -> List[ODDemand]:
    return load_demand(str(_FIXTURE))


def _all_od_demand(airports: Tuple[str, ...]) -> List[ODDemand]:
    """Create 1 passenger for every ordered pair of distinct airports."""
    return [
        ODDemand(o, d, 1.0)
        for o, d in permutations(airports, 2)
    ]


def _reaggregate(routes: List[RouteFlow]) -> Dict[Tuple[str, str], float]:
    totals: Dict[Tuple[str, str], float] = defaultdict(float)
    for r in routes:
        totals[(r.origin, r.destination)] += r.passengers
    return dict(totals)


def _interior_nodes(route: Tuple[str, ...]) -> Tuple[str, ...]:
    """Return nodes strictly between the first and last."""
    if len(route) <= 2:
        return ()
    return route[1:-1]


# ---------------------------------------------------------------------------
# 1. USES hubs.hub_of: structural delegation test
# ---------------------------------------------------------------------------


def test_build_routes_hub_spoke_uses_hub_of_via_delegation():
    """Invariance test — no tolerance.

    build_routes_hub_spoke must delegate hub assignment exclusively to
    hubs.hub_of.  We verify this by monkey-patching _hubs_module.hub_of in the
    networks module and checking the patch is actually invoked.

    networks.py imports hubs as ``_hubs_module`` and calls
    ``_hubs_module.hub_of(...)``; patching ``networks_mod._hubs_module.hub_of``
    intercepts those calls.  If networks.py contained its own closure, there
    would be no such attribute to patch and the recording function would not
    be called.

    Cannot catch: an implementation that imports hub_of by name at module level
    (``from hubs import hub_of``) rather than via the module attribute.  Using
    the module-attribute call pattern (``_hubs_module.hub_of``) ensures the
    patch is honoured.
    """
    import networks as networks_mod

    # networks.py imports hubs as _hubs_module; verify the attribute exists
    assert hasattr(networks_mod, "_hubs_module"), (
        "networks module has no '_hubs_module' attribute. "
        "build_routes_hub_spoke must import hubs as _hubs_module and call "
        "_hubs_module.hub_of(...)."
    )

    calls: List[Tuple] = []
    original = networks_mod._hubs_module.hub_of

    def recording_hub_of(node, h, assign=None):
        calls.append((node, tuple(h) if hasattr(h, "__iter__") else h, assign))
        return original(node, h, assign)

    # Patch the hub_of that networks.py will see
    networks_mod._hubs_module.hub_of = recording_hub_of
    try:
        demand = [ODDemand("BOS", "SFO", 10.0)]
        build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
        assert len(calls) >= 2, (
            f"Expected _hubs_module.hub_of to be called at least twice (once per endpoint), "
            f"but got {len(calls)} calls. "
            "This means build_routes_hub_spoke is NOT delegating to hubs.hub_of."
        )
    finally:
        networks_mod._hubs_module.hub_of = original


# ---------------------------------------------------------------------------
# 2. Per-OD re-aggregation equals original demand (abs 1e-9)
# ---------------------------------------------------------------------------


def test_per_od_conservation_default_assign():
    """Property test — tolerance abs 1e-9.

    Tolerance reason: each OD maps to exactly one RouteFlow (no splitting in
    this implementation), so the per-OD sum should match to floating-point
    precision.  1e-9 guards against any micro-rounding without being loose.

    Cannot catch: a bug that cancels out two equal-and-opposite errors.
    """
    demand = _demand_from_csv()
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    od_demand = _reaggregate(list(ODDemand(r.origin, r.destination, r.passengers) for r in
                                  [ODDemand(d.origin, d.destination, d.passengers) for d in demand]))
    # Rebuild from raw demand
    od_expected: Dict[Tuple[str, str], float] = defaultdict(float)
    for d in demand:
        od_expected[(d.origin, d.destination)] += d.passengers
    od_got = _reaggregate(routes)
    for od, exp in od_expected.items():
        got = od_got.get(od, 0.0)
        assert abs(got - exp) <= 1e-9, (
            f"Per-OD conservation failed for {od}: got={got}, expected={exp}, diff={abs(got-exp)}"
        )


def test_per_od_conservation_explicit_assign():
    """Property test — tolerance abs 1e-9.

    Tolerance reason: same as test_per_od_conservation_default_assign.

    Tests explicit assign: BOS->ORD, SFO->JFK (non-default mapping).
    """
    demand = _demand_from_csv()
    assign = {"BOS": "ORD", "SFO": "JFK"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    od_expected: Dict[Tuple[str, str], float] = defaultdict(float)
    for d in demand:
        od_expected[(d.origin, d.destination)] += d.passengers
    od_got = _reaggregate(routes)
    for od, exp in od_expected.items():
        got = od_got.get(od, 0.0)
        assert abs(got - exp) <= 1e-9, (
            f"Per-OD conservation failed for {od} with explicit assign: "
            f"got={got}, expected={exp}"
        )


def test_per_od_conservation_three_hubs():
    """Property test — tolerance abs 1e-9.

    Tolerance reason: same as above.

    Tests three hubs: JFK, ORD, DEN.  Default assign maps every non-hub to
    first hub (JFK).
    """
    demand = _demand_from_csv()
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD", "DEN"])
    od_expected: Dict[Tuple[str, str], float] = defaultdict(float)
    for d in demand:
        od_expected[(d.origin, d.destination)] += d.passengers
    od_got = _reaggregate(routes)
    for od, exp in od_expected.items():
        got = od_got.get(od, 0.0)
        assert abs(got - exp) <= 1e-9, (
            f"Per-OD conservation failed (3 hubs) for {od}: got={got}, expected={exp}"
        )


def test_per_od_conservation_all_pairs_default():
    """Property test — tolerance abs 1e-9.

    Covers ALL ordered pairs of the 6 known airports with 1 passenger each,
    using default assign.  Ensures conservation holds over a much wider input
    space than the CSV fixture alone.

    Cannot catch: bugs that only manifest with fractional passenger counts.
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    for hub_set in [["JFK", "ORD"], ["JFK", "ORD", "DEN"], ["BOS"]]:
        routes = build_routes_hub_spoke(demand, hubs=hub_set)
        od_expected: Dict[Tuple[str, str], float] = defaultdict(float)
        for d in demand:
            od_expected[(d.origin, d.destination)] += d.passengers
        od_got = _reaggregate(routes)
        for od, exp in od_expected.items():
            got = od_got.get(od, 0.0)
            assert abs(got - exp) <= 1e-9, (
                f"Conservation failed for hub_set={hub_set}, OD={od}: "
                f"got={got}, expected={exp}"
            )


# ---------------------------------------------------------------------------
# 3. Path structure: H(O)==H(D) cases
# ---------------------------------------------------------------------------


def test_same_hub_non_hub_non_hub():
    """Example test — no tolerance (exact route tuple comparison).

    BOS and SFO both map to JFK (default assign).  H(BOS)==H(SFO)==JFK.
    Expected route: (BOS, JFK, SFO) — neither endpoint is a hub.
    """
    demand = [ODDemand("BOS", "SFO", 10.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "JFK", "SFO"), (
        f"Expected (BOS, JFK, SFO), got {routes[0].route}"
    )


def test_same_hub_origin_is_hub():
    """Example test — no tolerance.

    JFK->SFO with hubs=[JFK, ORD].  H(JFK)==JFK==H(SFO).
    Compression: O==H(O) so drop H from interior.
    Expected route: (JFK, SFO).
    """
    demand = [ODDemand("JFK", "SFO", 5.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert len(routes) == 1
    assert routes[0].route == ("JFK", "SFO"), (
        f"Expected (JFK, SFO), got {routes[0].route}"
    )


def test_same_hub_destination_is_hub():
    """Example test — no tolerance.

    BOS->JFK with hubs=[JFK, ORD].  H(BOS)==JFK==H(JFK)==JFK.  H(O)==H(D).
    Compression: D==H(D) so drop trailing hub.
    Expected route: (BOS, JFK).
    """
    demand = [ODDemand("BOS", "JFK", 20.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "JFK"), (
        f"Expected (BOS, JFK), got {routes[0].route}"
    )


def test_same_hub_both_endpoints_are_same_hub():
    """Example test — no tolerance.

    JFK->JFK with hubs=[JFK, ORD].  H(O)==H(D)==JFK, O==D==hub.
    Expected route: (JFK,) — single node, no duplication.
    """
    demand = [ODDemand("JFK", "JFK", 3.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert len(routes) == 1
    assert routes[0].route == ("JFK",), (
        f"Expected (JFK,), got {routes[0].route}"
    )


def test_same_hub_hub_to_hub_same():
    """Example test — no tolerance.

    JFK->JFK with hubs=[JFK].  Single-hub, H(O)==H(D)==JFK.
    Expected route: (JFK,).
    """
    demand = [ODDemand("JFK", "JFK", 1.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK"])
    assert len(routes) == 1
    assert routes[0].route == ("JFK",), (
        f"Expected (JFK,), got {routes[0].route}"
    )


# ---------------------------------------------------------------------------
# 4. Path structure: H(O) != H(D) cases
# ---------------------------------------------------------------------------


def test_different_hub_non_hub_non_hub():
    """Example test — no tolerance.

    BOS (H=JFK by explicit assign) -> DEN (H=ORD by explicit assign).  H(O)!=H(D).
    Without explicit assign, both BOS and DEN would map to JFK (first hub default).
    Expected route: (BOS, JFK, ORD, DEN).
    """
    demand = [ODDemand("BOS", "DEN", 15.0)]
    assign = {"BOS": "JFK", "DEN": "ORD"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "JFK", "ORD", "DEN"), (
        f"Expected (BOS, JFK, ORD, DEN), got {routes[0].route}"
    )


def test_different_hub_origin_is_hub():
    """Example test — no tolerance.

    JFK->DEN with hubs=[JFK, ORD], explicit assign DEN->ORD.
    H(JFK)==JFK, H(DEN)==ORD (via assign).  H(O)!=H(D).
    Compression: O==H(O), drop first interior.
    Expected route: (JFK, ORD, DEN).

    Note: without explicit assign, H(DEN)=JFK (first hub default), making
    H(O)==H(D)==JFK so the route would be (JFK, DEN) instead.
    """
    demand = [ODDemand("JFK", "DEN", 8.0)]
    assign = {"DEN": "ORD"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    assert len(routes) == 1
    assert routes[0].route == ("JFK", "ORD", "DEN"), (
        f"Expected (JFK, ORD, DEN), got {routes[0].route}"
    )


def test_different_hub_destination_is_hub():
    """Example test — no tolerance.

    BOS->ORD with hubs=[JFK, ORD].  H(BOS)==JFK, H(ORD)==ORD.  H(O)!=H(D).
    Compression: D==H(D), drop last interior.
    Expected route: (BOS, JFK, ORD).
    """
    demand = [ODDemand("BOS", "ORD", 12.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "JFK", "ORD"), (
        f"Expected (BOS, JFK, ORD), got {routes[0].route}"
    )


def test_different_hub_both_endpoints_are_hubs():
    """Example test — no tolerance.

    JFK->ORD with hubs=[JFK, ORD].  H(JFK)==JFK, H(ORD)==ORD.  H(O)!=H(D).
    Both endpoints are hubs; compress both interior nodes.
    Expected route: (JFK, ORD).
    """
    demand = [ODDemand("JFK", "ORD", 25.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert len(routes) == 1
    assert routes[0].route == ("JFK", "ORD"), (
        f"Expected (JFK, ORD), got {routes[0].route}"
    )


def test_explicit_assign_bos_ord_sfo_jfk_path():
    """Example test — no tolerance.

    assign={BOS: ORD, SFO: JFK}.  BOS->SFO: H(BOS)==ORD, H(SFO)==JFK.
    H(O)!=H(D).  Expected route: (BOS, ORD, JFK, SFO).
    """
    demand = [ODDemand("BOS", "SFO", 7.0)]
    assign = {"BOS": "ORD", "SFO": "JFK"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "ORD", "JFK", "SFO"), (
        f"Expected (BOS, ORD, JFK, SFO), got {routes[0].route}"
    )


def test_explicit_assign_bos_ord_same_hub_path():
    """Example test — no tolerance.

    assign={BOS: ORD, DEN: ORD}.  BOS->DEN: H(BOS)==ORD, H(DEN)==ORD.
    H(O)==H(D).  Expected route: (BOS, ORD, DEN).
    """
    demand = [ODDemand("BOS", "DEN", 7.0)]
    assign = {"BOS": "ORD", "DEN": "ORD"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "ORD", "DEN"), (
        f"Expected (BOS, ORD, DEN), got {routes[0].route}"
    )


def test_three_hubs_default_assign():
    """Example test — no tolerance.

    hubs=[JFK, ORD, DEN], assign=None.  BOS (H=JFK) -> SFO (H=JFK).
    H(O)==H(D).  Expected route: (BOS, JFK, SFO).
    """
    demand = [ODDemand("BOS", "SFO", 5.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD", "DEN"])
    assert len(routes) == 1
    assert routes[0].route == ("BOS", "JFK", "SFO"), (
        f"Expected (BOS, JFK, SFO), got {routes[0].route}"
    )


def test_three_hubs_den_is_hub():
    """Example test — no tolerance.

    hubs=[JFK, ORD, DEN].  DEN->SFO: H(DEN)==DEN (it's a hub), H(SFO)==JFK.
    H(O)!=H(D).  Expected route: (DEN, JFK, SFO).
    """
    demand = [ODDemand("DEN", "SFO", 5.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD", "DEN"])
    assert len(routes) == 1
    assert routes[0].route == ("DEN", "JFK", "SFO"), (
        f"Expected (DEN, JFK, SFO), got {routes[0].route}"
    )


def test_three_hubs_den_to_ord():
    """Example test — no tolerance.

    hubs=[JFK, ORD, DEN].  DEN->ORD: H(DEN)==DEN, H(ORD)==ORD.
    H(O)!=H(D).  Expected route: (DEN, ORD).
    """
    demand = [ODDemand("DEN", "ORD", 4.0)]
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD", "DEN"])
    assert len(routes) == 1
    assert routes[0].route == ("DEN", "ORD"), (
        f"Expected (DEN, ORD), got {routes[0].route}"
    )


# ---------------------------------------------------------------------------
# 5. Invariants: check_endpoints and check_nonnegative
# ---------------------------------------------------------------------------


def test_invariants_default_assign_csv():
    """Invariance test — no tolerance for endpoints; abs 1e-9 for nonneg.

    Tolerance reason: endpoints are string equality (no float); nonneg
    tolerance 1e-9 matches the project standard for float passenger noise.

    All routes from build_routes_hub_spoke on the CSV fixture with default
    assign must satisfy both invariants.
    """
    demand = _demand_from_csv()
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    assert check_endpoints(routes) is None
    assert check_nonnegative(routes) is None


def test_invariants_explicit_assign():
    """Invariance test — same tolerances as test_invariants_default_assign_csv.

    Tests explicit assign={BOS: ORD, SFO: JFK}.
    """
    demand = _demand_from_csv()
    assign = {"BOS": "ORD", "SFO": "JFK"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    assert check_endpoints(routes) is None
    assert check_nonnegative(routes) is None


def test_invariants_three_hubs():
    """Invariance test — same tolerances.

    Tests three hubs: JFK, ORD, DEN.
    """
    demand = _demand_from_csv()
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD", "DEN"])
    assert check_endpoints(routes) is None
    assert check_nonnegative(routes) is None


def test_invariants_all_pairs_all_hub_sets():
    """Invariance test — no tolerance for endpoints; abs 1e-9 for nonneg.

    Covers all ordered pairs of 6 airports across multiple hub configurations.
    Cannot catch: bugs that only appear with very large passenger counts.
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    for hub_set in [["JFK", "ORD"], ["JFK", "ORD", "DEN"], ["BOS"], ["ORD", "SFO"]]:
        routes = build_routes_hub_spoke(demand, hubs=hub_set)
        assert check_endpoints(routes) is None, f"endpoint invariant failed for hubs={hub_set}"
        assert check_nonnegative(routes) is None, f"nonneg invariant failed for hubs={hub_set}"


# ---------------------------------------------------------------------------
# 6. Interior nodes are always hubs
# ---------------------------------------------------------------------------


def test_interior_nodes_are_hubs_default():
    """Invariance test — no tolerance.

    For every route produced by build_routes_hub_spoke, every INTERIOR node
    (i.e., not route[0] or route[-1]) must be a member of the hubs set.

    Cannot catch: bugs in routes where origin or destination accidentally equal
    a hub (which is valid and makes the interior look empty).
    """
    demand = _demand_from_csv()
    hub_list = ["JFK", "ORD"]
    hub_set = set(hub_list)
    routes = build_routes_hub_spoke(demand, hubs=hub_list)
    for r in routes:
        interior = _interior_nodes(r.route)
        for node in interior:
            assert node in hub_set, (
                f"Interior node {node!r} is not a hub in {hub_set} "
                f"for route {r.route} (OD: {r.origin}->{r.destination})"
            )


def test_interior_nodes_are_hubs_explicit_assign():
    """Invariance test — no tolerance.

    Same as above, with explicit assign={BOS: ORD, SFO: JFK}.
    """
    demand = _demand_from_csv()
    hub_list = ["JFK", "ORD"]
    hub_set = set(hub_list)
    assign = {"BOS": "ORD", "SFO": "JFK"}
    routes = build_routes_hub_spoke(demand, hubs=hub_list, assign=assign)
    for r in routes:
        interior = _interior_nodes(r.route)
        for node in interior:
            assert node in hub_set, (
                f"Interior node {node!r} is not a hub for route {r.route}"
            )


def test_interior_nodes_are_hubs_three_hubs():
    """Invariance test — no tolerance.

    Same check with three hubs: JFK, ORD, DEN.
    """
    demand = _demand_from_csv()
    hub_list = ["JFK", "ORD", "DEN"]
    hub_set = set(hub_list)
    routes = build_routes_hub_spoke(demand, hubs=hub_list)
    for r in routes:
        interior = _interior_nodes(r.route)
        for node in interior:
            assert node in hub_set, (
                f"Interior node {node!r} is not a hub (set={hub_set}) "
                f"for route {r.route} (OD: {r.origin}->{r.destination})"
            )


def test_interior_nodes_are_hubs_all_pairs():
    """Invariance test — no tolerance.

    All ordered pairs of 6 airports, multiple hub sets.  Every interior node
    must always be a hub.
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    for hub_list in [["JFK", "ORD"], ["JFK", "ORD", "DEN"], ["BOS"], ["ORD", "SFO"]]:
        hub_set = set(hub_list)
        routes = build_routes_hub_spoke(demand, hubs=hub_list)
        for r in routes:
            interior = _interior_nodes(r.route)
            for node in interior:
                assert node in hub_set, (
                    f"Interior node {node!r} not a hub (hubs={hub_list}) "
                    f"route {r.route}"
                )


# ---------------------------------------------------------------------------
# 7. No duplicate consecutive nodes in any route
# ---------------------------------------------------------------------------


def test_no_duplicate_consecutive_nodes():
    """Invariance test — no tolerance.

    Every route must have no repeated consecutive node (compression must be
    applied correctly).  A route like (BOS, JFK, JFK, SFO) indicates that
    compression was not applied when an endpoint equals its own hub.

    Cannot catch: non-consecutive repeats, which are structurally impossible
    in a 2-hub chain but possible with 3+ hubs in exotic configurations.
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    for hub_list in [["JFK", "ORD"], ["JFK", "ORD", "DEN"], ["BOS"]]:
        routes = build_routes_hub_spoke(demand, hubs=hub_list)
        for r in routes:
            for i in range(len(r.route) - 1):
                assert r.route[i] != r.route[i + 1], (
                    f"Consecutive duplicate {r.route[i]!r} in route {r.route} "
                    f"(OD: {r.origin}->{r.destination}, hubs={hub_list})"
                )


# ---------------------------------------------------------------------------
# 8. Metamorphic: route length is 1-4 nodes, never 0
# ---------------------------------------------------------------------------


def test_route_length_range():
    """Metamorphic test — no tolerance.

    For two hubs, every route has between 1 and 4 nodes:
    - min 1: O==D==hub (degenerate OD)
    - max 4: non-hub O, H(O)!=H(D), non-hub D

    Cannot catch: bugs that produce routes of valid length but wrong structure.
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    for hub_list in [["JFK", "ORD"]]:
        routes = build_routes_hub_spoke(demand, hubs=hub_list)
        for r in routes:
            assert 1 <= len(r.route) <= 4, (
                f"Route {r.route} has length {len(r.route)}, "
                f"expected 1-4 (OD: {r.origin}->{r.destination}, hubs={hub_list})"
            )


# ---------------------------------------------------------------------------
# 9. Differential test: new impl vs reference, all pairs, multiple hub sets
# ---------------------------------------------------------------------------


def test_differential_vs_reference_default_assign_csv():
    """Differential test — no tolerance (exact route tuple equality).

    Compares build_routes_hub_spoke on CSV demand (default assign, hubs=JFK,ORD)
    against the independent reference implementation in this file.

    Cannot catch: bugs that both implementations share because they both call
    hubs.hub_of (the reference does too).  The reference IS structurally
    independent — it builds paths with explicit if/else rather than reusing
    networks.py logic.
    """
    demand = _demand_from_csv()
    hubs_list = ["JFK", "ORD"]
    got = {(r.origin, r.destination): r.route
           for r in build_routes_hub_spoke(demand, hubs=hubs_list)}
    ref = {(r.origin, r.destination): r.route
           for r in _reference_build_hub_spoke(demand, hubs=hubs_list)}
    assert got == ref, (
        "Route tuples differ between build_routes_hub_spoke and reference:\n"
        + "\n".join(f"  OD {od}: got={got.get(od)}, ref={ref.get(od)}"
                    for od in set(got) | set(ref) if got.get(od) != ref.get(od))
    )


def test_differential_vs_reference_explicit_assign():
    """Differential test — no tolerance.

    Explicit assign: BOS->ORD, SFO->JFK.  Tests that the refactored code
    and reference agree on every OD in the CSV fixture.
    """
    demand = _demand_from_csv()
    hubs_list = ["JFK", "ORD"]
    assign = {"BOS": "ORD", "SFO": "JFK"}
    got = {(r.origin, r.destination): r.route
           for r in build_routes_hub_spoke(demand, hubs=hubs_list, assign=assign)}
    ref = {(r.origin, r.destination): r.route
           for r in _reference_build_hub_spoke(demand, hubs=hubs_list, assign=assign)}
    assert got == ref, (
        "Explicit-assign routes differ:\n"
        + "\n".join(f"  OD {od}: got={got.get(od)}, ref={ref.get(od)}"
                    for od in set(got) | set(ref) if got.get(od) != ref.get(od))
    )


def test_differential_vs_reference_three_hubs():
    """Differential test — no tolerance.

    Three hubs: JFK, ORD, DEN.  Default assign on CSV fixture.
    """
    demand = _demand_from_csv()
    hubs_list = ["JFK", "ORD", "DEN"]
    got = {(r.origin, r.destination): r.route
           for r in build_routes_hub_spoke(demand, hubs=hubs_list)}
    ref = {(r.origin, r.destination): r.route
           for r in _reference_build_hub_spoke(demand, hubs=hubs_list)}
    assert got == ref, (
        "Three-hub routes differ:\n"
        + "\n".join(f"  OD {od}: got={got.get(od)}, ref={ref.get(od)}"
                    for od in set(got) | set(ref) if got.get(od) != ref.get(od))
    )


def test_differential_vs_reference_all_pairs_multiple_hub_sets():
    """Differential test — no tolerance (exact route tuple equality).

    The most comprehensive differential test: every ordered pair of 6 airports,
    hub sets: [JFK,ORD], [JFK,ORD,DEN], [BOS], [ORD,SFO].
    Default assign (None) for all.

    This is the primary guard against any divergence between the refactored
    build_routes_hub_spoke and the reference implementation.

    Cannot catch: bugs shared by both implementations (both call hubs.hub_of).
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    for hub_list in [["JFK", "ORD"], ["JFK", "ORD", "DEN"], ["BOS"], ["ORD", "SFO"]]:
        got = {(r.origin, r.destination): r.route
               for r in build_routes_hub_spoke(demand, hubs=hub_list)}
        ref = {(r.origin, r.destination): r.route
               for r in _reference_build_hub_spoke(demand, hubs=hub_list)}
        diffs = [(od, got.get(od), ref.get(od))
                 for od in set(got) | set(ref) if got.get(od) != ref.get(od)]
        assert not diffs, (
            f"Differential failures for hubs={hub_list}:\n"
            + "\n".join(f"  OD {od}: got={g}, ref={r}" for od, g, r in diffs)
        )


def test_differential_vs_reference_explicit_assign_all_pairs():
    """Differential test — no tolerance.

    Explicit assign: BOS->ORD, SFO->JFK over ALL ordered airport pairs.
    Verifies the explicit assign path is fully delegated to hubs.hub_of.
    """
    demand = _all_od_demand(_ALL_AIRPORTS)
    hubs_list = ["JFK", "ORD"]
    assign = {"BOS": "ORD", "SFO": "JFK"}
    got = {(r.origin, r.destination): r.route
           for r in build_routes_hub_spoke(demand, hubs=hubs_list, assign=assign)}
    ref = {(r.origin, r.destination): r.route
           for r in _reference_build_hub_spoke(demand, hubs=hubs_list, assign=assign)}
    diffs = [(od, got.get(od), ref.get(od))
             for od in set(got) | set(ref) if got.get(od) != ref.get(od)]
    assert not diffs, (
        "Explicit-assign all-pairs differential failures:\n"
        + "\n".join(f"  OD {od}: got={g}, ref={r}" for od, g, r in diffs)
    )


# ---------------------------------------------------------------------------
# 10. Case-handling: hubs.hub_of normalises assign keys to uppercase
# ---------------------------------------------------------------------------


def test_lowercase_assign_keys_handled_by_hub_of():
    """Example test — no tolerance.

    hubs.hub_of normalises assign keys to uppercase before lookup.
    The old closure in networks.py used ``assign[node]`` without normalising,
    so a lower-case key like {'bos': 'ORD'} would NOT match 'BOS'.
    After refactoring to use hubs.hub_of, lower-case assign keys MUST work.

    This test WILL FAIL before the refactoring (old closure) and PASS after.
    It is included here to document the only deliberate behaviour change.
    """
    demand = [ODDemand("BOS", "SFO", 10.0)]
    # lower-case key: old closure fails to match; hubs.hub_of succeeds
    assign = {"bos": "ORD", "sfo": "JFK"}
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=assign)
    assert len(routes) == 1
    # BOS -> ORD hub, SFO -> JFK hub; H(O)!=H(D) -> (BOS, ORD, JFK, SFO)
    assert routes[0].route == ("BOS", "ORD", "JFK", "SFO"), (
        f"Expected (BOS, ORD, JFK, SFO) with lower-case assign keys, "
        f"got {routes[0].route}"
    )


# ---------------------------------------------------------------------------
# 11. RouteFlow fields: origin/destination/passengers preserved
# ---------------------------------------------------------------------------


def test_routeflow_fields_preserved():
    """Invariance test — no tolerance for string fields; abs 1e-15 for passengers.

    Tolerance reason: passengers are copied directly without arithmetic, so
    they should be bit-identical.  1e-15 guards against any future change that
    might introduce a trivial float operation.

    For every RouteFlow, origin/destination must equal the OD record's, and
    passengers must be bit-identical.
    """
    demand = _demand_from_csv()
    routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    demand_map = {(d.origin, d.destination): d.passengers for d in demand}
    for r in routes:
        assert r.origin == r.route[0], f"origin mismatch: {r.origin} vs {r.route[0]}"
        assert r.destination == r.route[-1], f"destination mismatch: {r.destination} vs {r.route[-1]}"
        expected_pax = demand_map[(r.origin, r.destination)]
        assert abs(r.passengers - expected_pax) <= 1e-15, (
            f"passengers not preserved for {r.origin}->{r.destination}: "
            f"{r.passengers} vs {expected_pax}"
        )


# ---------------------------------------------------------------------------
# 12. Error cases
# ---------------------------------------------------------------------------


def test_empty_hubs_raises():
    """Example test — no tolerance.

    Passing an empty hubs list must raise ValueError (cannot route anywhere).
    """
    with pytest.raises(ValueError):
        build_routes_hub_spoke([ODDemand("BOS", "JFK", 1.0)], hubs=[])


def test_invalid_assign_raises():
    """Example test — no tolerance.

    Assigning a node to a non-existent hub must raise ValueError.
    """
    with pytest.raises(ValueError, match="LAX"):
        build_routes_hub_spoke(
            [ODDemand("BOS", "SFO", 1.0)],
            hubs=["JFK", "ORD"],
            assign={"BOS": "LAX"},
        )
