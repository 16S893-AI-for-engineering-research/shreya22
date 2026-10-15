"""
cli.py — P2P vs hub-and-spoke airline network comparison CLI.

Usage
-----
    python -m cli --demand PATH --hubs JFK,ORD --out DIR
                  [--seats-per-flight 150] [--lto-share 0.1]

Outputs written to --out (only on success; nothing on any failure):
    totals.json       — p2p and hub_spoke network_totals dicts + parameters + warning
    totals.csv        — one row per metric: metric,p2p,hub_spoke,hub_over_p2p_ratio
    WARNING.txt       — verbatim placeholder-model disclaimer
    manifest.json     — inputs (relative demand path, sha256), hubs, parameters,
                        output file names and sha256 of each
    passenger_legs.svg/.png  — bar chart of passenger_legs metric
    fuel_kg.svg/.png         — bar chart of fuel_kg metric

Exit codes
----------
    0  success
    2  bad usage / inputs (missing file, unknown airport, empty hubs, bad CSV)
    3  conservation or invariant check failure

Determinism guarantee
---------------------
No timestamps, no absolute paths, no usernames are written to any output.
Identical inputs produce byte-identical files within the same locked environment
(same matplotlib, pillow, freetype, zlib, Python).

⚠️  PLACEHOLDER MODEL — see WARNING.txt and totals.json["warning"].
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Public API imports (all from sibling modules on sys.path)
# ---------------------------------------------------------------------------
from networks import (
    load_demand,
    build_routes_point_to_point,
    build_routes_hub_spoke,
    RouteFlow,
)
from invariants import check_endpoints, check_nonnegative
from totals import network_totals
import figures as _figures

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_WARNING_TEXT = (
    "PLACEHOLDER MODEL: seats_per_flight, lto_share, fuel coefficients and "
    "emission indices are illustrative. Numbers here are NOT results."
)

_METRIC_KEYS = [
    "passengers",
    "passenger_legs",
    "flights",
    "aircraft_nm",
    "fuel_kg",
    "NOx_kg",
    "CO_kg",
    "HC_kg",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: pathlib.Path) -> str:
    """Return the hex-digest SHA-256 of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _check_conservation(
    demand,
    routes,
    tol: float = 1e-9,
) -> Optional[str]:
    """Return None if passengers are conserved per OD, else an error message.

    Checks that for each (origin, destination) pair the sum of passengers
    across all matching RouteFlow records equals the sum from demand records,
    to within abs tolerance *tol*.
    """
    from collections import defaultdict

    od_demand: Dict[tuple, float] = defaultdict(float)
    for rec in demand:
        od_demand[(rec.origin, rec.destination)] += rec.passengers

    od_routes: Dict[tuple, float] = defaultdict(float)
    for r in routes:
        od_routes[(r.origin, r.destination)] += r.passengers

    for od, expected in od_demand.items():
        got = od_routes.get(od, 0.0)
        if abs(got - expected) > tol:
            return (
                f"Conservation failure for OD {od}: "
                f"routes sum to {got}, demand is {expected} "
                f"(abs diff {abs(got - expected):.3e} > tol {tol:.3e})"
            )
    return None


