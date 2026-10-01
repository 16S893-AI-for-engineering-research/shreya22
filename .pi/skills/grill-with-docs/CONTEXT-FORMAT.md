# Glossary format

Prefer the project's existing glossary and conventions. If none exists and the
user approves a new file, use a small `CONTEXT.md`:

```markdown
# Heat-transfer model

Terminology for the model and its experimental comparison.

## Language

**Temperature observation**: A sensor reading with a timestamp, position, units,
and estimated measurement uncertainty. Not an exact boundary condition.

**Solver residual**: The norm of the discrete equation imbalance used to monitor
iteration. Specify the norm and normalization when reporting a value.
_Avoid_: Error, unless the type of error is stated.
```

- Define terms in one or two sentences. Include units, conventions, or distinctions
  when they affect interpretation; do not invent values to fill a template.
- Include scientific terms whose meaning needs agreement in this project, not a
  general textbook or programming glossary.
- List alternative terms to avoid only when they would cause confusion.
- Keep algorithms, solver settings, and implementation plans in their own docs.
- If the repository has several areas, follow its context map. Do not create a
  multi-area structure for a small project or force an existing format into this one.
