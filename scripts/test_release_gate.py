"""Publication requires the whole matrix; stale or vacuous proof cannot pass."""

import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError, sha256_file
from nvx_tools.containment import PROBES
from nvx_tools.release_gate import (
    REQUIRED_STEPS,
    acceptance_performance_policy,
    gate_failure,
    require_core_artifact,
    verify_matrix,
)
from nvx_tools.runtime_release import PLATFORMS


class ReleaseGateTests(unittest.TestCase):
    def test_ci_limits_use_configuration_and_release_limits_stay_strict(self) -> None:
        environment = {
            "PERFORMANCE_REGRESSION_THRESHOLD": "50",
            "PERFORMANCE_REGRESSION_ABSOLUTE_TOLERANCE_MS": "10",
        }
        self.assertEqual(
            acceptance_performance_policy("ci", environment),
            {"threshold": 50, "absolute_tolerance_ms": 10},
        )
        self.assertEqual(
            acceptance_performance_policy("release", environment),
            {"threshold": 20, "absolute_tolerance_ms": 1},
        )
        self.assertEqual(
            acceptance_performance_policy("ci", {}),
            {"threshold": 50, "absolute_tolerance_ms": 10},
        )
        for field in environment:
            for value in ("nan", "inf", "-1", "invalid"):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ScriptError):
                        acceptance_performance_policy(
                            "ci", {**environment, field: value}
                        )

    def test_failed_gate_reports_bounded_redacted_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "scenarios.log"
            log.write_text(
                "early-only\n"
                + "padding " * 2000
                + "\nactual startup failure\nBearer private-token-value"
            )
            error = str(gate_failure("scenarios", log))
            self.assertIn("actual startup failure", error)
            self.assertNotIn("early-only", error)
            self.assertNotIn("private-token-value", error)
            self.assertLess(len(error), 4500)

    def test_acceptance_rejects_old_dirty_or_changed_core_binaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / "openvmm"
            executable.write_bytes(b"current compiled core")
            provenance = root / "openvmm.provenance.json"
            expected = {
                "format": 1,
                "source_revision": "b" * 40,
                "source_clean": True,
                "executable_sha256": sha256_file(executable),
            }
            provenance.write_text(json.dumps(expected))
            self.assertEqual(
                require_core_artifact("b" * 40, executable, provenance),
                expected["executable_sha256"],
            )
            for field, value in (
                ("source_revision", "a" * 40),
                ("source_clean", False),
                ("executable_sha256", "0" * 64),
                ("format", 2),
            ):
                with self.subTest(field=field):
                    provenance.write_text(json.dumps({**expected, field: value}))
                    with self.assertRaises(ScriptError):
                        require_core_artifact("b" * 40, executable, provenance)
            provenance.write_text(json.dumps(expected))
            executable.write_bytes(b"replaced compiled core")
            with self.assertRaises(ScriptError):
                require_core_artifact("b" * 40, executable, provenance)
            provenance.write_text("[]")
            with self.assertRaises(ScriptError):
                require_core_artifact("b" * 40, executable, provenance)

    def fixture(self, root: Path) -> None:
        for platform, arch in PLATFORMS.items():
            lane = root / f"release-runtime-{platform}"
            evidence = lane / "release-proof"
            evidence.mkdir(parents=True)
            steps: list[dict[str, object]] = []
            for name in sorted(REQUIRED_STEPS):
                log = evidence / f"{name}.log"
                log.write_text(
                    "Checked 3 metric(s), found 0 regression(s)"
                    if name == "performance"
                    else "fixture success"
                )
                steps.append(
                    {
                        "name": name,
                        "returncode": 0,
                        "log": log.name,
                        "sha256": sha256_file(log),
                    }
                )
            backend = (
                "hvf"
                if platform.startswith("darwin-")
                else "mshv"
                if platform.endswith("mshv")
                else "whp"
                if platform.endswith("whp")
                else "kvm"
            )
            containment = evidence / "scenarios/containment/containment.json"
            containment.parent.mkdir(parents=True)
            containment.write_text(
                json.dumps(
                    {
                        "containment_version": 1,
                        "backend": backend,
                        "results": [
                            {
                                "family": probe.family,
                                "verdict": "CONTAINED",
                                "control_verdict": "UNCONTAINED",
                                "scope": probe.scope,
                                "evidence": {"fixture": True},
                            }
                            for probe in PROBES
                        ],
                    }
                )
            )
            identity = {
                "platform": platform,
                "architecture": arch,
                "nvx_revision": "a" * 40,
                "core_revision": "b" * 40,
            }
            (evidence / "NVX-ACCEPTANCE.json").write_text(
                json.dumps(
                    {
                        **identity,
                        "proof_version": 1,
                        "backend": backend,
                        "steps": steps,
                        "containment_sha256": sha256_file(containment),
                    }
                )
            )
            with tarfile.open(lane / f"nvx-1.0.0-{platform}.tar.gz", "w:gz") as archive:
                body = json.dumps(
                    {
                        **identity,
                        "version": "1.0.0",
                        "development": False,
                        "source_clean": True,
                        "nvx_source_clean": True,
                        "signing": {"notarized": True},
                    }
                ).encode()
                member = tarfile.TarInfo("package/NVX-RELEASE.json")
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
            (lane / f"nvx-1.0.0-{arch}-sources.tar.gz").write_bytes(
                b"same-source-fixture"
            )
            for asset in lane.glob("*.tar.gz"):
                asset.with_name(asset.name + ".sigstore.jsonl").write_bytes(
                    b"signature-fixture"
                )

    def test_complete_matching_matrix_deduplicates_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.fixture(root / "input")
            verify_matrix(root / "input", "a" * 40, "b" * 40, "1.0.0", root / "publish")
            self.assertEqual(
                len(list((root / "publish").iterdir())), 2 * (len(PLATFORMS) + 2)
            )

    def test_stale_failed_missing_and_warmup_proofs_cannot_publish(self) -> None:
        for defect in (
            "revision",
            "missing",
            "log",
            "warmup",
            "control",
            "sources",
            "ci-policy",
            "loose-policy",
        ):
            with (
                self.subTest(defect=defect),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                self.fixture(root / "input")
                lane = root / "input/release-runtime-darwin-arm64"
                proof = lane / "release-proof/NVX-ACCEPTANCE.json"
                body = json.loads(proof.read_bytes())
                if defect == "revision":
                    body["nvx_revision"] = "c" * 40
                elif defect == "ci-policy":
                    body["acceptance_policy"] = "ci"
                elif defect == "loose-policy":
                    body["performance_policy"] = {
                        "threshold": 50,
                        "absolute_tolerance_ms": 10,
                    }
                elif defect == "missing":
                    body["steps"].pop()
                elif defect in ("log", "warmup"):
                    log = lane / "release-proof/performance.log"
                    log.write_text("Checked 0 metric(s), found 0 regression(s)")
                    if defect == "warmup":
                        next(
                            row for row in body["steps"] if row["name"] == "performance"
                        )["sha256"] = sha256_file(log)
                elif defect == "control":
                    path = lane / "release-proof/scenarios/containment/containment.json"
                    matrix = json.loads(path.read_bytes())
                    matrix["results"][0]["control_verdict"] = "CONTAINED"
                    path.write_text(json.dumps(matrix))
                    body["containment_sha256"] = sha256_file(path)
                else:
                    (lane / "nvx-1.0.0-aarch64-sources.tar.gz").unlink()
                proof.write_text(json.dumps(body))
                with self.assertRaises((ScriptError, OSError)):
                    verify_matrix(
                        root / "input", "a" * 40, "b" * 40, "1.0.0", root / "publish"
                    )
                self.assertFalse((root / "publish").exists())


if __name__ == "__main__":
    unittest.main()
