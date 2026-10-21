"""
Tests for project/src/trip_compare.py and project/src/precompute_trips.py (NW-28).

Inputs are a tiny SYNTHETIC DB1B + T-100 + airport set under
tests/fixtures/trips/ (see its README) where every answer is known by hand;
perf / range / profile functions are injected fakes (no OpenAP) except in the
single test marked "REAL OPENAP".  Nothing touches the network or the BTS cache,
except tests with a function-level skipif on data/bts-cache/ (absent in CI).

Every test docstring states: KIND, TOLERANCE (and why), CANNOT CATCH.

Regenerating the committed 3-pair fixture (tests/fixtures/trips_fixture.json)
after an intended change:  TRIPS_FIXTURE_REGEN=1 pytest tests/test_trip_compare.py
"""
from __future__ import annotations

import copy
import csv
import json
import math
import os
import pathlib
import random

import pytest

import distance
import fleet
from db1b import SAMPLE_SCALE
import trip_compare as tc
import precompute_trips as pt

_HERE = pathlib.Path(__file__).resolve().parent
FIX = _HERE / "fixtures" / "trips"
TRIPS_FIXTURE = _HERE / "fixtures" / "trips_fixture.json"
REPO = _HERE.parent.parent
COMMITTED = REPO / "data" / "trips_top50.json"
COMMITTED_PROFILES = REPO / "data" / "trip_profiles.json"
CACHE = REPO / "data" / "bts-cache"
HAS_CACHE = (CACHE / "t100_2019_03.csv").exists() and (CACHE / "ourairports_airports.csv").exists()

TOP_N = 4          # fixture airports by departures: ORD 94, BOS 84, SFO 63, DEN 59 (MSP 15 is outside)
ALL_PAIRS = 12     # 4 * 3


# --------------------------------------------------------------------------- fakes
def _factor(code: str) -> float:
    return sum(ord(c) for c in code) / 100.0


