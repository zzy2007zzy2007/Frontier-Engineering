"""Self-contained unified evaluator for the CVRP benchmark.

This module runs entirely inside the evaluation sandbox. It embeds instance
parsing, route validation, scoring and candidate-integrity checks, so no
`verification/` files (including the reference solver) are copied into the
sandbox.

Security / integrity:
  * Reference distances are read from the host benchmark directory
    (FRONTIER_EVAL_UNIFIED_SOURCE_BENCHMARK_DIR) and are never copied into
    the sandbox; the candidate subprocess runs without that variable and
    without any reference-distance settings.
  * The candidate is checked before running: EVOLVE-BLOCK markers must be
    present, the fixed regions must match the initial baseline, and the
    source must not reference the verification module / reference solver /
    reference.json / absolute paths / per-instance hardcoded routes.
  * A determinism probe runs the candidate twice on the smallest instance;
    differing outputs invalidate the run.

The reference implementation (`verification/ref_solver.py`) stays out of the
sandbox and is only used to produce `data/reference.json` on the host.
"""
from __future__ import annotations

import json
import math
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

TASK_ROOT = Path(__file__).resolve().parents[1]  # <sandbox root> or CVRP dir
INSTANCES_DIR = TASK_ROOT / "data" / "instances"
HELDOUT_DIR = TASK_ROOT / "data" / "instances_heldout"

EVOLVE_START = "# EVOLVE-BLOCK-START"
EVOLVE_END = "# EVOLVE-BLOCK-END"

# Strong tokens: almost never appear in legitimate solver code, so a bare
# substring match is fine (e.g. "ref_solver", "reference.json").
STRONG_TOKENS = (
    "ref_solver",
    "grasp_solve",
    "savings_solve",
    "reference.json",
)
# Weak token "verification": only rejected in an import/from/module-path
# context, so a comment like "verification pass" is NOT a false positive.
FORBIDDEN_RE = (
    re.compile(r"\b(?:import|from)\s+verification\b"),
    re.compile(r"verification[\\/.]"),
)

HARDCODE_RE = re.compile(r"[\"'][A-Z][A-Z0-9-]*\d+-\d+[\"']\s*:")
ABS_PATH_RE = re.compile(r"[A-Za-z]:[\\/]|/home/|/Users/")

DEFAULT_TIMEOUT_S = 60


def parse_instance(path: Path) -> dict:
    """Parse a TSPLIB-style CVRP .vrp file into an instance dict."""
    text = path.read_text(encoding="utf-8", errors="ignore")
    coords: dict[int, tuple[float, float]] = {}
    demands: dict[int, int] = {}
    capacity = 0
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("CAPACITY"):
            capacity = int(line.split(":")[-1].strip())
            continue
        if upper == "NODE_COORD_SECTION":
            section = "coords"
            continue
        if upper == "DEMAND_SECTION":
            section = "demand"
            continue
        if upper == "DEPOT_SECTION":
            section = None
            continue
        if upper == "EOF" or upper.startswith(("EDGE_WEIGHT", "DISPLAY_DATA")):
            section = None
            continue
        if upper.startswith(("NAME", "COMMENT", "TYPE", "DIMENSION")):
            continue
        if section == "coords":
            parts = line.split()
            if len(parts) >= 3:
                coords[int(parts[0])] = (float(parts[1]), float(parts[2]))
        elif section == "demand":
            parts = line.split()
            if len(parts) >= 2:
                demands[int(parts[0])] = int(parts[1])
    n_customers = max(coords) - 1  # depot is id 1, customers are ids 2..n+1
    pts = [coords[1]] + [coords[i] for i in range(2, n_customers + 2)]
    dist = [[0] * (n_customers + 1) for _ in range(n_customers + 1)]
    for i in range(n_customers + 1):
        for j in range(n_customers + 1):
            dx = pts[i][0] - pts[j][0]
            dy = pts[i][1] - pts[j][1]
            dist[i][j] = int(round(math.hypot(dx, dy)))
    return {
        "name": path.stem,
        "n": n_customers,
        "capacity": capacity,
        "demand": [0] + [demands.get(i, 0) for i in range(2, n_customers + 2)],
        "distance": dist,
    }


def route_distance(routes: list, dist: list[list[int]]) -> int:
    total = 0
    for seq in routes:
        if not seq:
            continue
        total += dist[0][seq[0]]
        for a, b in zip(seq, seq[1:]):
            total += dist[a][b]
        total += dist[seq[-1]][0]
    return total


