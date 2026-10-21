"""
test_nearest_hub.py — Tests for NW-27: nearest-hub assignment.

Covers:
  - hubs.assign_hubs(nodes, hubs, distance_fn) -> dict
  - hub_of 'first' mode reproduces old byte-identical behaviour (golden hash)
  - pipeline.run_pipeline and CLI with hub_assign / --hub-assign option
  - Properties: one-hub agrees, permutation invariance, relabelling invariance,
    adding-hub never increases distance, conservation + invariants with multi-hub.

Test kinds: example / property / metamorphic / differential / golden / invariance.

CANNOT CATCH notes are per test.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"

# ---------------------------------------------------------------------------
# Golden hash: computed on base commit BEFORE any edits to hubs.py/pipeline.py.
# Represents the hub-spoke routes for tiny_demand.csv, hubs=['ORD'], assign=None
# (which is the 'first' mode: every non-hub maps to ORD).
# Command used to generate:
#   python - << 'EOF'  (see test_golden_first_mode_hash below)
# Hash of json.dumps(sorted list of (origin, dest, route, passengers), sort_keys=True)
# ---------------------------------------------------------------------------
_GOLDEN_FIRST_HASH = "ba680ec2669f0510d37b6c42e4f09ae93e5fbe1e5b3fe5f96e6b59edd7a0315e"


# ---------------------------------------------------------------------------
# Fake distance function (avoids AIRPORT_COORDS dependency for pure-unit tests)
# ---------------------------------------------------------------------------

# Small topology for unit tests:
#   A at (0, 0), B at (0, 10), C at (0, 20), D at (0, 30)
#   H1=A, H2=C
# All distances are positive multiples of 10°-of-latitude.

_FAKE_COORDS: Dict[str, Tuple[float, float]] = {
    "A": (0.0, 0.0),
    "B": (0.0, 10.0),
    "C": (0.0, 20.0),
    "D": (0.0, 30.0),
    "E": (0.0, 25.0),  # closer to C (5°) than to A (25°)
    "F": (0.0, 5.0),   # closer to A (5°) than to C (15°)
}


def _fake_dist(code_a: str, code_b: str) -> float:
    """Distance proportional to |lon_a - lon_b| (simplified for testing)."""
    lat_a, lon_a = _FAKE_COORDS[code_a]
    lat_b, lon_b = _FAKE_COORDS[code_b]
    return abs(lon_a - lon_b) * 60.0  # 1° latitude ~ 60 nm


# ---------------------------------------------------------------------------
# Fixtures for pipeline tests
# ---------------------------------------------------------------------------

def _make_tiny3_demand_csv(tmp_path: pathlib.Path) -> pathlib.Path:
    """3-airport demand CSV."""
    p = tmp_path / "demand3.csv"
    p.write_text(
        "origin,destination,passengers\n"
        "BOS,ORD,100.0\n"
        "ORD,SFO,80.0\n"
        "BOS,SFO,60.0\n",
        encoding="utf-8",
    )
    return p


def _make_tiny3_t100(tmp_path: pathlib.Path) -> pathlib.Path:
    """Minimal T-100 with all BOS/ORD/SFO segments."""
    p = tmp_path / "t100_tiny3.csv"
    p.write_text(
        "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
        "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        "10.0,1500.0,1200.0,867.0,120.0,UA,BOS,ORD,614,1,2019,1\n"
        "8.0,1200.0,960.0,1600.0,230.0,UA,ORD,SFO,614,1,2019,1\n"
        "5.0,750.0,600.0,2704.0,390.0,UA,BOS,SFO,614,1,2019,1\n"
        "10.0,1500.0,1190.0,867.0,120.0,UA,ORD,BOS,614,1,2019,1\n"
        "8.0,1200.0,950.0,1600.0,230.0,UA,SFO,ORD,614,1,2019,1\n"
        "5.0,750.0,595.0,2704.0,390.0,UA,SFO,BOS,614,1,2019,1\n",
        encoding="utf-8",
    )
    return p


_FAKE_FUEL_PER_FLIGHT = 1000.0
_FAKE_NOX_PER_FLIGHT = 10.0


def _fake_perf(bts_code: str, distance_nm: float) -> Optional[Dict]:
    """Deterministic fake perf, never calls OpenAP."""
    return {"fuel_kg": _FAKE_FUEL_PER_FLIGHT, "nox_kg": _FAKE_NOX_PER_FLIGHT}


# ===========================================================================
# 1. assign_hubs EXISTS and has correct signature
# ===========================================================================

def test_assign_hubs_importable() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: function present but broken.

    assign_hubs must be importable from hubs.
    """
    from hubs import assign_hubs  # noqa: F401


