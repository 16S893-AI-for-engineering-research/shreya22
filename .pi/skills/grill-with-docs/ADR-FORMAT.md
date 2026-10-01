# Decision-record format

An architecture decision record (ADR) preserves a consequential choice and its
reason. Follow existing naming and formatting conventions. Otherwise use
`docs/adr/0001-short-title.md`, taking the next unused number. Create the directory
only when the user approves the first record; never overwrite an existing record.

```markdown
# Use a conservative discretization for transport

Status: accepted

The study compares integrated tracer mass across mesh resolutions. We chose a
finite-volume discretization so internal face fluxes cancel in the domain balance.
A pointwise finite-difference scheme was considered, but would require a separate
conservation treatment. This choice adds face-flux bookkeeping; it does not remove
time-integration error or guarantee positivity.
```

The example illustrates a trade-off, not a recommendation for every transport model.
Record the project's actual assumptions, alternatives, and consequences. Cite the
supporting experiment or analysis when available.

Offer a record only when all three apply:

1. Reversing the choice would require meaningful work.
2. A future reader would need the rationale to understand it.
3. The choice involved genuine alternatives.

Examples include a data format used by several laboratories, a model approximation
that constrains the study, or a solver backend with a costly migration. Routine
parameter tuning usually belongs in experiment configuration, not an ADR.

Use `proposed` until the user accepts the choice. If it replaces an accepted
decision, link both records and mark the old one superseded without erasing its
rationale. Keep the record as short as the decision allows.
