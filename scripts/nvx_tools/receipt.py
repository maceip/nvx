"""Versioned host receipts with content integrity and optional Ed25519 signatures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import json
import time
from pathlib import Path
from typing import Any, cast

from .build_constants import BuildConstants, OpenVMMBuildConstants
from .common import ScriptError, artifact_path, openvmm_binary_path, sha256_file
from .events import read_events, redact
from .image import canonical

BODY_KEYS: set[str] = set(
    (
        "instance_id",
        "created_ns",
        "image",
        "policy",
        "argv",
        "network",
        "resources",
        "versions",
        "evidence",
        "outcome",
    )
)
FLOW_KEYS: set[str] = set(
    (
        "flow_version",
        "sequence",
        "time_ns",
        "src",
        "src_port",
        "dst",
        "dst_port",
        "proto",
        "verdict",
        "bytes",
    )
)
MAX_RECEIPT_BYTES = 16 << 20


def parse_flows(path: Path) -> dict[str, Any]:
    rows: dict[tuple[str, int, str, int, str, str], dict[str, Any]] = {}
    truncated = False
    with path.open("rb") as source:
        for sequence, raw in enumerate(iter(lambda: source.readline(4097), b""), 1):
            if sequence > 100_001 or len(raw) > 4096 or not raw.endswith(b"\n"):
                raise ScriptError("invalid or oversized flow log")
            try:
                value: object = json.loads(raw)
                if not isinstance(value, dict):
                    raise ScriptError("flow record must be an object")
                row = cast(dict[str, Any], value)
            except ValueError as error:
                raise ScriptError("invalid flow JSON") from error
            if row == {"flow_version": 1, "truncated": True}:
                truncated = True
                if source.read(1):
                    raise ScriptError("flow records after truncation marker")
                break
            if (
                set(row) != FLOW_KEYS
                or row["flow_version"] != 1
                or row["sequence"] != sequence
                or row["proto"] not in ("tcp", "udp")
                or row["verdict"] not in ("allowed", "denied")
            ):
                raise ScriptError("unsupported flow schema or ordering")
            for key in ("src", "dst"):
                try:
                    ipaddress.IPv4Address(row[key])
                except (ValueError, TypeError) as error:
                    raise ScriptError("invalid flow address") from error
            for key in ("src_port", "dst_port", "time_ns", "bytes"):
                if (
                    type(row[key]) is not int
                    or row[key] < 0
                    or (key.endswith("port") and row[key] > 65535)
                ):
                    raise ScriptError("invalid flow number")
            identity = (
                row["src"],
                row["src_port"],
                row["dst"],
                row["dst_port"],
                row["proto"],
                row["verdict"],
            )
            if identity not in rows:
                rows[identity] = {
                    key: row[key]
                    for key in (
                        "src",
                        "src_port",
                        "dst",
                        "dst_port",
                        "proto",
                        "verdict",
                    )
                }
                rows[identity].update(
                    packets=0, bytes=0, first_ns=row["time_ns"], last_ns=row["time_ns"]
                )
            flow = rows[identity]
            flow["packets"] += 1
            flow["bytes"] += row["bytes"]
            flow["last_ns"] = row["time_ns"]
    return {
        "source": "host-endpoint-policy",
        "complete": not truncated,
        "flows": [rows[key] for key in sorted(rows)],
    }


def seal(body: dict[str, Any]) -> dict[str, Any]:
    validate_body(body)
    return {
        "receipt_version": 1,
        "body": body,
        "body_sha256": hashlib.sha256(canonical(body)).hexdigest(),
    }


def validate_body(body: dict[str, Any]) -> None:
    if set(body) != BODY_KEYS:
        raise ScriptError("receipt body schema changed; use a new receipt version")
    if not isinstance(body["instance_id"], str) or not isinstance(
        body["evidence"], dict
    ):
        raise ScriptError("invalid receipt identity or evidence")
    evidence = cast(dict[str, object], body["evidence"])
    for name, digest in evidence.items():
        if (
            not isinstance(digest, str)
            or Path(name).name != name
            or name in (".", "..")
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ScriptError("invalid receipt evidence name or digest")


def _ed25519(payload: bytes, key: Path, signature: bytes | None = None) -> bytes:
    # Import only on the optional signing path. Unsigned receipts and sandboxing
    # remain usable without the signing dependency, including in the converter.
    try:
        from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
            Ed25519PublicKey,
        )
    except ImportError as error:
        raise ScriptError(
            "receipt signatures require cryptography; run python3 -m pip install "
            "-r requirements-signing.txt from the NVX installation"
        ) from error
    try:
        with key.open("rb") as source:
            encoded = source.read(65537)
        if len(encoded) > 65536:
            raise ValueError("oversized key")
        if signature is None:
            private = serialization.load_pem_private_key(encoded, password=None)
            if not isinstance(private, Ed25519PrivateKey):
                raise ValueError("not an Ed25519 private key")
            return private.sign(payload)
        public = serialization.load_pem_public_key(encoded)
        if not isinstance(public, Ed25519PublicKey):
            raise ValueError("not an Ed25519 public key")
        public.verify(signature, payload)
        return b""
    except (
        OSError,
        ValueError,
        TypeError,
        InvalidSignature,
        UnsupportedAlgorithm,
    ) as error:
        raise ScriptError(
            "Ed25519 signing/verification failed; check the key and signature"
        ) from error


def sign(document: dict[str, Any], key: Path) -> None:
    document["signature"] = {
        "algorithm": "Ed25519",
        "value": base64.b64encode(_ed25519(canonical(document["body"]), key)).decode(),
    }


def verify(
    path: Path, *, evidence_dir: Path | None = None, public_key: Path | None = None
) -> dict[str, Any]:
    if path.stat().st_size > MAX_RECEIPT_BYTES:
        raise ScriptError("receipt exceeds size limit")
    try:
        value: object = json.loads(path.read_bytes())
        if not isinstance(value, dict):
            raise ScriptError("receipt must be an object")
        document = cast(dict[str, Any], value)
        if (
            document.get("receipt_version") != 1
            or set(document) - {"receipt_version", "body", "body_sha256", "signature"}
            or not isinstance(document.get("body"), dict)
        ):
            raise ScriptError("unsupported receipt version/schema")
        expected = seal(document["body"])
        if document.get("body_sha256") != expected["body_sha256"]:
            raise ScriptError("receipt was modified: body digest mismatch")
        if "signature" in document:
            signature = document["signature"]
            if public_key is None:
                raise ScriptError(
                    "signed receipt needs --public-key from a trusted source"
                )
            if (
                set(signature) != {"algorithm", "value"}
                or signature["algorithm"] != "Ed25519"
            ):
                raise ScriptError("unsupported receipt signature")
            _ed25519(
                canonical(document["body"]),
                public_key,
                base64.b64decode(signature["value"], validate=True),
            )
        elif public_key is not None:
            raise ScriptError("receipt has no signature")
        root = evidence_dir or path.parent
        for name, digest in document["body"]["evidence"].items():
            file = root / name
            if file.is_symlink() or not file.is_file() or sha256_file(file) != digest:
                raise ScriptError(f"receipt evidence was modified or missing: {name}")
        return document
    except (ValueError, TypeError, KeyError) as error:
        raise ScriptError("malformed receipt") from error


def versions(architecture: str) -> dict[str, Any]:
    return {
        "nvx": (BuildConstants.REPO_ROOT / "VERSION").read_text().strip(),
        "microvm_abi": OpenVMMBuildConstants.MICROVM_ABI_VERSION,
        "control_protocol": OpenVMMBuildConstants.CONTROL_SESSION_PROTOCOL_VERSION,
        "openvmm_sha256": sha256_file(openvmm_binary_path()),
        "kernel_sha256": sha256_file(
            artifact_path("Image" if architecture == "aarch64" else "vmlinux")
        ),
        "initramfs_sha256": sha256_file(artifact_path("initramfs.cpio.gz")),
    }


def build(
    state: Path,
    policy: dict[str, Any],
    argv: tuple[str, ...],
    outcome: dict[str, Any],
    *,
    secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    image = json.loads((state / "image.json").read_bytes())
    manifest = image["manifest"]
    resources = json.loads((state / "resources.json").read_bytes())
    events = read_events(state / "events.jsonl")
    if not events or events[-1]["kind"] != "run.exited":
        raise ScriptError("receipt requires confirmed teardown")
    evidence = {
        name: sha256_file(state / name)
        for name in (
            "image.json",
            "config.json",
            "events.jsonl",
            "flows.jsonl",
            "resources.json",
            "outcome.json",
        )
    }
    for name in ("proxy.jsonl", "policy.json", "argv.json", "versions.json"):
        if (state / name).is_file():
            evidence[name] = sha256_file(state / name)
    body: dict[str, Any] = {
        "instance_id": state.name,
        "created_ns": time.time_ns(),
        "image": {
            "ref": image["ref"],
            "manifest_digest": image["manifest_digest"],
            "layers": manifest["layers"],
            "oci_config_digest": manifest["oci_config_digest"],
            "architecture": manifest["architecture"],
        },
        "policy": policy,
        "argv": redact(argv, secrets=secrets),
        "network": parse_flows(state / "flows.jsonl"),
        "resources": resources,
        "versions": json.loads((state / "versions.json").read_bytes())
        if (state / "versions.json").is_file()
        else versions(manifest["architecture"]),
        "evidence": evidence,
        "outcome": outcome,
    }
    body["image"]["ref"] = redact(body["image"]["ref"], secrets=secrets)
    return seal(body)


def publish(path: Path, document: dict[str, Any]) -> None:
    payload = canonical(document) + b"\n"
    if len(payload) > MAX_RECEIPT_BYTES:
        raise ScriptError("receipt exceeds size limit")
    from .sandbox_lifecycle import publish_json

    publish_json(path, document)


def command(args: argparse.Namespace) -> None:
    document = verify(
        args.path, evidence_dir=args.evidence_dir, public_key=args.public_key
    )
    print(
        json.dumps(
            {
                "instance_id": document["body"]["instance_id"],
                "receipt_version": 1,
                "verified": True,
            },
            sort_keys=True,
        )
    )


def configure_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="receipt_operation", required=True)
    child = commands.add_parser("verify")
    child.add_argument("path", type=Path)
    child.add_argument("--evidence-dir", type=Path)
    child.add_argument("--public-key", type=Path)
    child.set_defaults(handler=command)
