"""
test_fuel_gap.py — Tests for fuel_gap.py (NW-26).

Tests run entirely offline with committed fixtures and injected fake perf.
Real-data tests skip when data/bts-cache is absent.

Test kinds used:
  - property:       decomposition identity (product == ratio, rel 1e-9)
  - example:        control run on synthetic T-100 (known answer)
  - metamorphic:    factor sensitivity (doubling demand doubles f1 only)
  - differential:   control vs real: gap is demand coverage, not model
  - golden (skip):  real-data numbers vs committed baselines (needs cache)
"""
from __future__ import annotations

import json
import math
import os
import pathlib
import sys
import tempfile
from typing import Dict, Optional

import pytest
import pandas as pd

# ── paths ────────────────────────────────────────────────────────────────────
PROJECT_SRC = pathlib.Path(__file__).resolve().parent.parent / "src"
FIXTURE_BTS = pathlib.Path(__file__).resolve().parent / "fixtures" / "bts"
CACHE_DIR   = pathlib.Path(__file__).resolve().parent.parent.parent / "data" / "bts-cache"

HAS_CACHE = (CACHE_DIR / "demand_region_2019Q1.csv").exists()

sys.path.insert(0, str(PROJECT_SRC))

# ── helpers ──────────────────────────────────────────────────────────────────

SM_TO_NM = 1.0 / 1.150779  # from t2_check._SM_TO_NM

# Fixed haversine-equivalent distance for synthetic airports (km → nm)
_SYNTH_DIST_NM = 400.0  # arbitrary positive value for synthetic airport pairs


def _fake_distance_fn(origin: str, dest: str) -> float:
    """Return a fixed distance for any (origin, dest) pair (tests with synthetic airports)."""
    if origin == dest:
        return 0.0
    return _SYNTH_DIST_NM


def _fake_perf(fuel_per_nm: float = 7.0, nox_per_nm: float = 0.1):
    """Return a perf function that returns fuel_per_nm × distance_nm per flight."""
    from fleet import BTS_TO_OPENAP

    def _perf(bts_code: str, dist_nm: float) -> Optional[Dict]:
        openap = BTS_TO_OPENAP.get(bts_code)
        if openap is None:
            return None
        return {"fuel_kg": fuel_per_nm * dist_nm, "nox_kg": nox_per_nm * dist_nm}

    return _perf


# ── SYNTHETIC T-100 helpers ──────────────────────────────────────────────────

def _make_synthetic_t100(
    *,
    origin: str = "AAA",
    dest: str = "BBB",
    departures: float = 10.0,
    passengers: float = 800.0,  # 80 pax/flight × 10 flights
    seats: float = 1500.0,      # 150 seats/flight × 10
    distance_sm: float = 500.0,  # statute miles
    aircraft_type: str = "614",  # b738
    year: int = 2019,
    month: int = 1,
) -> pd.DataFrame:
    """Minimal T-100 DataFrame for one segment, one aircraft type."""
    return pd.DataFrame([{
        "DEPARTURES_PERFORMED": departures,
        "SEATS": seats,
        "PASSENGERS": passengers,
        "DISTANCE": distance_sm,
        "AIR_TIME": departures * 60.0,
        "UNIQUE_CARRIER": "XX",
        "ORIGIN": origin,
        "DEST": dest,
        "AIRCRAFT_TYPE": aircraft_type,
        "AIRCRAFT_CONFIG": 1,
        "YEAR": year,
        "MONTH": month,
    }])


def _make_synthetic_t2(
    *,
    aircraft_type: str = "614",  # b738
    fuel_gal: float = 1_000_000.0,
    miles_sm: float = 100_000.0,  # statute miles
    departures: float = 100.0,
) -> pd.DataFrame:
    """Minimal T2 DataFrame for one aircraft type (SERVICE_CLASS=='Z')."""
    return pd.DataFrame([{
        "YEAR": 2019,
        "QUARTER": 1,
        "UNIQUE_CARRIER": "XX",
        "CARRIER_GROUP_NEW": 3,
        "AIRCRAFT_TYPE": aircraft_type,
        "AIRCRAFT_CONFIG": 1,
        "SERVICE_CLASS": "Z",
        "AVL_SEAT_MILES_320": 0.0,
        "REV_PAX_MILES_140": 0.0,
        "REV_ACRFT_MILES_FLOWN_410": float(miles_sm),
        "REV_ACRFT_DEP_PERF_510": float(departures),
        "HOURS_AIRBORNE_650": departures * 1.5,
        "AIRCRAFT_FUELS_921": float(fuel_gal),
        "NUM_MONTHS": 3,
    }])


