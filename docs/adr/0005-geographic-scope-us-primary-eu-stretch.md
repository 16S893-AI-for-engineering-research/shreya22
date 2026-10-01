# Geographic scope: US primary, EU stretch; global demand data not available at required resolution

Status: accepted

Checked directly (not assumed) which sources provide route-level
origin&ndash;destination passenger demand, as opposed to country-level
totals or route existence without demand:

| Region | Source | Grain | Access |
|---|---|---|---|
| US | BTS T-100 / DB1B | True O&ndash;D passenger counts, per flight segment or ticketed itinerary | Free, public |
| EU | Eurostat `avia_par_*` | Route-level passenger counts between EU airports and partner airports, by reporting country, 1993&ndash;2024 | Free, public (confirmed live via Eurostat API) |
| Global | World Bank `IS.AIR.PSGR` | Total passengers carried, by country, by year | Free, public, but **country aggregates only, no O&ndash;D pairs** |
| Global | OpenFlights `routes.dat` | Which airline flies which airport pair (with codeshare flag, aircraft type) | Free, public, but **no passenger counts at all** &mdash; route existence only |

Not confirmed as free/public: OAG and Cirium (commercial, genuinely
global O&ndash;D passenger demand at BTS-like granularity &mdash; no free
tier found); ICAO's member-state-reported traffic statistics (public
statistics portal pages returned 404/redirect during this check; a bulk
free download was not located, not ruled out); China (CAAC), India (DGCA),
Brazil (ANAC) national portals exist and are reachable but it was not
verified whether any publish route-level O&ndash;D demand versus
airport-level totals only.

Decision: scope is **US primary**, with **EU as a stretch region** if time
and the EU data's resolution support it. Global demand data at the
resolution this project's network comparison requires (route-level
O&ndash;D passenger counts) is not freely available as a single dataset
and should not be assumed obtainable without a specific, verified source
for a specific region. This supersedes the "global if I have the data"
framing considered earlier; it does not rule out adding a verified region
later, but no further region should be added to scope without first
repeating this same free/public-data check for it.

`project-methods.html`'s existing "US domestic only" scope-limit language
was, in retrospect, the correct call and should be kept rather than loosened.
