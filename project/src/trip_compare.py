"""
trip_compare.py - per-trip comparison of point-to-point vs hub-and-spoke
routing for one directed origin-destination pair (NW-28).

Output contract: docs/trip-schema.md (published; the page agent reads it).

Pipeline (see precompute_trips.py for the driver)
-------------------------------------------------
* ``load_t100_data``   T-100 (passenger config) -> airport departures,
                       directional segment departures, per-segment aircraft mix.
* ``rank_airports``    top-N airports by departures (ties by code, must have
                       coordinates).
* ``aggregate_db1b``   ONE streaming pass over DB1B Market -> per directed OD:
                       total / nonstop / one-stop-by-intermediate passengers
                       (x db1b.SAMPLE_SCALE).
* ``build_trip``       one TRIP record for a directed pair.

Rules (author's decisions, not to be changed here)
--------------------------------------------------
* Nonstop observed  = directional T-100 segment with >= NONSTOP_MIN_DEPARTURES
  (13) departures in the quarter.  We additionally require a *usable* mix
  (seats > 0 and load factor in (0, inf)) because passengers-per-flight 0
  would make per-passenger fuel infinite; if a segment fails this it is
  treated as not observed and a message says so.
* ADR 0007 (strict): no observed nonstop => point_to_point is an exact copy
  of hub_and_spoke and point_to_point_is_connecting is true.
* Hub = intermediate airport with most (scaled) DB1B one-stop passengers whose
  two legs are both observed; ties by code.  Otherwise fall back to the
  top-10 airport with both legs observed and the shortest total great-circle
  distance; if none, the shortest top-10 hub regardless (its unobserved legs
  carry an empty aircraft mix, unmapped_share 1.0 and fuel 0.0 + a message).
* Fuel/NOx per flight = departure-weighted over MAPPED aircraft types.
  Unmapped types are excluded and NOT rescaled (same as network_perf).
* Performance is evaluated at the leg distance rounded to the nearest 10 nm
  (cached per (OpenAP code, distance)); profiles at the nearest 50 nm.
* range_ok = range_km >= distance_nm * 1.852.
"""
from __future__ import annotations

import copy
import csv
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

import db1b as _db1b
import distance as _distance
import fleet
from db1b import SAMPLE_SCALE

NONSTOP_MIN_DEPARTURES: int = 13
MAX_CANDIDATES: int = 5
NM_TO_KM: float = 1.852
PERF_ROUND_NM: int = 10
PROFILE_ROUND_NM: int = 50
MAX_PROFILE_POINTS: int = 120
UNMAPPED_MESSAGE_THRESHOLD: float = 0.01

Seg = Tuple[str, str]


# --------------------------------------------------------------------------- data holders
@dataclass
class T100Data:
    airport_departures: Dict[str, float]
    segment_departures: Dict[Seg, float]
    mix: Dict[Seg, Dict]


@dataclass
class Db1bAgg:
    total: Dict[Seg, float]
    nonstop: Dict[Seg, float]
    onestop: Dict[Seg, Dict[str, float]]
    anomalies: int = 0


