"""
totals.py — Aggregate network-level metrics from a list of RouteFlow objects.

⚠️  PLACEHOLDER MODELLING ASSUMPTIONS — DO NOT CITE AS RESULTS ⚠️
==================================================================
* ``seats_per_flight`` (default 150.0) is an illustrative constant.  Real
  fleets are heterogeneous and load factors vary by market; a single scalar
  here is a gross simplification.
* ``lto_share`` (default 0.1) is an illustrative placeholder for the fraction
  of fuel burned in the LTO (landing-and-take-off) cycle below ~3 000 ft.
  Actual values depend on stage length and engine type.
* All fuel coefficients (kg/nm) come from ``fuel.py`` and are explicitly
  marked as PLACEHOLDER values in that module.
* All emission indices (g/kg fuel) come from ``emissions.py`` and are
  explicitly marked as PLACEHOLDER values in that module.

NO number produced by this module may be quoted as an engineering or
scientific result without substituting real, validated data sources.

Public API
----------
network_totals(routes, seats_per_flight=150.0, lto_share=0.1) -> dict
    Return a dict with the following keys:

    passengers      : total passengers across all RouteFlow records (sum of
                      r.passengers for r in routes).
    passenger_legs  : sum over routes of r.passengers × (number of legs in r),
                      where the number of legs = len(r.route) - 1.
                      For point-to-point routes (1 leg each) this equals
                      passengers.  For hub-and-spoke routes with connecting
                      legs this is strictly greater.
    flights         : sum over *legs* of flights_leg, where
                        flights_leg = leg_passengers / seats_per_flight
                      This is a FLUID (non-integer) flight count; no ceiling
                      is applied.  A fractional flight represents the capacity
                      equivalent of the passenger load on that leg.
    aircraft_nm     : sum over legs of flights_leg × distance_nm(leg)  [nm]
    fuel_kg         : sum over legs of flights_leg × fuel.fuel_kg(d, cls)
                      where cls = fuel.stage_length_class(d).
    NOx_kg          : sum over legs of NOx emissions from
                      emissions.emissions_kg(fuel_leg, lto_share).
    CO_kg           : same for CO.
    HC_kg           : same for HC.

Notes on same-airport legs (origin == destination)
--------------------------------------------------
distance_nm(X, X) == 0.0 (the haversine formula is exact at zero).
A zero-distance leg contributes 0.0 to aircraft_nm and fuel_kg; it does
contribute to passenger_legs (leg counted once per route leg).  We do NOT
raise for zero-distance legs because they can arise naturally from hub-spoke
routing when an airport is its own hub, and the geometry is well-defined.
"""
from __future__ import annotations

from typing import Iterable, Dict, List

from networks import RouteFlow
import distance as _distance
import fuel as _fuel
import emissions as _emissions


def network_totals(
    routes: Iterable[RouteFlow],
    seats_per_flight: float = 150.0,
    lto_share: float = 0.1,
) -> Dict[str, float]:
    """Compute aggregate network metrics from a collection of RouteFlow objects.

    Parameters
    ----------
    routes : iterable of RouteFlow
        Route records to aggregate.  Each RouteFlow has an ordered `route`
        tuple of airport codes (IATA-like), and a `passengers` count.
    seats_per_flight : float, optional
        ⚠️ PLACEHOLDER.  Nominal seats per flight used to convert passenger
        loads to a fluid flight count.  Default 150.0.
    lto_share : float, optional
        ⚠️ PLACEHOLDER.  Fraction of leg fuel attributed to the LTO cycle,
        passed directly to emissions.emissions_kg().  Default 0.1.

    Returns
    -------
    dict[str, float]
        Keys: passengers, passenger_legs, flights, aircraft_nm, fuel_kg,
        NOx_kg, CO_kg, HC_kg.  All values are finite and >= 0 for valid
        inputs.
    """
    total_passengers: float = 0.0
    total_passenger_legs: float = 0.0
    total_flights: float = 0.0
    total_aircraft_nm: float = 0.0
    total_fuel_kg: float = 0.0
    total_NOx_kg: float = 0.0
    total_CO_kg: float = 0.0
    total_HC_kg: float = 0.0

    for r in routes:
        pax: float = r.passengers
        path: tuple = r.route
        n_legs: int = len(path) - 1  # number of legs in this route

        total_passengers += pax
        total_passenger_legs += pax * n_legs

        # Iterate over consecutive pairs in the path
        for i in range(n_legs):
            origin_leg = path[i]
            dest_leg = path[i + 1]

            d: float = _distance.distance_nm(origin_leg, dest_leg)
            flights_leg: float = pax / seats_per_flight

            cls: str = _fuel.stage_length_class(d)
            fuel_leg: float = flights_leg * _fuel.fuel_kg(d, cls)

            em: dict = _emissions.emissions_kg(fuel_leg, lto_share)

            total_flights += flights_leg
            total_aircraft_nm += flights_leg * d
            total_fuel_kg += fuel_leg
            total_NOx_kg += em["NOx"]
            total_CO_kg += em["CO"]
            total_HC_kg += em["HC"]

    return {
        "passengers": total_passengers,
        "passenger_legs": total_passenger_legs,
        "flights": total_flights,
        "aircraft_nm": total_aircraft_nm,
        "fuel_kg": total_fuel_kg,
        "NOx_kg": total_NOx_kg,
        "CO_kg": total_CO_kg,
        "HC_kg": total_HC_kg,
    }
