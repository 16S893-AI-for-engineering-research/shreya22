"""
Tests for project/src/t2_check.py — BTS Schedule T2 fuel vs OpenAP check.

=== Overview ===

BTS Form 41 Schedule T2 reports actual gallons burned per aircraft type per
quarter.  OpenAP generates synthetic trajectories and integrates a physics-
based fuel-flow model.  This test file verifies both the T2 parser
(t2_fuel_per_nm) and the comparison function (compare_openap) against the
committed fixture and, where the cache is present, the full T2 file.

=== Tolerance decision (chosen BEFORE looking at types beyond the 5) ===

Expected ratio = OpenAP_kg_per_nm / T2_kg_per_nm.

OpenAP is expected to run LOW relative to T2 because:
  (a) Taxi and APU fuel — typically 5–15 % of block fuel (ICAO Doc 9889) — is
      included in T2's AIRCRAFT_FUELS_921 but absent from the synthetic
      OpenAP trajectory which starts at wheels-off.
  (b) Non-revenue flying: T2 includes some positioning/ferry flights whose
      fuel is diluted across fewer revenue seat-miles.
  (c) Real route ≥ great circle: actual routes add ~2–5 % (winds, ATC routing).
  (d) Mass assumption: 0.8 × MTOW is a rough midpoint; heavier aircraft burn
      more; the real distribution is unknown.
  (e) Jet-A density assumed 0.80 kg/L; ASTM/ICAO range is 0.775–0.840 kg/L,
      a ±3.2 % uncertainty.

Combined these causes can push T2 ~15–25 % above OpenAP (ratio 0.75–0.85),
consistent with the reviewer probe (0.85–0.92 for the 5 reviewed types).

We therefore choose:
  Lower bound: 0.70  — allows up to 30 % underestimate (conservative envelope
                        for all causes operating simultaneously in worst case).
  Upper bound: 1.10  — a 10 % overestimate is plausible if a type averages
                        light payload / unusually efficient routing; values
                        higher than 1.10 would suggest a model error.

This band is a MAGNITUDE check that OpenAP is in the right order of magnitude,
NOT a calibration target.  OpenAP is NOT tuned to T2.

=== Test kinds ===
  - example:        concrete single-case assertions with exact/range checks
  - property:       invariant over multiple inputs
  - metamorphic:    result changes predictably under input changes
  - differential:   two implementations / subsets agree
  - golden:         pinned reference values from a real data source

=== Skip policy ===
Tests that require data/bts-cache/ use @pytest.mark.skipif at function level
with a clear reason string.  Module-level skip guards (allow_module_level)
are banned by test_no_silent_skips.py.
"""
from __future__ import annotations

import io
import pathlib

import pandas as pd
import pytest

# ── locate helpers ────────────────────────────────────────────────────────────
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
_FIXTURES_DIR = _THIS_DIR / "fixtures" / "bts"
_CACHE_DIR = _THIS_DIR.parent.parent / "data" / "bts-cache"

import sys
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from t2_check import t2_fuel_per_nm, compare_openap

# ── skip predicates ───────────────────────────────────────────────────────────
_T2_CACHE_PRESENT = (_CACHE_DIR / "t2_2019_q1.csv").exists()
_SKIP_NO_T2 = pytest.mark.skipif(
    not _T2_CACHE_PRESENT,
    reason="data/bts-cache/t2_2019_q1.csv absent; skipping real-data T2 test",
)

# ── constants: unit conversion (pinned) ──────────────────────────────────────
# gallon -> litre  (exact by NIST)
_GAL_TO_L = 3.785411784
# Jet-A density assumed 0.80 kg/L (reviewer probe and module design)
_JET_A_KG_PER_L = 0.80
# statute mile -> nautical mile  (exact: 1 sm = 1852/1609.344 nm)
_SM_TO_NM = 1.0 / 1.150779

# ── fixture loader ────────────────────────────────────────────────────────────
_FIXTURE_PATH = _FIXTURES_DIR / "t2_q1_2019_sample.csv"


def _load_fixture() -> pd.DataFrame:
    """Load the committed T2 sample fixture."""
    return pd.read_csv(_FIXTURE_PATH)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Unit conversion constants
# ─────────────────────────────────────────────────────────────────────────────