# ═══════════════════════════════════════════════════════════════════════════
# PROPERTY: decomposition identity  product == ratio to rel 1e-9
# ═══════════════════════════════════════════════════════════════════════════

class TestDecompositionIdentity:
    """PROPERTY: f1 × f2 × f3 × f4 == fuel_model/fuel_reference to rel 1e-9.

    Tolerance: 1e-9 relative. All factors are exact floating-point arithmetic
    from the same data; the identity is algebraic, not approximate.
    Cannot catch: bugs that cancel out (e.g., wrong numerator AND denominator
    inverted); sign errors that still yield positive ratios.
    """

    def test_product_equals_total_ratio_synthetic(self):
        """Property: 4-factor product equals model/reference ratio (synthetic T-100, rel 1e-9).

        KIND: property
        TOLERANCE: rel 1e-9 (algebraic identity — should be exact float)
        CANNOT CATCH: simultaneous numerator/denominator swaps that cancel
        """
        from fuel_gap import compute_decomposition

        # One segment: AAA→BBB, 10 deps, 800 pax, 150 seats, 500 sm
        t100_df = _make_synthetic_t100(
            origin="AAA", dest="BBB",
            departures=10.0, passengers=800.0, seats=1500.0,
            distance_sm=500.0, aircraft_type="614",
        )
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=500_000.0, miles_sm=50_000.0, departures=50.0,
        )
        # Demand: same segment, 600 pax (less than T-100 800 → pax_coverage < 1)
        demand_df = pd.DataFrame([{"origin": "AAA", "dest": "BBB", "passengers": 600.0}])

        perf_fn = _fake_perf(fuel_per_nm=7.0)

        result = compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=["AAA", "BBB"],
            perf_fn=perf_fn,
            distance_fn=_fake_distance_fn,
        )

        product = (
            result["f1_pax_coverage"]
            * result["f2_flights_per_pax"]
            * result["f3_nm_per_flight"]
            * result["f4_kg_per_nm"]
        )
        ratio = result["fuel_model"] / result["fuel_reference"]

        assert abs(product - ratio) / (ratio + 1e-30) < 1e-9, (
            f"Decomposition product {product:.9g} != ratio {ratio:.9g} "
            f"(rel diff {abs(product-ratio)/ratio:.2e})"
        )

    def test_product_equals_total_ratio_fixture(self):
        """Property: 4-factor decomposition identity on fixture T-100 sample (rel 1e-9).

        KIND: property
        TOLERANCE: rel 1e-9
        CANNOT CATCH: bugs that cancel across factors
        """
        from fuel_gap import compute_decomposition

        t100_df = pd.read_csv(FIXTURE_BTS / "t100_segment_sample.csv", dtype=str)
        t2_df   = pd.read_csv(FIXTURE_BTS / "t2_q1_2019_sample.csv")

        # Use only airports present in the fixture
        airports_in_fixture = ["BOS", "DEN", "DTW", "MSP", "ORD", "SFO"]

        # Build a tiny demand from fixture T-100 passengers
        demand_records = []
        for col in ("DEPARTURES_PERFORMED", "SEATS", "PASSENGERS"):
            t100_df[col] = pd.to_numeric(t100_df[col], errors="coerce")
        t100_df["AIRCRAFT_CONFIG"] = pd.to_numeric(t100_df["AIRCRAFT_CONFIG"], errors="coerce")
        t100_df["DEPARTURES_PERFORMED"] = pd.to_numeric(t100_df["DEPARTURES_PERFORMED"], errors="coerce")

        region = t100_df[
            t100_df["ORIGIN"].isin(airports_in_fixture)
            & t100_df["DEST"].isin(airports_in_fixture)
            & (t100_df["DEPARTURES_PERFORMED"] > 0)
            & (t100_df["AIRCRAFT_CONFIG"] == 1)
        ]
        seg_pax = region.groupby(["ORIGIN", "DEST"])["PASSENGERS"].sum().reset_index()
        # Use half the T-100 passengers to get pax_coverage != 1
        demand_df = pd.DataFrame({
            "origin": seg_pax["ORIGIN"],
            "dest": seg_pax["DEST"],
            "passengers": seg_pax["PASSENGERS"] * 0.5,
        })

        perf_fn = _fake_perf(fuel_per_nm=7.0)

        result = compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=airports_in_fixture,
            perf_fn=perf_fn,
        )

        product = (
            result["f1_pax_coverage"]
            * result["f2_flights_per_pax"]
            * result["f3_nm_per_flight"]
            * result["f4_kg_per_nm"]
        )
        ratio = result["fuel_model"] / result["fuel_reference"]

        assert abs(product - ratio) / (ratio + 1e-30) < 1e-9, (
            f"Fixture decomposition identity failed: product={product:.9g}, ratio={ratio:.9g}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# EXAMPLE: control run on synthetic T-100 (known answer)
# ═══════════════════════════════════════════════════════════════════════════

class TestControlRun:
    """EXAMPLE: Control run where T-100 passengers become demand.

    With one aircraft type, one segment, LF 1 (all seats filled → flights=pax/seats×LF),
    the control run should reproduce T-100 flights (departures) exactly.

    KIND: example (constructed with known answer)
    TOLERANCE: rel 1e-9 for flights (algebraic); rel 0.005 for fuel (rounding of
      nm distance: haversine vs statute-mile gives <0.5% diff for these airports)
    CANNOT CATCH: bugs where model flights agree but for the wrong reason
    """

    def test_control_flights_match_t100(self):
        """Example: control run reproduces T-100 departures exactly (load_factor × seats arithmetic).

        When T-100 passengers == demand, the fluid flight model gives:
          flights = pax / (seats_per_dep × load_factor) = pax / (seats/dep × pax/seats) = dep
        So flights == departures exactly.

        KIND: example
        TOLERANCE: rel 1e-9 (algebraic identity)
        CANNOT CATCH: wrong normalisation that still gives same total
        """
        from fuel_gap import run_control

        deps = 20.0
        pax = 1600.0   # 80 pax/flight
        seats = 3000.0  # 150 seats/flight
        dist_sm = 600.0

        t100_df = _make_synthetic_t100(
            origin="AAA", dest="BBB",
            departures=deps, passengers=pax, seats=seats,
            distance_sm=dist_sm, aircraft_type="614",
        )

        perf_fn = _fake_perf(fuel_per_nm=7.0)

        ctrl = run_control(
            t100_df=t100_df,
            airports=["AAA", "BBB"],
            perf_fn=perf_fn,
            distance_fn=_fake_distance_fn,
        )

        assert abs(ctrl["model_flights"] - deps) / deps < 1e-9, (
            f"Control flights {ctrl['model_flights']} != T-100 deps {deps}"
        )

    def test_control_aircraft_nm_close_to_t100(self):
        """Example: control aircraft-NM is within 2% of T-100 (haversine vs statute-mile).

        The model uses haversine distances; T-100 uses the DISTANCE (statute-mile) field.
        For BOS→ORD (~867 sm), haversine and great-circle agree within 0.5%.

        KIND: example
        TOLERANCE: rel 0.02 (2% allows for projection differences between
          BTS statute-mile DISTANCE and haversine great-circle)
        CANNOT CATCH: systematic distance bias of exactly 2%
        """
        from fuel_gap import run_control

        # Use real airport pair so haversine distance is meaningful
        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=100.0, passengers=8000.0, seats=15000.0,
            distance_sm=867.0, aircraft_type="614",
        )

        perf_fn = _fake_perf(fuel_per_nm=7.0)

        ctrl = run_control(
            t100_df=t100_df,
            airports=["BOS", "ORD"],
            perf_fn=perf_fn,
        )

        t100_nm = 100.0 * 867.0 * SM_TO_NM  # deps × dist_sm × SM_TO_NM
        rel = abs(ctrl["model_aircraft_nm"] - t100_nm) / t100_nm

        assert rel < 0.02, (
            f"Control aircraft-NM {ctrl['model_aircraft_nm']:.0f} "
            f"differs from T-100 {t100_nm:.0f} by {rel:.3%} > 2%"
        )

    def test_control_fuel_ratio_structure(self):
        """Example: control fuel ratio dict has required keys and finite values.

        KIND: example
        TOLERANCE: n/a (structural)
        CANNOT CATCH: wrong values that still pass structural check
        """
        from fuel_gap import run_control

        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=50.0, passengers=4000.0, seats=7500.0,
            distance_sm=867.0, aircraft_type="614",
        )
        perf_fn = _fake_perf(fuel_per_nm=7.0)
        ctrl = run_control(t100_df=t100_df, airports=["BOS", "ORD"], perf_fn=perf_fn)

        required_keys = {
            "model_flights", "t100_departures",
            "model_aircraft_nm", "t100_aircraft_nm",
            "model_fuel_kg", "unmapped_share",
            "flight_ratio", "nm_ratio",
        }
        assert required_keys.issubset(ctrl.keys()), (
            f"Missing keys: {required_keys - ctrl.keys()}"
        )
        for k in ("model_flights", "model_aircraft_nm", "model_fuel_kg"):
            assert math.isfinite(ctrl[k]) and ctrl[k] >= 0, f"{k}={ctrl[k]} is not finite non-negative"


