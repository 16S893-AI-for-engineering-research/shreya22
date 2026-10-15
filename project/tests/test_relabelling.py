"""
test_relabelling.py — Relabelling invariance tests for network_totals and figures.

DESIGN
------
The key insight: distance.distance_nm reads AIRPORT_COORDS from the *distance
module's namespace* at call time.  totals.py imports the distance module as
``import distance as _distance`` and calls ``_distance.distance_nm(...)``, which
reads ``distance.AIRPORT_COORDS`` at the time of the call.  So patching
``distance.AIRPORT_COORDS`` with monkeypatch.setattr is sufficient — totals.py
reaches through the same module object.

A valid relabelling is a bijection f: old_code -> new_code that must be applied
simultaneously to:
  1. distance.AIRPORT_COORDS  (rename keys, keep coordinates)
  2. The demand origins/destinations
  3. The hub list (same ORDER, since the first hub is the default)
  4. Any explicit assign mapping (both keys and values)

This way every distance_nm(new_a, new_b) computes the same haversine as
distance_nm(old_a, old_b).  All network_totals values must therefore be
numerically identical.

IMPORTANT: the "original" totals for comparison are computed at module-import
time (before any monkeypatch), stored as module-level constants, and reused by
every parametrised test.  This avoids computing them inside the monkeypatched
context, which would fail because the original codes are absent from the
relabelled AIRPORT_COORDS dict.

NEGATIVE CONTROL: relabel demand but NOT the coords dict → KeyError or wrong
distances → totals differ.  Both outcomes are asserted.

FIGURE INVARIANCE: bar_compare receives a dict whose keys are METRIC NAMES
(not airport codes) and whose values are (p2p_val, hs_val) pairs derived from
network_totals.  Because the values are identical pre/post relabelling, the
dict passed to bar_compare is identical, and the SVG output is byte-identical.

METAMORPHIC ROUTE CHECK: for P2P and H&S, every route in the relabelled result
equals tuple(f(x) for x in route_original).

BIJECTIONS USED
---------------
B1: X01..X06  (completely alien codes, no collision possible)
B2: reversed-alphabet: A→Z, B→Y, C→X...  applied to code chars
    (we map each whole code, not each char, via a fixed mapping)
B3: swap-pairs: BOS<->JFK, ORD<->SFO, DEN<->PHX  (nasty: hardcoded 'JFK'
    would silently point at a different airport's coordinates)
B4: seeded random permutation via random.Random(12345)
B5: six 1-char codes A..F — catches any mutation that scales by len(code)
    because all codes map to length 1 instead of 3
B6: MIXED lengths 1,2,3,4,5,9 — one unique length per airport:
      BOS→Q (1), JFK→ZA (2), ORD→WXY (3), SFO→MNOP (4),
      DEN→RSTUV (5), PHX→LONGCODE1 (9)
    No two airports share a code length.  Any formula involving len(code)
    that worked for all-3-char bijections will break here, because the
    per-leg (len(a)+len(b)) factor is now different for every edge.
B7: hub-initial-collision: BOS→WQ (non-hub, len=2, starts 'W' = ORD-hub
    initial), JFK→ZA (hub#1), ORD→WW (hub#2).  Under mutation (ii)
    in hubs.hub_of (len≠3 + first-char match), BOS would be mis-routed to
    the second hub instead of the first, changing H&S totals.

TOLERANCES
----------
rel_tol=1e-12 throughout, with the following justification:
  - All values are computed from the same floating-point arithmetic.
  - The only source of discrepancy is summation order if route or dict
    iteration order changes when codes change.  With 8 OD pairs and values
    in the 10^3–10^6 range, ULP-level differences are well below 1e-12.
  - 1e-12 is tighter than 1e-9 (used in other tests) to make this test
    sensitive to any code-order-dependent summation.

CANNOT CATCH
------------
* A production bug that produces the wrong total for *every* permutation in
  the same wrong way (impossible if the bijections are sufficiently varied).
* Non-determinism within a single Python process (unrelated to relabelling).
"""

from __future__ import annotations

import math
import pathlib
import random
import sys
from typing import Dict, List, Tuple

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

import distance as _distance_mod           # noqa: E402
from networks import (                      # noqa: E402
    ODDemand,
    RouteFlow,
    load_demand,
    build_routes_point_to_point,
    build_routes_hub_spoke,
)
from totals import network_totals           # noqa: E402
from figures import bar_compare             # noqa: E402

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"
_PROJECT_HUBS = ["JFK", "ORD"]
_THREE_HUBS = ["JFK", "ORD", "SFO"]

