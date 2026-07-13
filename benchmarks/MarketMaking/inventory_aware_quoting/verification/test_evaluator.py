from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

try:
    from verification.evaluator import (
        AnchorPolicy,
        _is_feasible,
        _robust_diagnostic_score,
        _run_policy,
        _scenario_score,
        evaluate,
    )
    from verification.simulator import EVALUATION_SCENARIOS
    _EVALUATOR_MODULE = "verification.evaluator"
except ModuleNotFoundError:  # pytest invoked from the repository root
    from evaluator import (
        AnchorPolicy,
        _is_feasible,
        _robust_diagnostic_score,
        _run_policy,
        _scenario_score,
        evaluate,
    )
    from simulator import EVALUATION_SCENARIOS
    _EVALUATOR_MODULE = "evaluator"


class FixedInventoryPolicy:
    base = 14.0
    size = 3
    skew = 0.15

    @classmethod
    def decide_quotes(cls, observation: dict[str, Any]) -> dict[str, dict[str, float | int]]:
        actions: dict[str, dict[str, float | int]] = {}
        for symbol, asset in observation["assets"].items():
            inventory = float(asset["dual_inventory"])
            actions[symbol] = {
                "bid_offset_bps": min(70.0, max(1.0, cls.base + cls.skew * inventory)),
                "ask_offset_bps": min(70.0, max(1.0, cls.base - cls.skew * inventory)),
                "bid_size": cls.size if inventory + cls.size <= 48 else 0,
                "ask_size": cls.size if inventory - cls.size >= -48 else 0,
            }
        return actions


class AdaptivePolicy:
    @staticmethod
    def decide_quotes(observation: dict[str, Any]) -> dict[str, dict[str, float | int]]:
        actions: dict[str, dict[str, float | int]] = {}
        for symbol, asset in observation["assets"].items():
            inventory = float(asset["dual_inventory"])
            flow = float(asset["order_imbalance"])
            center_shift = 5.0 * flow - 0.35 * inventory
            actions[symbol] = {
                "bid_offset_bps": min(70.0, max(1.0, 14.0 - center_shift)),
                "ask_offset_bps": min(70.0, max(1.0, 14.0 + center_shift)),
                "bid_size": 4 if inventory + 4 <= 48 else 0,
                "ask_size": 4 if inventory - 4 >= -48 else 0,
            }
        return actions


class EvaluatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.anchor = {
            scenario.name: _run_policy(AnchorPolicy, scenario)
            for scenario in EVALUATION_SCENARIOS
        }

    def _candidate(self, source: str) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="evaluator_candidate_"))
        path = directory / "candidate.py"
        path.write_text(source, encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        return path

    def test_anchor_is_feasible_and_scores_fifty(self) -> None:
        for scenario in EVALUATION_SCENARIOS:
            metrics = self.anchor[scenario.name]
            self.assertTrue(_is_feasible(metrics))
            self.assertAlmostEqual(_scenario_score(metrics, metrics), 50.0)

    def test_adaptation_beats_tuned_fixed_policy(self) -> None:
        fixed_scores = []
        adaptive_scores = []
        for scenario in EVALUATION_SCENARIOS:
            fixed = _run_policy(FixedInventoryPolicy, scenario)
            adaptive = _run_policy(AdaptivePolicy, scenario)
            self.assertTrue(_is_feasible(fixed))
            self.assertTrue(_is_feasible(adaptive))
            anchor = self.anchor[scenario.name]
            fixed_scores.append(_scenario_score(fixed, anchor))
            adaptive_scores.append(_scenario_score(adaptive, anchor))
        fixed_total = _robust_diagnostic_score(fixed_scores)
        adaptive_total = _robust_diagnostic_score(adaptive_scores)
        self.assertLess(fixed_total, 65.0)
        self.assertGreater(adaptive_total, fixed_total + 5.0)

    def test_invalid_candidate_has_noncompetitive_combined_score(self) -> None:
        candidate = self._candidate(
            "def decide_quotes(observation):\n"
            "    return {s: {'bid_offset_bps': 80, 'ask_offset_bps': 80, "
            "'bid_size': 0, 'ask_size': 0} for s in observation['assets']}\n"
        )
        with patch(
            f"{_EVALUATOR_MODULE}.EVALUATION_SCENARIOS",
            EVALUATION_SCENARIOS[:2],
        ):
            result = evaluate(candidate)
        self.assertEqual(result["valid"], 0.0)
        self.assertEqual(result["combined_score"], 0.0)
        self.assertGreaterEqual(result["diagnostic_score"], 0.0)

    def test_malformed_candidate_produces_invalid_result_not_crash(self) -> None:
        candidate = self._candidate("def broken(:\n")
        with patch(
            f"{_EVALUATOR_MODULE}.EVALUATION_SCENARIOS",
            EVALUATION_SCENARIOS[:1],
        ):
            result = evaluate(candidate)
        self.assertEqual(result["valid"], 0.0)
        self.assertEqual(result["combined_score"], 0.0)
        self.assertIn("error", result["rows"][0])

    def test_candidate_cannot_monkeypatch_parent_evaluator(self) -> None:
        candidate = self._candidate(
            "import sys\n"
            "setattr(sys.modules['__main__'], '_scenario_score', lambda *a: 100.0)\n"
            "def decide_quotes(observation):\n"
            "    return {s: {'bid_offset_bps': 80, 'ask_offset_bps': 80, "
            "'bid_size': 0, 'ask_size': 0} for s in observation['assets']}\n"
        )
        with patch(
            f"{_EVALUATOR_MODULE}.EVALUATION_SCENARIOS",
            EVALUATION_SCENARIOS[:1],
        ):
            result = evaluate(candidate)
        self.assertEqual(result["combined_score"], 0.0)
        self.assertEqual(result["valid"], 0.0)

    def test_fresh_worker_prevents_cross_scenario_state_leakage(self) -> None:
        candidate = self._candidate(
            "counter = 0\n"
            "def decide_quotes(observation):\n"
            "    global counter\n"
            "    if observation['step'] == 0 and counter != 0:\n"
            "        raise RuntimeError('state leaked')\n"
            "    counter += 1\n"
            "    return {s: {'bid_offset_bps': 18, 'ask_offset_bps': 18, "
            "'bid_size': 2, 'ask_size': 2} for s in observation['assets']}\n"
        )
        with patch(
            f"{_EVALUATOR_MODULE}.EVALUATION_SCENARIOS",
            EVALUATION_SCENARIOS[:2],
        ):
            result = evaluate(candidate)
        self.assertTrue(all("error" not in row for row in result["rows"]))

    def test_candidate_failure_stops_launching_more_workers(self) -> None:
        candidate = self._candidate("def decide_quotes(observation): return {}\n")
        scenarios = EVALUATION_SCENARIOS[:3]
        with (
            patch(f"{_EVALUATOR_MODULE}.EVALUATION_SCENARIOS", scenarios),
            patch(
                f"{_EVALUATOR_MODULE}._run_candidate",
                side_effect=RuntimeError("deterministic failure"),
            ) as run_candidate,
        ):
            result = evaluate(candidate)
        self.assertEqual(run_candidate.call_count, 1)
        self.assertEqual(len(result["rows"]), 3)
        self.assertEqual(result["combined_score"], 0.0)


if __name__ == "__main__":
    unittest.main()
