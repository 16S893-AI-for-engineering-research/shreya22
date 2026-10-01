"""Render a static, accessible page from the exact same inputs and audit."""
import os
from decimal import Decimal
from html import escape
from pathlib import Path
from string import Template
from urllib.parse import quote

from .figures import ORDER

PROJECT = Path(__file__).resolve().parents[2]
SITE = PROJECT.parent


def _scientific(value: str) -> str:
    mantissa, exponent = f"{Decimal(value):.2E}".split("E")
    return f"{mantissa} × 10<sup>{int(exponent)}</sup>"


def _row(cells: list[str]) -> str:
    return '<tr><th scope="row">' + cells[0] + "</th>" + "".join(f"<td>{cell}</td>" for cell in cells[1:]) + "</tr>\n"


def render_page(data: dict, report: dict, page_path: Path, output_dir: Path) -> str:
    def link(path: Path) -> str:
        return escape(quote(os.path.relpath(path.resolve(), Path(page_path).resolve().parent).replace(os.sep, "/"), safe="/"), quote=True)

    metrics = {m["key"]: m for m in data["metrics"]}
    totals_rows = "".join(_row([
        escape(metrics[key]["label"]), _scientific(metrics[key]["table7_hub"]),
        _scientific(metrics[key]["table7_city"]),
    ]) for key in ORDER)
    reductions = {c["metric"]: c for c in report["checks"] if c["kind"] == "reduction"}
    reduction_rows = ""
    for key in ORDER:
        check = reductions[key]
        badge = '<span class="pass">PASS</span>' if check["passed"] else '<span class="fail">FAIL</span>'
        reduction_rows += _row([
            escape(metrics[key]["label"]), f'{Decimal(check["reference"]):.2f}%',
            f'{Decimal(check["actual"]):.6f}%', f'{Decimal(check["difference"]):+.6f}',
            f'{Decimal(check["relative_error_percent"]):.6f}%', badge,
        ])
    discrepancy_rows = ""
    for check in report["checks"]:
        if check["kind"] == "reduction" or check["passed"]:
            continue
        source = "Figure 4 annotation" if check["kind"] == "figure4_label" else "Section 4.3 prose"
        network = "hub-and-spoke" if check["network"] == "hub" else "city-to-city"
        discrepancy_rows += _row([
            f'{escape(check["metric"])} / {network}', _scientific(check["actual"]),
            _scientific(check["reference"]), source, f'{Decimal(check["relative_error_percent"]):.6f}%',
        ])
    paths = {
        "site_css": SITE / "css/style.css", "assignment_css": SITE / "css/assignment2.css",
        "layout_js": SITE / "js/layout.js", "project_nav_js": SITE / "js/project-nav.js",
        "project_link": SITE / "project.html", "site_devlog": SITE / "devlog.html",
        "figure_svg": Path(output_dir) / "figure4_repro.svg", "figure_png": Path(output_dir) / "figure4_repro.png",
        "totals_csv": Path(output_dir) / "totals.csv", "comparisons_csv": Path(output_dir) / "comparisons.csv",
        "audit_json": Path(output_dir) / "audit.json", "run_manifest": Path(output_dir) / "run_manifest.json",
        "original_figure": PROJECT / "data/sources/figure4-original.png",
        "source_data": PROJECT / "data/reported_values.json", "source_xml": PROJECT / "data/sources/sun2021.xml",
        "source_manifest": PROJECT / "data/sources/manifest.json", "citation_file": PROJECT / "CITATIONS.bib",
        "readme_doc": PROJECT / "README.md", "skills_doc": PROJECT / "SKILLS.md",
        "spec_doc": PROJECT / "SPECIFICATION.md", "devlog_doc": PROJECT / "DEVLOG.md",
        "evidence_doc": PROJECT / "evidence/README.md",
    }
    context = {key: link(path) for key, path in paths.items()}
    context.update({
        "totals_rows": totals_rows, "reduction_rows": reduction_rows, "discrepancy_rows": discrepancy_rows,
        "passed_checks": sum(c["passed"] for c in report["checks"]),
        "total_checks": len(report["checks"]),
        "status_heading": "First pass complete · strict agreement " + ("achieved" if report["all_passed"] else "not achieved"),
    })
    return Template((PROJECT / "templates/page.html").read_text(encoding="utf-8")).substitute(context)
