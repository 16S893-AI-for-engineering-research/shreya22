"""
Tests for project/src/network_perf.py — network fuel and NOx from leg flows,
real aircraft mix, and injected per-flight performance function.

All tests use hand-built fakes only; no OpenAP calls, no data/bts-cache.
The one cache-dependent test is marked @pytest.mark.skipif at *function* level
(module-level skips are banned; function-level @pytest.mark.skipif used instead).

Test catalogue
--------------
 1. segment_loads_conservation      — example + conservation
 2. segment_loads_hub_spoke         — example (3-airport mock)
 3. segment_loads_p2p               — example
 4. segment_loads_sum_eq_passenger_legs — conservation vs legs.py
 5. network_perf_hand_example       — example, hand-calculated
 6. network_perf_doubling           — metamorphic (double demand → double fuel/NOx)
 7. network_perf_split_od           — metamorphic (split OD → same totals)
 8. network_perf_relabel            — metamorphic (relabel airports → same totals)
 9. network_perf_unmapped_share     — unmapped share reported; fuel reduced exactly
10. network_perf_missing_segment_raises — missing segment raises with message
11. network_perf_missing_segment_option — option records, no raise
12. network_perf_bad_load_factor    — nan/<=0 load factor raises
13. network_perf_bad_seats          — seats <=0 raises
14. network_perf_result_keys        — result dict has required keys
15. network_perf_no_renorm          — unmapped share NOT renormalised by default
16. network_perf_segment_rows       — per-segment rows keyed by (o,d)
17. network_perf_real_data_p2p      — (cache-skipif) P2P on region demand,
                                      fake perf, no missing segments,
                                      compare computed flights to T-100 Q1

Docstrings state: kind, tolerance + reason, cannot-catch.
"""
from __future__ import annotations

import math
import os
import pathlib
import sys

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from networks import ODDemand, RouteFlow, build_routes_point_to_point, build_routes_hub_spoke  # noqa: E402
from legs import passenger_legs  # noqa: E402
from network_perf import segment_loads, network_perf  # noqa: E402

# ─── cache sentinel ──────────────────────────────────────────────────────────
_CACHE_DIR = pathlib.Path(__file__).parent.parent.parent / "data" / "bts-cache"
_CACHE_T100 = [
    _CACHE_DIR / "t100_2019_01.csv",
    _CACHE_DIR / "t100_2019_02.csv",
    _CACHE_DIR / "t100_2019_03.csv",
]
_DEMAND_CSV = _CACHE_DIR / "demand_region_2019Q1.csv"
_CACHE_ABSENT = not (
    all(p.exists() for p in _CACHE_T100) and _DEMAND_CSV.exists()
)
_CACHE_REASON = (
    "data/bts-cache/ files not present (git-ignored). "
    "Populate with data-fetch script before running this test."
)

# ─── shared fakes ────────────────────────────────────────────────────────────

# 3-airport mock: ORD hub, BOS and SFO spokes.
# Distances (nm, rounded for hand-calc):
#   BOS->ORD  ≈ 751.2   ORD->SFO ≈ 1600.3   BOS->SFO ≈ 2344.1
# Computed precisely from distance.py at module load time:
import distance as _distance_mod  # noqa: E402
_BOS_ORD = _distance_mod.distance_nm("BOS", "ORD")
_ORD_SFO = _distance_mod.distance_nm("ORD", "SFO")
_BOS_SFO = _distance_mod.distance_nm("BOS", "SFO")
_DEN_SFO = _distance_mod.distance_nm("DEN", "SFO")
_BOS_DEN = _distance_mod.distance_nm("BOS", "DEN")

# Simple fake perf function: fuel_kg = k * distance_nm per aircraft code.
# k=1.0 for code "A", k=2.0 for code "B"; nox_kg = 0.05 * fuel_kg always.
_K = {"A": 1.0, "B": 2.0}
_NOX_RATIO = 0.05


def _fake_perf(bts_code: str, distance_nm: float) -> dict | None:
    """Fake per-flight perf: fuel = k[code]*distance_nm, nox = 0.05*fuel."""
    if bts_code not in _K:
        return None  # unmapped
    fuel = _K[bts_code] * distance_nm
    return {"fuel_kg": fuel, "nox_kg": _NOX_RATIO * fuel}


def _fake_distance_fn(a: str, b: str) -> float:
    """Route all calls to distance.distance_nm (real AIRPORT_COORDS)."""
    return _distance_mod.distance_nm(a, b)


def _make_hub_spoke_routes(passengers_bos_sfo: float = 100.0,
                           passengers_bos_ord: float = 50.0) -> list[RouteFlow]:
    """
    3-airport hub-and-spoke mock.
    BOS->SFO goes through ORD hub: [BOS, ORD, SFO]
    BOS->ORD is direct (ORD is the hub): [BOS, ORD]
    """
    return [
        RouteFlow("BOS", "SFO", ("BOS", "ORD", "SFO"), passengers_bos_sfo),
        RouteFlow("BOS", "ORD", ("BOS", "ORD"), passengers_bos_ord),
    ]


