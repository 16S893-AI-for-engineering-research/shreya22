"""
Minimal scaffolding for Lecture 6 test-only purposes.

This module provides a tiny interface to:
- load_demand(path): read a CSV with columns origin,destination,passengers
- build_routes_point_to_point(demand)
- build_routes_hub_spoke(demand, hubs, assign=None)

Notes
- Route records include origin and destination fields so tests can re-aggregate
  split routes back to OD demand when needed.
- The hub-spoke builder is deliberately simple: if assign is None, every
  non-hub airport maps to the first hub in the hubs list. This preserves
  passenger conservation but is not an optimisation or a realistic assignment.
- Passenger counts are floats to reflect weighted survey data (e.g., DB1B 10%).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Dict, Tuple, Optional
import csv


@dataclass(frozen=True)
class ODDemand:
    origin: str
    destination: str
    passengers: float


@dataclass(frozen=True)
class RouteFlow:
    origin: str
    destination: str
    route: Tuple[str, ...]  # ordered nodes visited, inclusive of endpoints
    passengers: float


def load_demand(path: str) -> List[ODDemand]:
    """Load a small OD demand CSV: origin,destination,passengers -> ODDemand list."""
    out: List[ODDemand] = []
    with open(path, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            o = (row["origin"] or "").strip().upper()
            d = (row["destination"] or "").strip().upper()
            p = float(row["passengers"])  # may be fractional
            if not o or not d:
                raise ValueError("origin/destination must be non-empty IATA-like codes")
            out.append(ODDemand(o, d, p))
    return out


def build_routes_point_to_point(demand: Iterable[ODDemand]) -> List[RouteFlow]:
    """Assign each OD to a single direct route [O, D] with identical passengers.

    This is a trivial point-to-point mapping used for the conservation test.
    """
    routes: List[RouteFlow] = []
    for rec in demand:
        routes.append(RouteFlow(rec.origin, rec.destination, (rec.origin, rec.destination), rec.passengers))
    return routes


def build_routes_hub_spoke(
    demand: Iterable[ODDemand],
    hubs: Iterable[str],
    assign: Optional[Dict[str, str]] = None,
) -> List[RouteFlow]:
    """Map each OD to a path through assigned hubs.

    Rules (minimal placeholder):
    - Airports in `hubs` are their own hub.
    - If `assign` is provided, every non-hub airport must appear mapping -> one hub.
    - If `assign` is None, every non-hub maps to the first hub from `hubs`.
    - Routing:
        * If H(O) == H(D): [O, H(O), D] unless O or D already equals H(O), in which case
          the path compresses naturally (e.g., H -> D becomes [H, D]).
        * If H(O) != H(D): [O, H(O), H(D), D], with natural compression for endpoints
          that are hubs (e.g., H1 -> H2 -> D if O is H1).
    - No splitting: each OD yields exactly one route in this placeholder.

    This function preserves total passengers but makes no optimality claims.
    """
    hubset = tuple(h.upper() for h in hubs)
    if not hubset:
        raise ValueError("At least one hub must be provided")
    first_hub = hubset[0]

    def hub_of(node: str) -> str:
        if node in hubset:
            return node
        if assign and node in assign:
            h = assign[node].upper()
            if h not in hubset:
                raise ValueError(f"Assigned hub {h} for {node} is not in hubs list")
            return h
        return first_hub

    routes: List[RouteFlow] = []
    for rec in demand:
        ho = hub_of(rec.origin)
        hd = hub_of(rec.destination)
        path: List[str]
        if ho == hd:
            # collapse repeated nodes naturally
            path = [rec.origin]
            if rec.origin != ho:
                path.append(ho)
            if ho != rec.destination:
                path.append(rec.destination)
        else:
            path = [rec.origin]
            if rec.origin != ho:
                path.append(ho)
            path.append(hd) if hd != path[-1] else None
            if hd != rec.destination:
                path.append(rec.destination)
        routes.append(RouteFlow(rec.origin, rec.destination, tuple(path), rec.passengers))
    return routes
