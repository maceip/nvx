#!/usr/bin/env python3
"""Install a published NVX runtime after verifying its signed provenance.

This bootstrap uses only Python's standard library and the GitHub CLI. It never
loads code from the downloaded archive before signature verification succeeds.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path, PurePosixPath


def download_public(url: str, destination: Path, limit: int) -> None:
    request = urllib.request.Request(
        url, headers={"User-Agent": "nvx-public-installer"}
    )
    with (
        urllib.request.urlopen(request, timeout=60) as source,
        destination.open("xb") as output,
    ):
        size = 0
        while chunk := source.read(1 << 20):
            size += len(chunk)
            if size > limit:
                raise ValueError("release download exceeds its size bound")
            output.write(chunk)


def extract_bootstrap(archive: Path, destination: Path) -> Path:
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        if len(members) > 50000 or sum(row.size for row in members) > 8 << 30:
            raise ValueError("release exceeds the installation size bound")
        roots: set[str] = set()
        names: set[str] = set()
        for row in members:
            path = PurePosixPath(row.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not path.parts
                or "\\" in row.name
                or row.name in names
                or not (row.isdir() or row.isfile())
                or row.size < 0
            ):
                raise ValueError(
                    "release contains an unsafe or duplicate archive member"
                )
            roots.add(path.parts[0])
            names.add(row.name)
        if len(roots) != 1:
            raise ValueError("release must have one package root")
        for row in members:
            target = destination / row.name
            if row.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                contents = source.extractfile(row)
                if contents is None:
                    raise ValueError("release member has no content")
                with contents, target.open("xb") as output:
                    shutil.copyfileobj(contents, output)
                target.chmod(0o755 if row.mode & 0o111 else 0o644)
    return destination / roots.pop()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="maceip/nvx")
    parser.add_argument(
        "--version", help="version to install; default is latest stable"
    )
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository) is None:
        parser.error("repository must be OWNER/REPOSITORY")
    if not shutil.which("gh"):
        parser.error(
            "GitHub CLI is required to verify signed releases; install gh first"
        )
    tag = (
        "v" + args.version.removeprefix("v")
        if args.version
        else latest_tag(args.repository)
    )
    if re.fullmatch(r"v[0-9][A-Za-z0-9._-]*", tag) is None:
        parser.error("release tag must be vVERSION")
    machine = {"arm64": "aarch64", "amd64": "x86_64"}.get(
        platform.machine().lower(), platform.machine().lower()
    )
    selected = {
        ("Darwin", "aarch64"): "darwin-arm64",
        ("Darwin", "x86_64"): "darwin-x86_64",
        ("Linux", "aarch64"): "linux-arm64",
        ("Linux", "x86_64"): "linux-kvm",
    }.get((platform.system(), machine))
    if selected is None:
        parser.error("no published runtime supports this host OS/architecture")
    destination = (
        args.destination or Path.home() / ".local/share/nvx" / tag
    ).absolute()
    if destination.exists() or destination.is_symlink():
        parser.error("installation destination already exists")
    with tempfile.TemporaryDirectory(prefix="nvx-install-") as temporary:
        work = Path(temporary)
        name = f"nvx-{tag[1:]}-{selected}.tar.gz"
        archive = work / name
        bundle = work / (name + ".sigstore.jsonl")
        base = f"https://github.com/{args.repository}/releases/download/{tag}"
        download_public(f"{base}/{name}", archive, 2 << 30)
        download_public(f"{base}/{bundle.name}", bundle, 16 << 20)
        subprocess.run(
            [
                "gh",
                "attestation",
                "verify",
                str(archive),
                "--bundle",
                str(bundle),
                "--repo",
                args.repository,
                "--signer-workflow",
                f"{args.repository}/.github/workflows/nvx-release.yml",
                "--source-ref",
                f"refs/tags/{tag}",
            ],
            check=True,
        )
        package = extract_bootstrap(archive, work / "package")
        metadata = json.loads((package / "NVX-RELEASE.json").read_bytes())
        if (
            metadata.get("platform") != selected
            or metadata.get("version") != tag[1:]
            or metadata.get("development") is not False
            or metadata.get("nvx_source_clean") is not True
        ):
            raise ValueError("archive is not the requested clean production release")
        # The authenticated CLI performs full inventory/provenance validation.
        subprocess.run(
            [
                sys.executable,
                str(package / "scripts/nvx.py"),
                "install",
                "--archive",
                str(archive),
                "--destination",
                str(destination),
            ],
            check=True,
        )
    print(f"Installed {tag}: {destination / 'bin/nvx'}")
    print(f"Add {destination / 'bin'} to PATH, then run: nvx doctor")


def latest_tag(repository: str) -> str:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={"User-Agent": "nvx-public-installer"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        document = json.loads(response.read(1 << 20))
    tag = document.get("tag_name")
    if not isinstance(tag, str):
        raise ValueError("public release query returned no version")
    return tag


if __name__ == "__main__":
    main()
