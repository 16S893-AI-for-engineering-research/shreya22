"""Regenerate outputs; optionally signal strict paper-agreement failure."""
import argparse
from decimal import Decimal
from pathlib import Path

from .core import load_data
from .figures import export_outputs
from .page import render_page

PROJECT = Path(__file__).resolve().parents[2]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reconstruct Sun et al. Figure 4 and audit published values at 0.1% relative error.")
    parser.add_argument("--data", type=Path, default=PROJECT / "data/reported_values.json")
    parser.add_argument("--output-dir", type=Path, default=PROJECT / "outputs")
    parser.add_argument("--page", type=Path, help="Also generate the standalone portfolio HTML page at this path")
    parser.add_argument("--check-paper", action="store_true", help="Exit 1 when any published-value comparison fails; still write the audit")
    args = parser.parse_args(argv)
    try:
        data = load_data(args.data)
        report = export_outputs(data, args.output_dir)
        if args.page is not None:
            content = render_page(data, report, args.page, args.output_dir)
            args.page.parent.mkdir(parents=True, exist_ok=True)
            args.page.write_text(content, encoding="utf-8")
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    passed = sum(c["passed"] for c in report["checks"])
    print(f"{passed}/{len(report['checks'])} source-consistency checks pass at 0.1% relative error.")
    for check in report["checks"]:
        if not check["passed"]:
            print(f"FAIL {check['kind']} {check['metric']} {check['network']}: relative error {Decimal(check['relative_error_percent']):.6f}%")
    print("Strict paper agreement: " + ("achieved." if report["all_passed"] else "NOT achieved."))
    print("Scope: reported-aggregate reconstruction; no network or aircraft model rerun.")
    print(f"Outputs: {args.output_dir}")
    return 1 if args.check_paper and not report["all_passed"] else 0