def _make_mix_two_aircraft(
    seg_loads: dict[tuple[str, str], float],
    share_A: float = 0.6,
    share_B: float = 0.4,
    seats_per_departure: float = 150.0,
    load_factor: float = 0.80,
) -> dict[tuple[str, str], dict]:
    """
    Build a minimal aircraft_mix for the segments present in seg_loads.
    Uses two BTS codes: 'A' (mapped in _fake_perf) and 'B' (mapped).
    """
    mix = {}
    for seg in seg_loads:
        mix[seg] = {
            "shares": {"A": share_A, "B": share_B},
            "seats_per_departure": seats_per_departure,
            "load_factor": load_factor,
            "_total_departures": 100.0,  # arbitrary; not used by network_perf
        }
    return mix


# ═══════════════════════════════════════════════════════════════════════════
# 1. segment_loads — conservation and examples
# ═══════════════════════════════════════════════════════════════════════════


def test_segment_loads_conservation():
    """
    Kind: conservation / example.
    Tolerance: abs <= 1e-9 (float addition of known inputs).
    Reason: sum of all segment loads must equal sum over routes of
            passengers * (number of legs), i.e., passenger_legs from legs.py.
            This is the fundamental conservation identity for leg-level
            aggregation.
    Cannot catch: a bug that correctly totals but wrong-assigns loads to
                  individual segments.
    """
    routes = _make_hub_spoke_routes(100.0, 50.0)
    loads = segment_loads(routes)
    # BOS->SFO has 2 legs: BOS->ORD and ORD->SFO, each with 100 passengers
    # BOS->ORD has 1 leg: BOS->ORD, with 50 passengers
    # sum of all segment loads = 100 + 100 + 50 = 250
    total_seg_load = sum(loads.values())
    pl = passenger_legs(routes)
    assert abs(total_seg_load - pl) <= 1e-9, (
        f"sum(segment_loads) = {total_seg_load}, passenger_legs = {pl}"
    )


def test_segment_loads_hub_spoke_example():
    """
    Kind: example (hand-calculated).
    Tolerance: abs <= 1e-9.
    Reason: BOS->SFO route has two legs contributing to (BOS,ORD) and
            (ORD,SFO).  BOS->ORD adds to (BOS,ORD) only.  So:
              (BOS,ORD) -> 100 + 50 = 150
              (ORD,SFO) -> 100
    Cannot catch: a bug that sums but assigns to wrong direction.
    """
    routes = _make_hub_spoke_routes(100.0, 50.0)
    loads = segment_loads(routes)
    assert abs(loads[("BOS", "ORD")] - 150.0) <= 1e-9, (
        f"(BOS,ORD) load = {loads.get(('BOS','ORD'))}, expected 150.0"
    )
    assert abs(loads[("ORD", "SFO")] - 100.0) <= 1e-9, (
        f"(ORD,SFO) load = {loads.get(('ORD','SFO'))}, expected 100.0"
    )


def test_segment_loads_p2p_example():
    """
    Kind: example (hand-calculated).
    Tolerance: abs <= 1e-9.
    Reason: point-to-point routes each contribute exactly one leg with full
            OD passengers.
    Cannot catch: bugs that only manifest with connecting legs.
    """
    routes = [
        RouteFlow("BOS", "SFO", ("BOS", "SFO"), 80.0),
        RouteFlow("BOS", "ORD", ("BOS", "ORD"), 40.0),
    ]
    loads = segment_loads(routes)
    assert abs(loads[("BOS", "SFO")] - 80.0) <= 1e-9
    assert abs(loads[("BOS", "ORD")] - 40.0) <= 1e-9


def test_segment_loads_sum_equals_passenger_legs():
    """
    Kind: conservation (property).
    Tolerance: abs <= 1e-9 (relative to totals ~250 pax-legs; machine error
               is O(N * eps * max_pax) ~ 2 * 1e-15 * 100 = 2e-13).
    Reason: sum(segment_loads.values()) == passenger_legs(routes) is a
            mandatory conservation identity.  Violation means loads are
            either double-counted or dropped.
    Cannot catch: correct total with wrong per-segment assignment.
    """
    demand = [
        ODDemand("BOS", "SFO", 100.0),
        ODDemand("BOS", "ORD", 50.0),
        ODDemand("ORD", "SFO", 75.0),
    ]
    routes = build_routes_hub_spoke(demand, hubs=["ORD"])
    loads = segment_loads(routes)
    total_load = sum(loads.values())
    pl = passenger_legs(routes)
    assert abs(total_load - pl) <= 1e-9, (
        f"sum(segment_loads) = {total_load}, passenger_legs = {pl}"
    )


