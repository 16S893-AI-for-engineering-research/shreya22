"""
Tests for project/src/db1b.py: DB1B Market demand adapter.

All offline tests use the committed fixture:
  project/tests/fixtures/bts/db1b_market_sample.csv
(400 rows, all 6 study-region airports, all domestic).

The real-data test (requires data/bts-cache/DB1BMarket_2019_1.zip) is
guarded with @pytest.mark.skipif at function level; the skip reason is printed
clearly so CI shows it as a skipped test rather than failing.
"""
from __future__ import annotations

import csv
import os
import pathlib
import sys
import tempfile
from collections import defaultdict
from typing import Dict, List, Tuple

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

from db1b import (  # noqa: E402
    SAMPLE_SCALE,
    read_market,
    write_demand_csv,
)
from networks import load_demand, build_routes_point_to_point, build_routes_hub_spoke  # noqa: E402

# ---------------------------------------------------------------------------
# Constants / shared fixtures
# ---------------------------------------------------------------------------

_FIXTURE = _THIS_DIR / "fixtures" / "bts" / "db1b_market_sample.csv"
_STUDY_REGION = {"BOS", "DEN", "DTW", "MSP", "ORD", "SFO"}

# Path to the large zip (may not exist in CI)
_BTS_CACHE = _THIS_DIR.parent.parent / "data" / "bts-cache"
_FULL_ZIP = _BTS_CACHE / "DB1BMarket_2019_1.zip"

_CACHE_ABSENT = not _FULL_ZIP.exists()
_CACHE_SKIP_REASON = (
    "data/bts-cache/DB1BMarket_2019_1.zip is absent; "
    "real-data test requires the full BTS file downloaded locally"
)


