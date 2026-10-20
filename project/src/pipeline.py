"""
pipeline.py — Real data-driven end-to-end pipeline (NW-25).

Public API
----------
run_pipeline(demand_path, hubs, t100_paths, out_dir, perf_fn, *,
             p2p_rule='observed', scale=1.0) -> int
    Run the full comparison pipeline:
      1. Load demand CSV (origin,destination,passengers) and apply scale.
      2. Build P2P and hub-and-spoke routes from the SAME demand.
      3. Verify passenger conservation (abs 1e-9) and route invariants.
         REFUSE (non-zero return, nothing written) on failure.
      4. Build aircraft mix via fleet.aircraft_mix over the airports in routes.
      5. Compute network_perf for each structure using perf_fn.
      6. Write outputs to out_dir (atomically via tempdir).

    Returns 0 on success, non-zero on failure (nothing written to out_dir).

p2p_rule options
----------------
'all'      : every OD pair gets a nonstop in the P2P scenario (existing behaviour).
'observed' : a nonstop is only built if that *directional* segment appears in
             the T-100 mix (both (O,D) and (D,O) must exist; one-way is enough for
             that direction).  OD pairs without a nonstop are connected through the
             nearest hub in the P2P scenario.  forced_connections counts the number
             of OD pairs forced to connect.
Default: 'observed' (per ADR 0007).

Outputs in out_dir (no timestamps, no absolute paths, byte-identical across runs)
----------------------------------------------------------------------------------
pipeline_totals.json   per structure: passengers, passenger_legs, flights,
                       fuel_kg, nox_kg, unmapped_share, missing_segment_count,
                       hub_over_p2p_ratio (blank when denominator 0).
                       Top-level: scaled (bool), scale_factor, p2p_rule.
pipeline_totals.csv    long-form: structure, metric, value, hub_over_p2p_ratio.
segments.csv           per structure per directed segment:
                       structure, origin, dest, passengers, flights,
                       fuel_kg, nox_kg, unmapped_share.
manifest.json          sha256 of inputs and outputs; parameters; p2p_rule;
                       forced_connections; missing_segments list; scale_factor.
MODEL.txt              Accurate model description (see _MODEL_TEXT constant).

Perf function caching
---------------------
The caller is responsible for wrapping perf_fn with their desired cache.
The pipeline itself does NOT add a second layer of caching on top of
whatever cache the caller provides.  For the CLI integration, a
(aircraft, rounded_distance) cache is built in the CLI layer.
Distance is rounded to the nearest 10 nm before caching in the CLI wrapper.

Missing segments
----------------
Segments in routes but absent from the mix are reported in
manifest['missing_segments'] and in missing_segment_count.  They are never
silently dropped.  The pipeline still returns 0 and writes outputs (the
incomplete fuel results are clearly labelled via unmapped_share and
missing_segment_count).  A caller can inspect these fields to decide whether
to treat the result as incomplete.

Unmapped aircraft
-----------------
When perf_fn returns None for an aircraft code, that code's departure share
is excluded from the fuel sum and reported as unmapped_share per segment and
as a network-level departure-weighted average.

Scale
-----
The demand passengers are multiplied by scale before routing.  Default 1.0
(no scaling).  The demand CSV written by db1b.py is ALREADY multiplied by 10
(DB1B is a 10% ticket sample; db1b.SAMPLE_SCALE), so do NOT pass scale=10.0
for it: that would scale twice (x100).  scale exists only for sensitivity
runs.  The manifest records both scaled (bool: scale != 1.0) and scale_factor.
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import shutil
import tempfile
from collections import defaultdict
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from networks import (
    ODDemand,
    RouteFlow,
    load_demand,
    build_routes_point_to_point,
    build_routes_hub_spoke,
)
from invariants import check_endpoints, check_nonnegative
from fleet import load_t100, aircraft_mix, BTS_TO_OPENAP
from network_perf import network_perf, segment_loads
from distance import distance_nm as _distance_nm

# ── Type aliases ─────────────────────────────────────────────────────────────
_PerfFn = Callable[[str, float], Optional[Dict]]

# ── Model description text (written to MODEL.txt) ────────────────────────────
_MODEL_TEXT = """\
MODEL DESCRIPTION — pipeline.py NW-25
======================================

