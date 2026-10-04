"""OCI/Docker conversion, immutable verified blobs, and private scratch leases.

Conversion runs off the launch path, in a networkless Linux tools container.
No command from the input image is executed. Cache manifests are canonical JSON.
"""

from __future__ import annotations

import argparse
import errno
import gzip
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, cast

from .build_constants import BuildConstants
from .common import ScriptError, require_tool, sha256_file
from .doctor import canonical_arch
from .locking import locked
from .sandbox import SandboxLayer

CONVERTER_VERSION = 1
MAX_MEMBERS = 250_000
MAX_FILE_BYTES = 1 << 30
MAX_IMAGE_BYTES = 32 << 30

# Extended attributes are Linux converter operations. Resolve at runtime so
# importing the offline cache API also works on macOS and Windows.
_setxattr = cast(Callable[..., None], getattr(os, "setxattr", None))
_getxattr = cast(Callable[..., bytes], getattr(os, "getxattr", None))
_listxattr = cast(Callable[..., list[str]], getattr(os, "listxattr", None))


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def validate_digest(value: str) -> str:
    if (
        not value.startswith("sha256:")
        or len(value) != 71
        or any(c not in "0123456789abcdef" for c in value[7:])
    ):
        raise ScriptError("expected a lowercase sha256 digest")
    return value


def split_prefix(
    layers: Sequence[str], bases: dict[str, list[str]]
) -> tuple[str | None, int]:
    """Only exact ordered digest prefixes match; tags and byte guesses never match."""
    matches = [
        (name, len(prefix))
        for name, prefix in bases.items()
        if prefix and list(layers[: len(prefix)]) == prefix
    ]
    return max(matches, key=lambda match: (match[1], match[0]), default=(None, 0))


def safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or "\\" in name or "\0" in name:
        raise ScriptError(f"unsafe image member path: {name!r}")
    return path


def guest_path(
    root: Path, relative: PurePosixPath, *, follow_final: bool = False
) -> Path:
    """Resolve symlinks in guest coordinates, never in the converter host namespace."""
    pending = list(relative.parts)
    parts: list[str] = []
    links = 0
    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise ScriptError("image symlink escapes its root")
            parts.pop()
            continue
        candidate = root.joinpath(*parts, part)
        if candidate.is_symlink() and (pending or follow_final):
            links += 1
            if links > 40:
                raise ScriptError("image symlink loop")
            target = PurePosixPath(os.readlink(candidate))
            if target.is_absolute():
                parts.clear()
            pending = (
                list(target.parts[1:] if target.is_absolute() else target.parts)
                + pending
            )
        else:
            parts.append(part)
    return root.joinpath(*parts)


def remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def validate_metadata(member: tarfile.TarInfo) -> None:
    for key in member.pax_headers:
        name = key.removeprefix("SCHILY.xattr.")
        if name.startswith(("trusted.overlay.", "user.overlay.")):
            raise ScriptError(f"forbidden overlay metadata: {name}")
        if key.startswith("SCHILY.xattr.") and not (
            name.startswith("user.") or name == "security.capability"
        ):
            raise ScriptError(f"unsupported image xattr: {name}")
    if (
        member.uid < 0
        or member.gid < 0
        or member.uid > 0xFFFFFFFF
        or member.gid > 0xFFFFFFFF
    ):
        raise ScriptError("invalid image ownership")
    if member.size < 0 or member.size > MAX_FILE_BYTES:
        raise ScriptError("image file exceeds the 1 GiB admission limit")
    if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
        raise ScriptError("image devices, sockets, and FIFOs are not admitted")


