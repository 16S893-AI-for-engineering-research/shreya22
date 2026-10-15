"""
test_airports.py — Tests for NW-19: airport coordinates from OurAirports.

Tests the following:
 - project/data/airports.csv schema, value ranges, no duplicates (example/property)
 - The 6 legacy airports are present (example)
 - Pinned distances BOS-SFO=2344, BOS-ORD=751, ORD-SFO=1600 (golden)
 - KeyError for unknown airport code (example)
 - build_airports.py script is deterministic (property)
 - Differential vs T-100 DISTANCE using committed fixture (differential)
 - Differential vs full bts-cache (skipif, function-level)

All skipif guards are at function level (not module level), per test_no_silent_skips.py.
"""
from __future__ import annotations

import math
import os
import pathlib
import subprocess
import sys

import pytest

_THIS_DIR = pathlib.Path(__file__).resolve().parent
_SRC_DIR = _THIS_DIR.parent / "src"
_DATA_DIR = _THIS_DIR.parent / "data"
_SCRIPTS_DIR = _THIS_DIR.parent / "scripts"
_FIXTURE_DIR = _THIS_DIR / "fixtures" / "airports"

# Add src to path for direct imports
sys.path.insert(0, str(_SRC_DIR))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BTS_CACHE = pathlib.Path(
    os.environ.get(
        "BTS_CACHE_DIR",
        pathlib.Path(__file__).resolve().parents[2] / "data" / "bts-cache",
    )
)
_FULL_OA_CSV = _BTS_CACHE / "ourairports_airports.csv"
_FULL_T100_CSVS = [
    _BTS_CACHE / "t100_2019_01.csv",
    _BTS_CACHE / "t100_2019_02.csv",
    _BTS_CACHE / "t100_2019_03.csv",
]
_HAS_FULL_DATA = _FULL_OA_CSV.exists() and all(p.exists() for p in _FULL_T100_CSVS)

NM_PER_SM: float = 1.0 / 1.150779  # statute miles -> nautical miles
R_NM: float = 3440.065  # mean Earth radius in nm (must match distance.py)


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nm — duplicate of the formula in distance.py,
    kept here so this test module is self-contained and does not depend on
    the module-under-test for its own validation logic."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    )
    a = min(1.0, max(0.0, a))
    return R_NM * 2.0 * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# 1. airports.csv schema, value ranges, no duplicates
# ---------------------------------------------------------------------------


def test_airports_csv_exists():
    """
    Example test — project/data/airports.csv must exist after build_airports.py is run.

    Kind: example.
    Tolerance: none (file existence check).
    Cannot catch: file present but empty or corrupt.
    """
    airports_csv = _DATA_DIR / "airports.csv"
    assert airports_csv.exists(), (
        f"project/data/airports.csv not found at {airports_csv}. "
        "Run project/scripts/build_airports.py to generate it."
    )


def test_airports_csv_schema():
    """
    Example test — airports.csv has exactly the columns iata, lat, lon.

    Kind: example (schema check).
    Tolerance: none.
    Cannot catch: columns present but with wrong dtypes.
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    assert list(df.columns) == ["iata", "lat", "lon"], (
        f"Expected columns ['iata', 'lat', 'lon'], got {list(df.columns)}"
    )


def test_airports_csv_row_count():
    """
    Example test — airports.csv must have ~806 rows (the number of T-100 Q1-2019
    passenger airports that match a non-closed OurAirports iata_code).

    Kind: example (golden count from reviewer measurement).
    Tolerance: ±20 rows — allows for minor variation due to filtering edge cases
    (e.g. whitespace in iata_code, duplicate handling), but tight enough to
    catch wholesale omission of airports.
    Cannot catch: wrong set of airports that still sums to the right count.
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    n = len(df)
    assert 786 <= n <= 826, (
        f"airports.csv has {n} rows; expected ~806 (±20). "
        "Reviewer measured 806 matched airports in Q1-2019 T-100 passenger data."
    )


