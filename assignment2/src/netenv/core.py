"""Exact-decimal arithmetic and explicitly separate source-consistency checks."""
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

TOLERANCE = Decimal("0.001")  # 0.1% relative, not 0.1 percentage points.
MASS_FIELDS = tuple(f"{source}_{network}" for source in ("table7", "figure4", "prose") for network in ("hub", "city"))


def _finite(value: Decimal) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Expected a finite decimal number") from exc
    if not number.is_finite():
        raise ValueError("Expected a finite decimal number")
    return number


def _mass(value: Decimal) -> Decimal:
    number = _finite(value)
    if number < 0:
        raise ValueError("Mass cannot be negative")
    return number


def load_data(path: Path) -> dict:
    """Keep source strings intact; reject missing/invalid/out-of-scope records."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        metrics = data["metrics"]
        if len(metrics) != 4 or {m["key"] for m in metrics} != {"fuel", "NOx", "CO", "HC"}:
            raise ValueError("Expected exactly fuel, NOx, CO, and HC")
        for metric in metrics:
            if not isinstance(metric["label"], str):
                raise ValueError("Metric label must be text")
            for field in (*MASS_FIELDS, "reported_reduction_percent"):
                if not isinstance(metric[field], str):
                    raise ValueError("Use decimal strings to preserve printed precision")
                (_mass if field in MASS_FIELDS else _finite)(metric[field])
            if Decimal(metric["table7_city"]) == 0:
                raise ValueError("City baseline must be positive")
    except (KeyError, TypeError) as exc:
        raise ValueError("Incomplete or malformed source records") from exc
    return data


def reduction_percent(hub: Decimal, city: Decimal) -> Decimal:
    """Percent less than the city baseline; negative means the hub case is worse."""
    hub, city = _mass(hub), _mass(city)
    if city == 0:
        raise ValueError("Reduction is undefined for a zero city baseline")
    return Decimal(100) * (city - hub) / city


def relative_error(actual: Decimal, reference: Decimal) -> Decimal:
    actual, reference = _finite(actual), _finite(reference)
    if reference == 0:
        return Decimal(0) if actual == 0 else Decimal("Infinity")
    return abs(actual - reference) / abs(reference)


def within_tolerance(actual: Decimal, reference: Decimal) -> bool:
    return relative_error(actual, reference) <= TOLERANCE


def convert_mass(value: Decimal, source_unit: str, target_unit: str) -> Decimal:
    """Explicit unit utility; never applied as a hidden correction to source data."""
    factors = {"g": Decimal("0.001"), "kg": Decimal(1), "t": Decimal(1000)}
    if source_unit not in factors or target_unit not in factors:
        raise ValueError("Supported mass units are g, kg, and t")
    return _mass(value) * factors[source_unit] / factors[target_unit]


def _comparison(kind: str, metric: str, network: str, actual: Decimal, reference: Decimal) -> dict:
    return {
        "kind": kind, "metric": metric, "network": network,
        "actual": str(actual), "reference": str(reference),
        "difference": str(actual - reference),
        "relative_error_percent": str(Decimal(100) * relative_error(actual, reference)),
        "passed": within_tolerance(actual, reference),
    }


def audit(data: dict) -> dict:
    """Compare reported aggregates/claims, without pretending to recompute physics."""
    checks = []
    for metric in data["metrics"]:
        for source, kind in (("figure4", "figure4_label"), ("prose", "prose_total")):
            for network in ("hub", "city"):
                checks.append(_comparison(
                    kind, metric["key"], network,
                    Decimal(metric[f"table7_{network}"]),
                    Decimal(metric[f"{source}_{network}"]),
                ))
        checks.append(_comparison(
            "reduction", metric["key"], "both",
            reduction_percent(Decimal(metric["table7_hub"]), Decimal(metric["table7_city"])),
            Decimal(metric["reported_reduction_percent"]),
        ))
    return {
        "doi": data["doi"],
        "method": "Reported-aggregate reconstruction and consistency audit; no physical model rerun",
        "canonical_source": data["canonical_source"],
        "relative_tolerance": str(TOLERANCE),
        "all_passed": all(c["passed"] for c in checks),
        "checks": checks,
    }
