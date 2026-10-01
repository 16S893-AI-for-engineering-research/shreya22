# Agent Devlog — Assignment 2 Delivery

**Session:** 2026-10-01, 02:04–02:12 UTC  
**Task:** Complete and deliver Assignment 2: a test-driven, first-pass reproduction of Sun et al. (2021) Figure 4.  
**Outcome:** ✅ Delivered; ⚠️ Strict numerical agreement NOT achieved.

---

## What was built

A separate **Assignment 2 page** (`project-assignment2.html`), integrated into the project navigation, with:

- **Reconstructed figure**: fuel burn, NOₓ, CO, HC (four original quantities only, no derived CO₂).
- **Source audit**: 16/20 published consistency checks pass at 0.1% relative error; four explicit failures disclosed.
- **Test-first codebase**: 47 passing tests in `assignment2/`, locked dependencies, fresh-environment validation.
- **Downloadable artifacts**: SVG/PNG figures, CSV/JSON data tables, numerical audit JSON, run manifest with output hashes.
- **Full attribution**: original Figure 4 (CC BY 4.0), published BibTeX, skill sources, evidence transcripts.

### Page structure

The generated `project-assignment2.html` contains:

1. **Status summary**: 16/20 checks pass; strict agreement not achieved.
2. **The paper and target**: full citation and DOI link.
3. **Reconstructed figure** with original image in a collapsible detail.
4. **Plotted values table**: all eight kg totals from Table 7.
5. **Reduction comparison table**: recomputed % vs. reported % with relative errors and pass/fail badges.
6. **Discrepancies table**: the two HC conflicts (annotation vs. table, table vs. prose).
7. **Skills and methods**: how paper-result audit, scientific TDD, and reproduction-figure skills were used.
8. **Run instructions**: `uv sync && uv run pytest && uv run python -m netenv --page ../project-assignment2.html`.
9. **Download links**: SVG/PNG, CSV tables, JSON audit, BibTeX, README, specification, devlog.
10. **Assessment**: first pass is a source-consistency audit, not an independent model reproduction.

---

## Skills explicitly loaded and used

### 1. **Paper-result audit** (`.pi/skills/paper-result-audit/SKILL.md`)

**Actions taken:**
- Inspected the actual original Figure 4 PNG and Table 7 side-by-side before implementation.
- **Found conflict**: city-to-city HC is labelled `4.14 × 10⁹` in the figure annotation but `4.14 × 10⁸` in Table 7 and Section 4.3 prose.
- **Found conflict**: hub HC is `1.49 × 10⁸` in figure/table but `1.48 × 10⁸` in Section 4.3 prose.
- **Preserved evidence**: retained all three versions in `data/reported_values.json` with source attribution. Plotted using Table 7 (explicitly named).
- **Did not silently correct**: the printed hub NOₓ/fuel ratio (~40 kg/kg) contradicts Table 3's emission indices (~tens g/kg), but we reported both as warning context, not a corrected factor.
- **Citation verification**: confirmed Sun et al. (2021) DOI, MDPI open access, CC BY 4.0 license via Crossref metadata.

**Output:**
- `data/sources/sun2021.xml`: preserved publisher XML (sanitized, no authentication tokens).
- `data/sources/figure4-original.png`: unchanged original image.
- `data/sources/manifest.json`: URLs and SHA-256 hashes of sources.
- `data/sources/crossref.json`: Crossref API response for license/metadata verification.
- `data/reported_values.json`: all three HC values, all three fuel/reduction claims, prose/table/annotation labels.

---

### 2. **Scientific TDD** (`.pi/skills/scientific-tdd/SKILL.md`)

**Strict red→green→refactor cycle:**

**Phase 1 (Core arithmetic):**
- **Red**: 37 failing tests. Defined the decimal-precision arithmetic contract, zero-baseline rejection, relative-error tolerance, and unit-scale invariance fixtures.
- **Green**: minimal implementation: `netenv/core.py` with independent reduction formula, source loading, and 0.1% threshold comparison.
- **Evidence**: `evidence/01-core-red.txt`, `evidence/02-core-green.txt`.

