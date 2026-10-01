---
name: reproduction-figure
description: Reconstruct a published numerical figure from auditable data with Matplotlib. Use for exact variable selection, truthful axes, source discrepancies, export verification, and accessible static research pages.
---

# Reproduction figure

1. Inspect the original figure. Preserve its variable selection, category order, network names, units, and meaningful axis scaling unless a deviation is explicitly justified.
2. Do not add new metrics or silently repair source data. Identify whether the plotted values came from annotations, tables, digitization, or calculations. Keep originals unchanged.
3. Plot bars from zero on a common linear axis when that is the source encoding. Do not visually enlarge small bars or use a second axis to hide differences in magnitude.
4. Generate figures only from numeric data. No generative-image service, guessed intermediate points, smoothing, or decorative uncertainty bars.
5. Keep plotted values, downloadable data, numerical audits, and page tables consistent. Provide an accessible table and descriptive alt text; preserve legend order and fixed left/right order in each group.
6. Export static SVG and PNG with recorded data/code provenance. Avoid timestamps and random SVG identifiers when deterministic replay is required.
7. Test actual plotted bar heights, labels, and axes. Inspect the exported image at its delivery size for clipping and readability.
8. State the source license and attribution. Describe any changed styling or source-version choices in the caption. Never equate visual resemblance with validation of the underlying model.

## Sources and adaptation

Original task-specific synthesis informed by K-Dense's `scientific-visualization` skill and `references/publication_guidelines.md`:
https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/scientific-visualization

See `assignment2/SKILLS.md` for reviewed source identifiers and execution evidence. No journal-compliance claim or paid service is required.
