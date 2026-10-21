"""
precompute_trips.py - build data/trips_top50.json and data/trip_profiles.json (NW-28).

Run from project/src::

    python -m precompute_trips --out-dir ../../data

Reads the BTS cache in data/bts-cache/ (DB1B Market 2019Q1 zip, T-100 Jan-Mar 2019,
L_AIRCRAFT_TYPE, OurAirports), one pass over DB1B, and writes the two compact,
deterministic JSON files described in docs/trip-schema.md.  Nothing here uses the
network, the clock or absolute paths in the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional

import distance
import trip_compare as tc

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent
_CACHE = _REPO / "data" / "bts-cache"
QUARTER = "2019Q1"


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _dump(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _openap_version() -> str:
    try:
        import importlib.metadata as m
        return m.version("openap")
    except Exception:  # pragma: no cover
        return "unknown"


def run(
    *,
    db1b_path,
    t100_paths: Iterable,
    types_csv,
    ourairports_csv,
    out_dir,
    top_n: int = 50,
    perf: Optional[Callable] = None,
    range_fn: Optional[Callable] = None,
    profile_fn: Optional[Callable] = None,
    openap_version: Optional[str] = None,
    only_pairs: Optional[Iterable[str]] = None,
    min_mix_share: float = tc.MIN_MIX_SHARE,
) -> Dict:
    """Build both output files in *out_dir*; return a report dict (not written to disk)."""
    t100_paths = [pathlib.Path(p) for p in t100_paths]
    out_dir = pathlib.Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    t100 = tc.load_t100_data(t100_paths)
    ranked, excluded = tc.rank_airports(t100.airport_departures, distance.AIRPORT_COORDS, top_n)
    codes = [a for a, _ in ranked]
    db1b = tc.aggregate_db1b(db1b_path, set(codes))
    names = tc.load_airport_names(ourairports_csv)
    env = tc.Env(
        t100=t100, db1b=db1b, hubs_fallback=codes[:10],
        type_names=tc.load_type_names(types_csv),
        perf=perf, range_fn=range_fn, profile_fn=profile_fn,
    )
    pairs = tc.build_all_pairs(env, codes, only=only_pairs)

    # ---- report (counts for the human; the notes below repeat the stable ones)
    rules = Counter(t["hub"]["rule"] for t in pairs.values())
    unobs = sorted(k for k, t in pairs.items()
                   if any(tc.leg_is_unobserved(l) for r in (t["point_to_point"], t["hub_and_spoke"]) for l in r["legs"]))
    range_fail = sorted({m for t in pairs.values() for m in t["messages"] if m.startswith("Range:")})
    range_fail_pairs = sorted(k for k, t in pairs.items() if any(m.startswith("Range:") for m in t["messages"]))

    notes = [
        f"Top {top_n} airports by T-100 passenger (AIRCRAFT_CONFIG=1) departures, Jan-Mar 2019, ties by code; "
        f"only airports with coordinates in project/data/airports.csv"
        + (f" (excluded for missing coordinates: {', '.join(excluded)})." if excluded else " (none excluded)."),
        f"Nonstop observed = directional T-100 segment with >= {tc.NONSTOP_MIN_DEPARTURES} departures and a usable "
        f"passenger/seat count; otherwise point_to_point is an exact copy of hub_and_spoke (ADR 0007, strict).",
        f"DB1B passengers are the 10% sample scaled x{tc.SAMPLE_SCALE}; hub = intermediate airport with most one-stop "
        f"passengers whose two legs are both observed (ties by code); share_of_connecting_passengers divides by ALL "
        f"one-stop passengers for the directed OD.",
        f"DB1B rows whose AirportGroup does not start at Origin and end at Dest are ignored ({db1b.anomalies} rows).",
        "Fuel and NOx per flight are departure-weighted over mapped aircraft types; unmapped types are excluded and NOT "
        "rescaled (unmapped_share is reported per leg). Per-passenger values divide by seats_per_departure * load_factor.",
        f"OpenAP fuel/NOx is evaluated at the leg great-circle distance rounded to the nearest {tc.PERF_ROUND_NM} nm "
        f"and cached per (OpenAP code, distance); profiles use the nearest {tc.PROFILE_ROUND_NM} nm (minimum "
        f"{tc.PROFILE_ROUND_NM}) and at most {tc.MAX_PROFILE_POINTS} points.",
        f"Size: aircraft types with a departure share below {min_mix_share * 100:g}% on a leg are merged into one "
        f"'other' entry in aircraft_mix (openap null, range null); fuel, NOx, unmapped_share and profile_key were "
        f"computed from the full mix before merging.",
        "Rounding: *_per_passenger 4 decimals; shares and load factors 5; all other floats 2. Totals of rounded "
        "legs may differ from the rounded route total by less than 1e-4 kg per passenger.",
        f"Pairs with a leg that is not observed (empty aircraft_mix, fuel 0.0): {len(unobs)}.",
    ]
    doc = {
        "schema_version": 1,
        "source": {
            "quarter": QUARTER,
            "db1b_sha256": _sha256(db1b_path),
            "t100_sha256": [_sha256(p) for p in t100_paths],
            "airports_sha256": _sha256(ourairports_csv),
            "openap_version": openap_version if openap_version is not None else _openap_version(),
            "nonstop_min_departures": tc.NONSTOP_MIN_DEPARTURES,
            "notes": notes,
        },
        "airports": [{"iata": c, "name": names.get(c, c), "departures": dep} for c, dep in ranked],
        "hubs_fallback": codes[:10],
        "pairs": pairs,
    }
    used = {l["profile_key"] for t in pairs.values() for r in (t["point_to_point"], t["hub_and_spoke"])
            for l in r["legs"] if l["profile_key"] is not None}
    profiles = {"schema_version": 1, "profiles": {k: env.profiles[k] for k in sorted(used)}}

    if min_mix_share > 0:
        doc["pairs"] = {k: tc.collapse_trip(t, min_mix_share) for k, t in doc["pairs"].items()}
    (out_dir / "trips_top50.json").write_text(_dump(tc.round_floats(doc)), encoding="utf-8")
    (out_dir / "trip_profiles.json").write_text(_dump(profiles), encoding="utf-8")

    return {
        "n_airports": len(codes), "n_pairs": len(pairs), "excluded_no_coords": excluded,
        "hub_rule_counts": dict(rules), "pairs_with_unobserved_leg": unobs,
        "range_failure_messages": range_fail, "range_failure_pairs": range_fail_pairs,
        "db1b_anomalous_rows": db1b.anomalies, "n_profiles": len(profiles["profiles"]),
        "n_perf_evaluations": len(env._perf_cache),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m precompute_trips",
        description="Precompute per-trip point-to-point vs hub-and-spoke fuel (docs/trip-schema.md). Run from project/src.",
    )
    p.add_argument("--out-dir", required=True, help="directory for trips_top50.json and trip_profiles.json")
    p.add_argument("--top-n", type=int, default=50)
    p.add_argument("--min-mix-share", type=float, default=tc.MIN_MIX_SHARE,
                   help="merge aircraft types below this departure share into 'other' (file size; 0 disables)")
    p.add_argument("--cache-dir", default=str(_CACHE), help="BTS cache directory (default: data/bts-cache)")
    return p


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    cache = pathlib.Path(args.cache_dir)
    t0 = time.time()
    rep = run(
        db1b_path=cache / "DB1BMarket_2019_1.zip",
        t100_paths=[cache / f"t100_2019_0{m}.csv" for m in (1, 2, 3)],
        types_csv=cache / "L_AIRCRAFT_TYPE.csv",
        ourairports_csv=cache / "ourairports_airports.csv",
        out_dir=args.out_dir,
        top_n=args.top_n,
        min_mix_share=args.min_mix_share,
    )
    rep["runtime_s"] = round(time.time() - t0, 1)
    json.dump(rep, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":  # pragma: no cover
    main()
