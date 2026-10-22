"""
build_trip_airports.py - build data/trip_airports.json (NW-31).

Run from project/src::

    python -m build_trip_airports --out ../../data/trip_airports.json

Writes ``{"schema_version": 1, "airports": {"BOS": {"lat": .., "lon": ..}, ...}}``
for exactly the airports that the trip tool can draw: the airports listed in
data/trips_top50.json plus every airport on either route of any pair (hubs
included).  Coordinates come from project/data/airports.csv, rounded to 4
decimals (~11 m).  Output is deterministic: sorted keys, no timestamps, no
absolute paths, no network.
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
from typing import Dict, List, Optional

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_TRIPS = _REPO / "data" / "trips_top50.json"
DEFAULT_CSV = _REPO / "project" / "data" / "airports.csv"
SCHEMA_VERSION = 1
DECIMALS = 4


def collect_airports(trips: dict) -> List[str]:
    """Sorted union of the listed airports and every airport on either route of every pair."""
    used = {a["iata"] for a in trips["airports"]}
    for pair in trips["pairs"].values():
        used.update(pair["point_to_point"]["airports"])
        used.update(pair["hub_and_spoke"]["airports"])
    return sorted(used)


def build(trips: dict, airports_csv) -> dict:
    """Return the JSON-ready dict. Raises KeyError naming any airport missing from the CSV."""
    with open(airports_csv, newline="") as f:
        rows: Dict[str, dict] = {r["iata"]: r for r in csv.DictReader(f)}
    out = {}
    for code in collect_airports(trips):
        if code not in rows:
            raise KeyError(f"airport {code!r} is used by the trips file but missing from {airports_csv}")
        r = rows[code]
        out[code] = {"lat": round(float(r["lat"]), DECIMALS), "lon": round(float(r["lon"]), DECIMALS)}
    return {"schema_version": SCHEMA_VERSION, "airports": out}


def dumps(obj: dict) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m build_trip_airports",
        description="Write data/trip_airports.json (coordinates for the trip tool maps). Run from project/src.",
    )
    p.add_argument("--out", required=True, help="output JSON path")
    p.add_argument("--trips", default=str(DEFAULT_TRIPS), help="trips_top50.json (default: repo data/)")
    p.add_argument("--airports-csv", default=str(DEFAULT_CSV), help="airports.csv (default: project/data/airports.csv)")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    with open(args.trips) as f:
        trips = json.load(f)
    text = dumps(build(trips, args.airports_csv))
    pathlib.Path(args.out).write_text(text)


if __name__ == "__main__":  # pragma: no cover
    main()