def validate(routes: Any, inst: dict) -> tuple[bool, str, int | None]:
    """Return (ok, error_message, total_distance)."""
    n, cap, demand = inst["n"], inst["capacity"], inst["demand"]
    if not isinstance(routes, list):
        return False, "routes is not a list", None
    seen: list[int] = []
    for seq in routes:
        if not isinstance(seq, list):
            return False, "a route is not a list", None
        load = 0
        for c in seq:
            if not isinstance(c, int) or isinstance(c, bool):
                return False, f"route contains non-integer {c!r}", None
            if c < 1 or c > n:
                return False, f"customer id {c} out of range 1..{n}", None
            if c in seen:
                return False, f"customer {c} visited more than once", None
            seen.append(c)
            load += demand[c]
        if load > cap:
            return False, f"route {seq} exceeds capacity {cap} (load {load})", None
    if set(seen) != set(range(1, n + 1)):
        missing = set(range(1, n + 1)) - set(seen)
        return False, f"customers not served: {sorted(missing)}", None
    return True, "", route_distance(routes, inst["distance"])


# ---------------------------------------------------------------------------
# Candidate integrity checks (mirrors verification/validator.py so the sandbox
# needs no verification/ files).
# ---------------------------------------------------------------------------
def split_evolve_blocks(src: str) -> tuple[str, str, str] | None:
    start = src.find(EVOLVE_START)
    end = src.find(EVOLVE_END)
    if start == -1 or end == -1 or end <= start:
        return None
    return (
        src[:start],
        src[start + len(EVOLVE_START) : end],
        src[end + len(EVOLVE_END) :],
    )


def fixed_region(parts: tuple[str, str, str]) -> str:
    return parts[0] + parts[2]


def static_check_source(src: str, baseline_src: str | None = None) -> list[str]:
    issues: list[str] = []
    parts = split_evolve_blocks(src)
    if parts is None:
        issues.append("missing EVOLVE-BLOCK-START / EVOLVE-BLOCK-END markers")
    elif baseline_src is not None:
        init_parts = split_evolve_blocks(baseline_src)
        if init_parts is not None and fixed_region(init_parts) != fixed_region(parts):
            issues.append("code outside EVOLVE-BLOCK differs from initial baseline")
    for token in STRONG_TOKENS:
        if token in src:
            issues.append(f"candidate references forbidden token {token!r}")
    for pat in FORBIDDEN_RE:
        if pat.search(src):
            issues.append("candidate references forbidden token 'verification'")
    if ABS_PATH_RE.search(src):
        issues.append("candidate contains an absolute filesystem path")
    if HARDCODE_RE.search(src):
        issues.append("candidate hardcodes per-instance solutions by name")
    return issues


def candidate_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if upper.startswith("FRONTIER_EVAL_UNIFIED_") or key in (
            "CVRP_EVAL_REFERENCE_JSON",
            "CVRP_EVAL_REFERENCES",
        ):
            del env[key]
    return env