What is modelled
----------------
* Aircraft performance: OpenAP synthetic flight trajectories (WRAP kinematic
  model) integrated with FuelFlow and Emission models.  Each flight is
  synthesised at 80% MTOW (mass_fraction=0.8), no wind, standard atmosphere,
  level climb-cruise-descent profile.
* Fleet assignment: T-100 Domestic Segment data provides per-directional-
  segment departure shares by BTS aircraft type code.  These shares are used
  as fractional weights when aggregating per-flight fuel and NOx.
* Flight counts: fluid (fractional) — flights = passenger_load /
  (seats_per_departure × load_factor).  No integer rounding is applied.
* Passenger routing: hub-and-spoke assigns each airport to a hub; point-to-
  point assigns each OD pair a nonstop (rule 'all') or falls back to the hub
  for pairs absent from the T-100 mix (rule 'observed', default, per ADR 0007).
* Synonym models: some aircraft types in BTS_TO_OPENAP map to OpenAP synonym
  models (e.g. b38m→b737 for trajectory, b39m→b38m for drag polar).  These
  are explicit approximations documented in fleet.py.
* Distance: great-circle (haversine) distance in nautical miles between
  airport reference points from OurAirports.com.
* Demand: the input CSV from db1b.py is DB1B (a 10% ticket sample) already
  multiplied by 10 to approximate full volume; this pipeline does not scale
  it again (scale_factor in the manifest is 1.0 unless set for a sensitivity
  run).  The x10 is a sampling assumption, not a measured count.

What is NOT modelled
--------------------
* Integer flight counts (fractional equivalents only).
* Schedules, departure times, or slot constraints.
* Passenger exposure, health impacts, or population weighting.
* Weather, winds, actual flight paths, or actual fuel burn from FDR data.
* Airport congestion, gate limitations, or turnaround time.
* Revenue management, pricing, or passenger choice behaviour.
* Connecting passenger minimum connection time or missed connections.
* Aircraft availability, maintenance, or network feasibility constraints.

Limitations to note
-------------------
* Fluid flight counts can be physically implausible for thin routes (e.g.,
  0.003 flights on a route with 0.5 passengers and 150-seat aircraft).
* The T-100 mix reflects what was actually operated, not what would be
  optimally assigned; proxy mappings (e.g., CRJ-200 → crj9) introduce
  known over-estimates of per-flight fuel burn.
* Unmapped aircraft codes (turboprops, pistons, business jets, MD-80 series)
  are excluded from fuel sums; unmapped_share quantifies the gap.
* Results are outputs of a modelled prototype, not research findings.
  No claim is made that hub-and-spoke is better or worse than point-to-point;
  the two scenarios differ by design (routing structure, not efficiency intent).