def apply_layer(root: Path, archive: Path, *, metadata: bool = True) -> None:
    """Whiteouts affect previous layers, independent of tar entry order."""
    with tarfile.open(archive, "r:*") as source:
        members = source.getmembers()
        if len(members) > MAX_MEMBERS or sum(m.size for m in members) > MAX_IMAGE_BYTES:
            raise ScriptError("image layer exceeds admission limits")
        names: set[str] = set()
        for member in members:
            relative = safe_name(member.name)
            normalized = str(relative)
            if normalized in names:
                raise ScriptError(f"duplicate image member: {normalized}")
            names.add(normalized)
            validate_metadata(member)
        for member in members:
            relative = safe_name(member.name)
            if relative.name == ".wh..wh..opq":
                parent = guest_path(root, relative.parent, follow_final=True)
                if parent.is_dir():
                    for child in parent.iterdir():
                        remove(child)
            elif relative.name.startswith(".wh."):
                if relative.name == ".wh.":
                    raise ScriptError("whiteout has no target")
                remove(guest_path(root, relative.with_name(relative.name[4:])))
        directories: list[tuple[Path, tarfile.TarInfo]] = []
        for member in members:
            relative = safe_name(member.name)
            if relative.name.startswith(".wh."):
                continue
            path = guest_path(root, relative, follow_final=member.isdir())
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                if path.exists() and not path.is_dir():
                    remove(path)
                path.mkdir(exist_ok=True)
                directories.append((path, member))
                continue
            remove(path)
            if member.isfile():
                content = source.extractfile(member)
                if content is None:
                    raise ScriptError("missing regular-file payload")
                with content, path.open("xb") as output:
                    shutil.copyfileobj(content, output)
            elif member.issym():
                if "\0" in member.linkname:
                    raise ScriptError("invalid symlink target")
                path.symlink_to(member.linkname)
            elif member.islnk():
                target = guest_path(root, safe_name(member.linkname), follow_final=True)
                if not target.is_file() or target.is_symlink():
                    raise ScriptError(
                        "hardlink target must be an admitted regular file"
                    )
                os.link(target, path)
            _metadata(path, member, metadata)
        for path, member in reversed(directories):
            _metadata(path, member, metadata)


def _chown(path: Path, uid: int, gid: int, *, follow_symlinks: bool = True) -> None:
    if os.name == "nt":
        raise ScriptError("OCI metadata application runs only in the Linux converter")
    os.chown(path, uid, gid, follow_symlinks=follow_symlinks)


def _mknod(path: Path, mode: int, device: int) -> None:
    if os.name == "nt":
        raise ScriptError("overlay whiteouts require the Linux converter")
    os.mknod(path, mode, device)


def _makedev(major: int, minor: int) -> int:
    if os.name == "nt":
        raise ScriptError("overlay whiteouts require the Linux converter")
    return os.makedev(major, minor)


def _metadata(path: Path, member: tarfile.TarInfo, enabled: bool) -> None:
    if not path.is_symlink():
        os.chmod(path, member.mode & 0o7777)
    if enabled:
        _chown(path, member.uid, member.gid, follow_symlinks=False)
        for key, value in member.pax_headers.items():
            if key.startswith("SCHILY.xattr."):
                _setxattr(
                    path,
                    key[len("SCHILY.xattr.") :],
                    value.encode("utf-8", "surrogateescape"),
                    follow_symlinks=False,
                )
    if path.is_symlink() and os.utime not in os.supports_follow_symlinks:
        if enabled:
            raise ScriptError("symlink metadata requires the Linux OCI converter")
        return
    os.utime(path, (0, 0), follow_symlinks=not path.is_symlink())