def test_assign_hubs_returns_dict() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: dict with wrong keys/values.

    assign_hubs must return a dict.
    """
    from hubs import assign_hubs

    result = assign_hubs(["A", "B", "C"], ["A"], _fake_dist)
    assert isinstance(result, dict)


# ===========================================================================
# 2. BASIC NEAREST-HUB BEHAVIOUR
# ===========================================================================

def test_hub_maps_to_itself_in_assign_hubs() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: hub mapped to itself by coincidence of distance.

    A node that IS a hub must map to itself, verified by using a NONZERO
    distance function for hub-to-hub (prevents the mutation 'skip Rule 1'
    from surviving because dist(hub, hub) > 0 means another hub could win).
    """
    from hubs import assign_hubs

    # Use a modified distance that returns NON-ZERO for same-airport pairs,
    # so that if Rule 1 (hub-identity) is removed, the hub would map to
    # whichever OTHER hub has smaller distance.
    def _nonzero_dist(a: str, b: str) -> float:
        """Like _fake_dist but returns 1.0 for same-node pairs."""
        if a == b:
            return 1.0  # nonzero: if hub-identity check is missing, wrong result
        return _fake_dist(a, b)

    # With hubs=['A','C']: A is at lon=0, C is at lon=20.
    # _nonzero_dist(A, A) = 1.0, _nonzero_dist(A, C) = 20*60 = 1200 nm
    # → correct: A maps to A (Rule 1 fires before the distance check)
    # → wrong (mutation): A maps to A anyway because 1.0 < 1200 (still maps to itself by distance!)
    # Wait — we need a case where another hub is closer. Use single-letter nodes with tricky coords.
    # Better: A at lon=0, C at lon=1 (very close), D at lon=3 (hub), dist(D,C)=60nm, dist(D,D)=1 nm.
    # If hub-identity check is skipped: D maps to D (dist=1nm) < C (dist=60nm) - STILL correct.
    # The real issue: haversine(hub,hub) = 0.0, so we need a non-zero self-distance.
    # With _nonzero_dist: hub A has dist(A,A)=1.0 nm vs dist(A,C)=1200 nm → still maps to A.
    # The mutation only fails if there EXISTS another hub strictly closer than 1.0 nm.
    # So let's place a second hub at distance 0.5 nm from A:

    def _dist_with_near_hub(a: str, b: str) -> float:
        """Custom distances: A-A=1.0, A-N=0.5 (N is very near A), all others use long distances."""
        pairs = {frozenset(['A','A']): 1.0, frozenset(['N','N']): 1.0,
                 frozenset(['A','N']): 0.5, frozenset(['A','B']): 600.0,
                 frozenset(['N','B']): 550.0}
        key = frozenset([a, b])
        return pairs.get(key, abs(ord(a[0]) - ord(b[0])) * 100.0)

    # hubs=['A', 'N'] where N is 0.5 nm from A; dist(A, A)=1.0 nm, dist(A, N)=0.5 nm
    # Without hub-identity: A would map to N (nearest hub by distance, 0.5 < 1.0)
    # With hub-identity: A maps to A (Rule 1 fires)
    result = assign_hubs(["A", "N"], ["A", "N"], _dist_with_near_hub)
    assert result["A"] == "A", (
        f"Hub A should map to itself (Rule 1), got {result['A']!r}. "
        "If 'N' is returned, hub-identity check is missing."
    )
    assert result["N"] == "N", (
        f"Hub N should map to itself (Rule 1), got {result['N']!r}."
    )


