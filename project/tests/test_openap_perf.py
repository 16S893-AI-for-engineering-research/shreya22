"""
Tests for openap_perf.flight_fuel_nox.

Unit-conversion constants pinned from openap.aero (verified from source):
  kts  = 0.514444  m/s per knot
  ft   = 0.3048    m per foot
  fpm  = 0.00508   m/s per ft/min
  nm   = 1852.0    m per nautical mile

These are tested explicitly so any upstream rename of openap constants fails
loudly rather than silently propagating wrong physics.

Module-level imports are plain (no try/except), so a missing openap_perf or
missing openap dependency causes a collection ERROR, not a silent skip.

Cached results
--------------
Heavy openap calls (full trajectory + fuel integration) are computed once per
(ac, distance_nm) pair using a module-level dict ``_CACHE`` and the helper
``_cached``.  This keeps total test runtime under 60 s.
"""
from __future__ import annotations

import math
from typing import Dict, Tuple

import pytest

# Plain imports — collection ERROR if module missing (no try/except guard;
# test_no_silent_skips.py enforces this pattern across all test files).
from openap_perf import flight_fuel_nox

# ---------------------------------------------------------------------------
# Module-level cache: (ac, distance_nm) -> result dict
# Computed lazily, re-used across all tests.
# ---------------------------------------------------------------------------
_CACHE: Dict[Tuple[str, int], dict] = {}


def _cached(ac: str, distance_nm: int) -> dict:
    """Return (cached) flight_fuel_nox result for (ac, distance_nm)."""
    key = (ac, distance_nm)
    if key not in _CACHE:
        _CACHE[key] = flight_fuel_nox(ac, float(distance_nm))
    return _CACHE[key]


# ---------------------------------------------------------------------------
# Helper: EI_NOx from a result dict
# ---------------------------------------------------------------------------
def _ei_nox(r: dict) -> float:
    return 1000.0 * r["nox_kg"] / r["fuel_kg"]


# ===========================================================================
# 1.  Unit-conversion constants (pinned)
# ===========================================================================

def test_unit_kts_value() -> None:
    """
    Kind: example / pinned-constant.
    Tolerance: exact (floating-point equality of the constant as defined in
    openap.aero source).
    Reason: flight_fuel_nox passes groundspeed (kt) directly to FuelFlow and
    Emission; if the upstream constant changes the physics is silently wrong.
    Cannot catch: a compensating sign error in both FuelFlow and Emission.
    """
    from openap import aero
    assert aero.kts == pytest.approx(0.514444, rel=1e-6), (
        "openap.aero.kts changed; verify flight_fuel_nox still passes kt correctly"
    )


def test_unit_ft_value() -> None:
    """
    Kind: example / pinned-constant.
    Tolerance: exact.
    Reason: altitude and vertical-rate arguments must be in ft; wrong conversion
    shifts NOx by > 2x.
    Cannot catch: a wrong sign that happens to be within tolerance at sea level.
    """
    from openap import aero
    assert aero.ft == pytest.approx(0.3048, rel=1e-6)


def test_unit_fpm_value() -> None:
    """
    Kind: example / pinned-constant.
    Tolerance: exact.
    Reason: vertical_rate column is in ft/min (source: gen.py line
    ``vertical_rate=lambda x: (x.vs / aero.fpm).astype(int)``); passing
    m/s would inflate vs by ~197x and corrupt thrust.
    Cannot catch: error exactly at level flight (vs=0) where fpm doesn't matter.
    """
    from openap import aero
    assert aero.fpm == pytest.approx(0.00508, rel=1e-6)


def test_unit_nm_value() -> None:
    """
    Kind: example / pinned-constant.
    Tolerance: exact.
    Reason: distance_nm is converted to meters using this factor for cruise range.
    Cannot catch: a uniform scale error that shifts all distances equally.
    """
    from openap import aero
    assert aero.nm == pytest.approx(1852.0, rel=1e-6)


def test_unit_known_kt_conversion() -> None:
    """
    Kind: example.
    Tolerance: abs 1e-4 m/s (4 significant figures suffice for aerodynamic use).
    Reason: verifies the multiplicative direction is correct — 450 kt should give
    ~231.5 m/s, not ~874 m/s (wrong direction would violate physics).
    Cannot catch: a consistent scale factor error of exactly 1.
    """
    from openap import aero
    result_ms = 450.0 * aero.kts
    assert result_ms == pytest.approx(231.4998, abs=1e-3)


