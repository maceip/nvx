"""Measure the existing Windows polling reader against a pipe-read lower bound.

This is a synthetic transport diagnostic, not guest acceptance or trend history.
The blocking reference deliberately supplies no production timeout implementation.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import queue
import secrets
import statistics
import subprocess
import sys
import threading
import time
from multiprocessing.connection import Listener
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

SOURCE_ROOT = Path(
    os.environ.get("NVX_PROBE_SOURCE_ROOT", str(Path(__file__).resolve().parents[2]))
).resolve()
sys.path.insert(0, str(SOURCE_ROOT / "scripts"))
from nvx_tools import control_session  # noqa: E402


def trial(method: str, count: int) -> dict[str, Any]:
    endpoint = r"\\.\pipe\nvx-transport-probe-" + secrets.token_hex(16)
    listener = Listener(endpoint, family="AF_PIPE")
    requests: queue.Queue[bool] = queue.Queue()
    failures: list[str] = []

    def respond() -> None:
        try:
            with listener.accept() as connection:
                while requests.get():
                    connection.send_bytes(b"R")
        except BaseException as error:
            failures.append(repr(error))

    peer = threading.Thread(target=respond, daemon=True)
    peer.start()
    reader = (
        cast(Any, control_session)._NamedPipeStream.connect(Path(endpoint), 5)
        if method == "production_reader"
        else None
    )
    read_fd = os.open(endpoint, os.O_RDWR | os.O_BINARY) if reader is None else None
    real_time = control_session.time
    sleeps = 0
    slept_ms = 0.0

    def measured_sleep(seconds: float) -> None:
        nonlocal sleeps, slept_ms
        started = time.perf_counter_ns()
        real_time.sleep(seconds)
        sleeps += 1
        slept_ms += (time.perf_counter_ns() - started) / 1e6

    cast(Any, control_session).time = SimpleNamespace(
        monotonic=real_time.monotonic, sleep=measured_sleep
    )
    samples = []
    try:
        for _ in range(count):
            sleeps = 0
            slept_ms = 0.0
            started = time.perf_counter_ns()
            requests.put(True)
            response = (
                reader.read_exact(1, time.monotonic() + 2)
                if reader is not None
                else os.read(cast(int, read_fd), 1)
            )
            elapsed_ms = (time.perf_counter_ns() - started) / 1e6
            if response != b"R":
                raise RuntimeError(f"invalid response: {response!r}; {failures}")
            samples.append(
                {
                    "roundtrip_ms": elapsed_ms,
                    "poll_sleeps": sleeps,
                    "actual_sleep_ms": slept_ms,
                }
            )
    finally:
        cast(Any, control_session).time = real_time
        requests.put(False)
        peer.join(5)
        if reader is not None:
            reader.close()
        else:
            os.close(cast(int, read_fd))
        listener.close()
    if peer.is_alive() or failures:
        raise RuntimeError(f"diagnostic peer failed: {failures}")
    return {
        "method": method,
        "samples": samples,
        "p50_roundtrip_ms": statistics.median(s["roundtrip_ms"] for s in samples),
        "p50_actual_sleep_ms": statistics.median(s["actual_sleep_ms"] for s in samples),
        "total_poll_sleeps": sum(s["poll_sleeps"] for s in samples),
    }


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("this diagnostic requires native Windows")
    source = Path(cast(str, control_session.__file__))
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    order = (
        "production_reader",
        "blocking_reference",
        "blocking_reference",
        "production_reader",
    )
    trials = [trial(method, 100) for method in order]
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
        raise RuntimeError("transport source changed during measurement")
    result = {
        "scope": "synthetic pipe transport diagnostic; no guest or acceptance claim",
        "python": sys.version,
        "host": platform.platform(),
        "nvx_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=SOURCE_ROOT, text=True
        ).strip(),
        "transport_source_sha256": source_sha,
        "order": order,
        "trials": trials,
    }
    output = Path("build/whp-control-transport/result.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n")
    for item in trials:
        print({k: v for k, v in item.items() if k != "samples"}, flush=True)
    if "--require-prompt-read" in sys.argv:
        reference = statistics.median(
            t["p50_roundtrip_ms"] for t in trials if t["method"] == "blocking_reference"
        )
        reader = statistics.median(
            t["p50_roundtrip_ms"] for t in trials if t["method"] == "production_reader"
        )
        if reader - reference > 5:
            raise RuntimeError(
                "control reader adds more than 5 ms to an immediate response"
            )


if __name__ == "__main__":
    main()