def check_determinism(
    python: str,
    solver_path: Path,
    inst_path: Path,
    timeout: float,
) -> tuple[bool, str]:
    outputs: list[Any] = []
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix="cvrp_det_") as td:
            out_path = Path(td) / "out.json"
            try:
                proc = subprocess.run(
                    [python, str(solver_path), str(inst_path), str(out_path)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(solver_path.parent),
                    env=candidate_env(),
                )
            except subprocess.TimeoutExpired:
                return False, "timeout during determinism check"
            if proc.returncode != 0:
                return (
                    False,
                    f"candidate exited with code {proc.returncode} during "
                    f"determinism check: {(proc.stderr or '')[:200]}",
                )
            try:
                outputs.append(json.loads(out_path.read_text(encoding="utf-8")))
            except Exception as exc:
                return False, f"cannot parse determinism output: {exc}"
    if outputs[0] != outputs[1]:
        return False, "candidate is not deterministic (output differs across two runs)"
    return True, ""


def select_determinism_probes(
    inst_paths: list, parse_instance, count: int = 3
) -> list:
    """Pick min / median / max instances (by customer count) as determinism
    probes, so a solver that is deterministic on small instances but random on
    large ones cannot slip through a single-probe check."""
    parsed = sorted(
        ((p, parse_instance(p)) for p in inst_paths), key=lambda t: t[1]["n"]
    )
    if not parsed:
        return []
    idxs = sorted({0, len(parsed) // 2, len(parsed) - 1})
    return [parsed[i][0] for i in idxs[:count]]


# ---------------------------------------------------------------------------
# Host-only access (reference distances + initial baseline live on the host).
# ---------------------------------------------------------------------------
def _source_benchmark_dir() -> Path | None:
    raw = os.environ.get("FRONTIER_EVAL_UNIFIED_SOURCE_BENCHMARK_DIR", "").strip()
    if raw:
        path = Path(raw)
        if path.is_dir():
            return path
    return None


def load_reference() -> dict[str, float]:
    """Reference distances, read from the host (never from the sandbox)."""
    src = _source_benchmark_dir()
    if src is not None:
        path = src / "data" / "reference.json"
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                return {k: float(v) for k, v in raw.items()}
            except Exception:
                pass
    return {}


def _initial_baseline() -> Path | None:
    """Pristine baseline on the host, used for the fixed-region diff."""
    src = _source_benchmark_dir()
    if src is not None:
        path = src / "baseline" / "solver.py"
        if path.is_file():
            return path
    return None


def _parse_env_str(name: str) -> str | None:
    raw = os.environ.get(name, "").strip()
    return raw or None


def _all_instance_paths() -> list[Path]:
    paths = sorted(INSTANCES_DIR.glob("*.vrp"))
    if HELDOUT_DIR.is_dir():
        paths += sorted(HELDOUT_DIR.glob("*.vrp"))
    return paths


def _run_candidate(
    python: str, solver_path: Path, inst_path: Path, out_path: Path, timeout: float
) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [python, str(solver_path), str(inst_path), str(out_path)],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(solver_path.parent),
            env=candidate_env(),
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError as exc:
        return False, f"os error: {exc}"
    if proc.returncode != 0:
        return False, f"exited with code {proc.returncode}: {(proc.stderr or '')[:200]}"
    return True, ""


def evaluate(program_path: str, *, repo_root: Path | None = None) -> dict[str, Any]:
    solver_path = Path(program_path).resolve()
    if not solver_path.is_file():
        raise FileNotFoundError(f"candidate solver not found: {solver_path}")

    timeout = float(_parse_env_str("CVRP_EVAL_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
    inst_names = _parse_env_str("CVRP_EVAL_INSTANCES")
    max_instances = _parse_env_str("CVRP_EVAL_MAX_INSTANCES")

    inst_paths = _all_instance_paths()
    if inst_names:
        picked = [
            p
            for n in inst_names.replace(",", " ").split()
            for p in inst_paths
            if p.stem == n
        ]
        if picked:
            inst_paths = picked
    if max_instances:
        inst_paths = inst_paths[: int(max_instances)]

    reference = load_reference()
    python = sys.executable
    tmp_dir = Path(tempfile.mkdtemp(prefix="cvrp_eval_"))

    # Preflight: static integrity + determinism probe on the smallest instance.
    try:
        src_text = solver_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"metrics": {"combined_score": 0.0, "valid": 0.0}, "artifacts": {}}
    baseline_path = _initial_baseline()
    baseline_src = None
    if baseline_path is not None:
        try:
            baseline_src = baseline_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            baseline_src = None
    preflight = static_check_source(src_text, baseline_src)
    if not preflight and inst_paths:
        for probe in select_determinism_probes(inst_paths, parse_instance):
            det_ok, det_note = check_determinism(python, solver_path, probe, timeout)
            if not det_ok:
                preflight = [f"determinism check failed on {probe.stem}: {det_note}"]
                break

    rows = []
    for inst_path in inst_paths:
        inst = parse_instance(inst_path)
        out_path = tmp_dir / f"{inst['name']}.out.json"
        ok = True
        err = ""
        if preflight:
            ok = False
            err = "preflight: " + "; ".join(preflight)
        else:
            ok, err = _run_candidate(python, solver_path, inst_path, out_path, timeout)
        score = 0.0
        cand_dist = None
        valid = False
        note = err if not ok else ""
        if ok:
            try:
                routes = json.loads(out_path.read_text(encoding="utf-8"))
                valid, note, cand_dist = validate(routes, inst)
            except Exception as exc:
                note = f"output parse error: {exc}"
        if valid and cand_dist is not None and cand_dist > 0:
            ref = reference.get(inst["name"])
            if ref is not None and ref > 0:
                scale = float(os.environ.get("CVRP_EVAL_SCORE_SCALE", "1.0"))
                score = min(100.0, scale * 100.0 * ref / cand_dist)
            else:
                score = 0.0
                valid = False
                note = f"no reference distance for {inst['name']}"
        rows.append(
            {
                "name": inst["name"],
                "valid": bool(valid),
                "score": score,
                "candidate_distance": cand_dist,
                "note": note or "",
            }
        )

    all_valid = all(r["valid"] for r in rows)
    combined = (
        statistics.fmean(r["score"] for r in rows) if rows and all_valid else 0.0
    )
    metrics: dict[str, Any] = {
        "combined_score": float(combined),
        "valid": 1.0 if all_valid and rows else 0.0,
        "instances": float(len(rows)),
        "per_instance": {
            r["name"]: {"score": r["score"], "valid": r["valid"], "note": r["note"]}
            for r in rows
        },
    }
    artifacts: dict[str, Any] = {
        "candidate_path": str(solver_path),
        "timeout_s": timeout,
        # Reference distances are deliberately NOT included (no leak).
        "reference_instance_count": float(len(reference)),
    }
    return {"metrics": metrics, "artifacts": artifacts}
