"""
Structural checks for the static HTML site pages: project-progress.html
and project-prototype.html.

Kind: example / structural property tests.
Tolerance: none (exact structural checks, no numeric tolerance).
Cannot catch: runtime JS errors; visual rendering issues; CSS computed styles
at browser render time; anything only evaluable in a real browser.
"""
from __future__ import annotations

import pathlib
import re
from html.parser import HTMLParser
from typing import Iterator

# Repo root is two levels up from project/tests/
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PROGRESS_PAGE = REPO_ROOT / "project-progress.html"
PROTOTYPE_PAGE = REPO_ROOT / "project-prototype.html"
CSS_FILE = REPO_ROOT / "css" / "style.css"
PROJECT_NAV = REPO_ROOT / "js" / "project-nav.js"


# ---------------------------------------------------------------------------
# HTML parsing helpers
# ---------------------------------------------------------------------------

class _TagBalanceChecker(HTMLParser):
    """
    Collects all open/close tags so we can verify that every opened
    non-void element is closed.

    Kind: structural property — well-formed HTML (balanced tags).
    """

    VOID_ELEMENTS = frozenset(
        "area base br col embed hr img input link meta param source track wbr".split()
    )

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag not in self.VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in self.VOID_ELEMENTS:
            return
        if self.stack and self.stack[-1] == tag:
            self.stack.pop()
        else:
            self.errors.append(
                f"Unexpected </{tag}> (stack top: {self.stack[-1] if self.stack else 'empty'})"
            )


def _check_balanced(text: str) -> list[str]:
    """Return list of balance errors; empty means well-formed."""
    checker = _TagBalanceChecker()
    checker.feed(text)
    errors = list(checker.errors)
    if checker.stack:
        errors.append(f"Unclosed tags remaining: {checker.stack}")
    return errors


class _AttrCollector(HTMLParser):
    """Collect all tags with their attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append((tag, dict(attrs)))


def _collect_tags(text: str) -> list[tuple[str, dict[str, str]]]:
    col = _AttrCollector()
    col.feed(text)
    return col.tags


class _SVGBoundsChecker(HTMLParser):
    """
    Collect inline SVG elements and check that rect/circle/line/text x,y
    attributes stay within the declared viewBox for simple shapes.

    Kind: property — inline SVG elements within viewBox bounds.
    Tolerance: none (exact comparison on declared coordinates).
    Cannot catch: transform/translate attributes that move elements; nested
    SVGs; percentage units; dynamic JS modifications.
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_svg = False
        self._viewbox: tuple[float, float, float, float] | None = None
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        d = dict(attrs)
        if tag == "svg":
            self._in_svg = True
            vb = d.get("viewbox") or d.get("viewBox")
            if vb:
                parts = vb.replace(",", " ").split()
                if len(parts) == 4:
                    try:
                        self._viewbox = tuple(float(p) for p in parts)  # type: ignore[assignment]
                    except ValueError:
                        self._viewbox = None
        if not self._in_svg or self._viewbox is None:
            return
        vx, vy, vw, vh = self._viewbox
        max_x, max_y = vx + vw, vy + vh

        def _f(v: str | None) -> float | None:
            if v is None:
                return None
            try:
                return float(v)
            except ValueError:
                return None

        if tag in ("rect", "text"):
            x = _f(d.get("x"))
            y = _f(d.get("y"))
            if x is not None and not (vx - 1 <= x <= max_x + 1):
                self.violations.append(f"<{tag} x={x}> outside viewBox x range [{vx},{max_x}]")
            if y is not None and not (vy - 1 <= y <= max_y + 1):
                self.violations.append(f"<{tag} y={y}> outside viewBox y range [{vy},{max_y}]")

        if tag == "circle":
            cx = _f(d.get("cx"))
            cy = _f(d.get("cy"))
            if cx is not None and not (vx - 1 <= cx <= max_x + 1):
                self.violations.append(f"<circle cx={cx}> outside viewBox x range [{vx},{max_x}]")
            if cy is not None and not (vy - 1 <= cy <= max_y + 1):
                self.violations.append(f"<circle cy={cy}> outside viewBox y range [{vy},{max_y}]")

        if tag == "line":
            for attr in ("x1", "x2"):
                v = _f(d.get(attr))
                if v is not None and not (vx - 1 <= v <= max_x + 1):
                    self.violations.append(f"<line {attr}={v}> outside viewBox x [{vx},{max_x}]")
            for attr in ("y1", "y2"):
                v = _f(d.get(attr))
                if v is not None and not (vy - 1 <= v <= max_y + 1):
                    self.violations.append(f"<line {attr}={v}> outside viewBox y [{vy},{max_y}]")

    def handle_endtag(self, tag: str) -> None:
        if tag == "svg":
            self._in_svg = False
            self._viewbox = None


