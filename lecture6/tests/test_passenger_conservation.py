import math
import pathlib
import sys
from collections import defaultdict

# Make lecture6/src importable when pytest runs from repo root
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from networks import load_demand, build_routes_point_to_point, build_routes_hub_spoke  # noqa: E402


def _approximately_equal(a: float, b: float, rel: float, abs_: float) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


def test_passenger_conservation_across_structures():
    """
    Passenger conservation across network structures (point-to-point vs hub-and-spoke).

    We assert that the total OD passenger demand equals the total passengers assigned to
    complete routes (path-level) for both structures, within a loose total-sum tolerance of
    ±1 passenger (abs_tol=1.0) to avoid flakiness with floating-point and weighted counts.

    Reason for tolerance: DB1B passengers are weighted and fractional; floating-point
    accumulation and split/re-aggregation can differ at sub-passenger scales. A ±1-passenger
    guard keeps the test robust across benign reorderings while we add tighter per-OD checks
    (abs=1e-9, rel=1e-12) to catch subtle redistribution bugs.

    Note: This is path-flow conservation, not leg-flow equality. In a hubbed network,
    passenger-legs exceed passenger count due to connections; that is expected and not
    tested here.
    """
    fixture = _THIS_DIR / "fixtures" / "tiny_demand.csv"
    demand = load_demand(str(fixture))

    total_demand = sum(rec.passengers for rec in demand)
    # Dynamic tolerance based on total scale
    abs_tol = max(1.0, 1e-9 * total_demand)
    rel_tol = 0.0

    # Point-to-point mapping: each OD -> [O, D]
    p2p_routes = build_routes_point_to_point(demand)
    total_p2p = sum(r.passengers for r in p2p_routes)
    assert _approximately_equal(total_p2p, total_demand, rel_tol, abs_tol), (
        f"P2P passengers not conserved: routes={total_p2p} vs demand={total_demand}"
    )

    # Per-OD re-aggregation sanity for point-to-point — should match exactly up to tight tol
    od_to_demand = defaultdict(float)
    for rec in demand:
        od_to_demand[(rec.origin, rec.destination)] += rec.passengers

    od_to_routes_p2p = defaultdict(float)
    for r in p2p_routes:
        od_to_routes_p2p[(r.origin, r.destination)] += r.passengers

    for od, expected in od_to_demand.items():
        got = od_to_routes_p2p.get(od, 0.0)
        assert _approximately_equal(got, expected, 1e-12, 1e-9), (
            f"P2P OD mismatch for {od}: routes={got} vs demand={expected}"
        )

    # Hub-and-spoke mapping with fixed hubs JFK and ORD (placeholder assignment in impl.)
    has_routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"], assign=None)
    total_has = sum(r.passengers for r in has_routes)
    assert _approximately_equal(total_has, total_demand, rel_tol, abs_tol), (
        f"Hub-and-spoke passengers not conserved: routes={total_has} vs demand={total_demand}"
    )

    # Optional: per-OD re-aggregation sanity — split routes (if any) must sum to OD demand
    od_to_demand = defaultdict(float)
    for rec in demand:
        od_to_demand[(rec.origin, rec.destination)] += rec.passengers

    od_to_routes = defaultdict(float)
    for r in has_routes:
        od_to_routes[(r.origin, r.destination)] += r.passengers

    for od, expected in od_to_demand.items():
        got = od_to_routes.get(od, 0.0)
        assert _approximately_equal(got, expected, rel_tol, abs_tol), (
            f"OD split mismatch for {od}: routes={got} vs demand={expected}"
        )
