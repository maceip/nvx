"""Translate recorded policy denials into actionable diagnostics."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from .events import read_events


def explain(event: Mapping[str, object]) -> str:
    raw = event.get("fields", {})
    fields = cast(dict[str, object], raw) if isinstance(raw, dict) else {}
    if event.get("kind") == "network.denied":
        destination = fields.get("dst", "unknown")
        port = fields.get("port", "unknown")
        return (
            f"blocked: egress to {destination}:{port} is outside the allowlist; "
            f"use --profile ci --egress-allow {destination}:{port}"
        )
    if event.get("kind") == "resource.denied":
        resource = fields.get("resource", "unknown")
        flag = {
            "pids": "--pids-max",
            "memory": "--memory-max",
            "wall": "--exec-timeout-ms",
        }.get(str(resource))
        if flag:
            return f"blocked: {resource} limit reached; increase {flag}"
    if event.get("kind") == "filesystem.denied":
        return f"blocked: {fields.get('path', 'unknown')} is outside the declared workspace"
    return "no explanation available for this event"


def command_explain(args: argparse.Namespace) -> None:
    from .registry import event_path

    path = event_path(str(args.id))
    for event in read_events(path):
        if str(event.get("kind", "")).endswith(".denied"):
            print(explain(event))


def configure_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "id", type=Path, help="run state directory or events JSONL path"
    )
    parser.set_defaults(handler=command_explain)
