"""
Tests for project/src/invariants.py: check_endpoints and check_nonnegative.

Import scaffolding mirrors test_passenger_conservation.py so that pytest can
run regardless of how the package is installed.
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
from invariants import check_endpoints, check_nonnegative  # noqa: E402

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _p2p_routes():
    demand = load_demand(str(_FIXTURE))
    return build_routes_point_to_point(demand)


def _has_routes():
    demand = load_demand(str(_FIXTURE))
    return build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])


# ---------------------------------------------------------------------------
# (a) Real builder outputs must pass both invariants
# ---------------------------------------------------------------------------


def test_endpoints_p2p_passes():
    """
    Invariance test — no tolerance (exact string equality on IATA codes).

    Reason: route[0] and route[-1] are string comparisons; there is no
    numeric rounding involved.  A failure here means the builder set a
    wrong origin/destination field or built a path that starts/ends at
    the wrong airport, which is an unambiguous bug.

    check_endpoints should return None (not raise) for all RouteFlows
    produced by build_routes_point_to_point.
    """
    routes = _p2p_routes()
    # Must not raise — return value is None
    assert check_endpoints(routes) is None


def test_endpoints_hub_spoke_passes():
    """
    Invariance test — no tolerance (exact string equality on IATA codes).

    Reason: same as test_endpoints_p2p_passes.  Hub-and-spoke paths have
    interior hub nodes, but route[0] and route[-1] must still equal the
    declared origin and destination fields.

    check_endpoints should return None for build_routes_hub_spoke output.
    """
    routes = _has_routes()
    assert check_endpoints(routes) is None


def test_nonnegative_p2p_passes():
    """
    Invariance test — tolerance 1e-9 (matches default tol in check_nonnegative).

    Reason: DB1B passengers are weighted and fractional; tiny negative values
    can arise from floating-point arithmetic in aggregation pipelines.  The
    1e-9 guard rejects deliberate negatives while accepting pure float noise.

    check_nonnegative should return None for build_routes_point_to_point output.
    """
    routes = _p2p_routes()
    assert check_nonnegative(routes) is None


def test_nonnegative_hub_spoke_passes():
    """
    Invariance test — tolerance 1e-9 (matches default tol in check_nonnegative).

    Reason: same as test_nonnegative_p2p_passes.

    check_nonnegative should return None for build_routes_hub_spoke output.
    """
    routes = _has_routes()
    assert check_nonnegative(routes) is None


# ---------------------------------------------------------------------------
# (b) Validators REJECT hand-built bad records
# ---------------------------------------------------------------------------


def test_check_endpoints_rejects_wrong_start():
    """
    Example test — no tolerance.

    A RouteFlow whose route does not start at origin is an endpoint mismatch.
    check_endpoints must raise ValueError and the message must identify the
    offending record (origin BOS, route starting at JFK).
    """
    bad = RouteFlow(
        origin="BOS",
        destination="SFO",
        route=("JFK", "SFO"),   # starts at JFK, not BOS
        passengers=50.0,
    )
    with pytest.raises(ValueError, match="BOS"):
        check_endpoints([bad])


def test_check_endpoints_rejects_wrong_end():
    """
    Example test — no tolerance.

    A RouteFlow whose route does not end at destination is an endpoint mismatch.
    check_endpoints must raise ValueError and the message must identify the
    offending record (destination SFO, route ending at ORD).
    """
    bad = RouteFlow(
        origin="BOS",
        destination="SFO",
        route=("BOS", "JFK", "ORD"),   # ends at ORD, not SFO
        passengers=50.0,
    )
    with pytest.raises(ValueError, match="SFO"):
        check_endpoints([bad])


def test_check_endpoints_accepts_single_node_same_origin_dest():
    """
    Example test — no tolerance.

    An OD where origin == destination is a degenerate case; a single-node
    route is still valid if route[0] == origin == destination == route[-1].
    """
    ok = RouteFlow(
        origin="BOS",
        destination="BOS",
        route=("BOS",),
        passengers=0.0,
    )
    assert check_endpoints([ok]) is None


def test_check_nonnegative_rejects_explicit_negative():
    """
    Example test — tolerance 1e-9.

    A RouteFlow with passengers = -80.0 must be rejected even if another
    RouteFlow has passengers = +200.0 so the per-OD sum looks fine (+120).
    check_nonnegative inspects each record individually, not their sum.
    The message must identify the offending negative value.
    """
    ok_record = RouteFlow(
        origin="BOS",
        destination="SFO",
        route=("BOS", "JFK", "SFO"),
        passengers=200.0,
    )
    bad_record = RouteFlow(
        origin="BOS",
        destination="SFO",
        route=("BOS", "ORD", "SFO"),
        passengers=-80.0,
    )
    with pytest.raises(ValueError, match=r"-80"):
        check_nonnegative([ok_record, bad_record])


def test_check_nonnegative_accepts_float_noise():
    """
    Example test — tolerance 1e-9.

    Reason: float arithmetic in DB1B aggregation can yield tiny negatives
    like -1e-12.  These are within the default tol=1e-9 and must NOT raise.
    This guards against a too-strict implementation that uses >= 0 exactly.
    """
    noise = RouteFlow(
        origin="BOS",
        destination="JFK",
        route=("BOS", "JFK"),
        passengers=-1e-12,   # float noise, within tolerance
    )
    # Must not raise
    assert check_nonnegative([noise]) is None


def test_check_nonnegative_rejects_value_just_outside_tolerance():
    """
    Example test — tolerance 1e-9.

    A value of -1e-9 is exactly on the boundary: passengers >= -tol means
    -1e-9 >= -1e-9 is True, so it should be accepted.
    A value of -(1e-9 + 1e-12) is just outside and must be rejected.

    This test pins the boundary behaviour precisely.
    """
    boundary_ok = RouteFlow(
        origin="BOS",
        destination="JFK",
        route=("BOS", "JFK"),
        passengers=-1e-9,   # exactly -tol: accepted
    )
    assert check_nonnegative([boundary_ok]) is None

    just_outside = RouteFlow(
        origin="BOS",
        destination="JFK",
        route=("BOS", "JFK"),
        passengers=-(1e-9 + 1e-12),   # just below -tol: rejected
    )
    with pytest.raises(ValueError):
        check_nonnegative([just_outside])


def test_check_endpoints_rejects_empty_route():
    """
    Example test — no tolerance.

    A RouteFlow with an empty route tuple cannot satisfy route[0]==origin
    or route[-1]==destination.  check_endpoints must raise ValueError with
    a message that identifies the record.
    """
    bad = RouteFlow(
        origin="BOS",
        destination="JFK",
        route=(),
        passengers=10.0,
    )
    with pytest.raises(ValueError, match="BOS"):
        check_endpoints([bad])


def test_check_endpoints_accepts_empty_list():
    """
    Example test — no tolerance.

    Vacuously valid: an empty route list has no offending records.
    """
    assert check_endpoints([]) is None


def test_check_nonnegative_accepts_empty_list():
    """
    Example test — no tolerance.

    Vacuously valid: an empty route list has no offending records.
    """
    assert check_nonnegative([]) is None


def test_check_nonnegative_rejects_first_bad_in_mixed_list():
    """
    Example test — tolerance 1e-9.

    When a list contains multiple bad records, ValueError is raised on the
    FIRST offending one (left-to-right scan).  This guards against an
    implementation that silently skips bad records.
    """
    good = RouteFlow("BOS", "JFK", ("BOS", "JFK"), 50.0)
    bad1 = RouteFlow("BOS", "ORD", ("BOS", "ORD"), -5.0)
    bad2 = RouteFlow("BOS", "SFO", ("BOS", "SFO"), -3.0)
    with pytest.raises(ValueError, match=r"-5"):
        check_nonnegative([good, bad1, bad2])