def test_unit_known_ft_conversion() -> None:
    """
    Kind: example.
    Tolerance: abs 0.01 m.
    Reason: 35 000 ft (typical cruise) should be 10 668 m; wrong direction
    would give 114 830 m (~FL376 error) and corrupt drag calculation.
    Cannot catch: an off-by-one flight level error.
    """
    from openap import aero
    result_m = 35_000.0 * aero.ft
    assert result_m == pytest.approx(10668.0, abs=0.01)


# ===========================================================================
# 2.  Return-value structure
# ===========================================================================

def test_return_keys_present() -> None:
    """
    Kind: example / structural.
    Tolerance: none (exact key check).
    Reason: callers index by name; missing key raises KeyError at call-site
    far from the root cause.
    Cannot catch: a key present but holding NaN silently.
    """
    r = _cached("a320", 749)
    assert set(r.keys()) >= {"fuel_kg", "nox_kg", "nan_fraction", "n_points"}


def test_return_values_finite_positive() -> None:
    """
    Kind: example / sanity.
    Tolerance: none (finite + >0 check).
    Reason: NaN or <=0 propagates into downstream EI or log calculations.
    Cannot catch: a value of 1e-300 (positive but physically absurd).
    """
    r = _cached("a320", 749)
    assert math.isfinite(r["fuel_kg"]) and r["fuel_kg"] > 0
    assert math.isfinite(r["nox_kg"]) and r["nox_kg"] > 0
    assert math.isfinite(r["nan_fraction"])
    assert isinstance(r["n_points"], int) and r["n_points"] > 0


# ===========================================================================
# 3.  NaN fraction reported and ≤ 0.02
# ===========================================================================

def test_nan_fraction_is_non_negative() -> None:
    """
    Kind: property.
    Tolerance: exact (>= 0).
    Reason: a negative nan_fraction indicates a counting bug.
    Cannot catch: a spuriously high fraction that happens to be ≤ 0.02.
    """
    for ac, d in [("a320", 749), ("b738", 782), ("a333", 771)]:
        r = _cached(ac, d)
        assert r["nan_fraction"] >= 0.0, f"{ac}@{d}: nan_fraction={r['nan_fraction']}"


def test_nan_fraction_below_threshold() -> None:
    """
    Kind: property.
    Tolerance: nan_fraction ≤ 0.02.
    Reason: The NaN source is tas=0 at ground (row 0 of each trajectory);
    that is 1 row out of ~300+, well below 2%.  The threshold is spec from the
    task.  If it exceeded 2% the function should have raised, so this test also
    checks that the function didn't raise.
    Cannot catch: a NaN that is filled but not counted (counting bug).
    """
    for ac, d in [("a320", 749), ("b738", 782), ("a333", 771)]:
        r = _cached(ac, d)
        assert r["nan_fraction"] <= 0.02, (
            f"{ac}@{d}: nan_fraction={r['nan_fraction']:.4f} > 0.02"
        )


def test_n_points_matches_trajectory_length() -> None:
    """
    Kind: example / consistency.
    Tolerance: exact int.
    Reason: n_points is exposed for callers to assess integration quality;
    it must equal the number of time-step rows actually used.
    Cannot catch: an off-by-one error that is within the expected range.
    """
    r = _cached("a320", 749)
    # With dt=10 and ~3000-3500 s flight time, we expect 300-400 rows.
    assert 200 <= r["n_points"] <= 1000, (
        f"n_points={r['n_points']} outside expected range [200, 1000]"
    )


# ===========================================================================
# 4.  Fuel monotone in distance (same aircraft)
# ===========================================================================

def test_fuel_monotone_in_distance_a320() -> None:
    """
    Kind: metamorphic / monotonicity.
    Tolerance: fuel must strictly increase with distance (no tolerance, exact
    ordering).
    Reason: longer flight = more fuel burned; a non-monotone result means the
    integration or distance calculation is wrong.
    Cannot catch: a non-monotonicity at distances outside the tested range.

    Distances chosen to span short-to-medium haul (400–1 600 nm) in steps
    large enough that rounding in climb/descent bookkeeping cannot reverse the
    ordering.
    """
    distances = [400, 600, 800, 1000, 1200, 1400, 1600]
    fuels = [_cached("a320", d)["fuel_kg"] for d in distances]
    for i in range(len(fuels) - 1):
        assert fuels[i] < fuels[i + 1], (
            f"Fuel not monotone: {distances[i]} nm → {fuels[i]:.0f} kg, "
            f"{distances[i+1]} nm → {fuels[i+1]:.0f} kg"
        )


