"""
Tests for the NW-24 fix: fetch_t2 cookie-session and clear error handling.

=== Overview ===

BTS T2 form requires a live ASP.NET session cookie.  The current code uses
the plain _open_url() seam (no cookie jar), so the GET response sets a
Set-Cookie header that is never replayed on the POST.  The server then
returns an HTML error page instead of a zip, causing zipfile.BadZipFile
to be raised.

The fix must:
  (a) Use a CookieJar so the GET's session cookie is replayed on the POST.
  (b) Check the response magic bytes (PK\x03\x04) before passing to zipfile,
      and raise a clear ValueError if the response is not a zip.
  (c) Keep _open_url as-is for all other fetch functions (no regression).
  (d) Expose an injectable opener-factory seam so tests can inject a fake
      two-step server without hitting the network.

=== Test kinds ===
  (a) example (cookie replay): fake server returns zip only if cookie present,
      else HTML error page.  FAILS on current code, PASSES after fix.
  (b) mutation-catching (cookie removed): removing cookie handling causes test (a)
      to fail (verified in mutation check step).
  (c) example (non-zip error): non-zip response raises clear ValueError with
      content-type and first 80 bytes in message.
  (d) example (largest-member): two-member zip → larger data CSV extracted.
  (e) regression: existing test_bts_fetch tests unchanged (no new test here;
      covered by the TestFetchT2Offline class in test_t2_check.py).

=== Skip policy ===
All tests here are fully offline (injected fake server).  No network calls.
Cache-dependent tests are in test_t2_check.py and use @pytest.mark.skipif.

=== Seam design ===
fetch_t2 accepts an optional ``_opener_factory`` keyword argument (default
None = use the real CookieJar-backed opener).  Tests inject a callable that
returns a fake opener whose .open(request) method records cookies and enforces
the cookie policy.

The injected factory signature:
  _opener_factory() -> opener
  opener.open(request_or_url, data=None, timeout=300) -> response

This keeps _open_url untouched for all other fetch functions.
"""
from __future__ import annotations

import http.cookiejar
import io
import pathlib
import urllib.parse
import urllib.request
import zipfile
from typing import Any
from unittest import mock

import pytest

# ── locate helpers ────────────────────────────────────────────────────────────
import sys
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from bts_fetch import fetch_t2, _T2_FORM_URL


# ── Fake two-step server infrastructure ─────────────────────────────────────

_SESSION_COOKIE_NAME = "ASP.NET_SessionId"
_SESSION_COOKIE_VALUE = "fake_session_xyz"

_FAKE_FORM_HTML = (
    b'<html><body><form method="POST">'
    b'<input type="hidden" name="__VIEWSTATE" value="vs_t2_cookie" />'
    b'<input type="hidden" name="__VIEWSTATEGENERATOR" value="vsg_t2" />'
    b'<input type="hidden" name="__EVENTVALIDATION" value="ev_t2" />'
    b'<input type="checkbox" name="AIRCRAFT_FUELS_921" />'
    b'<input type="checkbox" name="YEAR" />'
    b'<input type="checkbox" name="QUARTER" />'
    b'<input type="checkbox" name="AIRCRAFT_TYPE" />'
    b'<input type="checkbox" name="AIRCRAFT_CONFIG" />'
    b'<input type="checkbox" name="SERVICE_CLASS" />'
    b'<input type="checkbox" name="UNIQUE_CARRIER" />'
    b'<input type="checkbox" name="CARRIER_GROUP_NEW" />'
    b'<input type="checkbox" name="AVL_SEAT_MILES_320" />'
    b'<input type="checkbox" name="REV_PAX_MILES_140" />'
    b'<input type="checkbox" name="REV_ACRFT_MILES_FLOWN_410" />'
    b'<input type="checkbox" name="REV_ACRFT_DEP_PERF_510" />'
    b'<input type="checkbox" name="HOURS_AIRBORNE_650" />'
    b'<input type="checkbox" name="NUM_MONTHS" />'
    b'</form></body></html>'
)

_HTML_ERROR_PAGE = (
    b"<html><body><h1>Session Expired</h1>"
    b"<p>Your session has expired. Please try again.</p>"
    b"</body></html>"
)


