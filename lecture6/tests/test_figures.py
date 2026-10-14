"""
Tests for lecture6/src/figures.py: bar_compare() deterministic SVG/PNG export.

All tests use tmp_path (pytest built-in) as the output directory so nothing
is written to the repo tree.

Determinism caveat (stated honestly): byte-identity of SVG and PNG output is
only guaranteed within the SAME locked environment (same matplotlib version,
same freetype/zlib, same OS font metrics, same Python).  Across different
environments the rendered pixels or font glyphs may differ even with fixed
rcParams.  The tests here verify byte-identity within a single test run (two
consecutive calls in the same process), which is the meaningful guarantee.
"""
from __future__ import annotations

import math
import pathlib
import sys

import pytest

# Make lecture6/src importable when pytest runs from repo root
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

# ---------------------------------------------------------------------------
# Plain import – a missing/renamed figures.py must cause a collection ERROR,
# not a silent skip.  Do NOT wrap this in try/except ImportError.
# ---------------------------------------------------------------------------
from figures import bar_compare  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

SIMPLE_TOTALS: dict[str, tuple[float, float]] = {
    "BOS": (1200.0, 950.0),
    "SFO": (3400.0, 2800.0),
    "ORD": (5600.0, 6200.0),
}


# ---------------------------------------------------------------------------
# 1. DETERMINISM – SVG byte-identical on two consecutive calls (invariance)
# ---------------------------------------------------------------------------

def test_svg_byte_identical_consecutive(tmp_path: pathlib.Path) -> None:
    """
    Invariance / determinism test.
    Tolerance: exact byte equality (no numeric tolerance).
    Reason: SVG is text-based; if any timestamp, random salt, or mutable id
    leaks into the output the bytes will differ.  We call bar_compare twice in
    the same process and assert the produced files are byte-for-byte identical.
    This catches non-determinism introduced by matplotlib's SVG renderer
    (object ids, timestamps, etc.).
    """
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    bar_compare(SIMPLE_TOTALS, title="Test", ylabel_with_units="Passengers", out_dir=out1)
    bar_compare(SIMPLE_TOTALS, title="Test", ylabel_with_units="Passengers", out_dir=out2)

    svg1 = (out1 / "bar_compare.svg").read_bytes()
    svg2 = (out2 / "bar_compare.svg").read_bytes()
    assert svg1 == svg2, (
        f"SVG output is not byte-identical across two calls "
        f"(sizes: {len(svg1)} vs {len(svg2)}).  "
        "Check for embedded timestamps, random ids, or mutable metadata."
    )


# ---------------------------------------------------------------------------
# 2. DETERMINISM – PNG byte-identical on two consecutive calls (invariance)
# ---------------------------------------------------------------------------

def test_png_byte_identical_consecutive(tmp_path: pathlib.Path) -> None:
    """
    Invariance / determinism test.
    Tolerance: exact byte equality (no numeric tolerance).
    Reason: PNG output can embed creation timestamps in tEXt/tIME chunks;
    we require those to be suppressed so the file is reproducible.
    Same-environment byte identity is the meaningful guarantee here.
    """
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    bar_compare(SIMPLE_TOTALS, title="Test", ylabel_with_units="Passengers", out_dir=out1)
    bar_compare(SIMPLE_TOTALS, title="Test", ylabel_with_units="Passengers", out_dir=out2)

    png1 = (out1 / "bar_compare.png").read_bytes()
    png2 = (out2 / "bar_compare.png").read_bytes()
    assert png1 == png2, (
        f"PNG output is not byte-identical across two calls "
        f"(sizes: {len(png1)} vs {len(png2)}).  "
        "Check for embedded tIME/tEXt/Creation-Time chunks."
    )


# ---------------------------------------------------------------------------
# 3. BAR HEIGHT TRUTH – heights read back from Axes patches match inputs (example)
# ---------------------------------------------------------------------------

def test_bar_heights_match_inputs(tmp_path: pathlib.Path) -> None:
    """
    Example / truth test.
    Tolerance: 1e-12 relative (no absolute tolerance beyond float epsilon).
    Reason: bar heights are stored as floats in the Axes; any rounding during
    data-to-display transforms that is applied before storage would be a bug.
    We verify that the patches' heights (as stored in the artists, not inferred
    from pixels) equal the input values within floating-point precision.
    bar_compare must return the matplotlib Figure so we can inspect patches.
    """
    fig = bar_compare(SIMPLE_TOTALS, title="H test", ylabel_with_units="PAX", out_dir=tmp_path)
    ax = fig.axes[0]

    patches = ax.patches  # all Rectangle artists

    # matplotlib.axes.Axes.patches order: all bars from the first ax.bar() call
    # come first (left bars = P2P), then all bars from the second call (right
    # bars = hub).  This is NOT interleaved per-group; it is grouped per series.
    # Reference: confirmed empirically against matplotlib 3.10 bar() implementation.
    p2p_vals_expected = [v[0] for v in SIMPLE_TOTALS.values()]
    hub_vals_expected = [v[1] for v in SIMPLE_TOTALS.values()]
    expected: list[float] = p2p_vals_expected + hub_vals_expected

    assert len(patches) == len(expected), (
        f"Expected {len(expected)} bar patches, got {len(patches)}"
    )

    for i, (patch, exp) in enumerate(zip(patches, expected)):
        got = patch.get_height()
        assert math.isclose(got, exp, rel_tol=1e-12, abs_tol=0.0), (
            f"Bar[{i}] height {got!r} does not match input {exp!r} "
            f"(rel diff={abs(got - exp) / exp:.2e})"
        )


