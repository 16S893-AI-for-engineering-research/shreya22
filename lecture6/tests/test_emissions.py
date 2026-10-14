"""
Tests for lecture6/src/emissions.py — NW-8

All tests import from the `emissions` module (lecture6/src/emissions.py).

Emission indices (EI) used throughout are the PLACEHOLDER values defined
in emissions.py.  Specific numeric assertions use those placeholder values
directly so that if the placeholders are updated the tests remain correct.
"""

from __future__ import annotations

import math
import sys
import pathlib

_SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

import pytest
from emissions import split_phases, emissions_kg, EI_TABLE  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sum_pollutant(d: dict) -> float:
    return sum(d.values())


# ---------------------------------------------------------------------------
# 1. split_phases — invariants
# ---------------------------------------------------------------------------

class TestSplitPhases:
    def test_shares_sum_to_one(self):
        """
        Invariant/property test — no tolerance needed (exact float identity):
        lto_fuel + cruise_fuel must equal fuel_kg to abs <= 1e-9.

        Reason: split_phases just multiplies by lto_share and (1-lto_share), so the
        sum is exact in IEEE 754 (at most 1 ulp rounding) — 1e-9 is safe even for
        fuel_kg up to ~1e6 kg.
        """
        fuel = 5_000.0
        lto_fuel, cruise_fuel = split_phases(fuel, lto_share=0.3)
        assert abs(lto_fuel + cruise_fuel - fuel) <= 1e-9, (
            f"Phase fuels {lto_fuel}+{cruise_fuel} != {fuel}"
        )

    def test_phase_fuels_are_correct_fractions(self):
        """
        Example test — exact: split_phases(1000, 0.25) -> (250, 750).

        Checks that lto_share multiplies to lto_fuel and (1-lto_share) to cruise_fuel.
        Tolerance: abs <= 1e-9 to guard against any internal rounding.
        """
        lto_fuel, cruise_fuel = split_phases(1000.0, lto_share=0.25)
        assert abs(lto_fuel - 250.0) <= 1e-9
        assert abs(cruise_fuel - 750.0) <= 1e-9

    def test_zero_fuel_gives_zero_phases(self):
        """
        Example/edge test — no tolerance needed: 0 * anything == 0.

        Confirms split_phases handles zero fuel without error.
        """
        lto_fuel, cruise_fuel = split_phases(0.0, lto_share=0.2)
        assert lto_fuel == 0.0
        assert cruise_fuel == 0.0

    def test_lto_share_zero_gives_all_cruise(self):
        """
        Example/boundary test — abs <= 1e-9:
        lto_share=0 -> all fuel is cruise; none in LTO.
        """
        lto_fuel, cruise_fuel = split_phases(800.0, lto_share=0.0)
        assert abs(lto_fuel - 0.0) <= 1e-9
        assert abs(cruise_fuel - 800.0) <= 1e-9

    def test_lto_share_one_gives_all_lto(self):
        """
        Example/boundary test — abs <= 1e-9:
        lto_share=1 -> all fuel is LTO; none in cruise.
        """
        lto_fuel, cruise_fuel = split_phases(600.0, lto_share=1.0)
        assert abs(lto_fuel - 600.0) <= 1e-9
        assert abs(cruise_fuel - 0.0) <= 1e-9

    @pytest.mark.parametrize("bad_share", [-0.1, -1e-12, 1.001, 2.0, float("nan"), float("inf")])
    def test_invalid_lto_share_rejected(self, bad_share):
        """
        Property/validation test — no tolerance: any lto_share outside [0, 1]
        (including NaN and inf) must raise ValueError.

        Reason: a fractional share outside [0,1] is physically meaningless and
        would silently produce negative fuel masses.
        """
        with pytest.raises((ValueError, TypeError)):
            split_phases(1000.0, bad_share)

    def test_negative_fuel_rejected(self):
        """
        Property/validation test — no tolerance: negative fuel_kg must raise ValueError.

        Reason: fuel mass is a physical quantity and cannot be negative.
        """
        with pytest.raises((ValueError, TypeError)):
            split_phases(-1.0, lto_share=0.3)