class FakePerf:
    """fuel = factor(code) * distance; nox = fuel / 100.  Records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, code, distance_nm):
        self.calls.append((code, distance_nm))
        f = _factor(code) * distance_nm
        return {"fuel_kg": f, "nox_kg": f / 100.0}


def fake_range(code):
    return None if code == "crj9" else 100000.0


def fake_profile(code, distance_nm):
    return {
        "t_s": [0.0, 600.4, 1200.0],
        "alt_ft": [0.0, 35000.2, 0.0],
        "dist_nm": [0.0, distance_nm / 2.0, distance_nm],
    }


@pytest.fixture(scope="module")
def t100():
    return tc.load_t100_data([FIX / "t100_2019_q1.csv"])


@pytest.fixture(scope="module")
def airports(t100):
    ranked, excluded = tc.rank_airports(t100.airport_departures, distance.AIRPORT_COORDS, TOP_N)
    return ranked


@pytest.fixture(scope="module")
def db1b(airports):
    return tc.aggregate_db1b(FIX / "db1b_market.csv", {a for a, _ in airports})


def make_env(t100, db1b, airports, perf=None, range_fn=fake_range, hubs_fallback=None):
    return tc.Env(
        t100=t100,
        db1b=db1b,
        hubs_fallback=list(hubs_fallback) if hubs_fallback is not None else [a for a, _ in airports][:10],
        type_names=tc.load_type_names(FIX / "aircraft_types.csv"),
        perf=perf or FakePerf(),
        range_fn=range_fn,
        profile_fn=fake_profile,
    )


@pytest.fixture()
def env(t100, db1b, airports):
    return make_env(t100, db1b, airports)


def run_fixture(out_dir, perf=None, db1b_path=None, only_pairs=None):
    return pt.run(
        db1b_path=db1b_path or FIX / "db1b_market.csv",
        t100_paths=[FIX / "t100_2019_q1.csv"],
        types_csv=FIX / "aircraft_types.csv",
        ourairports_csv=_HERE / "fixtures" / "airports" / "ourairports_sample.csv",
        out_dir=out_dir,
        top_n=TOP_N,
        perf=perf or FakePerf(),
        range_fn=fake_range,
        profile_fn=fake_profile,
        openap_version="fake-perf-fixture",
        only_pairs=only_pairs,
    )


# --------------------------------------------------------------------------- independent oracle
def oracle_segment(o, d):
    """Plain-csv recomputation of one T-100 directional segment (passenger config only)."""
    deps = seats = pax = 0.0
    by_type = {}
    with open(FIX / "t100_2019_q1.csv", newline="") as f:
        for r in csv.DictReader(f):
            if r["ORIGIN"] == o and r["DEST"] == d and float(r["AIRCRAFT_CONFIG"]) == 1:
                dp = float(r["DEPARTURES_PERFORMED"])
                deps += dp
                seats += float(r["SEATS"])
                pax += float(r["PASSENGERS"])
                by_type[r["AIRCRAFT_TYPE"].zfill(3)] = by_type.get(r["AIRCRAFT_TYPE"].zfill(3), 0.0) + dp
    return {"deps": deps, "spd": seats / deps, "lf": pax / seats, "shares": {k: v / deps for k, v in by_type.items()}}


# --------------------------------------------------------------------------- schema validator (independent of impl)
def _isnum(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _nullable(check):
    return lambda x: x is None or check(x)


def _check(obj, spec, path):
    """spec: dict -> exact key set; list [spec] -> list of; callable -> predicate; type -> isinstance."""
    if isinstance(spec, dict):
        assert isinstance(obj, dict), f"{path}: expected object, got {type(obj).__name__}"
        assert set(obj) == set(spec), f"{path}: keys differ: extra={set(obj)-set(spec)} missing={set(spec)-set(obj)}"
        for k, s in spec.items():
            _check(obj[k], s, f"{path}.{k}")
    elif isinstance(spec, list):
        assert isinstance(obj, list), f"{path}: expected list"
        for i, x in enumerate(obj):
            _check(x, spec[0], f"{path}[{i}]")
    elif isinstance(spec, type):
        assert isinstance(obj, spec) and not (spec is not bool and isinstance(obj, bool)), f"{path}: expected {spec.__name__}, got {obj!r}"
    else:
        assert spec(obj), f"{path}: predicate failed for {obj!r}"


_str = str
AIRCRAFT = {"bts_code": str, "name": str, "openap": _nullable(lambda x: isinstance(x, str)), "share": _isnum,
            "range_km": _nullable(_isnum), "range_ok": _nullable(lambda x: isinstance(x, bool))}
LEG = {"origin": str, "destination": str, "distance_nm": _isnum, "t100_departures": _isnum,
       "seats_per_departure": _isnum, "load_factor": _isnum, "passengers_per_flight": _isnum,
       "aircraft_mix": [AIRCRAFT], "unmapped_share": _isnum, "fuel_kg_per_flight": _isnum,
       "nox_kg_per_flight": _isnum, "fuel_kg_per_passenger": _isnum, "nox_kg_per_passenger": _isnum,
       "profile_key": _nullable(lambda x: isinstance(x, str))}
ROUTE = {"airports": [str], "total_nm": _isnum, "legs": [LEG], "fuel_kg_per_passenger": _isnum,
         "nox_kg_per_passenger": _isnum, "unmapped_share_max": _isnum}
TRIP = {"origin": str, "destination": str, "great_circle_nm": _isnum, "db1b_passengers": _isnum,
        "db1b_nonstop_share": _nullable(_isnum), "nonstop_observed": bool, "point_to_point": ROUTE,
        "point_to_point_is_connecting": bool, "hub_and_spoke": ROUTE,
        "hub": {"iata": str, "rule": lambda x: x in ("db1b_most_used", "fallback_shortest_top10_hub"),
                "share_of_connecting_passengers": _nullable(_isnum),
                "candidates": [{"iata": str, "share": _isnum}]},
        "messages": [str]}
DOC = {"schema_version": int,
       "source": {"quarter": str, "db1b_sha256": str, "t100_sha256": [str], "airports_sha256": str,
                  "openap_version": str, "nonstop_min_departures": int, "notes": [str]},
       "airports": [{"iata": str, "name": str, "departures": _isnum}],
       "hubs_fallback": [str], "pairs": lambda p: isinstance(p, dict)}


def validate_doc(doc):
    _check(doc, DOC, "$")
    assert doc["schema_version"] == 1
    for key, trip in doc["pairs"].items():
        _check(trip, TRIP, f"$.pairs.{key}")
        assert key == f"{trip['origin']}-{trip['destination']}"


def validate_profiles(prof_doc, trips_doc):
    _check(prof_doc, {"schema_version": int, "profiles": lambda p: isinstance(p, dict)}, "$")
    assert prof_doc["schema_version"] == 1
    for k, p in prof_doc["profiles"].items():
        _check(p, {"t_s": [int], "alt_ft": [int], "dist_nm": [_isnum]}, f"$.profiles.{k}")
        assert 2 <= len(p["t_s"]) <= 120
        assert len(p["t_s"]) == len(p["alt_ft"]) == len(p["dist_nm"])
        assert all(round(x, 1) == x for x in p["dist_nm"])
    used = set()
    for trip in trips_doc["pairs"].values():
        for route in (trip["point_to_point"], trip["hub_and_spoke"]):
            for leg in route["legs"]:
                if leg["profile_key"] is not None:
                    used.add(leg["profile_key"])
    assert used == set(prof_doc["profiles"]), "profiles must be exactly the referenced ones"


def routes_of(trip):
    return (trip["point_to_point"], trip["hub_and_spoke"])


# =========================================================================== 1. T-100 / airports
def test_top_airports_by_departures_passenger_config_only(airports):
    """KIND: example (hand-computed from the fixture CSV).
    TOLERANCE: exact (sums of small integers in float; ties handled by code).
    Departures by origin, AIRCRAFT_CONFIG=1 only: ORD 94, BOS 84, SFO 63, DEN 59, MSP 15
    (the 100 config-2 DEN-SFO departures must not count: DEN would be 159).
    CANNOT CATCH: a wrong ranking on the real data (see the cache-gated test)."""
    assert airports == [("ORD", 94.0), ("BOS", 84.0), ("SFO", 63.0), ("DEN", 59.0)]


def test_rank_airports_tie_by_code_and_missing_coords_reported():
    """KIND: example with adversarial ordering.
    TOLERANCE: exact.
    Equal departures must be ordered by code ascending; an airport without
    coordinates is skipped, reported, and the next airport takes its place.
    CANNOT CATCH: ties among >2 airports mixed with exclusions on real data."""
    deps = {"ZZZ": 100.0, "BBB": 50.0, "AAA": 50.0, "CCC": 50.0, "DDD": 10.0}
    coords = {"AAA": (0, 0), "BBB": (0, 1), "CCC": (0, 2), "DDD": (0, 3)}
    ranked, excluded = tc.rank_airports(deps, coords, 3)
    assert ranked == [("AAA", 50.0), ("BBB", 50.0), ("CCC", 50.0)]
    assert excluded == ["ZZZ"]


def test_observed_threshold_boundary(t100):
    """KIND: example, boundary value.
    TOLERANCE: exact.
    NONSTOP_MIN_DEPARTURES is 13 (author's rule): SFO-BOS has exactly 13 deps -> observed;
    BOS-SFO has 12 -> not observed; BOS-DEN 12 -> not observed.
    CANNOT CATCH: off-by-one in a different segment than the ones in the fixture."""
    assert tc.NONSTOP_MIN_DEPARTURES == 13
    assert t100.segment_departures[("SFO", "BOS")] == 13.0
    assert t100.segment_departures[("BOS", "SFO")] == 12.0
    assert tc.is_observed(t100, "SFO", "BOS") is True
    assert tc.is_observed(t100, "BOS", "SFO") is False
    assert tc.is_observed(t100, "BOS", "DEN") is False
    assert tc.is_observed(t100, "NOP", "XYZ") is False


# =========================================================================== 2. DB1B
def test_db1b_aggregation_scaled_and_classified(db1b):
    """KIND: example (hand-computed from fixture rows).
    TOLERANCE: exact (integer-valued floats).
    Raw BOS->SFO rows: nonstop 4; one-stop ORD 3+2, DEN 8, CLE 6+6, MSP 2; two-stop 1 -> total 32 raw.
    All values are x SAMPLE_SCALE.  The anomalous row (BOS->DEN with group BOS:ORD) is
    counted in `anomalies` and ignored everywhere else.  Pairs outside the airport set
    (LAX->SFO, BOS->PHX) are not stored (memory).
    CANNOT CATCH: wrong handling of rows with 3+ stops other than total (not hub candidates)."""
    s = SAMPLE_SCALE
    assert db1b.total[("BOS", "SFO")] == 32 * s
    assert db1b.nonstop[("BOS", "SFO")] == 4 * s
    assert db1b.onestop[("BOS", "SFO")] == {"ORD": 5 * s, "DEN": 8 * s, "CLE": 12 * s, "MSP": 2 * s}
    assert db1b.onestop[("SFO", "BOS")] == {"ORD": 5 * s, "DEN": 5 * s}
    assert ("BOS", "DEN") not in db1b.total
    assert db1b.anomalies == 1
    assert ("LAX", "SFO") not in db1b.total and ("BOS", "PHX") not in db1b.total


def test_db1b_streams_file_exactly_once(monkeypatch, airports):
    """KIND: property (resource use) via spy.
    TOLERANCE: exact count.
    The spec requires ONE pass over the 1.6 GB DB1B file.
    CANNOT CATCH: reading the file twice through a different opener (spy wraps db1b._open_csv_source only)."""
    import db1b as db1b_mod
    calls = []
    real = db1b_mod._open_csv_source

    def spy(p):
        calls.append(str(p))
        return real(p)

    monkeypatch.setattr(db1b_mod, "_open_csv_source", spy)
    tc.aggregate_db1b(FIX / "db1b_market.csv", {a for a, _ in airports})
    assert len(calls) == 1


def test_db1b_row_order_invariance(tmp_path, airports):
    """KIND: metamorphic (permute input rows).
    TOLERANCE: exact.
    Aggregation is a commutative sum, so shuffling rows must not change any aggregate.
    CANNOT CATCH: bugs that are symmetric under row permutation (e.g. wrong scale)."""
    lines = (FIX / "db1b_market.csv").read_text().splitlines()
    head, rows = lines[0], lines[1:]
    random.Random(7).shuffle(rows)
    p = tmp_path / "shuffled.csv"
    p.write_text("\n".join([head] + rows) + "\n")
    a = tc.aggregate_db1b(FIX / "db1b_market.csv", {x for x, _ in airports})
    b = tc.aggregate_db1b(p, {x for x, _ in airports})
    assert (a.total, a.nonstop, a.onestop, a.anomalies) == (b.total, b.nonstop, b.onestop, b.anomalies)


# =========================================================================== 3. hub selection
def test_hub_is_most_used_among_qualified(env):
    """KIND: example, hand-computed.
    TOLERANCE: share abs 1e-12 (ratio of exact integer sums).
    DEN->ORD one-stop pax (raw): BOS 6, SFO 4 -> hub BOS, share 6/10, candidates [BOS .6, SFO .4]
    (both qualify: DEN-BOS 14 deps, BOS-ORD 45, DEN-SFO 20, SFO-ORD 30).
    CANNOT CATCH: ordering bugs that only show with >2 candidates (see skip test)."""
    t = tc.build_trip(env, "DEN", "ORD")
    assert t["hub"]["iata"] == "BOS" and t["hub"]["rule"] == "db1b_most_used"
    assert t["hub"]["share_of_connecting_passengers"] == pytest.approx(0.6, abs=1e-12)
    assert [(c["iata"], round(c["share"], 9)) for c in t["hub"]["candidates"]] == [("BOS", 0.6), ("SFO", 0.4)]
    assert t["hub_and_spoke"]["airports"] == ["DEN", "BOS", "ORD"]


def test_hub_tie_broken_by_code(env):
    """KIND: example, adversarial tie.
    TOLERANCE: exact.
    SFO->BOS one-stop pax: ORD 5 = DEN 5 (both legs observed for both) -> alphabetical: DEN.
    CANNOT CATCH: ties in the fallback rule (covered separately)."""
    t = tc.build_trip(env, "SFO", "BOS")
    assert t["hub"]["iata"] == "DEN"
    assert [c["iata"] for c in t["hub"]["candidates"]] == ["DEN", "ORD"]
    assert t["hub"]["share_of_connecting_passengers"] == pytest.approx(0.5, abs=1e-12)


def test_hub_choice_invariant_to_db1b_row_order(tmp_path, t100, airports):
    """KIND: metamorphic (permute DB1B rows, 5 seeds).
    TOLERANCE: exact.
    The hub (including the tie-break) must not depend on the order rows were read.
    CANNOT CATCH: dependence on dict ordering that happens to be stable across these seeds."""
    base = None
    lines = (FIX / "db1b_market.csv").read_text().splitlines()
    for seed in range(5):
        rows = lines[1:]
        random.Random(seed).shuffle(rows)
        p = tmp_path / f"s{seed}.csv"
        p.write_text("\n".join([lines[0]] + rows) + "\n")
        agg = tc.aggregate_db1b(p, {a for a, _ in airports})
        e = make_env(t100, agg, airports)
        got = {k: tc.build_trip(e, *k)["hub"] for k in [("SFO", "BOS"), ("BOS", "SFO"), ("DEN", "ORD")]}
        base = base or got
        assert got == base


def test_unobserved_leg_excludes_candidate_even_if_most_used(env):
    """KIND: example, hand-computed, adversarial.
    TOLERANCE: share abs 1e-12.
    BOS->SFO raw one-stop pax: CLE 12 (no T-100 segment at all), DEN 8 (BOS-DEN has 12 < 13 deps),
    ORD 5, MSP 2 (MSP is NOT in the top-4 airport set - hubs may be any airport).
    Winner must be ORD; share is of ALL one-stop pax: 5/27; candidates (qualified only) ORD, MSP;
    a message names the skipped heavier connections.
    CANNOT CATCH: a skip for a reason other than unobserved legs."""
    t = tc.build_trip(env, "BOS", "SFO")
    assert t["hub"]["iata"] == "ORD" and t["hub"]["rule"] == "db1b_most_used"
    assert t["hub"]["share_of_connecting_passengers"] == pytest.approx(5 / 27, abs=1e-12)
    assert [c["iata"] for c in t["hub"]["candidates"]] == ["ORD", "MSP"]
    assert t["hub"]["candidates"][1]["share"] == pytest.approx(2 / 27, abs=1e-12)
    joined = " ".join(t["messages"])
    assert "CLE" in joined and "DEN" in joined


def test_hub_candidates_capped_at_five(t100, airports):
    """KIND: example with synthetic DB1B aggregate.
    TOLERANCE: exact.
    Seven qualifying intermediates -> only the top five by passengers are listed.
    CANNOT CATCH: cap applied before the qualification filter (all 7 qualify here)."""
    hubs = ["ORD", "DEN", "BOS", "SFO", "MSP"]
    agg = tc.Db1bAgg(total={("DEN", "BOS"): 1000.0}, nonstop={}, onestop={("DEN", "BOS"): {}}, anomalies=0)
    # only ORD, SFO, MSP have both legs observed with DEN/BOS endpoints in the fixture? build a custom T100
    seg = {}
    mix = {}
    for h in ["H1", "H2", "H3", "H4", "H5", "H6", "H7"]:
        for s in [("DEN", h), (h, "BOS")]:
            seg[s] = 50.0
            mix[s] = {"shares": {"614": 1.0}, "seats_per_departure": 150.0, "load_factor": 0.8, "_total_departures": 50.0}
    t = tc.T100Data(airport_departures={}, segment_departures=seg, mix=mix)
    agg.onestop[("DEN", "BOS")] = {f"H{i}": 10.0 * i for i in range(1, 8)}
    coords = {a: (30.0 + i, -100.0 + i) for i, a in enumerate(["DEN", "BOS", "H1", "H2", "H3", "H4", "H5", "H6", "H7"])}
    e = tc.Env(t100=t, db1b=agg, hubs_fallback=[], type_names={}, perf=FakePerf(), range_fn=fake_range,
               profile_fn=fake_profile, coords=coords)
    trip = tc.build_trip(e, "DEN", "BOS")
    assert [c["iata"] for c in trip["hub"]["candidates"]] == ["H7", "H6", "H5", "H4", "H3"]
    assert trip["hub"]["iata"] == "H7"


def test_zero_passenger_leg_is_not_a_usable_hub_candidate(airports):
    """KIND: example, adversarial (real T-100 has observed segments with 0 passengers or 0 seats,
    which would make passengers_per_flight 0 and per-passenger fuel infinite).
    TOLERANCE: exact.
    H1 (most DB1B passengers) has a leg with >= 13 departures but load factor 0 -> must be skipped in
    favour of H2, with a message; no division by zero anywhere.
    CANNOT CATCH: zero-seat legs that are the nonstop itself (none exist among the top-50 pairs; the
    cache-gated full run reports it)."""
    seg, mix = {}, {}
    for h, lf in (("H1", 0.0), ("H2", 0.8)):
        for s in [("DEN", h), (h, "BOS")]:
            seg[s] = 50.0
            mix[s] = {"shares": {"614": 1.0}, "seats_per_departure": 150.0,
                      "load_factor": lf if s[0] == "DEN" else 0.8, "_total_departures": 50.0}
    t = tc.T100Data(airport_departures={}, segment_departures=seg, mix=mix)
    agg = tc.Db1bAgg(total={("DEN", "BOS"): 100.0}, nonstop={}, onestop={("DEN", "BOS"): {"H1": 60.0, "H2": 40.0}}, anomalies=0)
    coords = {"DEN": (39.0, -104.0), "BOS": (42.0, -71.0), "H1": (40.0, -90.0), "H2": (41.0, -85.0)}
    e = tc.Env(t100=t, db1b=agg, hubs_fallback=[], type_names={}, perf=FakePerf(), range_fn=fake_range,
               profile_fn=fake_profile, coords=coords)
    trip = tc.build_trip(e, "DEN", "BOS")
    assert trip["hub"]["iata"] == "H2" and [c["iata"] for c in trip["hub"]["candidates"]] == ["H2"]
    assert trip["hub"]["share_of_connecting_passengers"] == pytest.approx(0.4, abs=1e-12)
    assert "H1" in " ".join(trip["messages"])
    assert math.isfinite(trip["hub_and_spoke"]["fuel_kg_per_passenger"])


def test_fallback_hub_shortest_with_both_legs_observed(env):
    """KIND: example, hand-computed.
    TOLERANCE: distance abs 1e-9 nm (same function).
    ORD->DEN has no DB1B one-stop rows.  Fallback candidates (top-10 = the 4 fixture airports
    minus endpoints): BOS (ORD-BOS 40 but BOS-DEN 12 -> not observed) and SFO (ORD-SFO 30, SFO-DEN 20 ok).
    -> SFO, rule 'fallback_shortest_top10_hub', share None, candidates [], message present.
    CANNOT CATCH: shortest-distance selection among >1 valid fallbacks (see next test)."""
    t = tc.build_trip(env, "ORD", "DEN")
    assert t["hub"]["iata"] == "SFO"
    assert t["hub"]["rule"] == "fallback_shortest_top10_hub"
    assert t["hub"]["share_of_connecting_passengers"] is None
    assert t["hub"]["candidates"] == []
    assert any("fallback" in m.lower() for m in t["messages"])
    hs = t["hub_and_spoke"]
    assert hs["airports"] == ["ORD", "SFO", "DEN"]
    assert hs["total_nm"] == pytest.approx(distance.distance_nm("ORD", "SFO") + distance.distance_nm("SFO", "DEN"), abs=1e-9)


def test_fallback_picks_shortest_total_distance_and_ties_by_code(t100, airports):
    """KIND: example + metamorphic (permuting the order of the fallback list).
    TOLERANCE: exact choice.
    DEN->BOS with no DB1B rows at all: valid fallbacks are ORD (DEN-ORD 25, ORD-BOS 40) and SFO
    (DEN-SFO 20, SFO-BOS 13).  Total great-circle: via ORD < via SFO -> ORD, whatever the list order.
    CANNOT CATCH: exact-distance ties (needs identical coordinates)."""
    empty = tc.Db1bAgg(total={}, nonstop={}, onestop={}, anomalies=0)
    d_ord = distance.distance_nm("DEN", "ORD") + distance.distance_nm("ORD", "BOS")
    d_sfo = distance.distance_nm("DEN", "SFO") + distance.distance_nm("SFO", "BOS")
    assert d_ord < d_sfo
    for order in (["ORD", "SFO"], ["SFO", "ORD"], ["BOS", "DEN", "SFO", "ORD"]):
        e = make_env(t100, empty, airports, hubs_fallback=order)
        assert tc.build_trip(e, "DEN", "BOS")["hub"]["iata"] == "ORD"


def test_fallback_with_no_observed_pair_keeps_trip_with_empty_leg(t100, airports):
    """KIND: example, error path (explicit author rule: keep the pair, do not drop it).
    TOLERANCE: exact.
    ORD->DEN with fallback list ['MSP'] (ORD-MSP never flown): hub MSP is used anyway;
    both unobserved legs have aircraft_mix [], unmapped_share 1.0, fuel 0.0, departures 0, profile_key None,
    and a message says the leg is not observed.  Fuel is 0.0, never null/NaN.
    CANNOT CATCH: a mix of one observed and one unobserved leg (MSP-DEN is also unobserved here)."""
    empty = tc.Db1bAgg(total={}, nonstop={}, onestop={}, anomalies=0)
    e = make_env(t100, empty, airports, hubs_fallback=["MSP"])
    t = tc.build_trip(e, "ORD", "DEN")
    assert t["hub"]["iata"] == "MSP" and t["hub"]["rule"] == "fallback_shortest_top10_hub"
    for leg in t["hub_and_spoke"]["legs"]:
        assert leg["aircraft_mix"] == [] and leg["unmapped_share"] == 1.0
        assert leg["fuel_kg_per_flight"] == 0.0 and leg["fuel_kg_per_passenger"] == 0.0
        assert leg["nox_kg_per_passenger"] == 0.0 and leg["t100_departures"] == 0.0
        assert leg["profile_key"] is None
    assert t["hub_and_spoke"]["fuel_kg_per_passenger"] == 0.0
    assert any("not observed" in m for m in t["messages"])
    assert tc.leg_is_unobserved(t["hub_and_spoke"]["legs"][0])


# =========================================================================== 4. ADR 0007 strictness
def test_adr0007_strict_p2p_is_exact_copy_when_no_nonstop(env):
    """KIND: example + aliasing check.
    TOLERANCE: exact equality of the whole ROUTE dict.
    BOS->SFO has 12 deps (<13): point_to_point must equal hub_and_spoke exactly, flag connecting,
    message says so, and must be a distinct object (no shared mutable state).
    CANNOT CATCH: a copy that is equal today but diverges when only one route is post-processed."""
    t = tc.build_trip(env, "BOS", "SFO")
    assert t["nonstop_observed"] is False
    assert t["point_to_point_is_connecting"] is True
    assert t["point_to_point"] == t["hub_and_spoke"]
    assert t["point_to_point"] is not t["hub_and_spoke"]
    assert t["point_to_point"]["legs"][0] is not t["hub_and_spoke"]["legs"][0]
    assert any("no observed nonstop" in m.lower() for m in t["messages"])


def test_adr0007_observed_nonstop_is_single_leg_route(env):
    """KIND: example, hand-computed.
    TOLERANCE: abs 1e-9 nm (same distance function).
    SFO->BOS (13 deps) is observed: P2P = [SFO, BOS] single leg with total_nm == great circle,
    not connecting, and differs from the hub route.
    CANNOT CATCH: nonstop route built from a different mix than that segment's."""
    t = tc.build_trip(env, "SFO", "BOS")
    assert t["nonstop_observed"] is True and t["point_to_point_is_connecting"] is False
    p = t["point_to_point"]
    assert p["airports"] == ["SFO", "BOS"] and len(p["legs"]) == 1
    assert p["total_nm"] == pytest.approx(distance.distance_nm("SFO", "BOS"), abs=1e-9)
    assert t["great_circle_nm"] == pytest.approx(p["total_nm"], abs=1e-9)
    assert p != t["hub_and_spoke"] and len(t["hub_and_spoke"]["legs"]) == 2


def test_p2p_flag_matches_observation_for_all_pairs(t100, db1b, airports):
    """KIND: property over all 12 fixture pairs.
    TOLERANCE: exact.
    nonstop_observed <=> segment has >= 13 departures; connecting flag is its negation; when
    connecting P2P == H&S, else P2P has exactly the 2 airports.
    CANNOT CATCH: properties of pairs not in the fixture."""
    e = make_env(t100, db1b, airports)
    trips = tc.build_all_pairs(e, [a for a, _ in airports])
    assert len(trips) == ALL_PAIRS
    for key, t in trips.items():
        o, d = key.split("-")
        obs = t100.segment_departures.get((o, d), 0.0) >= 13
        assert t["nonstop_observed"] is obs
        assert t["point_to_point_is_connecting"] is (not obs)
        if obs:
            assert t["point_to_point"]["airports"] == [o, d]
        else:
            assert t["point_to_point"] == t["hub_and_spoke"]
        hub = t["hub_and_spoke"]["airports"]
        assert len(hub) == 3 and hub[0] == o and hub[2] == d and hub[1] not in (o, d)


# =========================================================================== 5. leg physics / fuel
def test_leg_mix_matches_independent_oracle(env):
    """KIND: differential (implementation vs plain-csv recomputation).
    TOLERANCE: rel 1e-12 (same arithmetic, different order of summation).
    BOS-ORD: 30 deps 737-800 + 15 deps A319 -> shares 2/3, 1/3; seats/dep = 6720/45;
    LF = 5856/6720.  Mix sorted by share desc; names from the L_AIRCRAFT_TYPE lookup;
    openap codes from fleet.BTS_TO_OPENAP.
    CANNOT CATCH: errors common to both implementations' reading of the T-100 semantics."""
    t = tc.build_trip(env, "SFO", "BOS")    # hub DEN: legs SFO-DEN, DEN-BOS ; also check BOS-ORD below
    leg, _ = tc.build_leg(env, "BOS", "ORD")
    o = oracle_segment("BOS", "ORD")
    assert leg["t100_departures"] == o["deps"] == 45.0
    assert leg["seats_per_departure"] == pytest.approx(6720 / 45, rel=1e-12)
    assert leg["load_factor"] == pytest.approx(5856 / 6720, rel=1e-12)
    assert leg["passengers_per_flight"] == pytest.approx(o["spd"] * o["lf"], rel=1e-12)
    got = [(a["bts_code"], a["share"]) for a in leg["aircraft_mix"]]
    assert [c for c, _ in got] == ["614", "698"]
    for code, share in got:
        assert share == pytest.approx(o["shares"][code], rel=1e-12)
    names = {a["bts_code"]: a["name"] for a in leg["aircraft_mix"]}
    assert names == {"614": "Boeing 737-800", "698": "Airbus Industrie A319"}
    for a in leg["aircraft_mix"]:
        assert a["openap"] == fleet.BTS_TO_OPENAP[a["bts_code"]]
    assert leg["distance_nm"] == pytest.approx(distance.distance_nm("BOS", "ORD"), abs=1e-9)
    assert t["origin"] == "SFO"


def test_leg_fuel_equals_independent_departure_weighted_sum(t100, db1b, airports):
    """KIND: differential (impl vs independent recomputation using the same injected perf function).
    TOLERANCE: rel 1e-12 (floating-point reassociation only).
    fuel_kg_per_flight == sum over MAPPED types of share * perf(openap, distance rounded to 10 nm);
    NOx likewise.  The perf is evaluated at the 10-nm-rounded distance (documented cache granularity).
    CANNOT CATCH: a wrong perf function (it is fake here; see the REAL OPENAP test)."""
    perf = FakePerf()
    e = make_env(t100, db1b, airports, perf=perf)
    leg, _ = tc.build_leg(e, "BOS", "ORD")
    dist = distance.distance_nm("BOS", "ORD")
    dr = max(10, round(dist / 10.0) * 10)
    o = oracle_segment("BOS", "ORD")
    exp_fuel = sum(s * _factor(fleet.BTS_TO_OPENAP[c]) * dr for c, s in o["shares"].items() if fleet.BTS_TO_OPENAP[c])
    exp_nox = exp_fuel / 100.0
    assert leg["fuel_kg_per_flight"] == pytest.approx(exp_fuel, rel=1e-12)
    assert leg["nox_kg_per_flight"] == pytest.approx(exp_nox, rel=1e-12)
    assert {c[1] for c in perf.calls} == {dr}


def test_unmapped_share_excluded_not_rescaled(t100, db1b, airports):
    """KIND: example, hand-computed + metamorphic.
    TOLERANCE: rel 1e-12.
    DEN-ORD: 20 deps 737-800 + 5 deps PC-12 (BTS 479, no OpenAP model) -> unmapped_share 0.2 and
    fuel_per_flight = 0.8 * perf(737-800) (NOT divided by 0.8).  The unmapped type still appears in
    aircraft_mix with openap None, range_km None, range_ok None.
    CANNOT CATCH: dropping the unmapped type from the mix list (checked via the mix assertions)."""
    assert fleet.BTS_TO_OPENAP["479"] is None, "fixture assumes PC-12 stays unmapped"
    e = make_env(t100, db1b, airports)
    leg, _ = tc.build_leg(e, "DEN", "ORD")
    dist = distance.distance_nm("DEN", "ORD")
    dr = max(10, round(dist / 10.0) * 10)
    full = _factor(fleet.BTS_TO_OPENAP["614"]) * dr
    assert leg["unmapped_share"] == pytest.approx(0.2, rel=1e-12)
    assert leg["fuel_kg_per_flight"] == pytest.approx(0.8 * full, rel=1e-12)
    pc12 = next(a for a in leg["aircraft_mix"] if a["bts_code"] == "479")
    assert pc12["openap"] is None and pc12["range_km"] is None and pc12["range_ok"] is None
    assert sum(a["share"] for a in leg["aircraft_mix"]) == pytest.approx(1.0, abs=1e-12)
    assert leg["profile_key"] == f"{fleet.BTS_TO_OPENAP['614']}-{round(dist / 50) * 50}"


def test_per_passenger_times_passengers_per_flight_is_per_flight(t100, db1b, airports):
    """KIND: property over every leg of every fixture pair (RAW, unrounded values).
    TOLERANCE: rel 1e-9 (the author's stated tolerance; true error ~1e-16).
    fuel_kg_per_passenger * passengers_per_flight == fuel_kg_per_flight (same for NOx),
    and passengers_per_flight == seats_per_departure * load_factor.
    CANNOT CATCH: a wrong per_flight that is consistently divided."""
    e = make_env(t100, db1b, airports)
    trips = tc.build_all_pairs(e, [a for a, _ in airports])
    n = 0
    for t in trips.values():
        for r in routes_of(t):
            for leg in r["legs"]:
                if not leg["aircraft_mix"]:
                    continue
                n += 1
                assert leg["passengers_per_flight"] == pytest.approx(leg["seats_per_departure"] * leg["load_factor"], rel=1e-9)
                assert leg["fuel_kg_per_passenger"] * leg["passengers_per_flight"] == pytest.approx(leg["fuel_kg_per_flight"], rel=1e-9)
                assert leg["nox_kg_per_passenger"] * leg["passengers_per_flight"] == pytest.approx(leg["nox_kg_per_flight"], rel=1e-9)
    assert n > 20


def test_route_totals_are_sums_of_legs(t100, db1b, airports):
    """KIND: property over all fixture pairs, both structures (RAW values).
    TOLERANCE: rel 1e-12 (float summation order).
    route fuel/NOx per passenger == sum of leg values; total_nm == sum of leg distances;
    unmapped_share_max == max leg unmapped_share; airports chain consistent with legs.
    CANNOT CATCH: legs that are individually wrong."""
    e = make_env(t100, db1b, airports)
    for t in tc.build_all_pairs(e, [a for a, _ in airports]).values():
        for r in routes_of(t):
            legs = r["legs"]
            assert r["fuel_kg_per_passenger"] == pytest.approx(sum(l["fuel_kg_per_passenger"] for l in legs), rel=1e-12)
            assert r["nox_kg_per_passenger"] == pytest.approx(sum(l["nox_kg_per_passenger"] for l in legs), rel=1e-12)
            assert r["total_nm"] == pytest.approx(sum(l["distance_nm"] for l in legs), rel=1e-12)
            assert r["unmapped_share_max"] == max(l["unmapped_share"] for l in legs)
            assert r["airports"] == [legs[0]["origin"]] + [l["destination"] for l in legs]
            assert all(a["destination"] == b["origin"] for a, b in zip(legs, legs[1:]))


def test_perf_called_once_per_type_and_rounded_distance(t100, db1b, airports):
    """KIND: property (cache) via spy.
    TOLERANCE: exact.
    Across all 12 pairs no (openap code, distance) is evaluated twice, and every evaluated distance is a
    multiple of 10 nm (the documented cache granularity).
    CANNOT CATCH: a cache keyed too coarsely (results would still be internally consistent)."""
    perf = FakePerf()
    e = make_env(t100, db1b, airports, perf=perf)
    tc.build_all_pairs(e, [a for a, _ in airports])
    assert len(perf.calls) == len(set(perf.calls)) > 0
    assert all(d % 10 == 0 and d >= 10 for _, d in perf.calls)


# =========================================================================== 6. range / profiles
def test_range_ok_flags_and_boundary(t100, db1b, airports):
    """KIND: example with injected range function, boundary value.
    TOLERANCE: exact comparison range_km >= distance_nm * 1.852.
    A range just under the leg length -> False; exactly equal -> True; range unknown (None) -> None.
    CANNOT CATCH: a wrong openap range value (see the REAL OPENAP test)."""
    dist = distance.distance_nm("BOS", "ORD")
    need = dist * 1.852
    for rng, expect in [(need - 1.0, False), (need, True), (need + 1.0, True), (None, None)]:
        e = make_env(t100, db1b, airports, range_fn=lambda c, r=rng: r)
        leg, _ = tc.build_leg(e, "BOS", "ORD")
        for a in leg["aircraft_mix"]:
            assert a["range_ok"] is expect, (rng, a)
            assert a["range_km"] == rng


def test_range_failure_reported_in_trip_messages(t100, db1b, airports):
    """KIND: example (error reporting).
    TOLERANCE: n/a.
    When a type cannot fly an observed leg, the trip's messages say so and name the type.
    CANNOT CATCH: whether the message wording is helpful to a reader."""
    e = make_env(t100, db1b, airports, range_fn=lambda c: 10.0)
    t = tc.build_trip(e, "SFO", "BOS")
    assert any("range" in m.lower() for m in t["messages"])
    assert not any("range" in m.lower() for m in tc.build_trip(make_env(t100, db1b, airports), "SFO", "BOS")["messages"])


def test_profile_keys_and_decimation(t100, db1b, airports):
    """KIND: property over all fixture pairs.
    TOLERANCE: exact.
    Every leg with a mapped type has profile_key '<openap of top mapped type>-<distance rounded to
    50 nm>'; env.profiles holds exactly the referenced keys; unmapped-only/empty legs have None.
    decimate_profile caps at 120 points, keeps first and last sample, ints for t/alt, dist 1 decimal.
    CANNOT CATCH: profile realism (fake profile here)."""
    e = make_env(t100, db1b, airports)
    trips = tc.build_all_pairs(e, [a for a, _ in airports])
    used = set()
    for t in trips.values():
        for r in routes_of(t):
            for leg in r["legs"]:
                mapped = [a for a in leg["aircraft_mix"] if a["openap"]]
                if not mapped:
                    assert leg["profile_key"] is None
                    continue
                top = sorted(mapped, key=lambda a: (-a["share"], a["bts_code"]))[0]
                expect = f"{top['openap']}-{max(50, round(leg['distance_nm'] / 50) * 50)}"
                assert leg["profile_key"] == expect
                used.add(expect)
    assert used == set(e.profiles)
    n = 1000
    prof = tc.decimate_profile({"t_s": [i * 10.0 for i in range(n)], "alt_ft": [i * 3.3 for i in range(n)],
                                "dist_nm": [i * 0.123456 for i in range(n)]})
    assert 2 <= len(prof["t_s"]) <= 120
    assert prof["t_s"][0] == 0 and prof["t_s"][-1] == (n - 1) * 10
    assert all(type(x) is int for x in prof["t_s"] + prof["alt_ft"])
    assert prof["dist_nm"][-1] == round((n - 1) * 0.123456, 1)
    short = tc.decimate_profile({"t_s": [0.0, 10.0, 20.0], "alt_ft": [0.0, 1.0, 2.0], "dist_nm": [0.0, 1.0, 2.0]})
    assert short["t_s"] == [0, 10, 20]


# =========================================================================== 7. schema / determinism / pipeline
def test_fixture_run_schema_and_profiles(tmp_path):
    """KIND: property (schema), full pipeline on the synthetic inputs.
    TOLERANCE: n/a (types).
    Every field of the published contract is present with the right type, no extra fields, no NaN/Infinity
    (json.loads with parse_constant raising), keys sorted, 12 ordered pairs, airports sorted by departures
    desc, hubs_fallback = top airports, source.nonstop_min_departures == 13, sha256 hex strings, no abs paths.
    CANNOT CATCH: semantic errors (other tests); fields the contract omits."""
    rep = run_fixture(tmp_path)
    text = (tmp_path / "trips_top50.json").read_text()

    def boom(c):
        raise AssertionError(f"non-finite constant {c} in output")

    doc = json.loads(text, parse_constant=boom)
    validate_doc(doc)
    validate_profiles(json.loads((tmp_path / "trip_profiles.json").read_text(), parse_constant=boom), doc)
    assert len(doc["pairs"]) == ALL_PAIRS
    assert [a["iata"] for a in doc["airports"]] == ["ORD", "BOS", "SFO", "DEN"]
    deps = [a["departures"] for a in doc["airports"]]
    assert deps == sorted(deps, reverse=True)
    assert doc["hubs_fallback"] == ["ORD", "BOS", "SFO", "DEN"]
    assert doc["source"]["quarter"] == "2019Q1" and doc["source"]["nonstop_min_departures"] == 13
    assert all(len(h) == 64 for h in [doc["source"]["db1b_sha256"], doc["source"]["airports_sha256"]] + doc["source"]["t100_sha256"])
    assert str(REPO) not in text and "/Users/" not in text and "/tmp/" not in text
    assert list(doc["pairs"]) == sorted(doc["pairs"])
    assert text == json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert rep["n_pairs"] == ALL_PAIRS


def test_two_runs_byte_identical(tmp_path):
    """KIND: property (determinism).
    TOLERANCE: byte equality.
    Two independent runs produce identical bytes for both files; so does a run on row-shuffled DB1B input
    (aggregation order must not leak into the output).
    CANNOT CATCH: nondeterminism that only appears with the real OpenAP (threading, library versions)."""
    a, b, c = tmp_path / "a", tmp_path / "b", tmp_path / "c"
    run_fixture(a)
    run_fixture(b)
    lines = (FIX / "db1b_market.csv").read_text().splitlines()
    rows = lines[1:]
    random.Random(3).shuffle(rows)
    shuffled = tmp_path / "shuf.csv"
    shuffled.write_text("\n".join([lines[0]] + rows) + "\n")
    # shuffled has a different sha in source; compare everything except that field
    run_fixture(c, db1b_path=shuffled)
    for name in ("trips_top50.json", "trip_profiles.json"):
        assert (a / name).read_bytes() == (b / name).read_bytes()
    da = json.loads((a / "trips_top50.json").read_text())
    dc = json.loads((c / "trips_top50.json").read_text())
    da["source"].pop("db1b_sha256"), dc["source"].pop("db1b_sha256")
    assert da == dc
    assert (a / "trip_profiles.json").read_bytes() == (c / "trip_profiles.json").read_bytes()


def test_rounding_policy_and_consistency(tmp_path):
    """KIND: property on written output.
    TOLERANCE: per-PASSENGER kg are rounded to 4 decimals (NOx per passenger is ~0.1-2 kg, so 4 dp keeps
    >= 3 significant figures), shares/load factors to 5, every other float to 2.
    A route per-passenger total equals the sum of its (rounded) legs within 0.5e-4 * (#legs + 1).
    Decimal places are checked exactly.
    CANNOT CATCH: loss of precision that matters downstream (3 dp of kg is <1e-6 relative for real values)."""
    run_fixture(tmp_path)
    doc = json.loads((tmp_path / "trips_top50.json").read_text())
    for t in doc["pairs"].values():
        for r in routes_of(t):
            assert round(r["fuel_kg_per_passenger"], 4) == r["fuel_kg_per_passenger"]
            assert round(r["nox_kg_per_passenger"], 4) == r["nox_kg_per_passenger"]
            for l in r["legs"]:
                assert round(l["fuel_kg_per_flight"], 2) == l["fuel_kg_per_flight"]
                assert round(l["fuel_kg_per_passenger"], 4) == l["fuel_kg_per_passenger"]
                assert round(l["load_factor"], 5) == l["load_factor"]
                assert round(l["unmapped_share"], 5) == l["unmapped_share"]
            tol = 0.5e-4 * (len(r["legs"]) + 1)
            assert abs(r["fuel_kg_per_passenger"] - sum(l["fuel_kg_per_passenger"] for l in r["legs"])) <= tol + 1e-9
        assert 0 < max(l["load_factor"] for r in routes_of(t) for l in r["legs"]) <= 1.0


def test_committed_fixture_matches_regeneration(tmp_path):
    """KIND: golden (hand-inspectable 3-pair file shared with the page agent).
    TOLERANCE: byte equality (deterministic fake perf, no floats from OpenAP).
    tests/fixtures/trips_fixture.json must equal what the pipeline writes for the pairs
    BOS-SFO (no nonstop, DB1B hub), SFO-BOS (boundary nonstop, tie-broken hub), DEN-ORD (unmapped share)
    and validate against the contract.  Set TRIPS_FIXTURE_REGEN=1 to rewrite it after an intended change.
    CANNOT CATCH: whether the fixture is a good stand-in for the real file's value ranges."""
    run_fixture(tmp_path, only_pairs=["BOS-SFO", "SFO-BOS", "DEN-ORD"])
    new = (tmp_path / "trips_top50.json").read_text()
    if os.environ.get("TRIPS_FIXTURE_REGEN") == "1":
        TRIPS_FIXTURE.write_text(new)
    committed = TRIPS_FIXTURE.read_text()
    assert committed == new
    doc = json.loads(committed)
    validate_doc(doc)
    assert sorted(doc["pairs"]) == ["BOS-SFO", "DEN-ORD", "SFO-BOS"]
    assert doc["pairs"]["BOS-SFO"]["hub"]["iata"] == "ORD"
    assert doc["pairs"]["SFO-BOS"]["hub"]["iata"] == "DEN"
    assert doc["pairs"]["DEN-ORD"]["hub"]["iata"] == "BOS"


def test_cli_parser_defaults_and_flags():
    """KIND: example.
    TOLERANCE: n/a.
    `python -m precompute_trips --out-dir DIR` is the documented invocation; parser exposes out-dir
    with the other inputs defaulting to the repo cache.
    CANNOT CATCH: behaviour of main() (needs the real cache; exercised by the manual full run)."""
    args = pt.build_parser().parse_args(["--out-dir", "somewhere"])
    assert args.out_dir == "somewhere"
    assert args.top_n == 50


def test_trajectory_for_distance_matches_fuel_trajectory():
    """KIND: differential (public helper vs the internals flight_fuel_nox uses), real OpenAP generator
    (cheap: one a320 trajectory, no fuel model).
    TOLERANCE: exact equality of the arrays (deterministic generator, random=False).
    openap_perf.trajectory_for_distance(ac, d) must equal _get_trajectory(ac, d*1852 - climb - descent),
    and its total distance must be within 1% of d (the module's documented accuracy).
    CANNOT CATCH: synonym-path trajectories (a320 has a native WRAP model)."""
    import numpy as np
    import openap_perf as op
    df = op.trajectory_for_distance("a320", 500.0)
    cl, de = op._climb_descent_distances("a320")
    ref = op._get_trajectory("a320", 500.0 * 1852.0 - cl - de)
    assert np.array_equal(df["altitude"].values, ref["altitude"].values)
    assert np.array_equal(df["t"].values, ref["t"].values)
    assert abs(df["s"].max() / 1852.0 - 500.0) / 500.0 < 0.01
    with pytest.raises(ValueError):
        op.trajectory_for_distance("a320", 0.0)


# =========================================================================== 7b. size-shrink: minor types -> "other"
def _synthetic_leg():
    mix = [
        {"bts_code": "A", "name": "Big", "openap": "a320", "share": 0.98, "range_km": 5000.0, "range_ok": True},
        {"bts_code": "B", "name": "Edge", "openap": "b738", "share": 0.01, "range_km": 3700.0, "range_ok": True},
        {"bts_code": "C", "name": "Small mapped", "openap": "crj9", "share": 0.0060, "range_km": None, "range_ok": None},
        {"bts_code": "D", "name": "Small unmapped", "openap": None, "share": 0.0040, "range_km": None, "range_ok": None},
    ]
    return {"aircraft_mix": mix, "unmapped_share": 0.0040, "fuel_kg_per_flight": 123.0, "profile_key": "a320-500"}


def test_collapse_minor_types_into_other():
    """KIND: example + invariants on a hand-built leg.
    TOLERANCE: shares abs 1e-12 (sum of exact decimals).
    Types with share < 0.01 become ONE trailing entry {'bts_code': 'other', openap/range None, share = sum}.
    A type at exactly 0.01 is kept (boundary); the other leg fields (fuel, unmapped_share, profile_key) are
    NOT touched; shares still sum to 1; input is not mutated; nothing minor -> no 'other' entry.
    CANNOT CATCH: the right threshold being chosen (author's decision, 0.01)."""
    leg = _synthetic_leg()
    before = copy.deepcopy(leg)
    out = tc.collapse_minor_types(leg, 0.01)
    assert leg == before
    assert [a["bts_code"] for a in out["aircraft_mix"]] == ["A", "B", "other"]
    other = out["aircraft_mix"][-1]
    assert other["share"] == pytest.approx(0.010, abs=1e-12)
    assert other["openap"] is None and other["range_km"] is None and other["range_ok"] is None
    assert "<" in other["name"] and "1" in other["name"]
    assert sum(a["share"] for a in out["aircraft_mix"]) == pytest.approx(1.0, abs=1e-12)
    for k in ("unmapped_share", "fuel_kg_per_flight", "profile_key"):
        assert out[k] == leg[k]
    no_minor = {"aircraft_mix": leg["aircraft_mix"][:2], "unmapped_share": 0.0}
    assert tc.collapse_minor_types(no_minor, 0.01)["aircraft_mix"] == no_minor["aircraft_mix"]
    empty = {"aircraft_mix": [], "unmapped_share": 1.0}
    assert tc.collapse_minor_types(empty, 0.01) == empty


def test_collapse_in_pipeline_changes_only_aircraft_mix(tmp_path):
    """KIND: metamorphic (min_mix_share 0 vs 0.5 on the fixture run) + identity at the default.
    TOLERANCE: exact equality of every field except aircraft_mix.
    The fixture has no type below 1%, so the default (0.01) run is byte-identical to the 0 run; with 0.5
    the minor types are merged, shares still sum to 1, and everything else (fuel, NOx, hubs, routes,
    unmapped_share, profile keys, messages) is unchanged: the merge must be display-only.
    CANNOT CATCH: collapse behaviour on the real file's types (see test_collapse_minor_types_into_other)."""
    def go(d, share):
        pt.run(db1b_path=FIX / "db1b_market.csv", t100_paths=[FIX / "t100_2019_q1.csv"],
               types_csv=FIX / "aircraft_types.csv",
               ourairports_csv=_HERE / "fixtures" / "airports" / "ourairports_sample.csv",
               out_dir=d, top_n=TOP_N, perf=FakePerf(), range_fn=fake_range, profile_fn=fake_profile,
               openap_version="fake-perf-fixture", min_mix_share=share)
        return json.loads((d / "trips_top50.json").read_text())

    full, dflt, half = go(tmp_path / "a", 0.0), go(tmp_path / "b", tc.MIN_MIX_SHARE), go(tmp_path / "c", 0.5)
    assert full["pairs"] == dflt["pairs"]
    merged = 0
    for k, t in half["pairs"].items():
        for r in ("point_to_point", "hub_and_spoke"):
            for lh, lf in zip(t[r]["legs"], full["pairs"][k][r]["legs"]):
                merged += sum(a["bts_code"] == "other" for a in lh["aircraft_mix"])
                assert {a: b for a, b in lh.items() if a != "aircraft_mix"} == {a: b for a, b in lf.items() if a != "aircraft_mix"}
                if lh["aircraft_mix"]:
                    assert sum(a["share"] for a in lh["aircraft_mix"]) == pytest.approx(1.0, abs=1e-4)
    assert merged > 0, "0.5 threshold should merge something in the fixture (BOS-ORD 698 at 1/3 and DEN-ORD 479 at 0.2)"
    strip = lambda d: {a: ({k: v for k, v in b.items() if k != "notes"} if a == "source" else b)
                       for a, b in d.items() if a != "pairs"}
    assert strip(half) == strip(full)


# =========================================================================== 8. committed outputs (run in CI too)
def _need_committed():
    assert COMMITTED.exists(), "data/trips_top50.json must be committed (NW-28 deliverable)"


def test_committed_trips_file_satisfies_contract():
    """KIND: property (schema + cross-field invariants on the committed deliverable; no cache needed).
    TOLERANCE: rounding tolerances as in test_rounding_policy_and_consistency (0.5e-4 per rounded term).
    2450 = 50*49 ordered pairs; airports sorted desc; hubs_fallback = first 10; ADR-0007 strictness holds
    for all pairs; flags consistent; P2P nonstop legs have departures >= 13; route sums = leg sums;
    profile files reference exactly the used keys; no NaN.
    CANNOT CATCH: wrong physics (only structure); that the file was generated from current code (see the
    fixture golden test for that)."""
    _need_committed()
    doc = json.loads(COMMITTED.read_text(), parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(c)))
    validate_doc(doc)
    assert len(doc["airports"]) == 50 and len(doc["pairs"]) == 2450
    codes = [a["iata"] for a in doc["airports"]]
    assert doc["hubs_fallback"] == codes[:10]
    assert [a["departures"] for a in doc["airports"]] == sorted((a["departures"] for a in doc["airports"]), reverse=True)
    assert set(doc["pairs"]) == {f"{o}-{d}" for o in codes for d in codes if o != d}
    for t in doc["pairs"].values():
        if t["nonstop_observed"]:
            assert t["point_to_point"]["airports"] == [t["origin"], t["destination"]]
            assert t["point_to_point"]["legs"][0]["t100_departures"] >= 13
            assert t["point_to_point_is_connecting"] is False
        else:
            assert t["point_to_point"] == t["hub_and_spoke"] and t["point_to_point_is_connecting"] is True
        assert len(t["hub_and_spoke"]["airports"]) == 3
        for r in routes_of(t):
            tol = 0.5e-4 * (len(r["legs"]) + 1) + 1e-9
            assert abs(r["fuel_kg_per_passenger"] - sum(l["fuel_kg_per_passenger"] for l in r["legs"])) <= tol
            for l in r["legs"]:
                assert 0.0 <= l["load_factor"] <= 1.0 + 1e-9
                codes_in_mix = [a["bts_code"] for a in l["aircraft_mix"]]
                assert codes_in_mix.count("other") <= 1 and "other" not in codes_in_mix[:-1]
                assert sum(a["share"] for a in l["aircraft_mix"]) == pytest.approx(1.0, abs=1e-4)
                real = [a["share"] for a in l["aircraft_mix"] if a["bts_code"] != "other"]
                assert real == sorted(real, reverse=True) and all(x >= 0.01 - 1e-6 for x in real)
    assert COMMITTED_PROFILES.exists()
    validate_profiles(json.loads(COMMITTED_PROFILES.read_text()), doc)