class ImageCache:
    def __init__(self, root: Path | None = None):
        self.root = (
            root or Path(os.environ.get("NVX_IMAGE_CACHE", "~/.nvx/cache")).expanduser()
        ).resolve()
        for name in ("blobs", "images", "refs", "leases", "bases"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def blob(self, value: str) -> Path:
        return self.root / "blobs" / validate_digest(value)[7:]

    def admit(self, path: Path) -> str:
        value = "sha256:" + sha256_file(path)
        destination = self.blob(value)
        with locked(self.root / "cache.lock"):
            if destination.exists():
                self.verify_blob(value)
                self.hits += 1
            else:
                temporary = destination.with_suffix("." + uuid.uuid4().hex)
                try:
                    shutil.copyfile(path, temporary)
                    os.chmod(temporary, 0o444)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
                self.misses += 1
        return value

    def verify_blob(self, value: str) -> Path:
        path = self.blob(value)
        if (
            path.is_symlink()
            or not path.is_file()
            or "sha256:" + sha256_file(path) != value
        ):
            raise ScriptError(
                f"cache blob missing or corrupt: {value}; reconvert the image"
            )
        return path

    def publish(self, manifest: dict[str, Any], ref: str) -> str:
        raw = canonical(manifest)
        value = digest(raw)
        for item in manifest["layers"]:
            self.verify_blob(str(item["digest"]))
        self.verify_blob(str(manifest["scratch_template"]))
        with locked(self.root / "cache.lock"):
            (self.root / "images" / value[7:]).write_bytes(raw)
            _atomic(
                self.root / "refs" / hashlib.sha256(ref.encode()).hexdigest(),
                {"ref": ref, "digest": value},
            )
        return value

    def resolve(self, ref: str) -> tuple[str, dict[str, Any]]:
        if ref.startswith("sha256:"):
            value = validate_digest(ref)
        else:
            try:
                item = json.loads(
                    (
                        self.root / "refs" / hashlib.sha256(ref.encode()).hexdigest()
                    ).read_bytes()
                )
                if item["ref"] != ref:
                    raise ValueError("reference mismatch")
                value = validate_digest(item["digest"])
            except (OSError, ValueError, KeyError, TypeError) as error:
                raise ScriptError(
                    f"image is not converted: {ref}; run nvx image pull {ref}"
                ) from error
        try:
            raw = (self.root / "images" / value[7:]).read_bytes()
            manifest = cast(dict[str, Any], json.loads(raw))
            if (
                digest(raw) != value
                or manifest["image_version"] != 1
                or manifest["converter_version"] != CONVERTER_VERSION
            ):
                raise ValueError("manifest digest/version mismatch")
            if (
                not isinstance(manifest["layers"], list)
                or not 1 <= len(cast(list[object], manifest["layers"])) <= 3
            ):
                raise ValueError("invalid layer inventory")
            for layer in cast(list[dict[str, Any]], manifest["layers"]):
                SandboxLayer.parse(
                    f"{layer['role']},{self.verify_blob(layer['digest'])},{layer['uuid']}"
                )
            self.verify_blob(manifest["scratch_template"])
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise ScriptError(f"cache manifest is corrupt: {value}") from error
        return value, manifest

    def hold(self, owner: str, value: str, kind: str) -> None:
        if (
            kind not in ("sandbox", "snapshot")
            or not owner
            or any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                for c in owner
            )
        ):
            raise ScriptError("invalid image lease owner or kind")
        with locked(self.root / "cache.lock"):
            self.resolve(value)
            path = self.root / "leases" / owner
            try:
                with path.open("xb") as output:
                    output.write(canonical({"kind": kind, "image": value}))
            except FileExistsError as error:
                raise ScriptError("image lease owner is already in use") from error

    def acquire(self, owner: str, value: str) -> Path:
        _, manifest = self.resolve(value)
        self.hold(owner, value, "sandbox")
        destination = self.root / "leases" / (owner + ".ext4")
        try:
            with (
                destination.open("xb") as output,
                self.verify_blob(manifest["scratch_template"]).open("rb") as source,
            ):
                shutil.copyfileobj(source, output)
            os.chmod(destination, 0o600)
        except OSError as error:
            destination.unlink(missing_ok=True)
            self.release(owner)
            if error.errno == errno.ENOSPC:
                raise ScriptError(
                    "scratch pool is out of space; free space in NVX_IMAGE_CACHE"
                ) from error
            raise ScriptError(f"cannot acquire private scratch: {error}") from error
        return destination

    def release(self, owner: str) -> None:
        if not owner or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for c in owner
        ):
            raise ScriptError("invalid lease owner")
        with locked(self.root / "cache.lock"):
            (self.root / "leases" / (owner + ".ext4")).unlink(missing_ok=True)
            (self.root / "leases" / owner).unlink(missing_ok=True)

    def rm(self, ref: str) -> None:
        with locked(self.root / "cache.lock"):
            value, removed = self.resolve(ref)
            for lease in (self.root / "leases").iterdir():
                if lease.suffix == ".ext4":
                    continue
                if json.loads(lease.read_bytes())["image"] == value:
                    raise ScriptError(
                        "image is referenced by a live sandbox or snapshot"
                    )
            for base in (self.root / "bases").iterdir():
                if json.loads(base.read_bytes())["image"] == value:
                    raise ScriptError("image is registered as a curated base")
            for link in (self.root / "refs").iterdir():
                if json.loads(link.read_bytes())["digest"] == value:
                    link.unlink()
            (self.root / "images" / value[7:]).unlink()
            used: set[str] = set()
            for path in (self.root / "images").iterdir():
                doc = json.loads(path.read_bytes())
                used.add(doc["scratch_template"])
                used.update(layer["digest"] for layer in doc["layers"])
            candidates = {
                removed["scratch_template"],
                *(layer["digest"] for layer in removed["layers"]),
            }
            for value in candidates - used:
                blob = self.blob(value)
                if os.name == "nt" and blob.exists():
                    # NT refuses removal of read-only files, even under a writable
                    # directory. Only unreferenced, verified cache blobs reach here.
                    os.chmod(blob, 0o600)
                blob.unlink(missing_ok=True)


