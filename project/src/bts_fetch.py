"""
bts_fetch.py — reproducible BTS data fetch + manifest.

Public API
----------
fetch_db1b_market(year, quarter, dest_dir) -> pathlib.Path
    Download Origin_and_Destination_Survey_DB1BMarket_{year}_{quarter}.zip
    from the BTS PREZIP server, extract the LARGEST member (the data CSV),
    and save it to dest_dir.  Returns the Path to the saved file.

fetch_t100_segment(year, month, dest_dir) -> pathlib.Path
    Download a T-100 Domestic Segment CSV via the BTS TranStats ASP.NET
    download form.  Scrapes __VIEWSTATE/__VIEWSTATEGENERATOR/__EVENTVALIDATION
    from the form page, then POSTs with the required field checkboxes and
    download parameters.  Extracts the LARGEST member from the returned zip
    and saves it to dest_dir.

fetch_aircraft_lookup(dest_dir) -> pathlib.Path
    Download the L_AIRCRAFT_TYPE lookup CSV from BTS and save to dest_dir.

write_manifest(paths_with_urls, out_json)
    Record source URL, retrieval date (date only, YYYY-MM-DD), byte size,
    and SHA-256 for each file.  Output is deterministic (sorted keys).

verify_manifest(manifest_path)
    Re-check every file listed in the manifest.  Raises ManifestMismatchError
    (subclass of ValueError) loudly on any sha256 or byte_size mismatch.

main(argv=None) -> int
    CLI entry point: ``python -m bts_fetch --all --dest DIR``.

All HTTP requests go through the injectable _open_url() function (a simple
wrapper around urllib.request.urlopen).  Tests override it via
unittest.mock.patch('bts_fetch._open_url', ...) for full offline operation.

Retry behaviour
---------------
All fetch functions retry up to MAX_RETRIES times with exponential back-off
(BACKOFF_BASE ** attempt seconds, starting at 0).  The back-off is applied
via time.sleep(), which tests can mock to avoid waiting.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DB1B_URL_TEMPLATE = (
    "https://transtats.bts.gov/PREZIP/"
    "Origin_and_Destination_Survey_DB1BMarket_{year}_{quarter}.zip"
)

_T100_FORM_URL = (
    "https://www.transtats.bts.gov/DL_SelectFields.aspx"
    "?gnoyr_VQ=GEE&QO_fu146_anzr=Nv4%20Pn44vr45"
)

_AIRCRAFT_LOOKUP_URL = (
    "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVePeNSg_glcR"
)

_T100_WANTED_FIELDS = frozenset([
    "YEAR", "MONTH", "UNIQUE_CARRIER", "ORIGIN", "DEST",
    "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG",
    "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS",
    "DISTANCE", "AIR_TIME",
])

MAX_RETRIES = 5
BACKOFF_BASE = 5  # seconds; attempt i waits BACKOFF_BASE * i seconds


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ManifestMismatchError(ValueError):
    """Raised by verify_manifest when a file's sha256 or size does not match."""


# ---------------------------------------------------------------------------
# Injectable HTTP primitive (tests replace this with mock.patch)
# ---------------------------------------------------------------------------

