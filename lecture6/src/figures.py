"""
figures.py – deterministic bar chart export for hub-and-spoke vs point-to-point comparison.

Public API
----------
bar_compare(totals, title, ylabel_with_units, out_dir) -> matplotlib.figure.Figure

Design notes for determinism
-----------------------------
SVG:
  - Use matplotlib.use('Agg') so no display is needed.
  - Set rcParams to fixed values (font sizes, DPI) so that the rendered output
    does not vary with user matplotlibrc.
  - Pass metadata={'Date': None, 'Creator': None} to savefig:
      'Date': None is special-cased by matplotlib's SVG backend to suppress the
      date element entirely (no timestamp written).
      'Creator': None causes the agents loop to `continue`, skipping the
      Matplotlib version string.
      (Do NOT pass None for 'Title' or other str-type keys; they fail
      _check_is_str in the SVG backend.)
  - Set rcParams['svg.hashsalt'] to a fixed string so that SVG element ids
    (which normally use a random UUID salt) are deterministic across renders.

PNG:
  - matplotlib uses Pillow for PNG output.  By default it embeds a 'Software'
    key (Matplotlib version string) in a tEXt chunk.
  - We pass metadata={'Software': None} to suppress it (the PNG writer skips
    entries with value None: `if v is not None: pnginfo.add_text(k, v)`).
  - 'Creation Time' / 'Date' are NOT added by default so need no suppression.

Byte-identity caveat: guaranteed only within the same locked environment
(same matplotlib, pillow, freetype, zlib, Python).  Cross-environment byte
identity is not promised.
"""
from __future__ import annotations

import math
import os
import pathlib
from typing import Union

import matplotlib
matplotlib.use("Agg")  # must be before any pyplot import

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker

# ---------------------------------------------------------------------------
# Fixed rcParams for determinism and consistency
# ---------------------------------------------------------------------------
_FIXED_RC: dict = {
    # fonts
    "font.family": "DejaVu Sans",
    "font.size": 11,
    "axes.titlesize": 13,
    "axes.labelsize": 11,
    "xtick.labelsize": 10,
    "ytick.labelsize": 10,
    # figure
    "figure.figsize": [8.0, 5.0],
    "figure.dpi": 100,
    "savefig.dpi": 100,
    # svg
    "svg.hashsalt": "lecture6-deterministic",
    "svg.fonttype": "none",  # embed as text not paths → smaller, stable
    # reproducibility
    "axes.autolimit_mode": "data",
    "axes.xmargin": 0.05,
    "axes.ymargin": 0.05,
}

# SVG save metadata that strips embedded timestamps / generator strings.
# Rules (from matplotlib SVG backend source):
#   'Date': None  → suppresses the date element (special-cased in _write_metadata)
#   'Creator': None → skips the creator element (agents loop does `if agents is None: continue`)
# DO NOT pass None for 'Title' or other string-type keys – they fail _check_is_str.
_SVG_METADATA: dict = {
    "Date": None,     # suppress timestamp
    "Creator": None,  # suppress Matplotlib version string
}

# PNG save metadata: suppress the auto-generated 'Software' tEXt chunk.
# 'Software': None causes matplotlib's PNG writer to skip it
# (the loop does `if v is not None: pnginfo.add_text(k, v)`).
# 'Creation Time' / 'Date' are not added by matplotlib by default, so no
# suppression needed; we leave them absent from this dict rather than
# passing None for keys that are not special-cased in the PNG path.
_PNG_METADATA: dict = {
    "Software": None,  # suppress Matplotlib version/URL string
}

# Bar colours (fixed)
_COLOUR_P2P = "#4878CF"   # blue
_COLOUR_HUB = "#6ACC65"   # green
_BAR_WIDTH = 0.35


def _validate(totals: dict[str, tuple[float, float]]) -> None:
    """Raise ValueError if any value is NaN or negative."""
    for label, (p2p, hub) in totals.items():
        for name, val in (("P2P", p2p), ("hub", hub)):
            if math.isnan(val):
                raise ValueError(
                    f"NaN value for label={label!r} ({name}={val!r}); "
                    "all passenger totals must be finite."
                )
            if val < 0.0:
                raise ValueError(
                    f"Negative value for label={label!r} ({name}={val!r}); "
                    "negative passenger counts are not meaningful."
                )


def bar_compare(
    totals: dict[str, tuple[float, float]],
    title: str,
    ylabel_with_units: str,
    out_dir: Union[str, os.PathLike],
) -> "matplotlib.figure.Figure":
    """Create a grouped bar chart comparing P2P vs hub-and-spoke passenger totals.

    Parameters
    ----------
    totals : dict[str, tuple[float, float]]
        Mapping of label (e.g. airport code or route group) to
        (p2p_passengers, hub_passengers).  All values must be finite and ≥ 0.
    title : str
        Chart title (set on the Axes).
    ylabel_with_units : str
        Y-axis label; must contain the unit string (e.g. "Passengers (000s)").
    out_dir : path-like
        Directory to write bar_compare.svg and bar_compare.png into.
        The directory must already exist.

    Returns
    -------
    matplotlib.figure.Figure
        The Figure so callers can inspect artists (e.g. in tests).

    Raises
    ------
    ValueError
        If any value in `totals` is NaN or negative.
    """
    # Validate inputs before touching the filesystem
    _validate(totals)

    out_dir = pathlib.Path(out_dir)

    labels = list(totals.keys())
    p2p_vals = [totals[k][0] for k in labels]
    hub_vals = [totals[k][1] for k in labels]
    n = len(labels)

    import numpy as np

    x = np.arange(n)

    with plt.rc_context(_FIXED_RC):
        fig, ax = plt.subplots()

        ax.bar(x - _BAR_WIDTH / 2, p2p_vals, width=_BAR_WIDTH,
               label="Point-to-point", color=_COLOUR_P2P)
        ax.bar(x + _BAR_WIDTH / 2, hub_vals, width=_BAR_WIDTH,
               label="Hub-and-spoke", color=_COLOUR_HUB)

        ax.set_xticks(x)
        ax.set_xticklabels(labels)
        ax.set_title(title)
        ax.set_ylabel(ylabel_with_units)

        # Explicit zero baseline (acceptance criterion)
        current_top = ax.get_ylim()[1]
        ax.set_ylim(bottom=0.0, top=current_top)

        ax.legend()
        ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))

        fig.tight_layout()

        svg_path = out_dir / "bar_compare.svg"
        png_path = out_dir / "bar_compare.png"

        fig.savefig(str(svg_path), format="svg", metadata=_SVG_METADATA)
        fig.savefig(str(png_path), format="png", metadata=_PNG_METADATA)

        plt.close(fig)

    return fig