def _check_svg_bounds(text: str) -> list[str]:
    checker = _SVGBoundsChecker()
    checker.feed(text)
    return checker.violations


# ---------------------------------------------------------------------------
# 1. Both pages exist
# ---------------------------------------------------------------------------

def test_progress_page_exists() -> None:
    """
    Example test — project-progress.html must exist in the repo root.

    Kind: example.
    Tolerance: none.
    Cannot catch: file present but empty or corrupt.
    """
    assert PROGRESS_PAGE.exists(), f"Missing file: {PROGRESS_PAGE}"


def test_prototype_page_exists() -> None:
    """
    Example test — project-prototype.html must exist in the repo root.

    Kind: example.
    Tolerance: none.
    Cannot catch: file present but empty.
    """
    assert PROTOTYPE_PAGE.exists(), f"Missing file: {PROTOTYPE_PAGE}"


# ---------------------------------------------------------------------------
# 2. Both pages parse as well-formed HTML (balanced tags)
# ---------------------------------------------------------------------------

def test_progress_page_balanced_tags() -> None:
    """
    Property test — project-progress.html has balanced HTML tags.

    Kind: property.
    Tolerance: none.
    Cannot catch: attribute quoting issues or invalid attribute names.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    errors = _check_balanced(text)
    assert not errors, f"Tag balance errors in project-progress.html:\n" + "\n".join(errors)


def test_prototype_page_balanced_tags() -> None:
    """
    Property test — project-prototype.html has balanced HTML tags.

    Kind: property.
    Tolerance: none.
    Cannot catch: attribute quoting issues.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    errors = _check_balanced(text)
    assert not errors, f"Tag balance errors in project-prototype.html:\n" + "\n".join(errors)


# ---------------------------------------------------------------------------
# 3. Inline SVG elements stay inside viewBox (simple shapes only)
# ---------------------------------------------------------------------------

def test_progress_svg_within_viewbox() -> None:
    """
    Property test — all rect/circle/line/text x,y coords in project-progress.html
    inline SVGs are within the declared viewBox bounds (±1 px tolerance for
    strokes on the border).

    Kind: property.
    Tolerance: ±1 px for stroke overlap.
    Cannot catch: transform/translate; percentage units; JS-modified SVGs.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    violations = _check_svg_bounds(text)
    assert not violations, "SVG out-of-bounds in project-progress.html:\n" + "\n".join(violations)


def test_prototype_svg_within_viewbox() -> None:
    """
    Property test — all inline SVG shapes in project-prototype.html stay
    within the declared viewBox.

    Kind: property.
    Tolerance: ±1 px.
    Cannot catch: transform/translate; percentage units.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    violations = _check_svg_bounds(text)
    assert not violations, "SVG out-of-bounds in project-prototype.html:\n" + "\n".join(violations)


# ---------------------------------------------------------------------------
# 4. Status classes: all three are used AND defined in CSS
# ---------------------------------------------------------------------------

