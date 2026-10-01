# Decision records

Architecture/design decision records for "Air Quality Impacts of Changing
Airline Network Structures" (16.S893, Shreya Sharma). Recorded during
design interviews with the `grill-with-docs` skill when a choice is costly
to reverse, needs explanation for a future reader, and involves a real
trade-off. See `../CONTEXT.md` for terminology, not decisions.

Numbered oldest first: `NNNN-short-title.md`. Status is `proposed`,
`accepted`, or `superseded` (link to the record that supersedes it).

- [0001](0001-binary-network-comparison.md) &mdash; binary network comparison, not an optimization or a spectrum
- [0002](0002-fleet-assignment-fallback.md) &mdash; fleet-assignment fallback: carrier case study if aircraft-ownership data is unavailable
- [0003](0003-comparison-metric-population-weighted-exposure.md) &mdash; comparison metric: population-weighted exposure, not per-passenger-km burn
- [0004](0004-adjoint-runs-complete-and-prior-art-scope.md) &mdash; GEOS-Chem adjoint runs complete; mortality pipeline is prior art, not the novel contribution
- [0005](0005-geographic-scope-us-primary-eu-stretch.md) &mdash; geographic scope: US primary, EU stretch; global demand data not available at required resolution
- [0006](0006-adjoint-raw-sensitivities-not-health-weighted.md) &mdash; health pipeline: adjoint gives raw sensitivities, CRFs applied separately (unconfirmed)
- [0007](0007-point-to-point-feasibility-cutoff.md) &mdash; point-to-point network: feasibility cutoff by assigned aircraft, not fully connected
- [0008](0008-db1b-not-t100.md) &mdash; US demand data source: BTS DB1B, not T-100
