"""
Tests for fleet.py — T-100 fleet adapter (NW-16).

Covers: load_t100, aircraft_mix, BTS_TO_OPENAP, coverage_report.
Fixtures used (offline, committed to repo):
  tests/fixtures/bts/t100_segment_sample.csv  (300 rows, AIRCRAFT_CONFIG=1)
  tests/fixtures/bts/L_AIRCRAFT_TYPE_sample.csv (aircraft type lookup)
Cache-dependent tests are marked with @pytest.mark.skipif at function level
(module-level allow_module_level skips are prohibited by test_no_silent_skips.py).
"""
from __future__ import annotations

import math
import os
import pathlib
import csv

import pytest

# ── plain imports; collection ERROR if fleet.py is missing ───────────────────
from fleet import (
    load_t100,
    aircraft_mix,
    coverage_report,
    BTS_TO_OPENAP,
)

# ── helpers ──────────────────────────────────────────────────────────────────
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "bts"
SAMPLE_T100 = FIXTURES / "t100_segment_sample.csv"
SAMPLE_LOOKUP = FIXTURES / "L_AIRCRAFT_TYPE_sample.csv"

CACHE_DIR = pathlib.Path(__file__).parent.parent.parent / "data" / "bts-cache"
CACHE_PATHS = [
    CACHE_DIR / "t100_2019_01.csv",
    CACHE_DIR / "t100_2019_02.csv",
    CACHE_DIR / "t100_2019_03.csv",
]
_cache_absent = not all(p.exists() for p in CACHE_PATHS)
_CACHE_REASON = (
    "Full BTS T-100 cache files not present (data/bts-cache/ is git-ignored). "
    "Run the data-fetch script to populate data/bts-cache/ before running "
    "this test."
)

REGION = {"BOS", "DEN", "DTW", "MSP", "ORD", "SFO"}

# frozen list of valid OpenAP codes (sourced by running
# openap.prop.available_aircraft() with openap 1.5.x; NW-17 adds openap as a
# dependency — until then we use this snapshot so the test is self-contained).
KNOWN_OPENAP = frozenset({
    "a19n", "a20n", "a21n", "a318", "a319", "a320", "a321",
    "a332", "a333", "a343", "a359", "a388",
    "b37m", "b38m", "b39m", "b3xm",
    "b734", "b737", "b738", "b739",
    "b744", "b748", "b752", "b763",
    "b772", "b773", "b77w", "b788", "b789",
    "c550", "crj9", "e145", "e170", "e190", "e195", "e75l", "glf6",
})


# ═══════════════════════════════════════════════════════════════════════════
# 1. load_t100 — basic loading
# ═══════════════════════════════════════════════════════════════════════════