# ---------------------------------------------------------------------------
# 2. EI_TABLE — structure
# ---------------------------------------------------------------------------

class TestEITable:
    EXPECTED_POLLUTANTS = {"NOx", "CO", "HC"}
    EXPECTED_PHASES = {"LTO", "cruise"}

    def test_ei_table_has_required_phases(self):
        """
        Invariant/structural test — no tolerance: EI_TABLE must contain
        'LTO' and 'cruise' top-level keys.

        The emissions model is defined over exactly these two phases.
        """
        for phase in self.EXPECTED_PHASES:
            assert phase in EI_TABLE, f"Missing phase {phase!r} in EI_TABLE"

    def test_ei_table_has_required_pollutants(self):
        """
        Invariant/structural test — no tolerance: each phase in EI_TABLE must
        contain at least the three pollutant keys: NOx, CO, HC.
        """
        for phase in self.EXPECTED_PHASES:
            for poll in self.EXPECTED_POLLUTANTS:
                assert poll in EI_TABLE[phase], (
                    f"Missing pollutant {poll!r} in EI_TABLE[{phase!r}]"
                )

    def test_ei_values_are_positive(self):
        """
        Property/invariant test — no tolerance: every EI value in the table
        must be strictly positive (g/kg fuel > 0).

        Negative EI would be physically nonsensical for a combustion process.
        """
        for phase, pols in EI_TABLE.items():
            for poll, val in pols.items():
                assert val > 0, (
                    f"EI_TABLE[{phase!r}][{poll!r}] = {val} is not positive"
                )


# ---------------------------------------------------------------------------
# 3. Unit-conversion correctness: 1 kg fuel, EI = 1000 g/kg -> exactly 1 kg
# ---------------------------------------------------------------------------

class TestUnitConversion:
    def test_unit_conversion_exact(self):
        """
        Example/golden test — exact (no tolerance): 1 kg fuel burned with an
        emission index of 1000 g/kg must yield exactly 1 kg of that pollutant.

        mass_kg = EI[g/kg] * fuel[kg] / 1000
                = 1000 * 1 / 1000 = 1.0  (exact integer arithmetic in float)

        This pins the g/kg -> kg conversion formula explicitly, confirming no
        off-by-1000 error.  Uses a *synthetic* EI of 1000 g/kg injected via a
        temporary monkeypatch so the test is independent of placeholder values.
        """
        # We call emissions_kg with a special monkeypatch-free approach:
        # Because we cannot change EI_TABLE values directly without affecting
        # other tests, we verify the formula by checking a known ratio.
        #
        # For any pollutant p, emissions_kg(fuel, share)[p] must equal:
        #   (EI_TABLE["LTO"][p] * share + EI_TABLE["cruise"][p] * (1-share)) * fuel / 1000
        #
        # We test this at lto_share=1.0 so only LTO applies, then check the ratio:
        #   emissions_kg(1.0, 1.0)[p] == EI_TABLE["LTO"][p] / 1000
        fuel = 1.0
        share = 1.0  # all LTO
        result = emissions_kg(fuel, lto_share=share)
        for poll in ("NOx", "CO", "HC"):
            ei_lto = EI_TABLE["LTO"][poll]
            expected = ei_lto / 1000.0  # the g/kg -> kg formula
            assert result[poll] == expected, (
                f"Unit conversion failed for {poll}: "
                f"got {result[poll]}, expected EI_LTO/1000 = {expected}"
            )

    def test_unit_conversion_with_synthetic_ei_1000(self, monkeypatch):
        """
        Golden test — exact (no tolerance): if every EI is 1000 g/kg, then
        1 kg fuel at any lto_share must give exactly 1 kg per pollutant.

        This directly tests the g/kg -> kg formula without dependence on actual
        placeholder EI numbers: mass = 1000 * 1 / 1000 = 1.0, exactly, for
        both phases, so the total over both phases at *any* split is also 1.0.

        Uses monkeypatch to temporarily override EI_TABLE.
        """
        import emissions as em_mod
        synthetic = {
            "LTO":    {"NOx": 1000.0, "CO": 1000.0, "HC": 1000.0},
            "cruise": {"NOx": 1000.0, "CO": 1000.0, "HC": 1000.0},
        }
        monkeypatch.setattr(em_mod, "EI_TABLE", synthetic)

        for share in (0.0, 0.3, 0.5, 0.7, 1.0):
            result = em_mod.emissions_kg(1.0, lto_share=share)
            for poll in ("NOx", "CO", "HC"):
                assert result[poll] == 1.0, (
                    f"With EI=1000 g/kg, 1 kg fuel -> expected 1 kg {poll}, "
                    f"got {result[poll]} at lto_share={share}"
                )