@dataclass
class Env:
    """Everything build_trip needs.  perf / range_fn / profile_fn are injectable;
    ``None`` selects the real OpenAP-backed implementations."""

    t100: T100Data
    db1b: Db1bAgg
    hubs_fallback: List[str]
    type_names: Dict[str, str] = field(default_factory=dict)
    perf: Optional[Callable[[str, float], Dict]] = None
    range_fn: Optional[Callable[[str], Optional[float]]] = None
    profile_fn: Optional[Callable[[str, float], Dict]] = None
    coords: Optional[Dict[str, Tuple[float, float]]] = None
    profiles: Dict[str, Dict] = field(default_factory=dict)
    _perf_cache: Dict[Tuple[str, int], Dict] = field(default_factory=dict)
    _range_cache: Dict[str, Optional[float]] = field(default_factory=dict)
    _leg_cache: Dict[Seg, Tuple[Dict, List[str]]] = field(default_factory=dict)

    # -- geometry
    def dist(self, a: str, b: str) -> float:
        c = self.coords if self.coords is not None else _distance.AIRPORT_COORDS
        (la1, lo1), (la2, lo2) = c[a], c[b]
        return _distance.haversine_nm(la1, lo1, la2, lo2)

    def has_coords(self, a: str) -> bool:
        c = self.coords if self.coords is not None else _distance.AIRPORT_COORDS
        return a in c

    # -- cached model calls
    def fuel_nox(self, openap: str, dist_rounded_nm: int) -> Dict:
        key = (openap, dist_rounded_nm)
        if key not in self._perf_cache:
            fn = self.perf if self.perf is not None else _real_perf
            r = fn(openap, float(dist_rounded_nm))
            self._perf_cache[key] = {"fuel_kg": float(r["fuel_kg"]), "nox_kg": float(r["nox_kg"])}
        return self._perf_cache[key]

    def range_km(self, openap: str) -> Optional[float]:
        if openap not in self._range_cache:
            fn = self.range_fn if self.range_fn is not None else _real_range_km
            v = fn(openap)
            self._range_cache[openap] = None if v is None else float(v)
        return self._range_cache[openap]

    def profile_for(self, openap: str, dist_nm: float) -> str:
        d50 = max(PROFILE_ROUND_NM, int(round(dist_nm / PROFILE_ROUND_NM)) * PROFILE_ROUND_NM)
        key = f"{openap}-{d50}"
        if key not in self.profiles:
            fn = self.profile_fn if self.profile_fn is not None else _real_profile
            self.profiles[key] = decimate_profile(fn(openap, float(d50)))
        return key


# --------------------------------------------------------------------------- real (OpenAP) implementations
def _real_perf(openap: str, distance_nm: float) -> Dict:
    import openap_perf
    return openap_perf.flight_fuel_nox(openap, distance_nm)


def _real_range_km(openap: str) -> Optional[float]:
    import warnings
    from openap import prop
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        cruise = prop.aircraft(openap, use_synonym=True).get("cruise", {})
    r = cruise.get("range")
    return None if r is None else float(r)


def _real_profile(openap: str, distance_nm: float) -> Dict:
    import openap_perf
    df = openap_perf.trajectory_for_distance(openap, distance_nm)
    return {
        "t_s": [float(x) for x in df["t"].values],
        "alt_ft": [float(x) for x in df["altitude"].values],
        "dist_nm": [float(x) / 1852.0 for x in df["s"].values],
    }


# --------------------------------------------------------------------------- inputs
def load_t100_data(paths: Iterable) -> T100Data:
    """Load T-100 monthly CSVs (passenger configuration only)."""
    import pandas as pd

    df = fleet.load_t100(list(paths))
    if df.empty:
        return T100Data({}, {}, {})
    airport_deps = {str(k): float(v) for k, v in df.groupby("ORIGIN")["DEPARTURES_PERFORMED"].sum().items()}
    seg_series = df.groupby(["ORIGIN", "DEST"])["DEPARTURES_PERFORMED"].sum()
    seg_deps = {(str(o), str(d)): float(v) for (o, d), v in seg_series.items()}
    observed_idx = seg_series[seg_series >= NONSTOP_MIN_DEPARTURES].index
    keep = pd.MultiIndex.from_frame(df[["ORIGIN", "DEST"]]).isin(observed_idx)
    obs_df = df[keep]
    region = set(obs_df["ORIGIN"]).union(obs_df["DEST"])
    mix = fleet.aircraft_mix(obs_df, region)
    return T100Data(airport_deps, seg_deps, mix)


def rank_airports(
    airport_departures: Dict[str, float],
    coords: Dict[str, Tuple[float, float]],
    n: int,
) -> Tuple[List[Tuple[str, float]], List[str]]:
    """Top-n airports by departures (ties by code) that have coordinates.

    Returns (ranked [(code, departures)], excluded) where *excluded* lists
    airports without coordinates that out-ranked the cut-off."""
    order = sorted(airport_departures.items(), key=lambda kv: (-kv[1], kv[0]))
    ranked: List[Tuple[str, float]] = []
    excluded: List[str] = []
    for code, deps in order:
        if len(ranked) >= n:
            break
        if code in coords:
            ranked.append((code, float(deps)))
        else:
            excluded.append(code)
    return ranked, excluded