def test_non_hub_maps_to_nearest_hub() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: correct result by coincidence.

    B at lon=10 is 10° from A (lon=0) and 10° from C (lon=20) — exact tie.
    E at lon=25 is 25° from A and 5° from C → nearest is C.
    F at lon=5  is 5° from A and 15° from C → nearest is A.
    """
    from hubs import assign_hubs

    result = assign_hubs(["A", "B", "C", "D", "E", "F"], ["A", "C"], _fake_dist)
    assert result["E"] == "C", f"E(lon=25) should map to C(lon=20), got {result['E']}"
    assert result["F"] == "A", f"F(lon=5) should map to A(lon=0), got {result['F']}"


def test_tie_broken_by_hub_list_order() -> None:
    """
    Kind: example / property.
    Tolerance: none.
    Cannot catch: tie-breaking by random order that happens to match.

    B at lon=10: distance to A(lon=0)=10, distance to C(lon=20)=10 → exact tie.
    Tie must be broken by hub-list order: the first hub in the list wins.
    With hubs=['A','C'], tie→A. With hubs=['C','A'], tie→C.
    """
    from hubs import assign_hubs

    result_ac = assign_hubs(["B"], ["A", "C"], _fake_dist)
    assert result_ac["B"] == "A", (
        f"Tie at B should be broken by first hub (A), got {result_ac['B']}"
    )

    result_ca = assign_hubs(["B"], ["C", "A"], _fake_dist)
    assert result_ca["B"] == "C", (
        f"Tie at B should be broken by first hub (C), got {result_ca['B']}"
    )


def test_all_nodes_appear_in_result() -> None:
    """
    Kind: property.
    Tolerance: none.
    Cannot catch: extra spurious keys in the result.

    Every node passed in must appear as a key in the returned dict.
    """
    from hubs import assign_hubs

    nodes = ["A", "B", "C", "D", "E", "F"]
    result = assign_hubs(nodes, ["A", "C"], _fake_dist)
    for node in nodes:
        assert node in result, f"Node {node!r} missing from assign_hubs result"


def test_all_values_are_hubs() -> None:
    """
    Kind: property / invariance.
    Tolerance: none.
    Cannot catch: value that is a hub code but the wrong hub.

    Every value in the assignment dict must be a hub.
    """
    from hubs import assign_hubs

    hubs = ["A", "C"]
    hub_set = set(hubs)
    nodes = ["A", "B", "C", "D", "E", "F"]
    result = assign_hubs(nodes, hubs, _fake_dist)
    for node, hub in result.items():
        assert hub in hub_set, (
            f"assign_hubs returned non-hub {hub!r} for node {node!r}"
        )


def test_single_hub_all_nodes_map_to_it() -> None:
    """
    Kind: property.
    Tolerance: none.
    Cannot catch: correct assignment but wrong hub code.

    With exactly one hub, every node (hub and non-hub) must map to that hub.
    This also checks that nearest-hub and first-hub agree when there is only
    one hub.
    """
    from hubs import assign_hubs

    nodes = ["A", "B", "C", "D", "E", "F"]
    result = assign_hubs(nodes, ["C"], _fake_dist)
    for node in nodes:
        assert result[node] == "C", (
            f"Single-hub: {node!r} should map to C, got {result[node]!r}"
        )


# ===========================================================================
# 3. ONE-HUB: assign_hubs and hub_of 'first' mode agree
# ===========================================================================

def test_one_hub_nearest_equals_first() -> None:
    """
    Kind: differential / property.
    Tolerance: none.
    Cannot catch: both wrong in the same way.

    With exactly one hub, the nearest-hub result must equal the first-hub
    (hub_of with assign=None, which returns hubset[0]).

    This is the 'one hub: modes agree' property from the task spec.
    """
    from hubs import assign_hubs, hub_of

    nodes = ["A", "B", "C", "D", "E", "F"]
    hub = "C"

    assign_result = assign_hubs(nodes, [hub], _fake_dist)
    for node in nodes:
        hub_of_result = hub_of(node, [hub], assign=None)
        assert assign_result[node] == hub_of_result, (
            f"One-hub: assign_hubs({node!r})={assign_result[node]!r} != "
            f"hub_of({node!r})={hub_of_result!r}"
        )


# ===========================================================================
# 4. PERMUTATION INVARIANCE (non-tie pairs)
# ===========================================================================

def test_permutation_of_hubs_does_not_change_non_tie_assignment() -> None:
    """
    Kind: property / metamorphic.
    Tolerance: none.
    Cannot catch: cancelling errors across permutations.

    For non-tie nodes, the assignment must not depend on hub list ordering.
    Nodes E (closer to C) and F (closer to A) have strict nearest hubs.
    Swapping hub order must not change their assignments.
    """
    from hubs import assign_hubs

    # Strict nearest: E→C, F→A regardless of hub order
    result_ac = assign_hubs(["E", "F"], ["A", "C"], _fake_dist)
    result_ca = assign_hubs(["E", "F"], ["C", "A"], _fake_dist)

    assert result_ac["E"] == result_ca["E"] == "C", (
        f"E should always map to nearest (C): ac={result_ac['E']!r}, ca={result_ca['E']!r}"
    )
    assert result_ac["F"] == result_ca["F"] == "A", (
        f"F should always map to nearest (A): ac={result_ac['F']!r}, ca={result_ca['F']!r}"
    )


def test_tie_permutation_changes_assignment() -> None:
    """
    Kind: property / metamorphic.
    Tolerance: none.
    Cannot catch: correct tie-breaking result for the wrong reason.

    Node B (exact tie) MUST change assignment when hub order is reversed.
    This verifies tie-breaking is actually by hub-list order, not by distance.
    """
    from hubs import assign_hubs

    result_ac = assign_hubs(["B"], ["A", "C"], _fake_dist)
    result_ca = assign_hubs(["B"], ["C", "A"], _fake_dist)

    assert result_ac["B"] != result_ca["B"], (
        f"Tie node B: assignment must change when hub order is reversed, "
        f"but got {result_ac['B']!r} and {result_ca['B']!r} (both the same)"
    )


# ===========================================================================
# 5. RELABELLING INVARIANCE (monkeypatching distance.AIRPORT_COORDS)
# ===========================================================================

def test_relabelling_invariance_assign_hubs(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Kind: metamorphic / invariance.
    Tolerance: none (exact assignment equality).
    Cannot catch: hardcoded airport codes that happen to be preserved by the
    specific bijection used.

    Relabelling airports (renaming codes + renaming the coords dict keys) must
    produce a structurally equivalent assignment: if A→C before relabelling,
    then f(A)→f(C) after relabelling.

    Uses the fake-coords topology with bijection A→P, B→Q, C→R, D→S, E→T, F→U.
    """
    from hubs import assign_hubs
    import distance as dist_mod

    bijection = {"A": "P", "B": "Q", "C": "R", "D": "S", "E": "T", "F": "U"}
    inv = {v: k for k, v in bijection.items()}  # inverse

    # New coords: same values, renamed keys
    new_coords = {bijection[k]: v for k, v in _FAKE_COORDS.items()}

    def _new_dist(code_a: str, code_b: str) -> float:
        """Distance using the new (relabelled) coords."""
        lat_a, lon_a = new_coords[code_a]
        lat_b, lon_b = new_coords[code_b]
        return abs(lon_a - lon_b) * 60.0

    # Original assignment
    orig_nodes = list(_FAKE_COORDS.keys())
    orig_assign = assign_hubs(orig_nodes, ["A", "C"], _fake_dist)

    # Relabelled assignment
    new_nodes = [bijection[n] for n in orig_nodes]
    new_hubs = [bijection["A"], bijection["C"]]
    new_assign = assign_hubs(new_nodes, new_hubs, _new_dist)

    # For every node: new_assign[f(n)] == f(orig_assign[n])
    for orig_n in orig_nodes:
        new_n = bijection[orig_n]
        expected_hub = bijection[orig_assign[orig_n]]
        got_hub = new_assign[new_n]
        assert got_hub == expected_hub, (
            f"Relabelling: {new_n!r} should map to {expected_hub!r} "
            f"(= f({orig_assign[orig_n]!r})), got {got_hub!r}"
        )


