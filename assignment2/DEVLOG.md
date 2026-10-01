# Assignment 2 devlog

Dates use UTC. Human reflections belong in the website's `yourNotes` field; none are invented here.

## 2026-10-01 — scope, source audit, and setup

- User approved a first-pass reconstruction of Sun et al. (2021) Figure 4, with 0.1% tolerance, no extra CO2 series, and a separate project page. A smaller model remains a potential next step, not something silently substituted for this result.
- Read `CONTEXT.md` and relevant decision records. The assignment uses the published Chinese case and reported emissions; it does not change the semester project's US demand/exposure scope.
- Inspected the original 4090 × 3067 Figure 4 PNG, publisher XML and Table 7, and Crossref metadata. Found a factor-of-ten HC annotation discrepancy (city: figure label 4.14e9, table/prose 4.14e8), plus a hub HC discrepancy (figure/table 1.49e8, prose 1.48e8). Informed the user before coding. Chose all plot inputs from Table 7 Base Case, with original annotations retained separately.
- Recorded the numerical contract in `SPECIFICATION.md` before writing calculations. Relative error is distinguished from percentage-point error; source mismatches will not be excused by relaxing the tolerance.
- Read upstream TDD, test-design, scientific-critical-thinking, and scientific-visualization guidance. Added and explicitly loaded three small project-specific skills, with attribution; did not modify the existing class skills. No upstream scripts or paid APIs are used.
- `uv` was unavailable in PATH. Downloaded the official macOS arm64 uv 0.12.21 archive, verified SHA-256 against GitHub release metadata, and unpacked it under ignored `.tools/uv/`. Python 3.12.12 and the environment are managed by uv. No shell profile, global agent settings, or sandbox permissions were changed.

## 2026-10-01 — observed TDD cycles

- The first test attempt failed at collection: uv had built the editable package before the package files existed. Kept that output as `evidence/00-setup-collection-error.txt`, reinstalled only the editable project, and did **not** count the import error as a successful red phase.
- Arithmetic/source audit: observed **37 failures** against importable unimplemented contracts, then **37 passes** after minimal implementation. Independent fixtures establish the city baseline, negative reductions, finite-input validation, and inclusive 0.1% relative threshold. Hypothesis exercises unit-scale invariance and g/kg/t round trips.
- Figure/export: observed **4 new failures, 38 passes**, then **42 passes**. Checked real Matplotlib bar heights and the common zero baseline, source order, original four quantities, CSV/JSON round trips, output hashes, and byte-identical SVG/PNG replay. A separate test independently reads Table 7 from the preserved XML to check transcription.
- CLI/page delivery: observed **4 new failures, 43 passes**, then **47 passes**. Tested real subprocess exit codes, input rejection, generated numeric tables, HTML escaping, local asset links, and archived source hashes/citation metadata. The strict CLI must return failure for paper disagreement even when plotting succeeds.
- Genuine transcripts are in `evidence/01-*` through `06-*`. Source-provenance tests that already passed are not misrepresented as implementation red/green cycles.

## 2026-10-01 — numerical result and artifacts

- Reconstructed the figure from Table 7 Base Case, with original metric order, network order, orange/blue bars, common linear axis, printed kg units, and source-choice disclosures. Saved SVG/PNG, plotted data CSV, all comparisons CSV, audit JSON, and a hashed run manifest. The original Figure 4 PNG remains unchanged and is attributed under CC BY 4.0.
- The recomputed reductions are fuel **64.308511%**, NOx **51.356239%**, CO **58.631579%**, HC **64.009662%**. Relative errors against the stated reductions are **0.111043%, 0.046246%, 0.014361%, 0.140933%**, respectively. Only NOx and CO reductions meet the user's 0.1% relative criterion. Rounded published inputs may explain small gaps; no tolerance was relaxed.
- The source-consistency audit passes 16/20 checks: 7/8 figure annotations, 7/8 prose totals, and 2/4 reduction claims. These are not independent validations or a confidence score. The city HC figure annotation and the hub HC prose total fail in addition to the two reduction claims.
- Retained suspected mass-unit problems as warnings, not corrections: the printed hub NOx/fuel ratio is about 40 kg/kg, far from Table 3's emission-index scale. Did not add CO2 or infer exposure/health effects from these totals.
- Generated `project-assignment2.html` and linked it under Project → Assignment 2. Tables come from the same executable pipeline as the figure. Added `.nojekyll` so GitHub Pages serves this static site and its Markdown/Python/data downloads without conversion. Existing proposal/modeling decisions were not edited.

## 2026-10-01 — verification and assessment

- Regenerated artifacts offline from the locked environment; ordinary generation returned 0, while `--check-paper` correctly returned **1** with all four failed comparisons named. Captured both runs.
- Created a separate fresh, ignored virtual environment and ran `uv sync --locked --offline` followed by the complete suite: **47 passed**. This verifies environment reconstruction from the local cache, not an independent download on another OS.
- Visually inspected the exported PNG. A headless Chrome run with a fresh temporary profile produced a 1440 × 1100 desktop screenshot and rendered DOM; both were inspected, including active Project/Assignment 2 navigation. The command nevertheless timed out after 60 seconds during/after shutdown and logged macOS display/allocator warnings. Kept that limitation explicit; no clean browser-exit, mobile-layout, or zero-console-error claim is made. Did not use the real Chrome profile, change permissions, or disable the browser sandbox.
- **Assessment:** this first pass is computationally trivial and primarily an aggregate reconstruction/arithmetic audit. It is not sufficient to claim the underlying network/emissions model was reproduced. A smaller, genuinely computed network would be the next step, but unavailable original OD/trajectory/BADA inputs and the units must be addressed. Public proxies would make it an explicitly labelled adaptation, not an exact Figure 4 rerun. No smaller model has been run yet.
- Added the factual portfolio devlog entry and left `yourNotes` blank for the user's own reflections. No commit or push was made.
