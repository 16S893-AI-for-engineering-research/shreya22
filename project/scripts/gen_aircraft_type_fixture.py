#!/usr/bin/env python3
"""
gen_aircraft_type_fixture.py — NW-21

Generates tests/fixtures/bts/q1_2019_aircraft_types.csv from the real
data/bts-cache/ T-100 files.

Output columns:
  aircraft_type  — BTS AIRCRAFT_TYPE code (zero-padded 3-digit string)
  departures     — total DEPARTURES_PERFORMED in Q1 2019 (AIRCRAFT_CONFIG==1)
  description    — BTS L_AIRCRAFT_TYPE description

Usage (from repo root):
  uv run --directory project -p 3.11 python scripts/gen_aircraft_type_fixture.py

The script writes to project/tests/fixtures/bts/q1_2019_aircraft_types.csv.
Requires data/bts-cache/ to be present (git-ignored; CI skips tests that need
this file).
"""
from __future__ import annotations

import pathlib
import sys

# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
CACHE_DIR = REPO_ROOT / "data" / "bts-cache"
OUT_PATH = REPO_ROOT / "project" / "tests" / "fixtures" / "bts" / "q1_2019_aircraft_types.csv"

T100_FILES = [
    CACHE_DIR / "t100_2019_01.csv",
    CACHE_DIR / "t100_2019_02.csv",
    CACHE_DIR / "t100_2019_03.csv",
]
LOOKUP_FILE = CACHE_DIR / "L_AIRCRAFT_TYPE.csv"


def main() -> None:
    try:
        import pandas as pd
    except ImportError:
        print("pandas is required; run inside the project venv.", file=sys.stderr)
        sys.exit(1)

    # ── verify inputs ────────────────────────────────────────────────────────
    missing = [p for p in T100_FILES + [LOOKUP_FILE] if not p.exists()]
    if missing:
        print("Missing cache files:", file=sys.stderr)
        for p in missing:
            print(f"  {p}", file=sys.stderr)
        sys.exit(1)

    # ── load T-100, filter passenger + positive departures ───────────────────
    frames: list = []
    for path in T100_FILES:
        for chunk in pd.read_csv(
            str(path),
            usecols=["DEPARTURES_PERFORMED", "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG"],
            chunksize=50_000,
            dtype=str,
        ):
            chunk["AIRCRAFT_CONFIG"] = pd.to_numeric(chunk["AIRCRAFT_CONFIG"], errors="coerce")
            chunk["DEPARTURES_PERFORMED"] = pd.to_numeric(
                chunk["DEPARTURES_PERFORMED"], errors="coerce"
            )
            mask = (chunk["AIRCRAFT_CONFIG"] == 1) & (chunk["DEPARTURES_PERFORMED"] > 0)
            chunk = chunk[mask].copy()
            if chunk.empty:
                continue
            chunk["AIRCRAFT_TYPE"] = chunk["AIRCRAFT_TYPE"].apply(
                lambda x: str(int(float(x))).zfill(3)
            )
            frames.append(chunk)

    df = pd.concat(frames, ignore_index=True)

    # ── aggregate by AIRCRAFT_TYPE ───────────────────────────────────────────
    grouped = (
        df.groupby("AIRCRAFT_TYPE")["DEPARTURES_PERFORMED"]
        .sum()
        .reset_index()
        .rename(columns={"DEPARTURES_PERFORMED": "departures"})
    )
    grouped["departures"] = grouped["departures"].astype(int)
    grouped = grouped.sort_values("departures", ascending=False).reset_index(drop=True)

    # ── join description ─────────────────────────────────────────────────────
    lookup = pd.read_csv(str(LOOKUP_FILE), dtype=str)
    lookup = lookup.rename(columns={"Code": "AIRCRAFT_TYPE", "Description": "description"})
    lookup["AIRCRAFT_TYPE"] = lookup["AIRCRAFT_TYPE"].apply(
        lambda x: str(int(float(x))).zfill(3)
    )
    grouped = grouped.merge(lookup, on="AIRCRAFT_TYPE", how="left")
    grouped["description"] = grouped["description"].fillna("UNKNOWN")
    grouped = grouped.rename(columns={"AIRCRAFT_TYPE": "aircraft_type"})

    # ── write ────────────────────────────────────────────────────────────────
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    grouped.to_csv(str(OUT_PATH), index=False)

    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"Written: {OUT_PATH}")
    print(f"  {len(grouped)} aircraft types, {grouped['departures'].sum():,} total departures")
    print(f"  File size: {size_kb:.1f} KB")
    if size_kb > 50:
        print("WARNING: fixture exceeds 50 KB limit!", file=sys.stderr)


if __name__ == "__main__":
    main()
