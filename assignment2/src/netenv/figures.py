"""A disclosed reconstruction of Figure 4 using Table 7 base-case values."""
import csv
import hashlib
import json
import platform
from decimal import Decimal
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from .core import audit, reduction_percent

ORDER = ("fuel", "NOx", "CO", "HC")
STYLE = {
    "font.family": "DejaVu Serif", "font.size": 12,
    "svg.fonttype": "none", "svg.hashsalt": "sun2021-figure4",
    "axes.linewidth": 1.2,
}


def _ordered_metrics(data: dict) -> list[dict]:
    lookup = {m["key"]: m for m in data["metrics"]}
    return [lookup[key] for key in ORDER]


def _label(value: str) -> str:
    mantissa, exponent = f"{Decimal(value):.2E}".split("E")
    superscript = str(int(exponent)).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
    return f"{mantissa} × 10{superscript}"


def make_figure(data: dict):
    metrics = _ordered_metrics(data)
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(12, 8))
        fig.subplots_adjust(left=0.12, right=0.98, bottom=0.19, top=0.89)
        for network, offset, color, name in (
            ("hub", -0.20, "#f46a1a", "Hub-and-spoke network"),
            ("city", 0.20, "#699dcc", "City-to-city network"),
        ):
            values = [m[f"table7_{network}"] for m in metrics]
            bars = ax.bar(
                [i + offset for i in range(4)], [float(Decimal(v)) for v in values],
                width=0.34, color=color, edgecolor="black", linewidth=1.2, label=name,
            )
            ax.bar_label(bars, labels=[_label(v) for v in values], rotation=90, padding=6, fontsize=11)
        ax.set_xticks(range(4), [m["label"] for m in metrics])
        ax.set_ylabel("Emission mass / kg", labelpad=12)
        ax.set_ylim(0, 7e9)
        ax.set_yticks([0, 2e9, 4e9, 6e9])
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: "0.0" if value == 0 else f"{value / 1e9:.1f} × 10⁹"))
        ax.set_xlim(-0.55, 3.55)
        ax.legend(loc="upper right", fontsize=11, framealpha=1, edgecolor="black")
        ax.set_title("The total environmental impact of two airline networks", fontsize=15, pad=18)
        fig.text(0.12, 0.09, "Figure 4 reconstruction • Sun et al. (2021), doi:10.3390/su13020465", fontsize=10)
        fig.text(0.12, 0.057, "Table 7 base-case values; kg retained as printed. HC annotation conflict is documented, not silently corrected.", fontsize=9)
        fig.text(0.12, 0.030, "Reported aggregates, not an independent rerun of the network or aircraft-emissions model.", fontsize=9)
    return fig


def export_outputs(data: dict, output_dir: Path) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics = _ordered_metrics(data)
    report = audit({**data, "metrics": metrics})
    (output_dir / "audit.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    with (output_dir / "comparisons.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(report["checks"][0]))
        writer.writeheader()
        writer.writerows(report["checks"])
    with (output_dir / "totals.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["metric", "hub_kg_as_printed", "city_kg_as_printed", "recomputed_reduction_percent", "source"])
        for metric in metrics:
            writer.writerow([
                metric["key"], metric["table7_hub"], metric["table7_city"],
                str(reduction_percent(Decimal(metric["table7_hub"]), Decimal(metric["table7_city"]))),
                data["canonical_source"],
            ])
    fig = make_figure(data)
    try:
        with plt.rc_context(STYLE):
            fig.savefig(output_dir / "figure4_repro.svg", metadata={"Date": None}, facecolor="white")
            fig.savefig(output_dir / "figure4_repro.png", dpi=160, facecolor="white")
    finally:
        plt.close(fig)
    names = ("figure4_repro.svg", "figure4_repro.png", "totals.csv", "comparisons.csv", "audit.json")
    project = Path(__file__).resolve().parents[2]
    inputs = [project / "pyproject.toml", project / "uv.lock", *sorted((project / "src/netenv").glob("*.py"))]
    manifest = {
        "python": platform.python_version(), "matplotlib": matplotlib.__version__,
        "normalized_input_sha256": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
        "code_environment_sha256": {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        "outputs_sha256": {name: hashlib.sha256((output_dir / name).read_bytes()).hexdigest() for name in names},
    }
    (output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return report