def _atomic(path: Path, value: object) -> None:
    temporary = path.with_suffix("." + uuid.uuid4().hex)
    try:
        temporary.write_bytes(canonical(value))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure(ref: str) -> tuple[str, dict[str, Any]]:
    """Prepare a missing reference before any VM lease or launch is created."""
    cache = ImageCache()
    try:
        return cache.resolve(ref)
    except ScriptError as error:
        if ref.startswith("sha256:") or "image is not converted:" not in str(error):
            raise
    print("Preparing OCI image " + ref, file=sys.stderr)
    convert(ref, pull=True)
    return cache.resolve(ref)


def convert(ref: str, *, pull: bool, curated_base: bool = False) -> str:
    cache = ImageCache()
    docker = require_tool(
        "docker", "Docker with a Linux engine is required for OCI conversion"
    )
    arch = canonical_arch(platform.machine())
    docker_arch = "arm64" if arch == "aarch64" else "amd64"
    if pull:
        subprocess.run(
            [docker, "pull", "--platform", f"linux/{docker_arch}", ref],
            check=True,
            stdout=sys.stderr,
        )
    with tempfile.TemporaryDirectory(prefix="nvx-image-", dir=cache.root) as temporary:
        work = Path(temporary)
        subprocess.run(
            [docker, "save", "--output", str(work / "image.tar"), ref], check=True
        )
        with (work / "convert.log").open("wb") as log:
            build = subprocess.run(
                [
                    docker,
                    "build",
                    "--target",
                    "converter",
                    "-t",
                    "nvx-converter-base",
                    "-f",
                    str(BuildConstants.REPO_ROOT / "docker/Dockerfile"),
                    str(BuildConstants.REPO_ROOT),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            if build.returncode:
                log.flush()
                detail = (work / "convert.log").read_text(errors="replace")[-4000:]
                raise ScriptError(f"OCI converter setup failed:\n{detail}")
            result = subprocess.run(
                [
                    docker,
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--cap-add",
                    "SYS_ADMIN",
                    "-e",
                    "PYTHONPATH=/repo/scripts",
                    "-v",
                    f"{work}:/out",
                    "-v",
                    f"{cache.root}:/cache:ro",
                    "-v",
                    f"{BuildConstants.REPO_ROOT / 'scripts'}:/repo/scripts:ro",
                    "nvx-converter-base",
                    "python3",
                    "-m",
                    "nvx_tools.image",
                    "--worker",
                    str(curated_base).lower(),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
                env={**os.environ},
            )
            if result.returncode:
                log.flush()
                detail = (work / "convert.log").read_text(errors="replace")[-4000:]
                raise ScriptError(f"image conversion failed:\n{detail}")
        manifest = cast(dict[str, Any], json.loads((work / "result.json").read_bytes()))
        if manifest["architecture"] != arch:
            raise ScriptError(
                f"image architecture {manifest['architecture']} does not match host {arch}"
            )
        for layer in manifest["layers"]:
            layer["digest"] = cache.admit(work / layer.pop("file"))
        manifest["scratch_template"] = cache.admit(work / "scratch.ext4")
        value = cache.publish(manifest, ref)
        if curated_base:
            _atomic(
                cache.root / "bases" / value[7:],
                {"image": value, "prefix": manifest["oci_layers"]},
            )
        return value


def _entries(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for directory, names, files in os.walk(root, followlinks=False):
        for name in names + files:
            path = Path(directory) / name
            result[path.relative_to(root).as_posix()] = path
    return result


def _same(left: Path, right: Path) -> bool:
    a, b = left.lstat(), right.lstat()
    if (a.st_mode, a.st_uid, a.st_gid) != (b.st_mode, b.st_uid, b.st_gid):
        return False
    if stat.S_ISLNK(a.st_mode):
        return os.readlink(left) == os.readlink(right)
    if stat.S_ISREG(a.st_mode):
        return sha256_file(left) == sha256_file(right)
    return True


def _delta(base: Path, final: Path, upper: Path) -> None:
    old, new = _entries(base), _entries(final)
    for name, path in new.items():
        if name in old and _same(old[name], path):
            continue
        target = upper / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            target.symlink_to(os.readlink(path))
        elif path.is_dir():
            target.mkdir(exist_ok=True)
        else:
            shutil.copyfile(path, target)
        info = path.lstat()
        _chown(target, info.st_uid, info.st_gid, follow_symlinks=False)
        if not target.is_symlink():
            os.chmod(target, stat.S_IMODE(info.st_mode))
        for xattr in _listxattr(path, follow_symlinks=False):
            _setxattr(
                target,
                xattr,
                _getxattr(path, xattr, follow_symlinks=False),
                follow_symlinks=False,
            )
    for name in old.keys() - new.keys():
        if any(
            parent.as_posix() in old and parent.as_posix() not in new
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        ):
            continue
        target = upper / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _mknod(target, stat.S_IFCHR | 0o000, _makedev(0, 0))


def worker(curated: bool) -> None:
    work = Path("/out")
    archive = work / "image.tar"
    unpack = Path(tempfile.mkdtemp(prefix="nvx-oci-"))
    with tarfile.open(archive) as saved:
        # Docker save outer archives only contain regular files/directories.
        for member in saved:
            relative = safe_name(member.name)
            if not (member.isfile() or member.isdir()):
                raise ScriptError("invalid Docker save member type")
            target = unpack / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                target.mkdir(exist_ok=True)
            else:
                content = saved.extractfile(member)
                if content is None:
                    raise ScriptError("missing Docker save payload")
                with content, target.open("xb") as output:
                    shutil.copyfileobj(content, output)
    saved_manifests = json.loads((unpack / "manifest.json").read_bytes())
    if len(saved_manifests) != 1:
        raise ScriptError("conversion requires exactly one image")
    saved_manifest = saved_manifests[0]
    config = json.loads((unpack / safe_name(saved_manifest["Config"])).read_bytes())
    arch = canonical_arch(config["architecture"])
    if config.get("os") != "linux":
        raise ScriptError("only Linux OCI workloads are supported")
    layers = [unpack / safe_name(name) for name in saved_manifest["Layers"]]
    ids = ["sha256:" + sha256_file(path) for path in layers]
    diff_ids: list[str] = []
    for path in layers:
        with path.open("rb") as header:
            compressed = header.read(2) == b"\x1f\x8b"
        hasher = hashlib.sha256()
        with gzip.open(path, "rb") if compressed else path.open("rb") as content:
            total = 0
            while chunk := content.read(1 << 20):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise ScriptError(
                        "uncompressed image layer exceeds admission limits"
                    )
                hasher.update(chunk)
        diff_ids.append("sha256:" + hasher.hexdigest())
    if diff_ids != config["rootfs"]["diff_ids"]:
        raise ScriptError("OCI layer diff_id mismatch")
    bases: dict[str, list[str]] = {}
    base_docs: dict[str, dict[str, Any]] = {}
    for path in Path("/cache/bases").iterdir():
        item = json.loads(path.read_bytes())
        bases[item["image"]] = item["prefix"]
        raw = (Path("/cache/images") / item["image"][7:]).read_bytes()
        if digest(raw) != item["image"]:
            raise ScriptError("curated base manifest corrupt")
        base_docs[item["image"]] = json.loads(raw)
    matched, prefix = split_prefix(ids, bases) if not curated else (None, 0)
    root = Path(tempfile.mkdtemp(prefix="nvx-root-"))
    for path in layers:
        apply_layer(root, path)
    # Numeric nobody is the stable default even for images without a user DB.
    # Add only missing records/home; never silently replace image identities.
    if (root / "etc").is_symlink() or (root / "tmp").is_symlink():
        raise ScriptError("image /etc and /tmp must be plain directories")
    (root / "etc").mkdir(exist_ok=True)
    for name, record in [
        ("passwd", "nvx:x:65534:65534:NVX workload:/home/nvx:/bin/sh\n"),
        ("group", "nvx:x:65534:\n"),
    ]:
        path = root / "etc" / name
        if path.is_symlink():
            raise ScriptError("image user database must not be a symlink")
        data = path.read_text() if path.exists() else ""
        if not any(line.split(":")[2:3] == ["65534"] for line in data.splitlines()):
            path.write_text(data + record)
    passwd = (root / "etc/passwd").read_text()
    homes = [
        line.split(":")[5]
        for line in passwd.splitlines()
        if len(line.split(":")) >= 6 and line.split(":")[2] == "65534"
    ]
    if len(homes) != 1:
        raise ScriptError("image must have exactly one uid 65534 record")
    home = guest_path(root, PurePosixPath(homes[0].lstrip("/")), follow_final=True)
    home.mkdir(parents=True, exist_ok=True)
    _chown(home, 65534, 65534)
    (root / "tmp").mkdir(exist_ok=True)
    os.chmod(root / "tmp", 0o1777)
    sbin = guest_path(root, PurePosixPath("sbin"), follow_final=True)
    sbin.mkdir(parents=True, exist_ok=True)
    smoke = sbin / "nvx-sandbox-smoke"
    remove(smoke)
    shutil.copyfile("/repo/guest/common/nvx-image-smoke", smoke)
    os.chmod(smoke, 0o755)
    _chown(smoke, 0, 0)
    converted: list[dict[str, Any]] = []
    if matched:
        base = Path(tempfile.mkdtemp(prefix="nvx-base-"))
        for path in layers[:prefix]:
            apply_layer(base, path)
        upper = Path(tempfile.mkdtemp(prefix="nvx-upper-"))
        _delta(base, root, upper)
        doc = base_docs[matched]
        if len(doc["layers"]) != 1:
            raise ScriptError("curated bases must be a single distro layer")
        layer = doc["layers"][0]
        source = Path("/cache/blobs") / layer["digest"][7:]
        if "sha256:" + sha256_file(source) != layer["digest"]:
            raise ScriptError("curated base blob corrupt")
        shutil.copyfile(source, work / "distro.erofs")
        converted.append({**layer, "file": "distro.erofs"})
        role = "custom"
        root = upper
    else:
        role = "distro" if curated else "custom"
    filesystem_uuid = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            digest(
                canonical({"layers": ids, "role": role, "converter": CONVERTER_VERSION})
            ),
        )
    )
    subprocess.run(
        [
            "mkfs.erofs",
            "--quiet",
            "-T0",
            "-U",
            filesystem_uuid,
            str(work / f"{role}.erofs"),
            str(root),
        ],
        check=True,
    )
    converted.append({"role": role, "file": f"{role}.erofs", "uuid": filesystem_uuid})
    scratch = work / "scratch.ext4"
    with scratch.open("wb") as output:
        output.truncate(128 << 20)
    # Scratch is a prepared mutable template, not part of deterministic layer
    # identity. A cached template is reused for subsequent conversions.
    subprocess.run(
        [
            "mke2fs",
            "-q",
            "-t",
            "ext4",
            "-F",
            "-U",
            "22222222-2222-2222-2222-222222222222",
            "-E",
            "hash_seed=22222222-2222-2222-2222-222222222222,lazy_itable_init=0,lazy_journal_init=0",
            str(scratch),
        ],
        check=True,
        env={**os.environ, "E2FSPROGS_FAKE_TIME": "1"},
    )
    _atomic(
        work / "result.json",
        {
            "image_version": 1,
            "converter_version": CONVERTER_VERSION,
            "architecture": arch,
            "oci_config_digest": "sha256:"
            + sha256_file(unpack / safe_name(saved_manifest["Config"])),
            "oci_layers": ids,
            "layers": converted,
            "config": {
                key: config.get("config", {}).get(key)
                for key in ("Entrypoint", "Cmd", "Env", "WorkingDir")
            },
        },
    )


def command(args: argparse.Namespace) -> None:
    if args.image_operation in ("pull", "convert"):
        print(
            convert(
                args.ref,
                pull=args.image_operation == "pull",
                curated_base=args.curated_base,
            )
        )
        return
    cache = ImageCache()
    if args.image_operation == "ls":
        for path in sorted((cache.root / "refs").iterdir()):
            print(path.read_text())
    elif args.image_operation == "rm":
        cache.rm(args.ref)
    else:
        value, manifest = cache.resolve(args.ref)
        print(json.dumps({"digest": value, "manifest": manifest}, sort_keys=True))


def configure_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="image_operation", required=True)
    for name in ("pull", "convert", "verify", "rm"):
        child = commands.add_parser(name)
        child.add_argument("ref")
        if name in ("pull", "convert"):
            child.add_argument("--curated-base", action="store_true")
        child.set_defaults(handler=command)
    commands.add_parser("ls").set_defaults(handler=command)


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("internal image worker only")
    worker(sys.argv[2] == "true")


def verify_image_determinism(refs: tuple[str, ...], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    cache = ImageCache()
    results: dict[str, str] = {}
    for ref in refs:
        digests: list[str] = []
        for _attempt in range(2):
            convert(ref, pull=False)
            digest, _ = cache.resolve(ref)
            digests.append(digest)
        if digests[0] != digests[1]:
            raise ScriptError(f"OCI conversion is not deterministic: {ref}")
        results[ref] = digests[0]
    (output / "oci-determinism.json").write_bytes(canonical(results) + b"\n")
    print("NVX-OCI-DETERMINISM-OK")