def test_segment_loads_empty_routes():
    """
    Kind: example (edge case).
    Tolerance: exact (empty dict).
    Reason: an empty route list must return an empty dict, not raise.
    Cannot catch: a default dict that silently creates keys on access.
    """
    loads = segment_loads([])
    assert loads == {}, f"expected empty dict, got {loads}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. network_perf — result keys
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_result_keys():
    """
    Kind: property (schema check).
    Tolerance: exact (key presence).
    Reason: downstream consumers depend on a stable dict schema.
    Cannot catch: correct keys with wrong value types.
    """
    routes = _make_hub_spoke_routes(100.0, 50.0)
    loads = segment_loads(routes)
    mix = _make_mix_two_aircraft(loads)
    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)
    # Required top-level keys
    required = {"segments", "total_fuel_kg", "total_nox_kg",
                "total_flights", "unmapped_share"}
    missing = required - set(result.keys())
    assert not missing, f"network_perf result missing keys: {missing}"
    # Each segment entry must have certain keys
    for seg, sdata in result["segments"].items():
        seg_keys = {"passengers", "flights", "fuel_kg", "nox_kg", "unmapped_share"}
        seg_missing = seg_keys - set(sdata.keys())
        assert not seg_missing, (
            f"Segment {seg} missing keys: {seg_missing}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. network_perf — hand-calculated example
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_hand_example():
    """
    Kind: example (hand-calculated).
    Tolerance: rel 1e-9 on each numeric value (double-precision arithmetic on
               floats known to be exact rational fractions of small integers).
    Reason: verify the exact formula:
        flights_on_leg = load / (seats_per_departure * load_factor)
        fuel_on_leg    = flights * (share_A * perf_A.fuel + share_B * perf_B.fuel)
                       = flights * (0.6 * 1.0 * d + 0.4 * 2.0 * d)
                       = flights * 1.4 * d
        nox_on_leg     = flights * 1.4 * d * 0.05

    Setup:
      routes: BOS->SFO via ORD (100 pax), BOS->ORD direct (50 pax)
      segment_loads: (BOS,ORD)=150, (ORD,SFO)=100
      mix: shares A=0.6, B=0.4, seats=150, LF=0.80
      flights_BOS_ORD = 150 / (150 * 0.80) = 1.25
      flights_ORD_SFO = 100 / (150 * 0.80) = 0.8333...

      fuel_per_flight = 0.6*1.0*d + 0.4*2.0*d = 1.4*d
      fuel_BOS_ORD = 1.25 * 1.4 * BOS_ORD_nm
      fuel_ORD_SFO = (100/120) * 1.4 * ORD_SFO_nm
      total_fuel = fuel_BOS_ORD + fuel_ORD_SFO
      total_nox  = total_fuel * 0.05

    Cannot catch: sign errors in the NOx formula that happen to net out.
    """
    routes = _make_hub_spoke_routes(100.0, 50.0)
    loads = segment_loads(routes)
    mix = _make_mix_two_aircraft(loads, share_A=0.6, share_B=0.4,
                                  seats_per_departure=150.0, load_factor=0.80)
    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)

    # ── hand-compute expected values ──
    spd, lf = 150.0, 0.80
    fl_bos_ord = 150.0 / (spd * lf)   # 1.25
    fl_ord_sfo = 100.0 / (spd * lf)   # 0.8333...

    d_bos_ord = _BOS_ORD
    d_ord_sfo = _ORD_SFO

    # fuel per flight at distance d: 0.6*1.0*d + 0.4*2.0*d = 1.4*d
    fuel_per_fl_bos_ord = 0.6 * 1.0 * d_bos_ord + 0.4 * 2.0 * d_bos_ord
    fuel_per_fl_ord_sfo = 0.6 * 1.0 * d_ord_sfo + 0.4 * 2.0 * d_ord_sfo

    exp_fuel_bos_ord = fl_bos_ord * fuel_per_fl_bos_ord
    exp_fuel_ord_sfo = fl_ord_sfo * fuel_per_fl_ord_sfo
    exp_total_fuel = exp_fuel_bos_ord + exp_fuel_ord_sfo

    exp_nox_total = exp_total_fuel * _NOX_RATIO

    exp_total_flights = fl_bos_ord + fl_ord_sfo

    tol = 1e-9

    assert abs(result["total_fuel_kg"] - exp_total_fuel) <= tol * exp_total_fuel, (
        f"total_fuel_kg = {result['total_fuel_kg']}, expected {exp_total_fuel}"
    )
    assert abs(result["total_nox_kg"] - exp_nox_total) <= tol * exp_nox_total, (
        f"total_nox_kg = {result['total_nox_kg']}, expected {exp_nox_total}"
    )
    assert abs(result["total_flights"] - exp_total_flights) <= tol * exp_total_flights, (
        f"total_flights = {result['total_flights']}, expected {exp_total_flights}"
    )

    # ── per-segment checks ──
    segs = result["segments"]
    assert abs(segs[("BOS", "ORD")]["flights"] - fl_bos_ord) <= tol * fl_bos_ord
    assert abs(segs[("ORD", "SFO")]["flights"] - fl_ord_sfo) <= tol * fl_ord_sfo
    assert abs(segs[("BOS", "ORD")]["fuel_kg"] - exp_fuel_bos_ord) <= tol * exp_fuel_bos_ord
    assert abs(segs[("ORD", "SFO")]["fuel_kg"] - exp_fuel_ord_sfo) <= tol * exp_fuel_ord_sfo