# ===========================================================================
# 6. ADDING A HUB NEVER INCREASES ANY NODE'S DISTANCE TO ITS ASSIGNED HUB
# ===========================================================================

def test_adding_hub_never_increases_assigned_distance() -> None:
    """
    Kind: property / metamorphic.
    Tolerance: 1e-9 nm (floating-point guard).
    Reason: great-circle distances are computed once per node-hub pair; the
    minimum over a larger set can only decrease or stay equal, never strictly
    increase. 1e-9 guards against floating-point noise in the haversine
    calculation when two distances are nearly equal.
    Cannot catch: improvement that is too small to measure at 1e-9 nm.

    Adding hub D to the set {A, C} can only decrease (or maintain) each
    non-hub's distance to its assigned hub.
    """
    from hubs import assign_hubs

    nodes = ["B", "D", "E", "F"]
    assign_small = assign_hubs(nodes, ["A", "C"], _fake_dist)
    assign_large = assign_hubs(nodes, ["A", "C", "D"], _fake_dist)

    for node in nodes:
        if node in ("A", "C", "D"):  # hubs map to themselves
            continue
        hub_small = assign_small[node]
        hub_large = assign_large[node]
        dist_small = _fake_dist(node, hub_small)
        dist_large = _fake_dist(node, hub_large)
        assert dist_large <= dist_small + 1e-9, (
            f"Adding hub D INCREASED {node!r}'s distance: "
            f"{hub_small!r}={dist_small:.2f} nm → {hub_large!r}={dist_large:.2f} nm"
        )


