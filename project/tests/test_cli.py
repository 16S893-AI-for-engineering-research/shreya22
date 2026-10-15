"""
Tests for project/src/cli.py: the P2P vs hub-and-spoke comparison CLI.

All subprocess tests set cwd=project/src and use sys.executable so the
correct venv Python is used.  Output directories are always tmp_path so
nothing is written to the repo tree.

Test kinds used here:
  - Example: concrete single-case assertions about outputs.
  - Differential: compare CLI output values with direct library calls
    (network_totals on same routes), rel_tol=1e-12.
  - Invariance: two runs with identical inputs produce byte-identical files.
  - Metamorphic: swapping hub order changes (or not) the results in the
    documented way.
  - Negative/failure-mode: verify exit codes, stderr messages, empty out-dirs.
"""
from __future__ import annotations

import hashlib
import json
import math
import pathlib
import subprocess
import sys
import csv

import pytest

# ── make project/src importable for in-process calls ─────────────────────
_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from networks import load_demand, build_routes_point_to_point, build_routes_hub_spoke
from totals import network_totals

_FIXTURE = _THIS_DIR / "fixtures" / "tiny_demand.csv"
_HUBS_DEFAULT = "JFK,ORD"
_SRC_DIR_STR = str(_SRC_DIR)


# ── helpers ────────────────────────────────────────────────────────────────

def _sha256(path: pathlib.Path) -> str:
    """Return hex-digest SHA-256 of a file."""
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _run_cli(args: list[str], *, out_dir: pathlib.Path | None = None) -> subprocess.CompletedProcess:
    """Run the CLI via subprocess, returning the CompletedProcess."""
    cmd = [sys.executable, "-m", "cli"] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=_SRC_DIR_STR,
    )


