"""
Tests for check_simple_path and check_hub_intermediates in lecture6/src/invariants.py.

Import scaffolding mirrors test_route_invariants.py so pytest runs regardless
of installation mode.
"""
from __future__ import annotations

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
from invariants import check_simple_path, check_hub_intermediates  # noqa: E402

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"
_AIRPORTS = ["BOS", "JFK", "ORD", "SFO", "DEN", "PHX"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tiny_hs_routes(hubs):
    demand = load_demand(str(_FIXTURE))
    return build_routes_hub_spoke(demand, hubs=hubs)


def _allpairs_hs_routes(hubs):
    """Return hub-spoke routes for every ordered pair of fixture airports."""
    demand = [
        ODDemand(o, d, 100.0)
        for o in _AIRPORTS
        for d in _AIRPORTS
        if o != d
    ]
    return build_routes_hub_spoke(demand, hubs=hubs)


def _p2p_routes():
    demand = load_demand(str(_FIXTURE))
    return build_routes_point_to_point(demand)


# ===========================================================================
# SECTION A: Real builder output must pass both validators
# ===========================================================================


class TestSimplePathOnRealBuilders:
    """Invariance tests: builders must only produce simple-path routes."""

    def test_simple_path_tiny_jfk_ord(self):
        """
        Invariance test — no tolerance (exact node-identity comparison).

        check_simple_path must return None (not raise) for every RouteFlow
        produced by build_routes_hub_spoke(tiny_demand, hubs=[JFK,ORD]).

        Cannot catch: incorrect route topology that still happens to have
        unique node sequences (e.g., a wrong hub that is not a repeat).
        """
        routes = _tiny_hs_routes(["JFK", "ORD"])
        assert check_simple_path(routes) is None

    def test_simple_path_tiny_ord_only(self):
        """
        Invariance test — no tolerance (exact node-identity comparison).

        check_simple_path must return None for hub_spoke with hubs=[ORD]
        over the tiny fixture, including routes where ORD is an endpoint
        (so the hub does not become an interior duplicate).

        Cannot catch: wrong routes that coincidentally have no repeated node.
        """
        routes = _tiny_hs_routes(["ORD"])
        assert check_simple_path(routes) is None

    def test_simple_path_allpairs_jfk_ord(self):
        """
        Property/invariance test — no tolerance.

        For every ordered pair of the six fixture airports with hubs=[JFK,ORD],
        no route produced by build_routes_hub_spoke should have a repeated node.
        30 OD pairs tested.

        Cannot catch: a rare bad pair not covered by the six airports.
        """
        routes = _allpairs_hs_routes(["JFK", "ORD"])
        assert check_simple_path(routes) is None

    def test_simple_path_allpairs_ord_only(self):
        """
        Property/invariance test — no tolerance.

        For every ordered pair of the six fixture airports with hubs=[ORD],
        no route should have a repeated node.  With a single hub, every
        non-hub endpoint-to-hub-to-endpoint path is naturally simple.
        30 OD pairs tested.

        Cannot catch: a rare bad pair not covered by the six airports.
        """
        routes = _allpairs_hs_routes(["ORD"])
        assert check_simple_path(routes) is None

    def test_simple_path_point_to_point(self):
        """
        Invariance test — no tolerance.

        Point-to-point routes are two-node paths; no node repeats are possible
        (origin != destination is guaranteed by load_demand / meaningful OD).
        check_simple_path must accept them without raising.

        Cannot catch: a degenerate O==D record (not present in tiny fixture).
        """
        routes = _p2p_routes()
        assert check_simple_path(routes) is None


class TestHubIntermediatesOnRealBuilders:
    """Invariance tests: builders must only produce routes whose interiors are hubs."""

    def test_hub_intermediates_tiny_jfk_ord(self):
        """
        Invariance test — no tolerance; case-insensitive hub comparison.

        Every interior node (route[1:-1]) produced by
        build_routes_hub_spoke(tiny_demand, hubs=[JFK,ORD]) must be in
        {JFK, ORD} (case-insensitively).  check_hub_intermediates must
        return None.

        Cannot catch: an interior node that coincidentally matches a hub
        name but was placed there incorrectly.
        """
        routes = _tiny_hs_routes(["JFK", "ORD"])
        assert check_hub_intermediates(routes, ["JFK", "ORD"]) is None

    def test_hub_intermediates_tiny_ord_only(self):
        """
        Invariance test — no tolerance; case-insensitive hub comparison.

        With hubs=[ORD], every interior node must be ORD.
        check_hub_intermediates must return None.

        Cannot catch: a route whose interior is empty (no intermediate hub).
        """
        routes = _tiny_hs_routes(["ORD"])
        assert check_hub_intermediates(routes, ["ORD"]) is None

    def test_hub_intermediates_allpairs_jfk_ord(self):
        """
        Property/invariance test — no tolerance; case-insensitive.

        For all 30 ordered OD pairs with hubs=[JFK,ORD], the interior of
        every route must be a subset of {JFK,ORD}.

        Cannot catch: incorrect routing that still routes through a valid hub.
        """
        routes = _allpairs_hs_routes(["JFK", "ORD"])
        assert check_hub_intermediates(routes, ["JFK", "ORD"]) is None

    def test_hub_intermediates_allpairs_ord_only(self):
        """
        Property/invariance test — no tolerance; case-insensitive.

        For all 30 ordered OD pairs with hubs=[ORD], the interior of every
        route must be {ORD} or empty.

        Cannot catch: incorrect routing that still routes through ORD.
        """
        routes = _allpairs_hs_routes(["ORD"])
        assert check_hub_intermediates(routes, ["ORD"]) is None

    def test_hub_intermediates_point_to_point(self):
        """
        Invariance test — no tolerance; case-insensitive.

        Point-to-point routes have empty interiors (route[1:-1] == ()),
        so check_hub_intermediates must accept them regardless of hub list.
        This verifies the 'no interior → always valid' contract.

        Cannot catch: a p2p route that accidentally has >2 nodes.
        """
        routes = _p2p_routes()
        # Any hub list is fine; interiors are all empty.
        assert check_hub_intermediates(routes, ["JFK", "ORD"]) is None

    def test_hub_intermediates_lowercase_hubs_accepted(self):
        """
        Invariance test — case-insensitive comparison explicitly exercised.

        Supplying hubs as lowercase ['jfk', 'ord'] must produce the same
        result as uppercase ['JFK', 'ORD'] — both should accept real
        builder output without raising.

        Cannot catch: a correct uppercase implementation that still passes
        after accidentally upper-casing the comparison the same way.
        """
        routes = _tiny_hs_routes(["JFK", "ORD"])
        assert check_hub_intermediates(routes, ["jfk", "ord"]) is None


# ===========================================================================
# SECTION B: Validators REJECT hand-built bad records
# ===========================================================================


class TestSimplePathRejectsBadRecords:
    """Example tests: check_simple_path must raise ValueError on violations."""

    def test_rejects_loop_at_endpoints(self):
        """
        Example test — no tolerance.

        A route (BOS, JFK, BOS, JFK) has BOS repeated (positions 0 and 2)
        and JFK repeated (positions 1 and 3).  check_simple_path must raise
        ValueError naming the offending record.

        Cannot catch: an implementation that only checks endpoint nodes,
        not interior nodes.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="JFK",
            route=("BOS", "JFK", "BOS", "JFK"),
            passengers=10.0,
        )
        with pytest.raises(ValueError, match="BOS"):
            check_simple_path([bad])

    def test_rejects_interior_repeat(self):
        """
        Example test — no tolerance.

        A route (BOS, JFK, JFK, SFO) repeats JFK in the interior.
        check_simple_path must raise ValueError mentioning the duplicate.

        Cannot catch: an implementation that only detects endpoint repeats.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="SFO",
            route=("BOS", "JFK", "JFK", "SFO"),
            passengers=5.0,
        )
        with pytest.raises(ValueError, match="JFK"):
            check_simple_path([bad])

    def test_rejects_hub_visited_twice(self):
        """
        Example test — no tolerance.

        A route (BOS, JFK, ORD, JFK, SFO) visits JFK twice (positions 1
        and 3).  A hub being used as the intermediate is still a repeated
        node and must be rejected.

        Cannot catch: an implementation that only checks non-hub repeats.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="SFO",
            route=("BOS", "JFK", "ORD", "JFK", "SFO"),
            passengers=15.0,
        )
        with pytest.raises(ValueError, match="JFK"):
            check_simple_path([bad])

    def test_rejects_single_node_loop(self):
        """
        Example test — no tolerance.

        A route (BOS, BOS) has the single node BOS repeated as both origin
        and destination.  This is a degenerate loop and must be rejected.

        Cannot catch: an implementation that special-cases length-2 routes.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="BOS",
            route=("BOS", "BOS"),
            passengers=0.0,
        )
        with pytest.raises(ValueError, match="BOS"):
            check_simple_path([bad])

    def test_rejects_first_violating_record_in_mixed_list(self):
        """
        Example test — no tolerance.

        When a list contains a good record followed by a bad record, ValueError
        is raised (at some point).  The good record before it must not suppress
        detection.

        Cannot catch: an implementation that skips the bad record because
        it appears second.
        """
        good = RouteFlow("BOS", "SFO", ("BOS", "JFK", "SFO"), 20.0)
        bad = RouteFlow("DEN", "PHX", ("DEN", "JFK", "DEN", "PHX"), 5.0)
        with pytest.raises(ValueError):
            check_simple_path([good, bad])

    def test_accepts_empty_list(self):
        """
        Example test — no tolerance.

        Vacuously valid: an empty list has no routes to violate the property.
        check_simple_path must return None.
        """
        assert check_simple_path([]) is None

    def test_accepts_single_node_route(self):
        """
        Example test — no tolerance.

        A single-node route (BOS,) has only one node; no repetition is
        possible.  check_simple_path must return None.

        Note: a single-node route would also fail check_endpoints unless
        origin==destination; here we only test check_simple_path.
        """
        ok = RouteFlow("BOS", "BOS", ("BOS",), 0.0)
        assert check_simple_path([ok]) is None

    def test_accepts_two_node_distinct_route(self):
        """
        Example test — no tolerance.

        A two-node route (BOS, JFK) is simple; check_simple_path must
        return None.
        """
        ok = RouteFlow("BOS", "JFK", ("BOS", "JFK"), 50.0)
        assert check_simple_path([ok]) is None

    def test_accepts_three_node_hub_route(self):
        """
        Example test — no tolerance.

        A three-node route (BOS, JFK, SFO) is simple; check_simple_path must
        return None.
        """
        ok = RouteFlow("BOS", "SFO", ("BOS", "JFK", "SFO"), 50.0)
        assert check_simple_path([ok]) is None


