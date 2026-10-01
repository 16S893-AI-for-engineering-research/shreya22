---
name: scientific-tdd
description: Implement scientific Python calculations using uv and observed red-green-refactor cycles. Use for research reproduction code, dimensional/property tests, and numerical discrepancy checks.
---

# Scientific TDD

1. Write down the scientific contract and the specific mistake each test should catch before implementing a calculation.
2. Derive expected values independently: hand calculations, analytical limiting cases, or cited source observations. Do not compute the expected value using the function under test.
3. Write tests first. An importable stub is permitted; observe failures caused by missing behavior, not dependency or collection errors. Save the real command, output, and exit status.
4. Implement only the behavior needed, run the tests again, then refactor while keeping them green.
5. Prefer meaningful properties: unit/scale invariance, serialization round trips, limiting cases, and conservation only when the implemented model actually represents that conserved quantity.
6. Never require a hypothesized outcome (e.g. hubs always emit less) as a universal invariant. Allow negative reductions when a comparison case is worse.
7. Reject invalid/nonfinite inputs and undefined baselines explicitly. Distinguish relative tolerances from absolute tolerances.
8. Use `uv` for Python, dependencies, and commands; commit the lockfile. Test the locked environment and run the whole suite before reporting completion.
9. Keep software correctness distinct from agreement with a publication. A strict paper check must report failure if the agreed tolerance is exceeded, even when code tests pass.

## Sources and adaptation

Original scientific-Python adaptation of the red-green-refactor and independent-expectation guidance in:
https://github.com/obra/superpowers/tree/main/skills/test-driven-development

Upstream's `SKILL.md` and `writing-good-tests.md` were read before implementation. See `assignment2/SKILLS.md` for identifiers and use evidence. No upstream scripts are executed or installed.
