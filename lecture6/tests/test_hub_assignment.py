"""Tests for lecture6/src/hubs.py :: hub_of(node, hubs, assign=None).

Hubs used throughout: JFK and ORD.
"""
from __future__ import annotations

import pathlib
import sys

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from hubs import hub_of  # noqa: E402 — module under test
from networks import load_demand, build_routes_hub_spoke  # noqa: E402

_HUBS = ("JFK", "ORD")
_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"


# ---------------------------------------------------------------------------
# Example tests: hubs map to themselves
# ---------------------------------------------------------------------------

def test_hub_maps_to_itself_jfk():
    """Example test, no tolerance.

    A node that IS a hub must map to itself — foundational contract.
    """
    assert hub_of("JFK", _HUBS) == "JFK"


def test_hub_maps_to_itself_ord():
    """Example test, no tolerance.

    Same foundational contract for the second hub.
    """
    assert hub_of("ORD", _HUBS) == "ORD"


# ---------------------------------------------------------------------------
# Example test: non-hub with no assign → first hub
# ---------------------------------------------------------------------------

def test_non_hub_no_assign_maps_to_first_hub():
    """Example test, no tolerance.

    With assign=None a non-hub node must map to the first hub in the list.
    Verifies the default-assignment rule documented in networks.py.
    """
    assert hub_of("BOS", _HUBS) == "JFK"


def test_non_hub_no_assign_always_first_hub():
    """Example test, no tolerance.

    Every distinct non-hub node (from tiny fixture) maps to first hub when
    assign=None.  Covers more nodes without parametrize overhead.
    """
    non_hubs = ("BOS", "SFO", "DEN", "PHX")
    for node in non_hubs:
        result = hub_of(node, _HUBS)
        assert result == "JFK", (
            f"hub_of({node!r}, {_HUBS}) returned {result!r}, expected 'JFK'"
        )


# ---------------------------------------------------------------------------
# Example test: explicit assign respected
# ---------------------------------------------------------------------------

def test_explicit_assign_overrides_default():
    """Example test, no tolerance.

    When assign maps a non-hub to the *second* hub, that mapping must be
    honoured instead of falling back to first hub.
    """
    result = hub_of("BOS", _HUBS, assign={"BOS": "ORD"})
    assert result == "ORD"


def test_explicit_assign_multiple_nodes():
    """Example test, no tolerance.

    Multiple nodes each assigned to different hubs; confirms per-node lookup
    is independent.
    """
    assign = {"BOS": "ORD", "SFO": "JFK", "DEN": "ORD"}
    assert hub_of("BOS", _HUBS, assign=assign) == "ORD"
    assert hub_of("SFO", _HUBS, assign=assign) == "JFK"
    assert hub_of("DEN", _HUBS, assign=assign) == "ORD"


def test_hub_node_ignores_assign():
    """Example test, no tolerance.

    A node that IS already a hub must return itself even when assign maps it
    elsewhere (hub-identity takes priority over assign).
    """
    # JFK is in hubs; assign trying to redirect it should have no effect
    result = hub_of("JFK", _HUBS, assign={"JFK": "ORD"})
    assert result == "JFK"


# ---------------------------------------------------------------------------
# Example test: invalid assign raises ValueError
# ---------------------------------------------------------------------------

def test_invalid_assign_raises_value_error():
    """Example test, no tolerance.

    If assign maps a node to an airport that is NOT in hubs, a ValueError
    must be raised — prevents silent mis-routing.
    """
    import pytest
    with pytest.raises(ValueError, match="LAX"):
        hub_of("BOS", _HUBS, assign={"BOS": "LAX"})


# ---------------------------------------------------------------------------
# Invariance / property tests: idempotency and determinism
# ---------------------------------------------------------------------------

def test_hub_of_is_idempotent():
    """Invariance test, no tolerance.

    hub_of(hub_of(x)) == hub_of(x) for all nodes in the tiny fixture.
    The output of hub_of is always a hub; applying it again must be a no-op.
    Tests all nodes (hub and non-hub) without external libraries.
    """
    demand = load_demand(str(_FIXTURE))
    all_nodes = {rec.origin for rec in demand} | {rec.destination for rec in demand}
    for node in all_nodes:
        first = hub_of(node, _HUBS)
        second = hub_of(first, _HUBS)
        assert first == second, (
            f"Idempotency violated: hub_of(hub_of({node!r}))={second!r} != hub_of({node!r})={first!r}"
        )