class TestHubIntermediatesRejectsBadRecords:
    """Example tests: check_hub_intermediates must raise ValueError on violations."""

    def test_rejects_interior_non_hub(self):
        """
        Example test — no tolerance; case-insensitive comparison.

        A route (BOS, SFO, JFK) has SFO as an interior node; SFO is not in
        hubs=[JFK,ORD].  check_hub_intermediates must raise ValueError naming
        the offending airport or record.

        Cannot catch: an implementation that only checks the first interior node.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="JFK",
            route=("BOS", "SFO", "JFK"),
            passengers=40.0,
        )
        with pytest.raises(ValueError, match="SFO"):
            check_hub_intermediates([bad], ["JFK", "ORD"])

    def test_rejects_non_hub_among_multiple_intermediates(self):
        """
        Example test — no tolerance; case-insensitive comparison.

        A route (BOS, JFK, DEN, SFO) has JFK (valid hub) as the first
        intermediate but DEN (not a hub) as the second.
        check_hub_intermediates must raise ValueError mentioning DEN.

        Cannot catch: an implementation that only checks the first interior node.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="SFO",
            route=("BOS", "JFK", "DEN", "SFO"),
            passengers=20.0,
        )
        with pytest.raises(ValueError, match="DEN"):
            check_hub_intermediates([bad], ["JFK", "ORD"])

    def test_rejects_interior_non_hub_case_insensitive(self):
        """
        Example test — case-insensitive comparison explicitly exercised.

        The hubs list is ['jfk','ord'] (lowercase); the bad interior node
        'SFO' must still be rejected (it's not in the hub set regardless of
        case normalisation).

        Cannot catch: an implementation that only compares after uppercasing
        but the comparison itself is wrong in a symmetric way.
        """
        bad = RouteFlow(
            origin="BOS",
            destination="JFK",
            route=("BOS", "SFO", "JFK"),
            passengers=40.0,
        )
        with pytest.raises(ValueError, match="SFO"):
            check_hub_intermediates([bad], ["jfk", "ord"])

    def test_accepts_empty_interior_direct_route(self):
        """
        Example test — no tolerance.

        A two-node route (BOS, JFK) has no interior; check_hub_intermediates
        must return None regardless of hubs list (p2p contract).

        Cannot catch: a bug that incorrectly treats endpoints as intermediates.
        """
        ok = RouteFlow("BOS", "JFK", ("BOS", "JFK"), 50.0)
        assert check_hub_intermediates([ok], ["JFK", "ORD"]) is None

    def test_accepts_single_node_route(self):
        """
        Example test — no tolerance.

        A single-node route (BOS,) also has no interior.
        check_hub_intermediates must return None.

        Cannot catch: an implementation that raises on length-1 routes.
        """
        ok = RouteFlow("BOS", "BOS", ("BOS",), 0.0)
        assert check_hub_intermediates([ok], ["JFK", "ORD"]) is None

    def test_accepts_hub_as_only_interior(self):
        """
        Example test — no tolerance; case-insensitive.

        A three-node route (BOS, JFK, SFO) has JFK as the sole interior node,
        and JFK is in hubs=[JFK,ORD].  check_hub_intermediates must return
        None.

        Cannot catch: a correct implementation — this is the intended happy path.
        """
        ok = RouteFlow("BOS", "SFO", ("BOS", "JFK", "SFO"), 50.0)
        assert check_hub_intermediates([ok], ["JFK", "ORD"]) is None

    def test_accepts_both_hubs_as_intermediates(self):
        """
        Example test — no tolerance; case-insensitive.

        A four-node route (BOS, JFK, ORD, SFO) has two interior nodes
        (JFK and ORD); both are in hubs=[JFK,ORD].
        check_hub_intermediates must return None.

        Cannot catch: an implementation that only checks the first interior.
        """
        ok = RouteFlow("BOS", "SFO", ("BOS", "JFK", "ORD", "SFO"), 50.0)
        assert check_hub_intermediates([ok], ["JFK", "ORD"]) is None

    def test_accepts_empty_list(self):
        """
        Example test — no tolerance.

        Vacuously valid: no routes means no violations.
        """
        assert check_hub_intermediates([], ["JFK", "ORD"]) is None

    def test_rejects_first_bad_in_mixed_list(self):
        """
        Example test — no tolerance.

        A list with a valid record followed by an invalid one must still raise.
        Validates that check_hub_intermediates does not short-circuit on the
        first good record.

        Cannot catch: an implementation that raises on any bad record but
        reports the wrong one.
        """
        good = RouteFlow("BOS", "SFO", ("BOS", "JFK", "SFO"), 10.0)
        bad = RouteFlow("DEN", "PHX", ("DEN", "BOS", "PHX"), 5.0)  # BOS interior, not a hub
        with pytest.raises(ValueError, match="BOS"):
            check_hub_intermediates([good, bad], ["JFK", "ORD"])

    def test_hub_as_endpoint_not_counted_as_intermediate(self):
        """
        Example test — no tolerance.

        In the route (JFK, ORD) the nodes JFK and ORD are endpoints (not
        interior nodes), so check_hub_intermediates must return None even
        with a hub list that does not contain BOS or SFO.

        This verifies the slice route[1:-1] correctly excludes endpoints.

        Cannot catch: an implementation that inspects route[0] or route[-1]
        as intermediates.
        """
        ok = RouteFlow("JFK", "ORD", ("JFK", "ORD"), 55.0)
        assert check_hub_intermediates([ok], ["JFK", "ORD"]) is None

    def test_accepts_lowercase_node_matching_hub(self):
        """
        Example test — case-insensitive comparison explicitly exercised for
        ROUTE NODES (not just hub list).

        A route with an interior node supplied as 'jfk' (lowercase) must be
        accepted when hubs=['JFK','ORD'], because the comparison is
        case-insensitive.  This specifically targets an implementation that
        forgets to call node.upper() when comparing against hub_upper.

        Cannot catch: a correct implementation — this is the happy-path.
        """
        ok = RouteFlow("BOS", "SFO", ("BOS", "jfk", "SFO"), 50.0)
        assert check_hub_intermediates([ok], ["JFK", "ORD"]) is None

    def test_rejects_lowercase_node_not_in_hubs(self):
        """
        Example test — case-insensitive comparison explicitly exercised.

        A route with an interior node 'sfo' (lowercase) must be REJECTED
        when hubs=['JFK','ORD'] (neither contains SFO in any case).
        This catches an implementation that forgets node.upper() and thus
        would incorrectly accept 'sfo' because 'sfo' != 'JFK' and 'sfo' != 'ORD'.

        Cannot catch: a broken implementation that never raises at all.
        """
        bad = RouteFlow("BOS", "JFK", ("BOS", "sfo", "JFK"), 40.0)
        with pytest.raises(ValueError):
            check_hub_intermediates([bad], ["JFK", "ORD"])
