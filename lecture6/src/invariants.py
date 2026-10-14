"""
Invariant validators for RouteFlow records.

check_endpoints(routes)
    Raise ValueError if any RouteFlow has route[0] != origin or
    route[-1] != destination.  An empty route also fails.

check_nonnegative(routes, tol=1e-9)
    Raise ValueError if any RouteFlow has passengers < -tol.
    Values in [-tol, 0) are treated as float noise and accepted.

Both functions return None on success so callers can use ``assert``
or ignore the return value.
"""
from __future__ import annotations

from typing import Iterable, Optional

from networks import RouteFlow


def check_endpoints(routes: Iterable[RouteFlow]) -> None:
    """Validate that every RouteFlow route starts at origin and ends at destination.

    Raises
    ------
    ValueError
        On the first offending record, with a message that includes the
        offending RouteFlow and a human-readable explanation.

    Returns
    -------
    None
        When all records are valid.
    """
    for rf in routes:
        if len(rf.route) == 0:
            raise ValueError(
                f"Endpoint mismatch: empty route for record "
                f"origin={rf.origin!r} destination={rf.destination!r} "
                f"passengers={rf.passengers}; route must have at least one node."
            )
        if rf.route[0] != rf.origin:
            raise ValueError(
                f"Endpoint mismatch: route starts at {rf.route[0]!r} "
                f"but origin is {rf.origin!r}. "
                f"Offending record: {rf}"
            )
        if rf.route[-1] != rf.destination:
            raise ValueError(
                f"Endpoint mismatch: route ends at {rf.route[-1]!r} "
                f"but destination is {rf.destination!r}. "
                f"Offending record: {rf}"
            )
    return None


def check_nonnegative(routes: Iterable[RouteFlow], tol: float = 1e-9) -> None:
    """Validate that every RouteFlow has passengers >= -tol.

    Values in the half-open interval [-tol, 0) are accepted as floating-point
    noise (e.g. -1e-12 arising from weighted DB1B aggregation).  Values below
    -tol are rejected as unphysical.

    Parameters
    ----------
    routes:
        Iterable of RouteFlow records to validate.
    tol:
        Non-negativity tolerance.  Default 1e-9 matches the per-OD tolerance
        used in the passenger conservation test.

    Raises
    ------
    ValueError
        On the first offending record, with a message that includes the
        negative passengers value and the full record.

    Returns
    -------
    None
        When all records are valid.
    """
    for rf in routes:
        if rf.passengers < -tol:
            raise ValueError(
                f"Negative passengers: {rf.passengers} is below tolerance "
                f"-{tol} for record {rf}."
            )
    return None
