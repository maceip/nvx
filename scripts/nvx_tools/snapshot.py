#!/usr/bin/env python3
"""Host-side snapshot lifecycle helpers.

`verify` structurally validates a snapshot directory before a restore is
attempted: required artifacts are present as regular files (never symlinks,
matching restore's no-follow stance), the manifest parses, and the recorded
memory/state lengths match the files on disk. It also reports the manifest
summary (an inspect view of the snapshot generation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nvx_tools.common import ScriptError, sha256_file

MANIFEST_FILE_NAME = "manifest.bin"
STATE_FILE_NAME = "state.bin"
MEMORY_FILE_NAME = "memory.bin"

# Manifest versions restore accepts (v2 legacy through v6 current).
MANIFEST_VERSION_MIN = 2
MANIFEST_VERSION_MAX = 6

# Known snapshot format magics, oldest to newest. Very old manifests may
# omit the magic; an unrecognized non-empty magic is rejected.
KNOWN_FORMAT_MAGICS = (
    b"OPENVMM_SNAPSHOT_V2\0",
    b"OPENVMM_SNAPSHOT_V3\0",
    b"OPENVMM_SNAPSHOT_V4\0",
    b"OPENVMM_SNAPSHOT_V5\0",
    b"OPENVMM_SNAPSHOT_V6\0",
)


@dataclass(frozen=True)
class SnapshotReport:
    """Manifest summary of a verified snapshot generation."""

    path: Path
    version: int
    openvmm_version: str
    memory_size_bytes: int
    vp_count: int
    page_size: int
    architecture: str
    state_size_bytes: int
    saved_state_schema_version: int
    saved_state_root_type: str
    restore_policy: str
    linux_direct_boot: bool
    created_at_seconds: int


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise ScriptError("snapshot manifest is truncated")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 64:
            raise ScriptError("snapshot manifest has a malformed varint")


def _skip_field(data: bytes, pos: int, wire: int) -> int:
    if wire == 0:
        _, pos = _read_varint(data, pos)
    elif wire == 1:
        pos += 8
    elif wire == 2:
        length, pos = _read_varint(data, pos)
        pos += length
    elif wire == 5:
        pos += 4
    else:
        raise ScriptError(f"snapshot manifest has unsupported wire type {wire}")
    if pos > len(data):
        raise ScriptError("snapshot manifest is truncated")
    return pos


def _parse_message(data: bytes) -> dict[int, int | bytes]:
    """Parse top-level protobuf fields; varints as int, LEN as raw bytes."""
    fields: dict[int, int | bytes] = {}
    pos = 0
    while pos < len(data):
        key, pos = _read_varint(data, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            value, pos = _read_varint(data, pos)
            fields[field] = value
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            end = pos + length
            if end > len(data):
                raise ScriptError("snapshot manifest is truncated")
            fields[field] = data[pos:end]
            pos = end
        else:
            pos = _skip_field(data, pos, wire)
    return fields


def _get_varint(fields: dict[int, int | bytes], number: int, name: str) -> int:
    value = fields.get(number, 0)
    if not isinstance(value, int):
        raise ScriptError(f"snapshot manifest field {name} has the wrong type")
    return value


def _get_string(fields: dict[int, int | bytes], number: int, name: str) -> str:
    value = fields.get(number, b"")
    if isinstance(value, int):
        raise ScriptError(f"snapshot manifest field {name} has the wrong type")
    try:
        return bytes(value).decode("utf-8")
    except UnicodeDecodeError as err:
        raise ScriptError(f"snapshot manifest field {name} is not valid UTF-8") from err


def _parse_timestamp(raw: int | bytes) -> int:
    if isinstance(raw, int):
        raise ScriptError("snapshot manifest timestamp has the wrong type")
    seconds = _get_varint(_parse_message(bytes(raw)), 1, "created_at.seconds")
    return seconds


def _require_regular_file(path: Path, description: str) -> int:
    if path.is_symlink():
        raise ScriptError(f"{description} must not be a symlink: {path}")
    if not path.is_file():
        raise ScriptError(f"{description} is missing or not a file: {path}")
    return path.stat().st_size


def verify_snapshot(
    snapshot_dir: Path, *, expected_arch: str | None = None
) -> SnapshotReport:
    """Validate a snapshot directory and return its manifest summary."""
    if not snapshot_dir.is_dir() or snapshot_dir.is_symlink():
        raise ScriptError(f"snapshot directory not found: {snapshot_dir}")
    manifest_size = _require_regular_file(
        snapshot_dir / MANIFEST_FILE_NAME, "snapshot manifest"
    )
    state_size = _require_regular_file(snapshot_dir / STATE_FILE_NAME, "snapshot state")
    memory_size = _require_regular_file(
        snapshot_dir / MEMORY_FILE_NAME, "snapshot memory"
    )
    if manifest_size == 0:
        raise ScriptError("snapshot manifest is empty")

    fields = _parse_message((snapshot_dir / MANIFEST_FILE_NAME).read_bytes())
    version = _get_varint(fields, 1, "version")
    if not MANIFEST_VERSION_MIN <= version <= MANIFEST_VERSION_MAX:
        raise ScriptError(
            f"snapshot manifest version {version} is not supported "
            f"(expected {MANIFEST_VERSION_MIN}..{MANIFEST_VERSION_MAX})"
        )
    magic = fields.get(12, b"")
    if magic and bytes(magic) not in KNOWN_FORMAT_MAGICS:
        raise ScriptError("snapshot manifest has an unrecognized format magic")

    memory_size_bytes = _get_varint(fields, 4, "memory_size_bytes")
    if memory_size_bytes <= 0:
        raise ScriptError("snapshot manifest records no guest RAM")
    if memory_size != memory_size_bytes:
        raise ScriptError(
            f"snapshot memory.bin is {memory_size} bytes but the manifest "
            f"records {memory_size_bytes}"
        )
    state_size_bytes = _get_varint(fields, 8, "state_size_bytes")
    if state_size_bytes <= 0:
        raise ScriptError("snapshot manifest records no device state")
    if state_size != state_size_bytes:
        raise ScriptError(
            f"snapshot state.bin is {state_size} bytes but the manifest "
            f"records {state_size_bytes}"
        )
    architecture = _get_string(fields, 7, "architecture")
    if not architecture:
        raise ScriptError("snapshot manifest has no architecture")
    if expected_arch is not None and architecture != expected_arch:
        raise ScriptError(
            f"snapshot architecture {architecture} does not match host {expected_arch}; "
            "cross-architecture restore is unsupported"
        )

    if version >= 6:
        for number, filename in ((9, STATE_FILE_NAME), (10, MEMORY_FILE_NAME)):
            digest = fields.get(number)
            if not isinstance(digest, bytes) or len(digest) != 32:
                raise ScriptError(f"snapshot {filename} has no valid SHA-256 digest")
            if sha256_file(snapshot_dir / filename) != digest.hex():
                raise ScriptError(f"snapshot {filename} SHA-256 digest mismatch")

    return SnapshotReport(
        path=snapshot_dir,
        version=version,
        openvmm_version=_get_string(fields, 3, "openvmm_version"),
        memory_size_bytes=memory_size_bytes,
        vp_count=_get_varint(fields, 5, "vp_count"),
        page_size=_get_varint(fields, 6, "page_size"),
        architecture=architecture,
        state_size_bytes=state_size_bytes,
        saved_state_schema_version=_get_varint(
            fields, 13, "saved_state_schema_version"
        ),
        saved_state_root_type=_get_string(fields, 14, "saved_state_root_type"),
        restore_policy=_get_string(fields, 16, "restore_policy"),
        # Absent means false: snapshots written before the field existed.
        linux_direct_boot=bool(_get_varint(fields, 18, "linux_direct_boot")),
        created_at_seconds=_parse_timestamp(fields.get(2, b"")),
    )


def format_report(report: SnapshotReport) -> str:
    """Render a one-block inspect view of a verified snapshot."""
    boot = "linux-direct" if report.linux_direct_boot else "firmware"
    lines = [
        f"snapshot: {report.path}",
        f"  manifest version: {report.version}",
        f"  created by openvmm: {report.openvmm_version or '(unknown)'}",
        f"  architecture: {report.architecture}",
        "  payload integrity: "
        + (
            "SHA-256 verified"
            if report.version >= 6
            else "legacy structural checks only"
        ),
        f"  guest RAM: {report.memory_size_bytes} bytes",
        f"  vCPUs: {report.vp_count}",
        f"  boot mode: {boot}",
        f"  device state: {report.state_size_bytes} bytes "
        f"({report.saved_state_root_type or 'unknown root'}, "
        f"schema {report.saved_state_schema_version})",
    ]
    if report.restore_policy:
        lines.append(f"  restore policy: {report.restore_policy}")
    return "\n".join(lines)
