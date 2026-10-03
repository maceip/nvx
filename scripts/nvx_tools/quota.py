"""Atomic admission before a VM, CPU, memory or snapshot resource is created."""

from __future__ import annotations

import threading
from dataclasses import dataclass

from .common import ScriptError


@dataclass(frozen=True)
class Limits:
    concurrent: int = 4
    cpus_per_sandbox: int = 1
    aggregate_cpus: int = 4
    memory_mib_per_sandbox: int = 1024
    aggregate_memory_mib: int = 2048
    wall_ms: int = 60_000
    snapshot_bytes: int = 4 << 30


class Quota:
    def __init__(self, limits: Limits | None = None):
        limits = limits or Limits()
        if any(type(value) is not int or value <= 0 for value in vars(limits).values()):
            raise ScriptError("quota limits must be positive integers")
        self.limits = limits
        self._lock = threading.Lock()
        self._sandboxes: dict[str, tuple[int, int]] = {}
        self._snapshots: dict[str, int] = {}

    def reserve(
        self, handle: str, memory_mib: int, cpus: int = 1, wall_ms: int = 60_000
    ) -> None:
        if any(
            type(value) is not int or value <= 0
            for value in (memory_mib, cpus, wall_ms)
        ):
            raise ScriptError("requested resources must be positive integers")
        with self._lock:
            if handle in self._sandboxes:
                raise ScriptError("quota handle is already reserved")
            limits = self.limits
            if (
                len(self._sandboxes) >= limits.concurrent
                or cpus > limits.cpus_per_sandbox
                or sum(item[0] for item in self._sandboxes.values()) + cpus
                > limits.aggregate_cpus
                or memory_mib > limits.memory_mib_per_sandbox
                or sum(item[1] for item in self._sandboxes.values()) + memory_mib
                > limits.aggregate_memory_mib
                or wall_ms > limits.wall_ms
            ):
                raise ScriptError("sandbox quota denied before spawning")
            self._sandboxes[handle] = (cpus, memory_mib)

    def release(self, handle: str) -> None:
        with self._lock:
            self._sandboxes.pop(handle, None)

    def reserve_snapshot(self, handle: str, size: int) -> None:
        if type(size) is not int or size <= 0:
            raise ScriptError("snapshot reservation must be positive")
        with self._lock:
            if (
                handle in self._snapshots
                or sum(self._snapshots.values()) + size > self.limits.snapshot_bytes
            ):
                raise ScriptError("snapshot quota denied before capture")
            self._snapshots[handle] = size

    def release_snapshot(self, handle: str) -> None:
        with self._lock:
            self._snapshots.pop(handle, None)

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "sandboxes": len(self._sandboxes),
                "cpus": sum(item[0] for item in self._sandboxes.values()),
                "memory_mib": sum(item[1] for item in self._sandboxes.values()),
                "snapshot_bytes": sum(self._snapshots.values()),
            }