def test_hub_of_is_deterministic_across_call_order():
    """Invariance test, no tolerance.

    Calling hub_of multiple times in different orders produces the same result.
    Guards against any hidden mutable state in hubs.py.
    """
    demand = load_demand(str(_FIXTURE))
    all_nodes = sorted({rec.origin for rec in demand} | {rec.destination for rec in demand})

    # Forward order
    forward = [hub_of(n, _HUBS) for n in all_nodes]
    # Reverse order
    reverse = [hub_of(n, _HUBS) for n in reversed(all_nodes)]

    assert forward == list(reversed(reverse)), (
        "hub_of results differ across call orders — hidden mutable state suspected"
    )


def test_hub_of_output_is_always_uppercase():
    """Invariance test, no tolerance.

    hub_of must always return an uppercase string regardless of input case.
    Covers the case-normalisation contract.
    """
    mixed_cases = ["bos", "Bos", "BOS", "jfk", "JFK", "Jfk", "ord", "ORD"]
    for node in mixed_cases:
        result = hub_of(node, _HUBS)
        assert result == result.upper(), (
            f"hub_of({node!r}, ...) returned non-uppercase {result!r}"
        )
        assert result in _HUBS, (
            f"hub_of({node!r}, ...) returned {result!r} which is not a hub"
        )


def test_hub_of_case_insensitive_input():
    """Invariance test, no tolerance.

    Lower-case and mixed-case inputs must produce the same result as uppercase.
    The contract says 'case-insensitive input, uppercase output'.
    """
    for node in ("bos", "jfk", "ord", "sfO", "DeN"):
        upper_result = hub_of(node.upper(), _HUBS)
        lower_result = hub_of(node, _HUBS)
        assert lower_result == upper_result, (
            f"hub_of({node!r}) != hub_of({node.upper()!r}): "
            f"{lower_result!r} vs {upper_result!r}"
        )


# ---------------------------------------------------------------------------
# Differential test: hub_of agrees with build_routes_hub_spoke routing
# ---------------------------------------------------------------------------

def test_hub_of_matches_hub_spoke_routing():
    """Differential test, no tolerance.

    For every OD pair in tiny_demand.csv processed by build_routes_hub_spoke
    (hubs=JFK,ORD, assign=None), verify that hub_of agrees with the hub node
    implicitly chosen inside build_routes_hub_spoke:

    * If origin IS a hub: route[0] == origin == hub_of(origin), so route[1]
      is the next step — we check hub_of(origin) == origin instead.
    * If origin is NOT a hub: the second node in the route (route[1]) must be
      the origin's hub, i.e. route[1] == hub_of(origin).

    This is a differential test because it cross-checks two independent code
    paths (hubs.py and the inner hub_of closure inside networks.py) and
    requires them to agree on every OD in the fixture.
    """
    demand = load_demand(str(_FIXTURE))
    routes = build_routes_hub_spoke(demand, hubs=list(_HUBS), assign=None)

    # Index routes by (origin, destination) for easy lookup
    route_map = {(r.origin, r.destination): r.route for r in routes}

    for rec in demand:
        route = route_map[(rec.origin, rec.destination)]
        expected_hub = hub_of(rec.origin, _HUBS)

        if rec.origin in _HUBS:
            # Hub maps to itself; route starts at the hub
            assert expected_hub == rec.origin, (
                f"hub_of({rec.origin!r}) should be {rec.origin!r} (it's a hub), "
                f"got {expected_hub!r}"
            )
        else:
            # Non-hub: second node of route IS the origin's hub
            assert len(route) >= 2, (
                f"Route for {rec.origin}->{rec.destination} has only {len(route)} node(s): {route}"
            )
            route_hub = route[1]
            assert route_hub == expected_hub, (
                f"For OD ({rec.origin}->{rec.destination}): "
                f"route[1]={route_hub!r} but hub_of({rec.origin!r})={expected_hub!r}"
            )