def test_network_perf_segment_rows():
    """
    Kind: example.
    Tolerance: abs <= 1e-9.
    Reason: per-segment rows must record passengers equal to segment_loads,
            and unmapped_share=0 when all aircraft codes are mapped.
    Cannot catch: wrong value in a correctly-keyed segment entry.
    """
    routes = _make_hub_spoke_routes(100.0, 50.0)
    loads = segment_loads(routes)
    mix = _make_mix_two_aircraft(loads)
    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)

    segs = result["segments"]
    assert ("BOS", "ORD") in segs, "Segment (BOS,ORD) missing from result"
    assert ("ORD", "SFO") in segs, "Segment (ORD,SFO) missing from result"

    assert abs(segs[("BOS", "ORD")]["passengers"] - 150.0) <= 1e-9
    assert abs(segs[("ORD", "SFO")]["passengers"] - 100.0) <= 1e-9

    # no unmapped aircraft in this mix
    assert segs[("BOS", "ORD")]["unmapped_share"] == 0.0
    assert segs[("ORD", "SFO")]["unmapped_share"] == 0.0
    assert result["unmapped_share"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# 4. Metamorphic: doubling demand doubles fuel and NOx
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_doubling():
    """
    Kind: metamorphic.
    Tolerance: rel 1e-9 (relative scaling; double-precision float arithmetic
               on a linear formula; error is O(machine_eps) ≈ 2e-16).
    Reason: all quantities (flights, fuel, NOx) scale linearly with demand
            because flights = load / (spd * lf) and fuel = flights * constant.
            Any deviation indicates a non-linear or caching bug.
    Cannot catch: a quadratic bug that happens to double exactly.
    """
    routes1 = _make_hub_spoke_routes(100.0, 50.0)
    loads1 = segment_loads(routes1)
    mix1 = _make_mix_two_aircraft(loads1)
    res1 = network_perf(routes1, mix1, _fake_perf, _fake_distance_fn)

    routes2 = _make_hub_spoke_routes(200.0, 100.0)  # doubled
    loads2 = segment_loads(routes2)
    mix2 = _make_mix_two_aircraft(loads2)  # same mix shape, doubled load → doubled flights
    res2 = network_perf(routes2, mix2, _fake_perf, _fake_distance_fn)

    tol = 1e-9
    for key in ("total_fuel_kg", "total_nox_kg", "total_flights"):
        ratio = res2[key] / res1[key]
        assert abs(ratio - 2.0) <= tol, (
            f"Doubling demand: {key} ratio = {ratio}, expected 2.0"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 5. Metamorphic: splitting one OD into two leaves totals unchanged
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_split_od():
    """
    Kind: metamorphic.
    Tolerance: rel 1e-9.
    Reason: splitting OD (BOS->ORD, 50 pax) into two RouteFlow records each
            with 25 pax leaves segment_loads unchanged (both are direct BOS->ORD).
            Hence network_perf totals must be identical because segment loads
            and mix are unchanged.
    Cannot catch: bugs that incorrectly aggregate RouteFlow records by origin/
                  destination rather than by segment.
    """
    routes_whole = _make_hub_spoke_routes(100.0, 50.0)
    loads_whole = segment_loads(routes_whole)
    mix = _make_mix_two_aircraft(loads_whole)
    res_whole = network_perf(routes_whole, mix, _fake_perf, _fake_distance_fn)

    # Split the BOS->ORD route into two halves
    routes_split = [
        RouteFlow("BOS", "SFO", ("BOS", "ORD", "SFO"), 100.0),
        RouteFlow("BOS", "ORD", ("BOS", "ORD"), 25.0),
        RouteFlow("BOS", "ORD", ("BOS", "ORD"), 25.0),
    ]
    loads_split = segment_loads(routes_split)
    # loads_split should equal loads_whole
    for seg in loads_whole:
        assert abs(loads_split[seg] - loads_whole[seg]) <= 1e-9, (
            f"Segment {seg}: split load {loads_split.get(seg)} != whole load {loads_whole[seg]}"
        )

    res_split = network_perf(routes_split, mix, _fake_perf, _fake_distance_fn)

    tol = 1e-9
    for key in ("total_fuel_kg", "total_nox_kg", "total_flights"):
        diff = abs(res_whole[key] - res_split[key])
        assert diff <= tol * max(abs(res_whole[key]), 1.0), (
            f"Split OD: {key} whole={res_whole[key]}, split={res_split[key]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. Metamorphic: relabelling airports leaves totals unchanged
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_relabel():
    """
    Kind: metamorphic (relabelling invariance).
    Tolerance: rel 1e-9.
    Reason: renaming BOS→X, ORD→Y, SFO→Z while updating routes, mix keys,
            AND the distance_fn consistently must leave fuel/NOx/flights
            unchanged because distances are identical.
    Cannot catch: a bug that correctly handles relabelling but wrong-assigns
                  distances by code.
    """
    routes_orig = _make_hub_spoke_routes(100.0, 50.0)
    loads_orig = segment_loads(routes_orig)
    mix_orig = _make_mix_two_aircraft(loads_orig)
    res_orig = network_perf(routes_orig, mix_orig, _fake_perf, _fake_distance_fn)

    # Relabelling: BOS→XX, ORD→YY, SFO→ZZ
    rename = {"BOS": "XX", "ORD": "YY", "SFO": "ZZ"}

    def relabel_route(rf: RouteFlow) -> RouteFlow:
        return RouteFlow(
            rename[rf.origin], rename[rf.destination],
            tuple(rename[a] for a in rf.route),
            rf.passengers,
        )

    routes_relabelled = [relabel_route(r) for r in routes_orig]
    loads_relabelled = segment_loads(routes_relabelled)

    # Relabel mix keys
    mix_relabelled = {
        (rename[o], rename[d]): v
        for (o, d), v in mix_orig.items()
    }

    # Fake distance function that uses the inverse rename map
    inv_rename = {v: k for k, v in rename.items()}

    def relabelled_dist(a: str, b: str) -> float:
        return _fake_distance_fn(inv_rename[a], inv_rename[b])

    res_rel = network_perf(routes_relabelled, mix_relabelled, _fake_perf, relabelled_dist)

    tol = 1e-9
    for key in ("total_fuel_kg", "total_nox_kg", "total_flights"):
        assert abs(res_orig[key] - res_rel[key]) <= tol * max(abs(res_orig[key]), 1.0), (
            f"Relabelling: {key} orig={res_orig[key]}, relabelled={res_rel[key]}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. Unmapped share: reported and fuel reduced by exactly that share
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_unmapped_share_reported():
    """
    Kind: example + property.
    Tolerance: abs <= 1e-9.
    Reason: code 'C' is unmapped (perf returns None). The mix has shares
            A=0.5, B=0.3, C=0.2.  The unmapped share per segment must be
            exactly 0.2.  The fuel must use only A and B contributions
            (normalised share NOT re-normalised: fuel = flights * (0.5*fuel_A
            + 0.3*fuel_B), NOT (5/8)*fuel_A + (3/8)*fuel_B).
    Cannot catch: a wrong per-aircraft contribution within the mapped share.
    """
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 120.0)]
    loads = segment_loads(routes)

    # mix with unmapped code 'C'
    mix = {("BOS", "ORD"): {
        "shares": {"A": 0.5, "B": 0.3, "C": 0.2},
        "seats_per_departure": 150.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}

    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)

    # unmapped_share per segment and network should be 0.2
    seg = result["segments"][("BOS", "ORD")]
    assert abs(seg["unmapped_share"] - 0.2) <= 1e-9, (
        f"segment unmapped_share = {seg['unmapped_share']}, expected 0.2"
    )
    assert abs(result["unmapped_share"] - 0.2) <= 1e-9, (
        f"network unmapped_share = {result['unmapped_share']}, expected 0.2"
    )

    # fuel must use ONLY A and B contributions, NOT renormalised
    # flights = 120 / (150 * 0.80) = 1.0
    flights = 120.0 / (150.0 * 0.80)
    d = _BOS_ORD
    # fuel = flights * (share_A * k_A * d + share_B * k_B * d)
    #      = 1.0 * (0.5 * 1.0 * d + 0.3 * 2.0 * d)
    #      = 1.0 * 1.1 * d
    exp_fuel = flights * (0.5 * 1.0 * d + 0.3 * 2.0 * d)
    assert abs(seg["fuel_kg"] - exp_fuel) <= 1e-9 * exp_fuel, (
        f"fuel_kg = {seg['fuel_kg']}, expected {exp_fuel} (no renorm)"
    )

    # verify NOT renormalised: if it were, fuel would be flights*(5/8*k_A + 3/8*k_B)*d
    renorm_fuel = flights * ((5 / 8) * 1.0 * d + (3 / 8) * 2.0 * d)
    assert abs(seg["fuel_kg"] - renorm_fuel) > 1e-6, (
        "fuel_kg matches renormalised value — renormalisation must NOT happen by default"
    )


def test_network_perf_no_renormalise_default():
    """
    Kind: property (negative test for renormalisation).
    Tolerance: difference > 1e-6 (the renorm/no-renorm delta for typical
               share=0.2 unmapped at ~1000 nm distance is ~100 kg*nm → large).
    Reason: the spec says 'do NOT renormalise the remaining shares unless an
            explicit parameter says so (default False, documented)'.  This test
            verifies that the default path does not renormalise.
    Cannot catch: renormalisation that is only triggered by specific share ratios.
    """
    routes = [RouteFlow("BOS", "SFO", ("BOS", "SFO"), 200.0)]
    loads = segment_loads(routes)
    mix = {("BOS", "SFO"): {
        "shares": {"A": 0.7, "B": 0.1, "C": 0.2},   # C unmapped
        "seats_per_departure": 150.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}

    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)
    seg = result["segments"][("BOS", "SFO")]

    flights = 200.0 / (150.0 * 0.80)
    d = _BOS_SFO
    # no-renorm fuel
    no_renorm = flights * (0.7 * 1.0 * d + 0.1 * 2.0 * d)
    # renorm fuel (if renorm were applied): mapped share = 0.8, renorm to 1.0
    renorm = flights * ((0.7 / 0.8) * 1.0 * d + (0.1 / 0.8) * 2.0 * d)

    assert abs(seg["fuel_kg"] - no_renorm) <= 1e-9 * no_renorm, (
        f"Default path: fuel_kg={seg['fuel_kg']} should equal no-renorm={no_renorm}"
    )
    assert abs(seg["fuel_kg"] - renorm) > 1e-6, (
        "Default path should NOT renormalise; fuel matched renorm value"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 8. Missing segment raises by default
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_missing_segment_raises():
    """
    Kind: example (error path).
    Tolerance: no numeric tolerance; tests exception type and message content.
    Reason: a segment present in routes but absent from mix must raise by
            default (not silently produce 0 fuel).  The error message must
            name the missing segment.
    Cannot catch: a case where mix accidentally contains the segment.
    """
    routes = [
        RouteFlow("BOS", "SFO", ("BOS", "ORD", "SFO"), 100.0),
    ]
    # mix only has (BOS,ORD); (ORD,SFO) is missing
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": 150.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}

    with pytest.raises((KeyError, ValueError)) as exc_info:
        network_perf(routes, mix, _fake_perf, _fake_distance_fn)

    # The missing segment must be named in the message
    msg = str(exc_info.value)
    assert "ORD" in msg and "SFO" in msg, (
        f"Error message must name the missing segment (ORD,SFO); got: {msg!r}"
    )


def test_network_perf_missing_segment_option():
    """
    Kind: example (option path).
    Tolerance: abs <= 1e-9 for reported fuel where present.
    Reason: with allow_missing=True (or equivalent documented parameter),
            missing segments are recorded in result['missing_segments'] and
            fuel is left out; no raise.
    Cannot catch: a silent zero being returned without recording the segment.
    """
    routes = [
        RouteFlow("BOS", "SFO", ("BOS", "ORD", "SFO"), 100.0),
    ]
    # mix only has (BOS,ORD); (ORD,SFO) is missing
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": 150.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}

    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn,
                          allow_missing=True)

    # result must record the missing segment
    assert "missing_segments" in result, (
        "allow_missing=True must produce 'missing_segments' key in result"
    )
    missing = result["missing_segments"]
    assert ("ORD", "SFO") in missing, (
        f"(ORD,SFO) must be listed in missing_segments; got {missing}"
    )

    # The BOS->ORD segment's fuel must still be computed
    segs = result["segments"]
    assert ("BOS", "ORD") in segs, "(BOS,ORD) must still be in segments"
    assert segs[("BOS", "ORD")]["fuel_kg"] > 0.0, "fuel for (BOS,ORD) must be > 0"


# ═══════════════════════════════════════════════════════════════════════════
# 9. Bad mix data raises
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_nan_load_factor_raises():
    """
    Kind: example (error path).
    Tolerance: no numeric tolerance.
    Reason: a NaN load_factor (from load_t100 when SEATS=0) must raise so the
            caller knows the fleet data is degenerate, not silently produce 0.
    Cannot catch: NaN that results from other arithmetic rather than input.
    """
    import math
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 100.0)]
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": 150.0,
        "load_factor": float("nan"),
        "_total_departures": 100.0,
    }}
    with pytest.raises((ValueError, ZeroDivisionError)):
        network_perf(routes, mix, _fake_perf, _fake_distance_fn)


