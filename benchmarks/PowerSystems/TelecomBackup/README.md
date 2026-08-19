# TelecomBackup: Power-Backup Scheduling for Telecom Sites (Frontier-Eng Benchmark)

An **original** Frontier-Engineering benchmark: given a region of telecom sites powered by
batteries, a solver must produce a **time-sequenced on/off schedule for every power supply** that
maximizes the region's total backup time while keeping good LTE coverage (RSRP > -105 dBm) above
80% at every moment. The power-consumption parameters live in each instance and are calibrated so
that stagger/rotation scheduling has clear, reproducible headroom over the naive always-on strategy
(see "Scoring").

The full game rules and evaluation semantics are in [Task.md](./Task.md) (Chinese).

## Layout

```
benchmarks/PowerSystems/TelecomBackup/
├── baseline/solver.py          # Candidate solver (EVOLVE-BLOCK region is the only editable part)
├── verification/
│   ├── generator.py            # Fixed-seed instance generator
│   ├── simulator.py            # Scoring simulator (coverage/power/battery simulation)
│   ├── evaluate.py             # Evaluation entry (subprocess + time budget + scoring)
│   ├── validator.py            # Integrity checks (static + env stripping + determinism)
│   ├── ref_solver.py           # Reference heuristic (rest-rotation) — documented "good" score
│   ├── test_simulator.py       # Unit tests: simulator correctness
│   ├── test_validator.py       # Unit tests: integrity checks / env stripping / determinism
│   ├── test_evaluator.py       # Unit tests: end-to-end evaluation behavior
│   ├── data/instances/         # 8 fixed instances (seed-fixed, reproducible)
│   └── requirements.txt
├── frontier_eval/              # UnifiedTask metadata (ConnectFour/AntGame pattern)
├── Task.md                     # Task rules, interface, scoring, reference scores
└── README.md
```

## Requirements

- Python >= 3.10, standard library only (no third-party dependencies).
- Runtime is pure-Python simulation; each instance evaluation takes well under a second for
  the baseline solver.

## Run

```powershell
# Score a solver on the fixed 8-instance set (default 60s time budget per instance)
python verification/evaluate.py baseline/solver.py

# Add runtime-generated instances (anti-hardcoding)
python verification/evaluate.py baseline/solver.py --generate-seed <SEED>

# Tighter budget (challenge tier: 10s)
python verification/evaluate.py baseline/solver.py --time-budget 10
```

### Docker

The evaluator is pure stdlib, so a minimal `python` image suffices. Build it and
use the unified runtime's `isolation_mode=docker`:

```bash
# Build (inside the TelecomBackup directory)
docker build -t telecombackup-benchmark -f verification/docker/Dockerfile .

# From the repo root
python -m frontier_eval task=unified task.benchmark=PowerSystems/TelecomBackup algorithm.iterations=0 \
  task.runtime.isolation_mode=docker task.runtime.docker_image=telecombackup-benchmark
```

> Docker isolation is validated on Linux / WSL. `frontier_eval/eval_command.txt`
> injects the host-benchmark path via the `{benchmark_source}` placeholder, so
> scoring works without framework changes; if the container user cannot write
> the evaluation sandbox, set `task.runtime.docker_user=<host uid>:<host gid>`
> (e.g. `1000:1000`). On Windows hosts the unified docker path is blocked by a
> framework path bug (`Path.resolve()` rewrites container paths to drive
> paths) — run docker mode under WSL instead.

## Tests

```powershell
# From the TelecomBackup task directory (stdlib unittest, no dependencies)
python -m unittest discover -s verification -p "test_*.py"
```

34 tests across four modules (simulator / validator / evaluator / sandbox): simulator correctness (manual golden cases, interval
normalization, battery depletion, coverage constraint, determinism), validator integrity
(EVOLVE-BLOCK / forbidden references / absolute paths / per-instance hardcoding / env
stripping / determinism probe), and evaluator behavior (scoring, malformed/timeout/preflight
handling, runtime generation, reproducibility).

To run inside the Frontier-Eng framework (unified task):

```powershell
# Windows: point the unified runtime at the venv python (WSL bash can't run Windows exes,
# so also use an MSYS2/Git bash instead of the default `bash`). Set PYTHONUTF8=1 to avoid
# GBK decoding crashes in some framework libs, and raise the LLM timeout for thinking models.
$env:PYTHONUTF8 = "1"
$env:FRONTIER_EVAL_UNIFIED_PYTHON = "<repo>\.venvs\frontier-eval-driver\Scripts\python.exe"
$env:TELECOM_EVAL_GENERATE_SEED = "<SEED>"  # runtime-generated instances (anti-hardcoding); 勿用固定值
python -m frontier_eval task=unified task.benchmark=PowerSystems/TelecomBackup algorithm.iterations=0 "task.runtime.shell=<path to Git Bash>" llm.timeout=300
```

Note: evaluation spawns solver subprocesses with a time budget; if running very slow solvers,
increase `FRONTIER_EVAL_EVALUATOR_TIMEOUT_S` accordingly (e.g. 1200).

