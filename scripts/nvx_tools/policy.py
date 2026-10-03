"""Deterministic profile resolution: explicit CLI > nvx.toml > profile defaults."""

from __future__ import annotations

import argparse
import importlib
import ipaddress
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, cast

from .common import ScriptError


@dataclass(frozen=True)
class Policy:
    profile: str = "default"
    egress: str = "deny"
    allow: tuple[str, ...] = ()
    seccomp: str = "nvx-default"
    uid: int = 65534
    gid: int = 65534
    caps: tuple[str, ...] = ()
    device_filter: bool = True
    masked_paths: bool = True
    no_new_privs: bool = True
    pids_max: int | None = 128
    memory_max: int | None = 256 << 20
    wall_timeout_ms: int = 60_000

    def document(self) -> dict[str, Any]:
        return asdict(self)


PROFILES = {
    "default": Policy(),
    "ci": Policy(profile="ci"),
    "risky": Policy(
        profile="risky",
        egress="allow",
        seccomp="unconfined",
        uid=0,
        gid=0,
        caps=("ALL",),
        device_filter=False,
        masked_paths=False,
        no_new_privs=False,
        pids_max=None,
        memory_max=None,
        wall_timeout_ms=0,
    ),
}
KEYS = frozenset(Policy.__dataclass_fields__)


def read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        try:
            module = importlib.import_module("tomllib")
        except ModuleNotFoundError:
            module = importlib.import_module("tomli")
        document = cast(dict[str, Any], module.loads(path.read_text()))
    except ModuleNotFoundError as error:
        raise ScriptError(
            "Python 3.10 needs tomli to read nvx.toml; install requirements-dev.txt"
        ) from error
    except (ValueError, OSError) as error:
        raise ScriptError(f"{path}: {error}") from error
    if set(document) - {"policy"} or not isinstance(document.get("policy", {}), dict):
        raise ScriptError(f"{path}: expected only a [policy] table")
    return cast(dict[str, Any], document.get("policy", {}))


def resolve(
    cli: dict[str, Any] | None = None, config: dict[str, Any] | None = None
) -> Policy:
    cli = {key: value for key, value in (cli or {}).items() if value is not None}
    config = config or {}
    unknown = (set(cli) | set(config)) - KEYS
    if unknown:
        raise ScriptError("unknown policy setting(s): " + ", ".join(sorted(unknown)))
    profile = cli.get("profile", config.get("profile", "default"))
    if profile not in PROFILES:
        raise ScriptError("profile must be default, ci, or risky")
    values = {**PROFILES[profile].document(), **config, **cli, "profile": profile}
    for key in ("allow", "caps"):
        value = values[key]
        if not isinstance(value, (list, tuple)) or any(
            not isinstance(item, str) for item in cast(list[object], value)
        ):
            raise ScriptError(f"{key} must be a string list")
        values[key] = tuple(sorted(set(cast(list[str], value))))
    for key in ("device_filter", "masked_paths", "no_new_privs"):
        if not isinstance(values[key], bool):
            raise ScriptError(f"{key} must be a boolean")
    for key in ("uid", "gid", "wall_timeout_ms", "pids_max", "memory_max"):
        value = values[key]
        if value is None and key in ("pids_max", "memory_max"):
            continue
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value < 0
            or (key in ("pids_max", "memory_max") and value == 0)
        ):
            raise ScriptError(
                f"{key} must be a positive integer (timeouts/uid/gid permit zero)"
            )
    if values["egress"] not in ("allow", "deny") or values["seccomp"] not in (
        "nvx-default",
        "unconfined",
    ):
        raise ScriptError("invalid egress or seccomp setting")
    normalized: list[str] = []
    for rule in values["allow"]:
        parts = rule.rsplit(":", 2)
        if len(parts) == 2:
            parts.insert(1, "tcp")
        if len(parts) != 3 or parts[1] not in ("tcp", "udp"):
            raise ScriptError("egress rule requires IP:PORT or IP:tcp|udp:PORT")
        try:
            ipaddress.ip_network(parts[0], strict=False)
            port = int(parts[2])
            if not 1 <= port <= 65535:
                raise ValueError("port out of range")
        except ValueError as error:
            raise ScriptError(
                "invalid numeric egress destination or port: " + rule
            ) from error
        normalized.append(f"{parts[0]}:{parts[1]}:{port}")
    values["allow"] = tuple(sorted(set(normalized)))
    if values["allow"] and (profile == "default" or values["egress"] != "deny"):
        raise ScriptError(
            "egress allow rules require --profile ci and default-deny egress"
        )
    if profile != "risky" and (
        values["uid"] == 0
        or values["gid"] == 0
        or (values["caps"] and not (profile == "ci" and values["caps"] == ("MKNOD",)))
        or not values["no_new_privs"]
        or not values["device_filter"]
        or not values["masked_paths"]
    ):
        raise ScriptError(
            "root identity, capabilities, and disabled isolation require --profile risky"
        )
    if values["caps"] not in ((), ("MKNOD",), ("ALL",)) or (
        values["caps"] == ("ALL",) and profile != "risky"
    ):
        raise ScriptError("supported capabilities are ci/MKNOD and risky/ALL")
    return Policy(**values)


def for_launch(args: argparse.Namespace) -> Policy:
    overrides: dict[str, Any] = {
        "profile": getattr(args, "profile", None),
        "seccomp": getattr(args, "seccomp", None),
        "caps": tuple(args.cap_add) if getattr(args, "cap_add", None) else None,
        "egress": args.network_egress,
        "allow": tuple(args.network_egress_allow)
        if args.network_egress_allow
        else None,
        "pids_max": getattr(args, "pids_max", None),
        "memory_max": getattr(args, "memory_max", None),
        "wall_timeout_ms": getattr(args, "exec_timeout_ms", None),
    }
    identity = getattr(args, "workload_user", None)
    if identity is not None:
        overrides.update(uid=identity[0], gid=identity[1])
    return resolve(overrides, read_config(getattr(args, "config", Path("nvx.toml"))))


def command(args: argparse.Namespace) -> None:
    policy = resolve({"profile": args.profile}, read_config(args.config))
    print(json.dumps(policy.document(), sort_keys=True, indent=2))
    if args.policy_operation == "lint":
        print("NVX-POLICY-LINT-OK")


def configure_parser(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="policy_operation", required=True)
    for name in ("show", "lint"):
        child = commands.add_parser(name)
        child.add_argument("--profile", choices=tuple(PROFILES))
        child.add_argument("--config", type=Path, default=Path("nvx.toml"))
        child.set_defaults(handler=command)