def _build_figure(
    p2p_totals: Dict[str, float],
    hs_totals: Dict[str, float],
    metric: str,
    tmp_root: pathlib.Path,
) -> tuple[bytes, bytes]:
    """Render a bar-compare figure for *metric* and return (svg_bytes, png_bytes).

    Uses a scratch subdirectory under *tmp_root* to avoid clobbering other
    output files.  The figure files are read back and the scratch dir is
    deleted before returning.
    """
    scratch = tmp_root / f"fig_{metric}"
    scratch.mkdir(parents=True, exist_ok=True)

    p2p_val = p2p_totals[metric]
    hs_val = hs_totals[metric]

    _figures.bar_compare(
        totals={"P2P": (p2p_val, hs_val)},
        title=f"{metric}: P2P vs Hub-and-Spoke",
        ylabel_with_units=metric,
        out_dir=scratch,
    )

    svg_bytes = (scratch / "bar_compare.svg").read_bytes()
    png_bytes = (scratch / "bar_compare.png").read_bytes()

    shutil.rmtree(str(scratch))
    return svg_bytes, png_bytes


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """Run the P2P vs hub-and-spoke comparison.

    Parameters
    ----------
    argv : list of str or None
        Command-line arguments (without the program name).  Defaults to
        ``sys.argv[1:]`` when None.

    Returns
    -------
    int
        Exit code: 0 success, 2 bad input, 3 conservation/invariant failure.
    """
    parser = argparse.ArgumentParser(
        prog="cli",
        description="Compare P2P vs hub-and-spoke airline network metrics.",
    )
    parser.add_argument("--demand", required=True,
                        help="Path to demand CSV (origin,destination,passengers).")
    parser.add_argument("--hubs", required=True,
                        help="Comma-separated hub airport codes, e.g. JFK,ORD.")
    parser.add_argument("--out", required=True,
                        help="Output directory (must already exist).")
    parser.add_argument("--seats-per-flight", type=float, default=150.0,
                        dest="seats_per_flight",
                        help="⚠️ PLACEHOLDER seats per flight (default 150).")
    parser.add_argument("--lto-share", type=float, default=0.1,
                        dest="lto_share",
                        help="⚠️ PLACEHOLDER LTO fuel share (default 0.1).")

    args = parser.parse_args(argv)

    # ── 1. Parse hubs ──────────────────────────────────────────────────────
    hub_codes = [h.strip().upper() for h in args.hubs.split(",") if h.strip()]
    if not hub_codes:
        print("ERROR: --hubs must be a non-empty comma-separated list.", file=sys.stderr)
        return 2

    # ── 2. Load demand ────────────────────────────────────────────────────
    demand_path_as_given = args.demand
    demand_path = pathlib.Path(demand_path_as_given)
    try:
        demand = load_demand(str(demand_path))
    except FileNotFoundError:
        print(f"ERROR: demand file not found: {demand_path_as_given}", file=sys.stderr)
        return 2
    except (KeyError, ValueError) as exc:
        print(f"ERROR: malformed demand CSV: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: could not load demand: {exc}", file=sys.stderr)
        return 2

    out_dir = pathlib.Path(args.out)

    # ── 3. Build routes ───────────────────────────────────────────────────
    try:
        p2p_routes = build_routes_point_to_point(demand)
        hs_routes = build_routes_hub_spoke(demand, hubs=hub_codes)
    except (KeyError, ValueError) as exc:
        print(f"ERROR: could not build routes: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: route building failed: {exc}", file=sys.stderr)
        return 2

    # ── 4. Run invariant checks (exit 3 on failure) ───────────────────────
    try:
        check_endpoints(p2p_routes)
        check_nonnegative(p2p_routes)
        check_endpoints(hs_routes)
        check_nonnegative(hs_routes)
    except ValueError as exc:
        print(f"ERROR: invariant check failed: {exc}", file=sys.stderr)
        return 3

    # ── 5. Passenger conservation (exit 3 on failure) ─────────────────────
    for label, routes in [("P2P", p2p_routes), ("hub-and-spoke", hs_routes)]:
        msg = _check_conservation(demand, routes)
        if msg is not None:
            print(f"ERROR: {label} passenger conservation failed: {msg}", file=sys.stderr)
            return 3

    # ── 6. Compute network totals (exit 2 on distance/fuel error) ─────────
    try:
        p2p_totals = network_totals(
            p2p_routes,
            seats_per_flight=args.seats_per_flight,
            lto_share=args.lto_share,
        )
        hs_totals = network_totals(
            hs_routes,
            seats_per_flight=args.seats_per_flight,
            lto_share=args.lto_share,
        )
    except (KeyError, ValueError) as exc:
        print(f"ERROR: computing network totals failed: {exc}", file=sys.stderr)
        return 2

    # ── 7. Build all outputs in a temp dir, then move atomically ──────────
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="cli_lecture6_"))
    try:
        _write_outputs(
            tmpdir=tmpdir,
            demand_path_as_given=demand_path_as_given,
            demand_path=demand_path,
            hub_codes=hub_codes,
            seats_per_flight=args.seats_per_flight,
            lto_share=args.lto_share,
            p2p_totals=p2p_totals,
            hs_totals=hs_totals,
        )

        # Move each file from tmpdir into out_dir
        for fname in tmpdir.iterdir():
            dest = out_dir / fname.name
            shutil.move(str(fname), str(dest))

    except Exception as exc:
        print(f"ERROR: writing outputs failed: {exc}", file=sys.stderr)
        # Clean up temp dir; out_dir left empty (nothing moved yet on error)
        shutil.rmtree(str(tmpdir), ignore_errors=True)
        return 2
    finally:
        shutil.rmtree(str(tmpdir), ignore_errors=True)

    return 0