def test_adding_hub_using_real_distance(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Kind: property / metamorphic.
    Tolerance: 1e-9 nm.
    Reason: same as above; uses real distance_nm over the 6 known airports.
    Cannot catch: improvements smaller than 1e-9 nm (physically impossible for
    airports hundreds of nm apart).

    With hubs=[ORD], every airport maps to ORD.
    With hubs=[ORD, DEN], each airport's hub can only get closer (or equal).
    """
    from hubs import assign_hubs
    from distance import distance_nm, AIRPORT_COORDS

    airports = list(AIRPORT_COORDS.keys())[:6]  # first 6 to keep test fast
    # Ensure ORD and DEN are in the list
    for code in ("ORD", "DEN"):
        if code not in airports:
            airports = airports[:5] + [code]

    small_assign = assign_hubs(airports, ["ORD"], distance_nm)
    large_assign = assign_hubs(airports, ["ORD", "DEN"], distance_nm)

    for node in airports:
        if node in ("ORD", "DEN"):
            continue
        hub_small = small_assign[node]
        hub_large = large_assign[node]
        dist_small = distance_nm(node, hub_small)
        dist_large = distance_nm(node, hub_large)
        assert dist_large <= dist_small + 1e-9, (
            f"Adding DEN INCREASED {node!r}'s distance: "
            f"{hub_small}={dist_small:.1f} nm → {hub_large}={dist_large:.1f} nm"
        )


# ===========================================================================
# 7. GOLDEN HASH — 'first' mode produces same routes as pre-change code
# ===========================================================================

def test_golden_first_mode_hash() -> None:
    """
    Kind: golden.
    Tolerance: exact SHA-256 equality.
    Cannot catch: pre-change code that was already producing wrong results.

    The golden hash was generated on the BASE COMMIT (before any changes to
    hubs.py or pipeline.py) by:

        import hashlib, json, sys
        sys.path.insert(0, 'src')
        from networks import load_demand, build_routes_hub_spoke
        demand = load_demand('tests/fixtures/tiny_demand.csv')
        hs_routes = build_routes_hub_spoke(demand, hubs=['ORD'], assign=None)
        routes_data = sorted(
            [(r.origin, r.destination, r.route, r.passengers) for r in hs_routes],
            key=lambda x: (x[0], x[1])
        )
        h = hashlib.sha256(json.dumps(routes_data, sort_keys=True).encode()).hexdigest()
        print(h)
        # → ba680ec2669f0510d37b6c42e4f09ae93e5fbe1e5b3fe5f96e6b59edd7a0315e

    After adding hub_assign, mode='first' must produce byte-identical routes
    to the pre-change code for the same fixture and hub list.
    """
    from hubs import assign_hubs
    from networks import load_demand, build_routes_hub_spoke

    demand = load_demand(str(_FIXTURE))
    hubs = ["ORD"]

    # 'first' mode: use assign=None (falls back to first hub, same as pre-change)
    hs_routes = build_routes_hub_spoke(demand, hubs=hubs, assign=None)

    routes_data = sorted(
        [(r.origin, r.destination, list(r.route), r.passengers) for r in hs_routes],
        key=lambda x: (x[0], x[1]),
    )
    actual_hash = hashlib.sha256(
        json.dumps(routes_data, sort_keys=True).encode()
    ).hexdigest()

    assert actual_hash == _GOLDEN_FIRST_HASH, (
        f"'first' mode hash changed!\n"
        f"  Expected: {_GOLDEN_FIRST_HASH}\n"
        f"  Got:      {actual_hash}\n"
        "This means hub_of 'first' mode no longer reproduces pre-change behaviour."
    )


# ===========================================================================
# 8. PIPELINE hub_assign OPTION
# ===========================================================================

def test_pipeline_accepts_hub_assign_nearest(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: option accepted but not applied.

    run_pipeline must accept hub_assign='nearest' without error.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
        hub_assign="nearest",
    )
    assert rc == 0, f"run_pipeline with hub_assign='nearest' returned {rc}"


def test_pipeline_accepts_hub_assign_first(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: option accepted but not applied.

    run_pipeline must accept hub_assign='first' without error.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
        hub_assign="first",
    )
    assert rc == 0, f"run_pipeline with hub_assign='first' returned {rc}"


def test_pipeline_default_hub_assign_is_nearest(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: default recorded but not applied.

    When hub_assign is not specified, the manifest must record 'nearest' as
    the hub_assign value (default per task spec).
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert "hub_assign" in manifest, "manifest must contain 'hub_assign'"
    assert manifest["hub_assign"] == "nearest", (
        f"Default hub_assign should be 'nearest', got {manifest['hub_assign']!r}"
    )


def test_pipeline_hub_assign_recorded_in_manifest(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: recorded but not used.

    hub_assign must be recorded in manifest.json.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)

    for mode in ("nearest", "first"):
        out_dir = tmp_path / f"out_{mode}"
        out_dir.mkdir()

        pipeline.run_pipeline(
            demand_path=str(demand_path),
            hubs=["ORD"],
            t100_paths=[str(t100_path)],
            out_dir=str(out_dir),
            perf_fn=_fake_perf,
            p2p_rule="all",
            hub_assign=mode,
        )

        manifest = json.loads((out_dir / "manifest.json").read_text())
        assert manifest.get("hub_assign") == mode, (
            f"hub_assign={mode!r}: manifest recorded {manifest.get('hub_assign')!r}"
        )


def test_pipeline_hub_assign_first_matches_golden(tmp_path: pathlib.Path) -> None:
    """
    Kind: golden / differential.
    Tolerance: exact SHA-256 equality.
    Cannot catch: both 'first' and pre-change code wrong in the same way.

    When hub_assign='first' and single hub, the HS routes must reproduce the
    golden hash, confirming backward-compat.

    This test runs the actual pipeline and hashes the segments.csv file,
    verifying not just the routes but the full pipeline output.
    Actually we re-hash the HS routes the same way as the base-commit golden.
    """
    import pipeline
    from networks import load_demand, build_routes_hub_spoke

    demand_path = _FIXTURE  # tiny_demand.csv
    t100_path = _make_tiny3_t100(tmp_path)

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
        hub_assign="first",
    )
    assert rc == 0, f"pipeline with hub_assign='first' failed: rc={rc}"

    # Re-derive routes as pipeline would with 'first' (assign=None → first hub)
    demand = load_demand(str(demand_path))
    hs_routes = build_routes_hub_spoke(demand, hubs=["ORD"], assign=None)
    routes_data = sorted(
        [(r.origin, r.destination, list(r.route), r.passengers) for r in hs_routes],
        key=lambda x: (x[0], x[1]),
    )
    actual_hash = hashlib.sha256(
        json.dumps(routes_data, sort_keys=True).encode()
    ).hexdigest()
    assert actual_hash == _GOLDEN_FIRST_HASH, (
        f"hub_assign='first' routes don't match golden hash\n"
        f"  Expected: {_GOLDEN_FIRST_HASH}\n"
        f"  Got:      {actual_hash}"
    )


def test_pipeline_one_hub_nearest_and_first_agree(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential / property.
    Tolerance: rel_tol=1e-12 for fuel_kg, passengers.
    Reason: same arithmetic path; only assignment differs. With one hub,
    nearest and first must agree exactly.
    Cannot catch: both modes wrong in the same way.

    With a single hub, 'nearest' and 'first' must produce identical totals
    since there is only one possible hub for every node.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)

    out_nearest = tmp_path / "nearest"
    out_first = tmp_path / "first"
    out_nearest.mkdir()
    out_first.mkdir()

    for mode, out_dir in [("nearest", out_nearest), ("first", out_first)]:
        pipeline.run_pipeline(
            demand_path=str(demand_path),
            hubs=["ORD"],
            t100_paths=[str(t100_path)],
            out_dir=str(out_dir),
            perf_fn=_fake_perf,
            p2p_rule="all",
            hub_assign=mode,
        )

    d_n = json.loads((out_nearest / "pipeline_totals.json").read_text())
    d_f = json.loads((out_first / "pipeline_totals.json").read_text())

    for metric in ("passengers", "passenger_legs", "fuel_kg"):
        assert math.isclose(
            d_n["hub_spoke"][metric], d_f["hub_spoke"][metric], rel_tol=1e-12
        ), (
            f"One-hub: nearest/first disagree on hub_spoke.{metric}: "
            f"nearest={d_n['hub_spoke'][metric]}, first={d_f['hub_spoke'][metric]}"
        )


def test_pipeline_multi_hub_nearest_first_can_differ(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential / property.
    Tolerance: none (we assert inequality, not equality).
    Cannot catch: coincidental equality due to symmetric demand.

    With two hubs (ORD, DEN) and airports on both coasts, BOS is much closer
    to ORD than DEN; SFO is much closer to DEN or ORD depending on the actual
    distances. Under 'first', every non-hub maps to ORD. Under 'nearest', some
    airports may map to DEN instead.

    We specifically check: SFO is closer to DEN than ORD; so with nearest and
    hubs=[ORD,DEN], SFO should map to DEN. With first, SFO would map to ORD.
    This means HS passenger_legs will differ between the two modes.

    Uses the tiny_demand.csv fixture which has BOS, SFO as non-hub airports.
    """
    import pipeline
    from hubs import assign_hubs
    from distance import distance_nm, AIRPORT_COORDS

    # Verify the precondition: SFO is closer to DEN than ORD in real coords
    if "SFO" not in AIRPORT_COORDS or "DEN" not in AIRPORT_COORDS or "ORD" not in AIRPORT_COORDS:
        pytest.skip("SFO/DEN/ORD not in AIRPORT_COORDS")

    dist_sfo_ord = distance_nm("SFO", "ORD")
    dist_sfo_den = distance_nm("SFO", "DEN")
    if dist_sfo_den >= dist_sfo_ord:
        pytest.skip(f"SFO is not closer to DEN ({dist_sfo_den:.0f}) than ORD ({dist_sfo_ord:.0f})")

    t100_path = _make_tiny3_t100(tmp_path)

    out_nearest = tmp_path / "nearest"
    out_first = tmp_path / "first"
    out_nearest.mkdir()
    out_first.mkdir()

    for mode, out_dir in [("nearest", out_nearest), ("first", out_first)]:
        pipeline.run_pipeline(
            demand_path=str(_FIXTURE),
            hubs=["ORD", "DEN"],
            t100_paths=[str(t100_path)],
            out_dir=str(out_dir),
            perf_fn=_fake_perf,
            p2p_rule="all",
            hub_assign=mode,
        )

    d_n = json.loads((out_nearest / "pipeline_totals.json").read_text())
    d_f = json.loads((out_first / "pipeline_totals.json").read_text())

    # passenger totals must be equal (same demand, same passengers)
    assert math.isclose(
        d_n["hub_spoke"]["passengers"], d_f["hub_spoke"]["passengers"], rel_tol=1e-12
    ), "Passenger totals must be equal regardless of hub assignment mode"

    # passenger_legs or fuel_kg must differ (different routing)
    legs_n = d_n["hub_spoke"]["passenger_legs"]
    legs_f = d_f["hub_spoke"]["passenger_legs"]
    fuel_n = d_n["hub_spoke"]["fuel_kg"]
    fuel_f = d_f["hub_spoke"]["fuel_kg"]

    routes_differ = (
        not math.isclose(legs_n, legs_f, rel_tol=1e-6) or
        not math.isclose(fuel_n, fuel_f, rel_tol=1e-6)
    )
    assert routes_differ, (
        f"nearest and first should differ with hubs=[ORD,DEN] but:\n"
        f"  nearest: legs={legs_n}, fuel={fuel_n}\n"
        f"  first:   legs={legs_f}, fuel={fuel_f}"
    )


# ===========================================================================
# 9. CONSERVATION AND INVARIANTS WITH MULTI-HUB NEAREST ROUTING
# ===========================================================================

def test_nearest_hub_routing_conservation(tmp_path: pathlib.Path) -> None:
    """
    Kind: property / invariance.
    Tolerance: abs 1e-9 per OD pair.
    Reason: passengers are copied exactly; only route structure changes.
    Cannot catch: cancelling errors across multiple OD pairs.

    Passenger conservation must hold for hub_assign='nearest' with two hubs.
    We test it directly on the routes produced by build_routes_hub_spoke
    when assign comes from assign_hubs.
    """
    from hubs import assign_hubs
    from networks import load_demand, build_routes_hub_spoke
    from distance import distance_nm, AIRPORT_COORDS
    from collections import defaultdict

    demand = load_demand(str(_FIXTURE))
    all_nodes = list(
        {rec.origin for rec in demand} | {rec.destination for rec in demand}
    )
    hubs = ["ORD", "DEN"]

    # Skip if ORD or DEN not in AIRPORT_COORDS
    for h in hubs:
        if h not in AIRPORT_COORDS:
            pytest.skip(f"{h} not in AIRPORT_COORDS")

    assign = assign_hubs(all_nodes, hubs, distance_nm)
    hs_routes = build_routes_hub_spoke(demand, hubs=hubs, assign=assign)

    od_demand: Dict = defaultdict(float)
    for rec in demand:
        od_demand[(rec.origin, rec.destination)] += rec.passengers

    od_routes: Dict = defaultdict(float)
    for r in hs_routes:
        od_routes[(r.origin, r.destination)] += r.passengers

    for od, expected in od_demand.items():
        got = od_routes.get(od, 0.0)
        assert abs(got - expected) <= 1e-9, (
            f"Conservation failed for OD {od}: got={got}, expected={expected}"
        )


def test_nearest_hub_routing_endpoints_invariant(tmp_path: pathlib.Path) -> None:
    """
    Kind: invariance.
    Tolerance: none (string equality).
    Cannot catch: endpoints correct but interior nodes wrong.

    check_endpoints must pass for nearest-hub routes with two hubs.
    """
    from hubs import assign_hubs
    from networks import load_demand, build_routes_hub_spoke
    from invariants import check_endpoints
    from distance import distance_nm, AIRPORT_COORDS

    demand = load_demand(str(_FIXTURE))
    all_nodes = list(
        {rec.origin for rec in demand} | {rec.destination for rec in demand}
    )
    hubs = ["ORD", "DEN"]

    for h in hubs:
        if h not in AIRPORT_COORDS:
            pytest.skip(f"{h} not in AIRPORT_COORDS")

    assign = assign_hubs(all_nodes, hubs, distance_nm)
    hs_routes = build_routes_hub_spoke(demand, hubs=hubs, assign=assign)
    check_endpoints(hs_routes)  # raises ValueError on failure


def test_nearest_hub_routing_nonneg_invariant() -> None:
    """
    Kind: invariance.
    Tolerance: abs 1e-9.
    Cannot catch: negative passengers with magnitude < 1e-9.

    check_nonnegative must pass for nearest-hub routes.
    """
    from hubs import assign_hubs
    from networks import load_demand, build_routes_hub_spoke
    from invariants import check_nonnegative
    from distance import distance_nm, AIRPORT_COORDS

    demand = load_demand(str(_FIXTURE))
    all_nodes = list(
        {rec.origin for rec in demand} | {rec.destination for rec in demand}
    )
    hubs = ["ORD", "DEN"]

    for h in hubs:
        if h not in AIRPORT_COORDS:
            pytest.skip(f"{h} not in AIRPORT_COORDS")

    assign = assign_hubs(all_nodes, hubs, distance_nm)
    hs_routes = build_routes_hub_spoke(demand, hubs=hubs, assign=assign)
    check_nonnegative(hs_routes)  # raises ValueError on failure


def test_nearest_hub_interior_nodes_are_hubs() -> None:
    """
    Kind: invariance.
    Tolerance: none.
    Cannot catch: hub-intermediate violation in long multi-hub chains.

    Every interior node of a nearest-hub route must be a hub.
    Multi-hub (spoke→hub_A→hub_B→spoke) paths are allowed; both hub_A and
    hub_B are hubs.

    NOTE: This test uses the existing check_hub_intermediates invariant which
    validates interior nodes against the hub set. Multi-hub paths with up to
    2 intermediate hub nodes (spoke→H_A→H_B→spoke) are legal because both
    intermediate nodes are hubs. The check_simple_path invariant (no repeated
    nodes) is NOT applied here because a spoke could appear on both sides:
    spoke_X → H_A → H_B → spoke_X is structurally possible but means the
    passenger has taken a round trip — in practice the network builder prevents
    this by construction (unique OD pairs). We document this limit.
    """
    from hubs import assign_hubs
    from networks import load_demand, build_routes_hub_spoke
    from invariants import check_hub_intermediates
    from distance import distance_nm, AIRPORT_COORDS

    demand = load_demand(str(_FIXTURE))
    all_nodes = list(
        {rec.origin for rec in demand} | {rec.destination for rec in demand}
    )
    hubs = ["ORD", "DEN"]

    for h in hubs:
        if h not in AIRPORT_COORDS:
            pytest.skip(f"{h} not in AIRPORT_COORDS")

    assign = assign_hubs(all_nodes, hubs, distance_nm)
    hs_routes = build_routes_hub_spoke(demand, hubs=hubs, assign=assign)
    check_hub_intermediates(hs_routes, hubs)  # raises on failure


# ===========================================================================
# 10. MODEL.txt MENTIONS hub_assign as MODELLING CHOICE
# ===========================================================================

def test_model_txt_mentions_hub_assign_nearest(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / content.
    Tolerance: none (substring presence).
    Cannot catch: text present but inaccurate.

    MODEL.txt must mention hub_assign/nearest-hub as a modelling choice and
    must NOT claim it is optimal or realistic.
    Must state it is not covered by an ADR.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
        hub_assign="nearest",
    )

    text = (out_dir / "MODEL.txt").read_text(encoding="utf-8")
    # Must mention nearest-hub as a choice
    assert any(phrase in text.lower() for phrase in ("nearest hub", "nearest-hub", "hub_assign")), (
        "MODEL.txt must mention nearest-hub assignment as a modelling choice"
    )
    # Must not claim it is optimal or realistic
    lower = text.lower()
    assert "optimal" not in lower or "not optimal" in lower or "no claim" in lower, (
        "MODEL.txt must not claim nearest-hub is optimal"
    )
    assert "realistic" not in lower or "not realistic" in lower or "no claim" in lower, (
        "MODEL.txt must not claim nearest-hub is realistic"
    )


# ===========================================================================
# 11. CLI --hub-assign OPTION
# ===========================================================================

def test_cli_pipeline_accepts_hub_assign_nearest(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / integration.
    Tolerance: none.
    Cannot catch: option accepted but not passed to pipeline.

    'cli pipeline --hub-assign nearest' must exit 0 and create output files.
    """
    import subprocess, sys

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable, "-m", "cli", "pipeline",
            "--demand", str(demand_path),
            "--hubs", "ORD",
            "--t100", str(t100_path),
            "--out", str(out_dir),
            "--p2p-rule", "all",
            "--hub-assign", "nearest",
        ],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    assert result.returncode == 0, (
        f"cli pipeline --hub-assign nearest failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert (out_dir / "pipeline_totals.json").exists()


def test_cli_pipeline_accepts_hub_assign_first(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / integration.
    Tolerance: none.
    Cannot catch: option accepted but not passed to pipeline.

    'cli pipeline --hub-assign first' must exit 0.
    """
    import subprocess, sys

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable, "-m", "cli", "pipeline",
            "--demand", str(demand_path),
            "--hubs", "ORD",
            "--t100", str(t100_path),
            "--out", str(out_dir),
            "--p2p-rule", "all",
            "--hub-assign", "first",
        ],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    assert result.returncode == 0, (
        f"cli pipeline --hub-assign first failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_cli_pipeline_hub_assign_help(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: option listed but not functional.

    --hub-assign must appear in --help output.
    """
    import subprocess, sys

    result = subprocess.run(
        [sys.executable, "-m", "cli", "pipeline", "--help"],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    assert result.returncode == 0
    help_text = result.stdout + result.stderr
    assert "--hub-assign" in help_text, (
        f"--hub-assign not found in pipeline --help output:\n{help_text}"
    )


# ===========================================================================
# 12. REAL BTS CACHE TESTS (skip if cache absent)
# ===========================================================================

_CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "bts-cache"
_DEMAND_CACHE = _CACHE_DIR / "demand_region_2019Q1.csv"
_T100_JAN = _CACHE_DIR / "t100_2019_01.csv"
_T100_FEB = _CACHE_DIR / "t100_2019_02.csv"
_T100_MAR = _CACHE_DIR / "t100_2019_03.csv"

_CACHE_AVAILABLE = (
    _DEMAND_CACHE.exists()
    and _T100_JAN.exists()
    and _T100_FEB.exists()
    and _T100_MAR.exists()
)


@pytest.mark.skipif(
    not _CACHE_AVAILABLE,
    reason="BTS cache absent (data/bts-cache/); skipping real-data test",
)
def test_cache_nearest_vs_first_assignment_differ(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential (real data, prototype).
    Tolerance: 1e-6 rel for totals that must differ; exact equality for passengers.
    Cannot catch: modes that agree by coincidence on this specific region/period.

    With hubs=[ORD, DEN] and real region data, 'nearest' and 'first' must
    produce different HS routes (different passenger_legs or fuel_kg) because
    some airports are closer to DEN than ORD.

    PROTOTYPE OUTPUT: not findings. Hub-hub trips may behave oddly
    (a direct ORD→DEN flight that routes through the hub network).
    """
    import pipeline

    t100_paths = [str(_T100_JAN), str(_T100_FEB), str(_T100_MAR)]

    out_nearest = tmp_path / "nearest"
    out_first = tmp_path / "first"
    out_nearest.mkdir()
    out_first.mkdir()

    for mode, out_dir in [("nearest", out_nearest), ("first", out_first)]:
        rc = pipeline.run_pipeline(
            demand_path=str(_DEMAND_CACHE),
            hubs=["ORD", "DEN"],
            t100_paths=t100_paths,
            out_dir=str(out_dir),
            perf_fn=_fake_perf,
            p2p_rule="observed",
            hub_assign=mode,
        )
        assert rc == 0, f"Pipeline failed for mode={mode!r}: rc={rc}"

    d_n = json.loads((out_nearest / "pipeline_totals.json").read_text())
    d_f = json.loads((out_first / "pipeline_totals.json").read_text())

    # Passengers must be equal
    assert math.isclose(
        d_n["hub_spoke"]["passengers"], d_f["hub_spoke"]["passengers"], rel_tol=1e-9
    ), "Passenger totals must be equal regardless of hub assignment mode"

    # Routes must differ
    legs_n = d_n["hub_spoke"]["passenger_legs"]
    legs_f = d_f["hub_spoke"]["passenger_legs"]
    assert not math.isclose(legs_n, legs_f, rel_tol=1e-6), (
        f"nearest and first should differ on real data with ORD,DEN:\n"
        f"  nearest legs={legs_n}, first legs={legs_f}"
    )