## Integrity / threat model

- **Runtime-generated instances**: with `TELECOM_EVAL_GENERATE_SEED` set, the evaluator
  generates fresh instances at evaluation time (temp dir, never in the repo/sandbox), so a
  candidate cannot pre-position solutions for them.
- **Candidate env stripping**: candidate subprocesses get `FRONTIER_*` / `TELECOM_EVAL_*`
  variables stripped (see `verification/validator.py`), closing the host-env side channel.
- **Static checks**: EVOLVE-BLOCK markers + fixed-region byte diff vs the initial baseline,
  forbidden imports of evaluation/generation modules, absolute paths, per-instance hardcoding,
  plus a determinism probe (two runs must match). Any violation scores 0.
- Honest note: in process mode the candidate has host filesystem access (framework-wide
  limitation); this benchmark relies on the layered defenses above. `verification/simulator.py`
  is intentionally exposed as a white-box scorer for candidate-side search.
- **Sandbox scope** (design trade-off): the 8 fixed instances and the evaluator/validator
  sources are visible to the candidate during evolution (they are needed for scoring and the
  simulator is intentionally usable). Anti-hardcoding therefore relies on
  `TELECOM_EVAL_GENERATE_SEED` (fresh instances at evaluation time — set it, do not use a
  fixed seed); the name-keyed hardcoding check is best-effort (array-index dispatch can evade
  it, as in any static check). The reference solver (`ref_solver.py`) and the generator are
  **not** copied into the sandbox and are additionally forbidden by the validator.

## Scoring

- Instances = 8 fixed (N = 20..40 sites, K = 6..12 power supplies, each instance carries its
  power-consumption params `p_silent` / `p_work_base` / `p_work_coef`) + runtime-generated when
  `TELECOM_EVAL_GENERATE_SEED` is set; score = mean backup time (minutes).
- Malformed output / out-of-range intervals / crash / timeout ⇒ 0 points for that instance.
- **Power calibration**: instances are generated with `p_silent=0.05`, `p_work_base=3.0`,
  `p_work_coef=3.0` (silent is cheap, working is expensive), and the generator accepts only
  instances where a multi-rest stagger (rest 1..3 supplies at a time) beats "always-on" by ≥ 25%
  per instance — so the scheduling problem has large, reproducible headroom (verified: +28%..+119%,
  avg +54%).
- Reference scores (measured on the fixed 8 instances, deepseek-v4-flash agents; agent scores are
  **verified by directly evaluating the saved programs** from the task directory — candidate
  solvers resolve `verification/simulator.py` relative to their own location, so re-evaluating a
  saved program from an arbitrary path silently degrades it to the always-on fallback). **The
  published agent scores are on the fixed 8 instances only** (no `TELECOM_EVAL_GENERATE_SEED`
  was set for those runs); set the seed to additionally score fresh instances, which is the
  recommended anti-hardcoding configuration:
  - baseline (always-on, no scheduling): **176.2** minutes
  - agent (openevolve, 25 iterations, best saved program): **414.4** minutes (+135%; run `20260816_130700`); on fixed 8 + 8 generated (seed 42): **410.9**
  - agent (ShinkaEvolve, 15 generations, best generation program): **312.5** minutes (+77%; run `20260816_214014`, gen 3); on fixed 8 + 8 generated (seed 42): **319.4**
  - agent (AB-MCTS, 15 iterations, best saved program): **266.9** minutes (+52%; run `20260816_220646`); on fixed 8 + 8 generated (seed 42): **280.3**
  - reference heuristic (`verification/ref_solver.py`, multi-rest rotation): **271.2** minutes (+54%)
  - **multi-run statistics** (3 runs per framework, best valid saved program per run):
    - openevolve (25 iterations): 414.4 / 298.1 / 357.5 → **mean 356.7 ± 47.5**
    - ShinkaEvolve (15 generations): 312.5 / 357.5 / 325.6 → **mean 331.9 ± 18.9**
    - AB-MCTS (15 iterations): 266.9 / 208.8 / 227.5 → **mean 234.4 ± 24.2**
  - horizon: 480 minutes (upper bound if coverage never fails)
  - note: with 5-15 iterations agents mostly plateau at the baseline; more iterations let all
    three frameworks discover stagger/coverage-driven schedules that beat it (openevolve even
    found a near-horizon minimal-covering-subset schedule, surpassing the reference heuristic).
    ShinkaEvolve's 25-generation run produced a higher-scoring but **non-deterministic** program
    (time-budgeted simulated annealing) that fails the determinism probe — only deterministic
    programs count (312.5 from the 15-generation run is the valid best).

## Time Budget Tiers (from the original problem)

| Tier | --time-budget | Notes |
|---|---|---|
| Base | 300 s | T+1: 10-min solve |
| Advanced (default) | 60 s | T+2: 1-min solve |
| Challenge | 10 s | T+3: 10-s solve |
