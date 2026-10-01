---
name: paper-result-audit
description: Audit a published numerical result before reproducing it. Use to verify source values, units, figure/table disagreements, and numerical acceptance criteria; not for a broad literature review.
---

# Paper result audit

1. Verify the DOI, authors, title, and reuse license against the publisher or Crossref. Read the actual target figure, not just the abstract or prose.
2. Preserve the original source files and acquisition URLs with SHA-256 hashes. Separate raw evidence, transcribed inputs, and generated outputs.
3. State whether the task recomputes a model, recomputes derived quantities from published aggregates, or only redraws data. Never claim these are equivalent.
4. Compare figure annotations, associated tables, and narrative claims. If they disagree, preserve all versions and name the source chosen for the reconstruction. Do not silently fix a suspected error.
5. Define the error formula and tolerance before implementation. Distinguish relative percent error from percentage-point differences. Never relax a tolerance to obtain a pass.
6. Check unit conversions and order of magnitude. Treat suspected unit errors as unresolved evidence, not permission to rescale the data.
7. Record failures separately from software-test results. Tests may verify correct detection of an inconsistent paper; that does not establish scientific reproduction.
8. End with the strongest claim supported by the evidence and the missing inputs needed for the next level of reproduction.

## Sources and adaptation

Original, project-specific synthesis informed by K-Dense's `citation-management` and `scientific-critical-thinking` workflows:
- https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/citation-management
- https://github.com/K-Dense-AI/scientific-agent-skills/tree/main/skills/scientific-critical-thinking

Reviewed source identifiers and actual usage are recorded in `assignment2/SKILLS.md` at the repository root. No external service, installation, or image-generation step is required. Do not change the existing class-provided skills.
