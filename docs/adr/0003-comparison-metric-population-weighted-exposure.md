# Comparison metric: population-weighted exposure/health burden, not per-passenger-km burn

Status: accepted

The hub-and-spoke vs. point-to-point comparison is made on population-weighted
exposure and health burden (via the Turner et al. ozone and GEMM PM2.5
concentration-response functions, applied to GEOS-Chem adjoint sensitivities),
not on fuel burn or emissions normalized per passenger-kilometre.

Per-passenger-km burn was considered and rejected as the primary comparison
metric: it is a burn-rate proxy, and this project's own premise (`project.html`
Section 04) is that burn-rate proxies are poor stand-ins for air-quality
impact because impact depends on *where* NOx/nvPM is emitted relative to
population, not only on how much is emitted. Two networks could have
identical per-passenger-km emissions and very different population-weighted
exposure if one concentrates emissions over denser population.

NOx-driven secondary PM2.5 and ozone formation is also nonlinear in emissions
(unlike CO2, which scales linearly with fuel burn), so a linear per-km burn
metric would misstate the comparison even if exposure were held equal. The
adjoint sensitivities capture this nonlinearity locally around the emission
state being perturbed; the comparison should use the adjoint-derived exposure
change directly rather than re-linearizing it against a burn-rate metric.

Falsification criterion: the hypothesis is contradicted if the two network
structures, serving the same fixed demand, produce statistically
indistinguishable population-weighted exposure and health burden.