def test_airports_csv_lat_range():
    """
    Property test — all latitudes are in [-90, 90].

    Kind: property.
    Tolerance: none (exact range check).
    Cannot catch: lat/lon columns swapped (caught by test_airports_csv_lon_range).
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    bad = df[(df["lat"] < -90) | (df["lat"] > 90)]
    assert len(bad) == 0, (
        f"{len(bad)} rows have lat outside [-90, 90]:\n{bad.head()}"
    )


def test_airports_csv_lon_range():
    """
    Property test — all longitudes are in [-180, 180].

    Kind: property.
    Tolerance: none (exact range check).
    Cannot catch: lon values that happen to be in range but are wrong.
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    bad = df[(df["lon"] < -180) | (df["lon"] > 180)]
    assert len(bad) == 0, (
        f"{len(bad)} rows have lon outside [-180, 180]:\n{bad.head()}"
    )


def test_airports_csv_no_duplicate_iata():
    """
    Property test — iata codes in airports.csv are unique (no duplicates).

    Kind: property.
    Tolerance: none.
    Cannot catch: duplicate rows that differ in lat/lon.
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    dups = df[df["iata"].duplicated(keep=False)]
    assert len(dups) == 0, (
        f"{len(dups)} rows have duplicate iata codes:\n{dups}"
    )


def test_airports_csv_no_null():
    """
    Property test — no null values in airports.csv.

    Kind: property.
    Tolerance: none.
    Cannot catch: values that are sentinel strings rather than null.
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    nulls = df.isnull().sum()
    assert nulls.sum() == 0, (
        f"airports.csv has null values:\n{nulls[nulls > 0]}"
    )


# ---------------------------------------------------------------------------
# 2. The 6 legacy airports are present
# ---------------------------------------------------------------------------

LEGACY_AIRPORTS = ["BOS", "JFK", "ORD", "SFO", "DEN", "PHX"]


def test_legacy_airports_present():
    """
    Example test — the 6 airports that were hardcoded in the original
    distance.py (BOS, JFK, ORD, SFO, DEN, PHX) must all be in airports.csv.

    Kind: example.
    Tolerance: none (exact membership check).
    Cannot catch: coordinates are wrong for these airports.
    """
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    codes = set(df["iata"].values)
    for code in LEGACY_AIRPORTS:
        assert code in codes, (
            f"Legacy airport '{code}' is missing from airports.csv. "
            "All 6 original hardcoded airports must be present."
        )


# ---------------------------------------------------------------------------
# 3. Pinned distances — must not change
# ---------------------------------------------------------------------------


def test_pinned_bos_sfo():
    """
    Golden test — distance_nm('BOS', 'SFO') rounds to 2344 nm.

    Kind: golden (pinned value from test_site.py and project-prototype.html).
    Tolerance: ±0.5 nm absolute, so round(d) == 2344.
    Reason: prototype page quotes '2344 nm' as a display value; changing it
    would require updating the HTML page. The OurAirports coordinates differ
    from the original by <0.3 nm on this pair, so the rounded value is stable.
    Cannot catch: a coordinate change that shifts the distance by exactly 0.5 nm.
    """
    import distance

    d = distance.distance_nm("BOS", "SFO")
    assert abs(d - 2344.0) <= 0.5, (
        f"BOS-SFO: got {d:.3f} nm, expected ~2344 nm (±0.5 nm). "
        "This is a pinned value quoted in project-prototype.html."
    )


def test_pinned_bos_ord():
    """
    Golden test — distance_nm('BOS', 'ORD') rounds to 751 nm.

    Kind: golden (pinned value from test_site.py and project-prototype.html).
    Tolerance: ±0.5 nm absolute.
    Reason: prototype page quotes '751 nm'; rounded value must remain stable.
    Cannot catch: a coordinate change that shifts the distance by exactly 0.5 nm.
    """
    import distance

    d = distance.distance_nm("BOS", "ORD")
    assert abs(d - 751.0) <= 0.5, (
        f"BOS-ORD: got {d:.3f} nm, expected ~751 nm (±0.5 nm). "
        "This is a pinned value quoted in project-prototype.html."
    )