# ---------------------------------------------------------------------------
# Original airport codes
# ---------------------------------------------------------------------------
_ORIGINAL_CODES = ["BOS", "JFK", "ORD", "SFO", "DEN", "PHX"]

# Explicit assign for the original codes: non-hub airports mapped to specific hubs.
_ORIG_ASSIGN: Dict[str, str] = {
    "BOS": "JFK",
    "SFO": "ORD",
    "DEN": "JFK",
    "PHX": "ORD",
}

# Metrics for bar_compare: fixed set of metric names (NOT airport codes).
_METRIC_KEYS = [
    "passengers", "passenger_legs", "flights", "aircraft_nm",
    "fuel_kg", "NOx_kg", "CO_kg", "HC_kg",
]

# ---------------------------------------------------------------------------
# Pre-compute original totals at module import time (before any monkeypatching).
# This is safe because distance.AIRPORT_COORDS still has the real codes here.
# Storing them as module-level constants avoids computing them inside a
# monkeypatched context (where old codes would be absent from new coords).
# ---------------------------------------------------------------------------
_DEMAND_ORIG = load_demand(str(_FIXTURE))

_T_P2P_ORIG: Dict[str, float] = network_totals(
    build_routes_point_to_point(_DEMAND_ORIG)
)
_T_HS_DEFAULT_ORIG: Dict[str, float] = network_totals(
    build_routes_hub_spoke(_DEMAND_ORIG, _PROJECT_HUBS, assign=None)
)
_T_HS_ASSIGN_ORIG: Dict[str, float] = network_totals(
    build_routes_hub_spoke(_DEMAND_ORIG, _PROJECT_HUBS, assign=_ORIG_ASSIGN)
)
_T_HS_THREE_ORIG: Dict[str, float] = network_totals(
    build_routes_hub_spoke(_DEMAND_ORIG, _THREE_HUBS, assign=None)
)

# Routes (structural, for metamorphic checks) — also computed once.
_ROUTES_P2P_ORIG = build_routes_point_to_point(_DEMAND_ORIG)
_ROUTES_HS_ORIG = build_routes_hub_spoke(_DEMAND_ORIG, _PROJECT_HUBS, assign=None)


# ---------------------------------------------------------------------------
# Four deterministic bijections
# ---------------------------------------------------------------------------

def _make_bijection_X01() -> Dict[str, str]:
    """B1: entirely alien codes X01..X06 — no alias with any real IATA code."""
    return {code: f"X{i+1:02d}" for i, code in enumerate(_ORIGINAL_CODES)}


def _make_bijection_revaz() -> Dict[str, str]:
    """B2: each code mapped to a reversed-alphabet counterpart (whole-code map).

    Reversed-alphabet: A↔Z, B↔Y, C↔X, D↔W, E↔V, F↔U, G↔T, H↔S, I↔R,
    J↔Q, K↔P, L↔O, M↔N, N↔M, O↔L, P↔K, Q↔J, R↔I, S↔H, T↔G, U↔F, V↔E,
    W↔D, X↔C, Y↔B, Z↔A.
    Applied character-by-character to each 3-letter IATA-like code.
    """
    alpha = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    rev = alpha[::-1]
    table = str.maketrans(alpha, rev)
    return {code: code.translate(table) for code in _ORIGINAL_CODES}


def _make_bijection_swappairs() -> Dict[str, str]:
    """B3: pairwise swap BOS<->JFK, ORD<->SFO, DEN<->PHX.

    This is the *nasty* bijection: if any production code contains a literal
    string like 'JFK' to identify a hub, the swap would point it at BOS's
    coordinates and the relabelled totals would differ from the originals.
    """
    return {
        "BOS": "JFK",
        "JFK": "BOS",
        "ORD": "SFO",
        "SFO": "ORD",
        "DEN": "PHX",
        "PHX": "DEN",
    }


def _make_bijection_random12345() -> Dict[str, str]:
    """B4: seeded random permutation using random.Random(12345).

    No unseeded randomness — the seed is fixed for reproducibility.
    We permute the six original codes, then map each original to its
    position-partner in the permutation.
    """
    rng = random.Random(12345)
    codes = list(_ORIGINAL_CODES)
    shuffled = list(codes)
    rng.shuffle(shuffled)
    return {orig: new for orig, new in zip(codes, shuffled)}