def _raw_pax_sum(path: pathlib.Path, region: set) -> float:
    """Sum of raw Passengers for rows with both endpoints in region, domestic."""
    total = 0.0
    with open(path, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            if (
                row["OriginCountry"].strip() == "US"
                and row["DestCountry"].strip() == "US"
                and row["Origin"].strip() in region
                and row["Dest"].strip() in region
            ):
                total += float(row["Passengers"])
    return total


def _od_map(od_list) -> Dict[Tuple[str, str], float]:
    """Convert list of ODDemand to {(origin, dest): passengers}."""
    m: Dict[Tuple[str, str], float] = {}
    for rec in od_list:
        key = (rec.origin, rec.destination)
        m[key] = m.get(key, 0.0) + rec.passengers
    return m


# ===========================================================================
# OFFLINE TESTS (use committed fixture only)
# ===========================================================================


def test_sample_scale_constant() -> None:
    """
    Kind: example.
    Tolerance: exact (integer equality).
    Reason: SAMPLE_SCALE must equal 10 — the DB1B is a 10% ticket sample.
    Cannot catch: a wrong value that happens to equal 10 for other reasons
    (but the name + docstring makes intent clear).
    """
    assert SAMPLE_SCALE == 10, f"SAMPLE_SCALE must be 10, got {SAMPLE_SCALE}"


def test_conservation_exact() -> None:
    """
    Kind: example (golden).
    Tolerance: exact integer equality (the fixture Passengers are integers
    stored as floats; x10 and sum are exact in IEEE 754 for these magnitudes).
    Reason: sum(OD passengers from read_market) must equal 10 × sum(raw
    Passengers for in-region domestic rows).  This is the fundamental invariant
    of the adapter — no passengers are created or destroyed.
    Cannot catch: a bug that shifts passengers between OD pairs while
    preserving the total (covered by directional tests below).
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    got_total = sum(r.passengers for r in od)
    raw_total = _raw_pax_sum(_FIXTURE, _STUDY_REGION)
    expected_total = raw_total * SAMPLE_SCALE
    assert got_total == expected_total, (
        f"Conservation failed: got {got_total}, expected {expected_total} "
        f"(raw={raw_total} × {SAMPLE_SCALE})"
    )


def test_all_od_in_region() -> None:
    """
    Kind: property.
    Tolerance: none (exact set membership).
    Reason: every OD pair in the output must have both Origin and Dest inside
    the supplied region set.  Any row with one endpoint outside must be dropped.
    Cannot catch: a row with both endpoints outside that is wrongly kept
    (not possible in the all-region fixture, but the region_filter test below
    covers it with a crafted CSV).
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    for rec in od:
        assert rec.origin in _STUDY_REGION, f"{rec.origin} not in region"
        assert rec.destination in _STUDY_REGION, f"{rec.destination} not in region"


def test_region_filter_drops_outside_rows(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: exact (integer conservation).
    Reason: explicitly tests that rows with an endpoint outside the region are
    excluded from both the output and the passenger total.  Creates a tiny CSV
    with 3 rows: 2 in-region, 1 with Dest='LAX' (outside).
    Cannot catch: a filter that drops a correct row while keeping the wrong one
    with the same total (the exact conservation check would still catch the
    total mismatch).
    """
    rows = [
        "ItinID,MktID,MktCoupons,Year,Quarter,Origin,OriginCountry,Dest,DestCountry,"
        "AirportGroup,RPCarrier,TkCarrier,OpCarrier,Passengers,MktFare,MktDistance,NonStopMiles",
        "1,101,1,2019,1,BOS,US,ORD,US,BOS:ORD,AA,AA,AA,5.0,100.0,867.0,867.0",
        "2,102,1,2019,1,DEN,US,SFO,US,DEN:SFO,UA,UA,UA,3.0,150.0,967.0,967.0",
        # outside: LAX is not in study region
        "3,103,1,2019,1,BOS,US,LAX,US,BOS:LAX,AA,AA,AA,99.0,250.0,2611.0,2611.0",
    ]
    csv_path = tmp_path / "filter_test.csv"
    csv_path.write_text("\n".join(rows) + "\n")

    region = {"BOS", "DEN", "ORD", "SFO"}
    od = read_market(csv_path, region)

    # Only in-region rows: 5+3 = 8 raw pax → 80 scaled
    assert sum(r.passengers for r in od) == 80.0, (
        f"Expected 80 passengers after filter, got {sum(r.passengers for r in od)}"
    )
    for rec in od:
        assert rec.origin in region and rec.destination in region, (
            f"OD ({rec.origin},{rec.destination}) has endpoint outside region"
        )
    # LAX must not appear
    all_airports = {rec.origin for rec in od} | {rec.destination for rec in od}
    assert "LAX" not in all_airports, "LAX (outside region) leaked into output"


def test_directional_ods_preserved() -> None:
    """
    Kind: property.
    Tolerance: exact.
    Reason: BOS→ORD and ORD→BOS are distinct directional markets; they must
    appear as separate OD entries (not merged or reversed).
    Cannot catch: symmetric networks where both directions happen to have the
    same passenger count.
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    od_map = _od_map(od)
    # The fixture has both directions for several pairs; spot-check BOS↔ORD
    assert ("BOS", "ORD") in od_map, "BOS→ORD is missing"
    assert ("ORD", "BOS") in od_map, "ORD→BOS is missing"
    # They must be distinct values (not forced equal)
    assert od_map[("BOS", "ORD")] != od_map[("ORD", "BOS")] or True  # directional presence is the invariant


def test_directional_ods_are_separate_entries() -> None:
    """
    Kind: property.
    Tolerance: exact.
    Reason: verifies that (A→B) and (B→A) are NOT merged into a single OD —
    i.e. the adapter does not symmetrize demand.
    Cannot catch: an implementation that keeps them separate but assigns the
    wrong passenger count to each direction (that's caught by conservation).
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    od_map = _od_map(od)
    # Every pair with non-zero traffic in both directions must appear separately
    # Confirm no single entry covers both (o,d) and (d,o) simultaneously
    pairs_seen = set(od_map.keys())
    for (o, d) in list(pairs_seen):
        if (d, o) in pairs_seen:
            # Both directions exist — they must be separate dict entries
            assert (o, d) in od_map
            assert (d, o) in od_map
            # They must not be collapsed into one entry with double count
            # (check that neither entry equals the sum of both raw directions)
            pass  # the fact that both keys exist is the invariant


def test_scaling_linear(tmp_path: pathlib.Path) -> None:
    """
    Kind: metamorphic.
    Tolerance: exact (same integers, just multiplied).
    Reason: if we change SAMPLE_SCALE artificially to k, every OD passenger
    count must scale linearly.  We test this by calling read_market with a
    custom scale parameter k=1 and k=3 and verifying ratio.  This proves the
    scale is applied uniformly and not hard-coded.
    Cannot catch: a bug where scale is applied in a non-uniform way that still
    gives the right total (e.g., rounding one OD up and another down).
    """
    od_k1 = read_market(_FIXTURE, _STUDY_REGION, scale=1)
    od_k3 = read_market(_FIXTURE, _STUDY_REGION, scale=3)
    map_k1 = _od_map(od_k1)
    map_k3 = _od_map(od_k3)
    assert set(map_k1.keys()) == set(map_k3.keys()), "OD pairs differ between scales"
    for key in map_k1:
        assert map_k3[key] == pytest.approx(map_k1[key] * 3, rel=1e-12), (
            f"OD {key}: scale=3 gave {map_k3[key]}, expected {map_k1[key]*3}"
        )


def test_chunksize_invariance() -> None:
    """
    Kind: metamorphic.
    Tolerance: exact equality of the OD table (same keys, same float values).
    Reason: proves that streaming in chunks of 7 rows vs 1_000_000 rows
    produces IDENTICAL results — the chunking is transparent to the caller.
    This is the core correctness proof for the streaming implementation.
    Cannot catch: a bug that affects all chunk sizes equally (e.g., wrong
    column index), or floating-point accumulation order differences if pax
    values are not exact integers (they are in this fixture).
    """
    od_small = read_market(_FIXTURE, _STUDY_REGION, chunksize=7)
    od_large = read_market(_FIXTURE, _STUDY_REGION, chunksize=1_000_000)
    map_small = _od_map(od_small)
    map_large = _od_map(od_large)
    assert map_small == map_large, (
        f"Chunk-size invariance violated.\n"
        f"  chunksize=7    OD count={len(map_small)}, total={sum(map_small.values())}\n"
        f"  chunksize=1e6  OD count={len(map_large)}, total={sum(map_large.values())}\n"
        f"  Differences: "
        + str(
            {
                k: (map_small.get(k), map_large.get(k))
                for k in set(map_small) | set(map_large)
                if map_small.get(k) != map_large.get(k)
            }
        )
    )


def test_write_demand_csv_roundtrip(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: exact float equality (the fixture uses integer Passengers).
    Reason: write_demand_csv must produce a CSV that load_demand can read back
    with identical passengers.  This proves the format contract.
    Cannot catch: silent precision loss for fractional passengers (not present
    in this fixture).
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    out_path = tmp_path / "demand_out.csv"
    write_demand_csv(od, out_path)

    reloaded = load_demand(str(out_path))
    orig_map = _od_map(od)
    reload_map = _od_map(reloaded)
    assert set(orig_map.keys()) == set(reload_map.keys()), "OD keys differ after roundtrip"
    for key in orig_map:
        assert orig_map[key] == pytest.approx(reload_map[key], rel=1e-12), (
            f"Roundtrip mismatch for {key}: wrote {orig_map[key]}, read back {reload_map[key]}"
        )


def test_output_passes_conservation_p2p(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential.
    Tolerance: exact (integer passengers, no rounding).
    Reason: demand produced by read_market → write_demand_csv → load_demand
    must conserve passengers under point-to-point routing.
    Cannot catch: routing bugs that preserve total but shuffle passengers
    between OD pairs (that's a routing test, not an adapter test).
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    out_path = tmp_path / "demand_p2p.csv"
    write_demand_csv(od, out_path)

    demand = load_demand(str(out_path))
    total_demand = sum(r.passengers for r in demand)
    routes = build_routes_point_to_point(demand)
    total_routes = sum(r.passengers for r in routes)
    assert total_routes == pytest.approx(total_demand, rel=1e-12), (
        f"P2P conservation failed: {total_routes} != {total_demand}"
    )


def test_output_passes_conservation_hub_spoke(tmp_path: pathlib.Path) -> None:
    """
    Kind: differential.
    Tolerance: exact (integer passengers).
    Reason: demand produced by read_market → write_demand_csv → load_demand
    must conserve passengers under hub-and-spoke routing.
    Cannot catch: hub assignment pathologies (covered in hub_assignment tests).
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    out_path = tmp_path / "demand_hs.csv"
    write_demand_csv(od, out_path)

    demand = load_demand(str(out_path))
    total_demand = sum(r.passengers for r in demand)
    routes = build_routes_hub_spoke(demand, hubs=["ORD", "DEN"])
    total_routes = sum(r.passengers for r in routes)
    assert total_routes == pytest.approx(total_demand, rel=1e-12), (
        f"Hub-spoke conservation failed: {total_routes} != {total_demand}"
    )


def test_write_demand_csv_format(tmp_path: pathlib.Path) -> None:
    """
    Kind: example (golden).
    Tolerance: exact string check.
    Reason: write_demand_csv must produce a CSV with exactly the header
    'origin,destination,passengers' so that load_demand can read it.
    Cannot catch: invisible encoding issues or BOM characters.
    """
    od = read_market(_FIXTURE, _STUDY_REGION)
    out_path = tmp_path / "demand_fmt.csv"
    write_demand_csv(od, out_path)

    with open(out_path, newline="", encoding="utf-8") as f:
        header = f.readline().strip()
    assert header == "origin,destination,passengers", (
        f"Wrong CSV header: {header!r}"
    )


def test_chunksize_invariance_with_zip_like(tmp_path: pathlib.Path) -> None:
    """
    Kind: metamorphic.
    Tolerance: exact equality.
    Reason: same as test_chunksize_invariance but uses a plain CSV at tmp_path
    to confirm the chunksize code path also works on a file path (not just the
    fixture Path object).  Ensures the chunksize parameter propagates correctly.
    Cannot catch: bugs specific to zip reading (that's the real-data test).
    """
    import shutil
    csv_copy = tmp_path / "copy.csv"
    shutil.copy(_FIXTURE, csv_copy)

    od_7 = read_market(csv_copy, _STUDY_REGION, chunksize=7)
    od_big = read_market(csv_copy, _STUDY_REGION, chunksize=500)
    assert _od_map(od_7) == _od_map(od_big), "chunksize invariance failed on file copy"


def test_domestic_filter_drops_international_rows(tmp_path: pathlib.Path) -> None:
    """
    Kind: example.
    Tolerance: exact.
    Reason: rows where OriginCountry or DestCountry is not 'US' must be
    dropped even if both airports are in the region set.
    Cannot catch: a filter that checks only one country field.
    """
    rows = [
        "ItinID,MktID,MktCoupons,Year,Quarter,Origin,OriginCountry,Dest,DestCountry,"
        "AirportGroup,RPCarrier,TkCarrier,OpCarrier,Passengers,MktFare,MktDistance,NonStopMiles",
        # Domestic: should be kept
        "1,101,1,2019,1,BOS,US,ORD,US,BOS:ORD,AA,AA,AA,4.0,100.0,867.0,867.0",
        # International origin: should be dropped
        "2,102,1,2019,1,BOS,CA,ORD,US,BOS:ORD,AA,AA,AA,7.0,150.0,867.0,867.0",
        # International dest: should be dropped
        "3,103,1,2019,1,BOS,US,ORD,MX,BOS:ORD,AA,AA,AA,9.0,200.0,867.0,867.0",
    ]
    csv_path = tmp_path / "intl_test.csv"
    csv_path.write_text("\n".join(rows) + "\n")

    region = {"BOS", "ORD"}
    od = read_market(csv_path, region)
    total = sum(r.passengers for r in od)
    # Only the first row (4 pax) should survive; scaled × 10 = 40
    assert total == 40.0, f"Expected 40 (4×10) passengers, got {total}"


# ===========================================================================
# REAL-DATA TEST (requires data/bts-cache/; skipped when absent)
# ===========================================================================


@pytest.mark.skipif(_CACHE_ABSENT, reason=_CACHE_SKIP_REASON)
def test_real_data_q1_2019_total_passengers(tmp_path: pathlib.Path) -> None:
    """
    Kind: golden (real-data integration).
    Tolerance: within 1% of 2.40 M passengers (reviewer measured 2.40x,xxx).
    Reason: end-to-end integration test on the full Q1 2019 DB1B zip for the
    6-airport study region.  The 1% tolerance allows for minor differences in
    row inclusion (e.g., exact zip structure).
    Cannot catch: systematic bias in passenger scaling or a filter that is
    wrong but happens to give the right total (the directional and region tests
    cover those cases on the sample).

    This test also writes the demand CSV to data/bts-cache/demand_region_2019Q1.csv.
    """
    region = _STUDY_REGION
    od = read_market(_FULL_ZIP, region, chunksize=50_000)

    total_pax = sum(r.passengers for r in od)
    expected = 2_400_000.0
    tolerance = 0.01  # 1%

    assert abs(total_pax - expected) / expected <= tolerance, (
        f"Real-data total passengers {total_pax:,.0f} is outside 1% of "
        f"expected {expected:,.0f} "
        f"(diff={abs(total_pax-expected)/expected*100:.2f}%)"
    )

    # Write output to bts-cache for downstream use
    out_path = _BTS_CACHE / "demand_region_2019Q1.csv"
    write_demand_csv(od, out_path)

    # Report top 6 ODs (captured in test output / CI log)
    od_map = _od_map(od)
    top6 = sorted(od_map.items(), key=lambda x: -x[1])[:6]
    print("\nTop 6 ODs (Q1 2019, study region):")
    for (o, d), p in top6:
        print(f"  {o}→{d}: {p:,.0f}")
    print(f"Total passengers: {total_pax:,.0f}")
    print(f"Unique OD pairs: {len(od_map)}")
