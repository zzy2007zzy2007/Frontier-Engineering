"""区域备电评测入口。

CLI：python verification/evaluate.py <solver.py> [--time-budget 60] [--data-dir ...]
对每个实例：subprocess 运行候选求解器（超时 = 时间预算），解析输出 on_intervals，
调 simulator 模拟打分；超时/格式错/越界 -> 该实例 0 分。
分数 = 各实例备电时长的平均值（分钟）。

完整性 / 防作弊（对齐 CVRP 基准的经验）：
  * 评分前静态检查候选（EVOLVE-BLOCK 标记与标记外代码、禁引用评测/生成模块、
    禁绝对路径、禁按实例名硬编码）——见 verification/validator.py；
  * 候选子进程环境剥离 FRONTIER_*/TELECOM_EVAL_*（candidate_env），封侧信道；
  * 运行时生成实例（TELECOM_EVAL_GENERATE_SEED 设置时，评测现场按种子生成新实例，
    候选无法预先记忆；生成的实例只存在于临时目录，不落仓库/沙箱）。
  * 确定性探针：跨规模选若干实例（小/中/大 + 一个生成实例）各跑两次，输出必须一致。

环境变量：
    TELECOM_EVAL_GENERATE_SEED  设置后开启运行时生成（防硬编码）
    TELECOM_EVAL_GENERATE_COUNT 生成实例数（默认 8）

对外接口（供 frontier_eval/evaluator.py 包装）：
    evaluate(program_path, *, time_budget=60.0) -> {"combined_score": float,
            "valid": float, "per_instance": {...}}
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# 保证无论从哪个 cwd/以何种方式加载，都能 import 到同目录的模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from simulator import simulate, load_instance  # noqa: E402
from validator import candidate_env, check_candidate, check_determinism  # noqa: E402

INVALID_SCORE = 0.0
DATA_DIR = Path(__file__).resolve().parent / "data" / "instances"
DEFAULT_GENERATE_COUNT = 8
# 运行时生成实例的规模循环（与 generator 默认一致）
GEN_SIZES = (20, 24, 28, 32, 36, 40)


def _source_benchmark_dir() -> Path | None:
    """宿主基准目录（unified 沙箱通过该环境变量暴露），用于读取初始 baseline 做比对。"""
    raw = os.environ.get("FRONTIER_EVAL_UNIFIED_SOURCE_BENCHMARK_DIR", "").strip()
    if raw:
        path = Path(raw)
        if path.is_dir():
            return path
    return None


def _parse_on_intervals(raw: str, k: int, horizon: int) -> list[list[list[int]]] | None:
    """解析候选 stdout 为 on_intervals；非法返回 None。

    格式：{"on": [[[a,b),...], ...]}，on[k] 为电源 k 的开启区间（半开 [a,b)）。
    """
    try:
        text = raw.strip()
        if not text:
            return None
        obj = json.loads(text)
        if isinstance(obj, dict):
            obj = obj.get("on")
        if not isinstance(obj, list) or len(obj) != k:
            return None
        out: list[list[list[int]]] = []
        for ivs in obj:
            if not isinstance(ivs, list):
                return None
            cur: list[list[int]] = []
            for iv in ivs:
                if not isinstance(iv, list) or len(iv) != 2:
                    return None
                a, b = iv
                if isinstance(a, bool) or isinstance(b, bool):
                    return None
                if not isinstance(a, int) or not isinstance(b, int):
                    return None
                if a < 0 or b > horizon or a > b:
                    return None
                cur.append([a, b])
            out.append(cur)
        return out
    except Exception:
        return None


def _run_one(program_path: Path, inst_path: Path, time_budget: float,
             python: str) -> tuple[float, dict[str, Any]]:
    inst = load_instance(inst_path)
    k = len(inst["groups"])
    horizon = int(inst["horizon"])
    started = time.time()
    try:
        proc = subprocess.run(
            [python, str(program_path), str(inst_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=time_budget,
            cwd=str(program_path.parent),
            env=candidate_env(),
        )
        elapsed = time.time() - started
        if proc.returncode != 0:
            return 0.0, {"status": "crash", "stderr_tail": proc.stderr[-500:]}
        on = _parse_on_intervals(proc.stdout, k, horizon)
        if on is None:
            return 0.0, {"status": "bad_output", "stdout_tail": proc.stdout[-500:]}
        minutes = simulate(inst, on)
        return minutes, {"status": "ok", "on": on, "minutes": round(minutes, 1),
                         "elapsed_s": round(elapsed, 2)}
    except subprocess.TimeoutExpired:
        return 0.0, {"status": "timeout", "budget_s": time_budget}
    except Exception as exc:
        return 0.0, {"status": "error", "message": str(exc)}


def _load_host_module(mod_name: str):
    """从宿主 benchmark 目录加载 `verification/<mod>.py`（沙箱内不含该模块时）。

    沙箱内不复制 generator.py（候选不可见）；评测器需要生成器时经
    FRONTIER_EVAL_UNIFIED_SOURCE_BENCHMARK_DIR 从宿主加载（CVRP 同款模式）。
    """
    import importlib.util

    src = os.environ.get("FRONTIER_EVAL_UNIFIED_SOURCE_BENCHMARK_DIR", "").strip()
    if src and Path(src).is_dir():
        path = Path(src) / "verification" / f"{mod_name}.py"
        if path.is_file():
            spec = importlib.util.spec_from_file_location(f"_tb_{mod_name}", path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def _generate_instances(base_seed: int, count: int, out_dir: Path) -> list[Path]:
    """按种子现场生成新实例（防硬编码）。派生种子 base_seed*1000+i，可复现。

    生成器从宿主加载；生成实例逐个过 `_stagger_ok`（错峰 ≥ 全程开启 25%），
    不合格跳过——保证生成实例同样奖励调度（与固定实例的验收一致）。
    """
    gen_mod = _load_host_module("generator")
    if gen_mod is None:
        import generator as gen_mod  # 直跑（非沙箱）时的本地回退

    paths: list[Path] = []
    i = 0
    attempts = 0
    while len(paths) < count and attempts < count * 64:
        inst = gen_mod.generate(base_seed * 1000 + i, GEN_SIZES[i % len(GEN_SIZES)])
        i += 1
        attempts += 1
        if not gen_mod._stagger_ok(inst):
            continue
        path = out_dir / f"gen_{base_seed}_{len(paths) + 1}.json"
        path.write_text(json.dumps(inst, ensure_ascii=False) + "\n", encoding="utf-8")
        paths.append(path)
    return paths


def _select_probes(instances: list[Path], n: int = 3) -> list[Path]:
    """跨规模选确定性探针：按排序取 小/中/大 各一（外加一个生成实例，若有）。"""
    if len(instances) <= n:
        return list(instances)
    idxs = sorted({0, len(instances) // 2, len(instances) - 1})
    probes = [instances[i] for i in idxs]
    gen = [p for p in instances if p.name.startswith("gen_")]
    if gen:
        probes.append(gen[0])
    return probes


def evaluate(program_path: str, *, time_budget: float = 60.0,
             data_dir: str | Path | None = None,
             python: str | None = None) -> dict[str, Any]:
    """评测候选求解器：返回 metrics 字典。"""
    prog = Path(program_path).resolve()
    if not prog.exists():
        return {"combined_score": 0.0, "valid": 0.0, "per_instance": {},
                "error": f"program not found: {prog}"}

    # 静态完整性检查（EVOLVE-BLOCK 标记/只读区比对、禁引用、禁绝对路径、禁硬编码）
    baseline_path = None
    src_dir = _source_benchmark_dir()
    if src_dir is not None:
        candidate_baseline = src_dir / "baseline" / "solver.py"
        if candidate_baseline.is_file():
            baseline_path = candidate_baseline
    violations = check_candidate(prog, baseline_path=baseline_path)

    # 实例池 = 固定实例（本地 data-dir 存在时）+ 运行时生成（设了生成种子时）
    inst_dir = Path(data_dir).resolve() if data_dir else DATA_DIR
    instances: list[Path] = []
    if inst_dir.is_dir():
        instances = sorted(inst_dir.glob("instance_*.json"))

    gen_seed_raw = os.environ.get("TELECOM_EVAL_GENERATE_SEED", "").strip()
    tmp_dir: Path | None = None
    if gen_seed_raw:
        try:
            base_seed = int(gen_seed_raw)
        except ValueError:
            base_seed = 0
        gen_count = DEFAULT_GENERATE_COUNT
        try:
            gen_count = max(0, int(os.environ.get("TELECOM_EVAL_GENERATE_COUNT", "").strip()))
        except ValueError:
            pass
        if gen_count > 0:
            tmp_dir = Path(tempfile.mkdtemp(prefix="telecom_eval_"))
            instances.extend(_generate_instances(base_seed, gen_count, tmp_dir))

    if not instances:
        return {"combined_score": 0.0, "valid": 0.0, "per_instance": {},
                "error": f"no instances (data_dir={inst_dir}, generate_seed={gen_seed_raw!r})"}

    py = python or sys.executable

    # 确定性探针：跨规模选若干实例（小/中/大 + 一个生成实例，若有）各跑两次，
    # 输出必须一致（候选不能只在最小实例上确定）。
    if not violations:
        for probe in _select_probes(instances):
            det_ok, det_note = check_determinism(py, prog, probe, time_budget)
            if not det_ok:
                violations = [f"determinism check failed on {probe.name}: {det_note}"]
                break

    per_instance: dict[str, Any] = {}
    total = 0.0
    all_valid = True
    for inst_path in instances:
        if violations:
            per_instance[inst_path.name] = {"status": "preflight_failed",
                                            "reasons": violations}
            continue
        minutes, info = _run_one(prog, inst_path, time_budget, py)
        per_instance[inst_path.name] = info
        if minutes <= 0 and info.get("status") != "ok":
            all_valid = False
        total += minutes

    score = total / len(instances) if instances else 0.0
    return {
        "combined_score": round(score, 2),
        "valid": 1.0 if all_valid and not violations else 0.0,
        "per_instance": per_instance,
        "num_instances": len(instances),
        "time_budget_s": time_budget,
        "generate_seed": gen_seed_raw or None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="区域备电评测")
    parser.add_argument("solver", help="候选求解器脚本路径")
    parser.add_argument("--time-budget", type=float, default=60.0,
                        help="求解时间预算（秒），默认 60")
    parser.add_argument("--data-dir", type=str, default=None,
                        help="实例目录（默认 verification/data/instances）")
    parser.add_argument("--generate-seed", type=int, default=None,
                        help="运行时生成实例的种子（防硬编码；等价于设 TELECOM_EVAL_GENERATE_SEED）")
    args = parser.parse_args(argv)

    if args.generate_seed is not None:
        os.environ["TELECOM_EVAL_GENERATE_SEED"] = str(args.generate_seed)
    result = evaluate(args.solver, time_budget=args.time_budget, data_dir=args.data_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
