# Project terminology

Glossary for "Air Quality Impacts of Changing Airline Network Structures"
(16.S893 project, Shreya Sharma). Definitions agreed during design
interviews with the `grill-with-docs` skill. Implementation choices and
their rationale live in `docs/adr/`, not here.

## Language

**Fixed demand**: The origin&ndash;destination passenger volume, held
constant across the hub-and-spoke and point-to-point comparison. Resolution
is opportunistic — per-flight O&ndash;D counts if available, otherwise the
coarsest resolution the data supports (e.g., annual O&ndash;D pairs).
Routing and aircraft assignment respond to demand; demand itself does not
change between the two scenarios being compared.

**Geographic scope**: US primary, with EU as a possible stretch region.
Not global &mdash; route-level origin&ndash;destination passenger demand
at the resolution this project needs is not freely available globally as
a single dataset. See
`docs/adr/0005-geographic-scope-us-primary-eu-stretch.md`.

**Network structure (this project)**: A binary choice between two discrete
network designs &mdash; hub-and-spoke and point-to-point &mdash; built to
serve the same fixed demand. Not a continuous hub-centricity spectrum, and
not the output of a fuel-minimizing optimizer.
_Avoid_: describing this as a "spectrum from hub-consolidated to fully
point-to-point" (see `docs/adr/0001-binary-network-comparison.md`) &mdash;
`project-methods.html` currently uses that language and is stale.

**Comparison metric**: Air quality and health outcomes are compared as
population-weighted exposure and health burden, not per-passenger-kilometre
fuel burn or emissions. See
`docs/adr/0003-comparison-metric-population-weighted-exposure.md`.

**Novel contribution (this project)**: Applying an established
emissions-to-mortality pipeline (GEOS-Chem adjoint sensitivities, Turner et
al. ozone and GEMM PM2.5 concentration-response functions) to compare how
hub-and-spoke vs. point-to-point network structures spread emissions
differently in space, for the same fixed demand. The pipeline itself is
prior art, not the contribution &mdash; see
`docs/adr/0004-adjoint-runs-complete-and-prior-art-scope.md`.

**Published results**: Must be reproduced on open data only, even though
development/validation may use proprietary data already in hand. The
proprietary-data run and the open-data run are not assumed to agree. See
`docs/adr/0004-adjoint-runs-complete-and-prior-art-scope.md`.

**US demand data**: BTS DB1B (itinerary-level, 10% ticket sample), not
T-100 (nonstop segment traffic only). See `docs/adr/0008-db1b-not-t100.md`.

**Point-to-point network (construction rule)**: Not fully connected. A
route is served nonstop only if the aircraft assigned to it is
range-feasible for that stage length; otherwise it remains connecting even
in the point-to-point scenario. See
`docs/adr/0007-point-to-point-feasibility-cutoff.md`.
