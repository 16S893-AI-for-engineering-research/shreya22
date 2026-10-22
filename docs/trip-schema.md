# Trip comparison data: published schema (NW-28 writes, NW-29 reads)

Produced by `python -m precompute_trips --out-dir ../../data` (run from
`project/src`; needs the git-ignored `data/bts-cache/`). Code:
`project/src/trip_compare.py`, `project/src/precompute_trips.py`. A 3-pair
fixture with the identical shape is `project/tests/fixtures/trips_fixture.json`.

## Contract (verbatim from the task; field names must not change)

SHARED OUTPUT CONTRACT (NW-28 writes it, NW-29 reads it). Do not change field names. All numbers are floats unless stated; units are in the field name.
File 1: data/trips_top50.json  (repo-root data/, committed; compact JSON, sorted keys, no NaN/Infinity — use null)
```
{
  "schema_version": 1,
  "source": {"quarter": "2019Q1", "db1b_sha256": str, "t100_sha256": [str,...], "airports_sha256": str,
             "openap_version": str, "nonstop_min_departures": 13, "notes": [str,...]},
  "airports": [{"iata": "ATL", "name": str, "departures": float}, ...],   # top 50 by T-100 passenger departures, sorted by departures desc
  "hubs_fallback": ["ATL", ...],                                            # top 10 of the above
  "pairs": { "BOS-SFO": TRIP, ... }                                         # every ordered pair of distinct top-50 airports (2450 keys)
}
TRIP = {
  "origin": "BOS", "destination": "SFO", "great_circle_nm": float,
  "db1b_passengers": float,                    # x10-scaled DB1B passengers for this directed OD (0 if none)
  "db1b_nonstop_share": float|null,            # share of DB1B passengers whose AirportGroup has no intermediate stop
  "nonstop_observed": bool,                    # T-100 directional segment with >= 13 departures in the quarter
  "point_to_point": ROUTE,                     # if nonstop_observed: the nonstop; else IDENTICAL to hub_and_spoke (ADR 0007, strict)
  "point_to_point_is_connecting": bool,        # true when no observed nonstop
  "hub_and_spoke": ROUTE,                      # always via one hub
  "hub": {"iata": str, "rule": "db1b_most_used" | "fallback_shortest_top10_hub",
          "share_of_connecting_passengers": float|null, "candidates": [{"iata": str, "share": float}, ... up to 5]},
  "messages": [str, ...]                       # human-readable caveats for this pair, e.g. no nonstop, fallback hub, unmapped share
}
ROUTE = {
  "airports": ["BOS","ORD","SFO"], "total_nm": float,
  "legs": [LEG, ...],
  "fuel_kg_per_passenger": float,              # sum over legs of leg fuel_kg_per_passenger
  "nox_kg_per_passenger": float,
  "unmapped_share_max": float                  # max leg unmapped_share
}
LEG = {
  "origin": str, "destination": str, "distance_nm": float, "t100_departures": float,
  "seats_per_departure": float, "load_factor": float, "passengers_per_flight": float,   # = seats_per_departure * load_factor
  "aircraft_mix": [{"bts_code": str, "name": str, "openap": str|null, "share": float, "range_km": float|null, "range_ok": bool|null}, ...],  # sorted by share desc, all types
  "unmapped_share": float,                     # departure share with openap null (excluded from fuel, NOT rescaled)
  "fuel_kg_per_flight": float, "nox_kg_per_flight": float,     # departure-weighted over mapped types, unmapped excluded
  "fuel_kg_per_passenger": float, "nox_kg_per_passenger": float,   # per_flight / passengers_per_flight
  "profile_key": str|null                      # key into trip_profiles.json: "<openap>-<distance rounded to 50 nm>" of the top mapped type
}
```
File 2: data/trip_profiles.json  `{"schema_version":1, "profiles": {"a320-2350": {"t_s":[...], "alt_ft":[...], "dist_nm":[...]}, ...}}`
  - Decimate to <= 120 points per profile, ints for t_s and alt_ft, dist_nm 1 decimal. Only profiles referenced by some leg.

## How the contract was realised (clarifications and deviations; read these)