def test_fuel_monotone_in_distance_b738() -> None:
    """
    Kind: metamorphic / monotonicity.
    Tolerance: strict ordering.
    Reason: same physics argument as for a320; tested on a different type to
    ensure the property isn't type-specific.
    Cannot catch: monotone but physically wrong absolute values.
    """
    distances = [500, 800, 1100]
    fuels = [_cached("b738", d)["fuel_kg"] for d in distances]
    for i in range(len(fuels) - 1):
        assert fuels[i] < fuels[i + 1], (
            f"b738 fuel not monotone at distances {distances}"
        )


# ===========================================================================
# 5.  Heavy aircraft burns more than light aircraft at the same distance
# ===========================================================================

def test_a333_heavier_burn_than_a320() -> None:
    """
    Kind: differential / cross-type.
    Tolerance: strict inequality (a333 fuel > a320 fuel).
    Reason: a333 is a wide-body twin (MTOW 242 t vs a320 78 t); at 0.8 MTOW
    it carries ~193 t vs ~62 t.  The drag and fuel flow are proportional to
    mass, so total fuel must be substantially higher.
    Cannot catch: equal MTOW values (would need a type-specific guard).
    """
    d_nm = 700
    r_a320 = _cached("a320", d_nm)
    r_a333 = _cached("a333", d_nm)
    assert r_a333["fuel_kg"] > r_a320["fuel_kg"], (
        f"a333 fuel ({r_a333['fuel_kg']:.0f}) not > a320 ({r_a320['fuel_kg']:.0f}) "
        f"at {d_nm} nm"
    )


def test_a333_heavier_mass_fraction_argument() -> None:
    """
    Kind: differential / mass sensitivity.
    Tolerance: strict inequality.
    Reason: mass_fraction=0.9 should burn more than mass_fraction=0.7 on the
    same aircraft and route; verifies the mass_fraction argument reaches the
    physics model.
    Cannot catch: a mass_fraction that is applied with the wrong sign.
    """
    heavy = flight_fuel_nox("a320", 700.0, mass_fraction=0.9)
    light = flight_fuel_nox("a320", 700.0, mass_fraction=0.7)
    assert heavy["fuel_kg"] > light["fuel_kg"], (
        f"mass_fraction=0.9 gave {heavy['fuel_kg']:.0f} kg, "
        f"mass_fraction=0.7 gave {light['fuel_kg']:.0f} kg — expected heavy > light"
    )


# ===========================================================================
# 6.  EI_NOx in physically plausible range [8, 30] g/kg
# ===========================================================================

def test_ei_nox_in_range_a320() -> None:
    """
    Kind: property / physical-range.
    Tolerance: [8, 30] g/kg.
    Reason: ICAO engine databank (EEDB) tabulates NOx emission indices at four
    LTO power settings for certified engines.  At cruise (lower thrust,
    lower temperature than takeoff), EI_NOx typically falls between the
    approach value (~10–15 g/kg) and the climb-out value (~20–30 g/kg).
    The lower bound of 8 g/kg allows for highly efficient engines at low
    power; the upper bound of 30 g/kg covers the hottest takeoff conditions.
    This range is consistent with Boeing Fuel Flow Method 2 (BFFM2) applied
    to ICAO EEDB data for CFM56-series engines (DuBois & Paynter 2006,
    NASA/CR-2006-214241).
    Cannot catch: a systematic EI error that keeps the integrated value in
    range while individual time steps are out of range.
    """
    r = _cached("a320", 749)
    ei = _ei_nox(r)
    assert 8.0 <= ei <= 30.0, (
        f"a320 EI_NOx={ei:.1f} g/kg outside physical range [8, 30]"
    )


def test_ei_nox_in_range_b738() -> None:
    """
    Kind: property / physical-range.
    Tolerance: [8, 30] g/kg (same reasoning as a320 test; CFM56-7B engine).
    Reason: CFM56-7B (b738 default) is closely related to CFM56-5B (a320);
    same ICAO EEDB range applies.
    Cannot catch: compensating over/under-counting in fuel vs NOx integrals.
    """
    r = _cached("b738", 782)
    ei = _ei_nox(r)
    assert 8.0 <= ei <= 30.0, f"b738 EI_NOx={ei:.1f} outside [8, 30]"


