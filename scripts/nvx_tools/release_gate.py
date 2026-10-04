"""Reject incomplete, stale or mismatched release evidence before publication."""

from __future__ import annotations

import json
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any, cast

from .common import ScriptError, sha256_file
from .containment import validate_document
from .runtime_release import PLATFORMS

REQUIRED_STEPS = frozenset(
    (
        "doctor",
        "scenarios",
        "determinism",
        "warm",
        "benchmark",
        "collect",
        "performance",
    )
)


def require_core_artifact(core: str, executable: Path, provenance: Path) -> str:
    """Bind acceptance to the actual clean pinned binary before exercising it."""
    try:
        value: object = json.loads(provenance.read_bytes())
    except (OSError, ValueError) as error:
        raise ScriptError(
            "release acceptance requires valid core provenance"
        ) from error
    if not isinstance(value, dict):
        raise ScriptError("release acceptance requires a core provenance object")
    metadata = cast(dict[str, Any], value)
    digest = sha256_file(executable)
    if (
        metadata.get("format") != 1
        or metadata.get("source_clean") is not True
        or metadata.get("source_revision") != core
        or metadata.get("executable_sha256") != digest
    ):
        raise ScriptError(
            "release acceptance requires the binary built from the clean pinned core; "
            "finish build-openvmm before starting the tests"
        )
    return digest


def require_checked_performance(log: Path, platform: str) -> None:
    if "Checked 3 metric(s), found 0 regression(s)" not in log.read_text():
        raise ScriptError(
            f"{platform}: performance Warmup is not a checked regression gate; "
            "record a measured matching-platform baseline before release acceptance"
        )


def verify_matrix(
    root: Path, revision: str, core: str, version: str, destination: Path
) -> None:
    if destination.exists():
        raise ScriptError("release staging destination already exists")
    assets: dict[str, Path] = {}
    for platform, arch in PLATFORMS.items():
        lane = root / f"release-runtime-{platform}"
        proof_root = lane / "release-proof"
        proof: dict[str, Any] = json.loads(
            (proof_root / "NVX-ACCEPTANCE.json").read_bytes()
        )
        if (
            proof.get("proof_version") != 1
            or proof.get("platform") != platform
            or proof.get("architecture") != arch
            or proof.get("nvx_revision") != revision
            or proof.get("core_revision") != core
        ):
            raise ScriptError(
                f"{platform}: release evidence belongs to another source or platform"
            )
        steps = proof.get("steps", [])
        if len(steps) != len(REQUIRED_STEPS) or {
            row.get("name") for row in steps
        } != set(REQUIRED_STEPS):
            raise ScriptError(f"{platform}: complete release gates were not recorded")
        for row in steps:
            log = row.get("log", "")
            if (
                not isinstance(log, str)
                or Path(log).name != log
                or row.get("returncode") != 0
                or not (proof_root / log).is_file()
                or sha256_file(proof_root / log) != row.get("sha256")
            ):
                raise ScriptError(
                    f"{platform}: missing, failing or changed gate output"
                )
        require_checked_performance(proof_root / "performance.log", platform)
        containment_path = proof_root / "scenarios/containment/containment.json"
        containment = json.loads(containment_path.read_bytes())
        validate_document(containment)
        if containment["backend"] != proof.get("backend") or sha256_file(
            containment_path
        ) != proof.get("containment_sha256"):
            raise ScriptError(f"{platform}: containment proof mismatch")
        name = f"nvx-{version}-{platform}.tar.gz"
        archive = lane / name
        with tarfile.open(archive, "r:gz") as package:
            candidates = [
                row
                for row in package.getmembers()
                if re.fullmatch(r"[^/]+/NVX-RELEASE.json", row.name)
            ]
            if (
                len(candidates) != 1
                or not candidates[0].isfile()
                or candidates[0].size > 1 << 20
            ):
                raise ScriptError(f"{platform}: missing or ambiguous release metadata")
            file = package.extractfile(candidates[0])
            assert file is not None
            with file:
                metadata = json.load(file)
        if (
            metadata.get("version") != version
            or metadata.get("platform") != platform
            or metadata.get("architecture") != arch
            or metadata.get("development") is not False
            or metadata.get("source_clean") is not True
            or metadata.get("nvx_source_clean") is not True
            or metadata.get("nvx_revision") != revision
            or metadata.get("core_revision") != core
            or (
                platform.startswith("darwin-")
                and not metadata.get("signing", {}).get("notarized")
            )
        ):
            raise ScriptError(
                f"{platform}: archive is not a matching clean production release"
            )
        for asset in (archive, lane / f"nvx-{version}-{arch}-sources.tar.gz"):
            if not asset.is_file():
                raise ScriptError(
                    f"{platform}: corresponding source or runtime archive is absent"
                )
            prior = assets.get(asset.name)
            if prior is not None and sha256_file(prior) != sha256_file(asset):
                raise ScriptError(
                    "different source archives share a release asset name"
                )
            assets[asset.name] = asset
            bundle = asset.with_name(asset.name + ".sigstore.jsonl")
            if not bundle.is_file():
                raise ScriptError(f"{platform}: offline signature bundle is absent")
            # Shared source bytes may have several independently valid build
            # signatures. The publisher cryptographically checks the selected one.
            assets.setdefault(bundle.name, bundle)
    destination.mkdir(parents=True)
    for name, asset in assets.items():
        shutil.copyfile(asset, destination / name)