def test_network_perf_zero_load_factor_raises():
    """
    Kind: example (error path).
    Tolerance: no numeric tolerance.
    Reason: load_factor=0 causes division-by-zero in flights = load/(spd*lf).
            Must raise explicitly rather than produce inf or NaN flights.
    Cannot catch: zero LF that arises from a different code path.
    """
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 100.0)]
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": 150.0,
        "load_factor": 0.0,
        "_total_departures": 100.0,
    }}
    with pytest.raises((ValueError, ZeroDivisionError)):
        network_perf(routes, mix, _fake_perf, _fake_distance_fn)


def test_network_perf_negative_load_factor_raises():
    """
    Kind: example (error path).
    Tolerance: no numeric tolerance.
    Reason: negative load_factor is physically impossible.  Must raise.
    Cannot catch: negative LF from a specific data source.
    """
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 100.0)]
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": 150.0,
        "load_factor": -0.5,
        "_total_departures": 100.0,
    }}
    with pytest.raises((ValueError, ZeroDivisionError)):
        network_perf(routes, mix, _fake_perf, _fake_distance_fn)


def test_network_perf_zero_seats_raises():
    """
    Kind: example (error path).
    Tolerance: no numeric tolerance.
    Reason: seats_per_departure=0 causes division-by-zero in
            flights = load/(spd*lf).  Must raise explicitly.
    Cannot catch: zero seats from other data sources.
    """
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 100.0)]
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": 0.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}
    with pytest.raises((ValueError, ZeroDivisionError)):
        network_perf(routes, mix, _fake_perf, _fake_distance_fn)


