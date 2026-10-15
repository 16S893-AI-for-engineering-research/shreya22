"""
test_openap_synonyms.py — Tests for synonym-aware flight_fuel_nox.

NW-20: make openap_perf.flight_fuel_nox run for every OpenAP model targeted
by fleet.BTS_TO_OPENAP, and report what was actually used.

Background
----------
The reviewer measured that FlightGenerator(ac) fails for 11 models and
FuelFlow(ac) fails for 7 models when called without use_synonym=True.  After
adding use_synonym=True to both constructors, all 22 BTS→OpenAP target models
run.

A "synonym" means OpenAP substitutes another aircraft's kinematic (WRAP) or
drag-polar model.  This is an approximation that must be visible in the output.

Synonym resolution tables (read from installed openap data, verified manually):
  WRAP synonyms relevant to our models:
    a20n → a320,  a21n → a320,  a359 → b789,  b38m → b737,  b39m → b737,
    b772 → b77w,  b773 → b77w,  crj9 → e190,  e145 → e190,  e170 → e190,
    e75l → e190
  Drag-polar synonyms relevant to our models:
    a21n → a20n,  b39m → b38m,  b763 → b752,  b773 → b77w,
    crj9 → e75l,  e145 → e75l,  e170 → e75l

Module-level cache
------------------
All heavy openap calls are computed once per (ac, distance_nm) pair and stored
in _CACHE.  Total runtime target: < 90 s (22 models × ~3 s each plus 3
baseline models already in test_openap_perf.py's cache — but that cache is
module-local, so we duplicate for the 3 baselines; total ≤ 22 × 4 s ≈ 88 s).

Failure mode
------------
Before the fix, every model that needed use_synonym=True raised either:
  ValueError: Kinematic model for <ac> not available.
  ValueError: Drag polar for <ac> not available. Try use_synonym=True.

The 'native_tried_first' tests use monkeypatching to confirm native
construction is attempted before synonym fallback.
"""
from __future__ import annotations

import math
import warnings
from typing import Dict, Optional, Tuple
from unittest import mock

import pytest

# Plain imports — collection ERROR if missing (test_no_silent_skips.py enforces
# this pattern; no try/except allowed at module level).
from fleet import BTS_TO_OPENAP
from openap_perf import flight_fuel_nox

# ---------------------------------------------------------------------------
# Compute the full set of distinct target OpenAP models.
# ---------------------------------------------------------------------------
_ALL_MODELS: list[str] = sorted(
    set(v for v in BTS_TO_OPENAP.values() if v is not None)
)
# NW-20 verified these 22 target models.  NW-21 (written in parallel, merged
# after) widened BTS_TO_OPENAP with 6 more exact/proxy targets
# (a332, a333, a343, b734, b744, b748), so the table now has 28.  We therefore
# assert the ORIGINAL 22 are still present (nothing was silently dropped) and
# let every other mapped model be exercised by the parametrised tests below,
# rather than pinning an exact count that goes stale whenever the mapping grows.
_NW20_MODELS = frozenset({
    "a20n", "a21n", "a319", "a320", "a321", "a359", "b38m", "b39m", "b737",
    "b738", "b739", "b752", "b763", "b772", "b773", "b788", "b789", "crj9",
    "e145", "e170", "e190", "e75l",
})
assert len(_NW20_MODELS) == 22
_missing = _NW20_MODELS - set(_ALL_MODELS)
assert not _missing, f"BTS_TO_OPENAP lost previously supported models: {sorted(_missing)}"

# ---------------------------------------------------------------------------
# Module-level cache: (ac, distance_nm) -> result dict.
# Populated lazily by _cached(); shared across all tests in this module.
# ---------------------------------------------------------------------------
_CACHE: Dict[Tuple[str, int], dict] = {}
_DISTANCE_NM = 800  # representative stage length for all models


