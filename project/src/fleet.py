"""
fleet.py — T-100 fleet adapter (NW-16).

Provides:
  load_t100(paths)       — load and filter T-100 monthly CSV files
  aircraft_mix(t100, region) — per-directional-segment departure shares,
                               seats_per_departure, load_factor
  BTS_TO_OPENAP          — mapping from BTS 3-digit aircraft type code to
                           OpenAP code (str) or None (UNMAPPED)
  MAPPING_NOTES          — dict of per-entry justification strings
  coverage_report(mix)   — fraction of departures exactly-mapped, proxy-mapped,
                           or unmapped

Design notes
------------
* Streams CSVs in chunks (chunksize 50_000) to avoid loading the full 1.6 GB
  DB1B file into memory; same pattern is safe here (T-100 is ~10 MB/month).
* AIRCRAFT_TYPE is kept as a *string* (zero-padded to 3 digits) throughout to
  match the string keys in BTS_TO_OPENAP.  Pandas reads it as int/float by
  default, so we coerce to str explicitly.
* Directional segments: (ORIGIN, DEST) is an ordered pair; (SFO, DEN) and
  (DEN, SFO) are separate segments.
* aircraft_mix stores "_total_departures" as an internal key in each segment
  dict so that coverage_report can weight segments correctly without a second
  pass over the raw data.

BTS_TO_OPENAP mapping rationale
--------------------------------
Sources used:
  - openap.prop.available_aircraft() run against openap 1.5.x (37 models)
  - BTS L_AIRCRAFT_TYPE lookup (code descriptions)
  - Reviewer-measured Q1 2019 region departure counts

Mapping categories (stored in MAPPING_NOTES):
  "exact"   — BTS aircraft is the same ICAO type as the OpenAP model
  "proxy"   — closest available OpenAP model; difference documented below
  None      — UNMAPPED; no suitable OpenAP model exists

Key proxy decisions:
  699 (A321Neoxlr) → 'a321':
      OpenAP has 'a321' (ceo) and 'a21n' (A321neo). The XLR is a range-
      extended variant of the neo with identical engines and ~same MTOW.
      The neo vs ceo fuel difference (~15%) is bounded and is actually
      *larger* than the XLR vs neo difference (structural only, ~same
      engines). Choosing 'a321' (ceo) is conservative for fuel; 'a21n'
      would be marginally better. We choose 'a321' because BTS describes
      this aircraft as "A321Neoxlr" without distinguishing, and the ceo
      is the more widely validated model in OpenAP's flight-phase database.
      Limitation: ~15% underestimate of fuel efficiency vs actual XLR.

  623 (Boeing 757-300) → 'b752':
      OpenAP has 'b752' (757-200) but no 'b753'. The 757-300 is 7.1 m
      longer, carries ~20% more passengers, same Rolls-Royce RB211 or
      PW2000 family engines.  Per-flight fuel burn is similar (heavier
      but more aerodynamically efficient at higher payload); per-seat
      burn will be slightly over-estimated. This is the only available
      proxy.

  631 (Canadair RJ-700) → 'crj9':
      OpenAP has 'crj9' (CRJ-900) but no CRJ-700 model. The CRJ-700 is
      smaller (70 vs 90 seats) with similar GE CF34 engines. crj9 will
      over-estimate per-flight fuel burn for a CRJ-700 mission.

  838 (Boeing 737 MAX 8) → 'b38m':
      'b38m' is the OpenAP code for the 737 MAX 8 — exact match.

  839 (Boeing 737 MAX 9) → 'b39m':
      'b39m' is the OpenAP code for the 737 MAX 9 — exact match.

  634 (Boeing 737-900) → 'b739':
      The 737-900 (non-ER) shares airframe and engines with the -900ER
      (888 → b739); the ER variant has extra fuel tanks, adding range but
      negligible fuel-burn difference at typical stage lengths.  Exact
      for practical purposes.

  722 (Airbus A320neo) → 'a20n':
      'a20n' is the OpenAP code for the A320neo — exact match.

  721 (Airbus A321-200n / A321neo) → 'a21n':
      'a21n' is the OpenAP code for the A321neo — exact match.

  677 (Embraer E170) → 'e170':
      'e170' is the OpenAP code — exact match.

  638 (Canadair CRJ-900) → 'crj9':
      'crj9' is the OpenAP code — exact match.

  675 (Embraer ERJ-145) → 'e145':
      'e145' is the OpenAP code — exact match.

  629 (Canadair CRJ-200) → 'crj9':
      OpenAP has 'crj9' (CRJ-900) but no CRJ-200/440 model. The CRJ-200
      is a 50-seat regional jet vs 90 seats for CRJ-900, with significantly
      lower fuel burn per flight.  crj9 will substantially over-estimate
      per-flight fuel burn; this proxy is flagged in MAPPING_NOTES and the
      departure share for 629 is <0.3%, limiting fleet-total impact.

  887 (B787-800 Dreamliner) → 'b788':
      'b788' is the OpenAP code — exact match.

  626 (Boeing 767-300/300ER) → 'b763':
      'b763' is the OpenAP code for the 767-300 — exact match.

  637 (Boeing 777-300/300ER/333ER) → 'b773':
      'b773' is the OpenAP code for the 777-300 — exact match.

  723 (Bombardier CS300 / A220-100 BD-500-1A10) → 'a20n':
      No A220/CS300 model in OpenAP. The A220-300 (CS300) is closest in
      size/technology to A320neo; 'a20n' is used as a proxy. Departure
      share <0.1%.

  515 (Bombardier Challenger 350) → None:
      Business jet; no OpenAP model and negligible departure share (<0.01%).
      UNMAPPED.

  479 (Pilatus PC-12) → None:
      Turboprop; no OpenAP model (all OpenAP jets). UNMAPPED.

NW-21 additions
---------------
NW-21 adds every remaining distinct national Q1-2019 AIRCRAFT_TYPE code as an
explicit decision, eliminating the 7.81% "absent" departure share.

New exact matches:
  687 (A330-300) → 'a333':
      BTS 687 = 'Airbus Industrie A330-300/333'; OpenAP 'a333' is the A330-300
      — exact match. Previously mapped to None in error (reviewer note: NW-21).
      1,453 national departures (0.07%).

  696 (A330-200) → 'a332':
      BTS 696 = 'Airbus Industrie A330-200'; OpenAP 'a332' is the A330-200
      — exact match. 3,812 national departures (0.19%).

  819 (747-400) → 'b744':
      BTS 819 = 'Boeing 747-400'; OpenAP 'b744' is the 747-400 — exact match.
      195 national departures (0.01%).

  821 (747-8) → 'b748':
      BTS 821 = 'Boeing B747-8'; OpenAP 'b748' is the 747-8 — exact match.
      137 national departures (0.01%).

  889 (B787-900) → 'b789':
      BTS 889 = 'B787-900 Dreamliner'; OpenAP 'b789' is the B787-9 — exact
      match. 750 national departures (0.04%).

  617 (Boeing 737-400) → 'b734':
      BTS 617 = 'Boeing 737-400'; OpenAP 'b734' is the Boeing 737-400 —
      exact match. 3 national departures (0.00%).

New proxy matches:
  676 (Embraer-140 / ERJ-140) → 'e145':
      OpenAP has 'e145' (ERJ-145) but no ERJ-140. The ERJ-140 is a shortened
      variant of the ERJ-145 platform (37 seats vs 50), same CF34-3 engines,
      same systems. Fuel burn per flight will be slightly overestimated.
      22,510 national departures (1.13%).

  674 (Embraer-135 / ERJ-135) → 'e145':
      OpenAP has 'e145' (ERJ-145) but no ERJ-135. The ERJ-135 is the smallest
      member of the ERJ-145 family (37 seats), same CF34-3 engines. Fuel burn
      per flight overestimated; per-seat estimate is conservative.
      5,933 national departures (0.30%).

  628 (Canadair RJ-100/RJ-100ER) → 'crj9':
      OpenAP has 'crj9' (CRJ-900) but no CRJ-100 model. The CRJ-100 is the
      original 50-seat Canadair Regional Jet using CF34-3A engines; the CRJ-900
      is 90-seat with CF34-8C5. crj9 significantly overestimates per-flight
      fuel burn; departure share is <0.01% nationally so impact is minimal.

  616 (Boeing 737-500) → 'b734':
      OpenAP has 'b734' (737-400 Classic) but no 737-500. Both are CFM56-3
      powered 737 Classics; the 737-500 is the shorter variant (108 seats vs
      146). b734 overestimates per-flight fuel burn for 737-500 missions.
      22 national departures (0.001%).

  620 (Boeing 737-100/200) → None:
      The 737-100/200 uses Pratt & Whitney JT8D turbojet/turbofan engines —
      a fundamentally different powerplant from the CFM56 series modeled in
      OpenAP. No suitable proxy exists. 151 national departures (0.008%).

  874 (Airbus A340-600) → 'a343':
      OpenAP has 'a343' (A340-300) but no A340-600. The A340-600 is the
      stretched variant (475 seats vs 295 for -300) with the same CFM56-5C
      engines and similar cruise performance. a343 overestimates fuel
      efficiency per seat; both are 4-engine widebodies.
      2 national departures (0.0001%).

  632 (Dornier 328 Jet) → None:
      The Dornier 328JET is a 32-seat regional jet with Pratt & Whitney Canada
      PW306B turbofan engines. No comparable model in OpenAP (e145 at 50 seats
      with CF34 engines would be a poor proxy). UNMAPPED.
      1,350 national departures (0.068%).

All turboprops, pistons, helicopters, and business jets mapped to None:
  Total departure share of None-mapped additions: ~6% national, ~0% region.
  Largest turboprop codes: 416 (Cessna 208 Caravan, 1.41%), 125 (Cessna C-402,
  0.94%), 482 (DHC-8-400 Dash-8, 0.89%), 415 (Grand Caravan, 0.60%), etc.
  Turboprops/pistons/helis combined: ~5.86% of national departures.
  Business jets combined: ~0.38% of national departures.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Tuple

# ── mapping table ─────────────────────────────────────────────────────────────
# Keys are BTS AIRCRAFT_TYPE codes as zero-padded 3-digit strings.
# Values are OpenAP aircraft codes (str) or None (UNMAPPED).
BTS_TO_OPENAP: Dict[str, Optional[str]] = {
    # ── exact matches ────────────────────────────────────────────────────────
    "888": "b739",   # Boeing 737-900ER → b739  (exact)
    "614": "b738",   # Boeing 737-800 → b738     (exact)
    "694": "a320",   # Airbus A320-100/200 → a320 (exact)
    "698": "a319",   # Airbus A319 → a319         (exact)
    "673": "e75l",   # Embraer ERJ-175 → e75l     (exact)
    "678": "e190",   # Embraer 190 → e190          (exact)
    "612": "b737",   # Boeing 737-700 → b737       (exact; -700LR/MAX7 share same code)
    "627": "b772",   # Boeing 777-200ER → b772     (exact)
    "622": "b752",   # Boeing 757-200 → b752       (exact)
    "838": "b38m",   # Boeing 737 MAX 8 → b38m     (exact)
    "839": "b39m",   # Boeing 737 MAX 9 → b39m     (exact)
    "722": "a20n",   # Airbus A320neo → a20n        (exact)
    "721": "a21n",   # Airbus A321neo → a21n        (exact)
    "677": "e170",   # Embraer E170 → e170           (exact)
    "638": "crj9",   # Canadair CRJ-900 → crj9       (exact)
    "675": "e145",   # Embraer ERJ-145 → e145         (exact)
    "887": "b788",   # B787-800 Dreamliner → b788     (exact)
    "626": "b763",   # Boeing 767-300/300ER → b763    (exact)
    "637": "b773",   # Boeing 777-300/300ER → b773    (exact)
    "625": "b763",   # Boeing 767-200/ER → b763 (proxy; -200 vs -300 same family)
    "624": "b763",   # Boeing 767-400/ER → b763 (proxy; -400 longer but same engines)
    "359": "a359",   # Airbus A350-900 → a359          (exact)
    "837": "b789",   # Boeing 787-10 → b789            (exact)
    # ── proxy mappings ───────────────────────────────────────────────────────
    "699": "a321",   # A321Neoxlr → a321 (proxy; see module docstring)
    "623": "b752",   # Boeing 757-300 → b752 (proxy; no b753 in OpenAP)
    "631": "crj9",   # Canadair CRJ-700 → crj9 (proxy; no crj7 in OpenAP)
    "629": "crj9",   # Canadair CRJ-200/440 → crj9 (proxy; no crj2 in OpenAP)
    "723": "a20n",   # A220-100 (CS300/BD-500) → a20n (proxy; no a220 in OpenAP)
    "634": "b739",   # Boeing 737-900 (non-ER) → b739 (proxy; codes differ; same engines)
    # ── UNMAPPED (no suitable OpenAP model) ──────────────────────────────────
    "608": None,     # Boeing 717-200: no OpenAP model
    "656": None,     # McDonnell Douglas MD-90: no OpenAP model
    "655": None,     # MD-81/82/83/88 (Super 80): no OpenAP model
    "740": None,     # McDonnell Douglas MD-11: no OpenAP model
    "515": None,     # Bombardier Challenger 350: business jet, no model
    "479": None,     # Pilatus PC-12: turboprop, no model
    "687": "a333",   # Airbus A330-300 → a333 (exact; NW-21 correction)
    # ── NW-21: additional exact matches ──────────────────────────────────────
    "696": "a332",   # Airbus A330-200 → a332 (exact)
    "819": "b744",   # Boeing 747-400 → b744 (exact)
    "821": "b748",   # Boeing B747-8 → b748 (exact)
    "889": "b789",   # B787-900 Dreamliner → b789 (exact)
    "617": "b734",   # Boeing 737-400 → b734 (exact)
    # ── NW-21: additional proxy mappings ─────────────────────────────────
    "676": "e145",   # Embraer ERJ-140 → e145 (proxy; same CF34 platform, shorter)
    "674": "e145",   # Embraer ERJ-135 → e145 (proxy; same CF34 platform, shorter)
    "628": "crj9",   # Canadair RJ-100/100ER → crj9 (proxy; 50 vs 90 seats)
    "616": "b734",   # Boeing 737-500 Classic → b734 (proxy; same CFM56-3 engines)
    "874": "a343",   # Airbus A340-600 → a343 (proxy; same CFM56-5C engines)
    # ── NW-21: UNMAPPED ─ turboprops, pistons, helicopters ──────────────────
    "010": None,     # Beech Bonanza 35/36: piston, no OpenAP model
    "026": None,     # Gipps Aero Ga8 Airvan: piston, no OpenAP model
    "034": None,     # Helio H-250/295/395: piston, no OpenAP model
    "035": None,     # Cessna C206/207/210 Stationair: piston, no OpenAP model
    "036": None,     # Cessna 172 Skyhawk: piston, no OpenAP model
    "079": None,     # Piper PA-32 Cherokee 6: piston, no OpenAP model
    "117": None,     # Beech Baron 55: piston twin, no OpenAP model
    "125": None,     # Cessna C-402: piston twin, no OpenAP model
    "131": None,     # Britten-Norman Islander: piston twin, no OpenAP model
    "194": None,     # Piper PA-31 Navajo: piston/turboprop, no OpenAP model
    "195": None,     # Piper PA-34/39: piston twin, no OpenAP model
    "340": None,     # Eurocopter AS350 AStar: helicopter, no OpenAP model
    "360": None,     # Robinson R44: helicopter, no OpenAP model
    "362": None,     # Bell 206 LT Long Ranger: helicopter, no OpenAP model
    "364": None,     # Bell 206 L4 Long Ranger: helicopter, no OpenAP model
    "366": None,     # Bell 407: helicopter, no OpenAP model
    "370": None,     # Eurocopter Bo-105: helicopter, no OpenAP model
    "393": None,     # AgustaWestland AW139: helicopter, no OpenAP model
    "405": None,     # Beech 1900: turboprop, no OpenAP model
    "406": None,     # Beech 200 Super King Air: turboprop, no OpenAP model
    "412": None,     # CASA C212 Aviocar: turboprop, no OpenAP model
    "415": None,     # Cessna Grand Caravan C208B: turboprop, no OpenAP model
    "416": None,     # Cessna 208 Caravan: turboprop, no OpenAP model
    "432": None,     # Saab 2000: turboprop, no OpenAP model
    "441": None,     # ATR-42: turboprop, no OpenAP model
    "442": None,     # ATR-72: turboprop, no OpenAP model
    "456": None,     # Saab 340B: turboprop, no OpenAP model
    "458": None,     # Beechcraft Super King Air 350: turboprop, no OpenAP model
    "461": None,     # Embraer EMB-120 Brasilia: turboprop, no OpenAP model
    "469": None,     # BAe Jetstream 31: turboprop, no OpenAP model
    "482": None,     # DHC-8-400 Dash 8: turboprop, no OpenAP model
    "485": None,     # DHC-6 Twin Otter: turboprop, no OpenAP model
    # ── NW-21: UNMAPPED ─ business jets ──────────────────────────────────
    "495": None,     # Hawker Beechcraft Hawker 4000: bizjet, no OpenAP model
    "575": None,     # Bombardier Learjet 75: bizjet, no OpenAP model
    "609": None,     # Bombardier Challenger 300: bizjet, no OpenAP model
    "620": None,     # Boeing 737-100/200: JT8D engine, no suitable OpenAP proxy
    "632": None,     # Dornier 328 Jet: 32-seat regional jet, no OpenAP model
    "636": None,     # Cessna Citation II/Bravo: bizjet, no OpenAP model
    "639": None,     # Cessna CJ1/CJ2/CJ3: bizjet, no OpenAP model
    "641": None,     # Gulfstream G-III/G-IV: bizjet, no OpenAP model
    "646": None,     # Cessna Citation X 650: bizjet, no OpenAP model
    "647": None,     # Cessna Citation CE750: bizjet, no OpenAP model
    "648": None,     # Gulfstream G200: bizjet, no OpenAP model
    "651": None,     # Gulfstream G150: bizjet, no OpenAP model
    "652": None,     # Canadair CL-600 Challenger: bizjet, no OpenAP model
    "658": None,     # Bombardier Global Express BD-700: bizjet, no OpenAP model
    "667": None,     # Gulfstream G-V/G-550: bizjet, no OpenAP model
    "668": None,     # Challenger 601: bizjet, no OpenAP model
    "669": None,     # Bombardier Challenger 604/605: bizjet, no OpenAP model
    "671": None,     # Gulfstream G450: bizjet, no OpenAP model
    "682": None,     # Dassault Falcon 2000EX: bizjet, no OpenAP model
    "684": None,     # Hawker 800XP: bizjet, no OpenAP model
    "685": None,     # Cessna Citation Excel/510 Mustang: bizjet, no OpenAP model
    "686": None,     # Learjet 45: bizjet, no OpenAP model
    "688": None,     # Bombardier Global 5000: bizjet, no OpenAP model
    "750": None,     # Gulfstream G650: bizjet (glf6 exists; no commercial pax ops)
    "770": None,     # Dassault Falcon 900/900EX: bizjet, no OpenAP model
    "774": None,     # Dassault Falcon 2000: bizjet, no OpenAP model
    "775": None,     # Dassault Falcon 7X: bizjet, no OpenAP model
    "788": None,     # Bombardier Global 6000/XRS: bizjet, no OpenAP model
}

# Codes that are "proxy" mappings (not exact); used by coverage_report.
_PROXY_CODES: frozenset = frozenset({
    "699",  # A321Neoxlr → a321
    "623",  # 757-300 → b752
    "631",  # CRJ-700 → crj9
    "629",  # CRJ-200 → crj9
    "723",  # A220-100 → a20n
    "625",  # 767-200 → b763
    "624",  # 767-400 → b763
    "634",  # 737-900 non-ER → b739
    # NW-21 additions
    "676",  # ERJ-140 → e145
    "674",  # ERJ-135 → e145
    "628",  # CRJ-100 → crj9
    "616",  # 737-500 → b734
    "874",  # A340-600 → a343
})

# ── per-entry justification notes (for documentation / NW-17 handoff) ────────
MAPPING_NOTES: Dict[str, str] = {
    "888": "exact: 737-900ER → b739",
    "614": "exact: 737-800 → b738",
    "694": "exact: A320 → a320",
    "698": "exact: A319 → a319",
    "673": "exact: ERJ-175 → e75l",
    "678": "exact: E190 → e190",
    "612": "exact: 737-700 → b737",
    "627": "exact: 777-200ER → b772",
    "622": "exact: 757-200 → b752",
    "838": "exact: 737 MAX 8 → b38m",
    "839": "exact: 737 MAX 9 → b39m",
    "722": "exact: A320neo → a20n",
    "721": "exact: A321neo → a21n",
    "677": "exact: E170 → e170",
    "638": "exact: CRJ-900 → crj9",
    "675": "exact: ERJ-145 → e145",
    "887": "exact: B787-800 → b788",
    "626": "exact: 767-300/300ER → b763",
    "637": "exact: 777-300/300ER → b773",
    "625": "proxy: 767-200/ER → b763 (767-300 proxy; same family, shorter fuselage)",
    "624": "proxy: 767-400/ER → b763 (767-300 proxy; -4 longer but same engines)",
    "359": "exact: A350-900 → a359",
    "837": "exact: B787-10 → b789",
    "699": (
        "proxy: A321Neoxlr → a321 (ceo). OpenAP has a321 and a21n. The XLR is "
        "a range-extended neo variant with same engines; ceo chosen over neo "
        "because BTS description does not distinguish ceo/neo/XLR, and the ceo "
        "model is more extensively validated in OpenAP. ~15% fuel-efficiency "
        "underestimate relative to actual XLR performance."
    ),
    "623": (
        "proxy: 757-300 → b752 (757-200). No b753 in OpenAP. Same engine family "
        "(RB211/PW2000). 757-300 is 7.1 m longer with ~20% more seats; "
        "per-seat fuel overestimated."
    ),
    "631": (
        "proxy: CRJ-700 → crj9 (CRJ-900). No CRJ-700 model in OpenAP. "
        "CRJ-700 has 70 seats vs 90 for CRJ-900; crj9 will overestimate "
        "per-flight fuel burn."
    ),
    "629": (
        "proxy: CRJ-200/440 → crj9 (CRJ-900). No CRJ-200 model in OpenAP. "
        "Large size difference (50 vs 90 seats); departure share <0.3%, "
        "limiting fleet-total impact."
    ),
    "723": (
        "proxy: A220-100 (CS300/BD-500) → a20n. No A220 model in OpenAP. "
        "A320neo is closest in generation/technology; departure share <0.05%."
    ),
    "634": (
        "proxy (functionally exact): Boeing 737-900 (non-ER) → b739. "
        "The non-ER shares the same CFM56-7B engines; ER adds auxiliary "
        "fuel tanks for range. Fuel burn difference negligible at typical "
        "stage lengths in this region."
    ),
    "608": "UNMAPPED: Boeing 717-200 has no OpenAP model (no MD-95/717 entry)",
    "656": "UNMAPPED: MD-90 has no OpenAP model",
    "655": "UNMAPPED: MD-80 series has no OpenAP model",
    "740": "UNMAPPED: MD-11 has no OpenAP model",
    "515": "UNMAPPED: Bombardier Challenger 350 (business jet) — no OpenAP model",
    "479": "UNMAPPED: Pilatus PC-12 (turboprop) — no OpenAP model",
    # NW-21: corrected entry (was None in error)
    "687": "exact: A330-300 → a333 (corrected from None in NW-21; a333 is exact OpenAP code)",
    # NW-21: new exact matches
    "696": "exact: A330-200 → a332",
    "819": "exact: Boeing 747-400 → b744",
    "821": "exact: Boeing 747-8 → b748",
    "889": "exact: B787-900 Dreamliner → b789",
    "617": "exact: Boeing 737-400 → b734",
    # NW-21: new proxy matches
    "676": (
        "proxy: Embraer ERJ-140 → e145 (ERJ-145). No ERJ-140 in OpenAP. "
        "Same Embraer ERJ-145 platform and CF34-3 engines; ERJ-140 has 37 seats "
        "vs 50 for ERJ-145. e145 overestimates per-flight fuel burn slightly."
    ),
    "674": (
        "proxy: Embraer ERJ-135 → e145 (ERJ-145). No ERJ-135 in OpenAP. "
        "Same Embraer ERJ-145 platform and CF34-3 engines; ERJ-135 has 37 seats "
        "vs 50 for ERJ-145. e145 overestimates per-flight fuel burn slightly."
    ),
    "628": (
        "proxy: Canadair RJ-100/RJ-100ER → crj9 (CRJ-900). No CRJ-100 in "
        "OpenAP. CRJ-100 uses CF34-3A1 (50 seats) vs CRJ-900 CF34-8C5 (90 seats). "
        "crj9 substantially overestimates per-flight fuel burn; "
        "departure share <0.01% nationally, limiting fleet-total impact."
    ),
    "616": (
        "proxy: Boeing 737-500 Classic → b734 (737-400 Classic). No 737-500 in "
        "OpenAP. Both are CFM56-3 powered 737 Classics; 737-500 is shorter "
        "(108 seats vs 146 for 737-400). b734 overestimates per-flight fuel burn. "
        "Departure share <0.001% nationally."
    ),
    "874": (
        "proxy: Airbus A340-600 → a343 (A340-300). No A340-600 in OpenAP. "
        "Both are 4-engine variants with CFM56-5C engines; A340-600 is longer "
        "(475 seats vs ~295 for A340-300) and heavier. a343 underestimates "
        "per-flight fuel burn. Only 2 national departures (0.0001%)."
    ),
    # NW-21: turboprops, pistons, helicopters
    "010": "UNMAPPED: Beech Bonanza 35/36 (piston single) — no OpenAP model",
    "026": "UNMAPPED: Gipps Aero Ga8 Airvan (piston) — no OpenAP model",
    "034": "UNMAPPED: Helio H-250/295/395 (piston) — no OpenAP model",
    "035": "UNMAPPED: Cessna C206/207/210 Stationair (piston) — no OpenAP model",
    "036": "UNMAPPED: Cessna 172 Skyhawk (piston) — no OpenAP model",
    "079": "UNMAPPED: Piper PA-32 Cherokee 6 (piston) — no OpenAP model",
    "117": "UNMAPPED: Beech Baron 55 series (piston twin) — no OpenAP model",
    "125": "UNMAPPED: Cessna C-402 (piston twin) — no OpenAP model",
    "131": "UNMAPPED: Britten-Norman BN2 Islander (piston twin) — no OpenAP model",
    "194": "UNMAPPED: Piper PA-31 Navajo/T-1020 (piston/turboprop) — no OpenAP model",
    "195": "UNMAPPED: Piper PA-34/39 (piston twin) — no OpenAP model",
    "340": "UNMAPPED: Eurocopter AS350 B2 AStar (helicopter) — no OpenAP model",
    "360": "UNMAPPED: Robinson R44 (helicopter) — no OpenAP model",
    "362": "UNMAPPED: Bell 206 LT Long Ranger (helicopter) — no OpenAP model",
    "364": "UNMAPPED: Bell 206 L4 Long Ranger (helicopter) — no OpenAP model",
    "366": "UNMAPPED: Bell 407 (helicopter) — no OpenAP model",
    "370": "UNMAPPED: Eurocopter Bo-105 (helicopter) — no OpenAP model",
    "393": "UNMAPPED: AgustaWestland AW139 (helicopter) — no OpenAP model",
    "405": "UNMAPPED: Beech 1900 A/B/C/D (turboprop: no OpenAP model)",
    "406": "UNMAPPED: Beech 200 Super King Air (turboprop: no OpenAP model)",
    "412": "UNMAPPED: CASA C212 Aviocar (turboprop: no OpenAP model)",
    "415": "UNMAPPED: Cessna Grand Caravan C208B (turboprop: no OpenAP model)",
    "416": "UNMAPPED: Cessna 208 Caravan (turboprop: no OpenAP model)",
    "432": "UNMAPPED: Saab 2000 (turboprop: no OpenAP model)",
    "441": "UNMAPPED: ATR-42 (turboprop: no OpenAP model)",
    "442": "UNMAPPED: ATR-72 (turboprop: no OpenAP model)",
    "456": "UNMAPPED: Saab 340B (turboprop: no OpenAP model)",
    "458": "UNMAPPED: Beechcraft Super King Air 350 (turboprop: no OpenAP model)",
    "461": "UNMAPPED: Embraer EMB-120 Brasilia (turboprop: no OpenAP model)",
    "469": "UNMAPPED: BAe Jetstream 31 (turboprop: no OpenAP model)",
    "482": "UNMAPPED: De Havilland DHC-8-400 Dash 8 (turboprop: no OpenAP model)",
    "485": "UNMAPPED: De Havilland DHC-6 Twin Otter (turboprop: no OpenAP model)",
    # NW-21: business jets
    "495": "UNMAPPED: Hawker Beechcraft Hawker 4000 (bizjet: no OpenAP model)",
    "575": "UNMAPPED: Bombardier Learjet 75 (bizjet: no OpenAP model)",
    "609": "UNMAPPED: Bombardier Challenger 300 BD-100-1A10 (bizjet: no OpenAP model)",
    "620": (
        "UNMAPPED: Boeing 737-100/200 uses Pratt & Whitney JT8D engines; "
        "no suitable OpenAP proxy (all OpenAP 737 models use CFM56)"
    ),
    "632": (
        "UNMAPPED: Dornier 328 Jet (32-seat regional jet with PW306B engines); "
        "no comparable OpenAP model (e145 at 50 seats with CF34 is too dissimilar)"
    ),
    "636": "UNMAPPED: Cessna Citation II/Bravo (bizjet: no OpenAP model)",
    "639": "UNMAPPED: Cessna CJ1/CJ2/CJ3 Citationjet (bizjet: no OpenAP model)",
    "641": "UNMAPPED: Gulfstream G-III/G-IV (bizjet: no OpenAP model)",
    "646": "UNMAPPED: Cessna Citation X 650/550B (bizjet: no OpenAP model)",
    "647": "UNMAPPED: Cessna Citation X CE750 (bizjet: no OpenAP model)",
    "648": "UNMAPPED: Gulfstream G200 (bizjet: no OpenAP model)",
    "651": "UNMAPPED: Gulfstream G150 (bizjet: no OpenAP model)",
    "652": "UNMAPPED: Canadair CL-600 Challenger (bizjet: no OpenAP model)",
    "658": "UNMAPPED: Bombardier BD-700 Global Express (bizjet: no OpenAP model)",
    "667": "UNMAPPED: Gulfstream G-V/G-550 (bizjet: no OpenAP model)",
    "668": "UNMAPPED: Canadair Challenger 601 (bizjet: no OpenAP model)",
    "669": "UNMAPPED: Bombardier Challenger 604/605 (bizjet: no OpenAP model)",
    "671": "UNMAPPED: Gulfstream G450 (bizjet: no OpenAP model)",
    "682": "UNMAPPED: Dassault Falcon 2000EX/2000LXS (bizjet: no OpenAP model)",
    "684": "UNMAPPED: Raytheon Beechcraft Hawker 800XP (bizjet: no OpenAP model)",
    "685": "UNMAPPED: Cessna 510 Mustang / Citation Excel (bizjet: no OpenAP model)",
    "686": "UNMAPPED: Learjet 45 (bizjet: no OpenAP model)",
    "688": "UNMAPPED: Bombardier Global 5000 (bizjet: no OpenAP model)",
    "750": (
        "UNMAPPED: Gulfstream G650 — glf6 exists in OpenAP but represents "
        "charter/executive operations not comparable to commercial passenger "
        "network analysis. No suitable mapping for hub-vs-spoke context."
    ),
    "770": "UNMAPPED: Dassault Falcon 900/900EX (bizjet: no OpenAP model)",
    "774": "UNMAPPED: Dassault Falcon 2000 (bizjet: no OpenAP model)",
    "775": "UNMAPPED: Dassault Falcon 7X (bizjet: no OpenAP model)",
    "788": "UNMAPPED: Bombardier Global 6000/Global XRS (bizjet: no OpenAP model)",
}


def load_t100(paths: Iterable[str]) -> "pd.DataFrame":
    """Load and filter T-100 monthly CSV files.

    Concatenates all files in ``paths``, keeps only rows where:
      * AIRCRAFT_CONFIG == 1  (passenger-configured aircraft)
      * DEPARTURES_PERFORMED > 0

    Returns a pandas DataFrame with at minimum these columns:
      DEPARTURES_PERFORMED, SEATS, PASSENGERS, ORIGIN, DEST,
      AIRCRAFT_TYPE (str, zero-padded to 3 digits), AIRCRAFT_CONFIG.

    Parameters
    ----------
    paths:
        Iterable of file paths (str or Path) to T-100 monthly CSVs.

    Notes
    -----
    Reads in chunks of 50 000 rows to avoid loading the full file into memory.
    """
    import pandas as pd

    USECOLS = [
        "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS",
        "ORIGIN", "DEST", "AIRCRAFT_TYPE", "AIRCRAFT_CONFIG",
    ]
    frames: list = []
    for path in paths:
        for chunk in pd.read_csv(
            str(path), usecols=USECOLS, chunksize=50_000, dtype=str,
        ):
            # coerce numeric columns
            for col in ("AIRCRAFT_CONFIG", "DEPARTURES_PERFORMED", "SEATS", "PASSENGERS"):
                chunk[col] = pd.to_numeric(chunk[col], errors="coerce")
            # apply filters
            mask = (chunk["AIRCRAFT_CONFIG"] == 1) & (chunk["DEPARTURES_PERFORMED"] > 0)
            chunk = chunk[mask].copy()
            if chunk.empty:
                continue
            # normalise AIRCRAFT_TYPE to zero-padded 3-digit string
            chunk["AIRCRAFT_TYPE"] = chunk["AIRCRAFT_TYPE"].apply(
                lambda x: str(int(float(x))).zfill(3)
            )
            frames.append(chunk)

    if not frames:
        return pd.DataFrame(columns=USECOLS)

    return pd.concat(frames, ignore_index=True)


def aircraft_mix(
    t100: "pd.DataFrame",
    region: Iterable[str],
) -> Dict[Tuple[str, str], Dict]:
    """Compute per-directional-segment aircraft departure shares.

    Parameters
    ----------
    t100:
        DataFrame produced by ``load_t100``; must have columns
        ORIGIN, DEST, AIRCRAFT_TYPE, DEPARTURES_PERFORMED, SEATS, PASSENGERS.
    region:
        Iterable of airport IATA codes defining the study region.  Only
        segments where both ORIGIN and DEST are in this set are returned.

    Returns
    -------
    dict keyed by (origin, dest) tuple (both strings), value is a dict:
      {
        "shares": {aircraft_type_str: departure_share_float, ...},
        "seats_per_departure": float,   # total SEATS / total DEPARTURES
        "load_factor": float,           # total PASSENGERS / total SEATS
                                        # (nan if total SEATS == 0)
        "_total_departures": float,     # internal key for coverage_report
      }

    Notes
    -----
    * Shares are departure-weighted: share(type) = deps(type) / total_deps.
    * Shares sum to exactly 1.0 (float arithmetic; error ≤ 1e-12).
    * AIRCRAFT_TYPE keys in ``shares`` are 3-digit zero-padded strings.
    """
    region_set = frozenset(str(a).upper() for a in region)
    df = t100[t100["ORIGIN"].isin(region_set) & t100["DEST"].isin(region_set)].copy()

    result: Dict[Tuple[str, str], Dict] = {}

    for (origin, dest), group in df.groupby(["ORIGIN", "DEST"]):
        # ── departure shares ────────────────────────────────────────────────
        type_deps = group.groupby("AIRCRAFT_TYPE")["DEPARTURES_PERFORMED"].sum()
        total_deps = float(type_deps.sum())
        shares = {
            str(ac_type): float(deps) / total_deps
            for ac_type, deps in type_deps.items()
        }

        # ── seats per departure ─────────────────────────────────────────────
        total_seats = float(group["SEATS"].sum())
        spd = total_seats / total_deps if total_deps > 0.0 else 0.0

        # ── load factor ─────────────────────────────────────────────────────
        total_pax = float(group["PASSENGERS"].sum())
        lf = (total_pax / total_seats) if total_seats > 0.0 else float("nan")

        result[(str(origin), str(dest))] = {
            "shares": shares,
            "seats_per_departure": spd,
            "load_factor": lf,
            "_total_departures": total_deps,
        }

    return result


def coverage_report(
    mix: Dict[Tuple[str, str], Dict],
) -> Dict:
    """Compute departure-share coverage breakdown across all segments.

    Aggregates across all segments in ``mix`` to produce fleet-wide fractions:
      * exactly_mapped  — departure share mapped via an exact BTS→OpenAP entry
      * proxy_mapped    — departure share mapped via a proxy entry
      * unmapped        — departure share with no OpenAP model (None in table)

    Returns
    -------
    dict with keys:
      "exactly_mapped"       : float  (fraction of departures, 0..1)
      "proxy_mapped"         : float
      "unmapped"             : float
      "exactly_mapped_codes" : list[str]  (BTS codes in this bucket)
      "proxy_mapped_codes"   : list[str]
      "unmapped_codes"       : list[str]

    Notes
    -----
    The three fractions sum to 1.0 (abs error ≤ 1e-12).
    Codes that do not appear in BTS_TO_OPENAP at all are treated as unmapped.
    Weighting uses the ``_total_departures`` internal key stored by
    aircraft_mix; if absent, each segment is treated as 1 departure total
    (equal-weight fallback — not recommended for real data).
    """
    fleet_total = 0.0
    exactly_deps = 0.0
    proxy_deps = 0.0
    unmapped_deps = 0.0
    exact_codes: set = set()
    proxy_codes: set = set()
    unmap_codes: set = set()

    for seg_data in mix.values():
        seg_total = seg_data.get("_total_departures", 1.0)
        fleet_total += seg_total
        for ac_code, share in seg_data["shares"].items():
            dep_count = share * seg_total
            openap = BTS_TO_OPENAP.get(ac_code, "ABSENT")
            if openap == "ABSENT" or openap is None:
                unmapped_deps += dep_count
                unmap_codes.add(ac_code)
            elif ac_code in _PROXY_CODES:
                proxy_deps += dep_count
                proxy_codes.add(ac_code)
            else:
                exactly_deps += dep_count
                exact_codes.add(ac_code)

    if fleet_total == 0.0:
        return {
            "exactly_mapped": 0.0,
            "proxy_mapped": 0.0,
            "unmapped": 0.0,
            "exactly_mapped_codes": [],
            "proxy_mapped_codes": [],
            "unmapped_codes": [],
        }

    return {
        "exactly_mapped": exactly_deps / fleet_total,
        "proxy_mapped": proxy_deps / fleet_total,
        "unmapped": unmapped_deps / fleet_total,
        "exactly_mapped_codes": sorted(exact_codes),
        "proxy_mapped_codes": sorted(proxy_codes),
        "unmapped_codes": sorted(unmap_codes),
    }
