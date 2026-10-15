# BTS sample extracts (committed, for offline tests)

Small extracts of real US DOT / BTS data with the original column layout, so
tests run offline and in CI. The full files are large and are NOT committed;
they are cached in `data/bts-cache/` (git-ignored).

| File | Source | Selection |
|---|---|---|
| `db1b_market_sample.csv` | DB1B Market, 2019 Q1 (`Origin_and_Destination_Survey_DB1BMarket_2019_1.zip`, transtats.bts.gov/PREZIP) | 400 rows, seed 12345, from itineraries with origin and destination both in {BOS, DEN, DTW, MSP, ORD, SFO} |
| `t100_segment_sample.csv` | T-100 Domestic Segment (All Carriers), Jan-Mar 2019 (TranStats download form) | 300 passenger-configured rows (AIRCRAFT_CONFIG=1), seed 12345, same airport set |
| `L_AIRCRAFT_TYPE_sample.csv` | BTS lookup `L_AIRCRAFT_TYPE` | Only the codes that appear in the region's T-100 rows |

DB1B is a **10% ticket sample**: multiply `Passengers` by 10 for full volume.
Public domain (US federal government data).
