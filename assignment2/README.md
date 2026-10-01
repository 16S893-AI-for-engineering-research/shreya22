# Assignment 2 — Sun et al. (2021), Figure 4

**Status: first-pass aggregate reconstruction and source-consistency audit. Not an independent physical-model reproduction. Overall strict agreement at 0.1% is NOT achieved.**

Target: [Sun et al., Sustainability 13(2), 465](https://doi.org/10.3390/su13020465), Figure 4: total fuel burn, NOx, CO, and HC for hub-and-spoke and city-to-city networks. No CO2 series is added. The separate site page is [`../project-assignment2.html`](../project-assignment2.html).

## Run

With [uv](https://docs.astral.sh/uv/) on PATH, from this directory:

```bash
uv sync --locked
uv run --locked pytest
uv run --locked python -m netenv --page ../project-assignment2.html
uv run --locked python -m netenv --check-paper  # intentionally exits 1 on these sources
```

The first generation command exits 0 when artifacts are generated successfully; it still prints that paper agreement failed. `--check-paper` additionally returns failure for numerical disagreement. Neither command changes the tolerance.

Python is pinned in `.python-version` (3.12.12). `uv.lock` records the dependency resolution. After the first environment sync, reproduction is network-free; `uv run --locked --offline ...` works with cached dependencies. No credentials, BADA installation, or paid APIs are needed for this first pass. This is a repo-local research project, not a distributable data package; retain the data and templates alongside `src/`.

On the development Mac, uv was absent. Official uv **0.12.21** was installed locally under ignored `../.tools/uv/`; its release archive SHA-256 was checked against GitHub metadata: `b88bda573e566ef9bced66b155fe0408626fbbc053aee1c30ba686f0728c9447`. From the repository root, `source .tools/env.sh` enables that local installation and its local cache/Python paths before the commands above. This convenience file is ignored, not a dependency of a fresh clone; use your own uv installation elsewhere. No global shell or agent configuration was modified.

## What is independently recomputed?

Only the arithmetic from reported aggregates:

- Reduction (%) = `100 * (city - hub) / city`.
- Relative error = `abs(recomputed - reported) / abs(reported)`.
- A comparison passes when relative error is **≤ 0.001 (0.1%)**, using unrounded Decimal arithmetic.
- Zero-reference comparisons accept only exact zero. Reduction with a zero baseline is undefined and rejected.

Plot inputs are transcribed published outputs. Exact agreement with Table 7 is a transcription/rendering check, **not independent evidence for the model**. No simulation, demand assignment, flight scheduling, fuel-performance calculation, or atmospheric chemistry is implemented here. This is computationally trivial by design; it should not be submitted as a full model reproduction.

## Source inconsistencies, not tuned away

All eight plotted numbers use the **Table 7 Base Case columns**. The original image, XML, metadata, conflicting annotations, and prose values remain available.

| Quantity | Recomputed reduction (%) | Section 4.3 (%) | Relative error (%) | At 0.1% |
|---|---:|---:|---:|---|
| Fuel | 64.308511 | 64.38 | 0.111043 | FAIL |
| NOx | 51.356239 | 51.38 | 0.046246 | PASS |
| CO | 58.631579 | 58.64 | 0.014361 | PASS |
| HC | 64.009662 | 64.10 | 0.140933 | FAIL |

There are also two conflicting absolute totals:

- City HC: Figure 4's annotation prints **4.14e9**, while Table 7 and Section 4.3 print **4.14e8**; the bar's apparent height also supports the latter. The figure-label comparison fails with 90% relative error (using the annotation as reference).
- Hub HC: Figure 4/Table 7 print **1.49e8**, while the prose prints **1.48e8**; relative error is 0.675676% against the prose.

Overall: **16 of 20 source-consistency checks pass**, including 7/8 figure annotations, 7/8 prose totals, and 2/4 reduction claims. These are not 20 independent validations and are not a statistical confidence score.

Three-significant-figure source precision can explain small reduction discrepancies; the precise original model outputs are unavailable here. We neither increase input precision nor replace a strict failure with a rounding exemption. The plotted kg units are retained **as printed**, despite an apparent order-of-magnitude issue: hub NOx/fuel is about 40 kg/kg, unlike Table 3's emission indices in tens of g/kg. No guessed factor-of-1000 correction is applied. Emission mass also does not establish population exposure or health burden.

## Layout

- `data/reported_values.json`: distinct Table 7, Figure 4 annotation, and Section 4.3 values; decimal strings preserve reported precision.
- `data/sources/`: publisher XML, unchanged Figure 4 PNG, Crossref metadata, acquisition URLs and SHA-256 hashes.
- `src/netenv/core.py`: validation, unit utility, arithmetic and numerical agreement checks.
- `src/netenv/figures.py`: figure plus CSV/JSON exports and a deterministic run manifest.
- `src/netenv/page.py`, `templates/page.html`: static site generation from the same data/audit.
- `tests/`: hand-derived fixtures, invariance/round-trip tests, real plotted-height tests, output replay, source checks, CLI behavior and static page/link checks.
- `evidence/`: actual red/green runs, including an honestly retained initial environment error.
- `outputs/`: generated static artifacts for GitHub Pages. Generation overwrites these known generated files; raw sources are not modified. The page is overwritten only when explicitly requested with `--page`.
- `SPECIFICATION.md`, `SKILLS.md`, `DEVLOG.md`: declared contract, skill attribution/use, and factual process log.

`outputs/run_manifest.json` records environment/code hashes and hashes of every generated scientific artifact. SVG/PNG/CSV/JSON replay is tested byte-for-byte within the same locked environment. Identical image bytes across different OS/font stacks are not promised.

## Skills and tests

The three new `.pi/skills/` workflows were explicitly loaded and used. See [`SKILLS.md`](SKILLS.md) for reviewed community sources and concrete actions, and [`evidence/README.md`](evidence/README.md) for observed red/green steps. Existing class skills are untouched. Tests of actual source disagreement stay green because they verify correct detection; the separate **paper-agreement command stays red**. No numerical acceptance test is skipped or marked xfail to hide a disagreement.

Property tests apply to the implemented arithmetic: common mass-unit scaling leaves a percentage reduction unchanged; g/kg/t conversions round-trip; zero/equal baselines and negative reductions behave correctly. There is no claim of flow conservation or flight-physics testing before such a model exists.

## Stronger next step

To go beyond this first pass, implement the paper's emissions accounting (Equations 9–21) on a small, explicitly specified network, with tests for passenger/leg accounting, phase integration, g/kg-to-kg conversion, and capacity. But the original OD demand matrix, full trajectory histories, and BADA coefficients are not supplied in the archived article. Public proxies would make this a **reduced adaptation**, not an exact reproduction of Figure 4. Resolve those inputs and unit inconsistencies before claiming quantitative physical agreement. No such adaptation has been run yet.

## Attribution

[Full citation](CITATIONS.bib). Original Figure 4 and publisher XML: Sun et al. (2021), **CC BY 4.0**, https://creativecommons.org/licenses/by/4.0/. The source image is unchanged. The redrawn plot changes typography/layout and explicitly selects Table 7 for the conflicting HC total; its source choice and scope are disclosed in the figure and page. Crossref metadata is archived to verify the citation and license. Local skills are original, attributed adaptations of the community guidance listed in `SKILLS.md`.
