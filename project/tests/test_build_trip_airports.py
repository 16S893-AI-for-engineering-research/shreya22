"""
Tests for project/src/build_trip_airports.py (NW-31): the committed
data/trip_airports.json that the trip tool's ground-track maps read.

Every test docstring states KIND, TOLERANCE (and why) and CANNOT CATCH.
Nothing here touches the network or the BTS cache: inputs are tiny synthetic
files in tmp_path, plus the two COMMITTED files data/trips_top50.json and
project/data/airports.csv (present in CI).
"""
from __future__ import annotations

import csv
import json
import pathlib

import pytest

import build_trip_airports as bta

REPO = pathlib.Path(__file__).resolve().parents[2]
TRIPS = REPO / "data" / "trips_top50.json"
OUT = REPO / "data" / "trip_airports.json"
CSV = REPO / "project" / "data" / "airports.csv"


def _route(*codes):
    return {"airports": list(codes)}


def _mini_trips():
    return {
        "airports": [{"iata": "BOS"}, {"iata": "SFO"}],
        "pairs": {
            "BOS-SFO": {"point_to_point": _route("BOS", "SFO"),
                        "hub_and_spoke": _route("BOS", "LAX", "SFO")},
        },
    }


def _mini_csv(tmp_path, rows):
    p = tmp_path / "airports.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["iata", "lat", "lon"])
        w.writerows(rows)
    return p


def test_collect_airports_is_union_of_list_and_both_structures():
    """KIND: example. TOLERANCE: none (set equality).
    LAX appears ONLY as a hub, so a collector that read just `airports` or just
    point_to_point would drop it. CANNOT CATCH: shapes of the real file (next tests)."""
    t = _mini_trips()
    assert bta.collect_airports(t) == ["BOS", "LAX", "SFO"]
    t["airports"].append({"iata": "ZZZ"})
    t["pairs"]["BOS-SFO"]["point_to_point"] = _route("BOS", "DEN", "SFO")
    assert bta.collect_airports(t) == ["BOS", "DEN", "LAX", "SFO", "ZZZ"]


def test_build_rounds_to_4_decimals_and_has_schema_version(tmp_path):
    """KIND: example. TOLERANCE: exact equality of the rounded value; rounding is
    the specified behaviour (4 dp ~ 11 m, far below a map pixel). CANNOT CATCH:
    a CSV with different column order (the header is read by name)."""
    p = _mini_csv(tmp_path, [["BOS", "42.36429596", "-71.00520325"],
                             ["LAX", "33.9425", "-118.4081"],
                             ["SFO", "37.61899948", "-122.375"]])
    out = bta.build(_mini_trips(), p)
    assert out["schema_version"] == 1
    assert out["airports"]["BOS"] == {"lat": 42.3643, "lon": -71.0052}
    assert list(out["airports"]) == ["BOS", "LAX", "SFO"]


def test_build_missing_airport_in_csv_is_a_clear_error(tmp_path):
    """KIND: example (error path). CANNOT CATCH: other bad CSV content."""
    p = _mini_csv(tmp_path, [["BOS", "1", "2"], ["SFO", "3", "4"]])
    with pytest.raises(KeyError, match="LAX"):
        bta.build(_mini_trips(), p)


def test_dumps_is_deterministic_sorted_and_has_no_timestamp(tmp_path):
    """KIND: metamorphic (same input, shuffled key order -> identical bytes).
    TOLERANCE: none. CANNOT CATCH: nondeterminism that depends on the
    environment (locale, platform float formatting) rather than dict order."""
    p = _mini_csv(tmp_path, [["SFO", "37.619", "-122.375"], ["LAX", "33.9425", "-118.4081"],
                             ["BOS", "42.3643", "-71.0052"]])
    t1 = _mini_trips()
    t2 = {"pairs": dict(reversed(list(t1["pairs"].items()))), "airports": list(reversed(t1["airports"]))}
    a = bta.dumps(bta.build(t1, p))
    b = bta.dumps(bta.build(t2, p))
    assert a == b
    assert a.endswith("\n")
    keys = list(json.loads(a)["airports"])
    assert keys == sorted(keys)
    assert "time" not in a.lower() and "date" not in a.lower()


def test_real_trips_every_airport_has_an_entry_equal_to_csv_rounded():
    """KIND: property over the real committed data (every airport of every one of
    the 2450 pairs, both structures). TOLERANCE: exact equality to round(csv, 4):
    the script rounds the same way, so any difference is a bug.
    CANNOT CATCH: wrong coordinates in the CSV itself (that is upstream data)."""
    trips = json.loads(TRIPS.read_text())
    out = bta.build(trips, CSV)["airports"]
    used = set(a["iata"] for a in trips["airports"])
    for p in trips["pairs"].values():
        used |= set(p["point_to_point"]["airports"]) | set(p["hub_and_spoke"]["airports"])
    assert set(out) == used and len(used) >= 50
    with open(CSV, newline="") as f:
        rows = {r["iata"]: r for r in csv.DictReader(f)}
    for code, v in out.items():
        assert v["lat"] == round(float(rows[code]["lat"]), 4)
        assert v["lon"] == round(float(rows[code]["lon"]), 4)


def test_committed_file_is_exactly_what_the_script_produces():
    """KIND: golden (the committed artefact equals a fresh regeneration, byte for
    byte). TOLERANCE: none. CANNOT CATCH: the script and file both being wrong
    in the same way (coordinate truth is checked against the CSV above)."""
    trips = json.loads(TRIPS.read_text())
    assert OUT.read_text() == bta.dumps(bta.build(trips, CSV))


def test_cli_writes_the_file(tmp_path):
    """KIND: example (end-to-end through main()). CANNOT CATCH: running via
    `python -m` from a different cwd (paths are explicit arguments here)."""
    out = tmp_path / "x.json"
    bta.main(["--out", str(out), "--trips", str(TRIPS), "--airports-csv", str(CSV)])
    assert out.read_text() == OUT.read_text()
