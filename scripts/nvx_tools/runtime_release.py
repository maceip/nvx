"""Architecture-specific, self-contained CLI/SDK distributions and preview builds."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .build_constants import (
    BuildConstants,
    OpenVMMBuildConstants,
    ReleaseBuildConstants,
)
from .common import (
    ScriptError,
    artifact_path,
    openvmm_binary_path,
    openvmm_git_state,
    sha256_file,
    verify_sha256_sums,
    write_sha256_sums,
)
from .doctor import binary_arch, entitlement_check

PLATFORMS = {
    "darwin-arm64": "aarch64",
    "darwin-x86_64": "x86_64",
    "linux-arm64": "aarch64",
    "linux-kvm": "x86_64",
    "linux-mshv": "x86_64",
    "windows-whp": "x86_64",
}


def _core_platform(path: Path) -> str:
    with path.open("rb") as source:
        magic = source.read(4)
    if magic == b"\x7fELF":
        return "linux"
    if magic == b"\xcf\xfa\xed\xfe":
        return "darwin"
    if magic[:2] == b"MZ":
        return "windows"
    return "unknown"


def inventory(platform: str) -> tuple[str, ...]:
    if platform not in PLATFORMS:
        raise ScriptError("unsupported release platform: " + platform)
    if PLATFORMS[platform] == "aarch64":
        return (
            "Image",
            "vmlinux",
            "vmlinux.config",
            "initramfs.cpio.gz",
            "initramfs.cpio.gz.packages.json",
        )
    return tuple(ReleaseBuildConstants.GUEST_ARTIFACT_NAMES)


def copy_cli(destination: Path) -> None:
    root = BuildConstants.REPO_ROOT
    for name in ("scripts", "guest", "kernel", "docker", "ubuntu", "sdk", "doc"):
        shutil.copytree(
            root / name,
            destination / name,
            ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc", "node_modules", "dist", ".DS_Store"
            ),
        )
    compiled = root / "sdk/typescript/dist"
    if not compiled.is_dir():
        raise ScriptError(
            "build the TypeScript SDK before packaging: npm --prefix sdk/typescript run build"
        )
    shutil.copytree(compiled, destination / "sdk/typescript/dist")
    for name in (
        ".dockerignore",
        "VERSION",
        "LICENSE",
        "README.md",
        "SOURCE-MANIFEST.json",
        "THIRD_PARTY_NOTICES.md",
        "requirements-signing.txt",
    ):
        shutil.copyfile(root / name, destination / name)
    entitlements = Path("openvmm/build_support/macos/entitlements.xml")
    (destination / entitlements).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / entitlements, destination / entitlements)
    launcher = destination / "bin" / "nvx"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text(
        "#!/usr/bin/env python3\nimport runpy,sys\nfrom pathlib import Path\nroot=Path(__file__).resolve().parents[1]\nsys.path.insert(0,str(root/'scripts'))\nrunpy.run_path(str(root/'scripts/nvx.py'),run_name='__main__')\n"
    )
    launcher.chmod(0o755)
    (destination / "bin/nvx.cmd").write_text(
        '@echo off\npython "%~dp0..\\scripts\\nvx.py" %*\n'
    )


def package(
    destination: Path,
    platform: str,
    version: str,
    *,
    development: bool = False,
    source: bool = False,
) -> Path:
    expected = PLATFORMS.get(platform)
    if expected is None or destination.exists():
        raise ScriptError("release platform unsupported or destination already exists")
    core = openvmm_binary_path()
    if (
        binary_arch(artifact_path("vmlinux")) != expected
        or binary_arch(core) != expected
        or _core_platform(core) != platform.split("-", 1)[0]
    ):
        raise ScriptError(
            "release kernel/core architecture does not match its platform"
        )
    revision, clean = openvmm_git_state(OpenVMMBuildConstants.DIRECTORY)
    nvx_revision = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=BuildConstants.REPO_ROOT,
        text=True,
    ).strip()
    nvx_clean = not subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=BuildConstants.REPO_ROOT,
        text=True,
    ).strip()
    pinned = subprocess.check_output(
        ["git", "rev-parse", "HEAD:openvmm"],
        cwd=BuildConstants.REPO_ROOT,
        text=True,
    ).strip()
    core_provenance = json.loads(artifact_path("openvmm.provenance.json").read_bytes())
    if core_provenance.get("source_revision") != revision or core_provenance.get(
        "executable_sha256"
    ) != sha256_file(core):
        raise ScriptError("core provenance does not match the packaged executable")
    if not development and (
        not clean
        or not nvx_clean
        or pinned != revision
        or core_provenance.get("source_clean") is not True
    ):
        raise ScriptError(
            "published releases require clean NVX source and a clean, pinned core build; --development creates a labelled local preview"
        )
    signing: dict[str, Any] = {"signed": False, "notarized": False}
    if platform.startswith("darwin-"):
        check = entitlement_check(core)
        if not check.ok:
            raise ScriptError(
                "release lacks the hypervisor entitlement: " + check.detail
            )
        signature = subprocess.run(
            ["codesign", "--verify", "--strict", str(core)], capture_output=True
        )
        identity = subprocess.run(
            ["codesign", "-dv", "--verbose=4", str(core)], capture_output=True
        )
        developer = b"Authority=Developer ID Application:" in identity.stderr
        assessed = subprocess.run(
            ["spctl", "--assess", "--type", "execute", str(core)], capture_output=True
        )
        signing = {
            "signed": signature.returncode == 0,
            "developer_id": developer,
            "notarized": assessed.returncode == 0 and developer,
        }
        if not development and not signing["notarized"]:
            raise ScriptError(
                "macOS publication requires Developer ID signing and a successful notarization assessment"
            )
    from .release import validate_initramfs_provenance, validate_kernel_provenance

    validate_kernel_provenance(
        artifact_path("vmlinux"),
        artifact_path("vmlinux.config"),
        artifact_path("vmlinux.provenance.json"),
    )
    validate_initramfs_provenance(
        artifact_path("initramfs.cpio.gz"),
        artifact_path("initramfs.cpio.gz.packages.json"),
        artifact_path("initramfs.provenance.json"),
    )
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".nvx-package-", dir=destination.parent))
    try:
        copy_cli(staging)
        shutil.copy2(core, staging / "bin" / core.name)
        (staging / "build").mkdir()
        for name in (
            *inventory(platform),
            "openvmm.provenance.json",
            "vmlinux.provenance.json",
            "initramfs.provenance.json",
        ):
            shutil.copyfile(artifact_path(name), staging / "build" / name)
        (staging / "licenses").mkdir()
        shutil.copyfile(
            OpenVMMBuildConstants.DIRECTORY / "LICENSE",
            staging / "licenses/LICENSE-OPENVMM",
        )
        shutil.copyfile(
            BuildConstants.REPO_ROOT / "kernel/COPYING-LINUX",
            staging / "licenses/COPYING-LINUX",
        )
        if source:
            from .release import validate_corresponding_sources

            sources = BuildConstants.SOURCE_DIR
            if not sources.is_dir():
                raise ScriptError(
                    "matching corresponding sources must be collected before packaging"
                )
            validate_corresponding_sources()
            shutil.copytree(sources, staging / "source")
        metadata = {
            "release_version": 1,
            "version": version,
            "platform": platform,
            "architecture": expected,
            "development": development,
            "core_revision": revision,
            "source_clean": clean,
            "nvx_revision": nvx_revision,
            "nvx_source_clean": nvx_clean,
            "signing": signing,
            "guest_artifacts": list(inventory(platform)),
            "commands": ["nvx", "nvx mcp serve"],
            "sdks": ["python", "typescript"],
            "corresponding_source": "included"
            if source
            else "publish matching source separately",
        }
        (staging / "NVX-RELEASE.json").write_text(
            json.dumps(metadata, sort_keys=True, indent=2) + "\n"
        )
        write_sha256_sums(staging)
        verify_sha256_sums(staging)
        staging.rename(destination)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return destination


def install(package_root: Path, destination: Path) -> Path:
    """Install the entire verified runtime into a new directory without overwrites."""
    if destination.exists() or destination.is_symlink():
        raise ScriptError("installation destination already exists")
    verify_sha256_sums(package_root)
    metadata = json.loads((package_root / "NVX-RELEASE.json").read_bytes())
    platform = metadata.get("platform")
    if (
        not isinstance(platform, str)
        or platform not in PLATFORMS
        or metadata.get("release_version") != 1
        or metadata.get("architecture") != PLATFORMS[platform]
        or tuple(metadata.get("guest_artifacts", ())) != inventory(platform)
    ):
        raise ScriptError("release inventory does not match its platform")
    core = "openvmm.exe" if platform == "windows-whp" else "openvmm"
    required = (
        "bin/nvx",
        "bin/" + core,
        "scripts/nvx.py",
        "scripts/nvx_tools/mcp.py",
        "sdk/python/nvx_sdk/__init__.py",
        "sdk/typescript/dist/index.js",
        "requirements-signing.txt",
        "licenses/LICENSE-OPENVMM",
        "licenses/COPYING-LINUX",
        "build/openvmm.provenance.json",
        "build/vmlinux.provenance.json",
        "build/initramfs.provenance.json",
        *("build/" + name for name in inventory(platform)),
        *(("bin/nvx.cmd",) if platform == "windows-whp" else ()),
    )
    for name in required:
        if not (package_root / name).is_file():
            raise ScriptError("release inventory is incomplete")
    if (
        binary_arch(package_root / "bin" / core) != PLATFORMS[platform]
        or binary_arch(package_root / "build/vmlinux") != PLATFORMS[platform]
        or _core_platform(package_root / "bin" / core) != platform.split("-", 1)[0]
    ):
        raise ScriptError("release architecture does not match its platform")
    shutil.copytree(package_root, destination)
    return destination / "bin/nvx"


def command_install(args: argparse.Namespace) -> None:
    from .release import extract_release_archive

    with tempfile.TemporaryDirectory(prefix="nvx-install-") as temporary:
        root = Path(temporary)
        extract_release_archive(args.archive, root)
        inventories = list(root.glob("*/NVX-RELEASE.json"))
        if len(inventories) != 1:
            raise ScriptError("archive must contain one self-contained NVX release")
        print(install(inventories[0].parent, args.destination))


def configure_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "install", help="install a verified self-contained release archive"
    )
    parser.set_defaults(handler=command_install)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