# ═══════════════════════════════════════════════════════════════════════════
# METAMORPHIC: factor sensitivity
# ═══════════════════════════════════════════════════════════════════════════

class TestFactorSensitivity:
    """METAMORPHIC: doubling demand doubles f1 only; other factors unchanged.

    KIND: metamorphic
    TOLERANCE: rel 1e-9 (f1 doubles exactly); 1e-9 for other factors (unchanged)
    CANNOT CATCH: bugs that change multiple factors proportionally
    """

    def _base_result(self, pax_scale: float = 1.0):
        from fuel_gap import compute_decomposition

        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=100.0, passengers=8000.0, seats=15000.0,
            distance_sm=867.0, aircraft_type="614",
        )
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=1_000_000.0, miles_sm=87_000.0, departures=100.0,
        )
        t100_pax = 8000.0
        demand_pax = 4000.0 * pax_scale
        demand_df = pd.DataFrame([{
            "origin": "BOS", "dest": "ORD", "passengers": demand_pax,
        }])
        perf_fn = _fake_perf(fuel_per_nm=7.0)
        return compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=["BOS", "ORD"],
            perf_fn=perf_fn,
        )

    def test_doubling_demand_doubles_f1(self):
        """Metamorphic: doubling demand pax doubles f1_pax_coverage.

        KIND: metamorphic
        TOLERANCE: rel 1e-9
        CANNOT CATCH: if pax_coverage factor is a constant (always returns same value)
        """
        r1 = self._base_result(pax_scale=1.0)
        r2 = self._base_result(pax_scale=2.0)
        ratio = r2["f1_pax_coverage"] / r1["f1_pax_coverage"]
        assert abs(ratio - 2.0) / 2.0 < 1e-9, (
            f"Doubling demand: f1 ratio = {ratio:.9g}, expected 2.0"
        )

    def test_doubling_demand_does_not_change_f2(self):
        """Metamorphic: doubling demand pax does not change f2_flights_per_pax.

        f2 = (model_flights/demand_pax) / (t100_deps/t100_pax).
        Both numerator and denominator scale by pax_scale, so f2 is invariant.

        KIND: metamorphic
        TOLERANCE: rel 1e-9
        CANNOT CATCH: if f2 is always 1.0 regardless of input
        """
        r1 = self._base_result(pax_scale=1.0)
        r2 = self._base_result(pax_scale=2.0)
        assert abs(r1["f2_flights_per_pax"] - r2["f2_flights_per_pax"]) / (r1["f2_flights_per_pax"] + 1e-30) < 1e-9, (
            f"f2 changed on demand doubling: {r1['f2_flights_per_pax']} vs {r2['f2_flights_per_pax']}"
        )

    def test_doubling_demand_does_not_change_f3(self):
        """Metamorphic: doubling demand pax does not change f3_nm_per_flight.

        f3 = (model_nm/model_flights) / (t100_nm/t100_deps).
        model_nm ∝ model_flights ∝ demand_pax, so model_nm/model_flights is invariant.

        KIND: metamorphic
        TOLERANCE: rel 1e-9
        CANNOT CATCH: if f3 is always 1.0 regardless of input
        """
        r1 = self._base_result(pax_scale=1.0)
        r2 = self._base_result(pax_scale=2.0)
        assert abs(r1["f3_nm_per_flight"] - r2["f3_nm_per_flight"]) / (r1["f3_nm_per_flight"] + 1e-30) < 1e-9, (
            f"f3 changed on demand doubling: {r1['f3_nm_per_flight']} vs {r2['f3_nm_per_flight']}"
        )

    def test_doubling_demand_does_not_change_f4(self):
        """Metamorphic: doubling demand pax does not change f4_kg_per_nm.

        f4 = (model_fuel/model_nm) / t2_kg_per_nm.
        model_fuel ∝ model_nm ∝ demand_pax, so the ratio is invariant.

        KIND: metamorphic
        TOLERANCE: rel 1e-9
        CANNOT CATCH: if f4 is always 1.0 regardless of input
        """
        r1 = self._base_result(pax_scale=1.0)
        r2 = self._base_result(pax_scale=2.0)
        assert abs(r1["f4_kg_per_nm"] - r2["f4_kg_per_nm"]) / (r1["f4_kg_per_nm"] + 1e-30) < 1e-9, (
            f"f4 changed on demand doubling: {r1['f4_kg_per_nm']} vs {r2['f4_kg_per_nm']}"
        )

    def test_doubling_demand_doubles_total_fuel_model(self):
        """Metamorphic: doubling demand pax doubles model fuel (linear scaling).

        KIND: metamorphic
        TOLERANCE: rel 1e-9
        CANNOT CATCH: offset terms that cancel on doubling
        """
        r1 = self._base_result(pax_scale=1.0)
        r2 = self._base_result(pax_scale=2.0)
        ratio = r2["fuel_model"] / r1["fuel_model"]
        assert abs(ratio - 2.0) / 2.0 < 1e-9, (
            f"Doubling demand: fuel_model ratio = {ratio:.9g}, expected 2.0"
        )