def _write_outputs(
    tmpdir: pathlib.Path,
    demand_path_as_given: str,
    demand_path: pathlib.Path,
    hub_codes: List[str],
    seats_per_flight: float,
    lto_share: float,
    p2p_totals: Dict[str, float],
    hs_totals: Dict[str, float],
) -> None:
    """Write all output files into *tmpdir*.  Called only from main()."""

    # ── totals.json ────────────────────────────────────────────────────────
    totals_data = {
        "warning": _WARNING_TEXT,
        "parameters": {
            "seats_per_flight": seats_per_flight,
            "lto_share": lto_share,
        },
        "p2p": {k: p2p_totals[k] for k in _METRIC_KEYS},
        "hub_spoke": {k: hs_totals[k] for k in _METRIC_KEYS},
    }
    totals_json_bytes = json.dumps(totals_data, indent=2, sort_keys=False).encode("utf-8")
    (tmpdir / "totals.json").write_bytes(totals_json_bytes)

    # ── totals.csv ─────────────────────────────────────────────────────────
    csv_lines = ["metric,p2p,hub_spoke,hub_over_p2p_ratio\n"]
    for metric in _METRIC_KEYS:
        p2p_val = p2p_totals[metric]
        hs_val = hs_totals[metric]
        if p2p_val != 0.0:
            ratio_str = repr(hs_val / p2p_val)
        else:
            ratio_str = ""
        csv_lines.append(
            f"{metric},{repr(p2p_val)},{repr(hs_val)},{ratio_str}\n"
        )
    (tmpdir / "totals.csv").write_text("".join(csv_lines), encoding="utf-8")

    # ── WARNING.txt ────────────────────────────────────────────────────────
    (tmpdir / "WARNING.txt").write_text(_WARNING_TEXT + "\n", encoding="utf-8")

    # ── Figures (passenger_legs and fuel_kg) ───────────────────────────────
    fig_scratch = tmpdir / "_fig_scratch"
    fig_scratch.mkdir()

    for metric in ("passenger_legs", "fuel_kg"):
        p2p_val = p2p_totals[metric]
        hs_val = hs_totals[metric]

        metric_scratch = fig_scratch / metric
        metric_scratch.mkdir()

        _figures.bar_compare(
            totals={"P2P": (p2p_val, hs_val)},
            title=f"{metric}: P2P vs Hub-and-Spoke",
            ylabel_with_units=metric,
            out_dir=metric_scratch,
        )

        # bar_compare writes bar_compare.{svg,png}; rename to metric name
        (metric_scratch / "bar_compare.svg").rename(tmpdir / f"{metric}.svg")
        (metric_scratch / "bar_compare.png").rename(tmpdir / f"{metric}.png")

    shutil.rmtree(str(fig_scratch))

    # ── manifest.json ──────────────────────────────────────────────────────
    # Compute SHA-256 of every output file already written to tmpdir
    output_files = [
        "totals.json",
        "totals.csv",
        "WARNING.txt",
        "passenger_legs.svg",
        "passenger_legs.png",
        "fuel_kg.svg",
        "fuel_kg.png",
    ]
    outputs_dict = {}
    for fname in output_files:
        fpath = tmpdir / fname
        outputs_dict[fname] = {"sha256": _sha256_file(fpath)}

    manifest_data = {
        "demand": {
            "path": demand_path_as_given,
            "sha256": _sha256_file(demand_path),
        },
        "hubs": hub_codes,
        "parameters": {
            "seats_per_flight": seats_per_flight,
            "lto_share": lto_share,
        },
        "outputs": outputs_dict,
    }
    manifest_bytes = json.dumps(manifest_data, indent=2, sort_keys=False).encode("utf-8")
    (tmpdir / "manifest.json").write_bytes(manifest_bytes)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sys.exit(main())
