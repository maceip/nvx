"""Measure existing source revisions with the unchanged canonical warm-pool suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

CORE = "75b6560159c4ba903025ffd99dc3c95909002f49"
REVISIONS = [
    "0244ae555630f309929a46af666d5d11e3a08d54",
    "4385058610c8138f336e919308c22b189307c769",
    "75c513f58d6f183fbea14fb8588360b7e6cf2a07",
    "598ca08b987c186fbde8a5757dd66dc379e70860",
    "b49531628e340b2c3e03766c6beeaae4cfec0ab4",
    "b977e3dbb2dd8cd1d0cf3a3bc1f91353f9916bee",
    "911764666548aa8982d04940932281a83631fcec",
    "5854598dd1cd238005b2e4cb61e1d74c1bc5d8a1",
    "8a0f6474a72414a10d9c0ce8976de76750016053",
]


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


def state(executable: Path) -> dict[str, str]:
    value = {
        "nvx_revision": git("rev-parse", "HEAD"),
        "core_revision": git("-C", "openvmm", "rev-parse", "HEAD"),
        "nvx_status": git("status", "--porcelain", "--untracked-files=all"),
        "core_status": git("-C", "openvmm", "status", "--porcelain"),
        "core_executable_sha256": digest(executable),
    }
    metadata = json.loads(Path("build/openvmm.provenance.json").read_bytes())
    if (
        value["nvx_status"]
        or value["core_status"]
        or value["core_revision"] != CORE
        or git("ls-tree", "HEAD", "openvmm").split()[2] != CORE
        or metadata["source_revision"] != CORE
        or metadata["source_clean"] is not True
        or metadata["executable_sha256"] != value["core_executable_sha256"]
    ):
        raise RuntimeError("history requires clean source and its exact pinned binary")
    return value


def main() -> None:
    # Load only the already verified owned-payload cleanup before switching
    # sources. Every runtime measurement runs the selected checkout in a fresh
    # subprocess; this utility runs only after that measurement has completed.
    sys.path.insert(0, str(Path("scripts").resolve()))
    from nvx_tools.mcp import _remove_snapshot

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("linux-kvm", "windows-whp"), required=True)
    parser.add_argument("--full-proof", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()
    if args.diagnostic:
        # Diagnostics retain causal worker stacks without editing the selected
        # source. Their instrumented timings are never collected as history.
        hook = Path("build/pool-refill-hook").resolve()
        hook.mkdir()
        (hook / "sitecustomize.py").write_text(
            "import traceback\n"
            "from nvx_tools import pool,warm\n"
            "def trace(function):\n"
            " def call(*args,**kwargs):\n"
            "  try:return function(*args,**kwargs)\n"
            "  except Exception:\n"
            "   traceback.print_exc()\n"
            "   raise\n"
            " return call\n"
            "warm.clone=trace(warm.clone)\n"
            "pool._close=trace(pool._close)\n"
        )
        os.environ["PYTHONPATH"] = os.pathsep.join(
            [str(hook), str(Path("scripts").resolve()), os.environ.get("PYTHONPATH", "")]
        )
        os.environ["OPENVMM_STARTUP_PROFILE"] = "1"
    backend = "kvm" if args.platform == "linux-kvm" else "whp"
    executable = Path("openvmm/target/release") / (
        "openvmm.exe" if backend == "whp" else "openvmm"
    )
    head = git("rev-parse", "HEAD")
    state(executable)
    if args.full_proof:
        subprocess.run(
            [sys.executable, "scripts/release-proof.py", "--platform", args.platform,
             "--output-dir", "build/measured-history-full-proof"],
            check=True,
        )
        return
    revisions = REVISIONS[:1] if args.diagnostic else [*REVISIONS, head]
    runs = 100 if args.diagnostic else 20
    if not args.diagnostic and len(set(revisions)) != 10:
        raise RuntimeError("history needs ten distinct existing source revisions")
    out = Path("build/pool-refill-diagnostic" if args.diagnostic else "build/measured-history").resolve()
    out.mkdir()
    # The first exact-core Windows diagnostic is on its own existing branch.
    # Preserve its real revision rather than relabeling it as a main-branch run.
    subprocess.run(["git", "fetch", "https://github.com/maceip/nvx.git", REVISIONS[0]], check=True)
    for revision in revisions:
        subprocess.run(["git", "cat-file", "-e", revision + "^{commit}"], check=True)
        subprocess.run(["git", "checkout", "--detach", revision], check=True)
        before = state(executable)
        trial = out / revision[:12]
        trial.mkdir()
        (trial / "source-before.json").write_text(json.dumps(before, indent=2) + "\n")
        template = trial / "template"
        commands = [
            ("doctor", ["doctor", "--backend", backend, "--json"]),
            ("warm", ["warm", "--image", "python:3.12-slim", "--backend", backend,
                      "--output", str(template)]),
            ("benchmark", ["benchmark", "--suite", "warm-pool", "--backend", backend,
                           "--template", str(template), "--runs", str(runs), "--timeout", "120",
                           "--platform", args.platform, "--output", str(trial / "benchmark.json")]),
            ("collect", ["performance", "collect-warm", "--platform", args.platform,
                         "--commit", revision, "--input", str(trial / "benchmark.json"),
                         "--output-dir", str(trial / "performance")]),
        ]
        if args.diagnostic:
            commands = commands[:-1]
        steps = []
        for name, arguments in commands:
            print("Measured history", revision, name, flush=True)
            start = time.monotonic()
            log = trial / (name + ".log")
            with log.open("wb") as output:
                result = subprocess.run([sys.executable, "scripts/nvx.py", *arguments],
                                        stdout=output, stderr=subprocess.STDOUT)
            steps.append({"name": name, "returncode": result.returncode,
                          "duration_seconds": time.monotonic() - start,
                          "log_sha256": digest(log)})
            (trial / "progress.json").write_text(json.dumps(steps, indent=2) + "\n")
            if result.returncode:
                pools = Path(os.environ["NVX_IMAGE_CACHE"]) / "pools"
                errors = []
                for error in sorted(pools.rglob("refill-error-*.txt")):
                    if error.is_file() and not error.is_symlink():
                        detail = error.read_text(errors="replace")[-4096:]
                        errors.append({"path": str(error.relative_to(pools)), "detail": detail})
                        print("Retained refill failure:", detail, flush=True)
                failure = {"before": before, "after": state(executable), "failed_step": name,
                           "pool_refill_errors": errors}
                (trial / "failure-evidence.json").write_text(json.dumps(failure, indent=2) + "\n")
                raise RuntimeError(f"{revision} {name} failed; original payload retained at {trial}")
        after = state(executable)
        if before != after:
            raise RuntimeError("source or executable changed during measurement")
        benchmark = json.loads((trial / "benchmark.json").read_bytes())
        if benchmark["runs"] != runs or benchmark["pool_size"] != 2:
            raise RuntimeError("unexpected canonical benchmark configuration")
        for metric in benchmark["metrics"].values():
            if len(metric["samples_ms"]) != runs or abs(
                statistics.median(metric["samples_ms"]) - metric["p50_ms"]
            ) > 1e-7:
                raise RuntimeError("benchmark sample or median mismatch")
        payloads = [{"path": str(p.relative_to(template)), "sha256": digest(p),
                     "size_bytes": p.stat().st_size}
                    for p in template.rglob("*") if p.is_file() and not p.is_symlink()]
        receipt = {"before": before, "after": after, "steps": steps,
                   "benchmark_sha256": digest(trial / "benchmark.json"),
                   "acceptance_scope": "diagnostic only; never performance history" if args.diagnostic else "twenty cold and twenty warm canonical requests; not full runtime acceptance",
                   "completed_template_payloads": payloads,
                   "payload_retirement": "completed templates removed after hashing; any failed template is retained"}
        (trial / "source-provenance.json").write_text(json.dumps(receipt, indent=2) + "\n")
        _remove_snapshot(template)
        print("Completed source-bound history", revision, flush=True)
    subprocess.run(["git", "checkout", "--detach", head], check=True)
    state(executable)


if __name__ == "__main__":
    main()
