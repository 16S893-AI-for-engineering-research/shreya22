# Trip-comparison fixtures (NW-28)

Tiny **synthetic** inputs (not BTS extracts) for `test_trip_compare.py`, built
so every answer is known by hand. Q1-2019 totals are split over Jan/Feb/Mar.

* `t100_2019_q1.csv` - T-100 segment rows (BTS column layout). Segment
  departures: BOS-ORD 45 (30 x 737-800 `614`, 15 x A319 `698`), ORD-BOS 40,
  ORD-SFO 30, SFO-ORD 30, **BOS-DEN 12 (below the 13 threshold)**, DEN-BOS 14,
  DEN-SFO 20 (+100 deps in AIRCRAFT_CONFIG=2, which must be ignored), SFO-DEN 20,
  **BOS-SFO 12 (not observed)**, **SFO-BOS 13 (boundary, observed)**, ORD-DEN 24,
  DEN-ORD 25 (20 x `614`, 5 x PC-12 `479`, unmapped), BOS-MSP 15, MSP-SFO 15.
  Departures by origin: ORD 94, BOS 84, SFO 63, DEN 59, MSP 15.
* `db1b_market.csv` - DB1B Market rows (only the columns the reader uses).
  BOS->SFO one-stop raw passengers: CLE 12 (no T-100 segment), DEN 8 (BOS-DEN
  leg has 12 deps), ORD 5, MSP 2; plus 1 two-stop and 4 nonstop.
  SFO->BOS: ORD 5, DEN 5 (tie). DEN->ORD: BOS 6, SFO 4. ORD->DEN: none.
  `BOS->DEN` row with group `BOS:ORD` is an inconsistent AirportGroup.
* `aircraft_types.csv` - `L_AIRCRAFT_TYPE` lookup for the codes used.
