import hashlib
import json
import os
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from netenv.core import audit, load_data
from netenv.page import render_page

PROJECT = Path(__file__).resolve().parents[1]
DATA = PROJECT / "data/reported_values.json"


class PageContent(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.images = []
        self.tables = {}
        self.table = None
        self.cell = None
        self.row = None
        self.ids = []
        self.tags = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append(tag)
        if "id" in attrs:
            self.ids.append(attrs["id"])
        for attr in ("href", "src"):
            if attr in attrs:
                self.links.append(attrs[attr])
        if tag == "img":
            self.images.append(attrs)
        if tag == "table":
            self.table = attrs.get("id")
            self.tables[self.table] = []
        if self.table and tag == "tr":
            self.row = []
        if self.table and tag in ("td", "th"):
            self.cell = ""

    def handle_data(self, text):
        if self.cell is not None:
            self.cell += text

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append(self.cell.strip())
            self.cell = None
        if tag == "tr" and self.table:
            self.tables[self.table].append(self.row)
        if tag == "table":
            self.table = None


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "netenv", *map(str, args)], cwd=PROJECT,
        capture_output=True, text=True, env={**os.environ, "MPLBACKEND": "Agg"},
    )


def test_cli_builds_page_data_and_honest_numerical_tables(tmp_path):
    output = tmp_path / "artifacts"
    page = tmp_path / "page.html"
    result = run_cli("--output-dir", output, "--page", page)
    assert result.returncode == 0, result.stderr
    assert "16/20" in result.stdout
    assert "0.1% relative" in result.stdout
    assert "NOT achieved" in result.stdout
    parser = PageContent()
    parser.feed(page.read_text())
    assert len(parser.ids) == len(set(parser.ids))
    assert parser.tables["reductions-table"][1] == ["Total fuel burn", "64.38%", "64.308511%", "-0.071489", "0.111043%", "FAIL"]
    assert parser.tables["reductions-table"][2][-1] == "PASS"
    assert parser.tables["reductions-table"][3][-1] == "PASS"
    assert parser.tables["reductions-table"][4][-1] == "FAIL"
    assert parser.tables["totals-table"][4] == ["Total HC", "1.49 × 108", "4.14 × 108"]
    assert len(parser.images) == 2
    assert all(len(image.get("alt", "")) > 30 for image in parser.images)
    for link in parser.links:
        parsed = urlsplit(link)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        assert (page.parent / unquote(parsed.path)).resolve().exists(), link


def test_strict_cli_returns_failure_for_publication_not_successful_rendering(tmp_path):
    result = run_cli("--output-dir", tmp_path, "--check-paper")
    assert result.returncode == 1
    assert "0.1% relative" in result.stdout
    assert "FAIL reduction fuel" in result.stdout
    assert "FAIL reduction HC" in result.stdout
    assert "FAIL figure4_label HC city" in result.stdout
    assert "FAIL prose_total HC hub" in result.stdout
    assert "Traceback" not in result.stderr
    assert json.loads((tmp_path / "audit.json").read_text())["all_passed"] is False


def test_invalid_input_is_rejected_before_outputs_are_created(tmp_path):
    data = json.loads(DATA.read_text())
    data["metrics"][0]["table7_hub"] = "NaN"
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data))
    result = run_cli("--data", path, "--output-dir", tmp_path / "outputs")
    assert result.returncode == 2
    assert "finite" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "outputs").exists()


def test_page_escapes_source_labels(tmp_path):
    data = load_data(DATA)
    data["metrics"][0]["label"] = '<script>alert("injected")</script>'
    page = render_page(data, audit(data), tmp_path / "page.html", tmp_path / "outputs")
    assert '<script>alert("injected")</script>' not in page
    assert "&lt;script&gt;" in page


def test_archived_sources_match_acquisition_hashes_and_doi_metadata():
    sources = PROJECT / "data/sources"
    manifest = json.loads((sources / "manifest.json").read_text())
    for item in manifest["files"]:
        assert hashlib.sha256((sources / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    metadata = json.loads((sources / "crossref.json").read_text())["message"]
    assert metadata["DOI"] == load_data(DATA)["doi"]
    assert metadata["author"][0]["family"] == "Sun"
    assert metadata["published"]["date-parts"] == [[2021, 1, 6]]
    assert metadata["license"][0]["URL"] == "https://creativecommons.org/licenses/by/4.0/"
