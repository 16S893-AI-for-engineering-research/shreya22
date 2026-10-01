# Health pipeline: adjoint gives raw sensitivities, CRFs applied separately

Status: proposed

The completed GEOS-Chem adjoint run outputs raw sensitivities
(&part;concentration/&part;emission at each grid cell/altitude), not a
health-weighted cost function. Turner et al. (ozone) and GEMM (PM2.5)
concentration-response functions are applied as a separate step on top of
those sensitivities to convert &Delta;concentration into health burden.

This is believed true as of this record but not yet confirmed by directly
inspecting the adjoint run's cost-function definition. Confirm when the
health-burden step is actually implemented, to rule out the CRF being
double-counted if the adjoint objective turns out to already be
health-weighted. If confirmed otherwise, this record should be superseded.