def test_pinned_ord_sfo():
    """
    Golden test — distance_nm('ORD', 'SFO') rounds to 1600 nm.

    Kind: golden (pinned value from test_site.py and project-prototype.html).
    Tolerance: ±0.5 nm absolute.
    Reason: prototype page quotes '1600 nm'; rounded value must remain stable.
    Cannot catch: a coordinate change that shifts the distance by exactly 0.5 nm.
    """
    import distance

    d = distance.distance_nm("ORD", "SFO")
    assert abs(d - 1600.0) <= 0.5, (
        f"ORD-SFO: got {d:.3f} nm, expected ~1600 nm (±0.5 nm). "
        "This is a pinned value quoted in project-prototype.html."
    )


# ---------------------------------------------------------------------------
# 4. AIRPORT_COORDS covers all airports from airports.csv
# ---------------------------------------------------------------------------


def test_airport_coords_covers_csv():
    """
    Property test — distance.AIRPORT_COORDS must contain every iata code
    in airports.csv.

    Kind: property.
    Tolerance: none.
    Cannot catch: coordinates present but wrong.
    """
    import distance
    import pandas as pd

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    df = pd.read_csv(airports_csv, comment="#")
    missing = [c for c in df["iata"].values if c not in distance.AIRPORT_COORDS]
    assert len(missing) == 0, (
        f"{len(missing)} airports from airports.csv are missing from "
        f"distance.AIRPORT_COORDS: {missing[:10]}"
    )


def test_airport_coords_has_legacy_airports():
    """
    Property test — distance.AIRPORT_COORDS must contain all 6 legacy airports
    even if airports.csv is missing (fallback guard).

    Kind: property.
    Tolerance: none.
    Cannot catch: airports present with wrong coordinates.
    """
    import distance

    for code in LEGACY_AIRPORTS:
        assert code in distance.AIRPORT_COORDS, (
            f"Legacy airport '{code}' missing from distance.AIRPORT_COORDS"
        )


# ---------------------------------------------------------------------------
# 5. KeyError for unknown airport code
# ---------------------------------------------------------------------------


def test_distance_nm_unknown_code_raises():
    """
    Example test — distance_nm raises KeyError naming the unknown code.

    Kind: example.
    Tolerance: none (exception check).
    Cannot catch: wrong error type.
    """
    import distance

    with pytest.raises(KeyError) as exc_info:
        distance.distance_nm("ZZZUNK", "BOS")
    assert "ZZZUNK" in str(exc_info.value), (
        f"KeyError message should name 'ZZZUNK', got: {exc_info.value}"
    )


def test_distance_nm_faa_style_code_raises():
    """
    Example test — distance_nm raises KeyError for FAA-style codes like '1G4'
    that appear in T-100 but are not in OurAirports.

    Kind: example.
    Tolerance: none.
    Cannot catch: '1G4' coincidentally appearing in AIRPORT_COORDS.
    """
    import distance

    with pytest.raises(KeyError) as exc_info:
        distance.distance_nm("1G4", "BOS")
    assert "1G4" in str(exc_info.value), (
        f"KeyError message should name '1G4', got: {exc_info.value}"
    )


# ---------------------------------------------------------------------------
# 6. Script determinism — run twice on committed fixture, identical output
# ---------------------------------------------------------------------------


def test_build_airports_script_deterministic(tmp_path: pathlib.Path):
    """
    Property test — running build_airports.py twice on the committed fixture
    produces identical airports.csv output.

    Kind: property (determinism).
    Tolerance: exact byte equality.
    Reason: the script must produce reproducible output for CI; any sorting
    or dict-iteration non-determinism would fail this test.
    Cannot catch: determinism within a single process invocation that fails
    across multiple invocations due to external state.

    The test passes --oa-csv and --t100-csvs arguments pointing to the committed
    fixture files, writing to two different tmp directories, then compares the
    output CSV content (ignoring any comment lines with run timestamps).
    """
    script = _SCRIPTS_DIR / "build_airports.py"
    assert script.exists(), f"build_airports.py not found at {script}"

    oa_csv = _FIXTURE_DIR / "ourairports_sample.csv"
    t100_csv = _FIXTURE_DIR / "t100_sample.csv"
    assert oa_csv.exists() and t100_csv.exists(), "fixture files missing"

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    out1.mkdir()
    out2.mkdir()

    # Run twice
    for out_dir in [out1, out2]:
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--oa-csv", str(oa_csv),
                "--t100-csvs", str(t100_csv),
                "--out-dir", str(out_dir),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"build_airports.py exited {result.returncode}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    csv1 = out1 / "airports.csv"
    csv2 = out2 / "airports.csv"
    assert csv1.exists() and csv2.exists(), "airports.csv not written by script"

    # Compare data rows (strip comment lines starting with #)
    def data_lines(p: pathlib.Path) -> list[str]:
        return [ln for ln in p.read_text().splitlines() if not ln.startswith("#")]

    lines1 = data_lines(csv1)
    lines2 = data_lines(csv2)
    assert lines1 == lines2, (
        "build_airports.py produced different output on two runs (non-deterministic).\n"
        f"Run 1: {len(lines1)} lines; Run 2: {len(lines2)} lines"
    )


