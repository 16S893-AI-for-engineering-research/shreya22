# Airport fixtures (committed, for offline tests of NW-19)

Small fixtures for `test_airports.py`.  All files are small (<200 KB each).

| File | Source | Selection |
|---|---|---|
| `ourairports_sample.csv` | Hand-crafted from OurAirports format | 8 airports (BOS, ORD, SFO, DEN, PHX, ATL, DTW, MSP) + 1 closed (ZZX); same column schema as the real OurAirports airports.csv |
| `t100_sample.csv` | Based on real BTS T-100 DISTANCE values | 11 rows: 10 passenger-configured (AIRCRAFT_CONFIG=1) segments covering the 8 airports, plus 1 row with a FAA-style code (ZZX) as ORIGIN that has no OurAirports match |

## Notes on t100_sample.csv

- `ZZX` appears as an ORIGIN: it should appear in the "unmatched" list for the script
- `DISTANCE` values for BOS-ORD, BOS-SFO etc. are taken from real T-100 2019 Q1 data
- The row with `ZZX` has a deliberately wrong DISTANCE (40 sm) that would produce
  a wildly off ratio — mimicking the known outliers in the real data (0.37x and 38x)
  but ZZX is simply unmatched so it won't appear in the differential comparison

## Differential test expectations (fixture-based)

Using `ourairports_sample.csv` coordinates and `t100_sample.csv` DISTANCE values,
for the 10 matched segments (AIRCRAFT_CONFIG==1, both O and D in OurAirports):

- Median ratio (haversine_nm / t100_nm): ~0.998
- All 10 ratios within 0.5% of 1.0 (these are well-behaved US domestic segments)
- No outliers above 1.01 or below 0.99 in this small fixture
