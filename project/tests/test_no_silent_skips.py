"""
Regression guard: no test module may use allow_module_level=True skips.

What KIND of test this is: property / invariance test over the test source files.
Tolerance: none (exact text search).
Reason: a pytest.skip(allow_module_level=True) in a test module silently drops
every test in that module from the suite and exits 0, making the CI appear
green even when the entire module is uncollectible.  This is equivalent to
having no tests at all for that module while pretending they still exist.
The pattern is almost always a try/except ImportError guard wrapping a top-
level import — the correct fix is a plain import so pytest reports a
collection ERROR (non-zero exit) when the module is missing or renamed.
Cannot catch: a skip expressed as a decorator (@pytest.mark.skip) or a
pytest.skip() call INSIDE a test function body — those are legitimate uses
and are not covered by this scan.
"""
from __future__ import annotations

import pathlib


def test_no_allow_module_level_skips() -> None:
    """
    Property test — scans all project/tests/*.py files for the literal text
    'allow_module_level=True'.  Fails if any file contains that string.

    Kind: property / invariance.
    Tolerance: none (text-search, no numeric tolerance).
    Reason: described in module docstring above.
    Cannot catch: skips inside function bodies or via skip markers.
    """
    tests_dir = pathlib.Path(__file__).resolve().parent
    offenders: list[str] = []

    _this_file = pathlib.Path(__file__).resolve()
    for py_file in sorted(tests_dir.glob("*.py")):
        if py_file.resolve() == _this_file:
            # skip self — the docstring above necessarily contains the pattern
            continue
        text = py_file.read_text(encoding="utf-8")
        if "allow_module_level=True" in text:
            offenders.append(py_file.name)

    assert not offenders, (
        "The following test file(s) contain 'allow_module_level=True', which "
        "causes the entire module to be silently skipped when an import fails. "
        "Replace the try/except ImportError guard with a plain import so pytest "
        "reports a collection ERROR instead of a silent skip:\n"
        + "\n".join(f"  project/tests/{f}" for f in offenders)
    )


def test_figures_uses_plain_import() -> None:
    """
    Targeted regression test — verifies that test_figures.py specifically uses
    a plain 'from figures import bar_compare' and not a try/except ImportError
    guard with allow_module_level=True.

    Kind: example / targeted regression.
    Tolerance: none (text-search).
    Reason: this is the exact file that previously had the silent-skip guard;
    a targeted check ensures no property-scan bypass (e.g. an exclusion
    whitelist added to the property scan) can hide a re-introduction of the
    guard in this specific file.
    Cannot catch: a skip expressed as pytest.mark.skip or inside a function.
    """
    tests_dir = pathlib.Path(__file__).resolve().parent
    target = tests_dir / "test_figures.py"
    assert target.exists(), "test_figures.py is missing"
    text = target.read_text(encoding="utf-8")
    assert "allow_module_level=True" not in text, (
        "test_figures.py contains 'allow_module_level=True'.  "
        "The try/except ImportError guard must not be re-introduced; "
        "use a plain 'from figures import bar_compare' so a missing "
        "figures.py causes a collection ERROR, not a silent skip."
    )
