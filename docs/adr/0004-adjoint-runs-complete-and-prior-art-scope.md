# GEOS-Chem adjoint runs already completed; mortality pipeline is established prior art, not the novel contribution

Status: accepted

GEOS-Chem adjoint sensitivity runs for this project are already done &mdash;
this removes the heaviest open infrastructure risk flagged in the fight-me
critique (see discussion below; recorded here rather than as a separate
objection since it resolves to a factual status, not a design choice).

The emissions-to-mortality pipeline (GEOS-Chem adjoint sensitivities to
NOx/PM2.5 &rarr; Turner et al. ozone and GEMM PM2.5 concentration-response
functions &rarr; health burden) is well-established prior art (e.g., Barrett,
Britter & Waitz, "Global Mortality Attributable to Aircraft Cruise
Emissions," Environ. Sci. Technol. 2010, DOI: 10.1021/es101325r) and is
being adopted here, not re-derived or claimed as novel.

The project's novel contribution is applying that established pipeline to
compare how hub-and-spoke vs. point-to-point network structures spread
emissions differently in space, for the same fixed demand &mdash; not the
mortality/exposure methodology itself. State this distinction explicitly in
the proposal and methods pages to avoid implying the health pipeline is a
project contribution.

Development/validation work may use existing proprietary data already in
hand (per `docs/adr/0002`), but the version of the pipeline whose results
are published on the project website must run on open data only, since the
site is public. This is a real constraint, not a formality: results from
the proprietary-data run and the open-data run may differ, and only the
open-data result is publishable. Plan for both runs explicitly rather than
assuming the proprietary run can be shown or substituted later.