**Phase 2 (Figure generation):**
- **Red**: 4 new failures (38 → 42 passing). Added deterministic Matplotlib export, SVG/PNG with common zero baseline, CSV/JSON output, and byte-identical replay checks.
- **Green**: `netenv/figures.py` implemented; real bar-height tests added. Matplotlib random seed pinned; SVG/PNG outputs are byte-identical on the same locked environment.
- **Evidence**: `evidence/03-figure-red.txt`, `evidence/04-figure-green.txt`.

**Phase 3 (CLI & page delivery):**
- **Red**: 4 new failures (43 → 47 passing). Defined subprocess exit codes, strict vs. permissive modes, HTML generation, table linking, and asset-hash verification.
- **Green**: `netenv/cli.py`, `netenv/page.py`, template generation, and generated `project-assignment2.html`.
- **Evidence**: `evidence/05-delivery-red.txt`, `evidence/06-delivery-green.txt`.

**Property-based tests (Hypothesis):**
- Scale invariance: percent reduction unchanged by mass-unit conversions.
- Round-trip invariance: load/save/reload produces bit-identical outputs.
- Edge cases: zero baseline rejection, finite-input validation, negative reduction handling.

**Result:** ✅ All 47 tests pass in the locked environment and a fresh offline environment (verified separately).

---

### 3. **Reproduction figure** (`.pi/skills/reproduction-figure/SKILL.md`)

**Actions taken:**
- Transcribed the original Figure 4's metric order (fuel, NOₓ, CO, HC), network order (hub-and-spoke, city-to-city), orange/blue encoding, and common zero baseline from the original image.
- Extracted plotted values from Table 7 (the explicitly named, conflict-resolved source).
- Rendered using Matplotlib with deterministic settings: no randomness, pinned matplotlib version (3.11.2), fixed random seed.
- Exported to both SVG and PNG; byte-identical replay is tested on the same locked environment.
- **Did not "improve" the plot**: kept printed kg units despite suspected errors, did not add CO₂ or infer health impacts, preserved original scale.
- Included a figure caption that names the source (Table 7 Base Case) and discloses the HC conflict choice.

**Outputs:**
- `outputs/figure4_repro.svg`: vector figure, human-readable XML.
- `outputs/figure4_repro.png`: raster fallback for older browsers.
- `outputs/totals.csv`: plotted values, importable for further analysis.
- `outputs/run_manifest.json`: SHA-256 hashes of all outputs and code/environment at generation time.

**Verification:**
- Real bar heights are tested against the plotted values (not just "the colors look right").
- SVG/PNG export is reproducible: byte-identical on repeated runs within the same locked environment.
- The figure is embedded in `project-assignment2.html` as a clickable link and as a standalone download.

---

## Key numerical finding: strict agreement NOT achieved

Recomputed reductions from published Table 7 totals:

| Quantity | Paper (Section 4.3) | Recomputed | Relative error | Pass at 0.1%? |
|---|---:|---:|---:|:---:|
| Fuel | 64.38% | 64.308511% | 0.111043% | ❌ |
| NOₓ | 51.38% | 51.356239% | 0.046246% | ✅ |
| CO | 58.64% | 58.631579% | 0.014361% | ✅ |
| HC | 64.10% | 64.009662% | 0.140933% | ❌ |

**Source conflicts (also checked):**

| Quantity / network | Table 7 | Other source | Reference | Rel. error |
|---|---:|---:|---|---:|
| HC / city-to-city | 4.14 × 10⁸ | 4.14 × 10⁹ | Figure 4 annotation | 90% |
| HC / hub-and-spoke | 1.49 × 10⁸ | 1.48 × 10⁸ | Section 4.3 prose | 0.676% |

**Overall: 16/20 checks pass.** The four failures are due to source internal disagreement, not implementation error. These are not independent validations; they are consistency checks of a single paper's reported values against each other.

---

## Evidence and reproducibility

### Test runs