def _cached(ac: str, distance_nm: int = _DISTANCE_NM) -> dict:
    """Return (cached) flight_fuel_nox result for (ac, distance_nm).

    Suppresses openap UserWarning about synonym use — they are tested
    explicitly in the synonym-content tests below.
    """
    key = (ac, distance_nm)
    if key not in _CACHE:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _CACHE[key] = flight_fuel_nox(ac, float(distance_nm))
    return _CACHE[key]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fuel_per_nm(r: dict) -> float:
    return r["fuel_kg"] / _DISTANCE_NM


# ===========================================================================
# PART 1 — New return-key structure tests
# (These test that the NEW keys are present; they will FAIL until the feature
#  is implemented.)
# ===========================================================================


class TestNewReturnKeys:
    """Verify that flight_fuel_nox returns the new synonym-tracking keys."""

    def test_trajectory_model_key_present(self) -> None:
        """
        Kind: example / structural.
        Tolerance: exact (key must exist).
        Reason: callers need to know which WRAP model was used to generate the
        trajectory; without this key the synonym is invisible.
        Cannot catch: key present but holding wrong value.
        """
        r = _cached("a320")
        assert "trajectory_model" in r, (
            "flight_fuel_nox result missing 'trajectory_model' key"
        )

    def test_fuel_model_key_present(self) -> None:
        """
        Kind: example / structural.
        Tolerance: exact.
        Reason: callers need to know which drag-polar model underpins the fuel
        calculation (FuelFlow ultimately uses the drag model for thrust).
        Cannot catch: key present with wrong type.
        """
        r = _cached("a320")
        assert "fuel_model" in r, (
            "flight_fuel_nox result missing 'fuel_model' key"
        )

    def test_used_synonym_key_present(self) -> None:
        """
        Kind: example / structural.
        Tolerance: exact.
        Reason: boolean flag lets callers detect that a synonym was used without
        having to compare trajectory_model to the input aircraft code.
        Cannot catch: flag set True when no synonym was used.
        """
        r = _cached("a320")
        assert "used_synonym" in r, (
            "flight_fuel_nox result missing 'used_synonym' key"
        )

    def test_used_synonym_is_bool(self) -> None:
        """
        Kind: example / type.
        Tolerance: exact.
        Reason: downstream code may do `if r['used_synonym']`; a string or int
        would pass that test erroneously.
        Cannot catch: value of True when False is correct.
        """
        r = _cached("a320")
        assert isinstance(r["used_synonym"], bool), (
            f"used_synonym should be bool, got {type(r['used_synonym'])}"
        )

    def test_existing_keys_still_present(self) -> None:
        """
        Kind: regression / structural.
        Tolerance: exact set containment.
        Reason: existing callers rely on fuel_kg, nox_kg, nan_fraction, n_points;
        adding new keys must not remove old ones.
        Cannot catch: old keys present but holding different values (covered by
        test_openap_perf.py regression tests).
        """
        r = _cached("a320")
        assert set(r.keys()) >= {"fuel_kg", "nox_kg", "nan_fraction", "n_points"}, (
            f"Existing keys missing from result; got {set(r.keys())}"
        )


# ===========================================================================
# PART 2 — used_synonym flag: native models are False, synonym models are True
# ===========================================================================


