# Binary network comparison, not an optimization or a spectrum

Status: accepted

The project compares exactly two discrete network designs &mdash;
hub-and-spoke and point-to-point &mdash; built to serve the same fixed
demand, rather than optimizing across a continuous hub-centricity spectrum
or solving for a fuel-minimizing network structure. This matches the
proposal's HOW section (no optimizer among the planned tasks) and the
research question ("the choice between hub-and-spoke and point-to-point
networks").

`project-methods.html` (Task 2, "Re-optimise network structure") and its
figure language ("a family of candidate networks indexed by how
hub-centric they are") describe the older fuel-optimization framing and
have not been updated to match. `project.html` Fig 2 already frames this
correctly as two named structures. Reconcile `project-methods.html` before
relying on it.

A binary comparison is simpler to build and defend with proxy/partial data
than an optimizer, at the cost of not showing how the AQ outcome varies
continuously with hub-centricity. Reversing this later (introducing a
spectrum or an optimizer) would require re-deriving the point-to-point
network construction, so it is a real trade-off, not a formality.