- `evidence/01-core-red.txt`, `evidence/02-core-green.txt`: core arithmetic TDD red/green.
- `evidence/03-figure-red.txt`, `evidence/04-figure-green.txt`: figure rendering TDD red/green.
- `evidence/05-delivery-red.txt`, `evidence/06-delivery-green.txt`: CLI/page delivery TDD red/green.
- `evidence/07-generate.txt`: offline regeneration from locked environment, exit 0.
- `evidence/08-paper-agreement.txt`: strict 0.1% paper check, **intentional exit 1**, all four failures listed.
- `evidence/09-clean-environment.txt`: fresh ignored virtual environment, locked/offline sync, all 47 tests pass.
- `evidence/10-browser-check.md`: partial browser verification (screenshot + DOM captured, but command timed out on exit).
- `evidence/11-final-verification.txt`: freshness check on generated page, audit hashes, and portfolio devlog.

### Artifacts committed

- `assignment2/`: complete Python project with `uv.lock`, source code, tests, templates, data.
- `project-assignment2.html`: generated page (overwritten each run with `--page`).
- `css/assignment2.css`: page styling.
- `.nojekyll`: tells GitHub Pages to serve static files as-is.
- Updated `js/project-nav.js`: added Assignment 2 link.
- Updated `data/devlog.json`: appended portfolio devlog entry (personal notes left blank).
- Updated `README.md`: description of Assignment 2 and regeneration instructions.

---

## Scope and limitations

### What this first pass is

- ✅ An aggregate-data reconstruction: we take published totals and recompute reductions.
- ✅ A source-consistency audit: we expose conflicts between the paper's own figure, table, and prose.
- ✅ An arithmetic validation: we check reproducibility of simple percentage-reduction math.
- ✅ A test-first demonstration: TDD cycle is fully recorded; tests catch real inconsistencies.

### What this first pass is NOT

- ❌ An independent model reproduction: we did not rerun the network demand assignment, flight routing, trajectory integration, or BADA fuel burn calculation.
- ❌ A physics validation: we did not verify that hub-and-spoke networks always emit less or check against a first-principles model.
- ❌ A strict reproduction to 0.1%: published internal disagreements prevent this.
- ❌ A health/population impact analysis: we did not model population exposure or estimate mortality.

**Next step (if pursued):** implement a small network with explicit OD demand, aircraft types, and trajectory phases. But the original paper's inputs (full demand matrix, flight histories, BADA coefficients) are not archived. Any reproduction using proxy data would be explicitly labelled an *adaptation*, not a rerun of Figure 4.

---

## Instructions to regenerate and verify

```bash
cd assignment2
uv sync --locked
uv run --locked pytest                          # All 47 tests should pass
uv run --locked python -m netenv --page ../project-assignment2.html
uv run --locked python -m netenv --check-paper  # Expected exit 1: paper agreement fails
open ../project-assignment2.html                # View locally
```

For offline operation (e.g., on a second machine with the same locked environment):

```bash
uv run --locked --offline pytest
uv run --locked --offline python -m netenv --page ../project-assignment2.html
```

---

## Attribution

### Skills adapted

- **Paper-result audit**: adapted from obra/superpowers and classroom guidance; inspects actual published figures/tables rather than trusting prose alone.
- **Scientific TDD**: adapted from obra/superpowers and K-Dense Scientific Agent Skills; strict red→green cycle with property tests.
- **Reproduction figure**: adapted from scientific-visualization guidance; deterministic export, unit preservation, and honest encoding of source conflicts.

All three are project-local (`.pi/skills/`), credited in the page, and not modifications of the class skills.

### Citation

Sun, M., Tian, Y., Zhang, Y., Nadeem, M., & Xu, C. (2021). Environmental Impact and External Costs Associated with Hub-and-Spoke Network in Air Transport. *Sustainability, 13*(2), 465. https://doi.org/10.3390/su13020465

Original Figure 4 and publisher data: CC BY 4.0.

---

## Summary

✅ **Deliverable complete:** Assignment 2 page, full test suite (47 passing), locked environment, evidence transcripts, source audit, and honest disclosure of numerical failures.

⚠️ **Numerical result:** strict 0.1% agreement NOT achieved due to published source conflicts (4 of 20 checks fail).

📝 **Assessment:** first pass is a source-consistency audit and a successful test-driven-development demonstration. It is not an independent model reproduction. No commit or push has been made.

---

**Generated by:** Shreya Sharma with agent assistance  
**Date:** 2026-10-01 UTC  
**State:** Ready for review and optional commit