# =========================================================================== 9. real data / real OpenAP
@pytest.mark.skipif(not HAS_CACHE, reason="data/bts-cache/ (T-100 + OurAirports) absent (CI); real-data ranking test skipped")
def test_real_t100_top_airports():
    """KIND: property on real data (cache-gated).
    TOLERANCE: exact ordering facts; ties by code.
    Top-50 by Q1-2019 passenger departures: ATL first (largest hub), 50 distinct airports all with
    coordinates, departures non-increasing, and the set of 50 contains the 10 hubs named in the project
    (ORD, DFW, DEN, CLT, LAX, PHX, IAH, SEA, LAS -> top-10 candidates).
    CANNOT CATCH: DB1B-side errors (no DB1B read here; it takes minutes)."""
    paths = sorted(CACHE.glob("t100_2019_0[123].csv"))
    assert len(paths) == 3
    t = tc.load_t100_data(paths)
    ranked, excluded = tc.rank_airports(t.airport_departures, distance.AIRPORT_COORDS, 50)
    codes = [a for a, _ in ranked]
    assert codes[0] == "ATL" and len(set(codes)) == 50
    assert all(c in distance.AIRPORT_COORDS for c in codes)
    deps = [d for _, d in ranked]
    assert deps == sorted(deps, reverse=True)
    assert {"ORD", "DFW", "DEN", "CLT", "LAX", "PHX", "IAH", "SEA", "LAS"} <= set(codes[:10])


