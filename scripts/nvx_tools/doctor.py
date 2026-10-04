"""Read-only diagnostics and automatic macOS entitlement setup."""

from __future__ import annotations

import argparse
import json
import os
import platform
import plistlib
import shutil
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from .build_constants import BuildConstants
from .common import (
    ScriptError,
    artifact_path,
    openvmm_binary_path,
    run_checked,
    sha256_file,
)


def canonical_arch(machine: str) -> str:
    return {"arm64": "aarch64", "amd64": "x86_64"}.get(machine.lower(), machine.lower())


def binary_arch(path: Path) -> str:
    with path.open("rb") as source:
        header = source.read(64)
        if header[:2] == b"MZ" and len(header) == 64:
            offset = struct.unpack_from("<I", header, 60)[0]
            if offset > 1 << 20:
                return "unknown"
            source.seek(offset)
            pe = source.read(6)
            if len(pe) == 6 and pe[:4] == b"PE\0\0":
                return {0x8664: "x86_64", 0xAA64: "aarch64"}.get(
                    struct.unpack_from("<H", pe, 4)[0], "unknown"
                )
            return "unknown"
    if header[:4] == b"\x7fELF" and len(header) >= 20:
        endian = "<" if header[5] == 1 else ">"
        return {62: "x86_64", 183: "aarch64"}.get(
            struct.unpack_from(endian + "H", header, 18)[0], "unknown"
        )
    if header[:4] == b"\xcf\xfa\xed\xfe" and len(header) >= 8:
        return {0x0100000C: "aarch64", 0x01000007: "x86_64"}.get(
            struct.unpack_from("<I", header, 4)[0], "unknown"
        )
    if len(header) >= 60 and header[56:60] == b"ARM\x64":
        return "aarch64"
    return "unknown"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    advice: str = ""


def artifact_check(path: Path, expected_arch: str) -> Check:
    if not path.is_file():
        return Check(
            path.name, False, f"missing: {path}", "run nvx download or nvx build-guest"
        )
    actual = binary_arch(path)
    return Check(
        path.name,
        actual == expected_arch,
        f"architecture {actual}; expected {expected_arch}",
        "download artifacts for this host architecture"
        if actual != expected_arch
        else "",
    )


def entitlement_check(binary: Path) -> Check:
    try:
        result = subprocess.run(
            ["codesign", "-d", "--entitlements", ":-", str(binary)],
            capture_output=True,
            timeout=10,
        )
        start = result.stdout.find(b"<?xml")
        data = (
            cast(dict[str, object], plistlib.loads(result.stdout[start:]))
            if start >= 0
            else dict[str, object]()
        )
        ok = (
            result.returncode == 0 and data.get("com.apple.security.hypervisor") is True
        )
    except (
        OSError,
        ValueError,
        plistlib.InvalidFileException,
        subprocess.TimeoutExpired,
    ):
        ok = False
    return Check(
        "entitlement",
        ok,
        "Hypervisor entitlement present" if ok else "Hypervisor entitlement missing",
        "run nvx setup" if not ok else "",
    )


def device_check(device: Path) -> Check:
    ok = device.exists() and os.access(device, os.R_OK | os.W_OK)
    return Check(
        "hypervisor",
        ok,
        f"{device}: {'accessible' if ok else 'unavailable'}",
        f"enable the hypervisor and grant read/write access to {device}"
        if not ok
        else "",
    )


