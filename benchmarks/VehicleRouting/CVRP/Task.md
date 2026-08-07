# Task: Capacitated Vehicle Routing Problem (CVRP)

## Audience and assumptions

This task assumes a general CS background but no (or little) prior exposure to
combinatorial optimization / vehicle routing.

## Background

In logistics, trucks depart from a warehouse to deliver goods to customers. The
**capacitated vehicle routing problem (CVRP)** is its standard mathematical
model:

- **Depot**: node 0; every vehicle starts and ends here.
- **Customers**: nodes 1..n, each with a demand `demand[c]`.
- **Vehicles**: identical, capacity limit `capacity`.
- **Route**: a sequence `depot → some customers → depot` whose total demand
  does not exceed `capacity`.

Goal: serve **all** customers (each exactly once) with any number of routes,
minimizing **total travel distance**.

CVRP is NP-hard: instances with a few dozen customers cannot be solved exactly
and require heuristics (nearest-neighbour, savings, 2-opt, large neighbourhood
search, metaheuristics, ...).

## Instances (data/instances/)

12 deterministically generated clustered instances (modelling city-like customer
distributions, coordinates 1..100, rounded Euclidean distances):

| Instance | Customers | Capacity | Instance | Customers | Capacity |
|----------|-----------|----------|----------|-----------|----------|
| VRP-19-2 | 19 | 270 | VRP-45-7 | 45 | 150 |
| VRP-21-3 | 21 | 165 | VRP-48-7 | 48 | 170 |
| VRP-22-4 | 22 | 130 | VRP-54-8 | 54 | 175 |
| VRP-32-5 | 32 | 160 | VRP-55-8 | 55 | 185 |
| VRP-37-6 | 37 | 145 | VRP-60-9 | 60 | 160 |
| VRP-45-6 | 45 | 190 | VRP-60-10 | 60 | 165 |

The file name is the instance name (e.g. `VRP-19-2.vrp`), in TSPLIB style
(`NODE_COORD_SECTION` / `DEMAND_SECTION` / `DEPOT_SECTION`, depot is node 1).
Instances are generated deterministically by `verification/generate_instances.py`
(seed 42; the per-instance `seed_key` is fixed to the original release
identifiers so the dataset stays byte-identical across releases).

## Input / output contract

### Candidate program `baseline/solver.py`

```python
# EVOLVE-BLOCK-START
def solve(instance):
    """Takes an instance dict, returns a list of routes list[list[int]].
    Each route is a visit sequence of customer ids (1..n, depot 0 excluded)."""
    ...
# EVOLVE-BLOCK-END
```

- `instance` fields:
  - `n`: number of customers (ids 1..n; depot is 0)
  - `capacity`: vehicle capacity
  - `demand`: `demand[0..n]`, `demand[0] == 0`
  - `distance`: `(n+1)×(n+1)` rounded-Euclidean distance matrix,
    `distance[0][c]` = depot-to-customer-c distance
- Output: `list[list[int]]`. Each route is a **customer-id sequence** (depot 0
  excluded), e.g. `[[3,1,5],[2,4]]` means two vehicles.
- Standalone run: `python baseline/solver.py <instance.vrp> <output.json>`
  (the fixed I/O part must not be modified).

### Validation rules

Each candidate output is checked:
1. Well-formed: `routes` is a list of lists of integers in 1..n;
2. **Full coverage**: the union of all routes is exactly {1..n}
   (no duplicates, none missing);
3. **Capacity**: `sum(demand[c]) <= capacity` per route.

Any violation → that instance is invalid (0 points) and the whole candidate
gets `valid=0`.

## Scoring

```
score_instance = min(100, 100 × reference_distance / candidate_distance)
combined_score = mean(score_instance)     # average over the 12 instances
valid          = all instances valid ? 1 : 0
```

- `reference_distance` comes from `data/reference.json`, precomputed by
  `verification/ref_solver.py`: a **deterministic** GRASP multi-start +
  2-opt + relocate/swap/2-opt* + LNS (greedy repair with tabu diversification)
  that keeps the best result over the fixed seed list `(123, 2024, 7)`, so
  regeneration is byte-identical on any machine. The reference is
  near-optimal (cross-checked against the archived best agent solver and an
  OR-Tools GLS solve).
- **100 = reference-quality solutions**; candidates shorter than the reference
  exceed 100 and are truncated to 100. Reaching 100 means the solution is
  close to the practical optimum of the instance.
- Optional score knob `CVRP_EVAL_SCORE_SCALE` (default 1.0):
  `score = min(100, scale × 100 × ref / cand)`. A scale < 1 tightens the 100
  bar (e.g. scale=2/3 requires the candidate to be no longer than 2/3 of the
  reference distance to score 100).
- Invalid / crashing / timing-out candidates: 0 on that instance and
  `valid=0` for the whole run.

## How to run

```bash
# Evaluate a candidate solver (inside the CVRP directory)
python verification/evaluator.py baseline/solver.py

# Evaluate on a subset of instances
python verification/evaluator.py baseline/solver.py --instances VRP-19-2 VRP-32-5

# Unified-task adapter check (repo root)
python -m frontier_eval task=unified task.benchmark=VehicleRouting/CVRP algorithm.iterations=0
```

Environment variables: `CVRP_EVAL_TIMEOUT_S` (per-instance subprocess timeout,
default 60), `CVRP_EVAL_INSTANCES` (instance subset), `CVRP_EVAL_MAX_INSTANCES`
(instance cap), `CVRP_EVAL_SCORE_SCALE` (score knob, default 1.0).

## Reference scores (measured on this machine, against the current reference.json)

Agent runs use the `deepseek-v4-flash` model; each framework's best measured
score is listed (reasoning-effort settings vary by framework).

| Solver | combined_score |
|--------|----------------|
| baseline (random-order cheapest insertion) | **55.59** |
| reference (deterministic GRASP + LNS, scoring baseline) | 100 (near-optimal) |
| agent (openevolve, 5 iterations, best) | 96.38 |
| agent (ShinkaEvolve, 5 generations, best) | 99.31 |
| agent (AB-MCTS, 5 candidates, best) | 98.70 |

## Optimisation hints (weak → strong)

1. **Random-order cheapest insertion** (baseline): insert customers one by
   one, in a random order, at their cheapest feasible position across all
   routes — a weak but standard randomized construction.
2. **Clarke-Wright savings**: merge routes by decreasing
   `d(0,i)+d(0,j)-d(i,j)` — a clear improvement.
3. **Intra-route 2-opt**: flip route segments to remove crossings — better.
4. **Cross-route local search**: relocate / swap / 2-opt*.
5. **Large neighbourhood search (LNS) / simulated annealing / genetic
   algorithms**: approach or beat the reference.

Every step is a verifiable score gain. **First guarantee validity (full
coverage + capacity), then optimize distance.**
