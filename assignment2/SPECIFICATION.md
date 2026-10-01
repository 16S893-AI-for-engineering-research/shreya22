# First-pass contract (before implementation)

Target: Sun et al. (2021), DOI 10.3390/su13020465, Figure 4. Only total fuel burn, NOx, CO, and HC; two networks in the source order; no CO2 extension. This is an aggregate-data reconstruction and arithmetic/source-consistency audit, not an independent model reproduction. The paper's ten-airport Chinese case is not a change to the semester project's US/exposure scope.

## Source selection

Use **Table 7's Base Case columns** as a named, consistent source for all eight plotted numbers. Preserve all Figure 4 annotations and Section 4.3 totals separately. Figure 4's city HC annotation prints `4.14e9`, while its bar height appears consistent with `4.14e8`, as printed in Table 7 and Section 4.3. Hub HC is `1.49e8` in the figure/table but `1.48e8` in the prose. Do not silently reconcile or rescale these values. Preserve the original figure unchanged alongside the reconstruction. Units remain kg as printed, despite order-of-magnitude concerns.

## Quantitative acceptance

- User tolerance: **0.1% relative error**, fraction `0.001`, inclusive.
- Error = `abs(computed - reference) / abs(reference)`; compare unrounded numbers.
- Report percent relative error (`100 * error`) and percentage-point difference separately for reduction percentages.
- Percent reduction = `100 * (city - hub) / city`. Negative reductions are valid; a zero city baseline is undefined and rejected.
- Compare reconstructed base-case totals with each printed figure label and each prose total, and recompute the four stated reductions from base-case totals.
- A zero reference matches only exact zero (no hidden absolute tolerance).
- Do not round inputs, tune a parameter, or widen tolerance to force agreement. Limited source precision may explain small discrepancies but does not turn a strict failure into a pass.
- Overall paper agreement fails if any individual comparison fails. CLI strict checking must return nonzero in that case.
- Software tests passing only establishes correct arithmetic, source handling, rendering, and discrepancy detection.

## TDD increments

1. Decimal arithmetic, input validation, source comparisons, percent reductions; unit-scale invariance and conversion round trips. Hand-check simple baselines independently.
2. Real plotted bar heights, source order/labels, zero baseline, deterministic exports and CSV/JSON outputs; no CO2 series.
3. Reproduction CLI and static page; strict failure exit code, generated page/data consistency and working local asset links.

Keep real red/green transcripts in `evidence/`. No network access is required for tests or regeneration after `uv sync --locked`. Figure heights are source transcription, not independent numerical validation. Do not implement a toy network and label it this paper's model.