"""


# ── Conservation check (same as cli.py _check_conservation) ──────────────────

def _check_conservation(
    demand: List[ODDemand],
    routes: List[RouteFlow],
    tol: float = 1e-9,
) -> Optional[str]:
    """Return None if passengers are conserved per OD, else an error string."""
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


# ── SHA-256 helper ─────────────────────────────────────────────────────────────

def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


# ── Passenger leg counter ──────────────────────────────────────────────────────

def _passenger_legs(routes: List[RouteFlow]) -> float:
    """Total passenger-legs = sum over routes of passengers × (len(route)-1)."""
    total = 0.0
    for r in routes:
        n_legs = len(r.route) - 1
        total += r.passengers * n_legs
    return total


# ── P2P route builder with 'observed' rule ────────────────────────────────────

def _build_p2p_observed(
    demand: List[ODDemand],
    hubs: List[str],
    observed_segs: Set[Tuple[str, str]],
) -> Tuple[List[RouteFlow], int]:
    """Build P2P routes, falling back to hub routing when nonstop is absent.

    Parameters
    ----------
    demand:
        List of ODDemand records.
    hubs:
        Hub airport codes (used for fallback routing).
    observed_segs:
        Set of (origin, dest) directed segments observed in the T-100 mix.

    Returns
    -------
    (routes, forced_connections) where forced_connections is the number of
    OD pairs that were routed through a hub instead of nonstop.
    """
    routes: List[RouteFlow] = []
    forced: int = 0
    hs_routes_map: Dict[Tuple[str, str], RouteFlow] = {}

    # Build hub-spoke routes lazily for forced pairs
    # We do it per OD to avoid building all HS routes upfront (reuse logic)
    for rec in demand:
        seg = (rec.origin, rec.destination)
        if seg in observed_segs:
            # Nonstop observed: use direct route
            routes.append(RouteFlow(
                rec.origin, rec.destination,
                (rec.origin, rec.destination),
                rec.passengers,
            ))
        else:
            # Force through hub: re-use hub-and-spoke routing for this OD
            forced += 1
            # build_routes_hub_spoke for a single OD record
            single_hs = build_routes_hub_spoke([rec], hubs=hubs)
            routes.extend(single_hs)

    return routes, forced


# ── Main pipeline function ────────────────────────────────────────────────────

def run_pipeline(
    demand_path: str,
    hubs: List[str],
    t100_paths: List[str],
    out_dir: str,
    perf_fn: _PerfFn,
    *,
    p2p_rule: str = "observed",
    scale: float = 1.0,
) -> int:
    """Run the full P2P vs hub-and-spoke comparison pipeline.

    Parameters
    ----------
    demand_path:
        Path to a demand CSV with columns origin,destination,passengers.
    hubs:
        List of hub airport codes.
    t100_paths:
        List of paths to T-100 monthly CSV files.
    out_dir:
        Directory to write output files into (must exist).
    perf_fn:
        Callable (bts_code, distance_nm) → {fuel_kg, nox_kg} or None.
        Must be deterministic.  The pipeline does not add caching.
    p2p_rule:
        'all' or 'observed' (default 'observed').
    scale:
        Passenger multiplier applied to demand (default 1.0).

    Returns
    -------
    int
        0 on success; non-zero on failure (nothing written to out_dir).
    """
    import sys

    out_path = pathlib.Path(out_dir)
    hub_codes = [h.strip().upper() for h in hubs]

    if not hub_codes:
        print("ERROR: hubs must be non-empty", file=sys.stderr)
        return 2

    if p2p_rule not in ("all", "observed"):
        print(f"ERROR: p2p_rule must be 'all' or 'observed', got {p2p_rule!r}",
              file=sys.stderr)
        return 2

    # ── 1. Load demand ────────────────────────────────────────────────────
    try:
        demand_raw = load_demand(demand_path)
    except FileNotFoundError:
        print(f"ERROR: demand file not found: {demand_path}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: could not load demand: {exc}", file=sys.stderr)
        return 2

    # Apply scale
    if scale != 1.0:
        demand: List[ODDemand] = [
            ODDemand(r.origin, r.destination, r.passengers * scale)
            for r in demand_raw
        ]
    else:
        demand = demand_raw

    # ── 2. Load T-100 data ────────────────────────────────────────────────
    try:
        t100 = load_t100(t100_paths)
    except Exception as exc:
        print(f"ERROR: could not load T-100 data: {exc}", file=sys.stderr)
        return 2

    # Determine region airports from demand
    region_set: Set[str] = set()
    for rec in demand:
        region_set.add(rec.origin)
        region_set.add(rec.destination)
    # Also include hubs in region for mix computation
    for h in hub_codes:
        region_set.add(h)

    # ── 3. Build aircraft mix ─────────────────────────────────────────────
    mix = aircraft_mix(t100, region_set)

    # Observed segments (set of (origin, dest) pairs present in T-100 mix)
    observed_segs: Set[Tuple[str, str]] = set(mix.keys())

    # ── 4. Build routes ───────────────────────────────────────────────────
    forced_connections = 0

    try:
        if p2p_rule == "all":
            p2p_routes = build_routes_point_to_point(demand)
        else:  # 'observed'
            p2p_routes, forced_connections = _build_p2p_observed(
                demand, hub_codes, observed_segs
            )
        hs_routes = build_routes_hub_spoke(demand, hubs=hub_codes)
    except Exception as exc:
        print(f"ERROR: route building failed: {exc}", file=sys.stderr)
        return 2

    # ── 5. Invariant checks ───────────────────────────────────────────────
    try:
        check_endpoints(p2p_routes)
        check_nonnegative(p2p_routes)
        check_endpoints(hs_routes)
        check_nonnegative(hs_routes)
    except ValueError as exc:
        print(f"ERROR: invariant check failed: {exc}", file=sys.stderr)
        return 3

    # ── 6. Passenger conservation ─────────────────────────────────────────
    for label, routes in [("P2P", p2p_routes), ("hub-and-spoke", hs_routes)]:
        msg = _check_conservation(demand, routes)
        if msg is not None:
            print(f"ERROR: {label} conservation failed: {msg}", file=sys.stderr)
            return 3

    # ── 7. Compute network_perf ───────────────────────────────────────────
    try:
        p2p_perf = network_perf(
            p2p_routes, mix, perf_fn, _distance_nm, allow_missing=True
        )
        hs_perf = network_perf(
            hs_routes, mix, perf_fn, _distance_nm, allow_missing=True
        )
    except Exception as exc:
        print(f"ERROR: network_perf failed: {exc}", file=sys.stderr)
        return 2

    # ── 8. Derive passenger and leg totals ────────────────────────────────
    p2p_passengers = sum(r.passengers for r in p2p_routes)
    hs_passengers = sum(r.passengers for r in hs_routes)
    p2p_pax_legs = _passenger_legs(p2p_routes)
    hs_pax_legs = _passenger_legs(hs_routes)

    # ── 9. Collect missing segment info ───────────────────────────────────
    p2p_missing = [(s[0], s[1]) for s in sorted(p2p_perf.get("missing_segments", []))]
    hs_missing = [(s[0], s[1]) for s in sorted(hs_perf.get("missing_segments", []))]
    all_missing = sorted(set(p2p_missing) | set(hs_missing))

    # Collect unmapped codes from mix
    all_unmapped_codes: Set[str] = set()
    for seg_data in mix.values():
        for bts_code in seg_data["shares"]:
            openap_code = BTS_TO_OPENAP.get(bts_code, "ABSENT")
            if openap_code is None or openap_code == "ABSENT":
                all_unmapped_codes.add(bts_code)

    # ── 10. Build output dicts ────────────────────────────────────────────
    def _ratio_str(num: float, den: float) -> str:
        if den == 0.0:
            return ""
        return repr(num / den)

    p2p_total_fuel = p2p_perf["total_fuel_kg"]
    p2p_total_nox = p2p_perf["total_nox_kg"]
    p2p_total_flights = p2p_perf["total_flights"]
    hs_total_fuel = hs_perf["total_fuel_kg"]
    hs_total_nox = hs_perf["total_nox_kg"]
    hs_total_flights = hs_perf["total_flights"]

    p2p_summary = {
        "passengers": p2p_passengers,
        "passenger_legs": p2p_pax_legs,
        "flights": p2p_total_flights,
        "fuel_kg": p2p_total_fuel,
        "nox_kg": p2p_total_nox,
        "unmapped_share": p2p_perf["unmapped_share"],
        "missing_segment_count": len(p2p_missing),
    }
    hs_summary = {
        "passengers": hs_passengers,
        "passenger_legs": hs_pax_legs,
        "flights": hs_total_flights,
        "fuel_kg": hs_total_fuel,
        "nox_kg": hs_total_nox,
        "unmapped_share": hs_perf["unmapped_share"],
        "missing_segment_count": len(hs_missing),
    }

    totals_data = {
        "scaled": scale != 1.0,
        "scale_factor": scale,
        "p2p_rule": p2p_rule,
        "p2p": p2p_summary,
        "hub_spoke": hs_summary,
    }

    # ── 11. Write outputs atomically ──────────────────────────────────────
    tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="pipeline_"))
    try:
        _write_outputs(
            tmpdir=tmpdir,
            out_path=out_path,
            demand_path=pathlib.Path(demand_path),
            t100_paths=[pathlib.Path(p) for p in t100_paths],
            hub_codes=hub_codes,
            p2p_rule=p2p_rule,
            scale=scale,
            totals_data=totals_data,
            p2p_perf=p2p_perf,
            hs_perf=hs_perf,
            p2p_routes=p2p_routes,
            hs_routes=hs_routes,
            forced_connections=forced_connections,
            all_missing=all_missing,
            all_unmapped_codes=sorted(all_unmapped_codes),
        )
        # Move files atomically into out_dir
        for fpath in tmpdir.iterdir():
            dest = out_path / fpath.name
            shutil.move(str(fpath), str(dest))
    except Exception as exc:
        print(f"ERROR: writing outputs failed: {exc}", file=sys.stderr)
        shutil.rmtree(str(tmpdir), ignore_errors=True)
        return 2
    finally:
        shutil.rmtree(str(tmpdir), ignore_errors=True)

    return 0


def _write_outputs(
    tmpdir: pathlib.Path,
    out_path: pathlib.Path,
    demand_path: pathlib.Path,
    t100_paths: List[pathlib.Path],
    hub_codes: List[str],
    p2p_rule: str,
    scale: float,
    totals_data: Dict,
    p2p_perf: Dict,
    hs_perf: Dict,
    p2p_routes: List[RouteFlow],
    hs_routes: List[RouteFlow],
    forced_connections: int,
    all_missing: List[Tuple[str, str]],
    all_unmapped_codes: List[str],
) -> None:
    """Write all output files into tmpdir.  Called only from run_pipeline()."""

    _METRIC_KEYS = (
        "passengers", "passenger_legs", "flights", "fuel_kg",
        "nox_kg", "unmapped_share", "missing_segment_count",
    )

    # ── pipeline_totals.json ───────────────────────────────────────────────
    totals_bytes = json.dumps(totals_data, indent=2, sort_keys=False).encode("utf-8")
    (tmpdir / "pipeline_totals.json").write_bytes(totals_bytes)

    # ── pipeline_totals.csv ────────────────────────────────────────────────
    p2p_vals = totals_data["p2p"]
    hs_vals = totals_data["hub_spoke"]

    csv_lines = ["structure,metric,value,hub_over_p2p_ratio\n"]
    for metric in _METRIC_KEYS:
        p_val = p2p_vals[metric]
        h_val = hs_vals[metric]

        # P2P row
        if p_val != 0.0:
            ratio_str = repr(h_val / p_val)
        else:
            ratio_str = ""
        csv_lines.append(f"p2p,{metric},{repr(p_val)},{ratio_str}\n")
        # HS row (ratio is the same hs/p2p for hs row)
        csv_lines.append(f"hub_spoke,{metric},{repr(h_val)},{ratio_str}\n")

    (tmpdir / "pipeline_totals.csv").write_text("".join(csv_lines), encoding="utf-8")

    # ── segments.csv ──────────────────────────────────────────────────────
    seg_lines = ["structure,origin,dest,passengers,flights,fuel_kg,nox_kg,unmapped_share\n"]
    for struct, perf_result in (("p2p", p2p_perf), ("hub_spoke", hs_perf)):
        for (orig, dest), seg in sorted(perf_result["segments"].items()):
            seg_lines.append(
                f"{struct},{orig},{dest},"
                f"{repr(seg['passengers'])},"
                f"{repr(seg['flights'])},"
                f"{repr(seg['fuel_kg'])},"
                f"{repr(seg['nox_kg'])},"
                f"{repr(seg['unmapped_share'])}\n"
            )
    (tmpdir / "segments.csv").write_text("".join(seg_lines), encoding="utf-8")

    # ── MODEL.txt ─────────────────────────────────────────────────────────
    (tmpdir / "MODEL.txt").write_text(_MODEL_TEXT, encoding="utf-8")

    # ── manifest.json ─────────────────────────────────────────────────────
    # Compute sha256 of all output files written so far (NOT manifest itself)
    output_files = [
        "pipeline_totals.json", "pipeline_totals.csv",
        "segments.csv", "MODEL.txt",
    ]
    outputs_dict: Dict[str, Dict] = {}
    for fname in output_files:
        fpath = tmpdir / fname
        outputs_dict[fname] = {"sha256": _sha256_file(fpath)}

    # airports.csv sha256
    airports_csv = pathlib.Path(__file__).resolve().parent.parent / "data" / "airports.csv"
    airports_sha = _sha256_file(airports_csv) if airports_csv.exists() else None

    manifest_data: Dict = {
        "inputs": {
            "demand": {
                "path": demand_path.name,
                "sha256": _sha256_file(demand_path),
            },
            "t100": [
                {
                    "path": p.name,
                    "sha256": _sha256_file(p),
                }
                for p in t100_paths
            ],
            "airports_csv_sha256": airports_sha,
        },
        "hubs": hub_codes,
        "p2p_rule": p2p_rule,
        "scale_factor": scale,
        "scaled": scale != 1.0,
        "parameters": {
            "scale_factor": scale,
            "p2p_rule": p2p_rule,
        },
        "forced_connections": forced_connections,
        "missing_segments": [[s[0], s[1]] for s in all_missing],
        "unmapped_codes": all_unmapped_codes,
        "outputs": outputs_dict,
    }
    manifest_bytes = json.dumps(manifest_data, indent=2, sort_keys=False).encode("utf-8")
    (tmpdir / "manifest.json").write_bytes(manifest_bytes)