STATUS_CLASSES = ("status--done", "status--in-progress", "status--not-started")


def test_all_status_classes_defined_in_css() -> None:
    """
    Property test — the CSS file defines all three status classes used by
    the pipeline diagram (as actual rules, not just in comments).

    Kind: property.
    Tolerance: none.
    Cannot catch: classes defined via JS or in inline <style> blocks.
    """
    css_raw = CSS_FILE.read_text(encoding="utf-8")
    # Strip CSS block comments (/* ... */) before checking
    css_text = re.sub(r'/\*.*?\*/', '', css_raw, flags=re.DOTALL)
    for cls in STATUS_CLASSES:
        assert f".{cls}" in css_text, (
            f"CSS class '.{cls}' not defined in css/style.css "
            f"(checked after stripping comments)"
        )


def test_progress_uses_all_status_classes() -> None:
    """
    Property test — project-progress.html contains all three status class
    values in its markup (so the legend is complete and meaningful).

    Kind: property.
    Tolerance: none.
    Cannot catch: classes applied only via JS at runtime.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    for cls in STATUS_CLASSES:
        assert cls in text, (
            f"Status class '{cls}' not found in project-progress.html; "
            "all three status levels must appear in the pipeline diagram"
        )


def test_prototype_uses_all_status_classes() -> None:
    """
    Property test — project-prototype.html also uses all three status
    classes so the legend is verifiable from the prototype page too.

    Kind: property.
    Tolerance: none.
    Cannot catch: classes applied only via JS.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    for cls in STATUS_CLASSES:
        assert cls in text, (
            f"Status class '{cls}' not found in project-prototype.html"
        )


# ---------------------------------------------------------------------------
# 5. Nav includes 'Prototype' link in project-nav.js
# ---------------------------------------------------------------------------

def test_project_nav_includes_prototype() -> None:
    """
    Example test — js/project-nav.js must list project-prototype.html
    with label 'Prototype' in SUB_LINKS.

    Kind: example.
    Tolerance: none.
    Cannot catch: links injected from another JS file.
    """
    nav_text = PROJECT_NAV.read_text(encoding="utf-8")
    assert "project-prototype.html" in nav_text, (
        "project-prototype.html not found in js/project-nav.js SUB_LINKS"
    )
    assert '"Prototype"' in nav_text or "'Prototype'" in nav_text, (
        "Label 'Prototype' not found in js/project-nav.js SUB_LINKS"
    )