def _open_url(url: str, data: Optional[bytes] = None, timeout: int = 300):
    """Open a URL via urllib.request.urlopen and return the response object.

    Parameters
    ----------
    url:   Full URL string.
    data:  If not None, a POST body as bytes; otherwise GET.
    timeout: Socket timeout in seconds.

    The return value is a http.client.HTTPResponse (or compatible mock).
    Tests replace this function entirely via mock.patch('bts_fetch._open_url').
    """
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": "Mozilla/5.0 (bts_fetch/1.0)"},
    )
    return urllib.request.urlopen(req, timeout=timeout)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _retry(fn, *, max_retries: int = MAX_RETRIES):
    """Call fn(); retry up to max_retries times on OSError with back-off.

    Back-off: sleep BACKOFF_BASE * attempt seconds between attempts.
    On final failure re-raises the last exception.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            return fn()
        except OSError as exc:
            last_exc = exc
            wait = BACKOFF_BASE * attempt
            if wait > 0:
                time.sleep(wait)
    assert last_exc is not None
    raise last_exc


def _extract_largest_member(zip_bytes: bytes) -> bytes:
    """Return the content of the largest (by uncompressed size) zip member."""
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    largest = max(zf.infolist(), key=lambda info: info.file_size)
    return zf.read(largest.filename)


def _scrape_aspnet_fields(html: bytes) -> dict[str, str]:
    """Scrape ASP.NET hidden form fields from an HTML page.

    Returns a dict with keys: __VIEWSTATE, __VIEWSTATEGENERATOR,
    __EVENTVALIDATION, __EVENTTARGET, __EVENTARGUMENT, __LASTFOCUS.
    Missing fields default to "".
    """
    html_str = html.decode("utf-8", errors="replace")
    fields = {}
    for name in (
        "__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
        "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS",
    ):
        m = re.search(
            r'<input[^>]*name="%s"[^>]*value="([^"]*)"' % re.escape(name),
            html_str,
            re.IGNORECASE | re.DOTALL,
        )
        fields[name] = m.group(1) if m else ""
    return fields


def _scrape_checkbox_names(html: bytes) -> list[str]:
    """Return all checkbox input names found in the HTML page."""
    html_str = html.decode("utf-8", errors="replace")
    return re.findall(
        r'<input[^>]*type=["\']?checkbox["\']?[^>]*name="([^"]+)"',
        html_str,
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

def fetch_db1b_market(
    year: int,
    quarter: int,
    dest_dir: pathlib.Path | str,
) -> pathlib.Path:
    """Download and extract DB1B Market data for *year* / *quarter*.

    Downloads Origin_and_Destination_Survey_DB1BMarket_{year}_{quarter}.zip
    from the BTS PREZIP server, extracts the LARGEST member (the data CSV —
    the zip also contains readme.html), and writes it to *dest_dir*.

    Returns the Path to the saved CSV file.

    Raises OSError after MAX_RETRIES failed attempts.
    """
    dest_dir = pathlib.Path(dest_dir)
    url = _DB1B_URL_TEMPLATE.format(year=year, quarter=quarter)

    def _do_fetch():
        resp = _open_url(url)
        return resp.read()

    zip_bytes = _retry(_do_fetch)
    csv_bytes = _extract_largest_member(zip_bytes)

    # Derive a clean filename from the URL
    out_name = f"Origin_and_Destination_Survey_DB1BMarket_{year}_{quarter}.csv"
    out_path = dest_dir / out_name
    out_path.write_bytes(csv_bytes)
    return out_path


def fetch_t100_segment(
    year: int,
    month: int,
    dest_dir: pathlib.Path | str,
) -> pathlib.Path:
    """Download and extract T-100 Domestic Segment data for *year* / *month*.

    Workflow:
    1. GET the BTS TranStats DL_SelectFields form page.
    2. Scrape __VIEWSTATE, __VIEWSTATEGENERATOR, __EVENTVALIDATION.
    3. POST with cboGeography=All, cboYear, cboPeriod=month,
       btnDownload=Download, chkDownloadZip=on, and only the field
       checkboxes in _T100_WANTED_FIELDS.
    4. The response is a zip; extract the LARGEST member (the data CSV;
       the zip also contains Documentation.csv — real reviewer bug).
    5. Write the CSV to dest_dir.

    Each HTTP call is retried independently via _retry().
    Returns the Path to the saved CSV file.
    """
    dest_dir = pathlib.Path(dest_dir)

    # Step 1: GET the form page
    def _get_form():
        resp = _open_url(_T100_FORM_URL)
        return resp.read()

    html = _retry(_get_form)

    # Step 2: scrape hidden fields and checkbox names
    hidden_fields = _scrape_aspnet_fields(html)
    checkbox_names = _scrape_checkbox_names(html)

    # Step 3: build POST body
    form: dict[str, str] = {}
    form.update(hidden_fields)
    form["cboGeography"] = "All"
    form["cboYear"] = str(year)
    form["cboPeriod"] = str(month)
    form["btnDownload"] = "Download"
    form["chkDownloadZip"] = "on"

    # Only include checkboxes that are in the wanted set
    for cb_name in checkbox_names:
        if cb_name in _T100_WANTED_FIELDS:
            form[cb_name] = "on"

    post_body = urllib.parse.urlencode(form).encode("utf-8")

    def _do_post():
        resp = _open_url(_T100_FORM_URL, data=post_body)
        return resp.read()

    zip_bytes = _retry(_do_post)
    csv_bytes = _extract_largest_member(zip_bytes)

    out_name = f"T_T100_SEGMENT_ALL_CARRIER_{year}_{month:02d}.csv"
    out_path = dest_dir / out_name
    out_path.write_bytes(csv_bytes)
    return out_path


def fetch_aircraft_lookup(dest_dir: pathlib.Path | str) -> pathlib.Path:
    """Download the BTS L_AIRCRAFT_TYPE lookup CSV.

    Saves to dest_dir/L_AIRCRAFT_TYPE.csv.
    Returns the Path to the saved file.
    """
    dest_dir = pathlib.Path(dest_dir)

    def _do_fetch():
        resp = _open_url(_AIRCRAFT_LOOKUP_URL)
        return resp.read()

    csv_bytes = _retry(_do_fetch)

    out_path = dest_dir / "L_AIRCRAFT_TYPE.csv"
    out_path.write_bytes(csv_bytes)
    return out_path


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def write_manifest(
    paths_with_urls: dict[str, str],
    out_json: pathlib.Path | str,
) -> None:
    """Write a JSON manifest for the given files.

    Parameters
    ----------
    paths_with_urls:
        Mapping from file path (str or Path) to its source URL.
    out_json:
        Destination for the JSON manifest file.

    The manifest has the structure::

        {
          "files": {
            "<basename>": {
              "source_url": "<url>",
              "retrieval_date": "YYYY-MM-DD",
              "byte_size": <int>,
              "sha256": "<hex>"
            },
            ...
          }
        }

    Keys are always sorted for determinism.  retrieval_date is today's
    date (UTC calendar date from date.today()).
    """
    out_json = pathlib.Path(out_json)
    today = date.today().isoformat()

    files_dict: dict[str, dict] = {}
    for path_str, source_url in sorted(paths_with_urls.items()):
        p = pathlib.Path(path_str)
        raw = p.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        size = len(raw)
        files_dict[p.name] = {
            "source_url": source_url,
            "retrieval_date": today,
            "byte_size": size,
            "sha256": sha,
        }

    manifest = {"files": files_dict}
    out_json.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def verify_manifest(manifest_path: pathlib.Path | str) -> None:
    """Verify all files listed in a manifest.

    Re-computes sha256 and byte_size for each file relative to the manifest's
    directory and compares against the recorded values.

    Raises ManifestMismatchError (subclass of ValueError) on any mismatch.
    Raises FileNotFoundError if a listed file does not exist.
    """
    manifest_path = pathlib.Path(manifest_path)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    base_dir = manifest_path.parent

    for fname, entry in data["files"].items():
        fpath = base_dir / fname
        if not fpath.exists():
            raise FileNotFoundError(
                f"Manifest references '{fname}' but file not found at {fpath}"
            )
        raw = fpath.read_bytes()
        actual_size = len(raw)
        actual_sha = hashlib.sha256(raw).hexdigest()

        if actual_size != entry["byte_size"]:
            raise ManifestMismatchError(
                f"Size mismatch for '{fname}': "
                f"manifest={entry['byte_size']}, actual={actual_size}"
            )
        if actual_sha != entry["sha256"]:
            raise ManifestMismatchError(
                f"SHA-256 mismatch for '{fname}': "
                f"manifest={entry['sha256']!r}, actual={actual_sha!r}"
            )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    """CLI: python -m bts_fetch --all --dest DIR [--year Y] [--quarter Q] [--month M]

    Options
    -------
    --all           Fetch all supported datasets (DB1B Q1-Q4, T-100 months 1-12,
                    and aircraft lookup) for the configured year.
    --dest DIR      Directory to save downloaded files (default: ../../data/bts-cache
                    relative to this script).
    --year Y        Year to fetch (default: 2019).
    --quarter Q     If --all is not given, fetch DB1B for this quarter only.
    --month M       If --all is not given, fetch T-100 for this month only.
    --manifest PATH Write (or overwrite) a manifest JSON to this path.
    """
    parser = argparse.ArgumentParser(
        prog="bts_fetch",
        description="Fetch BTS airline data and write a reproducibility manifest.",
    )
    parser.add_argument("--dest", default=None,
                        help="Destination directory (default: ../../data/bts-cache).")
    parser.add_argument("--year", type=int, default=2019,
                        help="Year to fetch (default: 2019).")
    parser.add_argument("--all", action="store_true", dest="fetch_all",
                        help="Fetch all supported datasets for the year.")
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4],
                        help="DB1B quarter to fetch (1–4).")
    parser.add_argument("--month", type=int,
                        help="T-100 month to fetch (1–12).")
    parser.add_argument("--manifest", default=None,
                        help="Path to write the manifest JSON.")

    args = parser.parse_args(argv)

    # Resolve dest_dir
    if args.dest is not None:
        dest_dir = pathlib.Path(args.dest)
    else:
        # default: two levels up from project/src → root, then data/bts-cache
        dest_dir = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "bts-cache"
    dest_dir.mkdir(parents=True, exist_ok=True)

    fetched: dict[str, str] = {}  # path -> source_url

    if args.fetch_all:
        for q in range(1, 5):
            print(f"Fetching DB1B Market {args.year} Q{q} …", flush=True)
            try:
                p = fetch_db1b_market(args.year, q, dest_dir)
                fetched[str(p)] = _DB1B_URL_TEMPLATE.format(year=args.year, quarter=q)
                print(f"  → {p}", flush=True)
            except Exception as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)

        for m in range(1, 13):
            print(f"Fetching T-100 Segment {args.year}-{m:02d} …", flush=True)
            try:
                p = fetch_t100_segment(args.year, m, dest_dir)
                fetched[str(p)] = _T100_FORM_URL
                print(f"  → {p}", flush=True)
            except Exception as exc:
                print(f"  ERROR: {exc}", file=sys.stderr)

        print("Fetching Aircraft Lookup …", flush=True)
        try:
            p = fetch_aircraft_lookup(dest_dir)
            fetched[str(p)] = _AIRCRAFT_LOOKUP_URL
            print(f"  → {p}", flush=True)
        except Exception as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)

    else:
        if args.quarter is not None:
            print(f"Fetching DB1B Market {args.year} Q{args.quarter} …", flush=True)
            p = fetch_db1b_market(args.year, args.quarter, dest_dir)
            fetched[str(p)] = _DB1B_URL_TEMPLATE.format(year=args.year, quarter=args.quarter)
            print(f"  → {p}", flush=True)

        if args.month is not None:
            print(f"Fetching T-100 Segment {args.year}-{args.month:02d} …", flush=True)
            p = fetch_t100_segment(args.year, args.month, dest_dir)
            fetched[str(p)] = _T100_FORM_URL
            print(f"  → {p}", flush=True)

        if args.quarter is None and args.month is None and not args.fetch_all:
            parser.print_help()
            return 1

    if fetched:
        manifest_path_str = args.manifest
        if manifest_path_str is None:
            manifest_path_str = str(dest_dir.parent / "bts-manifest.json")
        manifest_path = pathlib.Path(manifest_path_str)
        write_manifest(fetched, manifest_path)
        print(f"Manifest written to {manifest_path}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