def test_build_airports_script_fixture_content(tmp_path: pathlib.Path):
    """
    Example test — running build_airports.py on the committed fixture produces
    the expected airports (8 of 9 airports; ZZX is closed, and ZZX is also an
    ORIGIN in t100 so it's in the unmatched list; FAA-style codes present in
    t100 but not OurAirports are also unmatched).

    Kind: example (golden structure from the known fixture).
    Tolerance: none.
    Cannot catch: script returns wrong coordinates for an airport.

    Expected output: airports.csv with iata codes BOS, ORD, SFO, DEN, PHX, ATL, DTW, MSP
    (8 airports matched), with ZZX unmatched (appears in T-100 but is 'closed' in OurAirports).
    """
    import pandas as pd

    script = _SCRIPTS_DIR / "build_airports.py"
    assert script.exists(), f"build_airports.py not found at {script}"

    oa_csv = _FIXTURE_DIR / "ourairports_sample.csv"
    t100_csv = _FIXTURE_DIR / "t100_sample.csv"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--oa-csv", str(oa_csv),
            "--t100-csvs", str(t100_csv),
            "--out-dir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Script failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    out_csv = tmp_path / "airports.csv"
    assert out_csv.exists()

    df = pd.read_csv(out_csv, comment="#")
    codes = set(df["iata"].values)

    # All 8 open airports from T-100 fixture should be present (PHX is in
    # OurAirports sample but NOT in any T-100 route, so it's not matched)
    # ZZX is in T-100 (as ORIGIN) but closed in OurAirports, so unmatched
    expected = {"BOS", "ORD", "SFO", "DEN", "ATL", "DTW", "MSP"}
    assert codes == expected, (
        f"Expected {expected}, got {codes}"
    )

    # ZZX must NOT be in the output (it's closed in OurAirports)
    assert "ZZX" not in codes, "ZZX (closed) must not appear in airports.csv"


def test_build_airports_sidecar_json(tmp_path: pathlib.Path):
    """
    Example test — build_airports.py writes a sidecar JSON recording the
    source URL, download date, source sha256, and unmatched codes list.

    Kind: example (sidecar provenance check).
    Tolerance: none.
    Cannot catch: sidecar JSON present but with wrong content.
    """
    import json

    script = _SCRIPTS_DIR / "build_airports.py"
    assert script.exists()

    oa_csv = _FIXTURE_DIR / "ourairports_sample.csv"
    t100_csv = _FIXTURE_DIR / "t100_sample.csv"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--oa-csv", str(oa_csv),
            "--t100-csvs", str(t100_csv),
            "--out-dir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0

    sidecar = tmp_path / "airports_meta.json"
    assert sidecar.exists(), (
        f"Sidecar airports_meta.json not found at {sidecar}. "
        "build_airports.py must write provenance metadata."
    )

    meta = json.loads(sidecar.read_text())
    assert "source_url" in meta, "sidecar must have 'source_url'"
    assert "download_date" in meta, "sidecar must have 'download_date'"
    assert "source_sha256" in meta, "sidecar must have 'source_sha256'"
    assert "unmatched_codes" in meta, "sidecar must have 'unmatched_codes'"
    assert isinstance(meta["unmatched_codes"], list), (
        "unmatched_codes must be a list"
    )
    # ZZX is in T100 as ORIGIN but closed in OurAirports -> should be unmatched
    assert "ZZX" in meta["unmatched_codes"], (
        "ZZX (in T-100 but closed in OurAirports) must appear in unmatched_codes"
    )


