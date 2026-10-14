"""
Tests for lecture6/src/fuel.py — fuel proxy: fuel_kg = c(class) * distance_nm.

All tests import fuel.py which does NOT exist yet, so the whole module fails on
import until the feature is implemented. Run before implementing to confirm the
correct red failure (ModuleNotFoundError / ImportError).
"""
from __future__ import annotations

import math
import pathlib
import sys

_SRC_DIR = pathlib.Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(_SRC_DIR))

import pytest

# ── The module under test ────────────────────────────────────────────────────
from fuel import (  # noqa: E402
    stage_length_class,
    fuel_kg,
    COEFFICIENTS_KG_PER_NM,  # dict: class_name -> float
)

# ── Constants ────────────────────────────────────────────────────────────────
NM_PER_KM = 1 / 1.852  # exactly, by definition of the nautical mile


# ════════════════════════════════════════════════════════════════════════════
# 1. stage_length_class — boundary tests (example-based)
# ════════════════════════════════════════════════════════════════════════════

class TestStageLengthClass:
    """
    Test type: example-based / boundary.
    Tolerance: exact (integer class boundaries; no floating-point arithmetic
    inside the classifier itself, just comparisons).
    Purpose: verify that the documented class cut-points (< 500 nm → short,
    500–1500 nm → medium, > 1500 nm → long) are applied exactly, including
    the two boundary values 500 and 1500.
    """

    def test_short_interior(self):
        """Interior short-haul value returns 'short'."""
        assert stage_length_class(300.0) == "short"

    def test_medium_interior(self):
        """Interior medium-haul value returns 'medium'."""
        assert stage_length_class(1000.0) == "medium"

    def test_long_interior(self):
        """Interior long-haul value returns 'long'."""
        assert stage_length_class(2000.0) == "long"

    def test_short_upper_boundary_exclusive(self):
        """499.999 nm is strictly below 500 → must be 'short'."""
        assert stage_length_class(499.999) == "short"

    def test_medium_lower_boundary_inclusive(self):
        """500.0 nm is exactly at the lower boundary → must be 'medium'."""
        assert stage_length_class(500.0) == "medium"

    def test_medium_upper_boundary_inclusive(self):
        """1500.0 nm is exactly at the upper boundary → must be 'medium'."""
        assert stage_length_class(1500.0) == "medium"

    def test_long_lower_boundary_exclusive(self):
        """1500.001 nm is strictly above 1500 → must be 'long'."""
        assert stage_length_class(1500.001) == "long"

    def test_zero_distance_is_short(self):
        """Zero nautical miles is short-haul (distance < 500)."""
        assert stage_length_class(0.0) == "short"

    def test_negative_distance_raises(self):
        """
        Negative distance_nm is physically meaningless.
        stage_length_class must raise ValueError for negative inputs.
        """
        with pytest.raises(ValueError, match="negative"):
            stage_length_class(-1.0)


# ════════════════════════════════════════════════════════════════════════════
# 2. fuel_kg — basic numeric properties (property-style, example-driven)
# ════════════════════════════════════════════════════════════════════════════

class TestFuelKgBasicProperties:
    """
    Test type: invariance / property-based (without hypothesis; each
    sub-test exercises a distinct algebraic invariant across all three
    stage classes, which gives sufficient coverage without a property
    framework).
    Tolerance: exact equality where math permits; relative 1e-12 for
    floating-point multiplication round-trips.
    """

    SAMPLE_DISTANCES = {
        "short": 300.0,
        "medium": 800.0,
        "long": 3000.0,
    }

    def test_zero_distance_gives_zero_fuel(self):
        """
        Zero distance → zero fuel for every class.
        Type: invariant.  Tolerance: exact (0.0).
        Physically: on the ground means no fuel burned.
        """
        for cls in ("short", "medium", "long"):
            result = fuel_kg(0.0, cls)
            assert result == 0.0, f"fuel_kg(0, {cls!r}) = {result}, expected 0.0"

    def test_fuel_non_negative(self):
        """
        fuel_kg must be ≥ 0 for all positive distances and every class.
        Type: invariant.  Tolerance: none (strictly non-negative).
        """
        for cls, d in self.SAMPLE_DISTANCES.items():
            assert fuel_kg(d, cls) >= 0.0

    def test_linear_scaling_within_class(self):
        """
        fuel(2*d, cls) == 2 * fuel(d, cls) for all three stage classes.
        Type: metamorphic (scaling invariant).
        Tolerance: relative 1e-12 to allow for IEEE-754 double-precision
        round-off in a single multiplication; no physical approximation.
        """
        for cls, d in self.SAMPLE_DISTANCES.items():
            f1 = fuel_kg(d, cls)
            f2 = fuel_kg(2.0 * d, cls)
            assert math.isclose(f2, 2.0 * f1, rel_tol=1e-12), (
                f"Linearity violated for {cls}: fuel({2*d}) = {f2}, "
                f"2 * fuel({d}) = {2*f1}"
            )

    def test_monotone_non_decreasing_within_class(self):
        """
        fuel_kg is monotone non-decreasing in distance within each class.
        Type: property / invariant.
        Tolerance: exact (≥).
        We test d < d+1 for a representative distance in each class.
        """
        checks = [
            ("short", 100.0, 200.0),
            ("medium", 600.0, 700.0),
            ("long", 2000.0, 2500.0),
        ]
        for cls, d1, d2 in checks:
            assert fuel_kg(d1, cls) <= fuel_kg(d2, cls), (
                f"Monotonicity violated for {cls}: "
                f"fuel({d1}) > fuel({d2})"
            )

    def test_negative_distance_raises(self):
        """
        fuel_kg must raise ValueError for any negative distance_nm.
        Type: invariant / boundary.
        Tolerance: none (structural contract).
        """
        for cls in ("short", "medium", "long"):
            with pytest.raises(ValueError, match="negative"):
                fuel_kg(-0.1, cls)

    def test_unknown_class_raises(self):
        """
        fuel_kg must raise ValueError (or KeyError) for an unrecognised
        stage-length class name.
        Type: boundary.  Tolerance: none.
        """
        with pytest.raises((ValueError, KeyError)):
            fuel_kg(500.0, "ultra")


