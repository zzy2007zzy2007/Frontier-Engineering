# CVRP (Capacitated Vehicle Routing Problem) Benchmark

Standard CVRP: a fleet of identical vehicles serves all customers from a
single depot, each route within vehicle capacity, minimizing total distance.

## Structure

```
CVRP/
├── Task.md                  # Task description (rules, I/O contract, scoring)
├── baseline/
│   └── solver.py            # Candidate solver (random-order cheapest insertion) + EVOLVE-BLOCK
├── verification/
│   ├── evaluator.py         # Runner + validator + scorer (stdlib only)
│   ├── ref_solver.py        # Reference solver: deterministic GRASP multi-start + 2-opt + relocate/swap + 2-opt* + LNS
│   ├── generate_instances.py# Deterministic instance generator (seed 42, byte-stable)

│   └── requirements.txt
├── data/
│   ├── instances/           # 12 generated TSPLIB-style .vrp instances
│   └── reference.json       # Precomputed reference distance per instance
└── frontier_eval/           # Unified-task metadata
```

## Requirements

Python 3 standard library only. No third-party dependencies.

## How to run

```bash
# Evaluate a candidate solver (inside the CVRP directory)
python verification/evaluator.py baseline/solver.py

# Unified-task adapter check (repo root)
python -m frontier_eval task=unified task.benchmark=VehicleRouting/CVRP algorithm.iterations=0
```

### Docker

```bash
# Build the image (inside the CVRP directory)
docker build -t cvrp-benchmark -f verification/docker/Dockerfile .

# Evaluate the baseline
docker run --rm -it cvrp-benchmark

# Evaluate a candidate (mount it into the image: only build-time files are inside)
docker run --rm -it -v "$(pwd)/candidate.py:/app/candidate.py" cvrp-benchmark candidate.py
```

## Unified-task integration

- Benchmark id: `VehicleRouting/CVRP`
- The evaluator uses only the Python standard library, so no runtime overrides
  (`python_path`, conda env, or Docker image) are required; `eval_command.txt`
  uses plain `{python}`.
- Windows only: local validation must point `task.runtime.shell` at Git Bash
  (e.g. `task.runtime.shell=C:/Program Files/Git/bin/bash.exe`), because the
  default `bash` resolves to the WSL shim without `python` (returncode 127).
  Linux needs no override.

## Scoring

`score = min(100, 100 * reference_distance / candidate_distance)` averaged over
12 instances. The reference distances are precomputed by the deterministic
`verification/ref_solver.py` and are near-optimal (cross-checked against the
archived best agent solver and an OR-Tools GLS solve). Invalid solutions
(missing/duplicate customers, capacity violations, crashes, timeouts) score 0
and mark the run invalid. An optional `CVRP_EVAL_SCORE_SCALE` knob (default
1.0) tightens the 100-point bar: `score = min(100, scale * 100 * ref / cand)`.

## Reference scores (measured on this machine)

Agent runs use the `deepseek-v4-flash` model; each framework's best measured
score is listed (reasoning-effort settings vary by framework).

| Solver | combined_score |
|--------|----------------|
| baseline (random-order cheapest insertion) | 55.59 |
| reference (deterministic GRASP + LNS, scoring baseline) | 100 (near-optimal) |
| agent (openevolve, 5 iterations, best) | 96.38 |
| agent (ShinkaEvolve, 5 generations, best) | 99.31 |
| agent (AB-MCTS, 5 candidates, best) | 98.70 |