class TestLoadT100:
    """Tests for the load_t100() loader."""

    def test_returns_dataframe_with_required_columns(self) -> None:
        """
        Kind: example.
        Tolerance: exact (column membership).
        Reason: downstream functions depend on specific column names.
        Cannot catch: wrong dtype for a correctly-named column.
        """
        df = load_t100([str(SAMPLE_T100)])
        required = {
            "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS",
            "ORIGIN", "DEST", "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG",
        }
        assert required.issubset(df.columns), (
            f"Missing columns: {required - set(df.columns)}"
        )

    def test_config_filter_removes_freighter_rows(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example.
        Tolerance: exact count.
        Reason: only AIRCRAFT_CONFIG==1 (passenger) rows should be loaded.
        Cannot catch: a row that is passenger but has config=2 due to data error.
        """
        # build a tiny CSV with one passenger row and one freighter row
        f = tmp_path / "mini.csv"
        f.write_text(
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
            "5.0,750.0,600.0,967.0,500.0,UA,SFO,DEN,614,1,2019,1\n"   # passenger
            "3.0,0.0,0.0,967.0,420.0,UA,SFO,DEN,614,4,2019,1\n"       # freighter (config=4)
        )
        df = load_t100([str(f)])
        assert len(df) == 1, f"Expected 1 row after config filter, got {len(df)}"
        assert float(df.iloc[0]["AIRCRAFT_CONFIG"]) == 1.0

    def test_zero_departures_filter(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example.
        Tolerance: exact count.
        Reason: rows with DEPARTURES_PERFORMED==0 carry no operational data;
                including them would skew shares and averages.
        Cannot catch: a row with DEPARTURES_PERFORMED=0.001 due to rounding.
        """
        f = tmp_path / "mini.csv"
        f.write_text(
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
            "5.0,750.0,600.0,967.0,500.0,UA,SFO,DEN,614,1,2019,1\n"
            "0.0,0.0,0.0,967.0,0.0,UA,SFO,DEN,614,1,2019,1\n"
        )
        df = load_t100([str(f)])
        assert len(df) == 1, f"Expected 1 row after zero-departures filter, got {len(df)}"

    def test_concatenates_multiple_files(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example.
        Tolerance: exact count.
        Reason: function must concatenate all three monthly files for Q1.
        Cannot catch: row duplication instead of concatenation.
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        row = "10.0,1500.0,1200.0,967.0,500.0,UA,SFO,DEN,614,1,2019,1\n"
        f1 = tmp_path / "jan.csv"
        f2 = tmp_path / "feb.csv"
        f1.write_text(header + row)
        f2.write_text(header + row + row)
        df = load_t100([str(f1), str(f2)])
        assert len(df) == 3, f"Expected 3 rows from two files, got {len(df)}"

    def test_sample_fixture_loads_all_passenger_rows(self) -> None:
        """
        Kind: golden.
        Tolerance: exact count (300 rows; all are AIRCRAFT_CONFIG=1 per README).
        Reason: guards against accidental filter regression on the committed
                fixture that offline CI uses for all downstream tests.
        Cannot catch: row corruption that preserves count.
        """
        df = load_t100([str(SAMPLE_T100)])
        assert len(df) == 300, f"Expected 300 rows from sample fixture, got {len(df)}"


# ═══════════════════════════════════════════════════════════════════════════
# 2. aircraft_mix — per-segment shares
# ═══════════════════════════════════════════════════════════════════════════

class TestAircraftMix:
    """Tests for aircraft_mix()."""

    @pytest.fixture(scope="class")
    def sample_df(self):
        return load_t100([str(SAMPLE_T100)])

    def test_shares_sum_to_one_per_segment(self, sample_df) -> None:
        """
        Kind: property.
        Tolerance: abs ≤ 1e-12 (float sum of exact rational fractions from the
                   same denominator; should be machine-zero, not merely close).
        Reason: departure-weighted shares are constructed as part/total where
                total = sum(parts), so any float error is rounding noise only.
        Cannot catch: incorrect numerators that still sum to denominator.
        """
        mix = aircraft_mix(sample_df, REGION)
        for seg, data in mix.items():
            shares = list(data["shares"].values())
            total = math.fsum(shares)
            assert abs(total - 1.0) <= 1e-12, (
                f"Segment {seg}: shares sum to {total!r}, expected 1.0"
            )

    def test_departure_weighting_hand_built(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (hand-computed).
        Tolerance: abs ≤ 1e-12.
        Reason: verifies the weighting rule — a 3:1 departure split must yield
                shares 0.75 and 0.25, not an unweighted 0.5/0.5.
        Cannot catch: weighting with seat-count or passenger-count instead of
                      departures (different if seat sizes differ).
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        # SFO->DEN: type 614 × 3 departures, type 888 × 1 departure
        f = tmp_path / "weighted.csv"
        f.write_text(
            header
            + "3.0,450.0,360.0,967.0,400.0,UA,SFO,DEN,614,1,2019,1\n"
            + "1.0,179.0,150.0,967.0,400.0,UA,SFO,DEN,888,1,2019,1\n"
        )
        df = load_t100([str(f)])
        mix = aircraft_mix(df, {"SFO", "DEN"})
        seg = ("SFO", "DEN")
        assert seg in mix, "Segment SFO->DEN missing from mix"
        shares = mix[seg]["shares"]
        assert abs(shares["614"] - 0.75) <= 1e-12, f"614 share={shares['614']}, expected 0.75"
        assert abs(shares["888"] - 0.25) <= 1e-12, f"888 share={shares['888']}, expected 0.25"

    def test_directional_sfo_den_vs_den_sfo(self, sample_df) -> None:
        """
        Kind: differential.
        Tolerance: none (expects inequality; the dominant type differs between
                   directions in the real sample data).
        Reason: confirms that (SFO,DEN) and (DEN,SFO) are treated as separate
                directional segments and yield different shares.
        Cannot catch: coincidentally equal bidirectional mixes.

        Real-data note: SFO→DEN top type is 614 (Boeing 737-800) with ~30.4%;
        DEN→SFO top type is 888 (737-900ER) with ~40.1% — so dominant type
        and shares differ.
        """
        mix = aircraft_mix(sample_df, REGION)
        seg_fwd = ("SFO", "DEN")
        seg_rev = ("DEN", "SFO")
        assert seg_fwd in mix, "SFO->DEN missing"
        assert seg_rev in mix, "DEN->SFO missing"
        # dominant aircraft type must differ — that is the whole point of directionality
        dom_fwd = max(mix[seg_fwd]["shares"], key=mix[seg_fwd]["shares"].__getitem__)
        dom_rev = max(mix[seg_rev]["shares"], key=mix[seg_rev]["shares"].__getitem__)
        assert dom_fwd != dom_rev, (
            f"SFO->DEN dominant type ({dom_fwd}) == DEN->SFO dominant type ({dom_rev}); "
            "directional split is not being applied"
        )

    def test_freighter_rows_excluded_end_to_end(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example.
        Tolerance: exact (only one segment in result, with one aircraft type).
        Reason: full pipeline test that config=2 freighter rows do not affect
                shares even if they slip past load_t100.
        Cannot catch: a freighter coded as config=1 in BTS data.
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        f = tmp_path / "mixed.csv"
        f.write_text(
            header
            + "10.0,1500.0,1200.0,967.0,500.0,UA,SFO,DEN,614,1,2019,1\n"  # pax
            + "5.0,0.0,0.0,967.0,420.0,UA,SFO,DEN,614,4,2019,1\n"          # freighter
        )
        df = load_t100([str(f)])
        mix = aircraft_mix(df, {"SFO", "DEN"})
        assert ("SFO", "DEN") in mix
        assert mix[("SFO", "DEN")]["shares"] == {"614": 1.0}, (
            "Freighter departures leaked into share calculation"
        )

    def test_seats_per_departure_computed(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (hand-computed).
        Tolerance: abs ≤ 1e-9 (floating-point division of exact integers).
        Reason: seats_per_departure = SEATS / DEPARTURES per segment
                (summed across all rows for that segment).
        Cannot catch: off-by-one in aggregation.
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        f = tmp_path / "seats.csv"
        # 2 deps × 150 seats + 3 deps × 180 seats = 840 seats / 5 deps = 168.0
        f.write_text(
            header
            + "2.0,300.0,250.0,967.0,400.0,UA,SFO,DEN,614,1,2019,1\n"
            + "3.0,540.0,500.0,967.0,400.0,UA,SFO,DEN,888,1,2019,1\n"
        )
        df = load_t100([str(f)])
        mix = aircraft_mix(df, {"SFO", "DEN"})
        expected_spd = 840.0 / 5.0
        assert abs(mix[("SFO", "DEN")]["seats_per_departure"] - expected_spd) <= 1e-9, (
            f"seats_per_departure={mix[('SFO','DEN')]['seats_per_departure']}, expected {expected_spd}"
        )

    def test_load_factor_computed(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (hand-computed).
        Tolerance: abs ≤ 1e-9.
        Reason: load_factor = PASSENGERS / SEATS per segment.
        Cannot catch: load_factor > 1 from erroneous data.
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        f = tmp_path / "lf.csv"
        # total seats=400, total pax=300 → LF = 0.75
        f.write_text(
            header
            + "2.0,200.0,150.0,967.0,400.0,UA,SFO,DEN,614,1,2019,1\n"
            + "2.0,200.0,150.0,967.0,400.0,UA,SFO,DEN,888,1,2019,1\n"
        )
        df = load_t100([str(f)])
        mix = aircraft_mix(df, {"SFO", "DEN"})
        assert abs(mix[("SFO", "DEN")]["load_factor"] - 0.75) <= 1e-9

    def test_only_region_segments_returned(self, sample_df) -> None:
        """
        Kind: property.
        Tolerance: exact (set containment).
        Reason: aircraft_mix(df, region) must only include segments where both
                origin AND destination are in the region set.
        Cannot catch: a region filtering bug that is compensated elsewhere.
        """
        mix = aircraft_mix(sample_df, REGION)
        for origin, dest in mix:
            assert origin in REGION, f"Non-region origin: {origin}"
            assert dest in REGION, f"Non-region dest: {dest}"

    def test_all_region_segments_present(self, sample_df) -> None:
        """
        Kind: property.
        Tolerance: exact (set equality: every (O,D) pair in the filtered df
                   appears in the mix result).
        Reason: no segment should be silently dropped.
        Cannot catch: aggregation that merges separate carriers into one row.
        """
        import pandas as pd
        df_reg = sample_df[
            sample_df["ORIGIN"].isin(REGION) & sample_df["DEST"].isin(REGION)
        ]
        expected_segs = set(zip(df_reg["ORIGIN"], df_reg["DEST"]))
        mix = aircraft_mix(sample_df, REGION)
        missing = expected_segs - set(mix.keys())
        assert not missing, f"Segments present in data but missing from mix: {missing}"

    def test_shares_keys_are_string_codes(self, sample_df) -> None:
        """
        Kind: property.
        Tolerance: exact (type check).
        Reason: downstream mapping via BTS_TO_OPENAP uses string keys; numeric
                AIRCRAFT_TYPE from pandas would cause silent KeyError misses.
        Cannot catch: string vs int that happens to match by coercion.
        """
        mix = aircraft_mix(sample_df, REGION)
        for seg, data in mix.items():
            for k in data["shares"]:
                assert isinstance(k, str), (
                    f"Segment {seg}: share key {k!r} is {type(k)}, expected str"
                )

    def test_zero_seats_does_not_crash(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (edge case).
        Tolerance: seats_per_departure=0.0 and load_factor=nan or 0.0 (both
                   acceptable; callers must handle NaN load factor).
        Reason: BTS has rows with SEATS=0 for some cancelled/repositioning
                ops; load_factor = PASSENGERS/SEATS would divide by zero.
        Cannot catch: divide-by-zero that is only triggered by specific
                      aggregation ordering.
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        f = tmp_path / "zeroseat.csv"
        f.write_text(
            header
            + "2.0,0.0,0.0,967.0,400.0,UA,SFO,DEN,614,1,2019,1\n"
        )
        df = load_t100([str(f)])
        # should not raise
        mix = aircraft_mix(df, {"SFO", "DEN"})
        assert ("SFO", "DEN") in mix


# ═══════════════════════════════════════════════════════════════════════════
# 3. BTS_TO_OPENAP — mapping table
# ═══════════════════════════════════════════════════════════════════════════

class TestBTSToOpenAPMapping:
    """Tests for the BTS_TO_OPENAP mapping dict."""

    # Required BTS codes to map (region's actual types from Q1 real data,
    # including minor types; the task spec lists the key ones).
    # Codes not in REGION_REQUIRED may be extra, but must still be valid.
    REGION_BTS_CODES = {
        "888",  # Boeing 737-900ER
        "614",  # Boeing 737-800
        "694",  # A320
        "699",  # A321Neoxlr
        "698",  # A319
        "673",  # ERJ-175
        "608",  # Boeing 717-200  (UNMAPPED — no OpenAP model)
        "623",  # 757-300
        "678",  # E190
        "622",  # 757-200
        "612",  # 737-700
        "631",  # CRJ-700
        "656",  # MD-90           (UNMAPPED — no OpenAP model)
        "627",  # 777-200ER
        "677",  # E170
        "722",  # A320neo
        "638",  # CRJ-900
        "675",  # ERJ-145
        "838",  # 737 MAX 8
        "629",  # CRJ-200
        "721",  # A321neo
    }

    def test_all_region_codes_present_in_mapping(self) -> None:
        """
        Kind: property.
        Tolerance: exact (set membership).
        Reason: every BTS aircraft type that appears in the study region must
                have an explicit entry — either a valid OpenAP code, a proxy, or
                the sentinel None (UNMAPPED). Silent omission means coverage_report
                underestimates unmapped share.
        Cannot catch: an entry mapped to wrong OpenAP code with correct key.
        """
        missing = self.REGION_BTS_CODES - set(BTS_TO_OPENAP.keys())
        assert not missing, (
            f"BTS codes present in study region but absent from BTS_TO_OPENAP: {missing}"
        )

    def test_mapped_targets_are_valid_openap_codes(self) -> None:
        """
        Kind: property (checked against frozen list; NW-17 will recheck live).
        Tolerance: exact (set membership against KNOWN_OPENAP snapshot).
        Reason: using a non-existent OpenAP model code would silently fail in
                NW-17 when OpenAP is called.
        Cannot catch: a code that is in KNOWN_OPENAP but wrong for the aircraft.
        """
        for bts_code, openap_code in BTS_TO_OPENAP.items():
            if openap_code is None:
                continue  # UNMAPPED — intentional
            assert openap_code in KNOWN_OPENAP, (
                f"BTS {bts_code} maps to '{openap_code}' which is not in the "
                f"known OpenAP aircraft list (snapshot from openap 1.5.x)"
            )

    def test_unmapped_codes_are_none_not_empty_string(self) -> None:
        """
        Kind: property.
        Tolerance: exact (type check).
        Reason: None is the canonical sentinel for "no OpenAP model"; empty
                string '' would cause a silent miss in NW-17 name lookups.
        Cannot catch: a code mapped to a string like "NONE" or "N/A".
        """
        for bts_code, openap_code in BTS_TO_OPENAP.items():
            assert openap_code is None or isinstance(openap_code, str), (
                f"BTS {bts_code}: mapping value must be str or None, got {type(openap_code)}"
            )
            assert openap_code != "", (
                f"BTS {bts_code}: empty string is not allowed; use None for UNMAPPED"
            )

    def test_717_is_unmapped(self) -> None:
        """
        Kind: example (regression).
        Tolerance: exact.
        Reason: Boeing 717-200 has no OpenAP model (confirmed against
                openap.prop.available_aircraft()). This test locks in the
                explicit decision so it cannot be silently "fixed" with a wrong
                proxy.
        Cannot catch: a future OpenAP version that adds b717.
        """
        assert "608" in BTS_TO_OPENAP, "BTS 608 (717-200) must have an entry"
        assert BTS_TO_OPENAP["608"] is None, (
            f"BTS 608 (717-200) must be UNMAPPED (None), got {BTS_TO_OPENAP['608']!r}"
        )

    def test_md90_is_unmapped(self) -> None:
        """
        Kind: example (regression).
        Tolerance: exact.
        Reason: MD-90 has no OpenAP model. Locks in explicit decision.
        Cannot catch: future OpenAP addition.
        """
        assert "656" in BTS_TO_OPENAP, "BTS 656 (MD-90) must have an entry"
        assert BTS_TO_OPENAP["656"] is None, (
            f"BTS 656 (MD-90) must be UNMAPPED (None), got {BTS_TO_OPENAP['656']!r}"
        )

    def test_737_900er_maps_to_b739(self) -> None:
        """
        Kind: example (exact mapping).
        Tolerance: exact string equality.
        Reason: 888 (737-900ER) is the top type in the region (20.4% of
                departures). Exact match to b739 is confirmed in OpenAP docs.
        Cannot catch: b739 model parameter inaccuracies.
        """
        assert BTS_TO_OPENAP.get("888") == "b739", (
            f"BTS 888 (737-900ER) should map to 'b739', got {BTS_TO_OPENAP.get('888')!r}"
        )

    def test_757_300_maps_to_b752_proxy(self) -> None:
        """
        Kind: example (proxy mapping).
        Tolerance: exact string equality to 'b752'.
        Reason: BTS 623 = 757-300; OpenAP has no b753, so b752 (757-200) is
                used as the nearest proxy. 757-300 is ~7.1 m longer with ~20%
                more seats but similar engines and aerodynamics. This is a
                known over-estimation of range/under-estimation of seat-count
                effect; the error is documented and bounded.
        Cannot catch: the magnitude of proxy error vs a dedicated b753 model.
        """
        assert BTS_TO_OPENAP.get("623") == "b752", (
            f"BTS 623 (757-300) should proxy to 'b752', got {BTS_TO_OPENAP.get('623')!r}"
        )

    def test_a321neoxlr_maps_to_a321(self) -> None:
        """
        Kind: example (proxy mapping with justification locked in).
        Tolerance: exact string equality.
        Reason: BTS 699 = 'A321Neoxlr'; OpenAP has 'a321' and 'a21n' (A321neo)
                but not the XLR variant. 'a321' is chosen over 'a21n' because
                the ceo/neo performance gap (~15% fuel burn) is documented and
                bounded, whereas the XLR vs neo gap is a smaller airframe change
                (~same MTOW, same engines). This test locks in the a321 choice;
                see the MAPPING_NOTES dict in fleet.py for the full rationale.
        Cannot catch: the sign or magnitude of the fuel-burn proxy error.
        """
        assert BTS_TO_OPENAP.get("699") == "a321", (
            f"BTS 699 (A321Neoxlr) should map to 'a321', got {BTS_TO_OPENAP.get('699')!r}"
        )

    def test_minimum_coverage_of_region_departures(self) -> None:
        """
        Kind: metamorphic / threshold.
        Tolerance: exactly-mapped + proxy-mapped >= 85% of region departures
                   in the committed sample fixture.
        Reason: the top few types dominate the region; the 717 (4.3%) and
                MD-90 (3.1%) are the only significant unmapped types. Combined
                ~92.6% coverage should be achieved with the provided mapping.
        Cannot catch: a mapping that hits the threshold via wrong proxy targets.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        mapped_frac = report["exactly_mapped"] + report["proxy_mapped"]
        assert mapped_frac >= 0.85, (
            f"Mapped fraction = {mapped_frac:.3f} < 0.85; "
            f"report={report}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 4. coverage_report — shares, completeness
# ═══════════════════════════════════════════════════════════════════════════

class TestCoverageReport:
    """Tests for coverage_report()."""

    def test_coverage_shares_sum_to_one(self) -> None:
        """
        Kind: property.
        Tolerance: abs ≤ 1e-12.
        Reason: the three buckets (exactly_mapped, proxy_mapped, unmapped) must
                partition all departures; any gap indicates a coding error.
        Cannot catch: the three buckets all being 0 + 0 + 0.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        total = report["exactly_mapped"] + report["proxy_mapped"] + report["unmapped"]
        assert abs(total - 1.0) <= 1e-12, (
            f"Coverage buckets sum to {total!r}, expected 1.0; report={report}"
        )

    def test_every_code_in_exactly_one_bucket(self) -> None:
        """
        Kind: property.
        Tolerance: exact (no code appears in more than one bucket).
        Reason: a code in both "exactly_mapped" and "proxy_mapped" buckets
                would double-count its departures.
        Cannot catch: a code silently absent from all three buckets.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        # verify the per_code breakdown is mutually exclusive
        assert set(report["exactly_mapped_codes"]).isdisjoint(
            set(report["proxy_mapped_codes"])
        ), "A code appears in both exactly_mapped and proxy_mapped"
        assert set(report["exactly_mapped_codes"]).isdisjoint(
            set(report["unmapped_codes"])
        ), "A code appears in both exactly_mapped and unmapped"
        assert set(report["proxy_mapped_codes"]).isdisjoint(
            set(report["unmapped_codes"])
        ), "A code appears in both proxy_mapped and unmapped"

    def test_unmapped_codes_include_717_and_md90(self) -> None:
        """
        Kind: example (regression guard).
        Tolerance: exact set membership.
        Reason: 717-200 (608) and MD-90 (656) are the two known unmapped
                aircraft in the region; they must appear in the unmapped bucket.
        Cannot catch: a future OpenAP update that adds these models.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        assert "608" in report["unmapped_codes"], (
            "BTS 608 (717-200) should be in unmapped_codes"
        )
        assert "656" in report["unmapped_codes"], (
            "BTS 656 (MD-90) should be in unmapped_codes"
        )

    def test_hand_built_coverage(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (hand-computed).
        Tolerance: abs ≤ 1e-12.
        Reason: full end-to-end test with known departure counts:
                10 deps of 888 (exact→b739), 5 deps of 623 (proxy→b752),
                5 deps of 608 (unmapped, None).
                Expected: exactly_mapped=0.5, proxy_mapped=0.25, unmapped=0.25.
        Cannot catch: implementation that achieves correct fractions via wrong
                      per-type allocations.
        """
        header = (
            "DEPARTURES_PERFORMED,SEATS,PASSENGERS,DISTANCE,AIR_TIME,"
            "UNIQUE_CARRIER,ORIGIN,DEST,AIRCRAFT_TYPE,AIRCRAFT_CONFIG,YEAR,MONTH\n"
        )
        f = tmp_path / "coverage.csv"
        f.write_text(
            header
            + "10.0,1790.0,1500.0,967.0,400.0,UA,SFO,DEN,888,1,2019,1\n"  # exact
            + "5.0,925.0,700.0,967.0,400.0,UA,SFO,DEN,623,1,2019,1\n"   # proxy
            + "5.0,875.0,650.0,967.0,400.0,DL,SFO,DEN,608,1,2019,1\n"   # unmapped
        )
        df = load_t100([str(f)])
        mix = aircraft_mix(df, {"SFO", "DEN"})
        report = coverage_report(mix)
        assert abs(report["exactly_mapped"] - 0.5) <= 1e-12, report
        assert abs(report["proxy_mapped"] - 0.25) <= 1e-12, report
        assert abs(report["unmapped"] - 0.25) <= 1e-12, report

    def test_coverage_report_keys(self) -> None:
        """
        Kind: property.
        Tolerance: exact (key presence check).
        Reason: downstream code and NW-17 depend on a stable dict schema.
        Cannot catch: correct keys with wrong dtypes.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        required_keys = {
            "exactly_mapped", "proxy_mapped", "unmapped",
            "exactly_mapped_codes", "proxy_mapped_codes", "unmapped_codes",
        }
        missing = required_keys - set(report.keys())
        assert not missing, f"coverage_report missing keys: {missing}"


# ═══════════════════════════════════════════════════════════════════════════
# 5. Real-data test (cache-dependent; skipped when bts-cache is absent)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(_cache_absent, reason=_CACHE_REASON)
def test_real_q1_coverage_numbers() -> None:
    """
    Kind: golden (real BTS Q1 2019 data).
    Tolerance: exactly_mapped + proxy_mapped >= 0.90 (target ≥ 90% departure
               coverage after applying the provided BTS_TO_OPENAP table).
    Reason: uses the full un-sampled T-100 data for Q1 2019, filtered to the
            study region {BOS, DEN, DTW, MSP, ORD, SFO}.
    Measurement: 31040 total region departures; top types by code 888/614/694/
                 699/698/673 cover ~68.3% of departures exactly; 623/622/631/
                 627/677/722/638/675/838/629/721 add proxy coverage; 608+656
                 account for ~7.4% that remains unmapped.
    Source: data/bts-cache/t100_2019_0{1,2,3}.csv streamed with chunksize
            50_000, AIRCRAFT_CONFIG==1 and DEPARTURES_PERFORMED>0 filters,
            airport pair filter ORIGIN ∈ REGION and DEST ∈ REGION.
    Cannot catch: data errors in the upstream BTS files.
    """
    paths = [str(p) for p in CACHE_PATHS]
    df = load_t100(paths)
    mix = aircraft_mix(df, REGION)
    report = coverage_report(mix)

    total = report["exactly_mapped"] + report["proxy_mapped"] + report["unmapped"]
    assert abs(total - 1.0) <= 1e-12, f"Buckets don't sum to 1: {total}"

    mapped = report["exactly_mapped"] + report["proxy_mapped"]
    assert mapped >= 0.90, (
        f"Real-data Q1 region coverage = {mapped:.3f} < 0.90; report={report}"
    )

    # Print the actual numbers for the report
    print(f"\nReal Q1 region coverage:")
    print(f"  exactly_mapped : {report['exactly_mapped']:.4f}")
    print(f"  proxy_mapped   : {report['proxy_mapped']:.4f}")
    print(f"  unmapped       : {report['unmapped']:.4f}")
    print(f"  exactly_mapped_codes : {sorted(report['exactly_mapped_codes'])}")
    print(f"  proxy_mapped_codes   : {sorted(report['proxy_mapped_codes'])}")
    print(f"  unmapped_codes       : {sorted(report['unmapped_codes'])}")
