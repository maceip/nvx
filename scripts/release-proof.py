"""Run the complete platform acceptance battery and bind it to the release source."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from nvx_tools.build_constants import BuildConstants
from nvx_tools.common import ScriptError, openvmm_git_state, sha256_file
from nvx_tools.containment import render, validate_document
from nvx_tools.runtime_release import PLATFORMS

BACKENDS = {
    "darwin-arm64": "hvf",
    "linux-arm64": "kvm",
    "linux-kvm": "kvm",
    "linux-mshv": "mshv",
    "windows-whp": "whp",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = BuildConstants.REPO_ROOT

    def source_state() -> tuple[str, str]:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            text=True,
        ).strip()
        core, clean = openvmm_git_state(root / "openvmm")
        if dirty or not clean:
            raise ScriptError(
                "release acceptance requires clean NVX and pinned OpenVMM source"
            )
        return revision, core

    revision, core = source_state()
    proof = args.output_dir.resolve()
    if proof.exists():
        raise ScriptError("release proof must use a new directory")
    proof.mkdir(parents=True)
    backend = BACKENDS[args.platform]
    steps: list[dict[str, object]] = []

    def run(name: str, *arguments: str) -> None:
        log = proof / f"{name}.log"
        command = [sys.executable, str(root / "scripts/nvx.py"), *arguments]
        started = time.monotonic()
        print(f"Running release gate: {name}", flush=True)
        with log.open("wb") as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT)
        steps.append(
            {
                "name": name,
                "argv": arguments,
                "returncode": result.returncode,
                "duration_seconds": time.monotonic() - started,
                "log": log.name,
                "sha256": sha256_file(log),
            }
        )
        (proof / "progress.json").write_text(json.dumps(steps, indent=2) + "\n")
        if result.returncode:
            raise ScriptError(f"release gate {name} failed; see {log}")

    run("doctor", "doctor", "--backend", backend, "--json")
    run(
        "scenarios",
        "test-microvm",
        "--backend",
        backend,
        "--output-dir",
        str(proof / "scenarios"),
    )
    containment = json.loads(
        (proof / "scenarios/containment/containment.json").read_bytes()
    )
    validate_document(containment)
    if containment["backend"] != backend:
        raise ScriptError("containment result belongs to another backend")
    (proof / "containment-matrix.md").write_text(render(containment))
    run(
        "determinism",
        "verify-guest-determinism",
        "--image",
        "alpine:3.20",
        "--image",
        "python:3.12-slim",
        "--work-dir",
        str(proof / "determinism"),
    )
    with tempfile.TemporaryDirectory(
        prefix="release-warm-", dir=root / "build"
    ) as temporary:
        template = Path(temporary) / "template"
        run(
            "warm",
            "warm",
            "--image",
            "python:3.12-slim",
            "--backend",
            backend,
            "--output",
            str(template),
        )
        series = (
            f"{args.platform}-{backend}"
            if args.platform in ("darwin-arm64", "linux-arm64")
            else args.platform
        )
        run(
            "benchmark",
            "benchmark",
            "--suite",
            "warm-pool",
            "--backend",
            backend,
            "--template",
            str(template),
            "--runs",
            "20",
            "--platform",
            series,
            "--output",
            str(proof / "benchmark.json"),
        )
    run(
        "collect",
        "performance",
        "collect-warm",
        "--platform",
        series,
        "--commit",
        revision,
        "--input",
        str(proof / "benchmark.json"),
        "--output-dir",
        str(proof / "performance"),
    )
    run(
        "performance",
        "performance",
        "gate",
        "--baseline-dir",
        str(root / "data/warm"),
        "--target-dir",
        str(proof / "performance"),
        "--minimum-history",
        "1",
        "--threshold",
        "20",
        "--absolute-tolerance-ms",
        "1",
        "--summary",
        str(proof / "performance-gate.md"),
    )
    document = {
        "proof_version": 1,
        "platform": args.platform,
        "backend": backend,
        "architecture": PLATFORMS[args.platform],
        "nvx_revision": revision,
        "core_revision": core,
        "steps": steps,
        "containment_sha256": sha256_file(
            proof / "scenarios/containment/containment.json"
        ),
    }
    if source_state() != (revision, core):
        raise ScriptError(
            "source changed during acceptance; no release proof was published"
        )
    (proof / "NVX-ACCEPTANCE.json").write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    main()