def test_network_perf_negative_seats_raises():
    """
    Kind: example (error path).
    Tolerance: no numeric tolerance.
    Reason: negative seats is physically impossible.  Must raise.
    Cannot catch: negative seats from a specific data source.
    """
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 100.0)]
    mix = {("BOS", "ORD"): {
        "shares": {"A": 1.0},
        "seats_per_departure": -150.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}
    with pytest.raises((ValueError, ZeroDivisionError)):
        network_perf(routes, mix, _fake_perf, _fake_distance_fn)


# ═══════════════════════════════════════════════════════════════════════════
# 10. Unmapped share is departure-weighted at network level
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_unmapped_share_departure_weighted():
    """
    Kind: example (hand-calculated).
    Tolerance: abs <= 1e-9.
    Reason: network-level unmapped_share must weight each segment's unmapped
            share by that segment's _total_departures.
      Segment (BOS,ORD): unmapped=0.2, _total_departures=100
      Segment (ORD,SFO): unmapped=0.0, _total_departures=200
      Network unmapped = (0.2*100 + 0.0*200) / (100+200) = 20/300 = 1/15
    Cannot catch: a simple average rather than a departure-weighted average.
    """
    routes = [
        RouteFlow("BOS", "SFO", ("BOS", "ORD", "SFO"), 100.0),
    ]
    mix = {
        ("BOS", "ORD"): {
            "shares": {"A": 0.8, "C": 0.2},  # C unmapped
            "seats_per_departure": 150.0,
            "load_factor": 0.80,
            "_total_departures": 100.0,
        },
        ("ORD", "SFO"): {
            "shares": {"A": 1.0},  # all mapped
            "seats_per_departure": 150.0,
            "load_factor": 0.80,
            "_total_departures": 200.0,
        },
    }

    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)
    expected_net_unmapped = (0.2 * 100.0 + 0.0 * 200.0) / (100.0 + 200.0)
    assert abs(result["unmapped_share"] - expected_net_unmapped) <= 1e-9, (
        f"network unmapped_share = {result['unmapped_share']}, "
        f"expected {expected_net_unmapped} (departure-weighted)"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 11. Totals equal sum of per-segment values
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_totals_equal_segment_sums():
    """
    Kind: conservation.
    Tolerance: abs <= 1e-9 relative.
    Reason: total_fuel_kg must equal sum of segment fuel_kg; likewise for
            nox and flights.  Any discrepancy indicates an aggregation bug.
    Cannot catch: per-segment errors that sum to the correct total by accident.
    """
    routes = _make_hub_spoke_routes(100.0, 50.0)
    loads = segment_loads(routes)
    mix = _make_mix_two_aircraft(loads)
    result = network_perf(routes, mix, _fake_perf, _fake_distance_fn)

    segs = result["segments"]
    sum_fuel = sum(s["fuel_kg"] for s in segs.values())
    sum_nox = sum(s["nox_kg"] for s in segs.values())
    sum_flights = sum(s["flights"] for s in segs.values())

    tol = 1e-9
    assert abs(result["total_fuel_kg"] - sum_fuel) <= tol * max(sum_fuel, 1.0), (
        f"total_fuel_kg={result['total_fuel_kg']} != sum of segments={sum_fuel}"
    )
    assert abs(result["total_nox_kg"] - sum_nox) <= tol * max(sum_nox, 1.0), (
        f"total_nox_kg={result['total_nox_kg']} != sum of segments={sum_nox}"
    )
    assert abs(result["total_flights"] - sum_flights) <= tol * max(sum_flights, 1.0), (
        f"total_flights={result['total_flights']} != sum of segments={sum_flights}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 12. Renormalise=True changes the fuel
# ═══════════════════════════════════════════════════════════════════════════


def test_network_perf_renormalise_true_changes_fuel():
    """
    Kind: differential (compare renorm=True vs default=False).
    Tolerance: difference > 1e-6 (large enough to distinguish).
    Reason: when renormalise=True, unmapped share is redistributed to mapped
            aircraft, increasing computed fuel.  The test verifies the option
            changes the result so the parameter is not inert.
    Cannot catch: renorm=True that applies the wrong formula.
    """
    routes = [RouteFlow("BOS", "ORD", ("BOS", "ORD"), 120.0)]
    mix = {("BOS", "ORD"): {
        "shares": {"A": 0.5, "B": 0.3, "C": 0.2},  # C unmapped
        "seats_per_departure": 150.0,
        "load_factor": 0.80,
        "_total_departures": 100.0,
    }}

    res_no_renorm = network_perf(routes, mix, _fake_perf, _fake_distance_fn)
    res_renorm = network_perf(routes, mix, _fake_perf, _fake_distance_fn,
                              renormalise=True)

    assert res_renorm["total_fuel_kg"] > res_no_renorm["total_fuel_kg"] + 1e-6, (
        "renormalise=True should increase fuel_kg vs default (no renorm), "
        f"got renorm={res_renorm['total_fuel_kg']}, no_renorm={res_no_renorm['total_fuel_kg']}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 13. Cache-dependent: P2P on full region demand
# ═══════════════════════════════════════════════════════════════════════════


# Coordinates for the full 6-airport study region (BOS/DEN/ORD/SFO from
# distance.py; DTW and MSP from OurAirports, same source).
_REGION_COORDS: dict[str, tuple[float, float]] = {
    "BOS": (42.3643, -71.0052),
    "DEN": (39.8561, -104.6737),
    "DTW": (42.21377, -83.353786),
    "MSP": (44.880081, -93.221741),
    "ORD": (41.9786, -87.9072),
    "SFO": (37.6188, -122.3750),
}


def _region_distance_fn(a: str, b: str) -> float:
    """Haversine distance for the full 6-airport study region."""
    lat1, lon1 = _REGION_COORDS[a]
    lat2, lon2 = _REGION_COORDS[b]
    return _distance_mod.haversine_nm(lat1, lon1, lat2, lon2)


@pytest.mark.skipif(_CACHE_ABSENT, reason=_CACHE_REASON)
def test_network_perf_real_data_p2p():
    """
    Kind: golden + integration (requires data/bts-cache/).
    Tolerance: no raises (structural), ratio check 0.05 <= computed/T100 <= 5.0.
    Reason: load the 30-pair region demand, build P2P routes, load T-100 Q1 mix,
            run network_perf with a fake_perf.  Reviewer verified all 30
            directional pairs in demand_region_2019Q1.csv appear in T-100;
            this test confirms no missing-segment raise.
    Reports: computed total_flights and T-100 Q1 total departures for the
             region; ratio and explanation of why they differ.
    Cannot catch: errors in the T-100 mix construction that preserve the
                  no-missing-segment property while giving wrong fleet shares.
    """
    from networks import load_demand, build_routes_point_to_point
    from fleet import load_t100, aircraft_mix

    REGION = {"BOS", "DEN", "DTW", "MSP", "ORD", "SFO"}

    # Load demand and build P2P routes
    demand = load_demand(str(_DEMAND_CSV))
    routes = build_routes_point_to_point(demand)

    # Load T-100 Q1 fleet mix
    t100 = load_t100([str(p) for p in _CACHE_T100])
    mix = aircraft_mix(t100, REGION)

    # Verify all P2P segments are in mix — must not raise
    # (this is the structural check the task spec requires)
    loads = segment_loads(routes)
    for seg in loads:
        assert seg in mix, (
            f"P2P segment {seg} not in T-100 mix. Reviewer verified all 30 "
            f"directional pairs exist in T-100; check data freshness."
        )

    # Fake perf: fuel = 5.0 * distance_nm per flight (order-of-magnitude realistic),
    # nox = 0.05 * fuel.  Using a distance-proportional fake so flights scale naturally.
    def fake_perf_for_real(bts_code: str, dist_nm: float) -> dict | None:
        from fleet import BTS_TO_OPENAP
        if BTS_TO_OPENAP.get(bts_code) is None:
            return None
        fuel = 5.0 * dist_nm
        return {"fuel_kg": fuel, "nox_kg": 0.05 * fuel}

    # Use _region_distance_fn which covers all 6 study airports including DTW/MSP.
    result = network_perf(routes, mix, fake_perf_for_real, _region_distance_fn)

    computed_flights = result["total_flights"]

    # T-100 Q1 total departures in region (region-internal only)
    t100_region = t100[t100["ORIGIN"].isin(REGION) & t100["DEST"].isin(REGION)]
    t100_departures = float(t100_region["DEPARTURES_PERFORMED"].sum())

    ratio = computed_flights / t100_departures if t100_departures > 0 else float("nan")

    # The ratio should be in a plausible range: DB1B demand is OD-level
    # (10% ticket sample × 10 = actual OD passengers), while T-100 counts
    # all departures including passengers whose full itinerary goes beyond
    # the region.  So computed_flights (from region-internal OD demand only)
    # should be a fraction of T-100 departures.
    # We assert 0.05 <= ratio <= 5.0 as a sanity bound rather than a tight tolerance.
    assert 0.05 <= ratio <= 5.0, (
        f"Computed flights / T-100 departures = {ratio:.3f}; "
        f"computed={computed_flights:.0f}, T-100={t100_departures:.0f}. "
        "This suggests a systematic error in flights formula."
    )

    print(
        f"\n[real-data] P2P computed flights: {computed_flights:.0f}  "
        f"T-100 Q1 region departures: {t100_departures:.0f}  "
        f"ratio: {ratio:.3f}\n"
        "Ratio <1 expected: DB1B captures OD demand within the 6-airport region; "
        "T-100 counts all departures on region segments including connecting pax "
        "whose itinerary starts or ends outside the region (e.g., BOS→ORD leg "
        "of a BOS→LAX itinerary).  DB1B 10% sample is expanded; T-100 is census."
    )