def checks(backend: str) -> list[Check]:
    arch = canonical_arch(platform.machine())
    binary = openvmm_binary_path()
    results = [artifact_check(binary, arch)]
    if backend == "hvf":
        results.append(
            Check(
                "host",
                sys.platform == "darwin" and arch in ("aarch64", "x86_64"),
                "HVF requires arm64 or Intel macOS",
            )
        )
        results.append(entitlement_check(binary))
    elif backend in ("kvm", "mshv"):
        results.append(device_check(Path("/dev") / backend))
    else:
        results.append(Check("host", sys.platform == "win32", "WHP requires Windows"))
    results.append(
        artifact_check(artifact_path("Image" if arch == "aarch64" else "vmlinux"), arch)
    )
    initramfs = artifact_path("initramfs.cpio.gz")
    results.append(
        Check(
            "initramfs",
            initramfs.is_file(),
            str(initramfs),
            "run nvx download or nvx build-guest" if not initramfs.is_file() else "",
        )
    )
    metadata = initramfs.with_name(initramfs.name + ".packages.json")
    try:
        guest_arch = json.loads(metadata.read_text())["architecture"]
        results.append(
            Check(
                "guest-architecture",
                canonical_arch(guest_arch) == arch,
                f"guest {guest_arch}; host {arch}",
                "rebuild or download matching guest artifacts",
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        results.append(
            Check(
                "guest-architecture",
                False,
                "guest architecture metadata unavailable",
                "run nvx download or nvx build-guest",
            )
        )
    docker = shutil.which("docker")
    try:
        result = subprocess.run(
            [docker or "docker", "info", "--format", "{{.Architecture}}"],
            capture_output=True,
            timeout=10,
        )
        docker_ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        docker_ok = False
    results.append(
        Check(
            "docker",
            docker_ok,
            "Docker ready" if docker_ok else "Docker unavailable",
            "start Docker for image conversion and guest builds"
            if not docker_ok
            else "",
        )
    )
    cache = Path(os.environ.get("NVX_IMAGE_CACHE", "~/.nvx/cache")).expanduser()
    parent = cache
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    results.append(
        Check(
            "cache",
            parent.is_dir() and os.access(parent, os.W_OK),
            str(cache),
            "set NVX_IMAGE_CACHE to a writable directory",
        )
    )
    for name, artifact, hash_key in (
        (
            "vmlinux",
            artifact_path("vmlinux"),
            "kernel_sha256",
        ),
        ("initramfs", initramfs, "initramfs_sha256"),
        ("openvmm", binary, "executable_sha256"),
    ):
        path = artifact_path(name + ".provenance.json")
        try:
            doc = json.loads(path.read_bytes())
            ok = doc["format"] == 1 and doc[hash_key] == sha256_file(artifact)
            if name == "vmlinux" and arch == "aarch64":
                ok = ok and doc.get("boot_image_sha256") == sha256_file(
                    artifact_path("Image")
                )
            version = (
                doc.get("source_revision")
                or doc.get("source", {}).get("version")
                or doc.get("inputs", {}).get("alpine", {}).get("version")
            )
            detail = f"{version or 'unknown version'}; artifact digest {'verified' if ok else 'mismatch'}"
        except (OSError, ValueError, KeyError, TypeError):
            ok, detail = False, "version provenance missing or malformed"
        results.append(
            Check(
                "versions:" + name,
                ok,
                detail,
                "download or rebuild a complete matching release",
            )
        )
    return results


def command_doctor(args: argparse.Namespace) -> int:
    backend = args.backend or (
        "hvf" if sys.platform == "darwin" else "whp" if os.name == "nt" else "kvm"
    )
    results = checks(backend)
    if args.json:
        print(json.dumps([asdict(check) for check in results], sort_keys=True))
    else:
        for check in results:
            print(f"{'OK' if check.ok else 'FAIL'} {check.name}: {check.detail}")
            if not check.ok and check.advice:
                print(f"  {check.advice}")
    return 0 if all(check.ok for check in results) else 1


def command_setup(_: argparse.Namespace) -> None:
    if sys.platform != "darwin":
        raise ScriptError(
            "nvx setup currently signs macOS binaries; use nvx doctor for host prerequisites"
        )
    binary = openvmm_binary_path()
    entitlements = (
        BuildConstants.REPO_ROOT / "scripts/nvx_tools/openvmm-macos.entitlements.plist"
    )
    run_checked(
        ["codesign", "--force", "--sign", "-", "--entitlements", entitlements, binary]
    )
    check = entitlement_check(binary)
    if not check.ok:
        raise ScriptError(check.detail)
    print(check.detail)


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--backend", choices=("kvm", "mshv", "whp", "hvf"))
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=command_doctor)