def _run_success(tmp_path: pathlib.Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Run a normal success-path CLI invocation into tmp_path."""
    out = tmp_path / "out"
    out.mkdir()
    args = [
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ]
    if extra_args:
        args += extra_args
    return _run_cli(args)


def _expected_files() -> list[str]:
    return [
        "totals.json",
        "totals.csv",
        "WARNING.txt",
        "manifest.json",
        "passenger_legs.svg",
        "passenger_legs.png",
        "fuel_kg.svg",
        "fuel_kg.png",
    ]


# ══════════════════════════════════════════════════════════════════════════
# 1. SUCCESS PATH — exit 0, exact file set present
# ══════════════════════════════════════════════════════════════════════════

def test_success_exits_zero(tmp_path: pathlib.Path) -> None:
    """
    Example test — no tolerance.

    The CLI must exit with code 0 when given a valid demand file, valid hubs,
    and a writable output directory.

    Cannot catch: correct exit code masking an internally wrong computation.
    """
    result = _run_success(tmp_path)
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_success_writes_all_expected_files(tmp_path: pathlib.Path) -> None:
    """
    Example test — no tolerance.

    After a successful run, exactly the documented output files must exist in
    --out.  Missing files break downstream consumers; extra unexpected files
    are tested separately via the manifest.

    Cannot catch: files present but empty or corrupt.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, f"CLI failed: {r.stderr}"

    for fname in _expected_files():
        assert (out / fname).exists(), f"Expected output file missing: {fname}"


# ══════════════════════════════════════════════════════════════════════════
# 2. DIFFERENTIAL — JSON values match library calls exactly (rel 1e-12)
# ══════════════════════════════════════════════════════════════════════════

def test_json_values_match_library(tmp_path: pathlib.Path) -> None:
    """
    Differential test — tolerance: rel_tol=1e-12 per key.
    Reason: CLI and library share the same code paths; only float-serialisation
    round-trips introduce error.  JSON uses 17 significant digits by default,
    so rel_tol 1e-12 is achievable.

    The CLI totals.json must match network_totals() called directly on the same
    routes with the default seats_per_flight=150 and lto_share=0.1.

    Cannot catch: consistent errors in both the library and the CLI output
    parsing.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr

    # Compute expected values via library
    demand = load_demand(str(_FIXTURE))
    p2p_routes = build_routes_point_to_point(demand)
    hs_routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    expected_p2p = network_totals(p2p_routes)
    expected_hs = network_totals(hs_routes)

    data = json.loads((out / "totals.json").read_text())
    p2p_out = data["p2p"]
    hs_out = data["hub_spoke"]

    keys = ["passengers", "passenger_legs", "flights", "aircraft_nm",
            "fuel_kg", "NOx_kg", "CO_kg", "HC_kg"]
    for k in keys:
        assert math.isclose(p2p_out[k], expected_p2p[k], rel_tol=1e-12), (
            f"p2p[{k!r}] mismatch: got {p2p_out[k]}, expected {expected_p2p[k]}"
        )
        assert math.isclose(hs_out[k], expected_hs[k], rel_tol=1e-12), (
            f"hub_spoke[{k!r}] mismatch: got {hs_out[k]}, expected {expected_hs[k]}"
        )


# ══════════════════════════════════════════════════════════════════════════
# 3. DETERMINISM — two runs into different dirs give byte-identical files
# ══════════════════════════════════════════════════════════════════════════

def test_byte_identical_across_runs(tmp_path: pathlib.Path) -> None:
    """
    Invariance / determinism test — tolerance: exact byte equality.
    Reason: the spec requires 'identical inputs give byte-identical files'.
    This catches any timestamp, random salt, or process-level non-determinism.
    We run twice and compare every output file byte-for-byte.

    Caveat: byte identity is guaranteed only within the same locked
    environment (same matplotlib, pillow, etc.).  The test is meaningful
    only within a single pytest session on the same machine.

    Cannot catch: non-determinism introduced by external state (e.g., system
    PRNG seeded from /dev/urandom in a dependency we don't control).
    """
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    common_args = [
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out",  # filled below
    ]
    r1 = _run_cli(common_args + [str(out1)])
    r2 = _run_cli(common_args + [str(out2)])
    assert r1.returncode == 0, f"Run 1 failed: {r1.stderr}"
    assert r2.returncode == 0, f"Run 2 failed: {r2.stderr}"

    for fname in _expected_files():
        b1 = (out1 / fname).read_bytes()
        b2 = (out2 / fname).read_bytes()
        assert b1 == b2, (
            f"File {fname!r} is not byte-identical across two runs "
            f"(sizes: {len(b1)} vs {len(b2)}).  "
            "Check for embedded timestamps, random ids, or absolute paths."
        )


# ══════════════════════════════════════════════════════════════════════════
# 4. MANIFEST — hashes match actual files, required keys present
# ══════════════════════════════════════════════════════════════════════════

def test_manifest_hashes_match_files(tmp_path: pathlib.Path) -> None:
    """
    Example / integrity test — tolerance: exact string equality of hex digests.

    manifest.json must list every output file with its SHA-256.  We recompute
    each hash and compare.  A mismatch means either the manifest is stale or
    a file was written after the manifest was computed.

    Cannot catch: hash collisions (negligible probability for SHA-256).
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr

    manifest = json.loads((out / "manifest.json").read_text())
    outputs = manifest["outputs"]

    for fname, info in outputs.items():
        fpath = out / fname
        assert fpath.exists(), f"Manifest references {fname!r} but file missing"
        actual_hash = _sha256(fpath)
        assert actual_hash == info["sha256"], (
            f"SHA-256 mismatch for {fname!r}: "
            f"manifest says {info['sha256']!r}, actual {actual_hash!r}"
        )


def test_manifest_contains_required_keys(tmp_path: pathlib.Path) -> None:
    """
    Example test — no tolerance.

    manifest.json must contain at minimum:
      - 'demand'  with 'path' and 'sha256'
      - 'hubs'    list
      - 'parameters' dict with at least 'seats_per_flight' and 'lto_share'
      - 'outputs' dict mapping filename -> {sha256: ...}

    The demand 'path' is the file's BASENAME only (never the path as given), so
    manifests are identical on every machine; the sha256 identifies the content.
    No home-directory fragments (/Users/, /home/) may appear.

    This test passes a relative path (using os.path.relpath) so the CLI can
    resolve it from project/src, and checks only the basename is recorded.

    Cannot catch: wrong values behind correct keys.
    """
    import os
    out = tmp_path / "out"
    out.mkdir()
    # Use a relative path from the CLI cwd (project/src) to the fixture
    rel_demand = os.path.relpath(str(_FIXTURE), start=_SRC_DIR_STR)
    r = _run_cli([
        "--demand", rel_demand,
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr

    manifest = json.loads((out / "manifest.json").read_text())

    assert "demand" in manifest, "manifest missing 'demand'"
    assert "path" in manifest["demand"], "manifest['demand'] missing 'path'"
    assert "sha256" in manifest["demand"], "manifest['demand'] missing 'sha256'"
    assert "hubs" in manifest, "manifest missing 'hubs'"
    assert "parameters" in manifest, "manifest missing 'parameters'"
    assert "seats_per_flight" in manifest["parameters"]
    assert "lto_share" in manifest["parameters"]
    assert "outputs" in manifest, "manifest missing 'outputs'"

    # demand path is recorded as the BASENAME only (portable; sha256 identifies content)
    stored_path = manifest["demand"]["path"]
    assert stored_path == pathlib.Path(rel_demand).name, (
        f"manifest demand path should be the basename "
        f"({pathlib.Path(rel_demand).name!r}), got {stored_path!r}"
    )
    # no home-directory fragments
    assert "/Users/" not in stored_path, (
        f"demand path contains absolute user path: {stored_path!r}"
    )
    assert "/home/" not in stored_path, (
        f"demand path contains absolute home path: {stored_path!r}"
    )

    # demand sha256 must match the actual fixture
    expected_sha = _sha256(_FIXTURE)
    assert manifest["demand"]["sha256"] == expected_sha, (
        f"demand sha256 mismatch: {manifest['demand']['sha256']!r} vs {expected_sha!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 5. totals.csv — structure and ratio column
# ══════════════════════════════════════════════════════════════════════════

def test_csv_structure(tmp_path: pathlib.Path) -> None:
    """
    Example test — tolerance: rel_tol=1e-12 for numeric values.

    totals.csv must have columns metric,p2p,hub_spoke,hub_over_p2p_ratio.
    Each metric row must contain the same values as the JSON (to rel 1e-12).
    The ratio column must equal hub_spoke/p2p when p2p != 0, and be blank
    (empty string) when p2p == 0.

    Cannot catch: correct structure but wrong ordering of rows.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr

    # Read CSV
    rows = {}
    with open(out / "totals.csv", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["metric", "p2p", "hub_spoke", "hub_over_p2p_ratio"], (
            f"CSV headers: {reader.fieldnames}"
        )
        for row in reader:
            rows[row["metric"]] = row

    # Check metrics present
    expected_metrics = [
        "passengers", "passenger_legs", "flights", "aircraft_nm",
        "fuel_kg", "NOx_kg", "CO_kg", "HC_kg",
    ]
    for m in expected_metrics:
        assert m in rows, f"CSV missing metric {m!r}"

    # Verify ratio
    data = json.loads((out / "totals.json").read_text())
    for m in expected_metrics:
        row = rows[m]
        p2p_val = float(row["p2p"])
        hs_val = float(row["hub_spoke"])
        assert math.isclose(p2p_val, data["p2p"][m], rel_tol=1e-12), (
            f"CSV p2p[{m}] mismatch"
        )
        assert math.isclose(hs_val, data["hub_spoke"][m], rel_tol=1e-12), (
            f"CSV hub_spoke[{m}] mismatch"
        )
        ratio_str = row["hub_over_p2p_ratio"]
        if p2p_val != 0.0:
            assert ratio_str != "", f"ratio should not be blank for {m!r} (p2p={p2p_val})"
            ratio = float(ratio_str)
            assert math.isclose(ratio, hs_val / p2p_val, rel_tol=1e-12), (
                f"ratio mismatch for {m!r}: {ratio} vs {hs_val/p2p_val}"
            )
        else:
            assert ratio_str == "", f"ratio should be blank when p2p==0 for {m!r}"


# ══════════════════════════════════════════════════════════════════════════
# 6. WARNING.txt and JSON warning key
# ══════════════════════════════════════════════════════════════════════════

_WARNING_VERBATIM = (
    "PLACEHOLDER MODEL: seats_per_flight, lto_share, fuel coefficients and "
    "emission indices are illustrative. Numbers here are NOT results."
)


def test_warning_txt_verbatim(tmp_path: pathlib.Path) -> None:
    """
    Example test — no tolerance (exact string containment).

    WARNING.txt must contain the exact verbatim warning string specified in
    the task.  Any paraphrase or truncation is a failure.

    Cannot catch: correct text but wrong file encoding.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr
    content = (out / "WARNING.txt").read_text(encoding="utf-8")
    assert _WARNING_VERBATIM in content, (
        f"WARNING.txt does not contain expected text.\n"
        f"Expected to find: {_WARNING_VERBATIM!r}\n"
        f"Got: {content!r}"
    )


def test_json_warning_key(tmp_path: pathlib.Path) -> None:
    """
    Example test — no tolerance (exact string containment).

    totals.json must contain a 'warning' key whose value contains the
    verbatim warning string.

    Cannot catch: correct key but wrong type (non-string).
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr
    data = json.loads((out / "totals.json").read_text())
    assert "warning" in data, "totals.json missing 'warning' key"
    assert _WARNING_VERBATIM in data["warning"], (
        f"totals.json 'warning' does not contain expected text.\n"
        f"Got: {data['warning']!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 7. FAILURE MODES — missing demand file → exit 2, stderr, empty out-dir
# ══════════════════════════════════════════════════════════════════════════

def test_missing_demand_file_exit2(tmp_path: pathlib.Path) -> None:
    """
    Negative test — no tolerance.

    When --demand points to a non-existent file the CLI must:
      - exit with code 2
      - print a message to stderr
      - leave --out empty (no partial files written)

    Cannot catch: an error message that happens to be on stdout instead of stderr.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(tmp_path / "nonexistent.csv"),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 2, (
        f"Expected exit 2 for missing demand file, got {r.returncode}.\n"
        f"stderr: {r.stderr}"
    )
    assert r.stderr.strip(), "Expected non-empty stderr message"
    assert list(out.iterdir()) == [], (
        f"Output dir must be empty on failure, found: {list(out.iterdir())}"
    )


def test_unknown_airport_exit2(tmp_path: pathlib.Path) -> None:
    """
    Negative test — no tolerance.

    A demand CSV containing an unknown airport code (ZZZ) must cause exit 2.
    The out-dir must remain empty.

    Cannot catch: an error that happens to produce code 2 for the wrong reason.
    """
    bad_csv = tmp_path / "bad_demand.csv"
    bad_csv.write_text("origin,destination,passengers\nBOS,ZZZ,100.0\n")
    out = tmp_path / "out"
    out.mkdir()

    r = _run_cli([
        "--demand", str(bad_csv),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 2, (
        f"Expected exit 2 for unknown airport ZZZ, got {r.returncode}.\n"
        f"stderr: {r.stderr}"
    )
    assert r.stderr.strip(), "Expected non-empty stderr message"
    assert list(out.iterdir()) == [], "Output dir must be empty on failure"


def test_empty_hubs_exit2(tmp_path: pathlib.Path) -> None:
    """
    Negative test — no tolerance.

    Passing --hubs with an empty string or no hubs must cause exit 2 with a
    non-empty stderr message.

    Cannot catch: an error raised for a different reason than empty hubs.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", "",
        "--out", str(out),
    ])
    assert r.returncode == 2, (
        f"Expected exit 2 for empty hubs, got {r.returncode}.\nstderr: {r.stderr}"
    )
    assert r.stderr.strip(), "Expected non-empty stderr for empty hubs"
    assert list(out.iterdir()) == [], "Output dir must be empty on failure"


def test_malformed_csv_exit2(tmp_path: pathlib.Path) -> None:
    """
    Negative test — no tolerance.

    A CSV missing the 'passengers' column must cause exit 2 with stderr.

    Cannot catch: graceful degradation that silently defaults to 0 passengers.
    """
    bad_csv = tmp_path / "malformed.csv"
    bad_csv.write_text("origin,destination\nBOS,JFK\n")
    out = tmp_path / "out"
    out.mkdir()

    r = _run_cli([
        "--demand", str(bad_csv),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 2, (
        f"Expected exit 2 for malformed CSV, got {r.returncode}.\n"
        f"stderr: {r.stderr}"
    )
    assert r.stderr.strip(), "Expected non-empty stderr"
    assert list(out.iterdir()) == [], "Output dir must be empty on failure"


# ══════════════════════════════════════════════════════════════════════════
# 8. CONSERVATION FAILURE — exit 3, stderr, empty out-dir (in-process)
# ══════════════════════════════════════════════════════════════════════════

def test_conservation_failure_exit3(tmp_path: pathlib.Path) -> None:
    """
    Negative / in-process test — no tolerance.

    When passenger conservation fails (injected by monkeypatching
    build_routes_point_to_point to return a route with wrong passengers),
    main() must return exit-code 3, print to stderr, and write NOTHING to
    --out.

    Kind: example (negative path) using in-process main() invocation with
    monkeypatching.

    Cannot catch: conservation failure that is detected but still writes
    partial output before raising.
    """
    import cli as cli_mod
    import networks as networks_mod

    original_builder = networks_mod.build_routes_point_to_point

    def _bad_p2p(demand):
        """Return routes with passengers multiplied by 2 (breaks conservation)."""
        routes = original_builder(demand)
        from networks import RouteFlow
        return [
            RouteFlow(r.origin, r.destination, r.route, r.passengers * 2.0)
            for r in routes
        ]

    out = tmp_path / "out"
    out.mkdir()

    # Patch the cli module's reference to the builder
    import unittest.mock as mock
    with mock.patch.object(networks_mod, "build_routes_point_to_point", _bad_p2p):
        # Also patch the reference inside cli module if it imported directly
        with mock.patch("cli.build_routes_point_to_point", _bad_p2p, create=True):
            exit_code = cli_mod.main([
                "--demand", str(_FIXTURE),
                "--hubs", _HUBS_DEFAULT,
                "--out", str(out),
            ])

    assert exit_code == 3, (
        f"Expected exit 3 for conservation failure, got {exit_code}"
    )
    assert list(out.iterdir()) == [], (
        f"Output dir must be empty on conservation failure, found: {list(out.iterdir())}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 9. PARAMS FORWARDED — --seats-per-flight and --lto-share change results
# ══════════════════════════════════════════════════════════════════════════

def test_custom_seats_and_lto_reflected_in_json(tmp_path: pathlib.Path) -> None:
    """
    Differential test — tolerance: rel_tol=1e-12.

    When --seats-per-flight 200 and --lto-share 0.2 are passed, totals.json
    must match network_totals(..., seats_per_flight=200, lto_share=0.2).

    Also verifies that manifest.json records the passed parameter values.

    Cannot catch: parameters accepted but forwarded to the wrong call.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
        "--seats-per-flight", "200",
        "--lto-share", "0.2",
    ])
    assert r.returncode == 0, r.stderr

    demand = load_demand(str(_FIXTURE))
    p2p_routes = build_routes_point_to_point(demand)
    hs_routes = build_routes_hub_spoke(demand, hubs=["JFK", "ORD"])
    expected_p2p = network_totals(p2p_routes, seats_per_flight=200.0, lto_share=0.2)
    expected_hs = network_totals(hs_routes, seats_per_flight=200.0, lto_share=0.2)

    data = json.loads((out / "totals.json").read_text())
    for k in ["passengers", "passenger_legs", "flights", "fuel_kg"]:
        assert math.isclose(data["p2p"][k], expected_p2p[k], rel_tol=1e-12), (
            f"p2p[{k}] mismatch with custom params"
        )
        assert math.isclose(data["hub_spoke"][k], expected_hs[k], rel_tol=1e-12), (
            f"hub_spoke[{k}] mismatch with custom params"
        )

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["parameters"]["seats_per_flight"] == 200.0
    assert manifest["parameters"]["lto_share"] == 0.2


# ══════════════════════════════════════════════════════════════════════════
# 10. HUB ORDER — ORD,JFK vs JFK,ORD gives DIFFERENT hub-spoke totals
# ══════════════════════════════════════════════════════════════════════════

def test_hub_order_matters_for_hub_spoke_totals(tmp_path: pathlib.Path) -> None:
    """
    Metamorphic test — no tolerance (inequality assertion).

    Reasoning: hub_of() Rule 3 maps every non-hub airport to the FIRST hub.
    With --hubs JFK,ORD, airports like BOS/SFO/DEN/PHX all map to JFK.
    With --hubs ORD,JFK, they all map to ORD.
    The routes (and therefore distances, fuel, legs) differ.

    Therefore hub_spoke totals MUST differ between the two orderings.
    (P2P totals should be identical because P2P ignores hub order.)

    Cannot catch: a model where all non-hub airports happen to be equidistant
    from both hubs (extremely unlikely with the fixture data).
    """
    out_jfk_first = tmp_path / "jfk_first"
    out_ord_first = tmp_path / "ord_first"
    out_jfk_first.mkdir()
    out_ord_first.mkdir()

    r1 = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", "JFK,ORD",
        "--out", str(out_jfk_first),
    ])
    r2 = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", "ORD,JFK",
        "--out", str(out_ord_first),
    ])
    assert r1.returncode == 0, r1.stderr
    assert r2.returncode == 0, r2.stderr

    d1 = json.loads((out_jfk_first / "totals.json").read_text())
    d2 = json.loads((out_ord_first / "totals.json").read_text())

    # P2P totals should be identical (hub order irrelevant for P2P)
    for k in ["passengers", "passenger_legs", "fuel_kg"]:
        assert math.isclose(d1["p2p"][k], d2["p2p"][k], rel_tol=1e-12), (
            f"P2P {k} should be identical regardless of hub order: "
            f"{d1['p2p'][k]} vs {d2['p2p'][k]}"
        )

    # Hub-spoke totals must differ (different default-hub assignments)
    # We check fuel_kg (depends on routing distances)
    assert not math.isclose(
        d1["hub_spoke"]["fuel_kg"], d2["hub_spoke"]["fuel_kg"], rel_tol=1e-6
    ), (
        f"Hub-spoke fuel_kg should differ with different hub orderings: "
        f"JFK,ORD → {d1['hub_spoke']['fuel_kg']}, "
        f"ORD,JFK → {d2['hub_spoke']['fuel_kg']}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 11. NO ABSOLUTE PATHS OR TIMESTAMPS in outputs
# ══════════════════════════════════════════════════════════════════════════

def test_no_absolute_paths_in_json_outputs(tmp_path: pathlib.Path) -> None:
    """
    Invariance test — no tolerance.

    totals.json and manifest.json must not contain any absolute paths or
    home-directory fragments.  This ensures the outputs are portable and
    byte-identical across machines.

    Cannot catch: absolute paths embedded in binary (image) files.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr

    for fname in ("totals.json", "manifest.json"):
        content = (out / fname).read_text()
        assert "/Users/" not in content, (
            f"{fname} contains absolute /Users/ path"
        )
        assert "/home/" not in content, (
            f"{fname} contains absolute /home/ path"
        )
        # No ISO timestamps like 2024-01-01T00:00:00
        import re
        ts_pattern = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
        assert not ts_pattern.search(content), (
            f"{fname} contains a timestamp-like string"
        )


def test_no_timestamps_in_warning_txt(tmp_path: pathlib.Path) -> None:
    """
    Invariance test — no tolerance.

    WARNING.txt must not contain any timestamp.  It is a static text file
    and its content should be byte-identical across runs.

    Cannot catch: non-ISO timestamp formats.
    """
    import re
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr
    content = (out / "WARNING.txt").read_text()
    ts_pattern = re.compile(r"\d{4}-\d{2}-\d{2}")
    assert not ts_pattern.search(content), (
        f"WARNING.txt contains a date-like string: {content!r}"
    )


# ══════════════════════════════════════════════════════════════════════════
# 12. INVARIANT FAILURE → exit 3 (inject a bad route via monkeypatching)
# ══════════════════════════════════════════════════════════════════════════

def test_endpoint_invariant_failure_exit3(tmp_path: pathlib.Path) -> None:
    """
    Negative / in-process test — no tolerance.

    When build_routes_point_to_point returns routes that fail check_endpoints
    (origin mismatch), main() must return exit code 3, not write any outputs.

    Cannot catch: the check passing on a different invariant but not the
    endpoint check.
    """
    import cli as cli_mod
    import networks as networks_mod
    from networks import RouteFlow

    original_builder = networks_mod.build_routes_point_to_point

    def _bad_endpoints(demand):
        routes = original_builder(demand)
        # Corrupt first route: swap origin/destination in the RouteFlow fields
        # but keep the route path the same → endpoint mismatch
        r = routes[0]
        bad = RouteFlow(r.destination, r.origin, r.route, r.passengers)
        return [bad] + routes[1:]

    out = tmp_path / "out"
    out.mkdir()

    import unittest.mock as mock
    with mock.patch.object(networks_mod, "build_routes_point_to_point", _bad_endpoints):
        with mock.patch("cli.build_routes_point_to_point", _bad_endpoints, create=True):
            exit_code = cli_mod.main([
                "--demand", str(_FIXTURE),
                "--hubs", _HUBS_DEFAULT,
                "--out", str(out),
            ])

    assert exit_code == 3, (
        f"Expected exit 3 for endpoint invariant failure, got {exit_code}"
    )
    assert list(out.iterdir()) == [], (
        "Output dir must be empty on invariant failure"
    )


# ══════════════════════════════════════════════════════════════════════════
# 14. NONNEG INVARIANT FAILURE → exit 3 (inject negative passengers)
# ══════════════════════════════════════════════════════════════════════════

def test_nonneg_invariant_failure_exit3(tmp_path: pathlib.Path) -> None:
    """
    Negative / in-process test — no tolerance.

    When build_routes_hub_spoke returns a route with slightly-negative
    passengers (below the -1e-9 tolerance) the CLI must return exit 3.
    This tests that check_nonnegative is actually invoked.

    Conservation alone CANNOT catch this because we split one OD into two
    routes: one with passengers + delta, one with -delta (sums to original).
    Only check_nonnegative catches the negative route.

    Kind: negative example using in-process main() with monkeypatching.

    Cannot catch: a CLI that passes despite negative passengers being a bug
    in a downstream computation (this only checks the CLI's guard).
    """
    import cli as cli_mod
    import networks as networks_mod
    from networks import RouteFlow

    original_builder = networks_mod.build_routes_hub_spoke

    def _bad_nonneg(demand, hubs, assign=None):
        """Return routes where the first route has passengers split into
        a positive and a deeply-negative sub-route, conservation preserved.
        """
        routes = original_builder(demand, hubs, assign)
        r = routes[0]
        # Split: one route with (passengers + 5), one with -5
        # Conservation: sum = r.passengers (unchanged)
        # check_nonnegative: -5 << -1e-9, so must be caught
        bad1 = RouteFlow(r.origin, r.destination, r.route, r.passengers + 5.0)
        bad2 = RouteFlow(r.origin, r.destination, r.route, -5.0)
        return [bad1, bad2] + list(routes[1:])

    out = tmp_path / "out"
    out.mkdir()

    import unittest.mock as mock
    with mock.patch.object(networks_mod, "build_routes_hub_spoke", _bad_nonneg):
        with mock.patch("cli.build_routes_hub_spoke", _bad_nonneg, create=True):
            exit_code = cli_mod.main([
                "--demand", str(_FIXTURE),
                "--hubs", _HUBS_DEFAULT,
                "--out", str(out),
            ])

    assert exit_code == 3, (
        f"Expected exit 3 for nonneg invariant failure, got {exit_code}"
    )
    assert list(out.iterdir()) == [], (
        "Output dir must be empty on invariant failure"
    )


def test_totals_json_structure(tmp_path: pathlib.Path) -> None:
    """
    Example test — no tolerance.

    totals.json must have keys: 'p2p', 'hub_spoke', 'parameters', 'warning'.
    Each of 'p2p' and 'hub_spoke' must contain all eight network metric keys.

    Cannot catch: correct keys with wrong types or NaN values.
    """
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli([
        "--demand", str(_FIXTURE),
        "--hubs", _HUBS_DEFAULT,
        "--out", str(out),
    ])
    assert r.returncode == 0, r.stderr

    data = json.loads((out / "totals.json").read_text())
    assert "p2p" in data
    assert "hub_spoke" in data
    assert "parameters" in data
    assert "warning" in data

    metric_keys = {
        "passengers", "passenger_legs", "flights", "aircraft_nm",
        "fuel_kg", "NOx_kg", "CO_kg", "HC_kg",
    }
    assert metric_keys.issubset(data["p2p"].keys()), (
        f"p2p missing keys: {metric_keys - set(data['p2p'].keys())}"
    )
    assert metric_keys.issubset(data["hub_spoke"].keys()), (
        f"hub_spoke missing keys: {metric_keys - set(data['hub_spoke'].keys())}"
    )

    # Parameters must include seats_per_flight and lto_share
    assert "seats_per_flight" in data["parameters"]
    assert "lto_share" in data["parameters"]


def test_manifest_records_only_basename_of_demand_file(tmp_path: pathlib.Path) -> None:
    """
    Invariance / portability test — exact string equality, no tolerance.

    The demand file is copied to a deep, unmistakably machine-specific directory
    and passed to the CLI by ABSOLUTE path.  manifest.json must record only the
    file's basename (identity of the content is carried by its sha256), so that
    identical inputs give byte-identical outputs on any machine.

    Why this exists: the earlier absolute-path test only looked for '/Users/' and
    '/home/', so it passed when the checkout lived under /tmp and failed (in CI
    and on a laptop) when it did not.  This test does not depend on where the
    repository is checked out: it builds its own unique absolute prefix.

    Cannot catch: a path leaking into some other output file (covered by the
    separate scan of totals.json and manifest.json).
    """
    deep = tmp_path / "machine_specific_prefix_8f3a" / "alice" / "data"
    deep.mkdir(parents=True)
    copied = deep / "tiny_demand.csv"
    copied.write_bytes(_FIXTURE.read_bytes())

    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli(["--demand", str(copied), "--hubs", _HUBS_DEFAULT, "--out", str(out)])
    assert r.returncode == 0, r.stderr

    manifest_text = (out / "manifest.json").read_text()
    manifest = json.loads(manifest_text)
    assert manifest["demand"]["path"] == "tiny_demand.csv"
    assert "machine_specific_prefix_8f3a" not in manifest_text
    assert str(tmp_path) not in manifest_text
    assert manifest["demand"]["sha256"] == _sha256(_FIXTURE)