**Deviation 1 - `aircraft_mix` is not "all types" in the committed file.** The
full-precision file was 12.8 MB. After coarser rounding it was still too big, so
types with a departure share < 0.01 (1%) on a leg are merged into **one trailing
entry** `{"bts_code": "other", "name": "Other types (<1% each)", "openap": null,
"share": <sum>, "range_km": null, "range_ok": null}`. Everything numeric on the
leg (`unmapped_share`, fuel, NOx, `profile_key`) was computed from the *full* mix
before merging, so only the list is shortened. `other` can contain mapped types:
do not read its `openap: null` as "unmapped"; `unmapped_share` is the only
authority. Merged types sort after the others (the remaining entries are sorted
by share desc). `--min-mix-share 0` writes the unmerged file.

**Rounding** (so output is stable and small): every `*_per_passenger` 4 decimals;
shares, `load_factor`, `unmapped_share*`, `db1b_nonstop_share` 5 decimals; all
other floats (kg per flight, nm, km, passengers, departures, seats) 2 decimals.
A route's `fuel_kg_per_passenger` is the sum of the *unrounded* legs, then
rounded, so it can differ from the sum of the printed legs by < 5e-5 per leg.
Floats with integer values are written like `5000.0`.

**Observed / usable.** `nonstop_observed` requires >= 13 departures in
AIRCRAFT_CONFIG=1 T-100 rows (Jan-Mar 2019) *and* a usable mix (seats > 0 and
load factor > 0; otherwise passengers-per-flight would be 0 and per-passenger
fuel infinite). A segment failing the second test counts as not observed and a
message says so.

**Hub.** One-stop = DB1B `AirportGroup` with exactly one intermediate airport
(`BOS:ORD:SFO`); the group must start at Origin and end at Dest (others are
ignored and counted). `share_of_connecting_passengers` = that hub's scaled
passengers / ALL one-stop passengers for the directed OD, including connections
excluded for an unobserved leg, so `candidates[].share` does not sum to 1.
`candidates` lists only qualified hubs (both legs observed), top 5, ties by code.
Fallback rule 'fallback_shortest_top10_hub' has `share_of_connecting_passengers`
null and `candidates` []. If even the fallback hubs have an unobserved leg, that
leg has `aircraft_mix: []`, `unmapped_share: 1.0`, `t100_departures` as observed
(possibly < 13), all fuel/NOx fields 0.0 and `profile_key` null, plus a message.
**Consumers must treat `aircraft_mix == []` as "missing data", not as zero fuel.**

**Fuel/NOx.** Per flight from `openap_perf.flight_fuel_nox(openap, d)`,
departure-weighted over mapped types, unmapped excluded and **not** rescaled.
`d` = leg great-circle nm rounded to the nearest 10 nm (min 10); results cached
per (OpenAP code, d). `range_ok = range_km >= distance_nm * 1.852` with
`range_km = openap.prop.aircraft(code)['cruise']['range']`; `null` when OpenAP
has no range for the type (currently CRJ-900 `crj9`). A type with `range_ok:
false` still contributes to the mix and the fuel (it appears because carriers do
fly it on the segment; the flag is a model-realism warning (the leg exceeds OpenAP's nominal design range; the type is observed flying it), also written as a
`Range:` message).

**Profiles.** From `openap_perf.trajectory_for_distance(code, d50)`, `d50` = leg
distance rounded to the nearest 50 nm (min 50), top *mapped* type by share (ties
by BTS code). `dist_nm` is distance flown since brake release. Decimated by
even index spacing, first and last samples kept.

**Determinism.** `json.dumps(sort_keys=True, separators=(",", ":"),
allow_nan=False)`; no timestamps, no absolute paths; input hashes are in `source`.

## File 3: data/trip_airports.json (NW-31, ground-track maps)

`{"schema_version":1, "airports": {"BOS": {"lat": 42.3643, "lon": -71.0052}, ...}}`: exactly
the airports listed in `trips_top50.json` plus every airport on either route of any pair
(hubs included; 64 today). Coordinates are from `project/data/airports.csv`, rounded to 4
decimals. Regenerate with `python -m build_trip_airports --out ../../data/trip_airports.json`
(from `project/src`; needs no cache and no network). Geometry for the maps lives in
`js/trip-tool-geo.js`.
