"""
network_perf.py — Network fuel and NOx from leg flows, real aircraft mix,
and an injected per-flight performance function.

Public API
----------
segment_loads(routes) -> dict[tuple[str,str], float]
    Aggregate passenger flows onto each directional leg of every route.
    Returns {(origin, dest): total_passengers_on_leg, ...}.

network_perf(routes, mix, perf, distance_fn, *, allow_missing=False,
             renormalise=False) -> dict
    Compute per-segment and total fuel (kg) and NOx (kg) from leg-level
    passenger flows, an aircraft mix, and a per-flight performance function.

Design decisions
----------------
Flight count (fluid, no ceiling)
    flights_on_segment = passenger_load / (seats_per_departure * load_factor)
    This is a continuous fluid approximation: fractional flights represent
    capacity equivalents. No ceiling (math.ceil) is applied. Rationale: the
    same fluid model is used in totals.py; the approach is consistent across
    the comparison pipeline.

Unmapped aircraft handling
    If perf(bts_code, distance_nm) returns None, that aircraft code is
    unmapped. Its departure share is *excluded* from the fuel sum and is
    reported per segment as 'unmapped_share' (float, 0..1). The network-
    level 'unmapped_share' is the departure-weighted mean across all segments
    (weights = _total_departures from each segment's mix entry).
    The remaining mapped shares are NOT renormalised by default (renormalise
    defaults to False). This means the computed fuel is less than it would
    be with 100% coverage, which is the honest representation of the gap.
    To renormalise: pass renormalise=True; the mapped shares are scaled to
    sum to 1.0 before computing fuel.

Missing segments
    If a segment appears in routes but is absent from mix, the default
    behaviour is to raise ValueError naming all missing segments (a list is
    collected before raising so all are reported at once). If allow_missing=True,
    missing segments are instead recorded under result['missing_segments'] and
    their fuel contribution is left out (never a silent zero).

Degenerate mix data
    load_factor that is NaN or <= 0, or seats_per_departure <= 0, causes
    ValueError to be raised before any computation on that segment. These
    conditions produce infinite or meaningless flight counts.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from networks import RouteFlow

# Type aliases
_Seg = Tuple[str, str]
_Mix = Dict[_Seg, Dict]
_PerfFn = Callable[[str, float], Optional[Dict]]
_DistFn = Callable[[str, str], float]


def segment_loads(routes: Iterable[RouteFlow]) -> Dict[_Seg, float]:
    """Aggregate passenger flows onto each directional leg of every route.

    For each RouteFlow, iterates over consecutive pairs of nodes in the route
    and adds the route's passengers to the corresponding directed segment.

    Parameters
    ----------
    routes:
        Iterable of RouteFlow records.

    Returns
    -------
    dict keyed by (origin, dest) tuples (str, str).
    Each value is the total passenger load on that directed segment,
    summed across all routes that traverse it.

    Notes
    -----
    An empty route list returns an empty dict.
    A RouteFlow with a 1-node route has no legs and contributes nothing.
    """
    loads: Dict[_Seg, float] = {}
    for rf in routes:
        path = rf.route
        for i in range(len(path) - 1):
            seg = (path[i], path[i + 1])
            loads[seg] = loads.get(seg, 0.0) + rf.passengers
    return loads


def network_perf(
    routes: Iterable[RouteFlow],
    mix: _Mix,
    perf: _PerfFn,
    distance_fn: _DistFn,
    *,
    allow_missing: bool = False,
    renormalise: bool = False,
) -> Dict:
    """Compute per-segment and network-level fuel and NOx from leg flows.

    Parameters
    ----------
    routes:
        Iterable of RouteFlow records.
    mix:
        Aircraft mix dict, shape as returned by fleet.aircraft_mix():
          {(origin, dest): {
            "shares": {bts_code: share, ...},   # departure-weighted; sum=1.0
            "seats_per_departure": float,
            "load_factor": float,
            "_total_departures": float,
          }, ...}
    perf:
        Callable (bts_code: str, distance_nm: float) -> dict or None.
        Return dict must have keys 'fuel_kg' and 'nox_kg' (per flight).
        Return None if the aircraft is unmapped; the share will be excluded
        from fuel and reported as unmapped_share.
    distance_fn:
        Callable (airport_a: str, airport_b: str) -> float (nautical miles).
    allow_missing:
        If False (default), raise ValueError listing all segments that appear
        in routes but are absent from mix. If True, record them in
        result['missing_segments'] and continue without computing their fuel.
    renormalise:
        If False (default), unmapped aircraft shares are excluded from fuel
        computation but the remaining mapped shares are NOT rescaled.
        If True, the mapped shares are normalised to sum to 1.0 before
        computing fuel, distributing the unmapped capacity to mapped aircraft.

    Returns
    -------
    dict with keys:
        "segments": dict {(o,d): {
            "passengers": float,
            "flights": float,
            "fuel_kg": float,
            "nox_kg": float,
            "unmapped_share": float,  # 0..1; share of departures unmapped
          }, ...}
        "total_fuel_kg": float
        "total_nox_kg": float
        "total_flights": float
        "unmapped_share": float   # departure-weighted network average
        "missing_segments": list  # only present when allow_missing=True

    Raises
    ------
    ValueError
        If mix data is degenerate (load_factor NaN/<=0, seats_per_departure<=0).
        If allow_missing=False and any segment in routes is absent from mix.
    """
    # ── Build segment loads ────────────────────────────────────────────────
    loads = segment_loads(routes)

    # ── Check for missing segments ─────────────────────────────────────────
    missing_segs: List[_Seg] = [seg for seg in loads if seg not in mix]
    if missing_segs:
        if not allow_missing:
            raise ValueError(
                f"Segment(s) present in routes but absent from mix: "
                f"{sorted(missing_segs)}.  "
                f"Pass allow_missing=True to record them and continue."
            )

    # ── Per-segment computation ────────────────────────────────────────────
    seg_results: Dict[_Seg, Dict] = {}
    total_fuel = 0.0
    total_nox = 0.0
    total_flights = 0.0
    # For departure-weighted unmapped_share at network level
    weighted_unmapped = 0.0
    total_dep_weight = 0.0

    for seg, pax_load in loads.items():
        if seg in missing_segs:
            # allow_missing=True path: skip fuel computation for this seg
            continue

        seg_data = mix[seg]
        spd: float = float(seg_data["seats_per_departure"])
        lf: float = float(seg_data["load_factor"])
        shares: Dict[str, float] = seg_data["shares"]
        dep_total: float = float(seg_data.get("_total_departures", 1.0))

        # ── Validate mix data ──────────────────────────────────────────────
        if math.isnan(lf):
            raise ValueError(
                f"Segment {seg}: load_factor is NaN; cannot compute flights. "
                "Check T-100 data for zero-seat rows on this segment."
            )
        if lf <= 0:
            raise ValueError(
                f"Segment {seg}: load_factor = {lf} <= 0; cannot compute flights."
            )
        if spd <= 0:
            raise ValueError(
                f"Segment {seg}: seats_per_departure = {spd} <= 0; "
                "cannot compute flights."
            )

        # ── Compute flights (fluid, no ceiling) ───────────────────────────
        # flights = passenger_load / (seats_per_departure * load_factor)
        flights: float = pax_load / (spd * lf)

        # ── Per-aircraft contributions ────────────────────────────────────
        dist_nm: float = distance_fn(seg[0], seg[1])

        seg_fuel = 0.0
        seg_nox = 0.0
        seg_unmapped = 0.0

        # Collect mapped shares (for renormalisation if requested)
        mapped_shares: Dict[str, float] = {}
        unmapped_share_total: float = 0.0
        for bts_code, share in shares.items():
            p = perf(bts_code, dist_nm)
            if p is None:
                unmapped_share_total += share
            else:
                mapped_shares[bts_code] = share

        # Optionally renormalise mapped shares
        if renormalise and unmapped_share_total > 0.0:
            mapped_sum = sum(mapped_shares.values())
            if mapped_sum > 0.0:
                mapped_shares = {
                    code: s / mapped_sum
                    for code, s in mapped_shares.items()
                }

        # Compute fuel and NOx using (possibly renormalised) mapped shares
        for bts_code, share in mapped_shares.items():
            p = perf(bts_code, dist_nm)
            assert p is not None  # guaranteed by mapped_shares construction
            seg_fuel += share * p["fuel_kg"]
            seg_nox += share * p["nox_kg"]

        seg_fuel *= flights
        seg_nox *= flights
        seg_unmapped = unmapped_share_total

        seg_results[seg] = {
            "passengers": pax_load,
            "flights": flights,
            "fuel_kg": seg_fuel,
            "nox_kg": seg_nox,
            "unmapped_share": seg_unmapped,
        }

        total_fuel += seg_fuel
        total_nox += seg_nox
        total_flights += flights
        weighted_unmapped += seg_unmapped * dep_total
        total_dep_weight += dep_total

    # ── Network-level unmapped_share (departure-weighted) ─────────────────
    net_unmapped = (weighted_unmapped / total_dep_weight
                    if total_dep_weight > 0.0 else 0.0)

    result: Dict = {
        "segments": seg_results,
        "total_fuel_kg": total_fuel,
        "total_nox_kg": total_nox,
        "total_flights": total_flights,
        "unmapped_share": net_unmapped,
    }

    if allow_missing:
        result["missing_segments"] = missing_segs

    return result
