"""Client request to first workload stdout, with cold and completion metrics."""

from __future__ import annotations

import argparse
import json
import os
import platform as host_platform
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import IO, Any

from . import pool, warm
from .build_constants import BuildConstants
from .common import ScriptError
from .performance import (
    BenchmarkDimensions,
    PerformanceError,
    Result,
    result_filename,
    write_results,
)

MARKER = b"NVX-WARM-WORKLOAD-OK"
WORKLOAD = ("python", "-c", "import os;os.write(1,b'NVX-WARM-WORKLOAD-OK')")


def collect(platform: str, commit: str, source: Path, output: Path) -> Path:
    document = json.loads(source.read_bytes())
    if document.get("benchmark_version") != 1 or document.get("platform") != platform:
        raise PerformanceError("warm benchmark version or platform mismatch")
    results: list[Result] = []
    for metric in (
        "warm_pool_first_stdout",
        "warm_pool_completion",
        "cold_image_first_stdout",
    ):
        item = document["metrics"][metric]
        samples = item["samples_ms"]
        if len(samples) < 2 or any(
            type(value) not in (int, float) or not 0 < value < float("inf")
            for value in samples
        ):
            raise PerformanceError("invalid warm benchmark samples")
        if item["p50_ms"] != statistics.median(samples):
            raise PerformanceError("warm benchmark p50 does not match samples")
        results.append(
            Result(commit, metric, "ms", "lower", item["p50_ms"], platform, 2, 1)
        )
    path = output / result_filename(BenchmarkDimensions(platform, 2, 1))
    write_results(path, results)
    return path


def run(args: argparse.Namespace) -> int:
    if args.template is None or args.runs < 2 or args.backend == "both":
        raise ScriptError(
            "warm-pool benchmark requires --template, one backend, and at least two runs"
        )
    metadata = warm.admit(args.template, args.backend)
    if metadata.get("runtime") != "python":
        raise ScriptError("warm-pool benchmark requires a retained Python runtime")
    platform = args.platform or (
        (
            "darwin-arm64-hvf"
            if host_platform.machine().lower() in ("aarch64", "arm64")
            else "darwin-x86_64-hvf"
        )
        if sys.platform == "darwin"
        else "linux-arm64-kvm"
        if host_platform.machine().lower() in ("aarch64", "arm64")
        else "windows-whp"
        if os.name == "nt"
        else "linux-kvm"
    )
    metrics: dict[str, list[float]] = {
        name: []
        for name in (
            "warm_pool_first_stdout",
            "warm_pool_completion",
            "cold_image_first_stdout",
        )
    }
    for _ in range(args.runs):
        started = time.perf_counter_ns()
        process = subprocess.Popen(
            [
                sys.executable,
                str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
                "run",
                "--image",
                metadata["image"],
                "--hypervisor",
                args.backend,
                "--profile",
                "default",
                "--memory-mib",
                str(metadata["config"]["memory_mib"]),
                "--stream",
                "--",
                *WORKLOAD,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        errors: list[bytes] = []

        def drain(
            pipe: IO[bytes] = process.stderr, target: list[bytes] = errors
        ) -> None:
            target.append(pipe.read())

        reader = threading.Thread(target=drain)
        reader.start()
        first = process.stdout.read(1)
        elapsed = (time.perf_counter_ns() - started) / 1e6
        remaining = process.stdout.read()
        reader.join()
        if process.wait() or first + remaining != MARKER:
            raise ScriptError("cold workload failed; inspect managed instance evidence")
        metrics["cold_image_first_stdout"].append(elapsed)
    identifier = pool.start(args.template, args.pool_size)
    identities: set[str] = set()
    try:
        for _ in range(args.runs):
            deadline = time.monotonic() + args.timeout
            while pool.request(identifier, "status")["ready"] == 0:
                if time.monotonic() >= deadline:
                    raise ScriptError("pool refill readiness timed out")
                time.sleep(0.01)
            started = time.perf_counter_ns()
            first_output: list[float] = []
            received = bytearray()

            def output(
                stream: str,
                data: bytes,
                first: list[float] = first_output,
                begin: int = started,
                buffer: bytearray = received,
            ) -> None:
                if stream == "stdout":
                    if not first:
                        first.append((time.perf_counter_ns() - begin) / 1e6)
                    buffer.extend(data)

            result = pool.request(identifier, "run", WORKLOAD, output=output)
            elapsed = (time.perf_counter_ns() - started) / 1e6
            if (
                result["returncode"]
                or bytes(received) != MARKER
                or result["id"] in identities
                or not first_output
            ):
                raise ScriptError(
                    "pool workload output, streaming, or single-use lease failed"
                )
            identities.add(result["id"])
            metrics["warm_pool_first_stdout"].append(first_output[0])
            metrics["warm_pool_completion"].append(elapsed)
    finally:
        pool.request(identifier, "stop")
    document: dict[str, Any] = {
        "benchmark_version": 1,
        "platform": platform,
        "backend": args.backend,
        "runtime": metadata["runtime"],
        "memory_mib": metadata["config"]["memory_mib"],
        "runs": args.runs,
        "pool_size": args.pool_size,
        "timing": "client request to first workload stdout; completion includes metrics; preparation and refill excluded",
        "metrics": {
            key: {"samples_ms": samples, "p50_ms": statistics.median(samples)}
            for key, samples in metrics.items()
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(
        json.dumps(
            {key: item["p50_ms"] for key, item in document["metrics"].items()},
            sort_keys=True,
        )
    )
    return 0