# ═══════════════════════════════════════════════════════════════════════════
# PROPERTY: JSON output structure
# ═══════════════════════════════════════════════════════════════════════════

class TestOutputStructure:
    """PROPERTY: fuel_gap module --out writes valid JSON with required keys.

    KIND: property (structural invariant)
    TOLERANCE: n/a
    CANNOT CATCH: JSON with correct keys but wrong values
    """

    def test_json_output_has_required_keys(self):
        """Property: fuel_gap writes gap_analysis.json with all required keys.

        KIND: property
        TOLERANCE: n/a (structural)
        CANNOT CATCH: correct keys, wrong computation
        """
        from fuel_gap import compute_decomposition, write_report

        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=100.0, passengers=8000.0, seats=15000.0,
            distance_sm=867.0, aircraft_type="614",
        )
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=1_000_000.0, miles_sm=87_000.0, departures=100.0,
        )
        demand_df = pd.DataFrame([{"origin": "BOS", "dest": "ORD", "passengers": 4000.0}])
        perf_fn = _fake_perf(fuel_per_nm=7.0)

        decomp = compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=["BOS", "ORD"],
            perf_fn=perf_fn,
        )
        ctrl = {"model_flights": 100.0, "t100_departures": 100.0,
                "model_aircraft_nm": 70000.0, "t100_aircraft_nm": 70100.0,
                "model_fuel_kg": 490000.0, "unmapped_share": 0.0,
                "flight_ratio": 1.0, "nm_ratio": 0.999}

        with tempfile.TemporaryDirectory() as tmpdir:
            write_report(
                out_dir=pathlib.Path(tmpdir),
                decomposition=decomp,
                control=ctrl,
            )
            gap_json = pathlib.Path(tmpdir) / "gap_analysis.json"
            assert gap_json.exists(), "gap_analysis.json not written"
            data = json.loads(gap_json.read_text())

        required_keys = {
            "factors", "product_check", "total_ratio",
            "product_error_rel", "control", "explained_factors",
            "unexplained_factors",
        }
        assert required_keys.issubset(data.keys()), (
            f"Missing JSON keys: {required_keys - data.keys()}"
        )

        factor_keys = {"f1_pax_coverage", "f2_flights_per_pax",
                       "f3_nm_per_flight", "f4_kg_per_nm"}
        assert factor_keys.issubset(data["factors"].keys()), (
            f"Missing factor keys: {factor_keys - data['factors'].keys()}"
        )

    def test_json_product_check_matches_total_ratio(self):
        """Property: JSON product_check matches total_ratio within rel 1e-9.

        KIND: property
        TOLERANCE: rel 1e-9
        CANNOT CATCH: consistent wrong values for both product and ratio
        """
        from fuel_gap import compute_decomposition, write_report

        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=100.0, passengers=8000.0, seats=15000.0,
            distance_sm=867.0, aircraft_type="614",
        )
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=1_000_000.0, miles_sm=87_000.0, departures=100.0,
        )
        demand_df = pd.DataFrame([{"origin": "BOS", "dest": "ORD", "passengers": 3500.0}])
        perf_fn = _fake_perf(fuel_per_nm=6.5)

        decomp = compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=["BOS", "ORD"],
            perf_fn=perf_fn,
        )
        ctrl = {"model_flights": 90.0, "t100_departures": 100.0,
                "model_aircraft_nm": 63000.0, "t100_aircraft_nm": 70100.0,
                "model_fuel_kg": 409500.0, "unmapped_share": 0.0,
                "flight_ratio": 0.9, "nm_ratio": 0.899}

        with tempfile.TemporaryDirectory() as tmpdir:
            write_report(
                out_dir=pathlib.Path(tmpdir),
                decomposition=decomp,
                control=ctrl,
            )
            data = json.loads((pathlib.Path(tmpdir) / "gap_analysis.json").read_text())

        assert abs(data["product_error_rel"]) < 1e-9, (
            f"product_error_rel = {data['product_error_rel']:.2e} > 1e-9"
        )