def test_real_openap_leg_fuel_range_and_profile(t100, db1b, airports):
    """KIND: differential, REAL OPENAP (the single real-OpenAP test).
    TOLERANCE: rel 1e-9 (identical deterministic computation; random=False in the generator).
    For BOS-ORD with the default (real) perf/range/profile functions: fuel_kg_per_flight equals
    sum(share * openap_perf.flight_fuel_nox(code, d10)['fuel_kg']) computed independently; range_km equals
    openap.prop.aircraft(code)['cruise']['range'] and range_ok is consistent; profile has <=120 points,
    starts at altitude 0 and reaches > 25000 ft; distances monotone non-decreasing.
    CANNOT CATCH: OpenAP model bias (this checks wiring, not physical truth)."""
    import openap_perf
    from openap import prop

    e = tc.Env(t100=t100, db1b=db1b, hubs_fallback=[], type_names=tc.load_type_names(FIX / "aircraft_types.csv"))
    leg, _ = tc.build_leg(e, "BOS", "ORD")
    dist = distance.distance_nm("BOS", "ORD")
    dr = max(10, round(dist / 10.0) * 10)
    exp = 0.0
    for a in leg["aircraft_mix"]:
        if a["openap"]:
            exp += a["share"] * openap_perf.flight_fuel_nox(a["openap"], dr)["fuel_kg"]
            rng = prop.aircraft(a["openap"])["cruise"]["range"]
            assert a["range_km"] == rng and a["range_ok"] is (rng >= dist * 1.852)
    assert exp > 1000.0
    assert leg["fuel_kg_per_flight"] == pytest.approx(exp, rel=1e-9)
    prof = e.profiles[leg["profile_key"]]
    assert 2 <= len(prof["t_s"]) <= 120
    assert prof["alt_ft"][0] == 0 and max(prof["alt_ft"]) > 25000
    assert prof["dist_nm"] == sorted(prof["dist_nm"])