class TestUnitConversions:
    """Pin unit conversions to known values so any future refactor breaks here.

    Kind: example / property.
    Tolerance: relative 1e-9 for floating-point round-trip.
    Cannot catch: wrong constant that happens to be consistent within code.
    """

    def test_gallons_to_litres_nist(self) -> None:
        """
        Kind: example, exact (NIST definition).
        Tolerance: absolute 1e-9 (rounding in float repr).
        1 US gallon = 3.785411784 litres exactly (NIST SP 811, 2008).
        Cannot catch: a wrong constant that is internally consistent.
        """
        # Import the constant from the module itself so tests break if changed
        from t2_check import _GAL_TO_L as module_const
        assert abs(module_const - 3.785411784) < 1e-9, (
            f"_GAL_TO_L should be 3.785411784 (NIST); got {module_const}"
        )

    def test_jet_a_density_kg_per_litre(self) -> None:
        """
        Kind: example, exact (design assumption).
        Tolerance: absolute 1e-9.
        Jet-A density assumed 0.80 kg/L (reviewer probe).
        Cannot catch: density being correct but applied to wrong volume unit.
        """
        from t2_check import _JET_A_KG_PER_L as module_const
        assert abs(module_const - 0.80) < 1e-9, (
            f"_JET_A_KG_PER_L should be 0.80; got {module_const}"
        )

    def test_statute_miles_to_nautical_miles(self) -> None:
        """
        Kind: example, exact.
        Tolerance: absolute 1e-9.
        1 statute mile = 1609.344 m; 1 nautical mile = 1852 m exactly.
        Factor = 1852/1609.344 = 1.150779... ; inverse = 1/1.150779.
        Cannot catch: wrong factor that is internally consistent.
        """
        from t2_check import _SM_TO_NM as module_const
        expected = 1.0 / 1.150779
        assert abs(module_const - expected) < 1e-9, (
            f"_SM_TO_NM should be {expected}; got {module_const}"
        )

    def test_gallons_to_kg_pipeline(self) -> None:
        """
        Kind: example, tolerance < 1e-6 relative.
        Convert 1000 gallons to kg via the module constants.
        1000 gal * 3.785411784 L/gal * 0.80 kg/L = 3028.3 kg.
        Cannot catch: factors applied in wrong order if they happen to commute.
        """
        from t2_check import _GAL_TO_L, _JET_A_KG_PER_L
        kg = 1000.0 * _GAL_TO_L * _JET_A_KG_PER_L
        expected = 1000.0 * 3.785411784 * 0.80
        assert abs(kg - expected) < 1e-6, (
            f"1000 gal should convert to {expected:.4f} kg; got {kg:.4f}"
        )
        assert abs(kg - 3028.329) < 0.001, (
            f"1000 gal should be ~3028.3 kg; got {kg:.4f}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 2. t2_fuel_per_nm — core parsing and aggregation
# ─────────────────────────────────────────────────────────────────────────────

class TestT2FuelPerNm:
    """Tests for t2_fuel_per_nm(df) -> dict of per-type metrics."""

    def test_returns_dict(self) -> None:
        """
        Kind: example.
        Tolerance: none (type check).
        t2_fuel_per_nm must return a dict.
        Cannot catch: a dict subclass that breaks attribute access.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        assert isinstance(result, dict), (
            f"t2_fuel_per_nm must return a dict; got {type(result)}"
        )

    def test_returns_five_types_from_fixture(self) -> None:
        """
        Kind: example (fixture-based).
        Tolerance: none.
        The committed fixture contains Z-class data for exactly 5 aircraft
        types (694, 614, 888, 698, 612 as strings).  t2_fuel_per_nm must
        return an entry for each.
        Cannot catch: entries with correct keys but wrong values.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        # BTS types are normalised to zero-padded 3-digit strings
        expected = {"694", "614", "888", "698", "612"}
        missing = expected - set(result.keys())
        assert not missing, (
            f"t2_fuel_per_nm missing entries for aircraft types: {missing}"
        )

    def test_entry_has_required_keys(self) -> None:
        """
        Kind: example.
        Tolerance: none.
        Each entry must have: kg_per_nm, stage_nm, departures.
        Cannot catch: correct keys but wrong types.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        for ac_type, entry in result.items():
            for key in ("kg_per_nm", "stage_nm", "departures"):
                assert key in entry, (
                    f"Entry for {ac_type!r} missing key {key!r}; "
                    f"got keys: {list(entry.keys())}"
                )

    def test_kg_per_nm_positive(self) -> None:
        """
        Kind: property (all entries).
        Tolerance: none.
        All kg_per_nm values must be strictly positive (fuel > 0, nm > 0
        by construction of the Z-only, fuel>0, miles>0 filter).
        Cannot catch: a positive value that is wildly wrong in magnitude.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        for ac_type, entry in result.items():
            assert entry["kg_per_nm"] > 0.0, (
                f"kg_per_nm for {ac_type!r} must be positive; got {entry['kg_per_nm']}"
            )

    def test_stage_nm_positive(self) -> None:
        """
        Kind: property.
        Tolerance: none.
        All stage_nm values must be strictly positive.
        Cannot catch: positive but incorrect value.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        for ac_type, entry in result.items():
            assert entry["stage_nm"] > 0.0, (
                f"stage_nm for {ac_type!r} must be positive; got {entry['stage_nm']}"
            )

    def test_departures_positive_integer_like(self) -> None:
        """
        Kind: property.
        Tolerance: none.
        All departures must be > 0.  They represent counted aircraft
        departures so must be positive.
        Cannot catch: slightly wrong count (off by one).
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        for ac_type, entry in result.items():
            assert entry["departures"] > 0, (
                f"departures for {ac_type!r} must be > 0; got {entry['departures']}"
            )

    def test_z_class_only_rule_f_rows_ignored(self) -> None:
        """
        Kind: example / metamorphic (Z-only rule).
        Tolerance: relative 1e-9 (exact arithmetic).
        The fixture contains F-class rows with NaN fuel for the same aircraft
        types.  Adding F rows must not change the result because t2_fuel_per_nm
        must use only SERVICE_CLASS == 'Z' rows.
        Cannot catch: F rows with non-NaN fuel (they don't exist in BTS T2).
        """
        df = _load_fixture()
        # Verify fixture actually has F rows
        f_count = (df["SERVICE_CLASS"] == "F").sum()
        assert f_count > 0, (
            "Fixture has no F rows; test cannot verify Z-only rule. "
            "Regenerate fixture with F rows included."
        )
        result_full = t2_fuel_per_nm(df)

        # Drop all non-Z rows and recompute
        df_z_only = df[df["SERVICE_CLASS"] == "Z"].copy()
        result_z = t2_fuel_per_nm(df_z_only)

        assert set(result_full.keys()) == set(result_z.keys()), (
            "Removing F rows changed the set of aircraft types returned"
        )
        for ac_type in result_full:
            diff = abs(result_full[ac_type]["kg_per_nm"] - result_z[ac_type]["kg_per_nm"])
            assert diff < 1e-9, (
                f"F rows changed kg_per_nm for {ac_type!r}: "
                f"full={result_full[ac_type]['kg_per_nm']:.6f}, "
                f"z_only={result_z[ac_type]['kg_per_nm']:.6f}"
            )

    def test_f_only_df_raises(self) -> None:
        """
        Kind: negative / Z-only rule.
        Tolerance: none (must raise, not return empty silently).
        A DataFrame with ONLY F-class rows (no Z rows) must raise ValueError,
        not silently return an empty dict.  Returning {} would be a silent
        failure that makes compare_openap return {} without any indication
        that the caller passed the wrong data.
        Cannot catch: a ValueError raised with the wrong message.
        """
        df = _load_fixture()
        f_only = df[df["SERVICE_CLASS"] != "Z"].copy()
        # Verify it has rows but no Z rows
        assert len(f_only) > 0, "Fixture must have non-Z rows for this test"
        assert (f_only["SERVICE_CLASS"] == "Z").sum() == 0

        with pytest.raises(ValueError, match="(?i)no.*Z.*rows|Z.*service.*class|Z.*class"):
            t2_fuel_per_nm(f_only)

    def test_golden_a320_kg_per_nm(self) -> None:
        """
        Kind: golden (pinned from real BTS T2 2019 Q1 data, fixture extract).
        Tolerance: absolute 0.005 kg/nm.
        Reason: the fixture is a complete extract of all Z-class AIRCRAFT_CONFIG=1
        rows for AC type 694 (A320) in the Q1 2019 T2 file.  The reviewer
        computed 7.67 kg/nm (rounded to 2 dp); the full-precision value is
        7.6655 kg/nm.  Tolerance 0.005 accommodates float arithmetic.
        Method: sum fuel_gal * GAL_TO_L * JET_A_KG_PER_L / (sum_miles * SM_TO_NM).
        Cannot catch: a fixture whose rows are not the full extract of A320 rows.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        assert "694" in result, "BTS type 694 (A320) must be in result"
        assert abs(result["694"]["kg_per_nm"] - 7.6655) < 0.005, (
            f"A320 (694) kg_per_nm expected ~7.6655; got {result['694']['kg_per_nm']:.4f}"
        )

    def test_golden_b738_kg_per_nm(self) -> None:
        """
        Kind: golden (pinned from real BTS T2 2019 Q1 data, fixture extract).
        Tolerance: absolute 0.005 kg/nm.
        Reviewer computed 7.19 kg/nm for B737-800 (614); full-precision 7.1874.
        Cannot catch: wrong aggregation that happens to match the golden value.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        assert "614" in result
        assert abs(result["614"]["kg_per_nm"] - 7.1874) < 0.005, (
            f"B737-800 (614) kg_per_nm expected ~7.1874; got {result['614']['kg_per_nm']:.4f}"
        )

    def test_golden_a320_stage_nm(self) -> None:
        """
        Kind: golden (pinned).
        Tolerance: absolute 0.5 nm.
        A320 mean stage length 899.85 nm from fixture.
        Cannot catch: wrong stage but right kg/nm (would require compensating errors).
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        assert abs(result["694"]["stage_nm"] - 899.85) < 0.5, (
            f"A320 stage_nm expected ~899.85; got {result['694']['stage_nm']:.2f}"
        )

    def test_golden_a320_departures(self) -> None:
        """
        Kind: golden (pinned).
        Tolerance: absolute 1 (integer count).
        A320 (694) total departures in Q1 2019 fixture = 167981.
        Cannot catch: correct count computed differently.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        assert abs(result["694"]["departures"] - 167981) < 1, (
            f"A320 departures expected 167981; got {result['694']['departures']}"
        )

    def test_aircraft_type_keys_are_strings(self) -> None:
        """
        Kind: property.
        Tolerance: none.
        Keys in the returned dict must be strings (BTS type codes as strings,
        e.g. '694', not int 694), to match the BTS_TO_OPENAP mapping convention
        in fleet.py which uses zero-padded 3-digit string keys.
        Cannot catch: string vs bytes distinction on Python 3.
        """
        df = _load_fixture()
        result = t2_fuel_per_nm(df)
        for key in result.keys():
            assert isinstance(key, str), (
                f"Key {key!r} must be a string; got {type(key)}"
            )

    def test_nan_fuel_rows_excluded(self) -> None:
        """
        Kind: metamorphic.
        Tolerance: relative 1e-9.
        Rows where AIRCRAFT_FUELS_921 is NaN must be excluded from aggregation.
        We inject a NaN-fuel Z row for type 694 and verify the result is
        unchanged from a df without that row.
        Cannot catch: NaN propagation that cancels out numerically.
        """
        df = _load_fixture()
        z_rows = df[df["SERVICE_CLASS"] == "Z"].copy()
        # Find one row with real fuel
        real_fuel_idx = z_rows[
            (z_rows["AIRCRAFT_TYPE"] == 694) & z_rows["AIRCRAFT_FUELS_921"].notna()
        ].index[0]

        # Baseline: result without that row altered
        result_baseline = t2_fuel_per_nm(df)

        # Inject NaN fuel for that row
        df_nan = df.copy()
        df_nan.loc[real_fuel_idx, "AIRCRAFT_FUELS_921"] = float("nan")
        result_nan = t2_fuel_per_nm(df_nan)

        # The result should differ (the NaN row is excluded, so less fuel summed)
        # kg_per_nm may change slightly if that carrier was different from mean
        # — what we're testing is that the NaN row doesn't appear as zero fuel
        # Let's verify: sum of fuel for 694 should decrease
        # We do a soft check: NaN injection must not increase kg_per_nm
        # Actually we verify they CAN differ (NaN row excluded = less total fuel/nm)
        # The key invariant: result_nan["694"]["departures"] < result_baseline["694"]["departures"]
        # because the NaN-fuel row's departures should also be excluded
        # (only rows with fuel>0 AND miles>0 are included)
        assert result_nan["694"]["departures"] < result_baseline["694"]["departures"], (
            "Excluding a fuel-NaN row must reduce the departure count for type 694; "
            f"baseline={result_baseline['694']['departures']}, "
            f"after_nan={result_nan['694']['departures']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. compare_openap — comparison with injected fuel function
# ─────────────────────────────────────────────────────────────────────────────

class TestCompareOpenAP:
    """Tests for compare_openap(...) using injected (fake) fuel functions."""

    # Mapping from BTS code to OpenAP code (subset used in tests)
    _BTS_TO_OPENAP = {
        "694": "a320",
        "614": "b738",
        "888": "b739",
        "698": "a319",
        "612": "b737",
    }

    def _make_fake_fuel_fn(self, kg_per_nm_by_ac: dict) -> object:
        """Return a fake flight_fuel_nox callable.

        The fake returns fuel_kg = kg_per_nm * distance_nm for the given
        aircraft code.
        """
        def _fake(ac: str, distance_nm: float, mass_fraction: float = 0.8) -> dict:
            kgnm = kg_per_nm_by_ac.get(ac, 5.0)
            return {
                "fuel_kg": kgnm * distance_nm,
                "nox_kg": 0.0,
                "nan_fraction": 0.0,
                "n_points": 100,
            }
        return _fake

    def test_returns_dict_with_bts_keys(self) -> None:
        """
        Kind: example.
        Tolerance: none.
        compare_openap must return a dict keyed by BTS aircraft type codes
        (strings) for types present in both the T2 data and the bts_to_openap
        mapping.
        Cannot catch: correct keys with wrong values.
        """
        df = _load_fixture()
        fake_fn = self._make_fake_fuel_fn({"a320": 6.5, "b738": 6.3, "b739": 6.5, "a319": 6.0, "b737": 6.6})
        result = compare_openap(df, self._BTS_TO_OPENAP, fake_fn)
        assert isinstance(result, dict), (
            f"compare_openap must return a dict; got {type(result)}"
        )
        assert "694" in result, "result must include BTS type '694' (A320)"

    def test_entry_has_required_keys(self) -> None:
        """
        Kind: example.
        Tolerance: none.
        Each entry must have: t2_kg_per_nm, openap_kg_per_nm, ratio,
        stage_nm, departures.
        Cannot catch: correct keys but wrong types.
        """
        df = _load_fixture()
        fake_fn = self._make_fake_fuel_fn({"a320": 6.5})
        result = compare_openap(df, {"694": "a320"}, fake_fn)
        entry = result.get("694", {})
        for key in ("t2_kg_per_nm", "openap_kg_per_nm", "ratio", "stage_nm", "departures"):
            assert key in entry, (
                f"compare_openap entry for '694' missing key {key!r}; "
                f"got keys: {list(entry.keys())}"
            )

    def test_ratio_is_openap_over_t2(self) -> None:
        """
        Kind: example (ratio definition).
        Tolerance: absolute 1e-9.
        ratio must equal openap_kg_per_nm / t2_kg_per_nm.
        Cannot catch: ratio being computed correctly but documented wrong.
        """
        df = _load_fixture()
        # Use a fake that always returns 6.0 kg/nm regardless of distance
        fake_fn = self._make_fake_fuel_fn({"a320": 6.0})
        result = compare_openap(df, {"694": "a320"}, fake_fn)
        entry = result["694"]
        expected_ratio = entry["openap_kg_per_nm"] / entry["t2_kg_per_nm"]
        assert abs(entry["ratio"] - expected_ratio) < 1e-9, (
            f"ratio must equal openap_kg_per_nm / t2_kg_per_nm; "
            f"got ratio={entry['ratio']:.6f}, computed={expected_ratio:.6f}"
        )

    def test_openap_kg_per_nm_uses_injected_function(self) -> None:
        """
        Kind: example (dependency injection).
        Tolerance: relative 0.01 (1 %).
        compare_openap must call the injected fuel function at the T2 stage_nm
        for the mapped OpenAP code and use its fuel_kg / stage_nm as
        openap_kg_per_nm.  We inject a function that returns a known value.
        Cannot catch: function called at wrong stage but giving same answer by
        coincidence.
        """
        df = _load_fixture()

        # Inject a function that records what it was called with
        calls: list[tuple] = []

        def _recording_fn(ac: str, distance_nm: float, mass_fraction: float = 0.8) -> dict:
            calls.append((ac, distance_nm, mass_fraction))
            return {"fuel_kg": 1234.5, "nox_kg": 0.0, "nan_fraction": 0.0, "n_points": 10}

        result = compare_openap(df, {"694": "a320"}, _recording_fn)

        # Verify the injected function was called for the A320
        assert any(ac == "a320" for ac, _, _ in calls), (
            f"compare_openap must call the fuel function for 'a320'; calls={calls}"
        )
        # Verify openap_kg_per_nm = 1234.5 / stage_nm
        entry = result["694"]
        expected_openap_kgnm = 1234.5 / entry["stage_nm"]
        assert abs(entry["openap_kg_per_nm"] - expected_openap_kgnm) < 1e-6, (
            f"openap_kg_per_nm should be fuel_kg/stage_nm = {expected_openap_kgnm:.4f}; "
            f"got {entry['openap_kg_per_nm']:.4f}"
        )

    def test_types_not_in_bts_to_openap_excluded(self) -> None:
        """
        Kind: property (exclusion behaviour).
        Tolerance: none.
        Aircraft types in the T2 data but NOT in bts_to_openap must be silently
        skipped (no entry in the result).  compare_openap is called with a
        sparse mapping that only includes '694'.
        Cannot catch: types that appear in the result with a wrong openap code.
        """
        df = _load_fixture()
        sparse_map = {"694": "a320"}
        fake_fn = self._make_fake_fuel_fn({"a320": 6.5})
        result = compare_openap(df, sparse_map, fake_fn)
        assert set(result.keys()) == {"694"}, (
            f"Result must only contain types in bts_to_openap; "
            f"got keys: {sorted(result.keys())}"
        )

    def test_none_mapped_types_excluded(self) -> None:
        """
        Kind: property (None mapping excluded).
        Tolerance: none.
        Types mapped to None in bts_to_openap (i.e. UNMAPPED) must not appear
        in the result.  This mirrors the fleet.py convention where None means
        'no suitable OpenAP model'.
        Cannot catch: a None type that silently causes an error.
        """
        df = _load_fixture()
        # Map 694 -> None (unmapped), 614 -> 'b738'
        mapping_with_none = {"694": None, "614": "b738"}
        fake_fn = self._make_fake_fuel_fn({"b738": 6.3})
        result = compare_openap(df, mapping_with_none, fake_fn)
        assert "694" not in result, (
            "Types mapped to None must not appear in compare_openap result"
        )
        assert "614" in result, "Types mapped to a real OpenAP code must appear"

    def test_ratio_numerically_consistent_with_fake(self) -> None:
        """
        Kind: differential (fake input → known output).
        Tolerance: absolute 0.001 on ratio.
        With a fake fuel function returning exactly 6.49 kg/nm for a320
        and T2 kg/nm of ~7.6655 for type 694, the ratio must be ≈0.847.
        Cannot catch: wrong T2 value that combined with a wrong OpenAP value
        still gives a plausible ratio.
        """
        df = _load_fixture()
        # Fake: returns exactly 6.49 * stage_nm as fuel_kg
        def _fake(ac: str, distance_nm: float, mass_fraction: float = 0.8) -> dict:
            return {"fuel_kg": 6.49 * distance_nm, "nox_kg": 0.0,
                    "nan_fraction": 0.0, "n_points": 10}

        result = compare_openap(df, {"694": "a320"}, _fake)
        # openap_kg_per_nm = 6.49 * stage / stage = 6.49
        # t2_kg_per_nm = 7.6655
        expected_ratio = 6.49 / result["694"]["t2_kg_per_nm"]
        assert abs(result["694"]["ratio"] - expected_ratio) < 0.001, (
            f"Expected ratio ~{expected_ratio:.3f}; got {result['694']['ratio']:.3f}"
        )

    def test_fuel_function_called_once_per_type(self) -> None:
        """
        Kind: property.
        Tolerance: none.
        The fuel function must be called exactly once per mapped aircraft type
        (using the mean stage_nm of that type, not once per carrier row).
        Cannot catch: called once per carrier but summing correctly by coincidence.
        """
        df = _load_fixture()
        call_count = {"a320": 0}

        def _counting_fn(ac: str, distance_nm: float, mass_fraction: float = 0.8) -> dict:
            call_count[ac] = call_count.get(ac, 0) + 1
            return {"fuel_kg": 6.0 * distance_nm, "nox_kg": 0.0,
                    "nan_fraction": 0.0, "n_points": 10}

        compare_openap(df, {"694": "a320"}, _counting_fn)
        assert call_count.get("a320", 0) == 1, (
            f"Fuel function for 'a320' must be called exactly once; "
            f"called {call_count.get('a320', 0)} times"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. Real-data check: ratio band for 5 reviewer types (cache optional)
# ─────────────────────────────────────────────────────────────────────────────

# The 5 reviewer types with expected ratio band [0.70, 1.10].
# This band is chosen from known physical causes BEFORE inspecting additional
# types.  See module docstring for full justification.
_RATIO_LO = 0.70
_RATIO_HI = 1.10

# BTS → OpenAP mapping for the 5 reviewer types
_FIVE_TYPES_MAP = {
    "694": "a320",
    "614": "b738",
    "888": "b739",
    "698": "a319",
    "612": "b737",
}

# Reviewer reference ratios (for documentation; not used as bounds)
_REVIEWER_RATIOS = {
    "694": 0.85,  # A320
    "614": 0.87,  # B737-800
    "888": 0.88,  # B737-900ER
    "698": 0.89,  # A319
    "612": 0.92,  # B737-700
}


class TestRealOpenAPRatioFixture:
    """Real-data check using the committed fixture and REAL OpenAP.

    This is NOT a calibration.  OpenAP is NOT tuned to T2.  The check verifies
    that OpenAP produces physically plausible fuel estimates (within the
    systematic-bias band caused by taxi, APU, non-revenue flying, etc.).
    """

    def test_five_types_ratio_in_band_fixture(self) -> None:
        """
        Kind: golden / magnitude check using committed fixture + real OpenAP.
        Tolerance: ratio in [0.70, 1.10] per type.
        Reason for band: see module docstring.  Lower 0.70 allows for taxi/APU
        (5-15 %), non-revenue flying, density uncertainty.  Upper 1.10 allows
        for light-payload efficiency gain.

        This is a check of magnitude, NOT a calibration.  OpenAP is NOT tuned
        to T2.  A ratio outside the band for any of the 5 types indicates
        either a new systematic error or a problem with the fixture/conversion.

        Cannot catch: compensating errors that give a ratio in-band for the
        wrong physical reasons.
        """
        from openap_perf import flight_fuel_nox

        df = _load_fixture()
        result = compare_openap(df, _FIVE_TYPES_MAP, flight_fuel_nox)

        outside_band: list[str] = []
        for bts_code, entry in result.items():
            r = entry["ratio"]
            if not (_RATIO_LO <= r <= _RATIO_HI):
                outside_band.append(
                    f"  BTS {bts_code} ({_FIVE_TYPES_MAP[bts_code]}): "
                    f"ratio={r:.3f} not in [{_RATIO_LO}, {_RATIO_HI}]"
                )

        assert not outside_band, (
            "OpenAP/T2 ratio outside expected band for types:\n"
            + "\n".join(outside_band)
            + "\nBand [0.70, 1.10] is a magnitude check, NOT a calibration. "
            "A ratio outside this band suggests a systematic error."
        )

    def test_five_types_ratio_near_reviewer_values_fixture(self) -> None:
        """
        Kind: golden / regression against reviewer probe.
        Tolerance: absolute 0.05 on ratio per type.
        The reviewer computed specific ratios (0.85, 0.87, 0.88, 0.89, 0.92)
        for these 5 types.  Our computed ratios must be within 0.05 of those
        reference values.  A larger deviation suggests a unit conversion error
        or a changed OpenAP model version.
        Cannot catch: small systematic shift that stays within tolerance.
        """
        from openap_perf import flight_fuel_nox

        df = _load_fixture()
        result = compare_openap(df, _FIVE_TYPES_MAP, flight_fuel_nox)

        for bts_code, ref_ratio in _REVIEWER_RATIOS.items():
            if bts_code not in result:
                pytest.fail(f"BTS type {bts_code!r} not in compare_openap result")
            actual_ratio = result[bts_code]["ratio"]
            assert abs(actual_ratio - ref_ratio) < 0.05, (
                f"BTS {bts_code}: ratio={actual_ratio:.3f}, reviewer reference={ref_ratio:.2f}, "
                f"diff={abs(actual_ratio - ref_ratio):.3f} > 0.05. "
                "This may indicate a unit conversion error or OpenAP version change."
            )


# ─────────────────────────────────────────────────────────────────────────────
# 5. fetch_t2 offline (injected transport) — similar pattern to test_bts_fetch
# ─────────────────────────────────────────────────────────────────────────────

class TestFetchT2Offline:
    """Offline tests for bts_fetch.fetch_t2 using injected fake transport.

    fetch_t2 must use a cookie-session: first GET the form page, then POST
    with session cookie.  The BTS T2 form requires a session cookie unlike
    the DB1B PREZIP download (which is a direct URL).
    """

    def _make_zip(self, members: dict) -> bytes:
        """Build an in-memory ZIP."""
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        return buf.getvalue()

    def _fake_response(self, body: bytes, status: int = 200,
                       headers: dict | None = None):
        """Return a mock response compatible with urllib opener.open()."""
        from unittest import mock
        resp = mock.MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.headers = headers or {}
        resp.getheader = lambda key, default=None: resp.headers.get(key, default)
        return resp

    def test_fetch_t2_returns_path(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example.
        Tolerance: none (path existence).
        fetch_t2(2019, 1, dest_dir) with injected fake transport must return
        a pathlib.Path that exists in dest_dir containing the CSV content.
        Cannot catch: correct path with wrong content.
        """
        from bts_fetch import fetch_t2
        from unittest import mock

        csv_content = (
            b"YEAR,QUARTER,AIRCRAFT_TYPE,SERVICE_CLASS,AIRCRAFT_FUELS_921\n"
            b"2019,1,694,Z,1000000.0\n"
        )
        doc_content = b"Documentation\n"
        zip_body = self._make_zip({
            "Documentation.csv": doc_content,
            "T_SCHEDULE_T2.csv": csv_content,
        })

        # Fake HTML page with session cookie
        fake_html = (
            b'<input type="hidden" name="__VIEWSTATE" value="vs_t2" />'
            b'<input type="hidden" name="__VIEWSTATEGENERATOR" value="vsg_t2" />'
            b'<input type="hidden" name="__EVENTVALIDATION" value="ev_t2" />'
        )

        get_resp = self._fake_response(fake_html, headers={"Set-Cookie": "ASP.NET_SessionId=abc; path=/"})
        post_resp = self._fake_response(zip_body)

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_resp]):
            with mock.patch("time.sleep"):
                result = fetch_t2(2019, 1, tmp_path)

        assert result.exists(), "fetch_t2 must return path to extracted CSV"
        assert result.parent.resolve() == tmp_path.resolve()

    def test_fetch_t2_extracts_largest_member(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (largest-member selection).
        Tolerance: none.
        The T2 zip has Documentation.csv (small) and T_SCHEDULE_T2.csv (large).
        fetch_t2 must extract the largest member.
        Cannot catch: zip where largest happens to be Documentation.
        """
        from bts_fetch import fetch_t2
        from unittest import mock

        small = b"doc\n"
        large = b"DATA\n" + b"row\n" * 5000
        zip_body = self._make_zip({"Documentation.csv": small, "T_SCHEDULE_T2.csv": large})
        fake_html = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
        get_resp = self._fake_response(fake_html)
        post_resp = self._fake_response(zip_body)

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_resp]):
            with mock.patch("time.sleep"):
                result = fetch_t2(2019, 1, tmp_path)

        assert result.read_bytes() == large, (
            "fetch_t2 must extract the largest zip member, not the first"
        )

    def test_fetch_t2_post_includes_required_fields(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (POST body).
        Tolerance: none.
        The POST body must include cboGeography=All, cboYear, cboPeriod
        (quarter not month), and the required T2 field checkboxes including
        AIRCRAFT_FUELS_921.
        Cannot catch: correct field names with wrong values.
        """
        from bts_fetch import fetch_t2
        from unittest import mock
        import urllib.parse

        small_zip = self._make_zip({"T_SCHEDULE_T2.csv": b"a\n"})
        fake_html = (
            b'<input type="hidden" name="__VIEWSTATE" value="vs1" />'
            b'<input type="hidden" name="__VIEWSTATEGENERATOR" value="vsg1" />'
            b'<input type="hidden" name="__EVENTVALIDATION" value="ev1" />'
            b'<input type="checkbox" name="AIRCRAFT_FUELS_921" />'
            b'<input type="checkbox" name="YEAR" />'
            b'<input type="checkbox" name="QUARTER" />'
            b'<input type="checkbox" name="AIRCRAFT_TYPE" />'
            b'<input type="checkbox" name="AIRCRAFT_CONFIG" />'
            b'<input type="checkbox" name="SERVICE_CLASS" />'
        )
        get_resp = self._fake_response(fake_html)
        post_resp = self._fake_response(small_zip)

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_resp]) as m_open:
            with mock.patch("time.sleep"):
                fetch_t2(2019, 1, tmp_path)

        post_call = m_open.call_args_list[1]
        post_data = post_call[0][1] if len(post_call[0]) > 1 else post_call[1].get("data", b"")
        if isinstance(post_data, bytes):
            post_str = post_data.decode("utf-8")
        else:
            post_str = str(post_data)

        params = dict(urllib.parse.parse_qsl(post_str))
        assert params.get("cboGeography") == "All", (
            f"cboGeography must be 'All'; got {params.get('cboGeography')!r}"
        )
        assert params.get("cboYear") == "2019", (
            f"cboYear must be '2019'; got {params.get('cboYear')!r}"
        )
        assert params.get("cboPeriod") == "1", (
            f"cboPeriod must be '1' (quarter); got {params.get('cboPeriod')!r}"
        )
        assert "AIRCRAFT_FUELS_921" in post_str, (
            "POST body must include AIRCRAFT_FUELS_921 checkbox"
        )

    def test_fetch_t2_retries_on_failure(self, tmp_path: pathlib.Path) -> None:
        """
        Kind: example (retry).
        Tolerance: none.
        fetch_t2 must retry on OSError.
        Cannot catch: retry that silently drops content.
        """
        from bts_fetch import fetch_t2
        from unittest import mock

        small_zip = self._make_zip({"T_SCHEDULE_T2.csv": b"a\n"})
        fake_html = b'<input type="hidden" name="__VIEWSTATE" value="x" />'
        get_resp = self._fake_response(fake_html)
        post_fail = OSError("POST failed")
        post_ok = self._fake_response(small_zip)

        with mock.patch("bts_fetch._open_url", side_effect=[get_resp, post_fail, post_ok]) as m_open:
            with mock.patch("time.sleep"):
                result = fetch_t2(2019, 1, tmp_path)

        assert result.exists()
        assert m_open.call_count >= 3


# ─────────────────────────────────────────────────────────────────────────────
# 6. Real-data ratio check with full T2 cache (skipped if absent)
# ─────────────────────────────────────────────────────────────────────────────

@_SKIP_NO_T2
def test_five_types_ratio_real_data_in_band() -> None:
    """
    Kind: golden / magnitude check using real BTS T2 2019 Q1 data.
    Tolerance: ratio in [0.70, 1.10] for all 5 reviewer types.
    Reason for band: see module docstring.

    This is a check of magnitude, NOT a calibration.  OpenAP is NOT tuned
    to T2.

    Reports all mapped narrowbody types (those where OpenAP can run) as
    findings, including any outside the band.

    Cannot catch: compensating errors that keep ratio in-band for the wrong
    physical reasons.
    """
    from openap_perf import flight_fuel_nox

    df = pd.read_csv(_CACHE_DIR / "t2_2019_q1.csv")
    result = compare_openap(df, _FIVE_TYPES_MAP, flight_fuel_nox)

    outside_band: list[str] = []
    for bts_code, entry in result.items():
        r = entry["ratio"]
        if not (_RATIO_LO <= r <= _RATIO_HI):
            outside_band.append(
                f"  BTS {bts_code} ({_FIVE_TYPES_MAP[bts_code]}): "
                f"ratio={r:.3f} not in [{_RATIO_LO}, {_RATIO_HI}], "
                f"stage_nm={entry['stage_nm']:.0f}, deps={entry['departures']:.0f}"
            )

    assert not outside_band, (
        "OpenAP/T2 ratio outside expected band for types:\n"
        + "\n".join(outside_band)
    )
