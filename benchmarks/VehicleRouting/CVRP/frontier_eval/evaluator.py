"""Unified evaluator entry point for the CVRP benchmark.

This module is loaded by `frontier_eval/run_eval.py` and must expose a
top-level `evaluate(program_path, *, repo_root=None)` callable. The real
implementation lives in `verification/evaluator.py`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any


def _load_verification_evaluator() -> Any:
    evaluator_path = (
        Path(__file__).resolve().parent.parent / "verification" / "evaluator.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_cvrp_verification_evaluator", evaluator_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"Failed to load verification evaluator from {evaluator_path}"
        )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(program_path: str, *, repo_root: Path | None = None) -> Any:
    module = _load_verification_evaluator()
    result = module.evaluate(program_path, repo_root=repo_root)
    if isinstance(result, dict) and "metrics" in result:
        return result
    return {"metrics": result, "artifacts": {}}
