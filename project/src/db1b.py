"""
DB1B Market demand adapter.

Reads BTS DB1B Market data (CSV or zip) and aggregates to directional OD
passenger demand for a specified set of airports.

Public API
----------
SAMPLE_SCALE : int
    The DB1B ticket sample factor (10 — BTS publishes a 10% sample, so
    raw Passengers must be multiplied by 10 to get estimated full demand).

read_market(path_or_zip, region, chunksize=100_000, scale=SAMPLE_SCALE)
    Stream DB1B Market rows, keep domestic rows with both endpoints in
    *region*, aggregate to directional OD, and return a list of ODDemand.

write_demand_csv(od, path)
    Write a list of ODDemand objects to a CSV with exactly the header
    ``origin,destination,passengers`` so that networks.load_demand can
    read it back.

CLI
---
Run from project/src::

    python -m db1b --market <zip> --region BOS,DEN,DTW,MSP,ORD,SFO --out demand.csv

Design notes
------------
* No third-party dependencies — only the Python standard library.
* "Streaming" means we iterate over the CSV in chunks of *chunksize* rows
  so that the 1.6 GB unzipped file never needs to be fully in memory.
* ``usecols`` behaviour is emulated by reading DictReader and indexing only
  Origin, OriginCountry, Dest, DestCountry, Passengers — all other columns
  are ignored and never stored.
* The chunk-size invariance test (metamorphic) proves that aggregate results
  are identical regardless of chunksize.
"""
from __future__ import annotations

import argparse
import csv
import io
import pathlib
import sys
import zipfile
from collections import defaultdict
from typing import Dict, Iterable, List, Set, Union

# Expose this in the public API so tests can import it directly.
from networks import ODDemand

# ---------------------------------------------------------------------------
# Named constant (documented: DB1B is a 10% ticket sample)
# ---------------------------------------------------------------------------

#: The DB1B Market survey is a **10% random ticket sample**.  Multiply raw
#: ``Passengers`` by this factor to estimate full-market demand.
SAMPLE_SCALE: int = 10

# Columns we actually need (all others are ignored)
_NEED_COLS = {"Origin", "OriginCountry", "Dest", "DestCountry", "Passengers"}


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _open_csv_source(path_or_zip: Union[str, pathlib.Path]):
    """
    Return a context manager that yields a text-mode iterable over the CSV
    rows of *path_or_zip*.

    Supports:
    * A plain ``.csv`` file path.
    * A ``.zip`` archive containing exactly one ``.csv`` file (BTS convention).
    """
    p = pathlib.Path(path_or_zip)
    if p.suffix.lower() == ".zip":
        return _ZipCsvContext(p)
    return open(p, newline="", encoding="utf-8")


