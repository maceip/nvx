"""ID-based local managed operations, with byte-preserving copy and ordered logs."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any, cast

from . import receipt, sandbox_lifecycle
from .common import ScriptError
from .control_session import ManagedExecResult
from .events import read_events
from .image import ImageCache
from .image_run import workload_argv
from .locking import locked
from .output import write_stream


def resolve(identifier: str) -> Path:
    if re.fullmatch("[0-9a-f]{32}", identifier) is None:
        raise ScriptError(
            "instance ID must be the 32 hexadecimal characters printed by NVX-ID"
        )
    state = ImageCache().root / "instances" / identifier
    if (
        state.is_symlink()
        or not state.is_dir()
        or not (state / "config.json").is_file()
    ):
        raise ScriptError(f"unknown instance ID: {identifier}")
    return state


def event_path(identifier: str) -> Path:
    if re.fullmatch("[0-9a-f]{32}", identifier):
        return resolve(identifier) / "events.jsonl"
    path = Path(identifier)
    return path / "events.jsonl" if path.is_dir() else path


def command_events(args: argparse.Namespace) -> None:
    for event in read_events(event_path(args.id)):
        print(json.dumps(event, sort_keys=True))


def exec_guest(
    state: Path,
    argv: tuple[str, ...],
    timeout: float = 60,
    *,
    record: bool = True,
    output: Callable[[str, bytes], None] | None = None,
) -> ManagedExecResult:
    with locked(state / "operation.lock"):
        result = sandbox_lifecycle.exec_workload(
            state,
            argv,
            timeout_ms=int(timeout * 1000),
            response_timeout=timeout + 10,
            output=output,
        )
        sandbox_lifecycle.publish_json(
            state / f"exec-{secrets.token_hex(8)}.json",
            {
                "operation": "exec",
                "category": result.category,
                "status_code": result.returncode,
            },
        )
        if record:
            (state / "last_exec.json").write_text(
                json.dumps(
                    {"category": result.category, "status_code": result.returncode}
                )
                + "\n"
            )
        return result


def _checked(state: Path, argv: tuple[str, ...]) -> bytes:
    result = exec_guest(state, argv, record=False)
    if result.returncode:
        raise ScriptError(
            f"guest file operation failed with status {result.returncode}"
        )
    return result.stdout


def guest_path(value: str) -> str:
    if not value.startswith("/") or "\0" in value or ".." in PurePosixPath(value).parts:
        raise ScriptError("guest copy path must be absolute without parent components")
    return value


def download(
    state: Path, source: str, destination: Path, *, maximum: int = 256 << 20
) -> None:
    guest_path(source)
    if destination.exists() or destination.is_symlink():
        raise ScriptError("copy destination already exists")
    try:
        size = int(
            _checked(
                state,
                (
                    "/bin/sh",
                    "-c",
                    'test -f "$1" && test ! -L "$1" && stat -c %s "$1"',
                    "nvx",
                    source,
                ),
            ).strip()
        )
    except ValueError as error:
        raise ScriptError("guest file has invalid size") from error
    if not 0 <= size <= maximum:
        raise ScriptError("guest file exceeds copy size limit")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            offset = 0
            while offset < size:
                chunk = _checked(
                    state,
                    (
                        "/bin/sh",
                        "-c",
                        'dd if="$1" bs=65536 skip="$2" count=4 2>/dev/null',
                        "nvx",
                        source,
                        str(offset // 65536),
                    ),
                )
                if len(chunk) != min(262144, size - offset):
                    raise ScriptError("guest file changed during copy")
                output.write(chunk)
                offset += len(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def upload(state: Path, source: Path, destination: str) -> None:
    guest_path(destination)
    if (
        source.is_symlink()
        or not source.is_file()
        or source.stat().st_size > (256 << 20)
    ):
        raise ScriptError("copy source must be a regular file within the 256 MiB limit")
    stage = str(
        PurePosixPath(destination).parent / f".nvx-copy-{secrets.token_hex(16)}"
    )
    _checked(
        state,
        (
            "/bin/sh",
            "-c",
            'test ! -e "$1" && test ! -L "$1" && umask 077 && : >"$1"',
            "nvx",
            stage,
        ),
    )
    try:
        with source.open("rb") as input:
            while chunk := input.read(24576):
                encoded = base64.b64encode(chunk).decode()
                pieces = tuple(
                    encoded[index : index + 3000]
                    for index in range(0, len(encoded), 3000)
                )
                _checked(
                    state,
                    (
                        "/bin/sh",
                        "-c",
                        'file=$1; shift; printf %s "$@" | base64 -d >>"$file"',
                        "nvx",
                        stage,
                        *pieces,
                    ),
                )
        _checked(
            state,
            (
                "/bin/sh",
                "-c",
                'test ! -e "$2" && test ! -L "$2" && mv "$1" "$2"',
                "nvx",
                stage,
                destination,
            ),
        )
    finally:
        try:
            _checked(state, ("/bin/sh", "-c", 'rm -f "$1"', "nvx", stage))
        except (ScriptError, OSError, TimeoutError):
            pass  # Cleanup must preserve the original copy failure.


def status(state: Path) -> dict[str, Any]:
    runtime_path = state / "runtime.json"
    runtime: dict[str, Any] = (
        json.loads(runtime_path.read_bytes()) if runtime_path.exists() else {}
    )
    pid = int(runtime.get("pid", 0))
    running = sandbox_lifecycle.process_running(pid)
    image_path = state / "image.json"
    image: dict[str, Any] = (
        json.loads(image_path.read_bytes()) if image_path.exists() else {}
    )
    rss: int | None = None
    if running:
        if os.name == "nt":
            from .benchmark import windows_process_memory_counters

            rss = windows_process_memory_counters(pid).working_set_size
        else:
            measured = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True
            )
            if measured.returncode == 0 and measured.stdout.strip():
                rss = int(measured.stdout.strip()) * 1024
    return {
        "id": state.name,
        "state": "running" if running else "stopped",
        "pid": pid if running else None,
        "image": image.get("ref"),
        "rss_bytes": rss,
        "wall_ms": max(
            0, int((time.time() - runtime.get("started_epoch", time.time())) * 1000)
        )
        if running
        else None,
    }


def stop_instance(state: Path, timeout: float = 30) -> dict[str, Any]:
    with locked(state / "operation.lock"):
        if not (state / "runtime.json").exists() and (state / "receipt.json").exists():
            return cast(
                dict[str, Any], json.loads((state / "outcome.json").read_bytes())
            )
        outcome = sandbox_lifecycle.stop(state, timeout)
        ImageCache().release(state.name)
        from .secrets import finish

        finish(state, timeout)
        policy = json.loads((state / "policy.json").read_bytes())
        argv = tuple(json.loads((state / "argv.json").read_bytes()))
        last_path = state / "last_exec.json"
        last = (
            json.loads(last_path.read_bytes())
            if last_path.exists()
            else {"category": "interrupted", "status_code": 124}
        )
        if not (state / "resources.json").exists():
            sandbox_lifecycle.replace_json(
                state / "resources.json",
                {
                    "resource_version": 1,
                    "complete": False,
                    "reason": "workload interrupted before metrics",
                    "wall_ms": None,
                    "cpu_usec": None,
                    "memory_peak_bytes": None,
                    "pids_peak": None,
                    "memory_oom_kills": None,
                    "pids_denials": None,
                },
            )
        document = receipt.build(
            state, policy, argv, {"workload": last, "teardown": outcome}
        )
        settings_path = state / "finish.json"
        settings: dict[str, Any] = (
            json.loads(settings_path.read_bytes()) if settings_path.exists() else {}
        )
        if settings.get("signing_key"):
            receipt.sign(document, Path(settings["signing_key"]))
        receipt.publish(state / "receipt.json", document)
        if settings.get("receipt"):
            receipt.publish(Path(settings["receipt"]), document)
    return outcome


def command(args: argparse.Namespace) -> None:
    if args.registry_operation == "ps":
        rows: list[dict[str, Any]] = []
        for state in sorted((ImageCache().root / "instances").glob("*")):
            if (
                re.fullmatch("[0-9a-f]{32}", state.name) is None
                or not (state / "config.json").is_file()
            ):
                continue
            rows.append(status(state))
        print(json.dumps(rows, sort_keys=True))
        return
    if args.registry_operation == "cp":
        endpoints = [
            value.split(":", 1)[0]
            for value in (args.source, args.destination)
            if re.match(r"^[0-9a-f]{32}:/", value)
        ]
        if len(endpoints) != 1:
            raise ScriptError(
                "cp requires one local path and one ID:/absolute/guest/path"
            )
        args.id = endpoints[0]
    state = resolve(args.id)
    if args.registry_operation == "logs":
        printed = 0
        while True:
            events = read_events(state / "events.jsonl")
            for event in events[printed:]:
                print(
                    json.dumps(event, sort_keys=True)
                    if args.json
                    else f"{event['sequence']} {event['kind']} {json.dumps(event['fields'], sort_keys=True)}",
                    flush=True,
                )
            printed = len(events)
            if not args.follow or status(state)["state"] != "running":
                break
            time.sleep(0.1)
    elif args.registry_operation == "exec":
        supplied = tuple(args.argv)
        if supplied and supplied[0] == "--":
            supplied = supplied[1:]
        if not supplied:
            raise ScriptError("exec requires COMMAND [ARG...]")
        image = cast(dict[str, Any], json.loads((state / "image.json").read_bytes()))
        from .secrets import environment

        result = exec_guest(
            state,
            workload_argv(image["manifest"], supplied, environment(state)),
            args.timeout,
            output=write_stream if args.stream else None,
        )
        if not args.stream:
            sys.stdout.buffer.write(result.stdout)
            sys.stderr.buffer.write(result.stderr)
        raise SystemExit(result.returncode)
    elif args.registry_operation == "stop":
        outcome = stop_instance(state, args.timeout)
        print(json.dumps(outcome, sort_keys=True))
    else:
        prefix = args.id + ":"
        if args.source.startswith(prefix) and not args.destination.startswith(prefix):
            download(state, args.source[len(prefix) :], Path(args.destination))
        elif args.destination.startswith(prefix) and not args.source.startswith(prefix):
            upload(state, Path(args.source), args.destination[len(prefix) :])
        else:
            raise ScriptError(
                "cp requires one local path and one ID:/absolute/guest/path"
            )


def configure_parsers(subparsers: Any) -> None:
    for operation in ("exec", "cp", "logs", "ps", "stop"):
        parser = subparsers.add_parser(operation, help="managed instance " + operation)
        parser.set_defaults(handler=command, registry_operation=operation)
        if operation not in ("ps", "cp"):
            parser.add_argument("id")
        if operation == "exec":
            parser.add_argument("--timeout", type=float, default=60)
            parser.add_argument("--stream", action="store_true")
            parser.add_argument("argv", nargs=argparse.REMAINDER)
        elif operation == "cp":
            parser.add_argument("source")
            parser.add_argument("destination")
        elif operation == "logs":
            parser.add_argument("--json", action="store_true")
            parser.add_argument("--follow", action="store_true")
        elif operation == "stop":
            parser.add_argument("--timeout", type=float, default=60)
