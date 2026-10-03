"""Workspace admission and bounded clean-run output collection."""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
from pathlib import Path

from .common import ScriptError
from .sandbox import SandboxMount

MAX_OUTPUT_BYTES = 256 << 20
MAX_OUTPUT_FILES = 10_000


def unpack_outputs(archive: Path, destination: Path) -> None:
    """Extract only bounded regular files and directories into a private stage."""
    total = 0
    count = 0
    seen: set[str] = set()
    with tarfile.open(archive, mode="r|") as incoming:
        for member in incoming:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or "\\" in member.name:
                raise ScriptError("output archive contains an unsafe path")
            relative = str(path)
            if relative in seen or not (member.isdir() or member.isfile()):
                raise ScriptError(
                    "output archive contains duplicate or special entries"
                )
            seen.add(relative)
            count += 1
            total += member.size
            if count > MAX_OUTPUT_FILES or not 0 <= total <= MAX_OUTPUT_BYTES:
                raise ScriptError("output archive exceeds collection limits")
            target = destination / path
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                source = incoming.extractfile(member)
                if source is None:
                    raise ScriptError("output archive member has no data")
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
                os.chmod(target, 0o600)


def parse_workspace(value: str) -> SandboxMount:
    fields = value.rsplit(":", 2)
    if len(fields) == 3 and fields[-1] in ("ro", "rw"):
        host, guest, access = fields
    else:
        fields = value.rsplit(":", 1)
        if len(fields) != 2:
            raise ScriptError("--workspace must be HOST:GUEST[:ro|rw]")
        host, guest = fields
        access = "ro"
    if not host:
        raise ScriptError("workspace host path is empty")
    for reserved in ("/etc", "/.nvx-agent"):
        if guest == reserved or guest.startswith(reserved + "/"):
            raise ScriptError("workspace overlaps reserved " + reserved)
    return SandboxMount(guest, Path(host), access).validated()


def output_destination(path: Path) -> Path:
    if path.is_symlink() or path.exists():
        raise ScriptError(
            "--out requires a new directory; existing output is never overwritten"
        )
    parent = path.absolute().parent
    if not parent.is_dir() or parent.is_symlink():
        raise ScriptError("output parent must be an existing plain directory")
    return parent.resolve() / path.name


def collect_outputs(source: Path, destination: Path, *, clean: bool) -> None:
    target = output_destination(destination)
    if not clean:
        raise ScriptError(
            "outputs are collected only after clean workload completion and VM teardown"
        )
    files: list[tuple[Path, Path]] = []
    size = 0
    for directory, dirs, names in os.walk(source, followlinks=False):
        root = Path(directory)
        for name in (*dirs, *names):
            file = root / name
            mode = file.lstat().st_mode
            if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise ScriptError("output tree contains a symlink or special file")
            if stat.S_ISREG(mode):
                size += file.stat().st_size
                files.append((file, file.relative_to(source)))
                if size > MAX_OUTPUT_BYTES or len(files) > MAX_OUTPUT_FILES:
                    raise ScriptError(
                        "output tree exceeds the 256 MiB / 10000-file limit"
                    )
    # Stage beside the destination so partial outputs never look complete.
    import tempfile

    with tempfile.TemporaryDirectory(
        prefix=".nvx-output-", dir=target.parent
    ) as temporary:
        stage = Path(temporary) / "artifacts"
        stage.mkdir(mode=0o700)
        for file, relative in files:
            out = stage / relative
            out.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            with (
                os.fdopen(os.open(file, flags), "rb") as incoming,
                out.open("xb") as outgoing,
            ):
                if not stat.S_ISREG(os.fstat(incoming.fileno()).st_mode):
                    raise ScriptError("output file changed type during collection")
                shutil.copyfileobj(incoming, outgoing)
            os.chmod(out, 0o600)
        # The public destination was never present. Reserve it before moving
        # files so a concurrently created directory cannot be overwritten.
        target.mkdir(mode=0o700)
        try:
            for file in stage.iterdir():
                file.rename(target / file.name)
        except BaseException:
            shutil.rmtree(target)
            raise
