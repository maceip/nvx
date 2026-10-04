import json
import tempfile
import unittest
from pathlib import Path

from nvx_tools.performance import PerformanceError, gate_results, persist_results
from nvx_tools.warm_benchmark import collect


class WarmPerformanceTests(unittest.TestCase):
    def test_measured_median_and_regression_gate_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "result.json"
            metrics = {
                key: {"samples_ms": [4.0, 6.0], "p50_ms": 5.0}
                for key in (
                    "warm_pool_first_stdout",
                    "warm_pool_completion",
                    "cold_image_first_stdout",
                )
            }
            document = {
                "benchmark_version": 1,
                "platform": "fixture-hvf",
                "metrics": metrics,
            }
            source.write_text(json.dumps(document))
            collect("fixture-hvf", "baseline", source, root / "baseline")
            collect("fixture-hvf", "current", source, root / "target")

            def gate(threshold: float = 20.0) -> int:
                return gate_results(
                    baseline_dir=root / "baseline",
                    target_dir=root / "target",
                    window=10,
                    threshold=threshold,
                    minimum_history=1,
                    absolute_tolerance_ms=1,
                )

            self.assertEqual(gate(), 0)
            def strict_gate(require_history: bool = False) -> int:
                return gate_results(
                    baseline_dir=root / "baseline",
                    target_dir=root / "target",
                    window=10,
                    threshold=20.0,
                    minimum_history=10,
                    require_history=require_history,
                )

            self.assertEqual(strict_gate(), 0)
            self.assertEqual(strict_gate(require_history=True), 1)
            history = root / "history"
            for index in range(10):
                measured = root / f"measured-{index}"
                collect("fixture-hvf", f"commit-{index}", source, measured)
                persist_results(measured, history)
            self.assertEqual(
                gate_results(
                    baseline_dir=history,
                    target_dir=root / "target",
                    window=10,
                    threshold=20.0,
                    minimum_history=10,
                    require_history=True,
                ),
                0,
            )
            metrics["warm_pool_first_stdout"] = {
                "samples_ms": [6.0, 6.5],
                "p50_ms": 6.25,
            }
            source.write_text(json.dumps(document))
            collect("fixture-hvf", "moderate-regression", source, root / "target")
            self.assertEqual(gate(threshold=40), 0)
            self.assertEqual(gate(), 1)
            metrics["warm_pool_first_stdout"] = {
                "samples_ms": [14.0, 16.0],
                "p50_ms": 15.0,
            }
            source.write_text(json.dumps(document))
            collect("fixture-hvf", "regressed", source, root / "target")
            self.assertEqual(gate(), 1)
            metrics["warm_pool_first_stdout"]["p50_ms"] = 1.0
            source.write_text(json.dumps(document))
            with self.assertRaisesRegex(PerformanceError, "p50"):
                collect("fixture-hvf", "tampered", source, root / "tampered")
