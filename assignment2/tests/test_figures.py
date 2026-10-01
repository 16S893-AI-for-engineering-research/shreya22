import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest
from PIL import Image

from netenv.core import load_data
from netenv.figures import export_outputs, make_figure

DATA = Path(__file__).resolve().parents[1] / "data/reported_values.json"


def test_real_plot_has_only_original_metrics_and_table7_basecase_heights():
    fig = make_figure(load_data(DATA))
    try:
        assert len(fig.axes) == 1
        ax = fig.axes[0]
        assert [t.get_text() for t in ax.get_xticklabels()] == ["Total fuel burn", "Total NOx", "Total CO", "Total HC"]
        assert ax.get_yscale() == "linear"
        assert ax.get_ylim() == (0, 7e9)
        assert ax.get_ylabel() == "Emission mass / kg"
        assert len(ax.containers) == 2
        # Independent transcription of Table 7. No model validation is implied.
        assert [b.get_height() for b in ax.containers[0]] == [6.71e7, 2.69e9, 7.86e8, 1.49e8]
        assert [b.get_height() for b in ax.containers[1]] == [1.88e8, 5.53e9, 1.90e9, 4.14e8]
        assert [t.get_text() for t in ax.get_legend().get_texts()] == ["Hub-and-spoke network", "City-to-city network"]
        assert all(b.get_y() == 0 for container in ax.containers for b in container)
        assert all(left.get_x() < right.get_x() for left, right in zip(*ax.containers))
    finally:
        plt.close(fig)


def test_plot_order_is_stable_even_if_input_records_are_reordered():
    data = load_data(DATA)
    data["metrics"].reverse()
    fig = make_figure(data)
    try:
        assert [b.get_height() for b in fig.axes[0].containers[0]] == [6.71e7, 2.69e9, 7.86e8, 1.49e8]
    finally:
        plt.close(fig)


def test_export_round_trips_plotted_values_and_preserves_failure(tmp_path):
    report = export_outputs(load_data(DATA), tmp_path)
    assert report["all_passed"] is False
    assert json.loads((tmp_path / "audit.json").read_text()) == report
    with (tmp_path / "totals.csv").open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["metric"] for r in rows] == ["fuel", "NOx", "CO", "HC"]
    assert Decimal(rows[3]["hub_kg_as_printed"]) == Decimal("1.49e8")
    assert Decimal(rows[3]["city_kg_as_printed"]) == Decimal("4.14e8")
    with (tmp_path / "comparisons.csv").open(newline="") as f:
        comparisons = list(csv.DictReader(f))
    assert len(comparisons) == 20
    assert sum(r["passed"] == "False" for r in comparisons) == 4
    image = Image.open(tmp_path / "figure4_repro.png")
    assert image.format == "PNG"
    assert image.width >= 1600 and image.height >= 1000
    root = ET.parse(tmp_path / "figure4_repro.svg").getroot()
    assert root.tag.endswith("svg")
    texts = ["".join(el.itertext()) for el in root.iter() if el.tag.endswith("}text")]
    assert "Total HC" in texts
    assert "Total CO2" not in texts
    manifest = json.loads((tmp_path / "run_manifest.json").read_text())
    for name, digest in manifest["outputs_sha256"].items():
        assert hashlib.sha256((tmp_path / name).read_bytes()).hexdigest() == digest


def test_two_exports_are_byte_identical_in_same_locked_environment(tmp_path):
    data = load_data(DATA)
    export_outputs(data, tmp_path / "first")
    export_outputs(data, tmp_path / "second")
    for name in ["figure4_repro.svg", "figure4_repro.png", "totals.csv", "comparisons.csv", "audit.json", "run_manifest.json"]:
        assert (tmp_path / "first" / name).read_bytes() == (tmp_path / "second" / name).read_bytes(), name


def test_published_table7_matches_input_transcription():
    # Independent source extraction catches transcribing a substituted-aircraft column
    # or replacing the table's HC with the conflicting narrative/annotation values.
    source = DATA.parent / "sources/sun2021.xml"
    root = ET.parse(source).getroot()
    table = root.find(".//table-wrap[@id='sustainability-13-00465-t007']")
    assert table is not None
    source_rows = table.findall("./table/tbody/tr")[:4]
    expected = [("6.71", "7", "1.88", "8"), ("2.69", "9", "5.53", "9"),
                ("7.86", "8", "1.90", "9"), ("1.49", "8", "4.14", "8")]
    for row, (hm, he, cm, ce), metric in zip(source_rows, expected, load_data(DATA)["metrics"], strict=True):
        cells = row.findall("td")
        hub, city = cells[1], cells[4]
        assert hub.text.strip() == hm + " × 10"
        assert hub.findtext("sup") == he
        assert city.text.strip() == cm + " × 10"
        assert city.findtext("sup") == ce
        assert Decimal(metric["table7_hub"]) == Decimal(hm) * 10 ** int(he)
        assert Decimal(metric["table7_city"]) == Decimal(cm) * 10 ** int(ce)