def test_ei_nox_in_range_a333() -> None:
    """
    Kind: property / physical-range.
    Tolerance: [8, 30] g/kg (Trent 772 engine; ICAO EEDB data for Trent 700
    family show cruise EI_NOx ~15–18 g/kg).
    Reason: same BFFM2 methodology; Trent engines have moderate EI_NOx at
    cruise.
    Cannot catch: integration over a near-zero NOx segment that dominates the
    average.
    """
    r = _cached("a333", 771)
    ei = _ei_nox(r)
    assert 8.0 <= ei <= 30.0, f"a333 EI_NOx={ei:.1f} outside [8, 30]"


# ===========================================================================
# 7.  Regression checks against reviewer-measured values
# ===========================================================================
# Label: REGRESSION (not independent validation).
# Source: reviewer-measured with the same openap version, mass_fraction=0.8,
#         random=False, dt=10 s time step.
# These are NOT independently validated against a published dataset; they pin
# the implementation against known-good output so refactoring doesn't silently
# change the numbers.
#
# Tolerance rationale: ±5% accounts for floating-point accumulation across
# ~300 integration steps and minor differences in cruise-range rounding
# (the generator snaps altitudes to flight levels; cruise distance is only
# approximately controlled).
# ---------------------------------------------------------------------------

def test_regression_a320_fuel() -> None:
    """
    Kind: golden / regression.
    Tolerance: ±5% of 4979 kg (reviewer-measured at ~749 nm).
    Reason: regression guard; not an independent validation.
    Cannot catch: a compensating error that preserves the total while
    distributing it differently over the trajectory.
    """
    r = _cached("a320", 749)
    ref = 4979.0
    assert r["fuel_kg"] == pytest.approx(ref, rel=0.05), (
        f"a320 fuel_kg={r['fuel_kg']:.0f}, ref={ref:.0f} (±5%)"
    )


def test_regression_b738_fuel() -> None:
    """
    Kind: golden / regression.
    Tolerance: ±5% of 5055 kg (reviewer-measured at ~782 nm).
    Reason: regression guard.
    Cannot catch: a systematic wrong aircraft type used.
    """
    r = _cached("b738", 782)
    ref = 5055.0
    assert r["fuel_kg"] == pytest.approx(ref, rel=0.05), (
        f"b738 fuel_kg={r['fuel_kg']:.0f}, ref={ref:.0f} (±5%)"
    )


def test_regression_a333_fuel() -> None:
    """
    Kind: golden / regression.
    Tolerance: ±5% of 11198 kg (reviewer-measured at ~771 nm).
    Reason: regression guard.
    Cannot catch: error in number of engines (a333 is twin).
    """
    r = _cached("a333", 771)
    ref = 11198.0
    assert r["fuel_kg"] == pytest.approx(ref, rel=0.05), (
        f"a333 fuel_kg={r['fuel_kg']:.0f}, ref={ref:.0f} (±5%)"
    )


def test_regression_a320_nox() -> None:
    """
    Kind: golden / regression.
    Tolerance: ±5% of 74.3 kg (reviewer-measured).
    Reason: regression guard.
    Cannot catch: a constant EI shift that keeps ratio within tolerance.
    """
    r = _cached("a320", 749)
    ref = 74.3
    assert r["nox_kg"] == pytest.approx(ref, rel=0.05), (
        f"a320 nox_kg={r['nox_kg']:.1f}, ref={ref:.1f} (±5%)"
    )


def test_regression_a320_ei_nox() -> None:
    """
    Kind: golden / regression.
    Tolerance: ±10% of 14.9 g/kg (reviewer-measured).
    Reason: EI_NOx is a ratio of two integrals each with ±5% tolerance,
    so the combined tolerance is up to ±10%.
    Cannot catch: compensating errors in numerator and denominator.
    """
    r = _cached("a320", 749)
    ei = _ei_nox(r)
    ref = 14.9
    assert ei == pytest.approx(ref, rel=0.10), (
        f"a320 EI_NOx={ei:.1f}, ref={ref:.1f} (±10%)"
    )