def _make_bijection_onechar() -> Dict[str, str]:
    """B5: six 1-character uppercase codes A..F — all codes have length 1.

    Any mutation that multiplies distance by a function of len(code) will
    be caught because len(code) changes from 3 (original) to 1 (new).
    The *ratio* of mutated vs. correct distance would be
    (1 + 0.001*2) / (1 + 0.001*6) = 1.002 / 1.006 ≈ 0.996 — a 0.4% error
    per leg, well above the 1e-12 relative tolerance.
    """
    return dict(zip(_ORIGINAL_CODES, list("ABCDEF")))


def _make_bijection_mixed_lengths() -> Dict[str, str]:
    """B6: bijection with codes of lengths 1, 2, 3, 4, 5, 9 — all distinct.

    Mapping:
        BOS -> Q          (length 1)
        JFK -> ZA         (length 2)   ← hub, now 2-char
        ORD -> WXY        (length 3)   ← hub, now 3-char (same as original)
        SFO -> MNOP       (length 4)
        DEN -> RSTUV      (length 5)
        PHX -> LONGCODE1  (length 9)

    Because no two codes share a length, any formula of the form
    f(len(a), len(b)) produces a unique scale factor for *every* edge.
    The mutation `* (1 + 0.001 * (len(a) + len(b)))` would scale edges by
    factors ranging from 1.003 (Q-ZA, lengths 1+2) to 1.018 (RSTUV-LONGCODE1,
    lengths 5+9), which are all different from the original all-3-char factor
    1.006 — producing visibly different totals at rel_tol 1e-12.
    """
    return {
        "BOS": "Q",
        "JFK": "ZA",
        "ORD": "WXY",
        "SFO": "MNOP",
        "DEN": "RSTUV",
        "PHX": "LONGCODE1",
    }


def _make_bijection_hub_initial_collision() -> Dict[str, str]:
    """B7: a non-hub code has len≠3 AND starts with the same character as the
    SECOND hub — designed to catch mutation (ii) in hubs.hub_of.

    Mutation (ii) being tested:
        if len(node)==3 and node in hubs: return node   (Rule 1 variant)
        elif len(node)!=3 and node[0] in {h[0] for h in hubs}:
            return the hub whose initial matches node[0]  (WRONG for non-hubs)

    Under mutation (ii), hub_of('WQ', ['ZA','WW']) returns 'WW' (second hub)
    because WQ[0]='W' matches WW[0]='W'.  But correct Rule 3 (no assign) says
    return the FIRST hub 'ZA'.  This mis-assignment changes H&S routes and thus
    totals—providing a detection signal even without touching distance.py.

    Mapping:
        BOS -> WQ         (length 2, NON-hub, starts with 'W' = ORD-hub initial)
        JFK -> ZA         (length 2, hub #1)
        ORD -> WW         (length 2, hub #2, initial 'W' = 'W')
        SFO -> MNOP       (length 4)
        DEN -> RSTUV      (length 5)
        PHX -> LONGCODE1  (length 9)
    """
    return {
        "BOS": "WQ",
        "JFK": "ZA",
        "ORD": "WW",
        "SFO": "MNOP",
        "DEN": "RSTUV",
        "PHX": "LONGCODE1",
    }


# All bijections as a list of (name, mapping) for parametrisation.
# B1-B4 are the original four; B5-B6 are the new mixed-length bijections;
# B7 is designed to catch mutation (ii) in hubs.hub_of.
_BIJECTIONS: List[Tuple[str, Dict[str, str]]] = [
    ("X01-X06",              _make_bijection_X01()),
    ("rev-alphabet",         _make_bijection_revaz()),
    ("swap-pairs",           _make_bijection_swappairs()),
    ("random-12345",         _make_bijection_random12345()),
    ("all-1-char",           _make_bijection_onechar()),
    ("mixed-lengths",        _make_bijection_mixed_lengths()),
    ("hub-initial-collision",_make_bijection_hub_initial_collision()),
]

# Convenience: bijection names for pytest parametrize ids
_BIJ_IDS = [name for name, _ in _BIJECTIONS]
_BIJ_MAPS = [m for _, m in _BIJECTIONS]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _relabel_coords(f: Dict[str, str]) -> Dict[str, Tuple[float, float]]:
    """Return a new AIRPORT_COORDS dict with keys renamed by bijection f."""
    orig = _distance_mod.AIRPORT_COORDS
    return {f[k]: v for k, v in orig.items()}


def _relabel_demand(
    demand: List[ODDemand], f: Dict[str, str]
) -> List[ODDemand]:
    """Return demand with all origin/destination codes renamed by f."""
    return [ODDemand(f[d.origin], f[d.destination], d.passengers) for d in demand]


