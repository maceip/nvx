"""Authenticate release archives before extracting or executing their contents."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .common import ScriptError, require_tool

RELEASE_WORKFLOW = ".github/workflows/nvx-release.yml"


def verify_release_attestation(
    archive: Path, repository: str, tag: str, *, bundle: Path | None = None
) -> None:
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is None:
        raise ScriptError("release repository must be OWNER/REPOSITORY")
    if re.fullmatch(r"v[0-9][A-Za-z0-9._-]*", tag) is None:
        raise ScriptError("signed release tag must be vVERSION")
    require_tool("gh", "install GitHub CLI to verify the signed release provenance")
    command = [
        "gh",
        "attestation",
        "verify",
        str(archive),
        "--repo",
        repository,
        "--signer-workflow",
        f"{repository}/{RELEASE_WORKFLOW}",
        "--source-ref",
        f"refs/tags/{tag}",
    ]
    if bundle is not None:
        command.extend(["--bundle", str(bundle)])
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ScriptError(
            "release signature verification could not complete; nothing was installed"
        ) from error
    if result.returncode:
        raise ScriptError(
            "release signature or trusted workflow/tag verification failed; nothing was installed"
        )