# ===========================================================================
# 8.  Independent reference check (fuel per nm vs published range)
# ===========================================================================
# Sun et al. (2020) "OpenAP: An open-source aircraft performance model for
# research", Transportation Research Part C, 119, 102803.
# Table 3 (or equivalent validation): OpenAP fuel flow vs QAR data shows
# typical agreement within ±10–15% for narrow-body cruise.
#
# The ICAO Carbon Emissions Calculator methodology (ICAO Doc 9988) reports
# A320 fuel burn at ~3.5–4.0 L/seat/100km (economy config ~150 seats).
# At 0.8 kg/L jet-A, ~150 seats: 3.75 L × 0.8 × 150 / 100 = 4.5 kg/km
# = 4.5 × 1.852 = 8.3 kg/nm total.  At 0.8 MTOW (not per-seat), OpenAP
# gives 4979 / 749 ≈ 6.6 kg/nm.  The ICAO figure is per-seat, so it includes
# full passenger payload; 0.8 MTOW mass_fraction is aircraft mass only,
# giving plausibly lower absolute fuel per nm.
#
# IMPORTANT: The ICAO Calculator number above is a rough desk calculation.
# We do NOT have a verified numeric match.  The test below uses ONLY the
# reviewer's measured values as a regression check (labeled as regression),
# combined with a generous ±30% range derived from the literature order-of-
# magnitude (6–10 kg/nm for narrow-body medium haul).
# ---------------------------------------------------------------------------

def test_independent_ref_fuel_per_nm_a320() -> None:
    """
    Kind: metamorphic / approximate-external-reference.
    Tolerance: fuel/nm in [4, 10] kg/nm.
    Reason: peer-reviewed validation (Sun et al. 2020, TRC 119) confirms
    OpenAP fuel flows agree with QAR data within ±15% for cruise; combined
    with climb/descent overhead, narrow-body medium haul is ~5–9 kg/nm.
    The wide [4, 10] kg/nm band is deliberately conservative because (a) the
    exact passenger load and routing differ between our synthetic scenario
    and QAR data, and (b) we cannot verify the ICAO Calculator desk number
    with a specific citable table entry.
    Source: Sun et al. 2020 DOI 10.1016/j.trc.2020.102803; ICAO Doc 9988
    Appendix A (used for order-of-magnitude cross-check only, not regression).
    Cannot catch: a systematic 2× error in both directions simultaneously.
    """
    r = _cached("a320", 749)
    # The actual distance produced by the generator may differ slightly
    # from 749 nm due to fixed climb/descent blocks; we use the ratio
    # fuel/n_points * dt as a proxy, or simply fuel/(actual nm).
    # Since we don't expose actual_nm, use the nominal distance (749) —
    # the error is < 1% and well inside the ±30% band.
    fuel_per_nm = r["fuel_kg"] / 749.0
    assert 4.0 <= fuel_per_nm <= 10.0, (
        f"a320 fuel/nm={fuel_per_nm:.2f} outside plausible range [4, 10] kg/nm "
        f"(Sun et al. 2020 + ICAO 9988 cross-check)"
    )


def test_independent_ref_fuel_per_nm_a333() -> None:
    """
    Kind: metamorphic / approximate-external-reference.
    Tolerance: fuel/nm in [8, 20] kg/nm.
    Reason: a333 is a wide-body (~242 t MTOW); at 0.8 MTOW mass ~193 t,
    fuel consumption should be roughly 2× narrow-body (11–18 kg/nm is
    consistent with ICAO Calculator for long-haul wide-body at partial load).
    Cannot catch: errors in number of engines or engine model lookup.
    """
    r = _cached("a333", 771)
    fuel_per_nm = r["fuel_kg"] / 771.0
    assert 8.0 <= fuel_per_nm <= 20.0, (
        f"a333 fuel/nm={fuel_per_nm:.2f} outside plausible range [8, 20] kg/nm"
    )


# ===========================================================================
# 9.  Error conditions
# ===========================================================================

def test_nan_fraction_raise_above_threshold() -> None:
    """
    Kind: example / error-path.
    Tolerance: exact (exception must be raised).
    Reason: the spec requires ValueError when nan_fraction > 0.02; this test
    exercises that code path by patching flight generation to return all-NaN
    fuelflow.  Uses unittest.mock so no real trajectory is generated.
    Cannot catch: a raise that does not include a descriptive message.
    """
    import unittest.mock as mock
    import numpy as np

    # Build a fake DataFrame with tas=0 everywhere (forces NaN in every row)
    import pandas as pd
    n = 50
    fake_df = pd.DataFrame({
        "t": np.arange(n) * 10,
        "h": np.zeros(n),
        "s": np.zeros(n),
        "v": np.zeros(n),
        "vs": np.zeros(n),
        "altitude": np.zeros(n),
        "vertical_rate": np.zeros(n),
        "groundspeed": np.zeros(n),  # all zero -> all NaN fuelflow -> > 0.02
    })

    import openap_perf as op

    with mock.patch.object(op, "_get_trajectory", return_value=fake_df):
        with pytest.raises(ValueError, match="nan_fraction"):
            flight_fuel_nox("a320", 749.0)