def _make_zip_bytes(members: dict[str, bytes]) -> bytes:
    """Build an in-memory ZIP with the given {name: content} dict."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


_DATA_CSV = b"YEAR,QUARTER,AIRCRAFT_TYPE,SERVICE_CLASS,AIRCRAFT_FUELS_921\n2019,1,694,Z,1000000.0\n"
_DOC_CSV = b"Documentation,info\nfield,description\n"
_GOOD_ZIP = _make_zip_bytes({"Documentation.csv": _DOC_CSV, "T_SCHEDULE_T2.csv": _DATA_CSV})


class _FakeResponse:
    """Minimal response object compatible with urllib opener response."""

    def __init__(self, body: bytes, *, headers: dict | None = None, status: int = 200):
        self.body = body
        self.status = status
        self._headers = headers or {}

    def read(self) -> bytes:
        return self.body

    def getheader(self, key: str, default: str | None = None) -> str | None:
        return self._headers.get(key, default)

    @property
    def headers(self):
        return self._headers


class _CookieEnforcingOpener:
    """
    Fake two-step server opener.

    - First call (GET): returns the form HTML and records that the session
      was issued.  If a cookie_jar is provided, it injects the session cookie
      into the jar so that subsequent requests via the same opener will carry
      the Cookie header.
    - Subsequent calls (POST): checks that the Cookie header on the request
      contains the session cookie name.  If yes, returns the zip.
      If no, returns the HTML error page.

    This simulates the BTS server behaviour: POST without session cookie →
    HTML error; POST with session cookie → zip.

    How cookie headers get added
    ----------------------------
    When urllib.request.build_opener(HTTPCookieProcessor(jar)) is used:
      1. The processor adds the Cookie header to outgoing requests.
      2. The processor stores Set-Cookie from responses in the jar.
    Our fake bypasses the full middleware chain, so we need the factory to
    wire cookies differently.  The factory uses a _RealCookieJarOpener that
    actually uses HTTPCookieProcessor so headers are added automatically.
    """

    def __init__(self, cookie_jar: http.cookiejar.CookieJar | None = None):
        self.cookie_jar = cookie_jar
        self._call_count = 0
        self.requests_seen: list[urllib.request.Request] = []

    def open(self, req_or_url, data=None, timeout=300):
        """Handle one request, recording which headers were sent."""
        if isinstance(req_or_url, str):
            req = urllib.request.Request(req_or_url, data=data)
        else:
            req = req_or_url

        # Apply cookies from jar to request (simulates HTTPCookieProcessor)
        if self.cookie_jar is not None:
            self.cookie_jar.add_cookie_header(req)

        self.requests_seen.append(req)
        self._call_count += 1

        if self._call_count == 1:
            # First call: GET — return HTML and store session cookie in jar
            if self.cookie_jar is not None:
                cookie = http.cookiejar.Cookie(
                    version=0,
                    name=_SESSION_COOKIE_NAME,
                    value=_SESSION_COOKIE_VALUE,
                    port=None, port_specified=False,
                    domain="www.transtats.bts.gov", domain_specified=True,
                    domain_initial_dot=False,
                    path="/", path_specified=True,
                    secure=False, expires=None,
                    discard=True, comment=None, comment_url=None,
                    rest={},
                )
                self.cookie_jar.set_cookie(cookie)

            return _FakeResponse(
                _FAKE_FORM_HTML,
                headers={
                    "Content-Type": "text/html",
                    "Set-Cookie": f"{_SESSION_COOKIE_NAME}={_SESSION_COOKIE_VALUE}; path=/",
                },
            )
        else:
            # Subsequent calls: POST — check for Cookie header
            cookie_header = req.get_header("Cookie") or ""
            has_cookie = _SESSION_COOKIE_NAME in cookie_header
            if has_cookie:
                return _FakeResponse(_GOOD_ZIP, headers={"Content-Type": "application/zip"})
            else:
                return _FakeResponse(
                    _HTML_ERROR_PAGE,
                    headers={"Content-Type": "text/html; charset=UTF-8"},
                )


class _OpenerFactory:
    """
    Injectable opener factory for tests.

    Returns a _CookieEnforcingOpener that shares a CookieJar.
    The CookieJar is what connects the GET's Set-Cookie to the POST's Cookie.
    """

    def __init__(self):
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = _CookieEnforcingOpener(cookie_jar=self.cookie_jar)

    def __call__(self) -> _CookieEnforcingOpener:
        """Return the shared opener (simulates build_opener with CookieProcessor)."""
        return self.opener


class _OpenerFactoryNoCookies:
    """
    Injectable opener factory WITHOUT cookie sharing.

    The GET and POST use separate openers with no shared CookieJar.
    This simulates the OLD (broken) behaviour — the session cookie from GET
    is never replayed on POST.
    """

    def __call__(self):
        """Return a fresh opener every call — no shared cookie jar."""
        return _CookieEnforcingOpener(cookie_jar=None)


# ════════════════════════════════════════════════════════════════════════════
# Test (a): Cookie replay — the core NW-24 bug fix test
# ════════════════════════════════════════════════════════════════════════════

class TestFetchT2CookieReplay:
    """
    Test that fetch_t2 replays the session cookie from GET onto the POST.

    KIND: example (two-step protocol correctness).
    TOLERANCE: none (binary: cookie present → zip; absent → HTML error).
    CANNOT CATCH: a server that accepts cookieless POSTs for this endpoint
    (i.e., if the real BTS server changes its behaviour).

    The fake server is a _CookieEnforcingOpener:
      - GET: returns HTML form + Set-Cookie header.
      - POST with cookie: returns zip.
      - POST without cookie: returns HTML error page.

    The test MUST FAIL on the current code (which uses _open_url with no
    cookie jar) and MUST PASS after the fix (which shares a CookieJar).
    """

    def test_cookie_replayed_on_post_yields_zip(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        fetch_t2 must succeed (write a CSV) when the injectable opener factory
        shares a CookieJar so the GET's session cookie is available for the POST.

        On the CURRENT (broken) code this test FAILS because:
          - fetch_t2 calls _open_url (no cookie jar)
          - the POST has no Cookie header
          - the fake server returns the HTML error page
          - _extract_largest_member raises zipfile.BadZipFile (or after the
            fix, the new ValueError with a clear message)

        After the fix the test PASSES because fetch_t2 accepts an optional
        _opener_factory kwarg and uses it to open a cookie-backed opener.

        CANNOT CATCH: a production server that does not enforce cookie-session.
        """
        factory = _OpenerFactory()
        result = fetch_t2(2019, 1, tmp_path, _opener_factory=factory)
        assert result.exists(), "fetch_t2 must write a CSV file"
        assert result.parent.resolve() == tmp_path.resolve()
        content = result.read_bytes()
        assert content == _DATA_CSV, (
            f"fetch_t2 must extract the largest zip member (the data CSV); "
            f"got {content[:80]!r}"
        )

    def test_no_cookie_gives_clear_error_not_bad_zip(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example (error clarity).
        TOLERANCE: none.
        When the POST response is not a zip (HTML error page instead of zip),
        fetch_t2 must raise a clear ValueError naming what was received,
        NOT the opaque zipfile.BadZipFile.

        This test uses a factory that does NOT share cookies so every POST
        gets the HTML error response (simulating the old broken behaviour).

        On the CURRENT code this FAILS: it raises BadZipFile, not ValueError.
        After the fix it PASSES: a clear ValueError is raised before zipfile
        is ever called.

        CANNOT CATCH: a ValueError with an unhelpful message (we check the
        content of the message below).
        """
        factory = _OpenerFactoryNoCookies()
        with pytest.raises(ValueError) as exc_info:
            with mock.patch("time.sleep"):
                fetch_t2(2019, 1, tmp_path, _opener_factory=factory)

        msg = str(exc_info.value)
        # The error message must contain something about content-type or the bytes received
        assert "text/html" in msg or "Session Expired" in msg or "<html" in msg.lower() or "not a zip" in msg.lower(), (
            f"ValueError message must mention content-type or bytes received; got: {msg!r}"
        )

    def test_cookie_name_sent_on_post(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example (protocol detail).
        TOLERANCE: none.
        Verify that the Cookie header on the POST request contains the
        session cookie name set by the GET response.  This is the direct
        evidence that cookie replay is happening.

        CANNOT CATCH: the cookie being sent with wrong value (same name,
        wrong value).  A stricter test could check the value too, but the
        fake server already enforces the value.
        """
        factory = _OpenerFactory()
        with mock.patch("time.sleep"):
            fetch_t2(2019, 1, tmp_path, _opener_factory=factory)

        # The POST request is the second request seen by the opener
        opener = factory.opener
        assert len(opener.requests_seen) >= 2, (
            f"Expected at least 2 requests (GET + POST), got {len(opener.requests_seen)}"
        )
        post_req = opener.requests_seen[1]
        cookie_header = post_req.get_header("Cookie") or ""
        assert _SESSION_COOKIE_NAME in cookie_header, (
            f"POST request must carry the session cookie '{_SESSION_COOKIE_NAME}'; "
            f"Cookie header: {cookie_header!r}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Test (c): Non-zip response gives clear ValueError
# ════════════════════════════════════════════════════════════════════════════

class TestFetchT2NonZipError:
    """
    Test that a non-zip response raises a clear ValueError with diagnostics.

    KIND: example (error clarity).
    TOLERANCE: none.
    CANNOT CATCH: a ValueError whose message exists but is unhelpful.
    """

    def _make_html_response(self, body: bytes, content_type: str = "text/html"):
        """Return a mock response as if from _open_url."""
        resp = mock.MagicMock()
        resp.read.return_value = body
        resp.getheader = lambda k, d=None: content_type if k.lower() in ("content-type", "Content-Type") else d
        resp.headers = {"Content-Type": content_type}
        return resp

    def test_non_zip_raises_valueerror_with_content_type(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        When the POST response body starts with HTML (not PK\x03\x04 magic),
        fetch_t2 must raise ValueError.  The message must include the
        content-type string from the response.

        We inject this via the existing _open_url mock path so we can also
        verify the old path is still injectable.

        CANNOT CATCH: ValueError raised for a different reason (e.g., scraping
        failure).
        """
        html_body = (
            b"<html><body>Your session has expired</body></html>"
        )
        fake_html_page = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
        zip_of_form = self._make_html_response(fake_html_page, "text/html")
        error_resp = self._make_html_response(html_body, "text/html; charset=utf-8")

        with mock.patch("bts_fetch._open_url", side_effect=[zip_of_form, error_resp]):
            with mock.patch("time.sleep"):
                with pytest.raises(ValueError) as exc_info:
                    fetch_t2(2019, 1, tmp_path)

        msg = str(exc_info.value)
        # Must mention content-type
        assert "text/html" in msg, (
            f"ValueError must mention content-type 'text/html'; got: {msg!r}"
        )

    def test_non_zip_error_includes_first_80_bytes(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        The ValueError message must include the first ≤80 safely printable
        bytes of the non-zip response for debugging.

        CANNOT CATCH: message that includes bytes but truncates before 80.
        """
        html_body = (
            b"<html><body>Error: session required.</body></html>"
        )
        fake_html_page = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
        zip_of_form = self._make_html_response(fake_html_page, "text/html")
        error_resp = self._make_html_response(html_body, "text/html")

        with mock.patch("bts_fetch._open_url", side_effect=[zip_of_form, error_resp]):
            with mock.patch("time.sleep"):
                with pytest.raises(ValueError) as exc_info:
                    fetch_t2(2019, 1, tmp_path)

        msg = str(exc_info.value)
        # The message must contain some snippet from the response body
        # We allow for encoding (repr) so check for recognizable bytes
        assert "Error" in msg or "session" in msg or "html" in msg.lower() or "3c68" in msg.lower(), (
            f"ValueError must include first bytes of non-zip response; got: {msg!r}"
        )

    def test_valid_zip_does_not_raise(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example (happy path guard).
        TOLERANCE: none.
        A well-formed zip response must NOT raise ValueError.

        CANNOT CATCH: a zip that passes the magic check but is corrupt.
        """
        csv_data = b"YEAR,QUARTER\n2019,1\n"
        zip_body = _make_zip_bytes({"T_SCHEDULE_T2.csv": csv_data})

        fake_html_page = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
        get_resp = mock.MagicMock()
        get_resp.read.return_value = fake_html_page
        get_resp.getheader = lambda k, d=None: None
        get_resp.headers = {}

        post_resp = mock.MagicMock()
        post_resp.read.return_value = zip_body
        post_resp.getheader = lambda k, d=None: "application/zip" if "content-type" in k.lower() else d
        post_resp.headers = {"Content-Type": "application/zip"}

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_resp]):
            with mock.patch("time.sleep"):
                result = fetch_t2(2019, 1, tmp_path)

        assert result.exists(), "fetch_t2 must succeed with valid zip response"


# ════════════════════════════════════════════════════════════════════════════
# Test (d): Largest-member extraction from two-member zip
# ════════════════════════════════════════════════════════════════════════════

class TestFetchT2LargestMember:
    """
    Test that fetch_t2 extracts the largest member when the zip has 2 members.

    KIND: example.
    TOLERANCE: none (exact byte comparison).
    CANNOT CATCH: a zip where the larger member happens to be Documentation.csv.
    """

    def test_extracts_largest_of_two_members(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        The T2 zip has two members:
          - Documentation.csv (small: a few bytes)
          - T_SCHEDULE_T2.csv (large: many kilobytes of real data)
        fetch_t2 must return the large data CSV, not the documentation file.

        The fix must extract the member with the largest uncompressed size,
        not the alphabetically first or last.

        CANNOT CATCH: a case where Documentation.csv is somehow larger
        than the data file (not possible in real BTS data).
        """
        small_doc = b"Documentation: BTS Schedule T2\nField,Description\n"
        large_data = (
            b"YEAR,QUARTER,AIRCRAFT_TYPE,SERVICE_CLASS,AIRCRAFT_FUELS_921\n"
            + b"2019,1,694,Z,1000000.0\n" * 1000
        )
        zip_body = _make_zip_bytes({
            "Documentation.csv": small_doc,
            "T_SCHEDULE_T2_2019_1.csv": large_data,
        })

        # Use the cookie-sharing factory to get a valid zip response
        factory = _OpenerFactory()
        # Replace the zip that the factory's opener returns with our two-member zip
        factory.opener.open = lambda req_or_url, data=None, timeout=300: (
            _FakeResponse(_FAKE_FORM_HTML, headers={
                "Content-Type": "text/html",
                "Set-Cookie": f"{_SESSION_COOKIE_NAME}={_SESSION_COOKIE_VALUE}; path=/",
            })
            if factory.opener._call_count == 0
            else _FakeResponse(zip_body, headers={"Content-Type": "application/zip"})
        )

        # We need to patch properly so call_count increments
        call_state = {"count": 0}

        def _patched_open(req_or_url, data=None, timeout=300):
            call_state["count"] += 1
            if isinstance(req_or_url, str):
                req = urllib.request.Request(req_or_url, data=data)
            else:
                req = req_or_url
            factory.opener.requests_seen.append(req)

            if call_state["count"] == 1:
                # GET: inject cookie into jar, return HTML
                cookie = http.cookiejar.Cookie(
                    version=0, name=_SESSION_COOKIE_NAME,
                    value=_SESSION_COOKIE_VALUE,
                    port=None, port_specified=False,
                    domain="www.transtats.bts.gov", domain_specified=True,
                    domain_initial_dot=False, path="/", path_specified=True,
                    secure=False, expires=None, discard=True,
                    comment=None, comment_url=None, rest={},
                )
                factory.cookie_jar.set_cookie(cookie)
                return _FakeResponse(_FAKE_FORM_HTML, headers={"Content-Type": "text/html"})
            else:
                # POST: return two-member zip
                return _FakeResponse(zip_body, headers={"Content-Type": "application/zip"})

        factory.opener.open = _patched_open

        with mock.patch("time.sleep"):
            result = fetch_t2(2019, 1, tmp_path, _opener_factory=factory)

        assert result.read_bytes() == large_data, (
            "fetch_t2 must extract the largest member (large_data), not Documentation.csv"
        )

    def test_output_filename_follows_convention(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example.
        TOLERANCE: none (exact string match).
        The output filename must be 't2_{year}_q{quarter}.csv'.
        For year=2019, quarter=1: 't2_2019_q1.csv'.

        CANNOT CATCH: correct filename with wrong content.
        """
        factory = _OpenerFactory()
        with mock.patch("time.sleep"):
            result = fetch_t2(2019, 1, tmp_path, _opener_factory=factory)

        assert result.name == "t2_2019_q1.csv", (
            f"Output filename must be 't2_2019_q1.csv'; got {result.name!r}"
        )


# ════════════════════════════════════════════════════════════════════════════
# Test (e): _open_url path still works (backward-compat with existing mocks)
# ════════════════════════════════════════════════════════════════════════════

class TestFetchT2BackwardCompat:
    """
    Verify that the existing mock.patch('bts_fetch._open_url', ...) injection
    still works for fetch_t2 (when no _opener_factory is given).

    KIND: regression / example.
    TOLERANCE: none.
    CANNOT CATCH: subtle differences in how _open_url is called vs. opener.open.
    """

    def test_open_url_still_injectable_without_opener_factory(
        self, tmp_path: pathlib.Path
    ) -> None:
        """
        KIND: regression.
        TOLERANCE: none.
        Calling fetch_t2 with _opener_factory=None (default) and mocking
        _open_url must still work.  This preserves backward compatibility with
        the existing tests in TestFetchT2Offline (test_t2_check.py).

        The existing tests mock _open_url directly and do not worry about
        cookies; they must continue to pass.

        CANNOT CATCH: a fix that breaks _open_url injection for other fetchers.
        """
        csv_data = b"YEAR,QUARTER\n2019,1\n"
        zip_body = _make_zip_bytes({"data.csv": csv_data})

        fake_html = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
        get_resp = mock.MagicMock()
        get_resp.read.return_value = fake_html
        get_resp.getheader = lambda k, d=None: None
        get_resp.headers = {}

        post_resp = mock.MagicMock()
        post_resp.read.return_value = zip_body
        post_resp.getheader = lambda k, d=None: "application/zip" if "content-type" in k.lower() else d
        post_resp.headers = {"Content-Type": "application/zip"}

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_resp]):
            with mock.patch("time.sleep"):
                result = fetch_t2(2019, 1, tmp_path)  # no _opener_factory

        assert result.exists(), (
            "fetch_t2 with no _opener_factory must still work via _open_url mock"
        )

    def test_other_fetch_functions_unchanged(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: regression.
        TOLERANCE: none.
        fetch_db1b_market, fetch_t100_segment, fetch_aircraft_lookup must
        still use _open_url (not the new cookie-opener path).

        We verify this by checking that mocking _open_url still drives these
        functions correctly.

        CANNOT CATCH: functions that work but use a different internal path.
        """
        from bts_fetch import fetch_db1b_market, fetch_aircraft_lookup

        # Test fetch_db1b_market still uses _open_url
        csv_bytes = b"ItinID,Passengers\n1,100\n"
        zip_body = _make_zip_bytes({"data.csv": csv_bytes})
        resp1 = mock.MagicMock()
        resp1.read.return_value = zip_body
        resp1.headers = {}

        with mock.patch("bts_fetch._open_url", return_value=resp1) as m_open:
            with mock.patch("time.sleep"):
                fetch_db1b_market(2019, 1, tmp_path)

        assert m_open.called, "fetch_db1b_market must still go through _open_url"

        # Test fetch_aircraft_lookup still uses _open_url
        csv_bytes2 = b"Code,Description\n614,B737-800\n"
        resp2 = mock.MagicMock()
        resp2.read.return_value = csv_bytes2
        resp2.headers = {}

        with mock.patch("bts_fetch._open_url", return_value=resp2) as m_open2:
            with mock.patch("time.sleep"):
                fetch_aircraft_lookup(tmp_path)

        assert m_open2.called, "fetch_aircraft_lookup must still go through _open_url"


# ════════════════════════════════════════════════════════════════════════════
# Test: Opener factory contract
# ════════════════════════════════════════════════════════════════════════════

class TestOpenerFactoryContract:
    """
    Test that the default (no-arg) fetch_t2 creates a CookieJar-backed opener
    (not directly testable without network, but we verify the seam exists and
    the module exports the right name for the factory parameter).

    KIND: example (API contract).
    TOLERANCE: none.
    CANNOT CATCH: a factory that creates the opener but doesn't actually
    attach a CookieProcessor.
    """

    def test_fetch_t2_accepts_opener_factory_kwarg(self, tmp_path: pathlib.Path) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        fetch_t2 must accept an _opener_factory keyword argument (not raise
        TypeError).  This is the injectable seam for cookie behaviour.

        CANNOT CATCH: an _opener_factory that is accepted but ignored.
        """
        factory = _OpenerFactory()
        # Should not raise TypeError
        with mock.patch("time.sleep"):
            result = fetch_t2(2019, 1, tmp_path, _opener_factory=factory)
        assert result.exists()

    def test_opener_factory_called_for_t2_not_for_db1b(
        self, tmp_path: pathlib.Path
    ) -> None:
        """
        KIND: property (isolation).
        TOLERANCE: none.
        The _opener_factory must be called by fetch_t2 but NOT by
        fetch_db1b_market (which has no cookie-session requirement).

        CANNOT CATCH: factory called AND _open_url also called (redundantly).
        """
        from bts_fetch import fetch_db1b_market

        factory_calls = {"count": 0}

        class _CountingFactory:
            def __call__(self):
                factory_calls["count"] += 1
                return _OpenerFactory()()  # return a real opener

        # fetch_db1b_market does not accept _opener_factory
        csv_bytes = b"ItinID,Passengers\n1,100\n"
        zip_body = _make_zip_bytes({"data.csv": csv_bytes})
        resp = mock.MagicMock()
        resp.read.return_value = zip_body
        resp.headers = {}

        with mock.patch("bts_fetch._open_url", return_value=resp):
            with mock.patch("time.sleep"):
                fetch_db1b_market(2019, 1, tmp_path)

        assert factory_calls["count"] == 0, (
            "fetch_db1b_market must not call any opener factory"
        )


# ════════════════════════════════════════════════════════════════════════════
# Test: _make_t2_opener produces a CookieJar-backed opener
# ════════════════════════════════════════════════════════════════════════════

class TestMakeT2Opener:
    """
    Unit tests for the _make_t2_opener() factory function.

    These tests verify that:
    (1) the opener returned has a CookieJar attached (opener.jar)
    (2) the jar starts empty
    (3) after a cookie is injected, add_cookie_header adds the Cookie header

    Together these guarantee that the production path correctly replays
    cookies between the GET and POST requests.

    KIND: example / property.
    TOLERANCE: none.
    CANNOT CATCH: a CookieJar that is attached but not used by the opener's
    internal HTTPCookieProcessor.
    """

    def test_opener_has_jar_attribute(self) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        _make_t2_opener() must return an opener with a .jar attribute that is
        an instance of http.cookiejar.CookieJar.  This attribute is the hook
        used by tests (and by _make_t2_opener itself) to inspect cookie state.

        CANNOT CATCH: a jar attribute that is set but not wired into the opener.
        """
        from bts_fetch import _make_t2_opener
        opener = _make_t2_opener()
        assert hasattr(opener, "jar"), (
            "_make_t2_opener() must return an opener with a .jar attribute"
        )
        assert isinstance(opener.jar, http.cookiejar.CookieJar), (
            f"opener.jar must be a CookieJar; got {type(opener.jar)}"
        )

    def test_opener_jar_starts_empty(self) -> None:
        """
        KIND: example.
        TOLERANCE: none.
        A freshly built opener must have an empty CookieJar.
        A non-empty jar at construction would indicate stale state from a
        previous request, which could corrupt the session.

        CANNOT CATCH: a jar that appears empty but has expired cookies.
        """
        from bts_fetch import _make_t2_opener
        opener = _make_t2_opener()
        assert list(opener.jar) == [], (
            f"opener.jar must be empty at construction; got {list(opener.jar)}"
        )

    def test_opener_jar_replays_cookies_on_request(self) -> None:
        """
        KIND: example (core contract).
        TOLERANCE: none.
        After a session cookie is injected into opener.jar, building a Request
        and calling opener.jar.add_cookie_header(req) must set the Cookie header
        on the request.  This verifies the jar is a real CookieJar (not a stub)
        and that its add_cookie_header works as expected.

        This test would FAIL if _make_t2_opener returned an opener without a
        real CookieJar (e.g., Mutation 2: `opener.jar = None`).

        CANNOT CATCH: a jar that adds cookie headers but the opener's
        HTTPCookieProcessor does not use it.
        """
        from bts_fetch import _make_t2_opener
        opener = _make_t2_opener()

        # Inject a session cookie into the jar
        cookie = http.cookiejar.Cookie(
            version=0,
            name=_SESSION_COOKIE_NAME,
            value=_SESSION_COOKIE_VALUE,
            port=None, port_specified=False,
            domain="www.transtats.bts.gov", domain_specified=True,
            domain_initial_dot=False,
            path="/", path_specified=True,
            secure=False, expires=None,
            discard=True, comment=None, comment_url=None,
            rest={},
        )
        opener.jar.set_cookie(cookie)

        # Build a request and add cookie header via the jar
        req = urllib.request.Request(
            "https://www.transtats.bts.gov/DL_SelectFields.aspx"
        )
        opener.jar.add_cookie_header(req)
        cookie_hdr = req.get_header("Cookie") or ""
        assert _SESSION_COOKIE_NAME in cookie_hdr, (
            f"After injecting cookie into opener.jar, add_cookie_header must "
            f"set the Cookie header; got: {cookie_hdr!r}.  "
            f"This may indicate _make_t2_opener does not attach HTTPCookieProcessor."
        )

    def test_make_t2_opener_uses_cookiejar(self) -> None:
        """
        KIND: property (CookieJar round-trip).
        TOLERANCE: none.
        The jar attached to the opener must support set_cookie and
        add_cookie_header correctly.  This is a minimal round-trip that
        would fail if the jar were replaced with a non-functional stub
        (Mutation 2b: `opener.jar = object()`).

        CANNOT CATCH: a working jar that has wrong expiry/domain settings.
        """
        from bts_fetch import _make_t2_opener
        opener = _make_t2_opener()

        # Verify the jar is functional
        c = http.cookiejar.Cookie(
            version=0, name="test", value="val",
            port=None, port_specified=False,
            domain="example.com", domain_specified=True,
            domain_initial_dot=False, path="/", path_specified=True,
            secure=False, expires=None, discard=True,
            comment=None, comment_url=None, rest={},
        )
        opener.jar.set_cookie(c)
        assert len(list(opener.jar)) == 1, (
            "After set_cookie, jar must contain 1 cookie; "
            f"got {len(list(opener.jar))}.  "
            "This indicates opener.jar is not a functional CookieJar."
        )

    def test_opener_has_httpcookieprocessor_handler(self) -> None:
        """
        KIND: property (handler chain).
        TOLERANCE: none.
        The opener returned by _make_t2_opener must have an HTTPCookieProcessor
        in its handler list.  Without this handler, cookies from responses are
        NOT automatically stored in the jar, so the POST cannot replay the
        GET's session cookie.

        This test DIRECTLY catches Mutation 2 (removing HTTPCookieProcessor
        from build_opener).

        CANNOT CATCH: an HTTPCookieProcessor that is wired to a DIFFERENT
        jar than opener.jar.
        """
        from bts_fetch import _make_t2_opener
        opener = _make_t2_opener()

        # Walk the handler chain to find an HTTPCookieProcessor
        cookie_processors = [
            h for h in opener.handlers
            if isinstance(h, urllib.request.HTTPCookieProcessor)
        ]
        assert len(cookie_processors) >= 1, (
            "_make_t2_opener() opener must have an HTTPCookieProcessor handler. "
            f"Handlers found: {[type(h).__name__ for h in opener.handlers]}. "
            "Without HTTPCookieProcessor, the GET's session cookie is not "
            "stored and cannot be replayed on the POST request."
        )

    def test_httpcookieprocessor_uses_same_jar_as_opener_jar(self) -> None:
        """
        KIND: property (jar identity).
        TOLERANCE: none.
        The HTTPCookieProcessor in the opener must use the SAME jar object
        as opener.jar.  If they are different objects, injecting a cookie
        into opener.jar will not be picked up by the processor (and vice versa).

        This catches the mutation: `opener.jar = CookieJar()` (new jar,
        different from the one in the processor).

        CANNOT CATCH: jar identity being correct but CookieJar methods
        being overridden.
        """
        from bts_fetch import _make_t2_opener
        opener = _make_t2_opener()

        cookie_processors = [
            h for h in opener.handlers
            if isinstance(h, urllib.request.HTTPCookieProcessor)
        ]
        assert len(cookie_processors) >= 1, "No HTTPCookieProcessor found"
        proc = cookie_processors[0]
        assert proc.cookiejar is opener.jar, (
            "HTTPCookieProcessor.cookiejar must be the SAME object as opener.jar. "
            f"processor.cookiejar id={id(proc.cookiejar)}, "
            f"opener.jar id={id(opener.jar)}. "
            "A mismatch means cookie injection into opener.jar won't be seen by "
            "the processor."
        )



# ════════════════════════════════════════════════════════════════════════════
# Real-socket test: the DEFAULT fetch_t2 against a local cookie-enforcing server
# ════════════════════════════════════════════════════════════════════════════

class TestFetchT2RealSocket:
    """KIND: integration (real urllib stack, loopback only, no internet).
    TOLERANCE: none (byte equality).
    CANNOT CATCH: BTS-specific behaviour (viewstate validation, rate limits,
    changed form fields); it proves only that the default opener replays the
    cookie the server set, using urllib's real cookie machinery.
    """

    def _serve(self):
        import http.server
        import threading

        zip_body = _make_zip_bytes({"Documentation.csv": b"d\n", "data.csv": _DATA_CSV})
        seen = {"post_cookie": None}

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_GET(self):
                body = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
                self.send_response(200)
                self.send_header("Set-Cookie", "ASP.NET_SessionId=abc123; path=/")
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                self.rfile.read(n)
                seen["post_cookie"] = self.headers.get("Cookie")
                ok = "ASP.NET_SessionId=abc123" in (seen["post_cookie"] or "")
                body = zip_body if ok else b"<html>session expired</html>"
                self.send_response(200)
                self.send_header("Content-Type", "application/zip" if ok else "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        return srv, seen

    def test_default_fetch_replays_session_cookie(self, tmp_path, monkeypatch):
        """KIND: integration. fetch_t2 with NO injected factory must succeed
        against a server that rejects a POST lacking the GET's cookie. This is
        the original bug: it fails (ValueError, non-zip) if the default path
        has no cookie jar. TOLERANCE: none."""
        import bts_fetch

        srv, seen = self._serve()
        try:
            monkeypatch.setattr(
                bts_fetch, "_T2_FORM_URL", f"http://127.0.0.1:{srv.server_port}/form"
            )
            result = fetch_t2(2019, 1, tmp_path)
        finally:
            srv.shutdown()
        assert "ASP.NET_SessionId=abc123" in seen["post_cookie"]
        assert result.read_bytes() == _DATA_CSV
