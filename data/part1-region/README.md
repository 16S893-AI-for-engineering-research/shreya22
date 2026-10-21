# Part 1 region run (BOS, DEN, DTW, MSP, ORD, SFO; 2019 Q1)

Outputs of `cli pipeline` (see `project/src/pipeline.py`), copied unchanged from two runs:

- `hub-ORD/`: hub ORD only.
- `hubs-ORD-DEN-nearest/`: hubs ORD and DEN, each airport assigned to its nearest hub.

Reproduce (from `project/src`, with the BTS cache in `data/bts-cache/`):

    ../.venv/bin/python -m cli pipeline --demand ../../data/bts-cache/demand_region_2019Q1.csv \
      --hubs ORD,DEN --t100 ../../data/bts-cache/t100_2019_0{1,2,3}.csv --out DIR --hub-assign nearest

Input hashes are in each `manifest.json`. These are outputs of a modelled prototype, not findings:
the project's comparison metric is population-weighted exposure (ADR 0003), which is not computed.