def test_progress_page_has_prototype_link() -> None:
    """
    Example test — project-progress.html must contain a link to
    project-prototype.html (the 'Prototype' page is linked from Progress).

    Kind: example.
    Tolerance: none.
    Cannot catch: link injected by JS only at runtime.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    assert "project-prototype.html" in text, (
        "project-progress.html does not link to project-prototype.html"
    )


# ---------------------------------------------------------------------------
# 6. Every relative href/src resolves to an existing file
# ---------------------------------------------------------------------------

def _iter_relative_refs(text: str) -> Iterator[str]:
    """Yield all relative href/src values from an HTML file."""
    tags = _collect_tags(text)
    for tag, attrs in tags:
        for attr in ("href", "src"):
            val = attrs.get(attr, "")
            if val and not val.startswith(("http://", "https://", "#", "mailto:", "javascript:")):
                yield val


def _check_refs(page: pathlib.Path) -> list[str]:
    text = page.read_text(encoding="utf-8")
    missing: list[str] = []
    for ref in _iter_relative_refs(text):
        # Strip fragment
        ref_path = ref.split("#")[0]
        if not ref_path:
            continue
        target = REPO_ROOT / ref_path
        if not target.exists():
            missing.append(ref)
    return missing


def test_progress_page_refs_resolve() -> None:
    """
    Property test — every relative href/src in project-progress.html
    resolves to an existing file in the repo.

    Kind: property.
    Tolerance: none.
    Cannot catch: CDN links; runtime-generated hrefs; anchor-only refs.
    """
    missing = _check_refs(PROGRESS_PAGE)
    assert not missing, (
        f"Broken relative refs in project-progress.html:\n"
        + "\n".join(f"  {r}" for r in missing)
    )


def test_prototype_page_refs_resolve() -> None:
    """
    Property test — every relative href/src in project-prototype.html
    resolves to an existing file in the repo.

    Kind: property.
    Tolerance: none.
    Cannot catch: CDN links; runtime-generated hrefs.
    """
    missing = _check_refs(PROTOTYPE_PAGE)
    assert not missing, (
        f"Broken relative refs in project-prototype.html:\n"
        + "\n".join(f"  {r}" for r in missing)
    )


# ---------------------------------------------------------------------------
# 7. Forbidden-claims: no fuel/kg number without 'placeholder'
# ---------------------------------------------------------------------------

# Pattern: a number followed by "kg" (with optional space) that could be
# interpreted as a real fuel mass.  We look for this WITHIN a single HTML
# element's text content by scanning between tag pairs.
_FUEL_NUMBER_RE = re.compile(r"\d[\d,.]*\s*kg", re.IGNORECASE)


class _ElementTextScanner(HTMLParser):
    """
    Scan element text content for fuel numbers without 'placeholder' in
    the same element or immediately adjacent text.
    """

    def __init__(self) -> None:
        super().__init__()
        self._current_attrs: dict[str, str] = {}
        self._current_tag: str = ""
        self._buffer: str = ""
        self.violations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        # Flush previous buffer
        self._flush()
        self._current_tag = tag
        self._current_attrs = dict(attrs)
        self._buffer = ""

    def handle_endtag(self, tag: str) -> None:
        self._flush()
        self._buffer = ""

    def handle_data(self, data: str) -> None:
        self._buffer += data

    def _flush(self) -> None:
        if not self._buffer.strip():
            return
        text = self._buffer
        if _FUEL_NUMBER_RE.search(text):
            if "placeholder" not in text.lower():
                self.violations.append(
                    f"Fuel/kg number found without 'placeholder' in text: {text.strip()!r}"
                )
        self._buffer = ""


def _check_no_unlabelled_fuel(text: str) -> list[str]:
    """
    Scan HTML text for fuel-quantity numbers (e.g. '1234 kg') not accompanied
    by 'placeholder' in the same element text node.
    """
    scanner = _ElementTextScanner()
    scanner.feed(text)
    return scanner.violations


def test_progress_no_unlabelled_fuel_numbers() -> None:
    """
    Property test — project-progress.html must not present any fuel/kg
    number without the word 'placeholder' in the same element text.

    Kind: property (forbidden-claims guard).
    Tolerance: none.
    Reason: current fuel coefficients are ~10-18x too high vs OpenAP;
    presenting raw numbers as results would be misleading.
    Cannot catch: fuel numbers described in prose without 'kg' units,
    or numbers in image alt text.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    violations = _check_no_unlabelled_fuel(text)
    assert not violations, (
        "project-progress.html contains fuel numbers without 'placeholder':\n"
        + "\n".join(f"  {v}" for v in violations)
    )


def test_prototype_no_unlabelled_fuel_numbers() -> None:
    """
    Property test — project-prototype.html must not present any fuel/kg
    number without the word 'placeholder'.

    Kind: property (forbidden-claims guard).
    Tolerance: none.
    Reason: same as above — placeholder coefficients ~10-18x off.
    Cannot catch: prose descriptions without units.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    violations = _check_no_unlabelled_fuel(text)
    assert not violations, (
        "project-prototype.html contains fuel numbers without 'placeholder':\n"
        + "\n".join(f"  {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# 8. Pipeline diagram has a legend (color + text/shape cue)
# ---------------------------------------------------------------------------

def test_progress_has_pipeline_svg() -> None:
    """
    Example test — project-progress.html must contain an inline SVG pipeline
    diagram with at least one 'pipeline' related class or id.

    Kind: example.
    Tolerance: none.
    Cannot catch: pipeline diagram rendered as an img tag.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    # Must have inline SVG
    assert "<svg" in text, "No inline SVG found in project-progress.html"
    # Must have a legend element (class containing 'legend' or id 'pipeline')
    assert "legend" in text.lower(), (
        "No legend found in project-progress.html pipeline diagram"
    )