def load_type_names(path) -> Dict[str, str]:
    """BTS L_AIRCRAFT_TYPE lookup (Code, Description) -> {3-digit code: description}."""
    out: Dict[str, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = row["Code"].strip()
            if code.isdigit():
                code = code.zfill(3)
            out[code] = row["Description"].strip()
    return out


def load_airport_names(ourairports_csv) -> Dict[str, str]:
    """IATA -> name from an OurAirports airports.csv (non-closed rows with an iata_code)."""
    out: Dict[str, str] = {}
    with open(ourairports_csv, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            iata = (row.get("iata_code") or "").strip()
            if iata and row.get("type") != "closed":
                out.setdefault(iata, row["name"].strip())
    return out


def aggregate_db1b(path, airports: Set[str]) -> Db1bAgg:
    """One streaming pass over a DB1B Market CSV/zip.

    Keeps only rows whose Origin and Dest are both in *airports* (memory stays
    O(pairs)).  The AirportGroup (e.g. 'BOS:ORD:SFO') must start at Origin and
    end at Dest; others are counted in ``anomalies`` and ignored.  Passengers are
    multiplied by db1b.SAMPLE_SCALE at the end.
    """
    total: Dict[Seg, float] = {}
    nonstop: Dict[Seg, float] = {}
    onestop: Dict[Seg, Dict[str, float]] = {}
    anomalies = 0
    with _db1b._open_csv_source(path) as f:
        reader = csv.reader(f)
        header = next(reader)
        i_o, i_d = header.index("Origin"), header.index("Dest")
        i_g, i_p = header.index("AirportGroup"), header.index("Passengers")
        need = max(i_o, i_d, i_g, i_p)
        for row in reader:
            if len(row) <= need:
                continue
            o, d = row[i_o], row[i_d]
            if o == d or o not in airports or d not in airports:
                continue
            grp = row[i_g].split(":")
            if grp[0] != o or grp[-1] != d:
                anomalies += 1
                continue
            pax = float(row[i_p])
            key = (o, d)
            total[key] = total.get(key, 0.0) + pax
            stops = len(grp) - 2
            if stops == 0:
                nonstop[key] = nonstop.get(key, 0.0) + pax
            elif stops == 1:
                inner = onestop.setdefault(key, {})
                inner[grp[1]] = inner.get(grp[1], 0.0) + pax
    s = float(SAMPLE_SCALE)
    return Db1bAgg(
        total={k: v * s for k, v in total.items()},
        nonstop={k: v * s for k, v in nonstop.items()},
        onestop={k: {h: p * s for h, p in v.items()} for k, v in onestop.items()},
        anomalies=anomalies,
    )


# --------------------------------------------------------------------------- observation helpers
def is_observed(t100: T100Data, o: str, d: str) -> bool:
    """>= NONSTOP_MIN_DEPARTURES departures on the directional segment."""
    return t100.segment_departures.get((o, d), 0.0) >= NONSTOP_MIN_DEPARTURES


def _usable(env: Env, o: str, d: str) -> bool:
    """Observed AND a mix with seats > 0 and load factor in (0, inf) exists."""
    if not is_observed(env.t100, o, d):
        return False
    m = env.t100.mix.get((o, d))
    if m is None:
        return False
    spd, lf = m["seats_per_departure"], m["load_factor"]
    return (
        isinstance(spd, float) and math.isfinite(spd) and spd > 0
        and math.isfinite(lf) and lf > 0
        and env.has_coords(o) and env.has_coords(d)
    )


def leg_is_unobserved(leg: Dict) -> bool:
    """True for the placeholder leg built when a segment has no usable T-100 mix."""
    return not leg["aircraft_mix"]


# --------------------------------------------------------------------------- profiles
def decimate_profile(p: Dict) -> Dict:
    """Decimate to <= MAX_PROFILE_POINTS samples (first and last kept, evenly
    spaced by index).  t_s and alt_ft become ints, dist_nm 1 decimal."""
    n = len(p["t_s"])
    if n <= MAX_PROFILE_POINTS:
        idx = list(range(n))
    else:
        m = MAX_PROFILE_POINTS
        idx = sorted({int(round(i * (n - 1) / (m - 1))) for i in range(m)})
    return {
        "t_s": [int(round(p["t_s"][i])) for i in idx],
        "alt_ft": [int(round(p["alt_ft"][i])) for i in idx],
        "dist_nm": [round(float(p["dist_nm"][i]), 1) for i in idx],
    }


# --------------------------------------------------------------------------- legs
def _empty_leg(env: Env, o: str, d: str) -> Tuple[Dict, List[str]]:
    deps = env.t100.segment_departures.get((o, d), 0.0)
    leg = {
        "origin": o, "destination": d, "distance_nm": env.dist(o, d),
        "t100_departures": float(deps),
        "seats_per_departure": 0.0, "load_factor": 0.0, "passengers_per_flight": 0.0,
        "aircraft_mix": [], "unmapped_share": 1.0,
        "fuel_kg_per_flight": 0.0, "nox_kg_per_flight": 0.0,
        "fuel_kg_per_passenger": 0.0, "nox_kg_per_passenger": 0.0,
        "profile_key": None,
    }
    msg = (
        f"Leg {o}-{d} is not observed in T-100 ({deps:.0f} departures in 2019Q1, "
        f"need >= {NONSTOP_MIN_DEPARTURES}): no aircraft mix, fuel and NOx are set to 0.0 "
        f"(missing data, not a real zero)."
    )
    return leg, [msg]


def build_leg(env: Env, o: str, d: str) -> Tuple[Dict, List[str]]:
    """LEG record + messages for the directional segment o->d (cached per segment)."""
    key = (o, d)
    if key not in env._leg_cache:
        env._leg_cache[key] = _build_leg_uncached(env, o, d)
    leg, msgs = env._leg_cache[key]
    return copy.deepcopy(leg), list(msgs)


def _build_leg_uncached(env: Env, o: str, d: str) -> Tuple[Dict, List[str]]:
    if not _usable(env, o, d):
        return _empty_leg(env, o, d)
    seg = env.t100.mix[(o, d)]
    dist = env.dist(o, d)
    d10 = max(PERF_ROUND_NM, int(round(dist / PERF_ROUND_NM)) * PERF_ROUND_NM)
    spd, lf = float(seg["seats_per_departure"]), float(seg["load_factor"])
    ppf = spd * lf

    entries = []
    fuel = nox = unmapped = 0.0
    msgs: List[str] = []
    for code, share in sorted(seg["shares"].items(), key=lambda kv: (-kv[1], kv[0])):
        openap = fleet.BTS_TO_OPENAP.get(code)
        name = env.type_names.get(code, f"BTS aircraft type {code}")
        rng = rok = None
        if openap is None:
            unmapped += share
        else:
            r = env.fuel_nox(openap, d10)
            fuel += share * r["fuel_kg"]
            nox += share * r["nox_kg"]
            rng = env.range_km(openap)
            if rng is not None:
                rok = bool(rng >= dist * NM_TO_KM)
                if not rok:
                    msgs.append(
                        f"Range: {name} ({openap}, {share * 100:.1f}% of departures) flies "
                        f"{o}-{d} in the T-100 data, but the leg ({dist:.0f} nm = {dist * NM_TO_KM:.0f} km) is "
                        f"beyond OpenAP's nominal design range for this type ({rng:.0f} km), so its modelled "
                        f"fuel may be unreliable."
                    )
        entries.append({"bts_code": code, "name": name, "openap": openap, "share": share,
                        "range_km": rng, "range_ok": rok})
    if unmapped >= UNMAPPED_MESSAGE_THRESHOLD:
        msgs.append(
            f"{unmapped * 100:.1f}% of departures on {o}-{d} are aircraft types with no OpenAP model: "
            f"excluded from fuel and NOx, not rescaled."
        )
    top = next((e for e in entries if e["openap"]), None)
    leg = {
        "origin": o, "destination": d, "distance_nm": dist,
        "t100_departures": float(env.t100.segment_departures[(o, d)]),
        "seats_per_departure": spd, "load_factor": lf, "passengers_per_flight": ppf,
        "aircraft_mix": entries, "unmapped_share": unmapped,
        "fuel_kg_per_flight": fuel, "nox_kg_per_flight": nox,
        "fuel_kg_per_passenger": fuel / ppf, "nox_kg_per_passenger": nox / ppf,
        "profile_key": env.profile_for(top["openap"], dist) if top else None,
    }
    return leg, msgs


def build_route(env: Env, airports: Sequence[str]) -> Tuple[Dict, List[str]]:
    """ROUTE record for a chain of airports."""
    legs: List[Dict] = []
    msgs: List[str] = []
    for a, b in zip(airports, airports[1:]):
        leg, m = build_leg(env, a, b)
        legs.append(leg)
        msgs.extend(m)
    route = {
        "airports": list(airports),
        "total_nm": sum(l["distance_nm"] for l in legs),
        "legs": legs,
        "fuel_kg_per_passenger": sum(l["fuel_kg_per_passenger"] for l in legs),
        "nox_kg_per_passenger": sum(l["nox_kg_per_passenger"] for l in legs),
        "unmapped_share_max": max(l["unmapped_share"] for l in legs),
    }
    return route, msgs


# --------------------------------------------------------------------------- hub selection
def select_hub(env: Env, o: str, d: str) -> Tuple[Dict, List[str]]:
    """Choose the hub for o->d.  Returns (HUB record, messages)."""
    msgs: List[str] = []
    one = env.db1b.onestop.get((o, d), {})
    total_one = sum(one.values())
    ranked = sorted(one.items(), key=lambda kv: (-kv[1], kv[0]))

    qualified: List[Tuple[str, float]] = []
    skipped: List[Tuple[str, float]] = []
    for h, p in ranked:
        if h in (o, d):
            continue
        if _usable(env, o, h) and _usable(env, h, d):
            qualified.append((h, p))
        else:
            skipped.append((h, p))

    if qualified:
        best, best_p = qualified[0]
        heavier = [(h, p) for h, p in skipped if p > best_p][:MAX_CANDIDATES]
        if heavier:
            msgs.append(
                "DB1B one-stop connections with more passengers than the chosen hub were skipped because "
                f"a leg is not an observed T-100 segment (>= {NONSTOP_MIN_DEPARTURES} departures): "
                + ", ".join(f"{h} ({p:.0f} pax)" for h, p in heavier) + "."
            )
        hub = {
            "iata": best, "rule": "db1b_most_used",
            "share_of_connecting_passengers": best_p / total_one,
            "candidates": [{"iata": h, "share": p / total_one} for h, p in qualified[:MAX_CANDIDATES]],
        }
        return hub, msgs

    # ---- fallback among the top-10 hubs
    cands = [h for h in env.hubs_fallback if h not in (o, d) and env.has_coords(h)]
    if not cands:
        raise ValueError(f"no fallback hub available for {o}-{d}")

    def total_nm(h: str) -> float:
        return env.dist(o, h) + env.dist(h, d)

    good = sorted((h for h in cands if _usable(env, o, h) and _usable(env, h, d)), key=lambda h: (total_nm(h), h))
    why = (
        f"No DB1B one-stop connection for {o}-{d} has both legs observed in T-100"
        if ranked else f"No DB1B one-stop itineraries for {o}-{d}"
    )
    if good:
        best = good[0]
        msgs.append(f"{why}; fallback hub {best}: top-10 airport with both legs observed and the shortest "
                    f"total great-circle distance.")
    else:
        best = sorted(cands, key=lambda h: (total_nm(h), h))[0]
        msgs.append(f"{why}, and no top-10 hub has both legs observed; fallback uses the shortest top-10 hub "
                    f"{best} although a leg is not observed in T-100 (see leg messages).")
    hub = {"iata": best, "rule": "fallback_shortest_top10_hub",
           "share_of_connecting_passengers": None, "candidates": []}
    return hub, msgs


# --------------------------------------------------------------------------- trip
def _dedupe(msgs: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    out: List[str] = []
    for m in msgs:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


def build_trip(env: Env, o: str, d: str) -> Dict:
    """TRIP record for the directed pair o->d (see docs/trip-schema.md)."""
    msgs: List[str] = []
    seg_deps = env.t100.segment_departures.get((o, d), 0.0)
    nonstop_ok = _usable(env, o, d)
    if is_observed(env.t100, o, d) and not nonstop_ok:
        msgs.append(f"Nonstop segment {o}-{d} has {seg_deps:.0f} departures but no usable passenger/seat data; "
                    f"treated as not observed.")

    hub, hub_msgs = select_hub(env, o, d)
    hs_route, hs_msgs = build_route(env, [o, hub["iata"], d])

    if nonstop_ok:
        p2p, p_msgs = build_route(env, [o, d])
        connecting = False
    else:
        if not msgs:
            msgs.append(f"No observed nonstop for {o}-{d} ({seg_deps:.0f} T-100 departures in 2019Q1, "
                        f"need >= {NONSTOP_MIN_DEPARTURES}): point-to-point = connecting (ADR 0007).")
        else:
            msgs.append("No observed nonstop: point-to-point = connecting (ADR 0007).")
        p2p, p_msgs = copy.deepcopy(hs_route), []
        connecting = True

    total = env.db1b.total.get((o, d), 0.0)
    nonstop_pax = env.db1b.nonstop.get((o, d), 0.0)
    return {
        "origin": o, "destination": d,
        "great_circle_nm": env.dist(o, d),
        "db1b_passengers": float(total),
        "db1b_nonstop_share": (nonstop_pax / total) if total > 0 else None,
        "nonstop_observed": bool(nonstop_ok),
        "point_to_point": p2p,
        "point_to_point_is_connecting": connecting,
        "hub_and_spoke": hs_route,
        "hub": hub,
        "messages": _dedupe(msgs + hub_msgs + p_msgs + hs_msgs),
    }


def build_all_pairs(env: Env, codes: Sequence[str], only: Optional[Iterable[str]] = None) -> Dict[str, Dict]:
    """TRIP for every ordered pair of distinct *codes* (or just the keys in *only*)."""
    wanted = None if only is None else set(only)
    out: Dict[str, Dict] = {}
    for o in codes:
        for d in codes:
            if o == d:
                continue
            key = f"{o}-{d}"
            if wanted is not None and key not in wanted:
                continue
            out[key] = build_trip(env, o, d)
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- output size reduction
MIN_MIX_SHARE: float = 0.01


def collapse_minor_types(leg: Dict, min_share: float = MIN_MIX_SHARE) -> Dict:
    """Return a copy of *leg* in which aircraft types with share < *min_share*
    are merged into one trailing ``other`` entry (openap/range_km/range_ok None,
    share = their sum).  Only ``aircraft_mix`` changes: fuel, NOx, unmapped_share
    and profile_key were computed from the full mix and are left untouched."""
    out = copy.deepcopy(leg)
    keep = [a for a in out["aircraft_mix"] if a["share"] >= min_share]
    minor = [a for a in out["aircraft_mix"] if a["share"] < min_share]
    if minor:
        keep.append({
            "bts_code": "other", "name": f"Other types (<{min_share * 100:g}% each)", "openap": None,
            "share": sum(a["share"] for a in minor), "range_km": None, "range_ok": None,
        })
    out["aircraft_mix"] = keep
    return out


def collapse_trip(trip: Dict, min_share: float = MIN_MIX_SHARE) -> Dict:
    """collapse_minor_types applied to every leg of both routes of a TRIP (copy)."""
    out = copy.deepcopy(trip)
    for r in ("point_to_point", "hub_and_spoke"):
        out[r]["legs"] = [collapse_minor_types(l, min_share) for l in out[r]["legs"]]
    return out


# --------------------------------------------------------------------------- output rounding
_SHARE_KEYS = frozenset({"share", "load_factor", "unmapped_share", "unmapped_share_max",
                         "share_of_connecting_passengers", "db1b_nonstop_share"})


def round_floats(obj, key: str = ""):
    """Apply the output rounding policy recursively (non-finite floats -> None):

    * ``*_per_passenger``                      4 decimals (kg; NOx per pax is ~0.1-2 kg)
    * shares / load factors                    5 decimals
    * everything else (kg per flight, nm, km,
      passengers, departures, seats)           2 decimals

    (Coarser than the first draft - 3/5/6 - because the committed file was 12.8 MB.)
    """
    if isinstance(obj, bool) or obj is None or isinstance(obj, (str, int)):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            return None
        if key.endswith("_per_passenger"):
            return round(obj, 4)
        if key in _SHARE_KEYS:
            return round(obj, 5)
        return round(obj, 2)
    if isinstance(obj, dict):
        return {k: round_floats(v, k) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [round_floats(v, key) for v in obj]
    raise TypeError(f"unsupported type {type(obj)!r}")
