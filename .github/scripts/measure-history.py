"""Measure existing source revisions with the unchanged canonical warm-pool suite."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

CORE = "75b6560159c4ba903025ffd99dc3c95909002f49"
INTEL_CORE = "f1f6019b73a891b0cc9be381eba220750cfb9146"
PLATFORMS = {
    "linux-kvm": ("kvm", CORE),
    "windows-whp": ("whp", CORE),
    "darwin-x86_64": ("hvf", INTEL_CORE),
}
DIAGNOSTIC_REVISION = "0244ae555630f309929a46af666d5d11e3a08d54"
REVISIONS = [
    "2131a1ea94b2e29ad56e5c193181feef06e708dd",
    "4385058610c8138f336e919308c22b189307c769",
    "75c513f58d6f183fbea14fb8588360b7e6cf2a07",
    "598ca08b987c186fbde8a5757dd66dc379e70860",
    "b49531628e340b2c3e03766c6beeaae4cfec0ab4",
    "b977e3dbb2dd8cd1d0cf3a3bc1f91353f9916bee",
    "911764666548aa8982d04940932281a83631fcec",
    "5854598dd1cd238005b2e4cb61e1d74c1bc5d8a1",
    "8a0f6474a72414a10d9c0ce8976de76750016053",
]
INTEL_REVISIONS = [
    "6521f01bfc0e86641da3b221e823cbae24828cd2",
    "34e31a73ffbfd1405583f3d6f04dde6b978fe2cc",
    "7c7f7239c7abc046fd4ec352e4e794670b07dae9",
    "981b974b605b87221b6ad17574848f8f2d012718",
    "3d72943db7a1395bd1f2e2d0bd051cd628af9d62",
    "328145980d2293064793cf5cb7063101f5d692e0",
    "1878a06c1db1005cd10e8dbbc1670a01c4ca1bda",
    "e78e18170dc20994226e4c172670761bc7670a6f",
    "de301089694241d606c46f5003f87edca6284686",
    "9e3d52c1219d10e0482031594e260fd1f618fc54",
]


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


def state(executable: Path, core: str) -> dict[str, str]:
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
        or value["core_revision"] != core
        or git("ls-tree", "HEAD", "openvmm").split()[2] != core
        or metadata["source_revision"] != core
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
    parser.add_argument("--platform", choices=tuple(PLATFORMS), required=True)
    parser.add_argument("--full-proof", action="store_true")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--revision", help="Measure one existing ancestor revision")
    args = parser.parse_args()
    if args.revision and (args.full_proof or args.diagnostic):
        parser.error("a single revision cannot be combined with a different proof mode")
    if args.diagnostic and args.platform != "linux-kvm":
        parser.error("the pool-refill diagnostic requires Linux KVM")
    if args.platform == "darwin-x86_64" and (
        sys.platform != "darwin" or platform.machine() != "x86_64"
    ):
        raise RuntimeError("Intel history requires a native x86_64 macOS host")
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
    backend, core = PLATFORMS[args.platform]
    series = "darwin-x86_64-hvf" if backend == "hvf" else args.platform
    executable = Path("openvmm/target/release") / (
        "openvmm.exe" if backend == "whp" else "openvmm"
    )
    head = git("rev-parse", "HEAD")
    state(executable, core)
    if args.full_proof:
        subprocess.run(
            [sys.executable, "scripts/release-proof.py", "--platform", args.platform,
             "--output-dir", "build/measured-history-full-proof"],
            check=True,
        )
        return
    if args.revision:
        revision = git("rev-parse", "--verify", args.revision + "^{commit}")
        subprocess.run(["git", "merge-base", "--is-ancestor", revision, head], check=True)
        revisions = [revision]
    else:
        if args.diagnostic:
            revisions = [DIAGNOSTIC_REVISION]
        elif backend == "hvf":
            revisions = INTEL_REVISIONS
        else:
            revisions = [*REVISIONS, head]
    runs = 100 if args.diagnostic else 20
    if not args.diagnostic and not args.revision and len(set(revisions)) != 10:
        raise RuntimeError("history needs ten distinct existing source revisions")
    out = Path("build/pool-refill-diagnostic" if args.diagnostic else "build/measured-history").resolve()
    out.mkdir()
    if args.diagnostic:
        # This source is on a separate diagnostic branch and is never a baseline.
        subprocess.run(["git", "fetch", "https://github.com/maceip/nvx.git", DIAGNOSTIC_REVISION], check=True)
    # Reject incompatible sources before moving HEAD or collecting any samples.
    for revision in revisions:
        subprocess.run(["git", "cat-file", "-e", revision + "^{commit}"], check=True)
        if not args.diagnostic:
            subprocess.run(["git", "merge-base", "--is-ancestor", revision, head], check=True)
        if git("ls-tree", revision, "openvmm").split()[2] != core:
            raise RuntimeError(f"{revision}: history source pins another core")
    for revision in revisions:
        subprocess.run(["git", "checkout", "--detach", revision], check=True)
        before = state(executable, core)
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
                           "--platform", series, "--output", str(trial / "benchmark.json")]),
            ("collect", ["performance", "collect-warm", "--platform", series,
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
                failure = {"before": before, "after": state(executable, core), "failed_step": name,
                           "pool_refill_errors": errors}
                (trial / "failure-evidence.json").write_text(json.dumps(failure, indent=2) + "\n")
                raise RuntimeError(f"{revision} {name} failed; original payload retained at {trial}")
        after = state(executable, core)
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
    state(executable, core)


if __name__ == "__main__":
    main()