# ═══════════════════════════════════════════════════════════════════════════
# PROPERTY: statute→nautical conversion is applied
# ═══════════════════════════════════════════════════════════════════════════

class TestStatuteMileConversion:
    """PROPERTY: T-100 aircraft-NM uses SM→NM conversion factor.

    KIND: property
    TOLERANCE: rel 1e-9 (exact arithmetic check)
    CANNOT CATCH: if the test fixture uses equal statute and nautical miles by coincidence
    """

    def test_t100_aircraft_nm_uses_sm_to_nm(self):
        """Property: compute_decomposition converts T-100 DISTANCE (statute mi) to nautical mi.

        The reference fuel = t100_aircraft_nm × t2_kg_per_nm.
        t100_aircraft_nm = departures × distance_sm × SM_TO_NM.
        If SM_TO_NM is incorrectly 1.0, the reference fuel would be 1.150779× larger.

        KIND: property
        TOLERANCE: rel 1e-9
        CANNOT CATCH: consistent wrong values that still multiply correctly
        """
        from fuel_gap import compute_decomposition
        from t2_check import _SM_TO_NM as SM_TO_NM_EXPECTED

        deps = 10.0
        dist_sm = 1000.0
        pax = 800.0
        seats = 1500.0

        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=deps, passengers=pax, seats=seats,
            distance_sm=dist_sm, aircraft_type="614",
        )
        # Set fuel and miles to force a known kg/nm
        fuel_gal = 100_000.0
        miles_sm = 10_000.0
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=fuel_gal, miles_sm=miles_sm, departures=50.0,
        )
        demand_df = pd.DataFrame([{"origin": "BOS", "dest": "ORD", "passengers": 400.0}])
        perf_fn = _fake_perf(fuel_per_nm=7.0)

        result = compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=["BOS", "ORD"],
            perf_fn=perf_fn,
        )

        # Expected: t100_aircraft_nm = deps × dist_sm × SM_TO_NM
        expected_nm = deps * dist_sm * SM_TO_NM_EXPECTED
        got_nm = result["t100_aircraft_nm"]

        assert abs(got_nm - expected_nm) / expected_nm < 1e-9, (
            f"t100_aircraft_nm = {got_nm:.4f}, expected {expected_nm:.4f} "
            f"(using SM_TO_NM = {SM_TO_NM_EXPECTED:.6f})"
        )


