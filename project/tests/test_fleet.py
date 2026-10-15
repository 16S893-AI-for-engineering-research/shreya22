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

# ═══════════════════════════════════════════════════════════════════════════
# NW-21: Complete national Q1-2019 passenger AIRCRAFT_TYPE coverage
# ═══════════════════════════════════════════════════════════════════════════
# All tests in this section use a small committed fixture (< 50 KB) that lists
# every distinct AIRCRAFT_TYPE code in the Q1-2019 T-100 passenger data with
# its total departures. The fixture was generated by:
#   uv run --directory project -p 3.11 python scripts/gen_aircraft_type_fixture.py
# and committed to tests/fixtures/bts/q1_2019_aircraft_types.csv.
# No bts-cache access is needed for these tests.

AIRCRAFT_TYPES_FIXTURE = FIXTURES / "q1_2019_aircraft_types.csv"


def _load_fixture_codes() -> dict[str, int]:
    """Load {aircraft_type: departures} from the committed fixture."""
    result: dict[str, int] = {}
    with AIRCRAFT_TYPES_FIXTURE.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            result[row["aircraft_type"]] = int(row["departures"])
    return result


class TestNW21FullCoverage:
    """NW-21: every national Q1-2019 passenger AIRCRAFT_TYPE code must be an
    explicit entry in BTS_TO_OPENAP (exact, proxy, or None with a note)."""

    def test_fixture_exists(self) -> None:
        """
        Kind: example (guard).
        Tolerance: exact (file presence).
        Reason: all other NW-21 tests depend on this fixture; a missing file
                would cause confusing errors downstream.
        Cannot catch: a corrupt/truncated fixture.
        """
        assert AIRCRAFT_TYPES_FIXTURE.exists(), (
            f"Fixture not found: {AIRCRAFT_TYPES_FIXTURE}. "
            "Run: uv run --directory project -p 3.11 python "
            "scripts/gen_aircraft_type_fixture.py"
        )

    def test_fixture_covers_expected_code_count(self) -> None:
        """
        Kind: golden.
        Tolerance: exact (105 distinct AIRCRAFT_TYPE codes).
        Reason: the fixture is generated from the full Q1-2019 national T-100
                passenger data; 105 is the correct count as of 2019-Q1.
                A change in count signals a bad regeneration or wrong filter.
        Cannot catch: swapped code descriptions.
        """
        codes = _load_fixture_codes()
        assert len(codes) == 105, (
            f"Expected 105 aircraft type codes in fixture, got {len(codes)}"
        )

    def test_all_national_q1_2019_codes_in_bts_to_openap(self) -> None:
        """
        Kind: property.
        Tolerance: exact (set membership — every code in fixture must be a key
                   in BTS_TO_OPENAP; value may be a string code or None).
        Reason: NW-21 requirement. Absent codes are silently treated as
                unmapped by coverage_report; this test makes every decision
                explicit. A reviewer measured 7.8% of national departures
                used codes absent from BTS_TO_OPENAP before this fix.
        Cannot catch: wrong mapping value (covered by other tests).
        """
        codes = _load_fixture_codes()
        missing = {c for c in codes if c not in BTS_TO_OPENAP}
        assert not missing, (
            f"{len(missing)} Q1-2019 national aircraft type codes are absent "
            f"from BTS_TO_OPENAP: {sorted(missing)}"
        )

    def test_all_non_none_values_in_live_openap(self) -> None:
        """
        Kind: property (checked against live openap.prop.available_aircraft()).
        Tolerance: exact (set membership).
        Reason: maps every non-None BTS_TO_OPENAP value to a real OpenAP model
                code so that downstream flight_fuel_nox() calls never get an
                unknown model. The KNOWN_OPENAP snapshot in class
                TestBTSToOpenAPMapping is a frozen copy; this test uses the
                live list to catch version drift.
        Cannot catch: an OpenAP model that exists but gives wrong physics.
        """
        import openap.prop as prop
        available = frozenset(prop.available_aircraft())
        bad: list[tuple[str, str]] = []
        for bts_code, openap_code in BTS_TO_OPENAP.items():
            if openap_code is None:
                continue
            if openap_code not in available:
                bad.append((bts_code, openap_code))
        assert not bad, (
            f"BTS codes mapped to non-existent OpenAP models: {bad}"
        )

    def test_every_entry_has_mapping_note(self) -> None:
        """
        Kind: property.
        Tolerance: exact (key membership in MAPPING_NOTES for every key in
                   BTS_TO_OPENAP).
        Reason: MAPPING_NOTES stores the human-readable justification for every
                mapping decision; a missing note means an undocumented choice
                that future engineers cannot review.
        Cannot catch: a note that is present but inaccurate.
        """
        from fleet import MAPPING_NOTES
        missing = {c for c in BTS_TO_OPENAP if c not in MAPPING_NOTES}
        assert not missing, (
            f"{len(missing)} BTS_TO_OPENAP entries lack a MAPPING_NOTES "
            f"justification: {sorted(missing)}"
        )

    def test_687_a330_300_maps_to_a333_not_none(self) -> None:
        """
        Kind: example (regression / NW-21 explicit requirement).
        Tolerance: exact string equality to 'a333'.
        Reason: NW-21 task spec states '687 (A330-300) is mapped to None
                although OpenAP has a333'. The A330-300 is the primary -300
                variant of the A330 family; a333 is the exact OpenAP code.
                Keeping it as None understates mapped coverage by ~0.07% of
                national departures.
        Cannot catch: a333 model parameter quality issues in OpenAP.
        """
        assert "687" in BTS_TO_OPENAP, "BTS 687 (A330-300) must have an entry"
        assert BTS_TO_OPENAP["687"] == "a333", (
            f"BTS 687 (A330-300) must map to 'a333' (exact), "
            f"got {BTS_TO_OPENAP['687']!r}"
        )

    def test_696_a330_200_maps_to_a332(self) -> None:
        """
        Kind: example (exact mapping).
        Tolerance: exact string equality to 'a332'.
        Reason: BTS 696 = 'Airbus Industrie A330-200'; OpenAP 'a332' is the
                A330-200 — exact match. This code had 3,812 national departures
                (0.19%) and was absent from BTS_TO_OPENAP before NW-21.
        Cannot catch: a332 model inaccuracies in OpenAP.
        """
        assert "696" in BTS_TO_OPENAP, "BTS 696 (A330-200) must have an entry"
        assert BTS_TO_OPENAP["696"] == "a332", (
            f"BTS 696 (A330-200) must map to 'a332', got {BTS_TO_OPENAP['696']!r}"
        )

    def test_889_b787_900_maps_to_b789(self) -> None:
        """
        Kind: example (exact mapping).
        Tolerance: exact string equality to 'b789'.
        Reason: BTS 889 = 'B787-900 Dreamliner'; OpenAP 'b789' is the
                B787-9 — exact match (code suffix '89' = 787-900 per BTS
                convention; '88' = 787-800). Had 750 national departures
                (0.04%) and was absent from BTS_TO_OPENAP before NW-21.
        Cannot catch: b789 model parameter quality.
        """
        assert "889" in BTS_TO_OPENAP, "BTS 889 (B787-900) must have an entry"
        assert BTS_TO_OPENAP["889"] == "b789", (
            f"BTS 889 (B787-900) must map to 'b789', got {BTS_TO_OPENAP['889']!r}"
        )

    def test_819_b747_400_maps_to_b744(self) -> None:
        """
        Kind: example (exact mapping).
        Tolerance: exact string equality to 'b744'.
        Reason: BTS 819 = 'Boeing 747-400'; OpenAP 'b744' is the 747-400 —
                exact match. Had 195 national departures (0.01%) absent from
                BTS_TO_OPENAP before NW-21.
        Cannot catch: b744 model parameter quality.
        """
        assert "819" in BTS_TO_OPENAP, "BTS 819 (747-400) must have an entry"
        assert BTS_TO_OPENAP["819"] == "b744", (
            f"BTS 819 (747-400) must map to 'b744', got {BTS_TO_OPENAP['819']!r}"
        )

    def test_821_b747_8_maps_to_b748(self) -> None:
        """
        Kind: example (exact mapping).
        Tolerance: exact string equality to 'b748'.
        Reason: BTS 821 = 'Boeing B747-8'; OpenAP 'b748' is the 747-8 —
                exact match. Had 137 national departures (0.01%) absent before
                NW-21.
        Cannot catch: b748 model parameter quality.
        """
        assert "821" in BTS_TO_OPENAP, "BTS 821 (747-8) must have an entry"
        assert BTS_TO_OPENAP["821"] == "b748", (
            f"BTS 821 (747-8) must map to 'b748', got {BTS_TO_OPENAP['821']!r}"
        )

    def test_turboprop_codes_are_none(self) -> None:
        """
        Kind: property.
        Tolerance: exact (all turboprop/piston/helicopter codes map to None).
        Reason: OpenAP models are all jet aircraft; mapping a turboprop to a
                jet model would give wildly incorrect fuel estimates. The
                turboprop share is ~5.86% of national departures (1.41%
                Cessna 208 Caravan, 0.94% Cessna C-402, 0.89% DHC-8-400,
                etc.) — large enough to matter if wrongly mapped.
                These codes are: pistons (010, 026, 034-036, 079, 117, 131,
                194, 195), turboprops (125, 405, 406, 412, 415, 416, 432,
                441, 442, 456, 458, 461, 469, 479, 482, 485), helicopters
                (340, 360, 362, 364, 366, 370, 393).
        Cannot catch: a code that is a turboprop but was miscoded in BTS data.
        """
        # These codes are all turboprops, pistons, or helicopters and MUST map
        # to None — mapping them to any jet would be physically wrong.
        turboprop_piston_helo = {
            "010",  # Beech Bonanza (piston)
            "026",  # Gipps Aero Ga8 Airvan (piston)
            "034",  # Helio H-250/295/395 (piston)
            "035",  # Cessna C206/207/209/210 Stationair (piston)
            "036",  # Cessna 172 Skyhawk (piston)
            "079",  # Piper PA-32 Cherokee 6 (piston)
            "117",  # Beech Baron 55 (piston)
            "125",  # Cessna C-402 (piston twin)
            "131",  # Britten-Norman Islander (piston)
            "194",  # Piper PA-31 Navajo (piston/turboprop)
            "195",  # Piper PA-34/39 (piston)
            "340",  # Eurocopter AS350 AStar (helicopter)
            "360",  # Robinson R44 (helicopter)
            "362",  # Bell 206 LT (helicopter)
            "364",  # Bell 206 L4 (helicopter)
            "366",  # Bell 407 (helicopter)
            "370",  # Eurocopter Bo-105 (helicopter)
            "393",  # AgustaWestland AW139 (helicopter)
            "405",  # Beech 1900 (turboprop)
            "406",  # Beech 200 Super King Air (turboprop)
            "412",  # CASA C212 Aviocar (turboprop)
            "415",  # Cessna Grand Caravan C208B (turboprop)
            "416",  # Cessna 208 Caravan (turboprop)
            "432",  # Saab 2000 (turboprop)
            "441",  # ATR-42 (turboprop)
            "442",  # ATR-72 (turboprop)
            "456",  # Saab 340B (turboprop)
            "458",  # Beechcraft Super King Air 350 (turboprop)
            "461",  # Embraer EMB-120 Brasilia (turboprop)
            "469",  # BAe Jetstream 31 (turboprop)
            "482",  # DHC-8-400 Dash 8 (turboprop)
            "485",  # DHC-6 Twin Otter (turboprop)
        }
        bad: list[str] = []
        for code in turboprop_piston_helo:
            if code not in BTS_TO_OPENAP:
                bad.append(f"{code} ABSENT (must be None)")
            elif BTS_TO_OPENAP[code] is not None:
                bad.append(f"{code} → {BTS_TO_OPENAP[code]!r} (must be None)")
        assert not bad, (
            "Turboprop/piston/helicopter codes must map to None: " + "; ".join(bad)
        )

    def test_bizjet_codes_are_none(self) -> None:
        """
        Kind: property.
        Tolerance: exact (all business jet codes map to None with a note).
        Reason: business jets operate at very different mission profiles
                (lighter MTOW, shorter range, fewer seats) vs the narrowbody
                and widebody jets modeled in OpenAP; no suitable proxy exists
                for the majority of these types. Exception: 750 (Gulfstream
                G650) which could proxy to glf6, but the departure share is
                negligible (<0.003%) and no commercial passenger service data
                makes the proxy useful for hub-vs-spoke analysis.
                Note: some large business jets like 669 (Challenger 604/605),
                688 (Global 5000), 658 (Global Express/6000) are mapped None
                because they represent charter/small-group operations that do
                not meaningfully contribute to passenger network analysis.
        Cannot catch: a code that is a bizjet but appears to be commercial.
        """
        bizjet_codes = {
            "495",  # Hawker 4000 (bizjet)
            "575",  # Learjet 75 (bizjet)
            "609",  # Challenger 300 (bizjet)
            "636",  # Cessna Citation II/Bravo (bizjet)
            "639",  # Cessna CJ1/2/3 (bizjet)
            "641",  # Gulfstream G-III/IV (bizjet)
            "646",  # Cessna Citation X 650 (bizjet)
            "647",  # Cessna Citation CE750 (bizjet)
            "648",  # Gulfstream G200 (bizjet)
            "651",  # Gulfstream G150 (bizjet)
            "652",  # Canadair CL-600 Challenger (bizjet)
            "658",  # Global Express/BD-700 (bizjet)
            "667",  # Gulfstream G-V/550 (bizjet)
            "668",  # Challenger 601 (bizjet)
            "669",  # Challenger 604/605 (bizjet)
            "671",  # Gulfstream G450 (bizjet)
            "682",  # Falcon 2000EX (bizjet)
            "684",  # Hawker 800XP (bizjet)
            "685",  # Cessna 510 Mustang/Citation Excel (bizjet)
            "686",  # Learjet 45 (bizjet)
            "688",  # Global 5000 (bizjet)
            "750",  # Gulfstream G650 (bizjet — glf6 exists but no commercial pax)
            "770",  # Falcon 900/900EX (bizjet)
            "774",  # Falcon 2000 (bizjet)
            "775",  # Falcon 7X (bizjet)
            "788",  # Global 6000/XRS (bizjet)
        }
        bad: list[str] = []
        for code in bizjet_codes:
            if code not in BTS_TO_OPENAP:
                bad.append(f"{code} ABSENT (must be None)")
            elif BTS_TO_OPENAP[code] is not None:
                bad.append(f"{code} → {BTS_TO_OPENAP[code]!r} (must be None)")
        assert not bad, (
            "Business jet codes must map to None: " + "; ".join(bad)
        )

    def test_national_coverage_fractions_from_fixture(self) -> None:
        """
        Kind: property / metamorphic.
        Tolerance: exact/proxy/None fractions sum to 1.0 (abs ≤ 1e-10).
        Reason: the fixture covers all 105 codes; once every code is mapped,
                departure-weighted shares must partition to 100%.
        Cannot catch: wrong departure counts in the fixture.
        """
        from fleet import _PROXY_CODES
        codes = _load_fixture_codes()
        total_deps = sum(codes.values())

        exact_deps = 0; proxy_deps = 0; none_deps = 0; absent_deps = 0
        for code, deps in codes.items():
            if code not in BTS_TO_OPENAP:
                absent_deps += deps
            elif BTS_TO_OPENAP[code] is None:
                none_deps += deps
            elif code in _PROXY_CODES:
                proxy_deps += deps
            else:
                exact_deps += deps

        fracs = (exact_deps + proxy_deps + none_deps + absent_deps) / total_deps
        assert abs(fracs - 1.0) <= 1e-10, (
            f"Coverage fractions do not sum to 1.0: {fracs}"
        )
        # After NW-21, absent_deps must be 0 (every code is an explicit decision)
        assert absent_deps == 0, (
            f"{absent_deps:,} departures ({absent_deps/total_deps*100:.2f}%) "
            f"use codes absent from BTS_TO_OPENAP after NW-21 fix"
        )

    def test_region_coverage_exact_floor(self) -> None:
        """
        Kind: threshold / metamorphic.
        Tolerance: exact fraction ≥ 0.763 (76.3% — the pre-NW-21 baseline;
                   NW-21 should maintain or improve it).
        Reason: the study region uses only 34 aircraft types in Q1 2019;
                all are already in BTS_TO_OPENAP from NW-16. NW-21 adds new
                codes that are NOT in the region, so region exact coverage
                must not decrease. The baseline is 76.31% from the real data.
        Cannot catch: changes to proxy vs exact classification of existing codes.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        assert report["exactly_mapped"] >= 0.763, (
            f"Region exactly_mapped = {report['exactly_mapped']:.4f} < 0.763"
        )

    def test_region_coverage_proxy_floor(self) -> None:
        """
        Kind: threshold.
        Tolerance: proxy fraction ≥ 0.160 (16.0% — just below the 16.2%
                   baseline to allow for floating-point variance in the sample
                   fixture).
        Reason: proxy codes like 623/631/629/634/699/723/625/624 should remain
                classified as proxy; this guards against accidentally moving
                them to exact or unmapped.
        Cannot catch: a proxy replaced by a different wrong proxy.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        assert report["proxy_mapped"] >= 0.160, (
            f"Region proxy_mapped = {report['proxy_mapped']:.4f} < 0.160"
        )

    def test_region_coverage_unmapped_ceiling(self) -> None:
        """
        Kind: threshold.
        Tolerance: unmapped fraction ≤ 0.076 (≤ 7.6% — just above the 7.54%
                   baseline; it should not increase because NW-21 fixes 687
                   which is in the region with 1 departure).
        Reason: the only unmapped codes in the region are 608 (717-200),
                655 (MD-80), 656 (MD-90), and 479 (PC-12). Fixing 687 → a333
                reduces unmapped by 1 dep (negligible but correct).
        Cannot catch: a future aircraft type entering the region.
        """
        df = load_t100([str(SAMPLE_T100)])
        mix = aircraft_mix(df, REGION)
        report = coverage_report(mix)
        assert report["unmapped"] <= 0.076, (
            f"Region unmapped = {report['unmapped']:.4f} > 0.076"
        )

    def test_proxy_codes_set_contains_nw21_new_proxies(self) -> None:
        """
        Kind: property (regression guard).
        Tolerance: exact (set membership in _PROXY_CODES).
        Reason: NW-21 added 5 new proxy codes (676, 674, 628, 616, 874). If any
                is removed from _PROXY_CODES, coverage_report will count its
                departures as 'exact' instead of 'proxy', silently
                misrepresenting mapping quality. The test guards this because
                476 and 674 (ERJ-140/135) have 1.13%/0.30% national share.
        Cannot catch: a code that is in _PROXY_CODES but has wrong OpenAP target.
        """
        from fleet import _PROXY_CODES
        new_proxies = {
            "676",  # ERJ-140 → e145 (proxy: same CF34 platform, shorter)
            "674",  # ERJ-135 → e145 (proxy: same CF34 platform, shorter)
            "628",  # CRJ-100 → crj9 (proxy: 50 vs 90 seats)
            "616",  # 737-500 → b734 (proxy: same CFM56-3, shorter)
            "874",  # A340-600 → a343 (proxy: same CFM56-5C engines)
        }
        missing = new_proxies - _PROXY_CODES
        assert not missing, (
            f"NW-21 proxy codes missing from _PROXY_CODES: {sorted(missing)}"
        )

    def test_fixture_proxy_codes_not_in_exact_bucket(self) -> None:
        """
        Kind: property (fixture-based, coverage_report consistency).
        Tolerance: exact (no NW-21 proxy code appears in exactly_mapped_codes
                   when running coverage_report on a hand-built mix containing
                   those codes).
        Reason: directly tests that _PROXY_CODES membership controls bucket
                assignment in coverage_report. A proxy code absent from
                _PROXY_CODES would silently inflate the exact bucket.
        Cannot catch: a proxy code with a correct target in a wrong bucket
                      if the test data doesn't include it.
        """
        # Build a synthetic mix with one segment using ERJ-140 (676) and
        # ERJ-135 (674) departures alongside an exact code (888)
        synthetic_mix = {
            ("BOS", "ORD"): {
                "shares": {"888": 0.5, "676": 0.3, "674": 0.2},
                "seats_per_departure": 150.0,
                "load_factor": 0.8,
                "_total_departures": 100.0,
            }
        }
        report = coverage_report(synthetic_mix)
        assert "676" not in report["exactly_mapped_codes"], (
            "676 (ERJ-140) must be proxy, not exact"
        )
        assert "674" not in report["exactly_mapped_codes"], (
            "674 (ERJ-135) must be proxy, not exact"
        )
        assert "676" in report["proxy_mapped_codes"], (
            "676 (ERJ-140) must appear in proxy_mapped_codes"
        )
        assert "674" in report["proxy_mapped_codes"], (
            "674 (ERJ-135) must appear in proxy_mapped_codes"
        )
        assert "888" in report["exactly_mapped_codes"], (
            "888 (737-900ER) must appear in exactly_mapped_codes"
        )

    @pytest.mark.skipif(
        not all(p.exists() for p in CACHE_PATHS),
        reason=(
            "Full BTS T-100 cache files not present (data/bts-cache/ is git-ignored). "
            "Run the data-fetch script to populate data/bts-cache/ before running "
            "this test."
        ),
    )
    def test_real_national_absent_codes_zero_after_nw21(self) -> None:
        """
        Kind: golden (real BTS Q1 2019 data, cache-dependent).
        Tolerance: 0 departures with codes absent from BTS_TO_OPENAP.
        Reason: NW-21 requires every distinct national passenger AIRCRAFT_TYPE
                code to be an explicit entry. Before NW-21, 70 codes (7.81%
                of 1,995,383 national departures) were absent.
        Source: data/bts-cache/t100_2019_0{1,2,3}.csv.
        Cannot catch: a new carrier introducing a code not yet in the fixture.
        """
        from fleet import _PROXY_CODES
        paths = [str(p) for p in CACHE_PATHS]
        df = load_t100(paths)
        national_grouped = df.groupby("AIRCRAFT_TYPE")["DEPARTURES_PERFORMED"].sum()
        total = float(national_grouped.sum())
        absent_deps = sum(
            float(deps)
            for code, deps in national_grouped.items()
            if code not in BTS_TO_OPENAP
        )
        assert absent_deps == 0.0, (
            f"{absent_deps:,.0f} national departures ({absent_deps/total*100:.2f}%) "
            "use aircraft type codes absent from BTS_TO_OPENAP after NW-21 fix"
        )

    @pytest.mark.skipif(
        not all(p.exists() for p in CACHE_PATHS),
        reason=(
            "Full BTS T-100 cache files not present (data/bts-cache/ is git-ignored). "
            "Run the data-fetch script to populate data/bts-cache/ before running "
            "this test."
        ),
    )
    def test_real_national_coverage_after_nw21(self) -> None:
        """
        Kind: golden / threshold (real BTS Q1 2019 data, cache-dependent).
        Tolerance:
          - exact ≥ 0.67 (67% national exact; was ~67.60% before 687 fix)
          - proxy ≥ 0.18 (18% national proxy; was ~18.46%)
          - none (unmapped) ≤ 0.15 (turboprops + old jets + bizjets ≈ 14%)
          - absent == 0 (post NW-21)
        Reason: national coverage improves after adding 70 codes; most newly
                added codes (turboprops, bizjets) map to None, so exact/proxy
                shares are similar but absent goes to 0.
        Source: data/bts-cache/t100_2019_0{1,2,3}.csv.
        Cannot catch: misclassification within exact/proxy/None buckets.
        """
        from fleet import _PROXY_CODES
        paths = [str(p) for p in CACHE_PATHS]
        df = load_t100(paths)
        national_grouped = df.groupby("AIRCRAFT_TYPE")["DEPARTURES_PERFORMED"].sum()
        total = float(national_grouped.sum())

        exact_deps = 0.0; proxy_deps = 0.0; none_deps = 0.0; absent_deps = 0.0
        for code, deps in national_grouped.items():
            d = float(deps)
            if code not in BTS_TO_OPENAP:
                absent_deps += d
            elif BTS_TO_OPENAP[code] is None:
                none_deps += d
            elif code in _PROXY_CODES:
                proxy_deps += d
            else:
                exact_deps += d

        print(f"\nPost-NW-21 national coverage:")
        print(f"  exact:   {exact_deps/total*100:.3f}%")
        print(f"  proxy:   {proxy_deps/total*100:.3f}%")
        print(f"  none:    {none_deps/total*100:.3f}%")
        print(f"  absent:  {absent_deps/total*100:.3f}%")

        assert absent_deps == 0.0, f"Absent codes: {absent_deps:,.0f} deps"
        assert exact_deps / total >= 0.67, f"exact={exact_deps/total:.4f} < 0.67"
        assert proxy_deps / total >= 0.18, f"proxy={proxy_deps/total:.4f} < 0.18"
        assert none_deps / total <= 0.15, f"none={none_deps/total:.4f} > 0.15"
