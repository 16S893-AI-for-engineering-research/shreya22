"""Independent arithmetic fixtures plus scale/round-trip properties.

These establish code behavior, NOT validation of the published physical model.
"""
import json
from decimal import Decimal as D
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from netenv.core import (
    audit, convert_mass, load_data, reduction_percent, relative_error, within_tolerance,
)

DATA = Path(__file__).resolve().parents[1] / "data/reported_values.json"


@pytest.mark.parametrize("hub,city,expected", [
    ("25", "100", "75"), ("0", "100", "100"),
    ("100", "100", "0"), ("125", "100", "-25"),
])
def test_reduction_uses_city_baseline_and_allows_increases(hub, city, expected):
    assert reduction_percent(D(hub), D(city)) == D(expected)


@pytest.mark.parametrize("hub,city", [
    ("1", "0"), ("-1", "10"), ("1", "-10"),
    ("NaN", "10"), ("1", "Infinity"),
])
def test_reduction_rejects_undefined_or_unphysical_inputs(hub, city):
    with pytest.raises(ValueError):
        reduction_percent(D(hub), D(city))


@pytest.mark.parametrize("value,source,target,expected", [
    ("2500", "g", "kg", "2.5"), ("2.5", "kg", "g", "2500"),
    ("1.25", "t", "kg", "1250"), ("0", "g", "t", "0"),
])
def test_conversion_handles_three_orders_of_magnitude(value, source, target, expected):
    assert convert_mass(D(value), source, target) == D(expected)


@pytest.mark.parametrize("value,source,target", [
    ("-1", "kg", "g"), ("NaN", "kg", "g"),
    ("Infinity", "kg", "t"), ("1", "lb", "kg"),
])
def test_conversion_rejects_invalid_mass_or_unknown_unit(value, source, target):
    with pytest.raises(ValueError):
        convert_mass(D(value), source, target)


@settings(max_examples=100, derandomize=True)
@given(st.integers(min_value=0, max_value=10**12), st.sampled_from(["g", "kg", "t"]))
def test_mass_conversion_round_trip_preserves_value(amount, unit):
    kg = convert_mass(D(amount), unit, "kg")
    assert convert_mass(kg, "kg", unit) == D(amount)


@settings(max_examples=100, derandomize=True)
@given(st.integers(0, 10**9), st.integers(1, 10**9), st.integers(-6, 6))
def test_reduction_is_invariant_to_common_unit_scale(hub, city, exponent):
    scale = D(10) ** exponent
    assert reduction_percent(D(hub) * scale, D(city) * scale) == reduction_percent(D(hub), D(city))


@pytest.mark.parametrize("actual,reference,error,passes", [
    ("100.1", "100", "0.001", True),
    ("100.10001", "100", "0.0010001", False),
    ("99.9", "100", "0.001", True),
    ("0", "0", "0", True),
    ("0.0000001", "0", "Infinity", False),
    ("-100.1", "-100", "0.001", True),
])
def test_relative_threshold_is_inclusive_without_hidden_absolute_tolerance(actual, reference, error, passes):
    assert relative_error(D(actual), D(reference)) == D(error)
    assert within_tolerance(D(actual), D(reference)) is passes


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_comparison_rejects_nonfinite_observations(value):
    with pytest.raises(ValueError):
        relative_error(D(value), D("1"))


def test_loader_preserves_conflicting_sources_and_three_significant_digits():
    data = load_data(DATA)
    hc = data["metrics"][3]
    assert hc["table7_hub"] == "1.49e8"
    assert hc["prose_hub"] == "1.48e8"
    assert hc["table7_city"] == "4.14e8"
    assert hc["figure4_city"] == "4.14e9"


@pytest.mark.parametrize("mutation", ["negative", "missing", "duplicate", "extra-metric", "nonfinite"])
def test_loader_rejects_invalid_or_out_of_scope_records(tmp_path, mutation):
    data = json.loads(DATA.read_text())
    if mutation == "negative":
        data["metrics"][0]["table7_hub"] = "-1"
    elif mutation == "missing":
        del data["metrics"][0]["figure4_city"]
    elif mutation == "duplicate":
        data["metrics"][1]["key"] = "fuel"
    elif mutation == "extra-metric":
        data["metrics"].append({**data["metrics"][0], "key": "CO2"})
    else:
        data["metrics"][0]["table7_city"] = "NaN"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        load_data(path)


def test_json_round_trip_does_not_change_audit(tmp_path):
    data = load_data(DATA)
    path = tmp_path / "roundtrip.json"
    path.write_text(json.dumps(data))
    assert audit(load_data(path)) == audit(data)


def test_audit_does_not_hide_paper_disagreements():
    report = audit(load_data(DATA))
    failed = {(c["kind"], c["metric"], c["network"]) for c in report["checks"] if not c["passed"]}
    assert failed == {
        ("figure4_label", "HC", "city"),
        ("prose_total", "HC", "hub"),
        ("reduction", "fuel", "both"),
        ("reduction", "HC", "both"),
    }
    assert len(report["checks"]) == 20
    assert report["all_passed"] is False
    assert report["relative_tolerance"] == "0.001"
    reductions = {c["metric"]: c for c in report["checks"] if c["kind"] == "reduction"}
    # Hand-checked from independent ratios: 1209/1880 and 265/414.
    assert D(reductions["fuel"]["actual"]) == pytest.approx(D("64.30851063829787234042553191"))
    assert D(reductions["HC"]["actual"]) == pytest.approx(D("64.00966183574879227053140097"))
    assert reductions["NOx"]["passed"] is True
    assert reductions["CO"]["passed"] is True
    figure_hc = next(c for c in report["checks"] if c["kind"] == "figure4_label" and c["metric"] == "HC" and c["network"] == "city")
    assert D(figure_hc["relative_error_percent"]) == D("90")


def test_audit_order_does_not_change_comparisons():
    data = load_data(DATA)
    first = audit(data)
    data["metrics"].reverse()
    second = audit(data)
    key = lambda c: (c["kind"], c["metric"], c["network"])
    assert sorted(first["checks"], key=key) == sorted(second["checks"], key=key)