# ---------------------------------------------------------------------------
# 7. Differential test vs T-100 DISTANCE — committed fixture
# ---------------------------------------------------------------------------


def test_differential_vs_t100_distance_fixture():
    """
    Differential test — compare haversine_nm (from AIRPORT_COORDS) to
    T-100 reported DISTANCE (statute miles) for the 10 matched segments
    in the committed fixture.

    Kind: differential (OurAirports haversine vs BTS-reported great-circle).
    Tolerance:
      - Median ratio in [0.990, 1.010]: the fixture uses well-behaved US domestic
        segments; median should be very close to 1.0. Real-data median is 0.99894.
      - P95 ratio ≤ 1.015: all 10 segments in the fixture are normal; no outliers.
      - P5  ratio ≥ 0.985: same reasoning.
    Rationale: T-100 DISTANCE is a BTS-reported great-circle distance in statute
    miles; OurAirports uses WGS-84 coordinates. Agreement within ±1% for normal
    segments is expected. The unmatched ZZX row is excluded from the comparison.
    Cannot catch: systematic bias in OurAirports coordinates that affects all
    airports equally; also cannot catch T-100 DISTANCE coding errors that would
    shift the ratio but still keep it within tolerance.
    """
    import pandas as pd
    import statistics

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    t100 = pd.read_csv(_FIXTURE_DIR / "t100_sample.csv")
    airports = pd.read_csv(airports_csv, comment="#")
    coords = {row["iata"]: (row["lat"], row["lon"]) for _, row in airports.iterrows()}

    # Filter to passenger-configured, both ends matched, positive DISTANCE
    pax = t100[t100["AIRCRAFT_CONFIG"] == 1].copy()

    ratios = []
    for _, row in pax.iterrows():
        o, d, dist_sm = row["ORIGIN"], row["DEST"], row["DISTANCE"]
        if o not in coords or d not in coords:
            continue
        if dist_sm <= 0:
            continue
        dist_nm = dist_sm * NM_PER_SM
        hav = _haversine(coords[o][0], coords[o][1], coords[d][0], coords[d][1])
        ratios.append(hav / dist_nm)

    assert len(ratios) >= 5, (
        f"Too few matched segments in fixture: {len(ratios)}. "
        "Expected ≥5 matched segments."
    )

    ratios_sorted = sorted(ratios)
    median = statistics.median(ratios)
    p95 = ratios_sorted[int(0.95 * len(ratios))]
    p5 = ratios_sorted[int(0.05 * len(ratios))]

    assert 0.990 <= median <= 1.010, (
        f"Median ratio {median:.5f} outside [0.990, 1.010]. "
        "haversine(OurAirports) should agree with T-100 DISTANCE to within 1% "
        "for normal US domestic segments."
    )
    assert p95 <= 1.015, (
        f"P95 ratio {p95:.5f} > 1.015 (fixture has unexpected high outlier)."
    )
    assert p5 >= 0.985, (
        f"P5 ratio {p5:.5f} < 0.985 (fixture has unexpected low outlier)."
    )


