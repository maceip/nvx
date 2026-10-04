"""End-to-end OCI gate, independent of the low-level guest fixture builders."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from .build_constants import BuildConstants
from .common import ScriptError
from .image import ImageCache


def run(backend: str, output: Path, timeout: float) -> None:
    command = [sys.executable, str(BuildConstants.REPO_ROOT / "scripts/nvx.py")]
    output.mkdir(parents=True, exist_ok=True)
    for ref, argv in [
        ("alpine:3.20", ["/sbin/nvx-sandbox-smoke"]),
        ("python:3.12-slim", ["python", "-c", "print(1)"]),
    ]:
        label = ref.replace(":", "-")
        with (output / f"{label}-convert.log").open("wb") as log:
            subprocess.run(
                [*command, "image", "pull", ref],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        cache = ImageCache()
        value, expected = cache.resolve(ref)
        # Reconvert through the actual Linux converter, not canonical JSON alone.
        with (output / f"{label}-determinism.log").open("wb") as log:
            subprocess.run(
                [*command, "image", "convert", ref],
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        if cache.resolve(ref)[0] != value:
            raise ScriptError(f"non-deterministic conversion: {ref}")
        result = subprocess.run(
            [*command, "run", "--hypervisor", backend, "--image", ref, "--", *argv],
            capture_output=True,
            timeout=timeout * 3,
        )
        (output / f"{label}-run.log").write_bytes(result.stdout + result.stderr)
        if result.returncode or (
            b"NVX-IMAGE-RUN-OK uid=65534 gid=65534" not in result.stdout
            if ref.startswith("alpine")
            else result.stdout != b"1\n"
        ):
            raise ScriptError(f"image workload failed: {ref}; see {label}-run.log")
        match = re.search(rb"^NVX-ID: ([0-9a-f]{32})\r?$", result.stderr, re.M)
        if match is None:
            raise ScriptError("image run did not publish an instance ID")
        image = cast(
            dict[str, Any],
            json.loads(
                (
                    cache.root / "instances" / match[1].decode() / "image.json"
                ).read_bytes()
            ),
        )
        if (
            image["manifest_digest"] != value
            or image["manifest"]["layers"] != expected["layers"]
        ):
            raise ScriptError("run image identity differs from converter output")
    print("NVX-IMAGE-RUN-OK")
