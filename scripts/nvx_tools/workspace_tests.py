"""Real VM proof for declared workspace access, copy, output and evidence bundles."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from .build_constants import BuildConstants
from .common import ScriptError


def run(backend: str, output: Path, timeout: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cli = [sys.executable, str(BuildConstants.REPO_ROOT / "scripts/nvx.py")]

    def invoke(
        label: str, *arguments: str, status: int = 0
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            [*cli, *arguments], capture_output=True, timeout=timeout * 3
        )
        (output / (label + ".log")).write_bytes(result.stdout + result.stderr)
        if result.returncode != status:
            raise ScriptError(
                f"workspace scenario {label} returned {result.returncode}, expected {status}"
            )
        return result

    workspace = output / "workspace"
    workspace.mkdir(mode=0o777)
    workspace.chmod(0o777)
    (workspace / "input").write_bytes(b"nvx-workspace-input\n")
    (workspace / "escape").symlink_to(output.absolute() / "outside-secret")
    (output / "outside-secret").write_bytes(b"must-not-be-readable")
    started = invoke(
        "rw-start",
        "run",
        "--hypervisor",
        backend,
        "--image",
        "alpine:3.20",
        "--workspace",
        str(workspace.absolute()) + ":/src:rw",
        "--keep-alive",
        "--",
        "/bin/sh",
        "-c",
        "set -eu; cat /src/input; echo roundtrip >/src/roundtrip; test ! -r /src/escape; echo NVX-WORKSPACE-RW-OK",
    )
    match = re.search(rb"NVX-ID: ([0-9a-f]{32})", started.stderr)
    if match is None:
        raise ScriptError("workspace run did not return an ID")
    identifier = match[1].decode()
    try:
        if (workspace / "roundtrip").read_bytes() != b"roundtrip\n":
            raise ScriptError("rw guest write did not reach the host")
        executed = invoke(
            "exec-37",
            "exec",
            identifier,
            "--",
            "/bin/sh",
            "-c",
            "printf output; printf error >&2; exit 37",
            status=37,
        )
        if executed.stdout != b"output" or executed.stderr != b"error":
            raise ScriptError("exec did not preserve independent output streams")
        original = output / "random.bin"
        original.write_bytes(os.urandom(1 << 20))
        invoke("upload", "cp", str(original), identifier + ":/tmp/copy.bin")
        recovered = output / "recovered.bin"
        invoke("download", "cp", identifier + ":/tmp/copy.bin", str(recovered))
        if original.read_bytes() != recovered.read_bytes():
            raise ScriptError("1 MiB guest copy changed bytes")
        events = invoke("logs", "logs", identifier, "--json")
        sequence = [json.loads(line)["sequence"] for line in events.stdout.splitlines()]
        if sequence != list(range(1, len(sequence) + 1)):
            raise ScriptError("logs are not ordered")
    finally:
        invoke("stop", "stop", identifier)
    first = output / "first.tar.gz"
    second = output / "second.tar.gz"
    invoke("bundle-first", "bundle", identifier, "--output", str(first))
    invoke("bundle-second", "bundle", identifier, "--output", str(second))
    if first.read_bytes() != second.read_bytes():
        raise ScriptError("evidence bundles are not byte deterministic")
    invoke("bundle-verify", "bundle", "verify", str(first))
    invoke(
        "ro-control",
        "run",
        "--hypervisor",
        backend,
        "--image",
        "alpine:3.20",
        "--workspace",
        str(workspace.absolute()) + ":/src:ro",
        "--",
        "/bin/sh",
        "-c",
        "if echo unexpected >/src/read-only; then exit 1; fi; test ! -r /src/escape; echo NVX-WORKSPACE-RO-OK",
    )
    if (workspace / "read-only").exists():
        raise ScriptError("read-only workspace permitted a host write")
    artifacts = output / "artifacts"
    invoke(
        "out-clean",
        "run",
        "--hypervisor",
        backend,
        "--image",
        "alpine:3.20",
        "--out",
        str(artifacts),
        "--",
        "/bin/sh",
        "-c",
        "mkdir /out/nested; printf artifact >/out/nested/result",
    )
    if (artifacts / "nested/result").read_bytes() != b"artifact":
        raise ScriptError("clean output collection lost artifact bytes")
    failed = output / "failed-artifacts"
    invoke(
        "out-failed",
        "run",
        "--hypervisor",
        backend,
        "--image",
        "alpine:3.20",
        "--out",
        str(failed),
        "--",
        "/bin/sh",
        "-c",
        "printf withheld >/out/result; exit 37",
        status=37,
    )
    if failed.exists():
        raise ScriptError("failed workload published outputs")
    print("NVX-WORKSPACE-LIFECYCLE-OK")