# ---------------------------------------------------------------------------
# 8. Differential test vs full bts-cache (skipif on missing data)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not _HAS_FULL_DATA,
    reason="bts-cache not present (data/bts-cache/ourairports_airports.csv and "
           "t100_2019_0[1-3].csv required); CI does not have these files",
)
def test_differential_vs_t100_full_data():
    """
    Differential test — compare haversine_nm to T-100 DISTANCE over 11,782
    matched segments from the full bts-cache data.

    Kind: differential (OurAirports haversine vs BTS-reported great-circle).
    Tolerance:
      - Median ratio in [0.994, 1.004]: reviewer measured 0.99894; tolerance
        ±0.005 around 0.999 is generous enough to survive minor OurAirports
        data updates but catches a systematic coordinate-sign flip or unit error.
      - P99 ratio ≤ 2.0: the known worst-case outlier is 38x; P99 must exclude
        the really extreme cases but a value of 38 at P99 would indicate something
        deeply wrong. Real P99 ≈ 1.010. Upper bound of 2.0 is very generous to
        allow for data revisions.
      - P1  ratio ≥ 0.35: reviewer's minimum was 0.37; lower bound 0.35 catches
        a complete breakdown (e.g. all ratios near 0) while tolerating the known
        T-100/coding outliers.
      - At least 800 segments matched (reviewer found 11,782; a filter regression
        that dropped most rows would fail this check).
    Cannot catch: systematic bias in OurAirports coordinates affecting all
    airports equally; T-100 coding errors; the large outliers are REPORTED
    (not hidden) as part of the test output.
    """
    import pandas as pd
    import statistics

    airports_csv = _DATA_DIR / "airports.csv"
    if not airports_csv.exists():
        pytest.skip("airports.csv not yet generated")

    # Load all three T-100 months
    dfs = []
    for p in _FULL_T100_CSVS:
        dfs.append(pd.read_csv(p))
    t100 = pd.concat(dfs, ignore_index=True)

    pax = t100[t100["AIRCRAFT_CONFIG"] == 1].copy()

    airports = pd.read_csv(airports_csv, comment="#")
    coords = {row["iata"]: (row["lat"], row["lon"]) for _, row in airports.iterrows()}

    # Per unique (ORIGIN, DEST) segment (first occurrence)
    segs = pax.groupby(["ORIGIN", "DEST"])["DISTANCE"].first().reset_index()

    ratios = []
    outliers: list[tuple[str, str, float, float, float]] = []

    for _, row in segs.iterrows():
        o, d, dist_sm = row["ORIGIN"], row["DEST"], row["DISTANCE"]
        if o not in coords or d not in coords:
            continue
        if dist_sm <= 0:
            continue
        dist_nm = dist_sm * NM_PER_SM
        hav = _haversine(coords[o][0], coords[o][1], coords[d][0], coords[d][1])
        ratio = hav / dist_nm
        ratios.append(ratio)
        if abs(ratio - 1.0) > 0.01:
            outliers.append((o, d, dist_sm, hav, ratio))

    n = len(ratios)
    ratios_sorted = sorted(ratios)
    median = statistics.median(ratios)
    p99 = ratios_sorted[int(0.99 * n)]
    p1 = ratios_sorted[int(0.01 * n)]
    n_off = len(outliers)

    # Report outliers (not hidden) — this is part of the test output
    outliers_sorted = sorted(outliers, key=lambda x: abs(x[4] - 1.0), reverse=True)
    outlier_report = "\n".join(
        f"  {o}-{d}: t100={dist_sm:.0f}sm, hav={hav:.2f}nm, ratio={ratio:.4f}"
        for o, d, dist_sm, hav, ratio in outliers_sorted[:10]
    )

    assert n >= 800, (
        f"Only {n} matched segments; expected ≥800 (reviewer found 11,782). "
        "A filter regression may have dropped most rows."
    )
    assert 0.994 <= median <= 1.004, (
        f"Full-data median ratio {median:.5f} outside [0.994, 1.004]. "
        f"Reviewer measured 0.99894. {n} segments, {n_off} off by >1%.\n"
        f"Top outliers:\n{outlier_report}"
    )
    assert p99 <= 2.0, (
        f"P99 ratio {p99:.4f} > 2.0 (unexpected systematic error). "
        f"Reviewer's max was 38x but at P99 it was ~1.01."
    )
    assert p1 >= 0.35, (
        f"P1 ratio {p1:.4f} < 0.35 (unexpected systematic error). "
        f"Reviewer's min was 0.37."
    )

    # Report (not fail) the off-by->1% count and top outliers
    # This information is for the final report, not a hard assertion
    print(
        f"\n[test_differential_vs_t100_full_data] n={n}, median={median:.5f}, "
        f"p99={p99:.4f}, p1={p1:.4f}, off>1%={n_off}\n"
        f"Top outliers:\n{outlier_report}"
    )