# ---------------------------------------------------------------------------
# 4. ZERO BASELINE – y-axis lower limit is exactly 0.0 (example)
# ---------------------------------------------------------------------------

def test_yaxis_starts_at_zero(tmp_path: pathlib.Path) -> None:
    """
    Example test.
    Tolerance: none (exact equality to 0.0).
    Reason: comparative bar charts that don't start at zero are misleading;
    we require the Axes y-lower-limit to be exactly 0.0 (not just close to it).
    """
    fig = bar_compare(SIMPLE_TOTALS, title="Baseline test", ylabel_with_units="PAX", out_dir=tmp_path)
    ax = fig.axes[0]
    ymin, _ = ax.get_ylim()
    assert ymin == 0.0, (
        f"y-axis lower limit should be 0.0 but got {ymin!r}.  "
        "Set ax.set_ylim(bottom=0) explicitly."
    )


# ---------------------------------------------------------------------------
# 5. YLABEL CONTAINS UNITS – label string includes the ylabel_with_units argument (example)
# ---------------------------------------------------------------------------

def test_ylabel_contains_units(tmp_path: pathlib.Path) -> None:
    """
    Example test.
    Tolerance: none (string containment check).
    Reason: the acceptance criterion requires 'axis label contains units';
    we pass a distinctive units string and verify it appears in the ylabel.
    """
    units_str = "Revenue Passenger-Miles (RPM)"
    fig = bar_compare(
        SIMPLE_TOTALS,
        title="Units test",
        ylabel_with_units=units_str,
        out_dir=tmp_path,
    )
    ax = fig.axes[0]
    label = ax.get_ylabel()
    assert units_str in label, (
        f"ylabel {label!r} does not contain the units string {units_str!r}"
    )


# ---------------------------------------------------------------------------
# 6. REJECTS NaN INPUT – raises ValueError for NaN passenger count (example)
# ---------------------------------------------------------------------------

def test_rejects_nan_input(tmp_path: pathlib.Path) -> None:
    """
    Example / negative test.
    Tolerance: none.
    Reason: NaN values would silently produce invisible or zero-height bars,
    hiding data errors.  bar_compare must raise ValueError before touching the
    filesystem when any value is NaN.
    """
    bad_totals = {"BOS": (float("nan"), 100.0)}
    with pytest.raises(ValueError, match="NaN"):
        bar_compare(bad_totals, title="bad", ylabel_with_units="PAX", out_dir=tmp_path)


# ---------------------------------------------------------------------------
# 7. REJECTS NEGATIVE INPUT – raises ValueError for negative passenger count (example)
# ---------------------------------------------------------------------------

def test_rejects_negative_input(tmp_path: pathlib.Path) -> None:
    """
    Example / negative test.
    Tolerance: none.
    Reason: negative passenger counts are physically meaningless; accepting
    them would produce misleading downward bars without warning the caller.
    """
    bad_totals = {"BOS": (100.0, -5.0)}
    with pytest.raises(ValueError, match="negative"):
        bar_compare(bad_totals, title="bad", ylabel_with_units="PAX", out_dir=tmp_path)


# ---------------------------------------------------------------------------
# 8. OUTPUT FILES EXIST – both SVG and PNG are written (example)
# ---------------------------------------------------------------------------

def test_output_files_created(tmp_path: pathlib.Path) -> None:
    """
    Example test.
    Tolerance: none.
    Reason: basic smoke test that both expected output files are actually
    created in out_dir after a successful call.
    """
    bar_compare(SIMPLE_TOTALS, title="Smoke", ylabel_with_units="PAX", out_dir=tmp_path)
    assert (tmp_path / "bar_compare.svg").exists(), "SVG file not created"
    assert (tmp_path / "bar_compare.png").exists(), "PNG file not created"


# ---------------------------------------------------------------------------
# 9. RETURNS FIGURE – return value is a matplotlib Figure (example)
# ---------------------------------------------------------------------------

def test_returns_figure(tmp_path: pathlib.Path) -> None:
    """
    Example test.
    Tolerance: none.
    Reason: several other tests depend on inspecting the returned Figure;
    this test verifies the contract explicitly.
    """
    import matplotlib.figure

    result = bar_compare(SIMPLE_TOTALS, title="Ret", ylabel_with_units="PAX", out_dir=tmp_path)
    assert isinstance(result, matplotlib.figure.Figure), (
        f"bar_compare should return a matplotlib Figure, got {type(result)!r}"
    )


# ---------------------------------------------------------------------------
# 10. TITLE IS SET ON AXES (example)
# ---------------------------------------------------------------------------

def test_title_is_set(tmp_path: pathlib.Path) -> None:
    """
    Example test.
    Tolerance: none (string equality).
    Reason: the title argument should appear as the axes title so the exported
    figure is self-describing.
    """
    chosen_title = "Hub-and-Spoke vs Point-to-Point Comparison"
    fig = bar_compare(
        SIMPLE_TOTALS,
        title=chosen_title,
        ylabel_with_units="PAX",
        out_dir=tmp_path,
    )
    ax = fig.axes[0]
    assert ax.get_title() == chosen_title, (
        f"ax.get_title() returned {ax.get_title()!r}, expected {chosen_title!r}"
    )
