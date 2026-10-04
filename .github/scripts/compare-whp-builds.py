"""Compare retained WHP builds sequentially on one native runner.

These measurements diagnose a regression and are never imported as history.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

BUILDS = {
    "baseline": (
        "19459df26f6d9184ee279bf84fedbb01e2044266",
        "75b6560159c4ba903025ffd99dc3c95909002f49",
        "c5a88eff5222cb66f5b656953ba0b9acb258088eb2d39860e5bf5792bb7af2ed",
    ),
    "candidate": (
        "fd639237c481b03c514233c23934c9885260dd26",
        "4b20f45f173bd4d971ec71a710fe9045c801da79",
        "d35c90036c73bc43c666a00eeeb937373487a7bf9e04949044d0b0f7aef7d8ad",
    ),
}


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


def state(root: Path, name: str) -> dict[str, str]:
    nvx, core, executable = BUILDS[name]
    value = {
        "nvx_revision": git(root, "rev-parse", "HEAD"),
        "core_revision": git(root / "openvmm", "rev-parse", "HEAD"),
        "nvx_status": git(root, "status", "--porcelain"),
        "core_status": git(root / "openvmm", "status", "--porcelain"),
        "executable_sha256": digest(root / "openvmm/target/release/openvmm.exe"),
    }
    metadata = json.loads((root / "build/openvmm.provenance.json").read_bytes())
    if (
        value["nvx_revision"] != nvx
        or value["core_revision"] != core
        or git(root, "ls-tree", "HEAD", "openvmm").split()[2] != core
        or value["nvx_status"]
        or value["core_status"]
        or value["executable_sha256"] != executable
        or metadata["source_revision"] != core
        or metadata["source_clean"] is not True
        or metadata["executable_sha256"] != executable
    ):
        raise RuntimeError(f"{name}: source or retained binary identity mismatch")
    return value


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("this diagnostic requires a native Windows WHP runner")
    workspace = Path.cwd()
    output = workspace / "build/whp-build-comparison"
    output.mkdir()
    checkouts = workspace / "build/whp-compare-checkouts"
    for name in BUILDS:
        root = checkouts / name
        (root / "build").mkdir(exist_ok=True)
        for source in (workspace / "build/whp-compare-guest").iterdir():
            if source.is_file():
                shutil.copy2(source, root / "build" / source.name)
        retained = workspace / "build" / f"whp-compare-{name}-build"
        binary = retained / "openvmm/target/release/openvmm.exe"
        target = root / "openvmm/target/release/openvmm.exe"
        target.parent.mkdir(parents=True)
        shutil.copy2(binary, target)
        shutil.copy2(
            retained / "build/openvmm.provenance.json",
            root / "build/openvmm.provenance.json",
        )
        state(root, name)

    # The NVX revisions differ only in their core pin. Verify the launcher,
    # runtime and benchmark sources before comparing the retained executables.
    source_sets = []
    for name in BUILDS:
        root = checkouts / name
        source_sets.append(
            {
                str(p.relative_to(root)): digest(p)
                for p in (root / "scripts").rglob("*")
                if p.is_file()
            }
        )
    if source_sets[0] != source_sets[1]:
        raise RuntimeError("NVX runtime sources differ between the two checkouts")

    trials = []
    # Reverse the order for the second pair to expose host drift.
    for index, name in enumerate(("baseline", "candidate", "candidate", "baseline"), 1):
        root = checkouts / name
        trial = output / f"{index}-{name}"
        trial.mkdir()
        template = trial / "template"
        before = state(root, name)
        env = os.environ.copy()
        env["NVX_IMAGE_CACHE"] = str(workspace / "build/whp-compare-image-cache")
        commands = [
            ("verify", ["verify"]),
            ("doctor", ["doctor", "--backend", "whp", "--json"]),
            (
                "seccomp",
                [
                    "test-microvm",
                    "--backend",
                    "whp",
                    "--scenario",
                    "seccomp-profile",
                    "--output-dir",
                    str(trial / "seccomp"),
                ],
            ),
            (
                "warm",
                [
                    "warm",
                    "--image",
                    "python:3.12-slim",
                    "--backend",
                    "whp",
                    "--output",
                    str(template),
                ],
            ),
            (
                "benchmark",
                [
                    "benchmark",
                    "--suite",
                    "warm-pool",
                    "--backend",
                    "whp",
                    "--template",
                    str(template),
                    "--runs",
                    "20",
                    "--timeout",
                    "120",
                    "--platform",
                    "windows-whp",
                    "--output",
                    str(trial / "benchmark.json"),
                ],
            ),
        ]
        steps = []
        for label, args in commands:
            log = trial / f"{label}.log"
            start = time.monotonic()
            with log.open("wb") as stream:
                result = subprocess.run(
                    [sys.executable, "scripts/nvx.py", *args],
                    cwd=root,
                    env=env,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            steps.append(
                {
                    "name": label,
                    "returncode": result.returncode,
                    "duration_seconds": time.monotonic() - start,
                    "log_sha256": digest(log),
                }
            )
            (trial / "progress.json").write_text(json.dumps(steps, indent=2) + "\n")
            print(index, name, label, result.returncode, flush=True)
            if result.returncode:
                raise RuntimeError(
                    f"{name} {label} failed; original log and payload retained"
                )
        after = state(root, name)
        if before != after:
            raise RuntimeError("source or executable changed during the measurement")
        benchmark = json.loads((trial / "benchmark.json").read_bytes())
        if benchmark["runs"] != 20 or benchmark["pool_size"] != 2:
            raise RuntimeError("unexpected canonical sampling counts")
        for metric in benchmark["metrics"].values():
            if (
                len(metric["samples_ms"]) != 20
                or abs(statistics.median(metric["samples_ms"]) - metric["p50_ms"])
                > 1e-7
            ):
                raise RuntimeError("raw samples do not match the recorded median")
        trials.append(
            {
                "build": name,
                "before": before,
                "after": after,
                "steps": steps,
                "benchmark_sha256": digest(trial / "benchmark.json"),
                "metrics_p50_ms": {
                    key: value["p50_ms"] for key, value in benchmark["metrics"].items()
                },
            }
        )
        (output / "result.json").write_text(
            json.dumps(
                {
                    "scope": "same-runner diagnostic; not acceptance or baseline history",
                    "order": ["baseline", "candidate", "candidate", "baseline"],
                    "trials": trials,
                },
                indent=2,
            )
            + "\n"
        )
        # Retire only a completed diagnostic template after its identity is
        # recorded; the canonical snapshot cleanup handles Windows attributes.
        sys.path.insert(0, str(root / "scripts"))
        from nvx_tools.mcp import _remove_snapshot

        payloads = [
            {
                "path": str(p.relative_to(template)),
                "sha256": digest(p),
                "size_bytes": p.stat().st_size,
            }
            for p in template.rglob("*")
            if p.is_file() and not p.is_symlink()
        ]
        (trial / "retired-template-payloads.json").write_text(
            json.dumps(payloads, indent=2) + "\n"
        )
        _remove_snapshot(template)


if __name__ == "__main__":
    main()
