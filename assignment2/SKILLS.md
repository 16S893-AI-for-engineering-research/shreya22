# Skills actually used

Three new, project-local skills were authored, explicitly read, and applied during this work. The existing class-provided skills were not changed. These are task-specific adaptations of reviewed community workflows, not installations of upstream scripts or the full collections.

| Local skill | Reviewed community source | Observable use |
|---|---|---|
| [`paper-result-audit`](../.pi/skills/paper-result-audit/SKILL.md) | [K-Dense citation-management](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/citation-management) and [scientific-critical-thinking](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/scientific-critical-thinking) | Checked DOI/license; read original Figure 4, Table 7 and prose; preserved conflicting values; declared the canonical source and tolerance before coding. The audit does not turn a source discrepancy into a passing result. |
| [`scientific-tdd`](../.pi/skills/scientific-tdd/SKILL.md) | [obra/superpowers test-driven-development](https://github.com/obra/superpowers/tree/main/skills/test-driven-development), including `writing-good-tests.md` | Actual observed red/green runs; independently derived arithmetic fixtures; Hypothesis scale invariance and mass-unit round trips; CLI strict failure behavior. See [evidence](evidence/README.md). |
| [`reproduction-figure`](../.pi/skills/reproduction-figure/SKILL.md) | [K-Dense scientific-visualization](https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/scientific-visualization), including `references/publication_guidelines.md` | Zero-baseline common axis; original four metrics/network order; tests of actual bar heights; preserved source image and source-choice caveat; accessible numeric table; deterministic SVG/PNG exports, inspected visually. |

## Reviewed source snapshots

Read during planning/implementation; identifiers below are Git **blob** SHA-1s of the reviewed file contents, not branch names or commit IDs. They identify the guidance actually reviewed even if upstream `main` changes. Upstream collections publish these workflows under MIT licenses. Local skill prose is a new, attributed, task-specific synthesis.

| Upstream file | Git blob SHA-1 |
|---|---|
| obra `test-driven-development/SKILL.md` | `46838cc9e893c06259e3bdad0fc2425b36242f15` |
| obra `test-driven-development/writing-good-tests.md` | `d3c4482fd30b4f9084bb61545fe73939a7502c28` |
| K-Dense `citation-management/SKILL.md` | `0badad49983aa42ca24387380165bf52c1a46308` |
| K-Dense `scientific-critical-thinking/SKILL.md` | `e12cbea98525f5d728fa2ef74b72f9c09b9fef73` |
| K-Dense `scientific-visualization/SKILL.md` | `13f3ae9b115aefc2f15098bcfd460f0b96e047c2` |
| K-Dense `scientific-visualization/references/publication_guidelines.md` | `dea3863b9d851a42c7f7fa06779d6edc15b6a495` |

## What was deliberately not adopted

- No broad systematic literature review; one already-selected paper was audited.
- No paid search/image-generation APIs, generative numerical figures, or upstream installer scripts.
- No tests asserting that a hub network must always emit less. That is a conditional model result, not an invariant.
- No claim that reported NOx mass equals exposure, health burden, or a validated physical model.
- No silent corrections to suspect units and no tolerance changes to get green results.

For future Pi sessions, these skills can be explicitly loaded with `/skill:paper-result-audit`, `/skill:scientific-tdd`, and `/skill:reproduction-figure` after reloading project skills.
