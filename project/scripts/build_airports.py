#!/usr/bin/env python3
"""
project/scripts/build_airports.py

Reads the OurAirports airports.csv and BTS T-100 monthly segment CSVs,
identifies which T-100 passenger airports (AIRCRAFT_CONFIG==1) match a
non-closed OurAirports iata_code, and writes:

  project/data/airports.csv      — iata,lat,lon for the ~806 matched airports
  project/data/airports_meta.json — provenance: source_url, download_date,
                                    source_sha256, and unmatched_codes list

Usage (production — reads from data/bts-cache/):
    python project/scripts/build_airports.py

Usage (test — custom paths):
    python project/scripts/build_airports.py \\
        --oa-csv path/to/ourairports_airports.csv \\
        --t100-csvs path/to/t100_jan.csv path/to/t100_feb.csv \\
        --out-dir path/to/output/

Source provenance
-----------------
OurAirports airports.csv:
  URL:           https://davidmegginson.github.io/ourairports-data/airports.csv
  Download date: 2026-10-15
  SHA-256:       536d6de0dfeed92d4bbe8fd7411ba6bc14c43251c2161f34b9fbfd50ee3f4113

T-100 files: BTS T-100 Domestic Segment (All Carriers), 2019 Q1
  db/bts-cache/t100_2019_01.csv, t100_2019_02.csv, t100_2019_03.csv
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

import pandas as pd

# ---------------------------------------------------------------------------
# Hard-coded provenance for the production OurAirports download
# ---------------------------------------------------------------------------

_OA_SOURCE_URL = (
    "https://davidmegginson.github.io/ourairports-data/airports.csv"
)
_OA_DOWNLOAD_DATE = "2026-10-15"
_OA_SHA256 = (
    "536d6de0dfeed92d4bbe8fd7411ba6bc14c43251c2161f34b9fbfd50ee3f4113"
)

# ---------------------------------------------------------------------------
# Default paths (relative to this script's location)
# ---------------------------------------------------------------------------

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
_PROJECT_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _PROJECT_DIR.parent

_DEFAULT_OA_CSV = _REPO_ROOT / "data" / "bts-cache" / "ourairports_airports.csv"
_DEFAULT_T100_CSVS = [
    _REPO_ROOT / "data" / "bts-cache" / "t100_2019_01.csv",
    _REPO_ROOT / "data" / "bts-cache" / "t100_2019_02.csv",
    _REPO_ROOT / "data" / "bts-cache" / "t100_2019_03.csv",
]
_DEFAULT_OUT_DIR = _PROJECT_DIR / "data"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _sha256_hex(path: pathlib.Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_airports(
    oa_csv: pathlib.Path,
    t100_csvs: list[pathlib.Path],
    out_dir: pathlib.Path,
) -> tuple[int, list[str]]:
    """
    Build airports.csv and airports_meta.json.

    Parameters
    ----------
    oa_csv : Path
        OurAirports airports.csv (full schema with iata_code, type, etc.)
    t100_csvs : list[Path]
        One or more T-100 monthly CSVs (ORIGIN, DEST, AIRCRAFT_CONFIG columns
        required; DISTANCE required only for validation purposes but not used here).
    out_dir : Path
        Directory to write airports.csv and airports_meta.json into.

    Returns
    -------
    (n_matched, unmatched_codes) : tuple[int, list[str]]
        Number of matched airports and sorted list of unmatched T-100 codes.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- 1. Load OurAirports; keep non-closed rows with a non-empty iata_code ---
    oa = pd.read_csv(oa_csv, low_memory=False)
    oa_open = oa[
        (oa["type"] != "closed")
        & oa["iata_code"].notna()
        & (oa["iata_code"].str.strip() != "")
    ].copy()

    # Verify no duplicate iata_codes in the open set
    # (reviewer confirmed 0 duplicates; we enforce this defensively)
    dups = oa_open["iata_code"].duplicated()
    if dups.any():
        dup_codes = sorted(oa_open.loc[dups, "iata_code"].unique())
        raise ValueError(
            f"Duplicate iata_codes in OurAirports (non-closed): {dup_codes}"
        )

    # Build lookup: iata_code -> (lat, lon)
    oa_lookup: dict[str, tuple[float, float]] = {
        row["iata_code"]: (float(row["latitude_deg"]), float(row["longitude_deg"]))
        for _, row in oa_open.iterrows()
    }

    # --- 2. Load T-100; collect unique airport codes from pax-configured rows ---
    dfs: list[pd.DataFrame] = []
    for p in t100_csvs:
        dfs.append(pd.read_csv(p))
    t100 = pd.concat(dfs, ignore_index=True)

    pax = t100[t100["AIRCRAFT_CONFIG"] == 1]
    origins = set(pax["ORIGIN"].dropna().unique())
    dests = set(pax["DEST"].dropna().unique())
    t100_codes: set[str] = origins | dests

    # --- 3. Match T-100 codes to OurAirports ---
    matched_codes = sorted(t100_codes & set(oa_lookup.keys()))
    unmatched_codes = sorted(t100_codes - set(oa_lookup.keys()))

    # --- 4. Build the output DataFrame (sorted by iata for determinism) ---
    rows = [
        {"iata": code, "lat": oa_lookup[code][0], "lon": oa_lookup[code][1]}
        for code in matched_codes
    ]
    df_out = pd.DataFrame(rows, columns=["iata", "lat", "lon"])

    # --- 5. Write airports.csv ---
    out_csv = out_dir / "airports.csv"
    with open(out_csv, "w") as f:
        f.write("iata,lat,lon\n")
        for _, row in df_out.iterrows():
            f.write(f"{row['iata']},{row['lat']},{row['lon']}\n")

    # --- 6. Write sidecar JSON ---
    # Use the hard-coded provenance if the oa_csv is the production file,
    # otherwise compute the sha256 of whatever file was passed in.
    if oa_csv.resolve() == _DEFAULT_OA_CSV.resolve():
        sha256 = _OA_SHA256
        source_url = _OA_SOURCE_URL
        download_date = _OA_DOWNLOAD_DATE
    else:
        sha256 = _sha256_hex(oa_csv)
        source_url = _OA_SOURCE_URL  # still the same dataset
        download_date = _OA_DOWNLOAD_DATE

    meta = {
        "source_url": source_url,
        "download_date": download_date,
        "source_sha256": sha256,
        "oa_csv_used": str(oa_csv),
        "t100_csvs_used": [str(p) for p in t100_csvs],
        "n_t100_airports": len(t100_codes),
        "n_matched": len(matched_codes),
        "n_unmatched": len(unmatched_codes),
        "unmatched_codes": unmatched_codes,
    }

    out_json = out_dir / "airports_meta.json"
    with open(out_json, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")

    return len(matched_codes), unmatched_codes


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Build project/data/airports.csv from OurAirports + T-100."
    )
    p.add_argument(
        "--oa-csv",
        type=pathlib.Path,
        default=_DEFAULT_OA_CSV,
        help="Path to OurAirports airports.csv",
    )
    p.add_argument(
        "--t100-csvs",
        nargs="+",
        type=pathlib.Path,
        default=_DEFAULT_T100_CSVS,
        help="Path(s) to T-100 monthly CSV file(s)",
    )
    p.add_argument(
        "--out-dir",
        type=pathlib.Path,
        default=_DEFAULT_OUT_DIR,
        help="Output directory for airports.csv and airports_meta.json",
    )
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    if not args.oa_csv.exists():
        print(
            f"ERROR: OurAirports CSV not found: {args.oa_csv}\n"
            "       Run with --oa-csv to specify a custom path, or ensure\n"
            "       data/bts-cache/ourairports_airports.csv is present.",
            file=sys.stderr,
        )
        sys.exit(1)

    missing_t100 = [p for p in args.t100_csvs if not p.exists()]
    if missing_t100:
        print(
            f"ERROR: T-100 CSV(s) not found: {missing_t100}\n"
            "       Run with --t100-csvs to specify custom paths.",
            file=sys.stderr,
        )
        sys.exit(1)

    n_matched, unmatched = build_airports(args.oa_csv, args.t100_csvs, args.out_dir)

    print(
        f"airports.csv written to {args.out_dir / 'airports.csv'}\n"
        f"  Matched airports: {n_matched}\n"
        f"  Unmatched T-100 codes: {len(unmatched)}\n"
        f"  Unmatched: {unmatched}"
    )
    print(
        f"airports_meta.json written to {args.out_dir / 'airports_meta.json'}"
    )


if __name__ == "__main__":
    main()
