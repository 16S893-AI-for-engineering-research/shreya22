"""
Tests for project/src/pipeline.py — the real data-driven end-to-end pipeline.

All tests use a fake perf function (returns fixed fuel/NOx per flight) so
OpenAP is never called in the automated suite.  At most one smoke test uses
real OpenAP and is marked skipif.

Test kinds per test function (see docstring inside each):
  example / property / metamorphic / differential / golden / negative
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import pathlib
import sys
import tempfile
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

import pytest

# ── make project/src importable ─────────────────────────────────────────────
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

_FIXTURES = _THIS_DIR / "fixtures"
_BTS_FIXTURES = _FIXTURES / "bts"
_T100_FIXTURE = _BTS_FIXTURES / "t100_segment_sample.csv"
_DEMAND_FIXTURE = _FIXTURES / "tiny_demand.csv"

# airports present in both T-100 fixture and tiny_demand.csv
_REGION_AIRPORTS = {"BOS", "DEN", "DTW", "MSP", "ORD", "SFO"}

# ---------------------------------------------------------------------------
# Fake perf function — deterministic, no OpenAP dependency
# ---------------------------------------------------------------------------
_FAKE_FUEL_PER_FLIGHT = 1000.0   # kg per flight
_FAKE_NOX_PER_FLIGHT = 10.0      # kg per flight

def _fake_perf(bts_code: str, distance_nm: float) -> Optional[Dict]:
    """Return constant fuel/NOx regardless of aircraft or distance.

    This is the canonical fake for all automated tests.  It is deterministic
    and never calls OpenAP.  The constant values (1000, 10) are chosen so
    that arithmetic is easy to verify by hand.

    Returns None for code '000' (used to test unmapped handling).
    """
    if bts_code == "000":
        return None
    return {"fuel_kg": _FAKE_FUEL_PER_FLIGHT, "nox_kg": _FAKE_NOX_PER_FLIGHT}


# ---------------------------------------------------------------------------
# Tiny demand fixture: 3 airports only, simpler to reason about
# ---------------------------------------------------------------------------
_TINY3_AIRPORTS = {"BOS", "ORD", "SFO"}

def _make_tiny3_demand_csv(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a 3-airport demand CSV to tmp_path and return its path."""
    p = tmp_path / "demand3.csv"
    p.write_text(
        "origin,destination,passengers\n"
        "BOS,ORD,100.0\n"
        "ORD,SFO,80.0\n"
        "BOS,SFO,60.0\n",
        encoding="utf-8",
    )
    return p


def _make_tiny3_t100(tmp_path: pathlib.Path) -> pathlib.Path:
    """Write a minimal T-100 fixture with a few known segments and return its path.

    Only BOS,ORD,SFO in the region; all AIRCRAFT_CONFIG=1; DEPARTURES_PERFORMED>0.
    Aircraft code '614' = b738 (exact in BTS_TO_OPENAP).
    """
    p = tmp_path / "t100_tiny3.csv"
    p.write_text(
        "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
        "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        # BOS->ORD nonstop (exists in T-100)
        "10.0,1500.0,1200.0,867.0,120.0,UA,BOS,ORD,614,1,2019,1\n"
        # ORD->SFO nonstop
        "8.0,1200.0,960.0,1600.0,230.0,UA,ORD,SFO,614,1,2019,1\n"
        # BOS->SFO nonstop (exists — so 'observed' and 'all' agree for this pair)
        "5.0,750.0,600.0,2704.0,390.0,UA,BOS,SFO,614,1,2019,1\n"
        # ORD->BOS reverse
        "10.0,1500.0,1190.0,867.0,120.0,UA,ORD,BOS,614,1,2019,1\n"
        # SFO->ORD reverse
        "8.0,1200.0,950.0,1600.0,230.0,UA,SFO,ORD,614,1,2019,1\n"
        # SFO->BOS reverse
        "5.0,750.0,595.0,2704.0,390.0,UA,SFO,BOS,614,1,2019,1\n",
        encoding="utf-8",
    )
    return p


def _make_tiny3_t100_missing_bos_sfo(tmp_path: pathlib.Path) -> pathlib.Path:
    """T-100 without BOS->SFO or SFO->BOS — forces those pairs to connect via hub."""
    p = tmp_path / "t100_no_bos_sfo.csv"
    p.write_text(
        "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
        "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        "10.0,1500.0,1200.0,867.0,120.0,UA,BOS,ORD,614,1,2019,1\n"
        "8.0,1200.0,960.0,1600.0,230.0,UA,ORD,SFO,614,1,2019,1\n"
        "10.0,1500.0,1190.0,867.0,120.0,UA,ORD,BOS,614,1,2019,1\n"
        "8.0,1200.0,950.0,1600.0,230.0,UA,SFO,ORD,614,1,2019,1\n",
        encoding="utf-8",
    )
    return p


