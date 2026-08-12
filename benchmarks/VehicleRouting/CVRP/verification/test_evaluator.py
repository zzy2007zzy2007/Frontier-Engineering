"""Unit tests for the CVRP evaluator (stdlib unittest, no third-party deps).

Run from the CVRP task directory:
    python verification/test_evaluator.py
or from the repo root:
    python -m unittest discover -s benchmarks/VehicleRouting/CVRP/verification -p 'test_*.py'
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import evaluator as ev  # noqa: E402

CVRP_ROOT = Path(__file__).resolve().parents[1]


class TestParseInstance(unittest.TestCase):
    def test_parse_public_instance(self):
        inst = ev.parse_instance(CVRP_ROOT / "data/instances" / "VRP-19-2.vrp")
        self.assertEqual(inst["name"], "VRP-19-2")
        self.assertEqual(inst["n"], 19)  # depot id 1, customers 2..20
        self.assertEqual(inst["capacity"], 270)
        self.assertEqual(len(inst["demand"]), 20)
        self.assertEqual(inst["demand"][0], 0)
        self.assertEqual(len(inst["distance"]), 20)
        self.assertEqual(len(inst["distance"][0]), 20)
        self.assertEqual(inst["distance"][0][0], 0)

    def test_parse_heldout_instance(self):
        inst = ev.parse_instance(CVRP_ROOT / "data/instances_heldout" / "VHO-22-3.vrp")
        self.assertEqual(inst["name"], "VHO-22-3")
        self.assertEqual(inst["n"], 22)


class TestRouteDistance(unittest.TestCase):
    def test_known_distance(self):
        # 1D line: depot at 0, customers at 3 and 7 => depot->3->7->depot = 17
        dist = [
            [0, 3, 7],
            [3, 0, 4],
            [7, 4, 0],
        ]
        self.assertEqual(ev.route_distance([[1, 2]], dist), 3 + 4 + 7)
        self.assertEqual(ev.route_distance([], dist), 0)


class TestValidate(unittest.TestCase):
    def setUp(self):
        self.inst = ev.parse_instance(CVRP_ROOT / "data/instances" / "VRP-19-2.vrp")

    def test_valid_single_customer_routes(self):
        routes = [[c] for c in range(1, self.inst["n"] + 1)]
        ok, note, dist = ev.validate(routes, self.inst)
        self.assertTrue(ok, note)
        self.assertIsInstance(dist, int)
        self.assertGreater(dist, 0)

    def test_duplicate_customer(self):
        routes = [[1, 1]] + [[c] for c in range(2, self.inst["n"] + 1)]
        ok, note, _ = ev.validate(routes, self.inst)
        self.assertFalse(ok)
        self.assertIn("more than once", note)

    def test_capacity_violation(self):
        routes = [list(range(1, self.inst["n"] + 1))]
        ok, note, _ = ev.validate(routes, self.inst)
        self.assertFalse(ok)
        self.assertIn("capacity", note)

    def test_missing_customer(self):
        routes = [[1, 2]]
        ok, note, _ = ev.validate(routes, self.inst)
        self.assertFalse(ok)
        self.assertIn("not served", note)

    def test_out_of_range_customer(self):
        routes = [[self.inst["n"] + 5]] + [[c] for c in range(1, self.inst["n"] + 1)]
        ok, note, _ = ev.validate(routes, self.inst)
        self.assertFalse(ok)
        self.assertIn("out of range", note)

    def test_non_integer_customer(self):
        routes = [[1.5]] + [[c] for c in range(2, self.inst["n"] + 1)]
        ok, note, _ = ev.validate(routes, self.inst)
        self.assertFalse(ok)


class TestCheckCandidate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="cvrp_test_"))
        self.valid_candidate = CVRP_ROOT / "baseline" / "solver.py"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name: str, content: str) -> Path:
        path = self.tmp / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_baseline_is_admissible(self):
        self.assertEqual(ev.check_candidate(self.valid_candidate), [])

    def test_import_reference_solver_rejected(self):
        src = self.valid_candidate.read_text(encoding="utf-8")
        bad = src.replace(
            "def solve(instance):", "import verification.ref_solver as rs\ndef solve(instance):", 1
        )
        path = self._write("bad_import.py", bad)
        issues = ev.check_candidate(path)
        self.assertTrue(any("verification" in i or "ref_solver" in i for i in issues))

    def test_hardcoding_rejected(self):
        src = self.valid_candidate.read_text(encoding="utf-8")
        bad = src.replace(
            "def solve(instance):",
            'def solve(instance):\n    if instance["name"] == "VRP-19-2": return [[1]]',
            1,
        )
        path = self._write("bad_hardcode.py", bad)
        issues = ev.check_candidate(path)
        self.assertTrue(any("hardcode" in i for i in issues))

    def test_missing_markers_rejected(self):
        src = self.valid_candidate.read_text(encoding="utf-8")
        bad = src.replace(ev.EVOLVE_START, "").replace(ev.EVOLVE_END, "")
        path = self._write("bad_markers.py", bad)
        issues = ev.check_candidate(path)
        self.assertTrue(any("EVOLVE-BLOCK" in i for i in issues))

    def test_absolute_path_rejected(self):
        src = self.valid_candidate.read_text(encoding="utf-8")
        bad = src + "\n# C:\\\\Users\\\\x\\\\reference.json\n"
        path = self._write("bad_path.py", bad)
        issues = ev.check_candidate(path)
        self.assertTrue(any("absolute" in i for i in issues))


class TestLoadReference(unittest.TestCase):
    def test_reference_contains_all_instances(self):
        ref = ev.load_reference()
        self.assertEqual(len(ref), 24)  # 12 public + 12 held-out
        for name in (
            "VRP-19-2",
            "VRP-60-9",
            "VHO-22-3",
            "VHO-58-9",
        ):
            self.assertIn(name, ref)
            self.assertGreater(ref[name], 0)


class TestEvaluateBaseline(unittest.TestCase):
    def test_baseline_scores(self):
        import os

        os.environ["CVRP_EVAL_INSTANCES"] = "VRP-19-2 VHO-22-3"
        try:
            result = ev.evaluate(str(CVRP_ROOT / "baseline" / "solver.py"))
        finally:
            os.environ.pop("CVRP_EVAL_INSTANCES", None)
        metrics = result["metrics"]
        self.assertEqual(metrics["valid"], 1.0)
        self.assertEqual(metrics["instances"], 2.0)
        self.assertGreater(metrics["combined_score"], 40.0)
        self.assertLess(metrics["combined_score"], 100.0)
        # artifacts must not leak reference distances
        artifacts = result["artifacts"]
        self.assertNotIn("reference", artifacts)
        self.assertEqual(artifacts["reference_instance_count"], 24.0)


class TestSplitEvolveBlocks(unittest.TestCase):
    def test_split(self):
        src = "a\n# EVOLVE-BLOCK-START\nb\n# EVOLVE-BLOCK-END\nc\n"
        before, between, after = ev._split_evolve_blocks(src)
        self.assertEqual(before, "a\n")
        self.assertIn("b", between)
        self.assertEqual(after, "\nc\n")

    def test_missing_end(self):
        self.assertIsNone(ev._split_evolve_blocks("a\n# EVOLVE-BLOCK-START\nb\n"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
