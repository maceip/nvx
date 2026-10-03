"""Bounded, host-owned run events. Credentials are removed before serialization."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import cast

from .common import ScriptError
from .locking import locked

MAX_FIELD_BYTES = 4096
MAX_EVENT_BYTES = 65536
_SECRET_KEY = re.compile(
    r"secret|token|password|authorization|api.?key|credential", re.I
)
_TOKEN = re.compile(
    r"(?:sk[-_][A-Za-z0-9_-]{8,}|Bearer\s+\S+|"
    r"(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]+|"
    r"AKIA[A-Z0-9]{16}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.I,
)


def redact(value: object, *, secrets: tuple[str, ...] = (), depth: int = 0) -> object:
    if depth > 12:
        return "[truncated]"
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = _TOKEN.sub("[redacted]", value)
        raw = value.encode("utf-8")
        if len(raw) > MAX_FIELD_BYTES:
            return (
                raw[:MAX_FIELD_BYTES].decode("utf-8", errors="ignore") + "[truncated]"
            )
        return value
    if isinstance(value, dict):
        return {
            str(key): "[redacted]"
            if _SECRET_KEY.search(str(key))
            else redact(item, secrets=secrets, depth=depth + 1)
            for key, item in list(cast(dict[object, object], value).items())[:128]
        }
    if isinstance(value, (list, tuple)):
        return [
            redact(item, secrets=secrets, depth=depth + 1)
            for item in list(cast("list[object] | tuple[object, ...]", value))[:128]
        ]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    raise ScriptError(f"unsupported event field type: {type(value).__name__}")


class EventLog:
    def __init__(self, path: Path, instance_id: str, *, secrets: tuple[str, ...] = ()):
        self.path = path
        self.instance_id = instance_id
        self.secrets = secrets
        self._lock = threading.Lock()
        self._sequence = 0
        if path.exists():
            previous = read_events(path)
            if previous:
                if previous[-1].get("instance_id") != instance_id:
                    raise ScriptError("event log belongs to another instance")
                self._sequence = int(str(previous[-1]["sequence"]))

    def emit(self, kind: str, **fields: object) -> dict[str, object]:
        with self._lock, locked(self.path.with_suffix(".lock")):
            if self.path.exists():
                previous = read_events(self.path)
                if previous:
                    if previous[-1]["instance_id"] != self.instance_id:
                        raise ScriptError("event log belongs to another instance")
                    self._sequence = int(str(previous[-1]["sequence"]))
            event = {
                "event_version": 1,
                "instance_id": self.instance_id,
                "sequence": self._sequence + 1,
                "time_ns": time.time_ns(),
                "kind": kind,
                "fields": redact(fields, secrets=self.secrets),
            }
            raw = (json.dumps(event, sort_keys=True, allow_nan=False) + "\n").encode()
            if len(raw) > MAX_EVENT_BYTES:
                raise ScriptError("event exceeds the 64 KiB limit")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags, 0o600)
            try:
                if os.write(descriptor, raw) != len(raw):
                    raise ScriptError("incomplete event write")
            finally:
                os.close(descriptor)
            self._sequence += 1
            return event


def read_events(path: Path) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    with path.open("rb") as source:
        while raw := source.readline(MAX_EVENT_BYTES + 1):
            if len(raw) > MAX_EVENT_BYTES or not raw.endswith(b"\n"):
                raise ScriptError(
                    "event log contains an oversized or incomplete record"
                )
            value: object = json.loads(raw)
            if not isinstance(value, dict):
                raise ScriptError("event record must be an object")
            event = cast(dict[str, object], value)
            if event.get("event_version") != 1:
                raise ScriptError("unsupported event version")
            if (
                type(event.get("sequence")) is not int
                or event["sequence"] != len(events) + 1
                or not isinstance(event.get("instance_id"), str)
                or (events and event["instance_id"] != events[0]["instance_id"])
            ):
                raise ScriptError("event log has invalid ordering or instance identity")
            events.append(event)
    return events