# ═══════════════════════════════════════════════════════════════════════════
# PROPERTY: factor f1 definition (demand pax / t100 pax)
# ═══════════════════════════════════════════════════════════════════════════

class TestFactorDefinitions:
    """PROPERTY: each factor has the correct definition.

    KIND: property
    TOLERANCE: rel 1e-9
    CANNOT CATCH: compensating bugs across factors
    """

    def test_f1_is_demand_over_t100_pax(self):
        """Property: f1 = demand_pax / t100_pax exactly.

        KIND: property
        TOLERANCE: rel 1e-9
        CANNOT CATCH: if f1 is computed from a variable correlated with pax ratio
        """
        from fuel_gap import compute_decomposition

        t100_pax = 8000.0
        demand_pax = 5000.0
        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=100.0, passengers=t100_pax, seats=15000.0,
            distance_sm=867.0, aircraft_type="614",
        )
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=1_000_000.0, miles_sm=87_000.0, departures=100.0,
        )
        demand_df = pd.DataFrame([{"origin": "BOS", "dest": "ORD", "passengers": demand_pax}])
        perf_fn = _fake_perf(fuel_per_nm=7.0)

        result = compute_decomposition(
            demand_df=demand_df, t100_df=t100_df, t2_df=t2_df,
            airports=["BOS", "ORD"], perf_fn=perf_fn,
        )
        expected_f1 = demand_pax / t100_pax
        assert abs(result["f1_pax_coverage"] - expected_f1) / expected_f1 < 1e-9, (
            f"f1 = {result['f1_pax_coverage']:.9g}, expected {expected_f1:.9g}"
        )

    def test_reference_fuel_uses_sm_to_nm_conversion(self):
        """Property: fuel_reference = t100_aircraft_nm × t2_kg_per_nm, where NM uses SM_TO_NM.

        KIND: property
        TOLERANCE: rel 1e-9
        CANNOT CATCH: if reference_fuel is computed via a different but equivalent formula
        """
        from fuel_gap import compute_decomposition
        from t2_check import _GAL_TO_L, _JET_A_KG_PER_L, _SM_TO_NM as SM_TO_NM_EXPECTED

        deps = 10.0
        dist_sm = 500.0
        pax = 800.0
        seats = 1500.0
        fuel_gal = 200_000.0
        miles_sm_t2 = 20_000.0

        t100_df = _make_synthetic_t100(
            origin="BOS", dest="ORD",
            departures=deps, passengers=pax, seats=seats,
            distance_sm=dist_sm, aircraft_type="614",
        )
        t2_df = _make_synthetic_t2(
            aircraft_type="614",
            fuel_gal=fuel_gal, miles_sm=miles_sm_t2, departures=20.0,
        )
        demand_df = pd.DataFrame([{"origin": "BOS", "dest": "ORD", "passengers": 400.0}])
        perf_fn = _fake_perf(fuel_per_nm=7.0)

        result = compute_decomposition(
            demand_df=demand_df, t100_df=t100_df, t2_df=t2_df,
            airports=["BOS", "ORD"], perf_fn=perf_fn,
        )

        # Expected reference
        t100_nm_expected = deps * dist_sm * SM_TO_NM_EXPECTED
        t2_kg = fuel_gal * _GAL_TO_L * _JET_A_KG_PER_L
        t2_nm = miles_sm_t2 * SM_TO_NM_EXPECTED
        t2_kg_per_nm = t2_kg / t2_nm
        expected_ref = t100_nm_expected * t2_kg_per_nm

        assert abs(result["fuel_reference"] - expected_ref) / expected_ref < 1e-9, (
            f"fuel_reference = {result['fuel_reference']:.4f}, expected {expected_ref:.4f}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# REAL-DATA TESTS (skip without cache)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(not HAS_CACHE, reason="data/bts-cache/demand_region_2019Q1.csv absent (CI)")
class TestRealData:
    """Real-data tests for NW-26 fuel gap analysis.

    All skip without the BTS cache (CI-safe). Uses real OpenAP perf (cached).

    KIND: golden (real numbers compared to expected ranges)
    TOLERANCE: stated per assertion
    CANNOT CATCH: systematic errors that move all real numbers in the same direction
    """

    @pytest.fixture(scope="class")
    def real_result(self):
        """Compute decomposition from real cache data (once per class)."""
        from fuel_gap import compute_decomposition, build_perf_cache

        t100_paths = [
            str(CACHE_DIR / "t100_2019_01.csv"),
            str(CACHE_DIR / "t100_2019_02.csv"),
            str(CACHE_DIR / "t100_2019_03.csv"),
        ]
        demand_path = str(CACHE_DIR / "demand_region_2019Q1.csv")
        t2_path = str(CACHE_DIR / "t2_2019_q1.csv")
        airports = ["BOS", "DEN", "DTW", "MSP", "ORD", "SFO"]

        demand_df = pd.read_csv(demand_path)
        t2_df = pd.read_csv(t2_path)
        frames = []
        for p in t100_paths:
            df = pd.read_csv(p)
            df["AIRCRAFT_TYPE"] = df["AIRCRAFT_TYPE"].apply(
                lambda x: str(int(float(x))).zfill(3) if pd.notna(x) else x
            )
            frames.append(df)
        t100_df = pd.concat(frames, ignore_index=True)

        perf_fn = build_perf_cache(t100_df=t100_df, airports=airports)

        return compute_decomposition(
            demand_df=demand_df,
            t100_df=t100_df,
            t2_df=t2_df,
            airports=airports,
            perf_fn=perf_fn,
        )

    def test_real_decomposition_identity(self, real_result):
        """Golden: real-data decomposition identity (rel 1e-9).

        KIND: golden + property
        TOLERANCE: rel 1e-9
        CANNOT CATCH: compensating bugs
        """
        r = real_result
        product = (
            r["f1_pax_coverage"]
            * r["f2_flights_per_pax"]
            * r["f3_nm_per_flight"]
            * r["f4_kg_per_nm"]
        )
        ratio = r["fuel_model"] / r["fuel_reference"]
        assert abs(product - ratio) / ratio < 1e-9

    def test_real_f1_pax_coverage_known_range(self, real_result):
        """Golden: f1 pax_coverage is in (0.55, 0.70) for this region/quarter.

        DB1B is a 10% ticket sample × 10 = ~full volume, but geographic scope
        differs from T-100: expected demand_pax / t100_pax ≈ 0.60–0.65.

        KIND: golden
        TOLERANCE: interval check (no arithmetic tolerance needed)
        CANNOT CATCH: systematic over/under-count of demand that stays in range
        """
        f1 = real_result["f1_pax_coverage"]
        assert 0.55 < f1 < 0.70, (
            f"f1_pax_coverage = {f1:.4f} outside expected range (0.55, 0.70)"
        )

    def test_real_model_fuel_known_range(self, real_result):
        """Golden: model P2P fuel is in (120_000, 160_000) tonnes for this scenario.

        The pipeline produces ~137,954 t; allow ±15% for model/data variation.

        KIND: golden
        TOLERANCE: ±15% of stated value
        CANNOT CATCH: consistent wrong answer within this range
        """
        fuel_t = real_result["fuel_model"] / 1_000.0
        assert 120_000 < fuel_t < 160_000, (
            f"model fuel = {fuel_t:.0f} t outside (120k, 160k) range"
        )

    def test_real_t100_aircraft_nm_known_range(self, real_result):
        """Golden: T-100 aircraft-NM is in (25M, 32M) nm for this 6-airport region.

        From the task: 28.0M aircraft-nm.

        KIND: golden
        TOLERANCE: ±14%
        CANNOT CATCH: consistent wrong answer within this range
        """
        nm = real_result["t100_aircraft_nm"]
        assert 25e6 < nm < 32e6, (
            f"t100_aircraft_nm = {nm/1e6:.2f}M outside (25M, 32M)"
        )

    def test_real_control_flights_match_t100(self):
        """Golden: control run reproduces T-100 departures within 0.1%.

        KIND: golden (differential)
        TOLERANCE: 0.1% (load-factor arithmetic; exact match expected by construction)
        CANNOT CATCH: systematic offset equal to 0.1% of total
        """
        from fuel_gap import run_control, build_perf_cache

        t100_paths = [
            str(CACHE_DIR / "t100_2019_01.csv"),
            str(CACHE_DIR / "t100_2019_02.csv"),
            str(CACHE_DIR / "t100_2019_03.csv"),
        ]
        airports = ["BOS", "DEN", "DTW", "MSP", "ORD", "SFO"]

        frames = []
        for p in t100_paths:
            df = pd.read_csv(p)
            df["AIRCRAFT_TYPE"] = df["AIRCRAFT_TYPE"].apply(
                lambda x: str(int(float(x))).zfill(3) if pd.notna(x) else x
            )
            frames.append(df)
        t100_df = pd.concat(frames, ignore_index=True)

        perf_fn = build_perf_cache(t100_df=t100_df, airports=airports)
        ctrl = run_control(t100_df=t100_df, airports=airports, perf_fn=perf_fn)

        rel = abs(ctrl["model_flights"] - ctrl["t100_departures"]) / ctrl["t100_departures"]
        assert rel < 1e-6, (
            f"Control flights {ctrl['model_flights']:.1f} != T-100 {ctrl['t100_departures']:.0f} "
            f"(rel diff {rel:.2e})"
        )