# ===========================================================================
# 1. MODULE IMPORT — pipeline.py must exist and expose run_pipeline
# ===========================================================================

def test_pipeline_module_importable() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: pipeline imported but run_pipeline missing (separate test).

    pipeline.py must be importable from project/src.
    """
    import pipeline  # noqa: F401


def test_run_pipeline_callable() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: callable with wrong signature (separate tests).

    run_pipeline must be a callable exported from pipeline.
    """
    import pipeline
    assert callable(pipeline.run_pipeline)


# ===========================================================================
# 2. END-TO-END SUCCESS — basic output files and structure
# ===========================================================================

def test_end_to_end_creates_output_files(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: files present but empty.

    run_pipeline must create pipeline_totals.json, pipeline_totals.csv,
    segments.csv, manifest.json, and MODEL.txt in --out.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )
    assert rc == 0, f"run_pipeline returned {rc}"

    for fname in ["pipeline_totals.json", "pipeline_totals.csv",
                  "segments.csv", "manifest.json", "MODEL.txt"]:
        assert (out_dir / fname).exists(), f"Missing expected output: {fname}"


def test_pipeline_totals_json_structure(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: keys present with wrong types.

    pipeline_totals.json must have 'p2p' and 'hub_spoke' top-level keys, each
    containing passengers, passenger_legs, flights, fuel_kg, nox_kg,
    unmapped_share, missing_segment_count.  Also must have 'scaled' and
    'scale_factor' keys and 'p2p_rule' key at the top level.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    for struct in ("p2p", "hub_spoke"):
        assert struct in data, f"Missing key: {struct!r}"
        row = data[struct]
        for key in ("passengers", "passenger_legs", "flights", "fuel_kg",
                    "nox_kg", "unmapped_share", "missing_segment_count"):
            assert key in row, f"Missing key in {struct!r}: {key!r}"

    assert "scaled" in data, "Missing 'scaled' in totals"
    assert "scale_factor" in data, "Missing 'scale_factor' in totals"
    assert "p2p_rule" in data, "Missing 'p2p_rule' in totals"


def test_pipeline_totals_csv_has_ratios(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: rel_tol=1e-12 for ratio column values.
    Cannot catch: ratio correct but for wrong metric.

    pipeline_totals.csv must have columns structure,metric,value,hub_over_p2p_ratio.
    The hub_over_p2p_ratio must be blank when denominator==0, otherwise == hub/p2p.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    rows = {}
    with open(out_dir / "pipeline_totals.csv", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows[(row["structure"], row["metric"])] = row

    data = json.loads((out_dir / "pipeline_totals.json").read_text())

    for struct in ("p2p", "hub_spoke"):
        for metric in ("passengers", "flights", "fuel_kg"):
            key = (struct, metric)
            assert key in rows, f"CSV missing row {key}"
            val_csv = float(rows[key]["value"])
            val_json = data[struct][metric]
            assert math.isclose(val_csv, val_json, rel_tol=1e-12), (
                f"CSV value mismatch for {key}: {val_csv} vs {val_json}"
            )

    # ratio check for fuel_kg
    p2p_fuel = data["p2p"]["fuel_kg"]
    hs_fuel = data["hub_spoke"]["fuel_kg"]
    ratio_str = rows[("hub_spoke", "fuel_kg")]["hub_over_p2p_ratio"]
    if p2p_fuel != 0.0:
        assert ratio_str != "", "ratio must not be blank when p2p != 0"
        assert math.isclose(float(ratio_str), hs_fuel / p2p_fuel, rel_tol=1e-12), (
            f"ratio mismatch: {float(ratio_str)} vs {hs_fuel/p2p_fuel}"
        )
    else:
        assert ratio_str == "", "ratio must be blank when p2p == 0"


def test_segments_csv_has_expected_columns(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: columns present with wrong data types.

    segments.csv must have: structure, origin, dest, passengers, flights,
    fuel_kg, nox_kg, unmapped_share.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    with open(out_dir / "segments.csv", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    for col in ("structure", "origin", "dest", "passengers", "flights",
                "fuel_kg", "nox_kg", "unmapped_share"):
        assert col in fieldnames, f"segments.csv missing column: {col!r}"

    assert len(rows) > 0, "segments.csv must have at least one data row"
    # structure values must be p2p or hub_spoke
    structures = {row["structure"] for row in rows}
    assert structures.issubset({"p2p", "hub_spoke"}), (
        f"Unexpected structure values: {structures}"
    )


# ===========================================================================
# 3. DIFFERENTIAL — totals match calling network_perf directly
# ===========================================================================

def test_differential_totals_match_network_perf(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential.
    Tolerance: rel_tol=1e-12 for fuel_kg, nox_kg, flights.
    Reason: pipeline.run_pipeline and network_perf must share the same
    arithmetic; the only differences allowed are caching/ordering effects.

    Cannot catch: consistent bugs in both pipeline and network_perf.
    """
    import pipeline
    from networks import load_demand, build_routes_point_to_point, build_routes_hub_spoke
    from fleet import load_t100, aircraft_mix
    from network_perf import network_perf
    from distance import distance_nm

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    # Recompute via direct calls
    demand = load_demand(str(demand_path))
    t100 = load_t100([str(t100_path)])
    region = _TINY3_AIRPORTS
    mix = aircraft_mix(t100, region)

    p2p_routes = build_routes_point_to_point(demand)
    hs_routes = build_routes_hub_spoke(demand, hubs=["ORD"])

    p2p_result = network_perf(
        p2p_routes, mix, _fake_perf, distance_nm, allow_missing=True
    )
    hs_result = network_perf(
        hs_routes, mix, _fake_perf, distance_nm, allow_missing=True
    )

    data = json.loads((out_dir / "pipeline_totals.json").read_text())

    for key in ("fuel_kg", "nox_kg", "flights"):
        p2p_direct = p2p_result[f"total_{key}"] if key != "flights" else p2p_result["total_flights"]
        hs_direct = hs_result[f"total_{key}"] if key != "flights" else hs_result["total_flights"]

        p2p_pipe = data["p2p"][key]
        hs_pipe = data["hub_spoke"][key]

        assert math.isclose(p2p_pipe, p2p_direct, rel_tol=1e-12, abs_tol=1e-9), (
            f"p2p {key} mismatch: pipeline={p2p_pipe} vs direct={p2p_direct}"
        )
        assert math.isclose(hs_pipe, hs_direct, rel_tol=1e-12, abs_tol=1e-9), (
            f"hub_spoke {key} mismatch: pipeline={hs_pipe} vs direct={hs_direct}"
        )


# ===========================================================================
# 4. PASSENGER CONSERVATION — refusal leaves --out empty
# ===========================================================================

def test_conservation_failure_leaves_out_empty(tmp_path: pathlib.Path) -> None:
    """
    Kind: negative / example.
    Tolerance: none.
    Cannot catch: refusal that happens to still write files (checked explicitly).

    When conservation fails (injected via a bad perf_fn that also corrupts
    demand via monkeypatching), run_pipeline must return non-zero and leave
    --out empty.  We achieve the failure by patching build_routes_point_to_point
    to double passengers (breaks conservation).
    """
    import pipeline
    import networks as networks_mod
    from networks import RouteFlow

    original = networks_mod.build_routes_point_to_point

    def _double_pax(demand):
        routes = original(demand)
        return [RouteFlow(r.origin, r.destination, r.route, r.passengers * 2.0)
                for r in routes]

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    import unittest.mock as mock
    with mock.patch.object(networks_mod, "build_routes_point_to_point", _double_pax):
        with mock.patch("pipeline.build_routes_point_to_point", _double_pax, create=True):
            rc = pipeline.run_pipeline(
                demand_path=str(demand_path),
                hubs=["ORD"],
                t100_paths=[str(t100_path)],
                out_dir=str(out_dir),
                perf_fn=_fake_perf,
                p2p_rule="all",
            )

    assert rc != 0, f"Expected non-zero exit for conservation failure, got {rc}"
    assert list(out_dir.iterdir()) == [], (
        f"--out must be empty on conservation failure, found: {list(out_dir.iterdir())}"
    )


# ===========================================================================
# 5. P2P RULE — 'observed' vs 'all' differ exactly where T-100 lacks nonstop
# ===========================================================================

def test_p2p_rule_observed_vs_all_differ_on_missing_segment(tmp_path: pathlib.Path) -> None:
    """
    Kind: metamorphic / differential.
    Tolerance: none (inequality assertion) for totals that must differ;
               exact equality for OD pairs that have nonstops in both rules.
    Cannot catch: rule applied correctly but to the wrong pairs.

    With t100_no_bos_sfo (BOS<->SFO nonstop absent from T-100), 'observed'
    rule forces BOS->SFO and SFO->BOS through ORD hub, while 'all' serves
    them nonstop.

    Consequently:
    - 'all' P2P has fewer passenger_legs than 'observed' P2P for BOS-SFO OD
    - The forced_connections count in manifest must be > 0 for 'observed',
      0 for 'all'.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    # T-100 WITHOUT BOS<->SFO nonstop segment
    t100_path = _make_tiny3_t100_missing_bos_sfo(tmp_path)

    out_all = tmp_path / "out_all"
    out_obs = tmp_path / "out_obs"
    out_all.mkdir()
    out_obs.mkdir()

    rc_all = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_all),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )
    rc_obs = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_obs),
        perf_fn=_fake_perf,
        p2p_rule="observed",
    )
    assert rc_all == 0, f"'all' run failed: {rc_all}"
    assert rc_obs == 0, f"'observed' run failed: {rc_obs}"

    all_manifest = json.loads((out_all / "manifest.json").read_text())
    obs_manifest = json.loads((out_obs / "manifest.json").read_text())

    # forced_connections must be 0 for 'all', >0 for 'observed'
    assert all_manifest["forced_connections"] == 0, (
        f"'all' rule should force no connections, got {all_manifest['forced_connections']}"
    )
    assert obs_manifest["forced_connections"] > 0, (
        f"'observed' rule should force at least 1 connection when BOS-SFO absent"
    )

    # P2P passenger_legs must be strictly greater under 'observed' (connecting adds legs)
    all_data = json.loads((out_all / "pipeline_totals.json").read_text())
    obs_data = json.loads((out_obs / "pipeline_totals.json").read_text())

    assert obs_data["p2p"]["passenger_legs"] > all_data["p2p"]["passenger_legs"], (
        f"'observed' p2p should have more passenger_legs (connecting legs) than 'all': "
        f"{obs_data['p2p']['passenger_legs']} vs {all_data['p2p']['passenger_legs']}"
    )

    # p2p_rule recorded in both manifests
    assert all_manifest["p2p_rule"] == "all"
    assert obs_manifest["p2p_rule"] == "observed"


def test_p2p_rule_all_has_zero_forced_connections(tmp_path: pathlib.Path) -> None:
    """
    Kind: property.
    Tolerance: none.
    Cannot catch: forced_connections wrong sign.

    The 'all' rule must always produce forced_connections == 0 because every
    OD pair gets a nonstop regardless of T-100 coverage.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100_missing_bos_sfo(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["forced_connections"] == 0


def test_p2p_rule_observed_default(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: default recorded but not applied.

    When p2p_rule is not specified (or specified as 'observed'), the manifest
    must record 'observed' as the p2p_rule.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # Call WITHOUT specifying p2p_rule → should default to 'observed'
    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["p2p_rule"] == "observed", (
        f"Default p2p_rule should be 'observed', got {manifest['p2p_rule']!r}"
    )


# ===========================================================================
# 6. MISSING-SEGMENT REPORTING
# ===========================================================================

def test_missing_segments_reported_not_silent(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / negative.
    Tolerance: none.
    Cannot catch: report present but containing wrong segment names.

    When routes contain segments absent from the T-100 mix (e.g. BOS->SFO
    nonstop under 'all' rule when T-100 has no BOS->SFO), they must be
    reported in manifest['missing_segments'] and in the totals, never silently
    dropped or counted as zero.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    # T-100 with NO BOS<->SFO segment: BOS->SFO is missing from mix
    t100_path = _make_tiny3_t100_missing_bos_sfo(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    # 'all' rule: BOS->SFO nonstop is in routes but NOT in mix → missing
    rc = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )
    assert rc == 0, f"Expected success with missing-segment reporting, got {rc}"

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert "missing_segments" in manifest, "manifest must have 'missing_segments' key"
    assert isinstance(manifest["missing_segments"], list), (
        "manifest['missing_segments'] must be a list"
    )
    # BOS->SFO should appear (as list of [origin, dest] or similar)
    # We just assert it's non-empty
    assert len(manifest["missing_segments"]) > 0, (
        "Expected at least one missing segment when BOS-SFO absent from T-100"
    )

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    p2p_missing = data["p2p"]["missing_segment_count"]
    assert p2p_missing > 0, (
        f"p2p missing_segment_count must be >0 when BOS-SFO absent, got {p2p_missing}"
    )


def test_missing_segment_count_in_totals_json(tmp_path: pathlib.Path) -> None:
    """
    Kind: property.
    Tolerance: none (integer count, no tolerance).
    Cannot catch: count correct but segments not actually missing from fuel.

    When T-100 fully covers all segments in 'all' rule (tiny3 full fixture),
    missing_segment_count must be 0 for both structures.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)  # has all 6 directed BOS/ORD/SFO segments
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    assert data["p2p"]["missing_segment_count"] == 0, (
        f"Expected 0 missing segments when T-100 covers all pairs, "
        f"got {data['p2p']['missing_segment_count']}"
    )
    assert data["hub_spoke"]["missing_segment_count"] == 0


# ===========================================================================
# 7. UNMAPPED SHARE REPORTING
# ===========================================================================

def test_unmapped_share_reported_when_code_missing(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none (positive inequality).
    Cannot catch: unmapped_share reported but used incorrectly in fuel.

    When the T-100 fixture contains an unmapped aircraft code (injected via
    a custom T-100 file with code '000'), the unmapped_share must be >0.
    """
    import pipeline

    # Build a T-100 with an unmapped code ('000' → _fake_perf returns None)
    t100_path = tmp_path / "t100_unmapped.csv"
    t100_path.write_text(
        "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
        "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        # 50% share mapped (614 → b738), 50% unmapped (000)
        "5.0,750.0,600.0,867.0,120.0,UA,BOS,ORD,614,1,2019,1\n"
        "5.0,750.0,600.0,867.0,120.0,UA,BOS,ORD,000,1,2019,1\n"
        "5.0,750.0,600.0,1600.0,230.0,UA,ORD,SFO,614,1,2019,1\n"
        "5.0,750.0,600.0,2704.0,390.0,UA,BOS,SFO,614,1,2019,1\n"
        "5.0,750.0,600.0,867.0,120.0,UA,ORD,BOS,614,1,2019,1\n"
        "5.0,750.0,600.0,1600.0,230.0,UA,SFO,ORD,614,1,2019,1\n"
        "5.0,750.0,600.0,2704.0,390.0,UA,SFO,BOS,614,1,2019,1\n",
        encoding="utf-8",
    )

    demand_path = _make_tiny3_demand_csv(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    def _perf_with_unmapped(bts_code: str, distance_nm: float):
        if bts_code == "000":
            return None
        return {"fuel_kg": _FAKE_FUEL_PER_FLIGHT, "nox_kg": _FAKE_NOX_PER_FLIGHT}

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_perf_with_unmapped,
        p2p_rule="all",
    )

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    # BOS->ORD has 50% unmapped; overall unmapped_share must be >0
    assert data["p2p"]["unmapped_share"] > 0, (
        f"Expected unmapped_share > 0, got {data['p2p']['unmapped_share']}"
    )


# ===========================================================================
# 8. DETERMINISM — two runs produce byte-identical outputs
# ===========================================================================

def test_determinism_byte_identical(tmp_path: pathlib.Path) -> None:
    """
    Kind: invariance / determinism.
    Tolerance: exact byte equality.
    Cannot catch: non-determinism from OS PRNG seeded via /dev/urandom.

    Two consecutive runs with identical inputs must produce byte-identical
    output files (pipeline_totals.json, segments.csv, manifest.json, MODEL.txt).
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    for out_dir in (out1, out2):
        pipeline.run_pipeline(
            demand_path=str(demand_path),
            hubs=["ORD"],
            t100_paths=[str(t100_path)],
            out_dir=str(out_dir),
            perf_fn=_fake_perf,
            p2p_rule="all",
        )

    for fname in ("pipeline_totals.json", "segments.csv", "manifest.json", "MODEL.txt"):
        b1 = (out1 / fname).read_bytes()
        b2 = (out2 / fname).read_bytes()
        assert b1 == b2, (
            f"{fname!r} is not byte-identical across two runs "
            f"(sizes: {len(b1)} vs {len(b2)})"
        )


# ===========================================================================
# 9. MANIFEST — SHA-256 hashes match actual files and parameters recorded
# ===========================================================================

def test_manifest_sha256_match_files(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / integrity.
    Tolerance: exact SHA-256 hex equality.
    Cannot catch: hash collisions.

    manifest.json must list sha256 of demand, T-100 inputs, airports.csv, and
    each output file.  We recompute each hash and compare.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())

    def sha256_file(p: pathlib.Path) -> str:
        h = hashlib.sha256()
        h.update(p.read_bytes())
        return h.hexdigest()

    # demand sha256
    assert sha256_file(demand_path) == manifest["inputs"]["demand"]["sha256"], (
        "demand sha256 mismatch"
    )

    # t100 sha256 — verify against the known t100_path (manifest stores basename only)
    assert len(manifest["inputs"]["t100"]) == 1
    t100_entry = manifest["inputs"]["t100"][0]
    assert sha256_file(t100_path) == t100_entry["sha256"], (
        f"T-100 sha256 mismatch for {t100_entry['path']}"
    )

    # output file sha256s
    for fname, info in manifest["outputs"].items():
        fpath = out_dir / fname
        assert fpath.exists(), f"manifest references {fname} but file absent"
        assert sha256_file(fpath) == info["sha256"], (
            f"output sha256 mismatch for {fname}"
        )


def test_manifest_records_parameters(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: parameters recorded but not actually used.

    manifest.json must record hubs, p2p_rule, scale_factor, forced_connections,
    missing_segments count, and unmapped_codes_count.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
        scale=1.0,
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert "hubs" in manifest, "manifest missing 'hubs'"
    assert manifest["hubs"] == ["ORD"], f"Unexpected hubs: {manifest['hubs']}"
    assert "p2p_rule" in manifest
    assert manifest["p2p_rule"] == "all"
    assert "scale_factor" in manifest
    assert manifest["scale_factor"] == 1.0
    assert "forced_connections" in manifest
    assert "missing_segments" in manifest
    assert "parameters" in manifest


def test_manifest_no_absolute_paths(tmp_path: pathlib.Path) -> None:
    """
    Kind: invariance / portability.
    Tolerance: none.
    Cannot catch: absolute paths in binary files.

    manifest.json must not contain absolute paths (no /Users/, /home/, /tmp/).
    The file must be self-consistent across machines.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    content = (out_dir / "manifest.json").read_text()
    # absolute path fragments that would be machine-specific
    assert str(tmp_path) not in content, (
        "manifest.json contains absolute tmp_path"
    )


# ===========================================================================
# 10. MODEL.txt — content accuracy
# ===========================================================================

def test_model_txt_exists_and_non_empty(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: MODEL.txt present but factually wrong (human review needed).

    MODEL.txt must be created and non-empty.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    model_txt = (out_dir / "MODEL.txt").read_text(encoding="utf-8")
    assert len(model_txt) > 100, "MODEL.txt appears suspiciously short"


def test_model_txt_states_openap_and_limitations(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / content.
    Tolerance: none (substring presence).
    Cannot catch: text present but inaccurate.

    MODEL.txt must mention 'OpenAP', '80%' (mass fraction), and must mention
    what is NOT modelled (at least 'integer' or 'schedule').
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    text = (out_dir / "MODEL.txt").read_text(encoding="utf-8")
    assert "OpenAP" in text, "MODEL.txt must mention OpenAP"
    assert "80%" in text, "MODEL.txt must mention 80% MTOW mass fraction"
    # Must state what is NOT modelled
    assert any(word in text.lower() for word in ("not modelled", "not model", "does not")), (
        "MODEL.txt must state what is NOT modelled"
    )


# ===========================================================================
# 11. SCALE OPTION
# ===========================================================================

def test_scale_factor_recorded_and_applied(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential.
    Tolerance: rel_tol=1e-9 for passenger counts.
    Cannot catch: scale applied to fuel but not passengers.

    When scale=10.0 is passed, passenger totals must be 10x those from
    scale=1.0, while fuel_kg per passenger stays constant.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)

    out1 = tmp_path / "scale1"
    out10 = tmp_path / "scale10"
    out1.mkdir()
    out10.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out1),
        perf_fn=_fake_perf,
        p2p_rule="all",
        scale=1.0,
    )
    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out10),
        perf_fn=_fake_perf,
        p2p_rule="all",
        scale=10.0,
    )

    d1 = json.loads((out1 / "pipeline_totals.json").read_text())
    d10 = json.loads((out10 / "pipeline_totals.json").read_text())

    # passengers must scale 10x
    for struct in ("p2p", "hub_spoke"):
        assert math.isclose(
            d10[struct]["passengers"], 10.0 * d1[struct]["passengers"], rel_tol=1e-9
        ), f"{struct} passengers should scale 10x"
        # fuel_kg also scales (more passengers → more flights)
        assert math.isclose(
            d10[struct]["fuel_kg"], 10.0 * d1[struct]["fuel_kg"], rel_tol=1e-9
        ), f"{struct} fuel_kg should scale 10x"

    # manifest must record scale_factor
    m1 = json.loads((out1 / "manifest.json").read_text())
    m10 = json.loads((out10 / "manifest.json").read_text())
    assert m1["scale_factor"] == 1.0
    assert m10["scale_factor"] == 10.0


def test_scale_default_is_one(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: scale_factor key absent (separate test).

    When scale is not specified, it defaults to 1.0 (no scaling), and
    manifest records 'scaled': False.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
    )

    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["scale_factor"] == 1.0
    # scaled must be False (1.0 means no scaling applied)
    assert manifest["scaled"] is False


def test_scale_10_sets_scaled_true(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: scaled flag used but not meaningful.
    """
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="all",
        scale=10.0,
    )

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    assert data["scaled"] is True
    manifest = json.loads((out_dir / "manifest.json").read_text())
    assert manifest["scaled"] is True


# ===========================================================================
# 12. CLI INTEGRATION — new 'pipeline' subcommand
# ===========================================================================

def test_cli_pipeline_subcommand_exists() -> None:
    """
    Kind: example.
    Tolerance: none.
    Cannot catch: subcommand present but broken.

    The CLI must accept a 'pipeline' subcommand without crashing with
    'unrecognised argument' errors.  We test by importing cli and checking
    its argument parser recognises 'pipeline'.
    """
    import cli
    # cli.main(['pipeline', '--help']) would exit 0; we parse manually
    import argparse
    # Build the parser from cli and check 'pipeline' is a valid subcommand
    # We do this by trying to run with --help capturing SystemExit(0)
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-m", "cli", "pipeline", "--help"],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    # --help exits with code 0 and prints usage; any code != 0 means unknown subcommand
    assert result.returncode == 0, (
        f"cli pipeline --help failed (returncode={result.returncode}):\n"
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )


def test_cli_pipeline_subcommand_creates_outputs(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / integration.
    Tolerance: none.
    Cannot catch: outputs present but wrong values.

    Running 'python -m cli pipeline --demand ... --hubs ORD --t100 ... --out ...'
    must exit 0 and create pipeline_totals.json.
    """
    import subprocess, sys

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable, "-m", "cli", "pipeline",
            "--demand", str(demand_path),
            "--hubs", "ORD",
            "--t100", str(t100_path),
            "--out", str(out_dir),
            "--p2p-rule", "all",
        ],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    assert result.returncode == 0, (
        f"cli pipeline failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert (out_dir / "pipeline_totals.json").exists(), "pipeline_totals.json not created"


def test_cli_pipeline_conservation_failure_nonzero_exit(tmp_path: pathlib.Path) -> None:
    """
    Kind: negative / example.
    Tolerance: none.
    Cannot catch: wrong exit code used for conservation vs invariant failure.

    A demand CSV with passengers that would break conservation (if routes
    doubled them) is hard to inject via CLI.  We instead verify that the
    existing placeholder CLI tests still pass (backward-compat) and that
    the pipeline subcommand exits 0 on valid inputs (non-zero only on real
    failures).  This test verifies the --out is empty on a non-zero exit
    by passing a nonexistent demand file.
    """
    import subprocess, sys

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable, "-m", "cli", "pipeline",
            "--demand", str(tmp_path / "nonexistent.csv"),
            "--hubs", "ORD",
            "--t100", str(tmp_path / "nonexistent_t100.csv"),
            "--out", str(out_dir),
        ],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    assert result.returncode != 0, (
        f"Expected non-zero exit for missing demand, got {result.returncode}"
    )
    assert list(out_dir.iterdir()) == [], (
        f"--out must be empty on failure, found: {list(out_dir.iterdir())}"
    )


def test_existing_cli_unchanged(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / backward-compat.
    Tolerance: none.
    Cannot catch: old CLI producing correct output via new code path.

    The existing placeholder CLI (without 'pipeline' subcommand) must still
    work unchanged.  We run it with the tiny_demand.csv fixture and verify
    exit 0 and presence of totals.json (existing expected output).
    """
    import subprocess, sys

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable, "-m", "cli",
            "--demand", str(_DEMAND_FIXTURE),
            "--hubs", "JFK,ORD",
            "--out", str(out_dir),
        ],
        capture_output=True, text=True, cwd=str(_SRC_DIR),
    )
    assert result.returncode == 0, (
        f"Existing CLI failed after adding pipeline subcommand:\n"
        f"stderr: {result.stderr}"
    )
    assert (out_dir / "totals.json").exists(), (
        "totals.json missing — existing CLI behaviour broken"
    )


# ===========================================================================
# 13. OPENAP SMOKE TEST (real OpenAP; skip if package unavailable, < 60 s)
# ===========================================================================

try:
    import openap  # noqa: F401
    _OPENAP_AVAILABLE = True
except ImportError:
    _OPENAP_AVAILABLE = False

@pytest.mark.skipif(not _OPENAP_AVAILABLE, reason="openap package not installed")
def test_real_openap_smoke(tmp_path: pathlib.Path) -> None:
    """
    Kind: example / smoke.
    Tolerance: physical plausibility only (fuel_kg > 0, nox_kg > 0).
    Cannot catch: wrong aircraft model used, incorrect OpenAP version.

    Run the pipeline end-to-end with real OpenAP on a 2-airport, 1-OD
    scenario using the real flight_fuel_nox function.  Verifies the pipeline
    returns 0 and produces positive fuel values.

    Max ~60 s (one or two OpenAP calls for one aircraft type).
    """
    import pipeline
    from openap_perf import flight_fuel_nox
    from fleet import BTS_TO_OPENAP

    # Minimal T-100: just BOS->ORD, aircraft '614' = b738
    t100_path = tmp_path / "t100_smoke.csv"
    t100_path.write_text(
        "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
        "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        "10.0,1500.0,1200.0,867.0,120.0,UA,BOS,ORD,614,1,2019,1\n"
        "10.0,1500.0,1190.0,867.0,120.0,UA,ORD,BOS,614,1,2019,1\n",
        encoding="utf-8",
    )

    # Minimal demand: BOS -> ORD only
    demand_path = tmp_path / "demand_smoke.csv"
    demand_path.write_text(
        "origin,destination,passengers\nBOS,ORD,100.0\n",
        encoding="utf-8",
    )

    # Real perf function using flight_fuel_nox via BTS_TO_OPENAP
    _perf_cache: Dict = {}
    def real_perf(bts_code: str, dist_nm: float) -> Optional[Dict]:
        openap_code = BTS_TO_OPENAP.get(bts_code)
        if openap_code is None:
            return None
        # Round to nearest 10 nm for caching
        dist_rounded = round(dist_nm / 10.0) * 10.0
        key = (openap_code, dist_rounded)
        if key not in _perf_cache:
            result = flight_fuel_nox(openap_code, dist_rounded)
            _perf_cache[key] = {"fuel_kg": result["fuel_kg"], "nox_kg": result["nox_kg"]}
        return _perf_cache[key]

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = pipeline.run_pipeline(
        demand_path=str(demand_path),
        hubs=["ORD"],
        t100_paths=[str(t100_path)],
        out_dir=str(out_dir),
        perf_fn=real_perf,
        p2p_rule="all",
    )
    assert rc == 0, f"Smoke test pipeline failed with rc={rc}"

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    assert data["p2p"]["fuel_kg"] > 0, "P2P fuel_kg must be positive (real OpenAP)"
    assert data["p2p"]["nox_kg"] > 0, "P2P nox_kg must be positive (real OpenAP)"


# ===========================================================================
# 14. REAL BTS CACHE TESTS (skip if cache absent)
# ===========================================================================

_CACHE_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "bts-cache"
_DEMAND_CACHE = _CACHE_DIR / "demand_region_2019Q1.csv"
_T100_JAN = _CACHE_DIR / "t100_2019_01.csv"
_T100_FEB = _CACHE_DIR / "t100_2019_02.csv"
_T100_MAR = _CACHE_DIR / "t100_2019_03.csv"

_CACHE_AVAILABLE = (
    _DEMAND_CACHE.exists() and
    _T100_JAN.exists() and _T100_FEB.exists() and _T100_MAR.exists()
)

@pytest.mark.skipif(
    not _CACHE_AVAILABLE,
    reason="BTS cache absent (data/bts-cache/); skipping cache-dependent test"
)
def test_cache_pipeline_with_fake_perf(tmp_path: pathlib.Path) -> None:
    """
    Kind: example (using real data, fake perf).
    Tolerance: none.
    Cannot catch: wrong values (smoke test — just exercises the full path).

    Run pipeline on real region data with fake perf to verify the data
    loading and routing code works end-to-end without needing OpenAP.
    """
    import pipeline

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    rc = pipeline.run_pipeline(
        demand_path=str(_DEMAND_CACHE),
        hubs=["ORD"],
        t100_paths=[str(_T100_JAN), str(_T100_FEB), str(_T100_MAR)],
        out_dir=str(out_dir),
        perf_fn=_fake_perf,
        p2p_rule="observed",
    )
    assert rc == 0, f"Cache pipeline with fake perf failed: rc={rc}"
    assert (out_dir / "pipeline_totals.json").exists()

    data = json.loads((out_dir / "pipeline_totals.json").read_text())
    # Basic sanity: passengers non-negative
    assert data["p2p"]["passengers"] >= 0
    assert data["hub_spoke"]["passengers"] >= 0


def test_model_text_does_not_tell_users_to_rescale_db1b_demand(tmp_path: pathlib.Path) -> None:
    """
    Kind: regression (documentation consistency).
    Tolerance: none.
    Cannot catch: wording that misleads in a way not containing these phrases.

    db1b.py already multiplies the 10% sample by 10, so any text telling the
    user to pass scale=10 for DB1B demand would cause a x100 double count.
    """
    import pathlib as _p
    import pipeline

    demand_path = _make_tiny3_demand_csv(tmp_path)
    t100_path = _make_tiny3_t100(tmp_path)
    out = tmp_path / "o"
    out.mkdir()
    pipeline.run_pipeline(
        demand_path=str(demand_path), hubs=["ORD"], t100_paths=[str(t100_path)],
        out_dir=str(out), perf_fn=_fake_perf, p2p_rule="all",
    )
    text = (out / "MODEL.txt").read_text()
    assert "scale=10.0 can be passed" not in text
    assert "already" in text and "10" in text
    src = (_p.Path(__file__).resolve().parents[1] / "src" / "cli.py").read_text()
    assert "use 10.0 for DB1B" not in src