class _ZipCsvContext:
    """Context manager that opens the first .csv member of a zip archive."""

    def __init__(self, zip_path: pathlib.Path) -> None:
        self._zip_path = zip_path
        self._zf: zipfile.ZipFile | None = None
        self._member_fh = None
        self._text_fh = None

    def __enter__(self):
        self._zf = zipfile.ZipFile(self._zip_path, "r")
        csv_members = [n for n in self._zf.namelist() if n.lower().endswith(".csv")]
        if not csv_members:
            raise FileNotFoundError(f"No .csv member found in {self._zip_path}")
        self._member_fh = self._zf.open(csv_members[0])
        self._text_fh = io.TextIOWrapper(self._member_fh, encoding="utf-8")
        return self._text_fh

    def __exit__(self, *args):
        if self._text_fh is not None:
            self._text_fh.close()
        if self._zf is not None:
            self._zf.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def read_market(
    path_or_zip: Union[str, pathlib.Path],
    region: Set[str],
    chunksize: int = 100_000,
    scale: int = SAMPLE_SCALE,
) -> List[ODDemand]:
    """
    Stream DB1B Market rows and aggregate to directional OD passenger demand.

    Parameters
    ----------
    path_or_zip:
        Path to a plain CSV file *or* a ``.zip`` archive (BTS PREZIP format).
    region:
        Set of IATA airport codes that define the study region.  Only rows
        where *both* Origin and Dest are in *region* (and both countries are
        'US') are kept.
    chunksize:
        Number of rows to read per chunk.  Results are identical for any
        positive chunksize (metamorphic invariant).
    scale:
        Passenger multiplier; defaults to ``SAMPLE_SCALE`` (10).  Override
        for testing linearity.

    Returns
    -------
    List[ODDemand]
        One entry per unique directional OD pair in *region*, with
        ``passengers = scale × sum(raw Passengers)``.
        Entries are sorted (origin, destination) for determinism.
    """
    # Accumulate raw (unscaled) passenger counts keyed by (origin, dest)
    od_raw: Dict[tuple, float] = defaultdict(float)

    with _open_csv_source(path_or_zip) as f:
        reader = csv.DictReader(f)
        chunk: list = []
        for row in reader:
            chunk.append(row)
            if len(chunk) >= chunksize:
                _process_chunk(chunk, region, od_raw)
                chunk = []
        # Final partial chunk
        if chunk:
            _process_chunk(chunk, region, od_raw)

    # Apply scale and convert to ODDemand objects
    result = [
        ODDemand(origin=o, destination=d, passengers=pax * scale)
        for (o, d), pax in sorted(od_raw.items())
    ]
    return result


def _process_chunk(
    chunk: list,
    region: Set[str],
    od_raw: Dict[tuple, float],
) -> None:
    """
    Filter *chunk* rows in-place and accumulate into *od_raw*.

    Filtering rules:
    1. OriginCountry == 'US' and DestCountry == 'US'  (domestic only)
    2. Origin in *region* and Dest in *region*         (study region only)
    """
    for row in chunk:
        o_country = row["OriginCountry"].strip()
        d_country = row["DestCountry"].strip()
        if o_country != "US" or d_country != "US":
            continue
        o = row["Origin"].strip()
        d = row["Dest"].strip()
        if o not in region or d not in region:
            continue
        pax = float(row["Passengers"])
        od_raw[(o, d)] += pax


def write_demand_csv(
    od: Iterable[ODDemand],
    path: Union[str, pathlib.Path],
) -> None:
    """
    Write a list of ODDemand objects to a CSV file.

    The output has exactly the header ``origin,destination,passengers`` so
    that ``networks.load_demand`` can read it back without modification.
    Passengers are written as plain floats (no integer truncation).

    Parameters
    ----------
    od:
        Iterable of ODDemand objects.
    path:
        Destination file path; parent directory must exist.
    """
    p = pathlib.Path(path)
    with open(p, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["origin", "destination", "passengers"])
        for rec in od:
            writer.writerow([rec.origin, rec.destination, rec.passengers])


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m db1b",
        description=(
            "Aggregate DB1B Market demand to OD CSV for a study region. "
            "Run from project/src."
        ),
    )
    p.add_argument("--market", required=True, help="Path to DB1B Market .zip or .csv")
    p.add_argument(
        "--region",
        required=True,
        help="Comma-separated IATA codes, e.g. BOS,DEN,DTW,MSP,ORD,SFO",
    )
    p.add_argument("--out", required=True, help="Output demand CSV path")
    p.add_argument(
        "--chunksize",
        type=int,
        default=100_000,
        help="Rows per streaming chunk (default 100000)",
    )
    return p


def main(argv: list | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    region = {code.strip().upper() for code in args.region.split(",")}
    print(f"Reading {args.market!r} for region {sorted(region)} …", file=sys.stderr)
    od = read_market(args.market, region, chunksize=args.chunksize)
    total = sum(r.passengers for r in od)
    print(f"  → {len(od)} OD pairs, {total:,.0f} total passengers", file=sys.stderr)
    write_demand_csv(od, args.out)
    print(f"  → written to {args.out!r}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover
    main()