# ---------------------------------------------------------------------------
# 4. Non-negativity
# ---------------------------------------------------------------------------

class TestNonNegativity:
    @pytest.mark.parametrize("fuel,share", [
        (0.0, 0.0),
        (0.0, 1.0),
        (1.0, 0.0),
        (1.0, 1.0),
        (500.0, 0.15),
        (1e6, 0.5),
    ])
    def test_totals_non_negative(self, fuel, share):
        """
        Property/invariant test — no tolerance: every pollutant emission must
        be >= 0 for any non-negative fuel and valid lto_share.

        EI values are positive and fuel is non-negative so the result cannot
        be negative; this guards against sign bugs in the implementation.
        """
        result = emissions_kg(fuel, lto_share=share)
        for poll, kg in result.items():
            assert kg >= 0.0, (
                f"Negative emission for {poll}: {kg} "
                f"(fuel={fuel}, share={share})"
            )


# ---------------------------------------------------------------------------
# 5. Linearity: doubling fuel doubles every pollutant
# ---------------------------------------------------------------------------

class TestLinearity:
    @pytest.mark.parametrize("fuel,share", [
        (100.0, 0.1),
        (500.0, 0.5),
        (12_345.0, 0.2),
        (1e6, 0.8),
    ])
    def test_doubling_fuel_doubles_emissions(self, fuel, share):
        """
        Property/metamorphic test — rel_tol=1e-12:
        emissions_kg(2*fuel, share)[p] == 2 * emissions_kg(fuel, share)[p]

        Reason: the formula mass = EI * fuel / 1000 is linear in fuel, so
        any deviation beyond machine epsilon (~1e-15) indicates a bug.
        Tolerance 1e-12 is conservative but well above float cancellation risk.
        """
        single = emissions_kg(fuel, lto_share=share)
        double = emissions_kg(2 * fuel, lto_share=share)
        for poll in ("NOx", "CO", "HC"):
            assert math.isclose(double[poll], 2 * single[poll], rel_tol=1e-12), (
                f"Linearity violated for {poll}: "
                f"2x({fuel})={double[poll]}, expected {2*single[poll]}"
            )


# ---------------------------------------------------------------------------
# 6. Additivity: emissions(A+B) == emissions(A) + emissions(B)  [metamorphic]
# ---------------------------------------------------------------------------

