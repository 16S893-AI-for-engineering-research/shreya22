"""
lecture6/src/legs.py
====================
Passenger-leg accounting for RouteFlow records.

A *leg* is a single flight segment between two consecutive nodes in a route.
A route with k nodes has (k - 1) legs.

Public API
----------
passenger_legs(routes) -> float
    Return sum over all RouteFlows of  passengers * (len(route) - 1).
    Raises ValueError for any RouteFlow whose route has fewer than 2 nodes,
    because a route with 0 or 1 nodes has no valid flight segment.

total_passengers(routes) -> float
    Return sum of passengers over all RouteFlows.

Key identities
--------------
- Point-to-point (all routes have exactly 2 nodes):
      passenger_legs == total_passengers  (exactly 1 leg per passenger)
- Hub-and-spoke (some routes have interior hub nodes):
      passenger_legs >= total_passengers
      passenger_legs - total_passengers == sum_routes(passengers * interior_hubs)
      where interior_hubs = len(route) - 2
- 'Strictly greater' only holds when at least one route has len(route) > 2.
  When O and D are both hubs the route has length 2 and legs == passengers.
"""
from __future__ import annotations

from typing import Iterable

from networks import RouteFlow


def passenger_legs(routes: Iterable[RouteFlow]) -> float:
    """Return total passenger-legs across all RouteFlows.

    Parameters
    ----------
    routes:
        Iterable of RouteFlow records.  Each route must have at least 2 nodes.

    Returns
    -------
    float
        Sum of  passengers * (len(route) - 1)  over all records.

    Raises
    ------
    ValueError
        If any RouteFlow has a route with fewer than 2 nodes (0 or 1 nodes),
        since there is no valid flight segment in such a route.
    """
    total = 0.0
    for rf in routes:
        n = len(rf.route)
        if n < 2:
            raise ValueError(
                f"Route has fewer than 2 nodes (len={n}) for record "
                f"origin={rf.origin!r} destination={rf.destination!r} "
                f"route={rf.route!r}; a valid route needs at least 2 nodes."
            )
        total += rf.passengers * (n - 1)
    return total


def total_passengers(routes: Iterable[RouteFlow]) -> float:
    """Return the total number of passengers across all RouteFlows.

    Parameters
    ----------
    routes:
        Iterable of RouteFlow records.

    Returns
    -------
    float
        Sum of passengers over all records.
    """
    return sum(rf.passengers for rf in routes)