def _relabel_hubs(hubs: List[str], f: Dict[str, str]) -> List[str]:
    """Return hub list with codes renamed by f (preserving order)."""
    return [f[h] for h in hubs]


def _relabel_assign(
    assign: Dict[str, str], f: Dict[str, str]
) -> Dict[str, str]:
    """Return assign dict with both keys and values renamed by f."""
    return {f[k]: f[v] for k, v in assign.items()}


def _build_bar_totals(
    t_p2p: Dict[str, float], t_hs: Dict[str, float]
) -> Dict[str, Tuple[float, float]]:
    """Build the dict to pass to bar_compare: metric_name -> (p2p_val, hs_val)."""
    return {k: (t_p2p[k], t_hs[k]) for k in _METRIC_KEYS}


# ---------------------------------------------------------------------------
# SANITY CHECK: verify that monkeypatching distance.AIRPORT_COORDS is visible
# to totals.network_totals (i.e. the patch reaches the right dict object).
# ---------------------------------------------------------------------------

def test_monkeypatch_sanity(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Example test — no tolerance.

    Confirms that monkeypatch.setattr(distance, 'AIRPORT_COORDS', ...) is
    actually seen by totals.network_totals when it calls distance.distance_nm.

    We replace AIRPORT_COORDS with a single-pair dict containing only X99
    and Y99, then call network_totals on a one-route demand using those codes.
    If the patch is NOT visible, distance_nm raises KeyError for X99; if it IS
    visible, the call succeeds and produces a positive aircraft_nm.

    Cannot catch: a case where the patch is visible but has no effect.
    """
    fake_coords = {
        "X99": (0.0, 0.0),
        "Y99": (0.0, 90.0),   # 90° of longitude at equator ≈ 5400 nm
    }
    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", fake_coords)

    route = RouteFlow("X99", "Y99", ("X99", "Y99"), 10.0)
    result = network_totals([route])
    # Should succeed and produce a positive distance.
    assert result["aircraft_nm"] > 0.0, (
        "Patch not visible to totals.network_totals; aircraft_nm came back 0 "
        "or distance_nm raised KeyError"
    )
    assert result["passengers"] == 10.0


# ---------------------------------------------------------------------------
# 1. TOTALS INVARIANCE — P2P, default hubs [JFK, ORD]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_totals_p2p_invariant_under_relabelling(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Property / invariance test — rel_tol=1e-12.

    Reason for tolerance: float summation order may differ when dict/set
    iteration or route order changes as codes change.  With 8 OD pairs and
    values in the 10^3–10^6 range, ULP-level differences are well below 1e-12
    relative.  1e-12 is deliberately tighter than most other tests so this test
    is sensitive to any code-order-dependent summation.

    For each bijection f, relabelling demand origins/destinations AND
    renaming AIRPORT_COORDS keys must yield identical network_totals for
    all eight keys in a point-to-point network.

    The reference totals (_T_P2P_ORIG) are computed at module-import time
    before any monkeypatching, so they use the real AIRPORT_COORDS.

    Sanity check: assert that all route nodes use only new codes (verifies
    the relabelling was applied to the demand, not silently ignored).

    Cannot catch: a bug that produces wrong totals identically for every
    bijection.
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_new = build_routes_point_to_point(demand_new)

    # Sanity: all route nodes should be new codes only.
    new_code_set = set(f.values())
    for r in routes_new:
        for node in r.route:
            assert node in new_code_set, (
                f"Route contains original code {node!r} after relabelling "
                f"with bijection {f!r}"
            )

    t_new = network_totals(routes_new)

    for key in _METRIC_KEYS:
        assert math.isclose(t_new[key], _T_P2P_ORIG[key], rel_tol=1e-12), (
            f"P2P totals[{key!r}] changed under bijection {f}: "
            f"orig={_T_P2P_ORIG[key]}, relabelled={t_new[key]}"
        )


# ---------------------------------------------------------------------------
# 2. TOTALS INVARIANCE — Hub-spoke, default assign (None)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_totals_hs_default_assign_invariant(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Property / invariance test — rel_tol=1e-12.

    Reason for tolerance: same as test_totals_p2p_invariant_under_relabelling.

    Hub-and-spoke with default assign=None.  Hubs are [JFK, ORD], relabelled
    to [f('JFK'), f('ORD')].  The hub ORDER is preserved, so the first hub is
    still the default for non-assigned non-hub airports.

    Reference totals (_T_HS_DEFAULT_ORIG) computed at module-import time.

    Sanity check: all route nodes must use only new codes.

    Cannot catch: same as P2P invariance test.
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_PROJECT_HUBS, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_new = build_routes_hub_spoke(demand_new, hubs_new, assign=None)

    new_code_set = set(f.values())
    for r in routes_new:
        for node in r.route:
            assert node in new_code_set, (
                f"H&S route contains original code {node!r} after relabelling"
            )

    t_new = network_totals(routes_new)

    for key in _METRIC_KEYS:
        assert math.isclose(t_new[key], _T_HS_DEFAULT_ORIG[key], rel_tol=1e-12), (
            f"H&S(default) totals[{key!r}] changed under bijection {f}: "
            f"orig={_T_HS_DEFAULT_ORIG[key]}, relabelled={t_new[key]}"
        )


# ---------------------------------------------------------------------------
# 3. TOTALS INVARIANCE — Hub-spoke, EXPLICIT assign mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_totals_hs_explicit_assign_invariant(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Property / invariance test — rel_tol=1e-12.

    Reason for tolerance: same as test_totals_p2p_invariant_under_relabelling.

    Hub-and-spoke with an EXPLICIT assign mapping.  Both the assign keys
    (non-hub airports) and values (hub assignments) must be relabelled with f.

    Reference totals (_T_HS_ASSIGN_ORIG) computed at module-import time.

    Sanity check: all route nodes must use only new codes.

    Cannot catch: same as P2P invariance test.
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_PROJECT_HUBS, f)
    assign_new = _relabel_assign(_ORIG_ASSIGN, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_new = build_routes_hub_spoke(demand_new, hubs_new, assign=assign_new)

    new_code_set = set(f.values())
    for r in routes_new:
        for node in r.route:
            assert node in new_code_set, (
                f"H&S(assign) route contains original code {node!r} after relabelling"
            )

    t_new = network_totals(routes_new)

    for key in _METRIC_KEYS:
        assert math.isclose(t_new[key], _T_HS_ASSIGN_ORIG[key], rel_tol=1e-12), (
            f"H&S(assign) totals[{key!r}] changed under bijection {f}: "
            f"orig={_T_HS_ASSIGN_ORIG[key]}, relabelled={t_new[key]}"
        )


# ---------------------------------------------------------------------------
# 4. TOTALS INVARIANCE — Hub-spoke, THREE hubs [JFK, ORD, SFO]
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_totals_hs_three_hubs_invariant(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Property / invariance test — rel_tol=1e-12.

    Reason for tolerance: same as test_totals_p2p_invariant_under_relabelling.

    Hub-and-spoke with three hubs [JFK, ORD, SFO] (default assign=None).
    Tests that the invariance holds for a different hub topology.

    Reference totals (_T_HS_THREE_ORIG) computed at module-import time.

    Cannot catch: same as P2P invariance test.
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_THREE_HUBS, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_new = build_routes_hub_spoke(demand_new, hubs_new, assign=None)

    new_code_set = set(f.values())
    for r in routes_new:
        for node in r.route:
            assert node in new_code_set, (
                f"H&S(3hubs) route contains original code {node!r} after relabelling"
            )

    t_new = network_totals(routes_new)

    for key in _METRIC_KEYS:
        assert math.isclose(t_new[key], _T_HS_THREE_ORIG[key], rel_tol=1e-12), (
            f"H&S(3hubs) totals[{key!r}] changed under bijection {f}: "
            f"orig={_T_HS_THREE_ORIG[key]}, relabelled={t_new[key]}"
        )


# ---------------------------------------------------------------------------
# 5. NEGATIVE CONTROL: relabel demand but NOT coords — must raise KeyError
#    OR produce different totals (both are acceptable detection signals).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_negative_control_relabel_demand_only(
    f: Dict[str, str],
) -> None:
    """
    Negative-control test — no tolerance.

    This test has teeth: it verifies that an INVALID relabelling (demand
    codes renamed but AIRPORT_COORDS NOT updated) is detected.

    If the new demand codes are not present in the original AIRPORT_COORDS,
    distance_nm raises KeyError (the expected outcome for alien bijections
    like X01..X06).  For swap-pair bijections the codes are all valid original
    codes, so distance_nm succeeds — but it looks up the WRONG coordinates
    (BOS gets JFK's coords, etc.), producing different distances and therefore
    different totals.

    The test asserts exactly one of these two outcomes:
      (a) A KeyError is raised during network_totals, OR
      (b) At least one of aircraft_nm, fuel_kg, NOx_kg differs from the correct
          original value by more than 1e-6 relative.

    Without this test, a mutation that hardcodes airport logic might pass all
    invariance tests by coincidence.

    Cannot catch: a bijection that happens to be the identity (not used here).
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)

    # Do NOT patch AIRPORT_COORDS — use the original dict with old codes.
    routes_new = build_routes_point_to_point(demand_new)

    try:
        t_bad = network_totals(routes_new)
    except KeyError:
        # Alien codes not in AIRPORT_COORDS → correctly detected.
        return

    # If no exception: the bijection uses existing codes (swap-pair / random case).
    # The totals must differ from the correct P2P totals.
    found_difference = False
    for key in ("aircraft_nm", "fuel_kg", "NOx_kg"):
        if not math.isclose(t_bad[key], _T_P2P_ORIG[key], rel_tol=1e-6):
            found_difference = True
            break

    assert found_difference, (
        f"Negative control FAILED for bijection {f!r}: relabelling demand "
        "without updating AIRPORT_COORDS produced the same totals as the "
        "correct result, which means the test has no teeth for this bijection."
    )


# ---------------------------------------------------------------------------
# 6. METAMORPHIC ROUTE CHECK — P2P routes map element-wise under f
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_metamorphic_routes_p2p(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Metamorphic test — no tolerance.

    For point-to-point, build_routes_point_to_point(relabelled_demand) must
    produce routes that are exactly the element-wise image of the original
    routes under bijection f:

        route_new[i] == tuple(f(x) for x in route_orig[i])

    Also checks origin/destination fields.  No distance calculations involved;
    this is a pure structural check.

    The original routes (_ROUTES_P2P_ORIG) are computed at module-import time.

    Cannot catch: a relabelling bug that cancels out between route structure
    and totals computation.
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    new_coords = _relabel_coords(f)
    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_new = build_routes_point_to_point(demand_new)

    assert len(_ROUTES_P2P_ORIG) == len(routes_new), (
        "Route count changed after relabelling"
    )
    for i, (ro, rn) in enumerate(zip(_ROUTES_P2P_ORIG, routes_new)):
        expected_route = tuple(f[x] for x in ro.route)
        assert rn.route == expected_route, (
            f"P2P route[{i}]: expected {expected_route!r}, got {rn.route!r}"
        )
        assert rn.origin == f[ro.origin], (
            f"P2P origin[{i}]: expected {f[ro.origin]!r}, got {rn.origin!r}"
        )
        assert rn.destination == f[ro.destination], (
            f"P2P destination[{i}]: expected {f[ro.destination]!r}, "
            f"got {rn.destination!r}"
        )
        assert rn.passengers == ro.passengers, (
            f"Passengers changed for route[{i}]: {ro.passengers} -> {rn.passengers}"
        )


# ---------------------------------------------------------------------------
# 7. METAMORPHIC ROUTE CHECK — Hub-spoke routes map element-wise under f
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_metamorphic_routes_hub_spoke(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Metamorphic test — no tolerance.

    For hub-and-spoke (hubs=[JFK,ORD], default assign), every route in the
    relabelled network must equal the element-wise image under f of the
    corresponding route in the original network:

        route_new[i] == tuple(f(x) for x in route_orig[i])

    This directly tests that hub_of() and build_routes_hub_spoke() contain
    no hardcoded airport code strings — any such hardcoding would cause the
    relabelled paths to differ structurally.

    The original routes (_ROUTES_HS_ORIG) are computed at module-import time.

    Cannot catch: a bug that transforms every code in an exactly cancelling
    way (e.g. a double transposition).
    """
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_PROJECT_HUBS, f)
    new_coords = _relabel_coords(f)
    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_new = build_routes_hub_spoke(demand_new, hubs_new, assign=None)

    assert len(_ROUTES_HS_ORIG) == len(routes_new), (
        "H&S route count changed after relabelling"
    )
    for i, (ro, rn) in enumerate(zip(_ROUTES_HS_ORIG, routes_new)):
        expected_route = tuple(f[x] for x in ro.route)
        assert rn.route == expected_route, (
            f"H&S route[{i}]: expected {expected_route!r}, got {rn.route!r}. "
            f"Orig route: {ro.route!r}. Bijection: {f!r}"
        )
        assert rn.origin == f[ro.origin]
        assert rn.destination == f[ro.destination]
        assert rn.passengers == ro.passengers


# ---------------------------------------------------------------------------
# 8. FIGURE INVARIANCE — bar_compare SVG is byte-identical before/after relabelling
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_figure_svg_invariant_under_relabelling(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """
    Invariance / determinism test — exact byte equality.

    bar_compare receives a dict keyed by METRIC NAMES (not airport codes):
        {"passengers": (p2p_val, hs_val), "flights": ..., ...}

    Because all totals are invariant under relabelling (tested above), the
    dict passed to bar_compare is identical before and after relabelling, so
    the SVG output must be byte-for-byte identical.

    If bar_compare embeds airport codes anywhere in the SVG (e.g. as x-tick
    labels), the byte comparison would fail, and we would report that as a
    finding.  The current implementation uses metric names as x-tick labels,
    so there are no airport codes in the SVG.

    The original bar_totals is built from module-level pre-computed totals
    (computed before any monkeypatching), and the SVG is rendered before
    patching so both renders share the same relabelling-independent data.

    Tolerance: exact byte equality (no numeric tolerance).
    Reason: SVG is text; any floating-point formatting difference would also
    appear in the totals comparison (already tested with rel_tol=1e-12).

    Cannot catch: a rendering difference that produces identical bytes
    by coincidence.
    """
    # Original bar totals (pre-computed, no patch needed).
    bar_totals_orig = _build_bar_totals(_T_P2P_ORIG, _T_HS_DEFAULT_ORIG)

    out_orig = tmp_path / "orig"
    out_orig.mkdir()
    bar_compare(
        bar_totals_orig,
        title="Network comparison",
        ylabel_with_units="Value",
        out_dir=out_orig,
    )
    svg_orig = (out_orig / "bar_compare.svg").read_bytes()

    # Relabelled bar totals.
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_PROJECT_HUBS, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_p2p_new = build_routes_point_to_point(demand_new)
    routes_hs_new = build_routes_hub_spoke(demand_new, hubs_new)
    t_p2p_new = network_totals(routes_p2p_new)
    t_hs_new = network_totals(routes_hs_new)
    bar_totals_new = _build_bar_totals(t_p2p_new, t_hs_new)

    out_new = tmp_path / "new"
    out_new.mkdir()
    bar_compare(
        bar_totals_new,
        title="Network comparison",
        ylabel_with_units="Value",
        out_dir=out_new,
    )
    svg_new = (out_new / "bar_compare.svg").read_bytes()

    # The two bar_totals dicts must be numerically equal first.
    for key in _METRIC_KEYS:
        assert math.isclose(
            bar_totals_new[key][0], bar_totals_orig[key][0], rel_tol=1e-12
        ), (
            f"P2P {key} differs: orig={bar_totals_orig[key][0]}, "
            f"new={bar_totals_new[key][0]}"
        )
        assert math.isclose(
            bar_totals_new[key][1], bar_totals_orig[key][1], rel_tol=1e-12
        ), (
            f"H&S {key} differs: orig={bar_totals_orig[key][1]}, "
            f"new={bar_totals_new[key][1]}"
        )

    # SVG bytes must be identical (same data → same rendering).
    assert svg_orig == svg_new, (
        f"SVG differs under bijection {f!r}. "
        f"Sizes: orig={len(svg_orig)}, new={len(svg_new)}. "
        "This likely means bar_compare embeds airport codes in the figure."
    )


# ---------------------------------------------------------------------------
# 9. FIGURE VALUE PAIRS — (p2p, hs) pairs match between relabelled and original
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("f", _BIJ_MAPS, ids=_BIJ_IDS)
def test_figure_value_pairs_identical(
    f: Dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Property test — rel_tol=1e-12.

    The (p2p_val, hub_val) pairs that would be passed to figures.bar_compare
    must be identical before and after relabelling for every metric key.

    Reason for tolerance: same as the totals invariance tests.  This test
    is separate from test_figure_svg_invariant_under_relabelling to provide
    a clearer failure message if the SVG differs (distinguishing a values
    issue from a rendering issue).

    The original bar totals are from module-level pre-computed values.

    Cannot catch: a metric key that is wrong in both runs in the same way.
    """
    bar_orig = _build_bar_totals(_T_P2P_ORIG, _T_HS_DEFAULT_ORIG)

    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_PROJECT_HUBS, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    routes_p2p_new = build_routes_point_to_point(demand_new)
    routes_hs_new = build_routes_hub_spoke(demand_new, hubs_new)
    t_p2p_new = network_totals(routes_p2p_new)
    t_hs_new = network_totals(routes_hs_new)
    bar_new = _build_bar_totals(t_p2p_new, t_hs_new)

    for key in _METRIC_KEYS:
        p2p_o, hs_o = bar_orig[key]
        p2p_n, hs_n = bar_new[key]
        assert math.isclose(p2p_n, p2p_o, rel_tol=1e-12), (
            f"P2P bar value for {key!r} changed: orig={p2p_o}, new={p2p_n}"
        )
        assert math.isclose(hs_n, hs_o, rel_tol=1e-12), (
            f"H&S bar value for {key!r} changed: orig={hs_o}, new={hs_n}"
        )


# ---------------------------------------------------------------------------
# 10. DEDICATED TEST: mixed-length bijection changes code lengths AND preserves totals
# ---------------------------------------------------------------------------

def test_relabelling_changes_code_length_but_not_totals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Property / invariance test — rel_tol=1e-12.  Dedicated sentinel for the
    code-length mutation.

    Kind: property (invariance) test.
    Tolerance: rel_tol=1e-12.  Reason: same floating-point arithmetic path,
    only airport-code labels differ; any code-length-dependent scaling would
    produce relative errors >> 1e-12 (the mutation produces ~0.6% error per leg
    for an all-3-char → all-3-char bijection; the mixed-length bijection makes
    the error per-leg range from 0.3% to 1.8% depending on the specific pair).

    This test has two mandatory assertions:

    1. MEANINGFUL: at least one code length changes between the original and
       relabelled sets.  Without this assertion, the test would vacuously pass
       for an identity bijection or any other length-preserving permutation,
       and the test would have no power against the target mutation.

    2. INVARIANT: network_totals is identical (rel_tol=1e-12) for P2P and H&S
       networks after a valid relabelling with the mixed-length bijection.

    The bijection used is B6 (mixed-lengths):
        BOS->Q (1), JFK->ZA (2), ORD->WXY (3), SFO->MNOP (4),
        DEN->RSTUV (5), PHX->LONGCODE1 (9).
    No two new codes share a length.  Under the mutation
    `* (1 + 0.001 * (len(a) + len(b)))`, each edge gets a DIFFERENT scale
    factor, so the total changes in a code-order-dependent way that cannot
    cancel out across all 8 OD pairs simultaneously.

    Cannot catch: a mutation that is provably independent of code identity
    (e.g. multiplying all distances by the same constant).
    """
    f = _make_bijection_mixed_lengths()

    # --- Assertion 1: the bijection actually changes code lengths ---
    original_lengths = sorted(len(code) for code in _ORIGINAL_CODES)
    new_lengths = sorted(len(new_code) for new_code in f.values())
    assert original_lengths != new_lengths, (
        f"Mixed-length bijection is INVALID for this test: original lengths "
        f"{original_lengths} == new lengths {new_lengths}.  The test only has "
        "power if code lengths actually change."
    )
    # Also assert all new lengths are distinct (otherwise some pairs share a
    # length and the length-dependent mutation might partially cancel).
    assert len(set(new_lengths)) == len(new_lengths), (
        f"New code lengths {new_lengths} contain duplicates; the test requires"
        " all lengths to be distinct so every edge has a unique mutation factor."
    )

    # --- Assertion 2: totals are invariant under the bijection ---
    demand_new = _relabel_demand(_DEMAND_ORIG, f)
    hubs_new = _relabel_hubs(_PROJECT_HUBS, f)
    new_coords = _relabel_coords(f)

    monkeypatch.setattr(_distance_mod, "AIRPORT_COORDS", new_coords)

    # P2P
    t_p2p_new = network_totals(build_routes_point_to_point(demand_new))
    for key in _METRIC_KEYS:
        assert math.isclose(t_p2p_new[key], _T_P2P_ORIG[key], rel_tol=1e-12), (
            f"P2P totals[{key!r}] changed under mixed-length bijection {f}: "
            f"orig={_T_P2P_ORIG[key]}, relabelled={t_p2p_new[key]}"
        )

    # H&S default
    t_hs_new = network_totals(build_routes_hub_spoke(demand_new, hubs_new))
    for key in _METRIC_KEYS:
        assert math.isclose(t_hs_new[key], _T_HS_DEFAULT_ORIG[key], rel_tol=1e-12), (
            f"H&S totals[{key!r}] changed under mixed-length bijection {f}: "
            f"orig={_T_HS_DEFAULT_ORIG[key]}, relabelled={t_hs_new[key]}"
        )