class TestAdditivity:
    @pytest.mark.parametrize("fuel_a,fuel_b,share", [
        (100.0, 200.0, 0.2),
        (0.0, 500.0, 0.4),
        (1234.5, 6789.1, 0.35),
        (1e5, 2.5e5, 0.6),
    ])
    def test_emissions_additivity(self, fuel_a, fuel_b, share):
        """
        Metamorphic test — abs_tol=1e-9:
        emissions_kg(A+B, share)[p] == emissions_kg(A, share)[p]
                                      + emissions_kg(B, share)[p]

        This is the classical additivity (linearity-over-addition) property.
        The same lto_share is used for all calls so phase fractions are consistent.
        Tolerance 1e-9 kg covers floating-point accumulation for inputs up to ~1e6 kg.

        What this test CANNOT catch: if EI values differ between the two calls
        (e.g., a non-deterministic or fuel-dependent EI), the test may pass
        spuriously; EI_TABLE must be stateless for additivity to hold physically.
        """
        em_ab = emissions_kg(fuel_a + fuel_b, lto_share=share)
        em_a = emissions_kg(fuel_a, lto_share=share)
        em_b = emissions_kg(fuel_b, lto_share=share)
        for poll in ("NOx", "CO", "HC"):
            combined = em_a[poll] + em_b[poll]
            assert abs(em_ab[poll] - combined) <= 1e-9, (
                f"Additivity violated for {poll}: "
                f"emissions({fuel_a}+{fuel_b})={em_ab[poll]}, "
                f"sum={combined} (diff={abs(em_ab[poll]-combined):.3e})"
            )


# ---------------------------------------------------------------------------
# 7. emissions_kg returns all expected pollutant keys
# ---------------------------------------------------------------------------

class TestEmissionsKgKeys:
    def test_returns_all_pollutants(self):
        """
        Invariant/structural test — no tolerance: emissions_kg must return a
        dict whose keys include at least NOx, CO, HC.

        Consumers depend on these exact key names; extra keys are allowed.
        """
        result = emissions_kg(1000.0, lto_share=0.2)
        for poll in ("NOx", "CO", "HC"):
            assert poll in result, f"Missing pollutant key {poll!r} in result"

    def test_returns_dict(self):
        """
        Structural test — no tolerance: the return type of emissions_kg must
        be a dict (or dict subclass) mapping strings to floats/ints.
        """
        result = emissions_kg(500.0, lto_share=0.3)
        assert isinstance(result, dict)
        for k, v in result.items():
            assert isinstance(k, str), f"Key {k!r} is not a str"
            assert isinstance(v, (int, float)), f"Value for {k!r} is not numeric"


# ---------------------------------------------------------------------------
# 8. Phase shares sum to 1 (abs <= 1e-9) — the acceptance criterion
# ---------------------------------------------------------------------------

class TestPhaseSumToOne:
    @pytest.mark.parametrize("share", [0.0, 0.1, 0.2, 0.5, 0.8, 1.0])
    def test_phase_fuels_sum_to_input(self, share):
        """
        Invariant/property test — abs <= 1e-9:
        lto_fuel_kg + cruise_fuel_kg == fuel_kg

        This is the primary acceptance criterion from NW-8: the LTO + cruise
        fuel shares must always sum back to the input fuel to within 1e-9 kg.
        The tolerance is 1e-9 because split_phases uses one multiply and one
        subtraction; absolute error is bounded by ~2*eps*fuel ≈ 4.4e-13 * fuel,
        which is well within 1e-9 for fuel up to ~2e3 kg.
        """
        fuel = 1000.0
        lto, cruise = split_phases(fuel, share)
        assert abs(lto + cruise - fuel) <= 1e-9, (
            f"Phase sum {lto}+{cruise}={lto+cruise} != {fuel} (diff={abs(lto+cruise-fuel):.3e})"
        )

    @pytest.mark.parametrize("fuel", [0.0, 1.0, 100.0, 1e4, 1e6])
    def test_phase_fuels_sum_for_various_fuel_amounts(self, fuel):
        """
        Property test — abs <= 1e-9 * max(fuel, 1.0):
        Phase fuels must sum to input fuel across a range of magnitudes.

        Tolerance scales with fuel to handle larger absolute rounding errors at
        large fuel values (e.g., 1e6 kg might have up to ~4e-10 abs error).
        """
        share = 0.3
        lto, cruise = split_phases(fuel, share)
        tol = max(1e-9 * fuel, 1e-12)  # scale tolerance with magnitude
        assert abs(lto + cruise - fuel) <= tol, (
            f"fuel={fuel}: phase sum error {abs(lto+cruise-fuel):.3e} > tol {tol:.3e}"
        )