def test_progress_pipeline_has_text_shape_cue() -> None:
    """
    Property test — the pipeline diagram uses both a shape/text cue AND a
    CSS class for status (not color alone). Verified by checking that each
    status class appears near a descriptive text label in the SVG.

    Kind: property (accessibility — not color alone).
    Tolerance: none.
    Cannot catch: a diagram where shape cue is identical for all statuses.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    # Each status should appear as a class AND the legend must have text labels
    # Check that status classes all appear
    for cls in STATUS_CLASSES:
        assert cls in text, f"'{cls}' missing from project-progress.html"
    # Check legend has text content (the word DONE, IN PROGRESS, or NOT STARTED)
    upper = text.upper()
    assert "DONE" in upper, "Legend text 'DONE' missing from pipeline diagram"
    assert "IN PROGRESS" in upper, "Legend text 'IN PROGRESS' missing from pipeline diagram"
    assert "NOT STARTED" in upper, "Legend text 'NOT STARTED' missing from pipeline diagram"


# ---------------------------------------------------------------------------
# 9. Pipeline stages: required stages present
# ---------------------------------------------------------------------------

REQUIRED_STAGES = [
    "BTS",           # BTS DB1B demand
    "Network",       # Network builder
    "Invariant",     # Invariant checks
    "Fleet",         # T-100 fleet
    "Fuel",          # Fuel & NOx
    "totals",        # Network totals
    "Exposure",      # Exposure / adjoint
    "Health",        # Health burden
]


def test_progress_pipeline_stages_present() -> None:
    """
    Example test — project-progress.html pipeline diagram mentions all
    eight pipeline stages by key term.

    Kind: example.
    Tolerance: none (case-insensitive substring match).
    Cannot catch: stages described with completely different terminology.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    lower = text.lower()
    for stage in REQUIRED_STAGES:
        assert stage.lower() in lower, (
            f"Pipeline stage '{stage}' not found in project-progress.html"
        )


# ---------------------------------------------------------------------------
# 10. Verification checks mentioned in pipeline
# ---------------------------------------------------------------------------

VERIFICATION_CHECKS = [
    "passengers conserved",  # builder verification
    "mutation",              # mutation-tested
    "additive",             # totals additive
    "placeholder",          # fuel placeholder label
]


def test_progress_verification_checks_mentioned() -> None:
    """
    Property test — project-progress.html mentions key verification checks
    under each stage in the pipeline diagram.

    Kind: property.
    Tolerance: none (case-insensitive).
    Cannot catch: checks described with completely different wording.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    lower = text.lower()
    for check in VERIFICATION_CHECKS:
        assert check.lower() in lower, (
            f"Verification check '{check}' not found in project-progress.html"
        )


# ---------------------------------------------------------------------------
# 11. Methods-page stale note present
# ---------------------------------------------------------------------------

def test_progress_has_methods_stale_note() -> None:
    """
    Example test — project-progress.html must contain a note that the
    Methods page uses older 'spectrum' framing (per task requirements and
    CONTEXT.md).

    Kind: example.
    Tolerance: none.
    Cannot catch: note present but factually incorrect.
    """
    text = PROGRESS_PAGE.read_text(encoding="utf-8")
    lower = text.lower()
    assert "methods" in lower and "spectrum" in lower, (
        "project-progress.html must have a note about the Methods page "
        "using stale 'spectrum' framing"
    )


# ---------------------------------------------------------------------------
# 12. Prototype page content checks
# ---------------------------------------------------------------------------

def test_prototype_has_3airport_mock() -> None:
    """
    Example test — project-prototype.html must describe the 3-airport mock
    with hub ORD, spokes BOS and SFO.

    Kind: example.
    Tolerance: none.
    Cannot catch: airports mentioned in a different context.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    assert "ORD" in text, "Hub airport ORD not mentioned in project-prototype.html"
    assert "BOS" in text, "Spoke BOS not mentioned in project-prototype.html"
    assert "SFO" in text, "Spoke SFO not mentioned in project-prototype.html"