# ════════════════════════════════════════════════════════════════════════════
# 3. Unit round-trip: nm ↔ km consistency
# ════════════════════════════════════════════════════════════════════════════

class TestFuelKgUnitRoundTrip:
    """
    Test type: metamorphic / unit-consistency.
    Tolerance: relative 1e-12 (only IEEE-754 round-off; 1 nm ≡ 1.852 km
    is an exact SI definition, so no physical approximation is introduced).
    Purpose: verify that computing distance in km and dividing by 1.852 to
    recover nm, then calling fuel_kg, gives the same answer as calling
    fuel_kg directly with the nm value.  This guards against accidental
    unit confusion inside fuel_kg.
    """

    def test_nm_km_round_trip_short(self):
        """Short-haul: distance expressed in nm and recovered from km gives same fuel."""
        d_nm = 400.0
        d_km = d_nm * 1.852
        d_nm_recovered = d_km / 1.852
        assert math.isclose(
            fuel_kg(d_nm, "short"),
            fuel_kg(d_nm_recovered, "short"),
            rel_tol=1e-12,
        )

    def test_nm_km_round_trip_medium(self):
        """Medium-haul: distance expressed in nm and recovered from km gives same fuel."""
        d_nm = 900.0
        d_km = d_nm * 1.852
        d_nm_recovered = d_km / 1.852
        assert math.isclose(
            fuel_kg(d_nm, "medium"),
            fuel_kg(d_nm_recovered, "medium"),
            rel_tol=1e-12,
        )

    def test_nm_km_round_trip_long(self):
        """Long-haul: distance expressed in nm and recovered from km gives same fuel."""
        d_nm = 4000.0
        d_km = d_nm * 1.852
        d_nm_recovered = d_km / 1.852
        assert math.isclose(
            fuel_kg(d_nm, "long"),
            fuel_kg(d_nm_recovered, "long"),
            rel_tol=1e-12,
        )


# ════════════════════════════════════════════════════════════════════════════
# 4. Coefficient table sanity
# ════════════════════════════════════════════════════════════════════════════

class TestCoefficientTable:
    """
    Test type: invariant / structural.
    Tolerance: none (structural contract).
    Purpose: verify the COEFFICIENTS_KG_PER_NM table is well-formed and
    internally consistent, without asserting specific numeric values (since
    the values are explicitly labelled as illustrative placeholders in the
    module docstring).
    """

    def test_all_classes_present(self):
        """COEFFICIENTS_KG_PER_NM must contain keys 'short', 'medium', 'long'."""
        assert set(COEFFICIENTS_KG_PER_NM.keys()) >= {"short", "medium", "long"}

    def test_all_coefficients_positive(self):
        """Every coefficient in the table must be strictly positive."""
        for cls, coeff in COEFFICIENTS_KG_PER_NM.items():
            assert coeff > 0.0, f"Coefficient for {cls!r} is not positive: {coeff}"

    def test_fuel_equals_coefficient_times_distance(self):
        """
        fuel_kg(d, cls) == COEFFICIENTS_KG_PER_NM[cls] * d for each class.
        Type: differential (verifies the formula is actually applied).
        Tolerance: relative 1e-12 (single IEEE-754 multiply).
        This does NOT assert a specific coefficient value; it verifies
        the formula fuel = c * d is implemented correctly.
        """
        test_pairs = [("short", 300.0), ("medium", 900.0), ("long", 3500.0)]
        for cls, d in test_pairs:
            expected = COEFFICIENTS_KG_PER_NM[cls] * d
            got = fuel_kg(d, cls)
            assert math.isclose(got, expected, rel_tol=1e-12), (
                f"fuel_kg({d}, {cls!r}) = {got}, "
                f"expected {expected} (coeff={COEFFICIENTS_KG_PER_NM[cls]})"
            )
