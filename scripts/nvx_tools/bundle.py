"""Deterministic, bounded evidence bundles; never include control capabilities or RAM."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import tarfile
import tempfile
from pathlib import Path
from typing import Any, cast

from .common import ScriptError
from .image import canonical
from .receipt import verify as verify_receipt

FILES = (
    "image.json",
    "policy.json",
    "argv.json",
    "config.json",
    "events.jsonl",
    "flows.jsonl",
    "resources.json",
    "outcome.json",
    "receipt.json",
    "openvmm.log",
)
MAX_BYTES = 256 << 20
OPTIONAL_FILES = ("proxy.jsonl", "versions.json")


def create(state: Path, destination: Path, *, public_key: Path | None = None) -> None:
    if (state / "runtime.json").exists():
        raise ScriptError("stop the instance before bundling evidence")
    verify_receipt(state / "receipt.json", public_key=public_key)
    if destination.exists() or destination.is_symlink():
        raise ScriptError("bundle destination already exists")
    content: dict[str, bytes] = {}
    total = 0
    names = (*FILES, *(name for name in OPTIONAL_FILES if (state / name).exists()))
    for name in names:
        path = state / name
        if path.is_symlink() or not path.is_file():
            raise ScriptError(f"missing plain bundle evidence: {name}")
        total += path.stat().st_size
        if total > MAX_BYTES:
            raise ScriptError("bundle exceeds the 256 MiB limit")
        content[name] = path.read_bytes()
    content["bundle.json"] = (
        canonical(
            {
                "bundle_version": 1,
                "files": {
                    name: hashlib.sha256(raw).hexdigest()
                    for name, raw in content.items()
                },
            }
        )
        + b"\n"
    )
    with tempfile.NamedTemporaryFile(
        prefix=".nvx-bundle-", dir=destination.absolute().parent, delete=False
    ) as output:
        stage = Path(output.name)
        try:
            with (
                gzip.GzipFile(
                    fileobj=output, mode="wb", mtime=0, filename=""
                ) as compressed,
                tarfile.open(
                    fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
                ) as archive,
            ):
                for name in sorted(content):
                    raw = content[name]
                    member = tarfile.TarInfo(name)
                    member.size = len(raw)
                    member.mtime = 0
                    member.mode = 0o600
                    archive.addfile(member, io.BytesIO(raw))
            output.flush()
            os.fsync(output.fileno())
            try:
                os.link(stage, destination)
            except FileExistsError as error:
                raise ScriptError("bundle destination already exists") from error
        finally:
            stage.unlink(missing_ok=True)


def verify(path: Path, *, public_key: Path | None = None) -> dict[str, Any]:
    content: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(path, "r|gz") as archive:
            for member in archive:
                if (
                    member.name not in (*FILES, *OPTIONAL_FILES, "bundle.json")
                    or member.name in content
                    or not member.isfile()
                ):
                    raise ScriptError(
                        "bundle contains duplicate, unsafe, or unexpected member"
                    )
                total += member.size
                if member.size < 0 or total > MAX_BYTES:
                    raise ScriptError("bundle exceeds size limit")
                source = archive.extractfile(member)
                if source is None:
                    raise ScriptError("bundle member has no data")
                with source:
                    content[member.name] = source.read(member.size + 1)
                if len(content[member.name]) != member.size:
                    raise ScriptError("incomplete bundle member")
        if not set((*FILES, "bundle.json")).issubset(content):
            raise ScriptError("bundle is missing required evidence")
        manifest = cast(dict[str, Any], json.loads(content.pop("bundle.json")))
        expected = {
            "bundle_version": 1,
            "files": {
                name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()
            },
        }
        if manifest != expected:
            raise ScriptError("bundle content digest mismatch")
        with tempfile.TemporaryDirectory(prefix="nvx-bundle-verify-") as directory:
            root = Path(directory)
            for name, raw in content.items():
                (root / name).write_bytes(raw)
            document = verify_receipt(root / "receipt.json", public_key=public_key)
        return {
            "bundle_version": 1,
            "instance_id": document["body"]["instance_id"],
            "verified": True,
        }
    except (OSError, tarfile.TarError, ValueError, KeyError) as error:
        raise ScriptError("invalid or corrupted bundle") from error


def command(args: argparse.Namespace) -> None:
    if args.bundle_target == "verify":
        if args.path is None:
            raise ScriptError("bundle verify requires an archive path")
        print(json.dumps(verify(args.path, public_key=args.public_key), sort_keys=True))
        return
    from .registry import resolve

    if args.output is None:
        raise ScriptError("bundle ID requires --output FILE.tar.gz")
    create(resolve(args.bundle_target), args.output, public_key=args.public_key)
    print(str(args.output))


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("bundle_target", help="instance ID or verify")
    parser.add_argument("path", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.set_defaults(handler=command)
