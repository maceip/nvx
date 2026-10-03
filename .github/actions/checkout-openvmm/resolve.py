"""Resolve the exact gitlink and repository from the checked-out NVX tree."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from nvx_tools.ci import github_submodule_repository


def git(*arguments: str) -> str:
    return subprocess.check_output(["git", *arguments], text=True).strip()


sha = git("rev-parse", ":openvmm")
if re.fullmatch(r"[0-9a-f]{40}", sha) is None:
    raise SystemExit("OpenVMM gitlink must pin a full commit SHA")
repository = github_submodule_repository(
    git("config", "-f", ".gitmodules", "--get", "submodule.openvmm.url")
)
with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
    output.write(f"sha={sha}\nrepository={repository}\n")
