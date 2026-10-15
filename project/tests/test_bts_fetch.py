"""
Tests for project/src/bts_fetch.py: reproducible BTS data fetch + manifest.

Test kinds:
  - example: concrete single-case assertions with exact/range checks
  - property: invariant over multiple inputs or corpus-level assertions
  - metamorphic: result transforms predictably under input changes
  - negative: error conditions produce the right exception/message

All HTTP-touching paths use dependency injection (fake openers / fake
responses) — NO real network calls anywhere in this file.

Tests that need the data/bts-cache/ files are marked @pytest.mark.skipif
at function level with @pytest.mark.skipif (module-level skips with
allow_module_level are banned by test_no_silent_skips.py).

BTS T-100 Jan-2019 magnitude sanity:
  reviewer measured 58.6 M domestic passengers (AIRCRAFT_CONFIG==1).
  Lower bound 50 M (allows ~15% undercount from sampling/rounding).
  Upper bound 70 M (catches 2× double-count or wrong CONFIG filter).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import pathlib
import struct
import time
import zipfile
from datetime import date
from typing import Any
from unittest import mock

import pytest

# ── locate helpers ──────────────────────────────────────────────────────────
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
_CACHE_DIR = _THIS_DIR.parent.parent / "data" / "bts-cache"

# Inline sys.path update so the module is importable
import sys
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from bts_fetch import (
    fetch_db1b_market,
    fetch_t100_segment,
    fetch_aircraft_lookup,
    write_manifest,
    verify_manifest,
    ManifestMismatchError,
)

# ── skip predicate for cache-requiring tests ────────────────────────────────
_CACHE_PRESENT = (
    (_CACHE_DIR / "t100_2019_01.csv").exists()
    and (_CACHE_DIR / "L_AIRCRAFT_TYPE.csv").exists()
    and (_CACHE_DIR / "DB1BMarket_2019_1.zip").exists()
)
_SKIP_NO_CACHE = pytest.mark.skipif(
    not _CACHE_PRESENT,
    reason="data/bts-cache/ files absent; skipping live-data magnitude test",
)

# ── fake zip builders ────────────────────────────────────────────────────────

def _make_zip(members: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP with the given {name: content} dict."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _fake_response(body: bytes, status: int = 200, content_type: str = "application/zip"):
    """Return a mock response object compatible with urllib opener.open()."""
    resp = mock.MagicMock()
    resp.status = status
    resp.read.return_value = body
    resp.headers = {"Content-Type": content_type}
    return resp


# ════════════════════════════════════════════════════════════════════════════
# 1. fetch_db1b_market — URL construction, largest-member selection, retry
# ════════════════════════════════════════════════════════════════════════════

class TestFetchDb1bMarket:

    def test_requests_correct_url(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — exact string match, no tolerance.

        fetch_db1b_market(2019, 1, dest_dir) must request the canonical
        BTS PREZIP URL for DB1B Market Q1 2019.  Any deviation means the
        wrong file is fetched from BTS.

        Cannot catch: a correct URL that times out or returns wrong content.
        """
        csv_bytes = b"ItinID,MktID,Passengers\n1,2,3\n"
        zip_body = _make_zip({"Origin_and_Destination_Survey_DB1BMarket_2019_1.csv": csv_bytes})
        fake_resp = _fake_response(zip_body)

        with mock.patch("bts_fetch._open_url", return_value=fake_resp) as m_open:
            fetch_db1b_market(2019, 1, tmp_path)

        called_url = m_open.call_args[0][0]
        expected = (
            "https://transtats.bts.gov/PREZIP/"
            "Origin_and_Destination_Survey_DB1BMarket_2019_1.zip"
        )
        assert called_url == expected, (
            f"fetch_db1b_market called wrong URL:\n  got  {called_url!r}\n"
            f"  want {expected!r}"
        )

    def test_extracts_largest_member(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — largest-member selection, no numeric tolerance.

        The BTS PREZIP zip contains both the data CSV AND a readme.html.
        fetch_db1b_market must extract the LARGEST member (by uncompressed
        size), not the first alphabetical member.  This replicates the real
        bug the reviewer encountered.

        Cannot catch: largest member that happens to be alphabetically first.
        """
        small = b"readme: use at your own risk\n"
        large = b"ItinID,MktID,Passengers\n" + b"1,2,3\n" * 5000
        zip_body = _make_zip({
            "readme.html": small,
            "Origin_and_Destination_Survey_DB1BMarket_2019_1.csv": large,
        })
        fake_resp = _fake_response(zip_body)

        with mock.patch("bts_fetch._open_url", return_value=fake_resp):
            result = fetch_db1b_market(2019, 1, tmp_path)

        # The returned path should contain the large CSV content
        assert result.exists(), "fetch_db1b_market must return path to extracted file"
        content = result.read_bytes()
        assert content == large, (
            "fetch_db1b_market extracted the wrong (non-largest) zip member"
        )

    def test_returns_path_in_dest_dir(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — path is under dest_dir, no tolerance.

        The returned pathlib.Path must be a direct child of dest_dir so callers
        can pass it to write_manifest without guessing the filename.

        Cannot catch: a path that exists but is not directly under dest_dir.
        """
        csv_bytes = b"col1,col2\n1,2\n"
        zip_body = _make_zip({"data.csv": csv_bytes})
        fake_resp = _fake_response(zip_body)

        with mock.patch("bts_fetch._open_url", return_value=fake_resp):
            result = fetch_db1b_market(2019, 1, tmp_path)

        assert result.parent.resolve() == tmp_path.resolve(), (
            f"Returned path {result} is not inside dest_dir {tmp_path}"
        )

    def test_retries_on_failure(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — retry count, no numeric tolerance.

        _open_url is called multiple times when it raises ConnectionResetError
        on the first attempt.  We inject one failure followed by a success.
        fetch_db1b_market must return the extracted file (not propagate the
        first error).

        Cannot catch: retries that succeed but return stale cached data.
        """
        csv_bytes = b"ItinID,Passengers\n1,50\n"
        zip_body = _make_zip({"data.csv": csv_bytes})
        success_resp = _fake_response(zip_body)

        side_effects = [ConnectionResetError("server reset"), success_resp]
        with mock.patch("bts_fetch._open_url", side_effect=side_effects) as m_open:
            # Patch sleep to avoid waiting in tests
            with mock.patch("time.sleep"):
                result = fetch_db1b_market(2019, 1, tmp_path)

        assert m_open.call_count == 2, (
            f"Expected 2 _open_url calls (1 failure + 1 success), got {m_open.call_count}"
        )
        assert result.exists()

    def test_raises_after_max_retries(self, tmp_path: pathlib.Path) -> None:
        """
        Negative test — error propagation, no tolerance.

        After exhausting all retries (all calls raise), fetch_db1b_market must
        raise (OSError or a subclass) so the caller knows the fetch failed.

        Cannot catch: a silent failure that returns a partial or empty file.
        """
        with mock.patch(
            "bts_fetch._open_url", side_effect=ConnectionResetError("always fails")
        ):
            with mock.patch("time.sleep"):
                with pytest.raises(OSError):
                    fetch_db1b_market(2019, 1, tmp_path)

    def test_url_uses_correct_quarter(self, tmp_path: pathlib.Path) -> None:
        """
        Metamorphic test — URL changes with quarter, no tolerance.

        Quarter 2 must produce a URL ending in …_2019_2.zip.
        Quarter 4 must produce a URL ending in …_2019_4.zip.

        Cannot catch: wrong year encoded alongside wrong quarter.
        """
        csv_bytes = b"a,b\n1,2\n"
        zip_body = _make_zip({"data.csv": csv_bytes})

        for quarter, suffix in [(2, "_2019_2.zip"), (4, "_2019_4.zip")]:
            fake_resp = _fake_response(zip_body)
            with mock.patch("bts_fetch._open_url", return_value=fake_resp) as m_open:
                fetch_db1b_market(2019, quarter, tmp_path)
            called_url = m_open.call_args[0][0]
            assert called_url.endswith(suffix), (
                f"Quarter {quarter}: URL should end with {suffix!r}, got {called_url!r}"
            )


# ════════════════════════════════════════════════════════════════════════════
# 2. fetch_t100_segment — form scraping, field checkboxes, largest member
# ════════════════════════════════════════════════════════════════════════════

# A minimal but structurally complete fake BTS form page.
_FAKE_BTS_HTML = b"""
<html><body>
<form method="POST">
  <input type="hidden" name="__VIEWSTATE" value="vs_abc123" />
  <input type="hidden" name="__VIEWSTATEGENERATOR" value="vsg_xyz" />
  <input type="hidden" name="__EVENTVALIDATION" value="ev_qwerty" />
  <input type="hidden" name="__EVENTTARGET" value="" />
  <input type="hidden" name="__EVENTARGUMENT" value="" />
  <input type="hidden" name="__LASTFOCUS" value="" />
  <input type="checkbox" name="DEPARTURES_PERFORMED" />
  <input type="checkbox" name="SEATS" />
  <input type="checkbox" name="PASSENGERS" />
  <input type="checkbox" name="DISTANCE" />
  <input type="checkbox" name="AIR_TIME" />
  <input type="checkbox" name="UNIQUE_CARRIER" />
  <input type="checkbox" name="ORIGIN" />
  <input type="checkbox" name="DEST" />
  <input type="checkbox" name="AIRCRAFT_TYPE" />
  <input type="checkbox" name="AIRCRAFT_CONFIG" />
  <input type="checkbox" name="YEAR" />
  <input type="checkbox" name="MONTH" />
  <input type="checkbox" name="IRRELEVANT_FIELD" />
</form>
</body></html>
"""

_T100_CSV_CONTENT = (
    b"DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
    b"UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
    b"10.0,1500.0,1200.0,500.0,120.0,AA,BOS,ORD,614,1,2019,1\n"
)


class TestFetchT100Segment:

    def _setup_mocks(self, tmp_path, html=None, csv_content=None, year=2019, month=1):
        """Helper: wire up _open_url side_effect for GET (HTML) then POST (zip)."""
        if html is None:
            html = _FAKE_BTS_HTML
        if csv_content is None:
            csv_content = _T100_CSV_CONTENT

        zip_body = _make_zip({
            "Documentation.csv": b"doc,info\n",
            "T_T100_SEGMENT_ALL_CARRIER.csv": csv_content,
        })
        get_resp = _fake_response(html, content_type="text/html")
        post_resp = _fake_response(zip_body, content_type="application/zip")

        side_effects = [get_resp, post_resp]
        return side_effects

    def test_get_form_url_is_correct(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — exact URL on first GET, no tolerance.

        The first call to _open_url must be a GET to the BTS DL_SelectFields URL
        for T-100 Domestic Segment.

        Cannot catch: correct URL with wrong session handling.
        """
        sides = self._setup_mocks(tmp_path)
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                fetch_t100_segment(2019, 1, tmp_path)

        first_call_url = m_open.call_args_list[0][0][0]
        expected_prefix = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
        assert first_call_url.startswith(expected_prefix), (
            f"First GET URL should start with {expected_prefix!r}, got {first_call_url!r}"
        )

    def test_post_includes_all_required_fields(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — POST body contains required field names, no tolerance.

        The POST body must include the ASP.NET hidden fields, the geography/
        year/period dropdowns, the download button, the zip checkbox, and
        ALL twelve column checkboxes.

        Cannot catch: fields present but with wrong values.
        """
        sides = self._setup_mocks(tmp_path)
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                fetch_t100_segment(2019, 1, tmp_path)

        # Find the POST call (second _open_url call, has data= kwarg)
        post_call = m_open.call_args_list[1]
        # data might be positional or keyword
        post_data = post_call[0][1] if len(post_call[0]) > 1 else post_call[1].get("data", b"")
        if isinstance(post_data, bytes):
            post_str = post_data.decode("utf-8")
        else:
            post_str = str(post_data)

        required_fields = [
            "__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION",
            "cboGeography", "cboYear", "cboPeriod",
            "btnDownload", "chkDownloadZip",
            "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS", "DISTANCE",
            "AIR_TIME", "UNIQUE_CARRIER", "ORIGIN", "DEST",
            "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG", "YEAR", "MONTH",
        ]
        missing = [f for f in required_fields if f not in post_str]
        assert not missing, (
            f"POST body missing required fields: {missing}\n"
            f"POST body (first 500 chars): {post_str[:500]}"
        )

    def test_post_does_not_include_irrelevant_checkbox(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — field exclusion, no tolerance.

        Checkboxes NOT in the wanted list (e.g. IRRELEVANT_FIELD from our fake
        HTML) must NOT appear in the POST body.  Sending extra checkboxes can
        cause the BTS server to return unexpected columns.

        Cannot catch: irrelevant fields that happen to share a prefix with
        required fields (would need more precise parsing).
        """
        sides = self._setup_mocks(tmp_path)
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                fetch_t100_segment(2019, 1, tmp_path)

        post_call = m_open.call_args_list[1]
        post_data = post_call[0][1] if len(post_call[0]) > 1 else post_call[1].get("data", b"")
        if isinstance(post_data, bytes):
            post_str = post_data.decode("utf-8")
        else:
            post_str = str(post_data)

        assert "IRRELEVANT_FIELD" not in post_str, (
            f"POST body should not contain IRRELEVANT_FIELD, got:\n{post_str[:500]}"
        )

    def test_post_year_and_month_match_args(self, tmp_path: pathlib.Path) -> None:
        """
        Metamorphic test — year and month encoded in POST, no tolerance.

        Calling fetch_t100_segment(2019, 3, ...) must encode cboYear=2019
        and cboPeriod=3 (not cboPeriod=03 or cboPeriod=1).

        Cannot catch: values that look correct but are URL-encoded differently.
        """
        import urllib.parse
        sides = self._setup_mocks(tmp_path, year=2019, month=3)
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                fetch_t100_segment(2019, 3, tmp_path)

        post_call = m_open.call_args_list[1]
        post_data = post_call[0][1] if len(post_call[0]) > 1 else post_call[1].get("data", b"")
        if isinstance(post_data, bytes):
            post_str = post_data.decode("utf-8")
        else:
            post_str = str(post_data)

        params = dict(urllib.parse.parse_qsl(post_str))
        assert params.get("cboYear") == "2019", (
            f"Expected cboYear=2019, got {params.get('cboYear')!r}"
        )
        assert params.get("cboPeriod") == "3", (
            f"Expected cboPeriod=3, got {params.get('cboPeriod')!r}"
        )

    def test_post_cbo_geography_is_all(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — cboGeography must be 'All', no tolerance.

        The BTS form's cboGeography field must be set to 'All' (not 'Domestic'
        or any other value) to retrieve all domestic-segment data.  A wrong
        value causes the server to return an empty or filtered dataset.

        Cannot catch: 'All' being the right string but wrong case for the BTS
        server (the server is case-sensitive on this dropdown value).
        """
        import urllib.parse
        sides = self._setup_mocks(tmp_path)
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                fetch_t100_segment(2019, 1, tmp_path)

        post_call = m_open.call_args_list[1]
        post_data = post_call[0][1] if len(post_call[0]) > 1 else post_call[1].get("data", b"")
        if isinstance(post_data, bytes):
            post_str = post_data.decode("utf-8")
        else:
            post_str = str(post_data)

        params = dict(urllib.parse.parse_qsl(post_str))
        assert params.get("cboGeography") == "All", (
            f"Expected cboGeography=All, got {params.get('cboGeography')!r}. "
            "A non-All value causes BTS to return wrong data."
        )

    def test_extracts_largest_member_from_response_zip(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — largest-member selection, no tolerance.

        The response zip from BTS contains Documentation.csv (small) AND the
        data CSV (large).  fetch_t100_segment must extract the LARGEST member.
        This was a real bug the reviewer hit.

        Cannot catch: largest member that accidentally matches a doc file name.
        """
        doc = b"This is short documentation.\n"
        big_csv = b"COL1,COL2\n" + b"1,2\n" * 10000
        zip_body = _make_zip({"Documentation.csv": doc, "data_big.csv": big_csv})
        get_resp = _fake_response(_FAKE_BTS_HTML, content_type="text/html")
        post_resp = _fake_response(zip_body, content_type="application/zip")

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_resp]):
            with mock.patch("time.sleep"):
                result = fetch_t100_segment(2019, 1, tmp_path)

        assert result.read_bytes() == big_csv, (
            "fetch_t100_segment did not extract the largest zip member"
        )

    def test_retries_post_on_connection_reset(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — POST retry, no tolerance.

        A ConnectionResetError on the POST call (not the GET) must be retried.
        The GET succeeds first; then the POST fails once; then succeeds.

        Cannot catch: retry that re-fetches the form (re-GET) vs reuses session.
        """
        zip_body = _make_zip({"data.csv": _T100_CSV_CONTENT})
        get_resp = _fake_response(_FAKE_BTS_HTML, content_type="text/html")
        post_fail = ConnectionResetError("POST reset")
        post_ok = _fake_response(zip_body, content_type="application/zip")

        sides = [get_resp, post_fail, post_ok]
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                result = fetch_t100_segment(2019, 1, tmp_path)

        assert result.exists()
        assert m_open.call_count >= 3, (
            f"Expected ≥3 _open_url calls (1 GET + 1 fail POST + 1 ok POST), "
            f"got {m_open.call_count}"
        )

    def test_scrapes_viewstate_from_html(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — ASP.NET hidden field scraping, no tolerance.

        The __VIEWSTATE scraped from the fake HTML ('vs_abc123') must appear
        in the POST body.  Without this the BTS server rejects the POST.

        Cannot catch: a VIEWSTATE that is present but double-encoded or mangled.
        """
        sides = self._setup_mocks(tmp_path)
        with mock.patch("bts_fetch._open_url", side_effect=sides) as m_open:
            with mock.patch("time.sleep"):
                fetch_t100_segment(2019, 1, tmp_path)

        post_call = m_open.call_args_list[1]
        post_data = post_call[0][1] if len(post_call[0]) > 1 else post_call[1].get("data", b"")
        if isinstance(post_data, bytes):
            post_str = post_data.decode("utf-8")
        else:
            post_str = str(post_data)

        import urllib.parse
        params = dict(urllib.parse.parse_qsl(post_str))
        assert params.get("__VIEWSTATE") == "vs_abc123", (
            f"__VIEWSTATE not correctly scraped; got {params.get('__VIEWSTATE')!r}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 3. fetch_aircraft_lookup — URL and file storage
# ════════════════════════════════════════════════════════════════════════════

class TestFetchAircraftLookup:

    def test_requests_correct_url(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — exact URL, no tolerance.

        fetch_aircraft_lookup must GET the BTS lookup URL for L_AIRCRAFT_TYPE.

        Cannot catch: correct URL that silently returns wrong lookup table.
        """
        csv_bytes = b"Code,Description\n614,Boeing 737-100\n"
        fake_resp = _fake_response(csv_bytes, content_type="text/csv")

        with mock.patch("bts_fetch._open_url", return_value=fake_resp) as m_open:
            fetch_aircraft_lookup(tmp_path)

        called_url = m_open.call_args[0][0]
        expected = "https://www.transtats.bts.gov/Download_Lookup.asp?Y11x72=Y_NVePeNSg_glcR"
        assert called_url == expected, (
            f"fetch_aircraft_lookup called wrong URL:\n  got  {called_url!r}\n"
            f"  want {expected!r}"
        )

    def test_writes_csv_to_dest_dir(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — file written, no tolerance.

        The returned path must exist and contain the response body.

        Cannot catch: file with correct name but wrong content.
        """
        csv_bytes = b"Code,Description\n614,Boeing 737-100\n"
        fake_resp = _fake_response(csv_bytes, content_type="text/csv")

        with mock.patch("bts_fetch._open_url", return_value=fake_resp):
            result = fetch_aircraft_lookup(tmp_path)

        assert result.exists(), "fetch_aircraft_lookup must write the CSV file"
        assert result.read_bytes() == csv_bytes, "Written CSV does not match response body"
        assert result.parent.resolve() == tmp_path.resolve()

    def test_retries_on_failure(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — retry on network error, no tolerance.

        A single OSError on the first call must be retried.

        Cannot catch: a retry that silently drops content.
        """
        csv_bytes = b"Code,Description\n614,Boeing 737-100\n"
        fake_resp = _fake_response(csv_bytes, content_type="text/csv")

        with mock.patch(
            "bts_fetch._open_url",
            side_effect=[OSError("connection error"), fake_resp],
        ) as m_open:
            with mock.patch("time.sleep"):
                result = fetch_aircraft_lookup(tmp_path)

        assert m_open.call_count == 2
        assert result.exists()


# ════════════════════════════════════════════════════════════════════════════
# 4. write_manifest and verify_manifest
# ════════════════════════════════════════════════════════════════════════════

class TestManifest:

    def _make_files(self, tmp_path: pathlib.Path) -> dict[str, pathlib.Path]:
        """Create two small test files and return {name: path}."""
        files = {}
        for name, content in [("alpha.csv", b"a,b\n1,2\n"), ("beta.zip", b"PK\x03\x04fakebody")]:
            p = tmp_path / name
            p.write_bytes(content)
            files[name] = p
        return files

    def test_write_manifest_creates_json(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — manifest file structure, no tolerance.

        write_manifest(paths, out_json) must create a valid JSON file with
        keys: 'files' mapping filename -> {source_url, retrieval_date, byte_size, sha256}.

        Cannot catch: correct structure but wrong values inside each record.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {
            str(files["alpha.csv"]): "https://example.com/alpha.csv",
            str(files["beta.zip"]): "https://example.com/beta.zip",
        }
        write_manifest(paths_with_meta, out_json)

        assert out_json.exists()
        data = json.loads(out_json.read_text())
        assert "files" in data, f"manifest missing 'files' key: {list(data.keys())}"
        assert "alpha.csv" in data["files"]
        assert "beta.zip" in data["files"]

    def test_manifest_records_required_fields(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — per-file fields, no tolerance.

        Each file entry in manifest['files'] must have:
          source_url, retrieval_date, byte_size, sha256.

        Cannot catch: correct key names but wrong types.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {
            str(files["alpha.csv"]): "https://example.com/alpha.csv",
        }
        write_manifest(paths_with_meta, out_json)

        data = json.loads(out_json.read_text())
        entry = data["files"]["alpha.csv"]
        for field in ("source_url", "retrieval_date", "byte_size", "sha256"):
            assert field in entry, f"manifest entry missing field: {field!r}"

    def test_manifest_sha256_is_correct(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — SHA-256 value, no tolerance (exact).

        The sha256 in the manifest must equal hashlib.sha256 of the file bytes.

        Cannot catch: hash collision (negligible for SHA-256).
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {str(files["alpha.csv"]): "https://example.com/alpha.csv"}
        write_manifest(paths_with_meta, out_json)

        data = json.loads(out_json.read_text())
        expected_sha = hashlib.sha256(files["alpha.csv"].read_bytes()).hexdigest()
        assert data["files"]["alpha.csv"]["sha256"] == expected_sha

    def test_manifest_byte_size_is_correct(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — byte size, no tolerance.

        byte_size must match the actual file size in bytes.

        Cannot catch: off-by-one that is coincidentally equal to file size.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {str(files["alpha.csv"]): "https://example.com/alpha.csv"}
        write_manifest(paths_with_meta, out_json)

        data = json.loads(out_json.read_text())
        actual_size = files["alpha.csv"].stat().st_size
        assert data["files"]["alpha.csv"]["byte_size"] == actual_size

    def test_manifest_source_url_is_correct(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — source_url recorded verbatim, no tolerance.

        Cannot catch: URL that is stored but not used for re-fetching.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        url = "https://example.com/alpha_specific.csv"
        paths_with_meta = {str(files["alpha.csv"]): url}
        write_manifest(paths_with_meta, out_json)

        data = json.loads(out_json.read_text())
        assert data["files"]["alpha.csv"]["source_url"] == url

    def test_manifest_retrieval_date_is_iso_date_string(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — date format YYYY-MM-DD, no tolerance.

        retrieval_date must be a string matching YYYY-MM-DD (date only, no time
        component) so manifests on different machines only differ on the date,
        not the time of day.

        Cannot catch: a correct date that drifts by 1 day across timezones
        if the server is in a different TZ.
        """
        import re
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {str(files["alpha.csv"]): "https://example.com/alpha.csv"}
        write_manifest(paths_with_meta, out_json)

        data = json.loads(out_json.read_text())
        date_str = data["files"]["alpha.csv"]["retrieval_date"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_str), (
            f"retrieval_date must be YYYY-MM-DD, got {date_str!r}"
        )

    def test_verify_manifest_passes_on_valid_files(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — happy path of verify_manifest, no tolerance.

        verify_manifest(manifest_path) must not raise when all files listed in
        the manifest have the correct sha256 and byte_size.

        Cannot catch: a verify that passes but does not actually read the files.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {
            str(files["alpha.csv"]): "https://example.com/alpha.csv",
            str(files["beta.zip"]): "https://example.com/beta.zip",
        }
        write_manifest(paths_with_meta, out_json)

        # Must not raise
        verify_manifest(out_json)

    def test_verify_manifest_raises_on_tampered_content(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — tampered file detection, no tolerance.

        After write_manifest, if alpha.csv content is changed to a SAME-SIZE
        replacement, verify_manifest must raise ManifestMismatchError because
        the sha256 differs.  We write content of identical byte length so the
        byte_size check cannot mask the sha256 check.

        Cannot catch: tampering of the manifest JSON itself (not the data file).
        """
        # Use a file whose content can be replaced with same-byte-length content
        p = tmp_path / "alpha.csv"
        original = b"a,b\n1,2\n"  # 8 bytes
        p.write_bytes(original)

        out_json = tmp_path / "manifest.json"
        paths_with_meta = {str(p): "https://example.com/alpha.csv"}
        write_manifest(paths_with_meta, out_json)

        # Tamper: overwrite with SAME number of bytes but different content
        assert len(original) == 8
        tampered = b"x,y\n9,8\n"  # also 8 bytes, different sha256
        assert len(tampered) == len(original), "test setup: sizes must match to isolate sha256 check"
        p.write_bytes(tampered)

        with pytest.raises((ManifestMismatchError, AssertionError, ValueError)):
            verify_manifest(out_json)

    def test_verify_manifest_raises_on_wrong_size(self, tmp_path: pathlib.Path) -> None:
        """
        Example test — byte-size mismatch detection, no tolerance.

        Append bytes to the file → size changes → verify_manifest must raise.

        Cannot catch: a verify that only checks sha256 and ignores byte_size.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {str(files["alpha.csv"]): "https://example.com/alpha.csv"}
        write_manifest(paths_with_meta, out_json)

        # Make size wrong while preserving first bytes (sha256 also changes, but
        # the test verifies the size check specifically)
        with open(files["alpha.csv"], "ab") as f:
            f.write(b"\nextra\n")

        with pytest.raises((ManifestMismatchError, AssertionError, ValueError)):
            verify_manifest(out_json)

    def test_verify_manifest_raises_on_missing_file(self, tmp_path: pathlib.Path) -> None:
        """
        Negative test — missing file detection, no tolerance.

        If a file listed in the manifest is deleted, verify_manifest must raise
        ManifestMismatchError (or FileNotFoundError / AssertionError / ValueError).

        Cannot catch: a verify that skips missing files silently.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {str(files["alpha.csv"]): "https://example.com/alpha.csv"}
        write_manifest(paths_with_meta, out_json)

        files["alpha.csv"].unlink()

        with pytest.raises((ManifestMismatchError, FileNotFoundError, AssertionError, ValueError)):
            verify_manifest(out_json)

    def test_manifest_round_trip(self, tmp_path: pathlib.Path) -> None:
        """
        Property test — write_manifest then verify_manifest is idempotent, no tolerance.

        For any set of files, write_manifest followed immediately by
        verify_manifest must succeed.  Calling write_manifest again with the
        same inputs and calling verify_manifest again must also succeed (the
        manifest content is deterministic).

        Cannot catch: non-determinism in write_manifest that only shows up
        after the file system caches are flushed.
        """
        files = self._make_files(tmp_path)
        out_json = tmp_path / "manifest.json"
        paths_with_meta = {
            str(files["alpha.csv"]): "https://example.com/alpha.csv",
            str(files["beta.zip"]): "https://example.com/beta.zip",
        }
        write_manifest(paths_with_meta, out_json)
        verify_manifest(out_json)  # must not raise

        # Second write must produce identical JSON
        first_content = out_json.read_bytes()
        write_manifest(paths_with_meta, out_json)
        second_content = out_json.read_bytes()
        assert first_content == second_content, (
            "write_manifest is not deterministic: two calls produce different JSON"
        )

        verify_manifest(out_json)  # still must not raise


# ════════════════════════════════════════════════════════════════════════════
# 5. CLI entry: python -m bts_fetch --all --dest
# ════════════════════════════════════════════════════════════════════════════

class TestCLIEntry:

    def test_cli_module_is_runnable(self) -> None:
        """
        Example test — module __main__ exists and is importable, no tolerance.

        'python -m bts_fetch' must be invokable.  We check the module has a
        main() callable (not that it actually fetches; that would need network).

        Cannot catch: a main() that silently does nothing.
        """
        import bts_fetch
        assert callable(getattr(bts_fetch, "main", None)), (
            "bts_fetch.main must be a callable for `python -m bts_fetch`"
        )

    def test_cli_help_exits_zero(self) -> None:
        """
        Example test — --help exits 0, no tolerance.

        `python -m bts_fetch --help` must exit 0 without hitting the network.

        Cannot catch: help text that is misleading or incomplete.
        """
        import subprocess
        import sys
        result = subprocess.run(
            [sys.executable, "-m", "bts_fetch", "--help"],
            capture_output=True,
            text=True,
            cwd=str(_SRC_DIR),
        )
        assert result.returncode == 0, (
            f"`python -m bts_fetch --help` exited {result.returncode}.\n"
            f"stderr: {result.stderr}"
        )
        assert "--all" in result.stdout or "--dest" in result.stdout, (
            f"--help output missing expected flags.\nstdout: {result.stdout[:500]}"
        )


# ════════════════════════════════════════════════════════════════════════════
# 6. Magnitude sanity (requires bts-cache; skipif absent)
# ════════════════════════════════════════════════════════════════════════════

@_SKIP_NO_CACHE
def test_t100_jan2019_domestic_pax_magnitude() -> None:
    """
    Golden / magnitude sanity test.

    Kind: golden (reviewer-measured reference value).
    Tolerance: [50e6, 70e6] passengers.
    Reason: reviewer computed 58.6 M domestic passengers (AIRCRAFT_CONFIG==1)
    from the full t100_2019_01.csv.  Lower bound 50 M allows ~15% undercount
    from rounding errors or slight scope difference.  Upper bound 70 M catches
    a 2× double-count (e.g., wrong AIRCRAFT_CONFIG filter summing both config
    values) or including international traffic.
    Cannot catch: a 10-20% systematic error in the same direction.

    Implementation note: we read the real t100_2019_01.csv from bts-cache
    using chunked pandas-free CSV reading to avoid importing pandas.
    """
    import csv as _csv

    cache_path = _CACHE_DIR / "t100_2019_01.csv"
    total = 0.0
    with open(cache_path, newline="") as f:
        reader = _csv.DictReader(f)
        for row in reader:
            try:
                config = int(float(row["AIRCRAFT_CONFIG"]))
                if config == 1:
                    total += float(row["PASSENGERS"])
            except (ValueError, KeyError):
                pass

    lo, hi = 50e6, 70e6
    assert lo <= total <= hi, (
        f"T-100 Jan-2019 domestic pax = {total/1e6:.1f} M; expected [{lo/1e6:.0f}M, {hi/1e6:.0f}M]. "
        f"Reviewer reference: 58.6 M.  "
        f"A value outside this range suggests wrong AIRCRAFT_CONFIG filter or double-counting."
    )


class TestManifestBaseDir:
    """Manifest stored apart from the files it describes."""

    def test_verify_manifest_honours_base_dir(self, tmp_path):
        """
        Example + regression test — exact sha256/size match, no tolerance.

        The repository commits data/bts-manifest.json while the files it
        describes live in the git-ignored data/bts-cache/.  verify_manifest
        must resolve files from the manifest's recorded ``base_dir`` (relative
        to the manifest's own directory), not assume they sit next to it.
        Before this test the committed manifest could not verify itself
        (FileNotFoundError on data/DB1BMarket_2019_1.zip).

        Cannot catch: a base_dir that points at the right directory but stale
        copies of the files (that is what the sha256 comparison is for).
        """
        cache = tmp_path / "bts-cache"
        cache.mkdir()
        (cache / "a.csv").write_bytes(b"x,y\n1,2\n")
        manifest = tmp_path / "bts-manifest.json"
        write_manifest({str(cache / "a.csv"): "https://example.invalid/a.csv"}, manifest)
        data = json.loads(manifest.read_text())
        assert data["base_dir"] == "bts-cache"
        assert not pathlib.PurePosixPath(data["base_dir"]).is_absolute()
        verify_manifest(manifest)          # must not raise
        (cache / "a.csv").write_bytes(b"x,y\n1,3\n")  # same size, different content
        with pytest.raises(ManifestMismatchError):
            verify_manifest(manifest)


    def test_base_dir_is_relative_through_a_symlinked_cache(self, tmp_path):
        """
        Regression test (portability) — exact string, no tolerance.

        In practice data/bts-cache may be a symlink to a big local disk.  The
        recorded base_dir must stay the short relative path ('bts-cache'),
        never a path through the symlink target (which leaks the machine's
        home directory into a committed file and breaks on every other
        checkout).  A reviewer hit exactly this: base_dir came out as
        '../../../../../Users/<name>/...'.

        Cannot catch: a symlink loop (filesystem error, not a manifest bug).
        """
        real = tmp_path / "elsewhere" / "big_disk"
        real.mkdir(parents=True)
        (real / "a.csv").write_bytes(b"x\n1\n")
        repo_data = tmp_path / "repo" / "data"
        repo_data.mkdir(parents=True)
        (repo_data / "bts-cache").symlink_to(real, target_is_directory=True)
        manifest = repo_data / "bts-manifest.json"
        write_manifest({str(repo_data / "bts-cache" / "a.csv"): "https://example.invalid/a"}, manifest)
        data = json.loads(manifest.read_text())
        assert data["base_dir"] == "bts-cache"
        assert "elsewhere" not in manifest.read_text()
        verify_manifest(manifest)
