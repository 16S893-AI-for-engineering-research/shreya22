# Point-to-point network: feasibility cutoff by assigned aircraft, not fully connected

Status: accepted

The point-to-point network does not assign a nonstop to every
origin&ndash;destination pair with nonzero demand. A route is only served
nonstop if the aircraft assigned to it can actually fly that stage length
(range-feasible); routes that fail this cutoff remain connecting even in
the point-to-point scenario.

This directly follows from the Eugene&ndash;Bangor example on the proposal
page: a true 50-seat regional jet cannot cover that stage length nonstop,
so a "fully connected" point-to-point network would either be physically
impossible as drawn, or would silently require assigning a larger aircraft
than the route's demand would normally justify. The feasibility cutoff
avoids that by only converting a route to nonstop when the assigned
aircraft's range supports it; thin, long-haul city pairs stay connecting in
both scenarios, which also limits how much the two scenarios can differ for
those pairs by construction &mdash; a limitation to flag when stating
results, not just an implementation detail.

The exact cutoff rule (how aircraft is assigned pre-feasibility-check, and
what counts as "range-feasible" &mdash; nameplate range, range at the
specific payload/fuel load for that route's demand, or something else) is
not yet specified and should be recorded here or in a follow-up record once
decided.
