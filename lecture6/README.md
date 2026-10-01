Lecture 6 — Passenger conservation test

What this adds
- A tiny OD-demand fixture (lecture6/tests/fixtures/tiny_demand.csv)
- Minimal scaffolding functions in lecture6/src/networks.py
- One pytest: lecture6/tests/test_passenger_conservation.py
- A CI workflow to run the test on every push

Concept
- Demand is defined as passengers per origin–destination (OD) pair.
- Two network mappings:
  - Point-to-point: each OD maps to a single direct route [O, D].
  - Hub-and-spoke: each OD maps to a path through hubs (placeholder rule),
    preserving total passengers at the path-flow level.
- The test enforces passenger conservation: the sum of route-passenger flows
  equals the sum of OD passengers.

Tolerance rationale (from the test docstring)
- Total-sum check uses ±1 passenger (abs_tol=1.0) for robustness with floating-point and weighted counts.
- Per-OD re-aggregation uses a tight tolerance (abs=1e-9, rel=1e-12) to catch subtle redistribution bugs.

What this test does not check (by design)
- Correct per-OD routing semantics beyond split summation.
- Connectivity/capacity, aircraft assignments, or schedule feasibility.
- Passenger-legs (they legitimately exceed passenger counts with connections).

Run locally

- With uv (preferred):
  - uv run --directory lecture6 -p 3.11 pytest -q
- With plain pytest:
  - python -m pytest lecture6 -q