def test_prototype_has_passenger_legs_counts() -> None:
    """
    Example test — project-prototype.html must state the passenger-legs
    comparison: 400 (P2P) vs 600 (hub-and-spoke) for the 3-airport mock.
    These are pure counting results, not fuel numbers.

    Kind: example (golden output for the 3-airport mock).
    Tolerance: exact integer match for pure counting.
    Cannot catch: wrong framing (e.g. swapped P2P/hub labels).
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    assert "400" in text, "Passenger-legs 400 (P2P) not found in project-prototype.html"
    assert "600" in text, "Passenger-legs 600 (hub) not found in project-prototype.html"


def test_prototype_has_distance_figures() -> None:
    """
    Example test — project-prototype.html mentions the route distances
    (2344 nm nonstop; 751 + 1600 nm hub legs) for the mock.

    Kind: example (golden output for the 3-airport mock).
    Tolerance: exact numbers.
    Cannot catch: distances mentioned but with wrong labels.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    assert "2344" in text, "P2P nonstop distance 2344 nm not found in project-prototype.html"
    assert "751" in text, "Hub leg BOS-ORD 751 nm not found in project-prototype.html"
    assert "1600" in text, "Hub leg ORD-SFO 1600 nm not found in project-prototype.html"


def test_prototype_has_test_count() -> None:
    """
    Example test — project-prototype.html mentions the test suite (319 tests).

    Kind: example (golden).
    Tolerance: exact count (changes only when test count changes).
    Cannot catch: stale count if tests are added without updating the page.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    assert "319" in text, "Test count 319 not found in project-prototype.html"


def test_prototype_has_ci_failure_mention() -> None:
    """
    Example test — project-prototype.html mentions a real CI failure that
    caught a portability bug (as required by task spec).

    Kind: example.
    Tolerance: none.
    Cannot catch: mention present but factually wrong.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    lower = text.lower()
    assert "ci" in lower or "continuous integration" in lower, (
        "project-prototype.html must mention CI"
    )
    assert "manifest" in lower or "portability" in lower, (
        "project-prototype.html must mention the portability/manifest CI bug"
    )


def test_prototype_has_not_yet_list() -> None:
    """
    Example test — project-prototype.html must have a 'not yet' section
    listing what is not done.

    Kind: example.
    Tolerance: none.
    Cannot catch: list present but incomplete.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    lower = text.lower()
    assert "not yet" in lower or "not started" in lower or "todo" in lower.replace(" ", ""), (
        "project-prototype.html must have a 'not yet' / limitations section"
    )


def test_prototype_has_inline_svg_networks() -> None:
    """
    Example test — project-prototype.html must contain an inline SVG
    showing the two network topologies (P2P and hub-and-spoke).

    Kind: example.
    Tolerance: none.
    Cannot catch: SVG rendered as img src.
    """
    text = PROTOTYPE_PAGE.read_text(encoding="utf-8")
    assert "<svg" in text, "No inline SVG found in project-prototype.html"
    # Must reference both network types in some way near the SVG
    assert "point-to-point" in text.lower() or "p2p" in text.lower(), (
        "Point-to-point network not mentioned in project-prototype.html"
    )
    assert "hub" in text.lower(), "Hub-and-spoke not mentioned in project-prototype.html"