class TestUsedSynonymFlag:
    """Test that used_synonym reflects whether a synonym was actually needed."""

    def test_a320_no_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool.
        Reason: a320 has native WRAP and drag-polar models; no synonym needed.
        Cannot catch: a synonym used but not reported (both should be False here).
        """
        r = _cached("a320")
        assert r["used_synonym"] is False, (
            f"a320 should not use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_b738_no_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool.
        Reason: b738 has native WRAP and drag-polar models.
        Cannot catch: silent synonym use.
        """
        r = _cached("b738")
        assert r["used_synonym"] is False, (
            f"b738 should not use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_b38m_uses_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool (True).
        Reason: b38m has no native WRAP model; WRAP synonym table maps
        b38m → b737 (verified from openap/data/wrap/_synonym.csv).
        Cannot catch: synonym used but model names not checked (covered in
        TestSynonymModelNames).
        """
        r = _cached("b38m")
        assert r["used_synonym"] is True, (
            f"b38m should use a synonym (WRAP b38m→b737); used_synonym={r['used_synonym']}"
        )

    def test_b773_uses_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool (True).
        Reason: b773 has no native WRAP model (WRAP table: b773→b77w) AND no
        native drag polar (drag table: b773→b77w).
        Cannot catch: only one of the two synonyms reported.
        """
        r = _cached("b773")
        assert r["used_synonym"] is True, (
            f"b773 should use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_a319_no_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool.
        Reason: a319 has native WRAP and drag-polar models.
        Cannot catch: a bug that sets used_synonym=True for all aircraft.
        """
        r = _cached("a319")
        assert r["used_synonym"] is False, (
            f"a319 should not use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_e190_no_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool.
        Reason: e190 has native WRAP and drag-polar models.
        Cannot catch: silent synonym introduced by a future openap update.
        """
        r = _cached("e190")
        assert r["used_synonym"] is False, (
            f"e190 should not use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_a21n_uses_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool (True).
        Reason: a21n has no native WRAP model (WRAP: a21n→a320) and no native
        drag polar (drag: a21n→a20n).
        Cannot catch: the specific synonym models (covered in TestSynonymModelNames).
        """
        r = _cached("a21n")
        assert r["used_synonym"] is True, (
            f"a21n should use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_crj9_uses_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool (True).
        Reason: crj9 has no native WRAP model (crj9→e190) and no native drag
        polar (crj9→e75l).
        Cannot catch: whether the correct synonym model is used.
        """
        r = _cached("crj9")
        assert r["used_synonym"] is True, (
            f"crj9 should use a synonym; used_synonym={r['used_synonym']}"
        )

    def test_b763_uses_synonym(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact bool (True).
        Reason: b763 has native WRAP model but no native drag polar (b763→b752).
        Even a partial synonym → used_synonym=True.
        Cannot catch: the specific substitute drag-polar model.
        """
        r = _cached("b763")
        assert r["used_synonym"] is True, (
            f"b763 should use a synonym (drag polar b763→b752); "
            f"used_synonym={r['used_synonym']}"
        )


# ===========================================================================
# PART 3 — trajectory_model and fuel_model names match expected synonyms
# ===========================================================================


class TestSynonymModelNames:
    """Verify that trajectory_model and fuel_model report the resolved name."""

    # Expected synonym resolutions, derived from openap source files.
    # Format: ac -> (expected_trajectory_model, expected_fuel_drag_model)
    # For native models, both resolve to the input ac.
    # WRAP synonyms: a20n/a21n→a320, a359→b789, b38m/b39m→b737,
    #                b772/b773→b77w, crj9/e145/e170/e75l→e190
    # Drag synonyms: a21n→a20n, b39m→b38m, b763→b752, b773→b77w,
    #                crj9/e145/e170→e75l

    def test_a320_models_are_native(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string match.
        Reason: a320 has native models for both WRAP and drag polar; reporting
        anything else indicates a logic error.
        Cannot catch: both trajectory and fuel resolve to same wrong synonym.
        """
        r = _cached("a320")
        assert r["trajectory_model"] == "a320", (
            f"a320 trajectory_model should be 'a320', got {r['trajectory_model']!r}"
        )
        assert r["fuel_model"] == "a320", (
            f"a320 fuel_model should be 'a320', got {r['fuel_model']!r}"
        )

    def test_b38m_trajectory_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: WRAP synonym table entry b38m→b737; trajectory uses b737.
        Cannot catch: fuel_model wrong (covered below).
        """
        r = _cached("b38m")
        assert r["trajectory_model"] == "b737", (
            f"b38m trajectory_model should be 'b737', got {r['trajectory_model']!r}"
        )

    def test_b38m_fuel_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: b38m HAS a native drag polar, so fuel_model should be 'b38m'
        (no drag synonym needed; the WRAP synonym is for trajectory only).
        Cannot catch: synonym used in fuel calc but not in WRAP calc.
        """
        r = _cached("b38m")
        assert r["fuel_model"] == "b38m", (
            f"b38m fuel_model should be 'b38m' (native drag polar), "
            f"got {r['fuel_model']!r}"
        )

    def test_a21n_trajectory_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: WRAP synonym table: a21n→a320.
        Cannot catch: wrong source of synonym (drag vs WRAP).
        """
        r = _cached("a21n")
        assert r["trajectory_model"] == "a320", (
            f"a21n trajectory_model should be 'a320', got {r['trajectory_model']!r}"
        )

    def test_a21n_fuel_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: drag polar synonym table: a21n→a20n.
        Cannot catch: whether the correct drag polar model is used numerically.
        """
        r = _cached("a21n")
        assert r["fuel_model"] == "a20n", (
            f"a21n fuel_model should be 'a20n' (drag synonym), "
            f"got {r['fuel_model']!r}"
        )

    def test_b773_trajectory_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: WRAP synonym: b773→b77w.
        Cannot catch: wrong synonym resolution for fuel.
        """
        r = _cached("b773")
        assert r["trajectory_model"] == "b77w", (
            f"b773 trajectory_model should be 'b77w', got {r['trajectory_model']!r}"
        )

    def test_b773_fuel_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: drag polar synonym: b773→b77w.
        Cannot catch: whether b77w drag polar gives physically correct results
        for a b773 mission.
        """
        r = _cached("b773")
        assert r["fuel_model"] == "b77w", (
            f"b773 fuel_model should be 'b77w', got {r['fuel_model']!r}"
        )

    def test_crj9_trajectory_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: WRAP synonym: crj9→e190.
        Cannot catch: wrong synonym resolution direction.
        """
        r = _cached("crj9")
        assert r["trajectory_model"] == "e190", (
            f"crj9 trajectory_model should be 'e190', got {r['trajectory_model']!r}"
        )

    def test_crj9_fuel_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: drag polar synonym: crj9→e75l.
        Cannot catch: whether e75l drag polar gives correct results for crj9.
        """
        r = _cached("crj9")
        assert r["fuel_model"] == "e75l", (
            f"crj9 fuel_model should be 'e75l', got {r['fuel_model']!r}"
        )

    def test_b763_trajectory_model_is_native(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: b763 HAS a native WRAP model; trajectory should not use a synonym.
        Cannot catch: whether the WRAP model produces physically correct results.
        """
        r = _cached("b763")
        assert r["trajectory_model"] == "b763", (
            f"b763 trajectory_model should be 'b763' (native WRAP), "
            f"got {r['trajectory_model']!r}"
        )

    def test_b763_fuel_model(self) -> None:
        """
        Kind: example / correctness.
        Tolerance: exact string.
        Reason: drag polar synonym: b763→b752.
        Cannot catch: whether b752 drag polar is a good proxy for b763.
        """
        r = _cached("b763")
        assert r["fuel_model"] == "b752", (
            f"b763 fuel_model should be 'b752' (drag synonym), "
            f"got {r['fuel_model']!r}"
        )


# ===========================================================================
# PART 4 — All 22 models run without error
# ===========================================================================


class TestAllModelsRun:
    """All 22 BTS→OpenAP target models must complete without raising."""

    @pytest.mark.parametrize("ac", _ALL_MODELS)
    def test_model_runs(self, ac: str) -> None:
        """
        Kind: example / smoke.
        Tolerance: no exception raised; result dict has required keys.
        Reason: before NW-20, 11 models raised ValueError from FlightGenerator
        and 7 from FuelFlow with use_synonym=False. This test is the primary
        goal of NW-20.
        Cannot catch: a result that is numerically wrong but doesn't raise.
        """
        try:
            r = _cached(ac)
        except Exception as e:
            pytest.fail(
                f"flight_fuel_nox('{ac}', {_DISTANCE_NM}) raised {type(e).__name__}: {e}"
            )
        assert set(r.keys()) >= {"fuel_kg", "nox_kg", "nan_fraction", "n_points",
                                  "trajectory_model", "fuel_model", "used_synonym"}, (
            f"{ac}: result missing expected keys; got {set(r.keys())}"
        )


# ===========================================================================
# PART 5 — Physical plausibility checks for all 22 models
# ===========================================================================


class TestPhysicalPlausibility:
    """Fuel/nm and NaN-fraction checks for every model."""

    # Plausible fuel-per-nm bands by rough aircraft category at 800 nm.
    # Wide lower bound: we're testing at 800 nm only and mass_fraction=0.8.
    # Upper bound: raised 30 -> 40 when NW-21 added the 747s (b744 31.8, b748 30.1
    # kg/nm at 800 nm, 0.8*MTOW). Still a sanity band, not a calibration.
    # These bounds deliberately err on the conservative side.
    _FUEL_PER_NM_MIN = 2.0    # kg/nm (smallest regional jet e.g. e145)
    _FUEL_PER_NM_MAX = 40.0   # kg/nm (heaviest: b744/b748 at ~0.8*MTOW run 30-32)

    @pytest.mark.parametrize("ac", _ALL_MODELS)
    def test_fuel_per_nm_positive_finite(self, ac: str) -> None:
        """
        Kind: property / sanity.
        Tolerance: (0, ∞).
        Reason: negative or zero fuel/nm is physically impossible; NaN would
        indicate a broken integration.
        Cannot catch: a value that is positive but wrong by 100×.
        """
        r = _cached(ac)
        fuel_per_nm = _fuel_per_nm(r)
        assert math.isfinite(fuel_per_nm) and fuel_per_nm > 0, (
            f"{ac}: fuel_per_nm={fuel_per_nm} is not positive finite"
        )

    @pytest.mark.parametrize("ac", _ALL_MODELS)
    def test_fuel_per_nm_in_plausible_band(self, ac: str) -> None:
        """
        Kind: property / physical-range.
        Tolerance: [2, 40] kg/nm.
        Reason: derived from OpenAP validation paper (Sun et al. 2020, TRC 119)
        and ICAO Carbon Emissions Calculator (Doc 9988). Regional jets burn
        ~3–6 kg/nm; narrow-bodies ~5–9 kg/nm; widebodies ~10–20 kg/nm.
        The [2, 40] band is intentionally wide to accommodate the synonym
        substitutions (e.g. crj9 trajectory using e190 kinematics) which may
        shift fuel modestly.
        Cannot catch: a systematic 2× error for all models.
        Source: Sun et al. 2020 DOI 10.1016/j.trc.2020.102803.
        """
        r = _cached(ac)
        fuel_per_nm = _fuel_per_nm(r)
        assert self._FUEL_PER_NM_MIN <= fuel_per_nm <= self._FUEL_PER_NM_MAX, (
            f"{ac}: fuel_per_nm={fuel_per_nm:.2f} outside [{self._FUEL_PER_NM_MIN}, "
            f"{self._FUEL_PER_NM_MAX}] kg/nm — check synonym substitution."
        )

    @pytest.mark.parametrize("ac", _ALL_MODELS)
    def test_nan_fraction_below_threshold(self, ac: str) -> None:
        """
        Kind: property.
        Tolerance: nan_fraction ≤ 0.02.
        Reason: the NaN rule requires a raise if nan_fraction > 0.02; if a
        model run completes, nan_fraction must be ≤ 0.02 by construction.
        Checked explicitly here to confirm no model silently breaks the rule.
        Cannot catch: a NaN that was filled but not counted.
        """
        r = _cached(ac)
        assert r["nan_fraction"] <= 0.02, (
            f"{ac}: nan_fraction={r['nan_fraction']:.4f} > 0.02 — "
            "this should have raised ValueError inside flight_fuel_nox"
        )

    def test_widebody_burns_more_than_narrowbody(self) -> None:
        """
        Kind: differential / cross-type.
        Tolerance: strict inequality.
        Reason: b772 (Boeing 777-200ER, MTOW 297.6 t) at 0.8 MTOW carries
        ~238 t, vs a320 (MTOW 78 t) at ~62 t. Even with the WRAP synonym
        b772→b77w, the fuel consumption should be substantially higher.
        Cannot catch: an error that gives both the same fuel burn.
        """
        r_b772 = _cached("b772")
        r_a320 = _cached("a320")
        assert r_b772["fuel_kg"] > r_a320["fuel_kg"], (
            f"b772 fuel ({r_b772['fuel_kg']:.0f} kg) should exceed "
            f"a320 fuel ({r_a320['fuel_kg']:.0f} kg) at {_DISTANCE_NM} nm"
        )


# ===========================================================================
# PART 6 — Regression checks: a320/b738/a333 are unchanged
# ===========================================================================


class TestRegressionExistingModels:
    """Existing models must be unaffected by the synonym feature addition."""

    def test_a320_fuel_regression(self) -> None:
        """
        Kind: golden / regression.
        Tolerance: ±0.5% of 4965 kg (task spec says 749 nm stays within 0.5%).
        Reason: the spec explicitly requires this regression; 0.5% is tighter
        than the existing ±5% test in test_openap_perf.py.  Both must pass.
        Cannot catch: a compensating error that preserves total but changes
        the trajectory shape.
        """
        r = flight_fuel_nox("a320", 749.0)
        ref = 4965.0
        assert r["fuel_kg"] == pytest.approx(ref, rel=0.005), (
            f"a320@749nm fuel_kg={r['fuel_kg']:.1f}, ref={ref} (±0.5%)"
        )

    def test_a320_used_synonym_false_at_749nm(self) -> None:
        """
        Kind: regression / correctness.
        Tolerance: exact bool.
        Reason: a320 must report used_synonym=False; if the synonym feature
        accidentally always sets True, this test catches it.
        Cannot catch: used_synonym=False when it should be True for other models.
        """
        r = flight_fuel_nox("a320", 749.0)
        assert r["used_synonym"] is False

    def test_b738_used_synonym_false(self) -> None:
        """
        Kind: regression / correctness.
        Tolerance: exact bool.
        Reason: b738 has native WRAP and drag polar; must not report synonym use.
        Cannot catch: wrong synonym for b738 if it were introduced.
        """
        r = _cached("b738")
        assert r["used_synonym"] is False


# ===========================================================================
# PART 7 — Native model tried first (monkeypatch / mock tests)
# ===========================================================================


class TestNativeTryFirst:
    """
    Confirm that the synonym fallback only activates when the native model
    actually fails — not pre-emptively.

    Strategy: monkeypatch FlightGenerator and FuelFlow constructors to track
    calls, then call flight_fuel_nox for a native model (a320) and a synonym
    model (b38m), checking the construction sequence.
    """

    def test_native_trajectory_tried_for_b38m(self) -> None:
        """
        Kind: example / behavioural.
        Tolerance: FlightGenerator called with use_synonym=False before
        use_synonym=True.
        Reason: "try native first; fall back to synonyms only if native fails"
        is the spec requirement.  If we always used use_synonym=True we'd skip
        the native attempt and miss future regressions when a model is added.
        Cannot catch: a correct order with a bug in the synonym logic itself.
        """
        import openap_perf as op
        call_log: list[dict] = []

        original_gen = op.FlightGenerator

        class TrackingGen:
            def __init__(self, ac: str, **kwargs):
                use_syn = kwargs.get("use_synonym", False)
                call_log.append({"ac": ac, "use_synonym": use_syn})
                # Raise as the real constructor would for b38m without synonym
                if not use_syn and ac == "b38m":
                    raise ValueError(f"Kinematic model for {ac} not available.")
                # Use real constructor on second call
                self._delegate = original_gen(ac, **kwargs)

            def __getattr__(self, name):
                return getattr(self._delegate, name)

        with mock.patch.object(op, "FlightGenerator", TrackingGen):
            # Need to also allow _get_trajectory / _climb_descent_distances
            # to not fail — only constructor tracking needed
            # Actually flight_fuel_nox calls _climb_descent_distances and
            # _get_trajectory which use FlightGenerator internally.
            # We only want to verify the order of (use_synonym=False) then
            # (use_synonym=True); real execution can proceed after.
            # Use a real result via pass-through after first failure.
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    flight_fuel_nox("b38m", 800.0)
            except Exception:
                pass  # may fail if mock disrupts trajectory; that's ok

        # The first call attempt must have use_synonym=False
        native_calls = [c for c in call_log if not c["use_synonym"]]
        synonym_calls = [c for c in call_log if c["use_synonym"]]

        assert len(native_calls) >= 1, (
            "Expected at least one call to FlightGenerator with use_synonym=False "
            f"(native attempt), but call log was: {call_log}"
        )
        # If there were synonym calls, the first call overall must be native
        if synonym_calls:
            first_call_is_native = not call_log[0]["use_synonym"]
            assert first_call_is_native, (
                "First FlightGenerator call was with use_synonym=True; "
                "expected native attempt first"
            )

    def test_native_fuel_tried_for_b39m(self) -> None:
        """
        Kind: example / behavioural.
        Tolerance: FuelFlow called with use_synonym=False before use_synonym=True.
        Reason: same 'native first' spec requirement for the FuelFlow pathway.
        Cannot catch: native called but ignored in favor of synonym result.
        """
        import openap_perf as op
        fuel_call_log: list[dict] = []

        original_ff = op.FuelFlow

        class TrackingFF:
            def __init__(self, ac: str, **kwargs):
                use_syn = kwargs.get("use_synonym", False)
                fuel_call_log.append({"ac": ac, "use_synonym": use_syn})
                if not use_syn and ac == "b39m":
                    raise ValueError(
                        f"Drag polar for {ac} not available. "
                        "Try to set `use_synonym=True` to initialize the object."
                    )
                self._delegate = original_ff(ac, **kwargs)

            def __getattr__(self, name):
                return getattr(self._delegate, name)

        with mock.patch.object(op, "FuelFlow", TrackingFF):
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    flight_fuel_nox("b39m", 800.0)
            except Exception:
                pass

        native_ff_calls = [c for c in fuel_call_log if not c["use_synonym"]]
        assert len(native_ff_calls) >= 1, (
            "Expected at least one FuelFlow call with use_synonym=False "
            f"(native attempt), but call log was: {fuel_call_log}"
        )

    def test_a320_never_needs_synonym_call(self) -> None:
        """
        Kind: example / behavioural.
        Tolerance: no FlightGenerator call with use_synonym=True for a320.
        Reason: a320 has native models; the synonym fallback should not be
        triggered.  If we always fall back to synonyms, this test catches it.
        Cannot catch: synonym triggered but returning the same model.
        """
        import openap_perf as op
        gen_calls: list[dict] = []

        original_gen = op.FlightGenerator

        class TrackingGen2:
            def __init__(self, ac: str, **kwargs):
                use_syn = kwargs.get("use_synonym", False)
                gen_calls.append({"ac": ac, "use_synonym": use_syn})
                self._delegate = original_gen(ac, **kwargs)

            def __getattr__(self, name):
                return getattr(self._delegate, name)

        with mock.patch.object(op, "FlightGenerator", TrackingGen2):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                flight_fuel_nox("a320", 749.0)

        synonym_calls = [c for c in gen_calls if c["use_synonym"]]
        assert len(synonym_calls) == 0, (
            f"a320 should not trigger synonym FlightGenerator; got calls: {gen_calls}"
        )
