"""Portable MCP stdio and authenticated loopback HTTP over the managed CLI."""

from __future__ import annotations

import argparse
import base64
import hmac
import http.server
import json
import os
import queue
import re
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, cast

from . import registry, warm
from .build_constants import BuildConstants
from .common import ScriptError
from .events import redact
from .host import default_backend
from .image import ImageCache, canonical, digest
from .local_http import BoundedServer
from .policy import resolve
from .quota import Limits, Quota

PROTOCOL = "2025-06-18"
VERSION = "1.0"
MAX_MESSAGE = 1 << 20
TOOL_NAMES = (
    "nvx_run",
    "nvx_exec",
    "nvx_status",
    "nvx_snapshot",
    "nvx_files",
    "nvx_secrets",
)
Emit = Callable[[dict[str, Any]], None]


def tools() -> list[dict[str, Any]]:
    properties: dict[str, dict[str, Any]] = {
        "nvx_run": {
            "image": {"type": "string"},
            "argv": {"type": "array", "items": {"type": "string"}},
            "memory_mib": {"type": "integer"},
            "wall_ms": {"type": "integer"},
            "keep_alive": {"type": "boolean"},
            "secrets": {"type": "array", "items": {"type": "string"}},
            "scopes": {"type": "array", "items": {"type": "string"}},
            "handle": {"type": "string"},
        },
        "nvx_exec": {
            "id": {"type": "string"},
            "argv": {"type": "array", "items": {"type": "string"}},
            "wall_ms": {"type": "integer"},
            "handle": {"type": "string"},
        },
        "nvx_status": {
            "id": {"type": "string"},
            "operation": {"type": "string", "enum": ["list", "inspect", "stop"]},
        },
        "nvx_snapshot": {
            "image": {"type": "string"},
            "operation": {"type": "string", "enum": ["capture", "list", "remove"]},
            "id": {"type": "string"},
            "memory_mib": {"type": "integer"},
        },
        "nvx_files": {
            "id": {"type": "string"},
            "operation": {"type": "string", "enum": ["upload", "download"]},
            "guest": {"type": "string"},
            "local": {"type": "string"},
        },
        "nvx_secrets": {},
    }
    required = {
        "nvx_run": ["image", "argv"],
        "nvx_exec": ["id", "argv"],
        "nvx_files": ["id", "operation", "guest", "local"],
    }
    return [
        {
            "name": name,
            "description": name.removeprefix("nvx_")
            + " through the local managed sandbox",
            "inputSchema": {
                "type": "object",
                "properties": properties[name],
                "required": required.get(name, []),
                "additionalProperties": False,
            },
        }
        for name in TOOL_NAMES
    ]


class Scrubber:
    """Retain possible token prefixes so credentials split across frames cannot leak."""

    def __init__(self, values: tuple[str, ...]):
        self.values = tuple(value.encode() for value in values if value)
        self.pending = b""
        self.tail = max((len(value) - 1 for value in self.values), default=0)

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        raw = self.pending + data
        for value in self.values:
            raw = raw.replace(value, b"[redacted]")
        keep = 0 if final else min(len(raw), self.tail)
        self.pending = raw[-keep:] if keep else b""
        return raw[:-keep] if keep else raw


class InstanceMarker:
    """Consume one host CLI identity line without interpreting workload stderr."""

    def __init__(self, identifier: str | None = None):
        self.identifier = identifier
        self.pending = b""
        self.line_start = True

    def feed(self, data: bytes, *, final: bool = False) -> bytes:
        if self.identifier is not None:
            return data
        self.pending += data
        output = bytearray()
        while self.pending:
            end = self.pending.find(b"\n")
            if end >= 0:
                line, self.pending = self.pending[: end + 1], self.pending[end + 1 :]
                match = (
                    re.fullmatch(rb"NVX-ID: ([0-9a-f]{32})\r?\n", line)
                    if self.line_start
                    else None
                )
                self.line_start = True
                if match is not None:
                    self.identifier = match[1].decode()
                    output.extend(self.pending)
                    self.pending = b""
                    break
                output.extend(line)
            else:
                prefix = b"NVX-ID: "
                possible = prefix.startswith(self.pending) or (
                    self.pending.startswith(prefix)
                    and re.fullmatch(rb"[0-9a-f]{0,32}\r?", self.pending[len(prefix) :])
                    is not None
                )
                if self.line_start and possible and not final:
                    break
                output.extend(self.pending)
                self.pending = b""
                self.line_start = False
        return bytes(output)


@dataclass
class Job:
    fingerprint: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    result: dict[str, Any] | None = None


class Service:
    def __init__(
        self,
        backend: str,
        root: Path,
        limits: Limits | None = None,
        secret_names: tuple[str, ...] = (),
    ):
        self.backend = backend
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.files_root = self.root / "files"
        self.files_root.mkdir(mode=0o700, exist_ok=True)
        self.quota = Quota(limits)
        snapshots = self.root / "snapshots"
        if snapshots.is_dir():
            for snapshot in snapshots.iterdir():
                if (snapshot / "warm.json").is_file():
                    doc = warm.admit(snapshot, backend)
                    self.quota.reserve_snapshot(
                        snapshot.name,
                        (int(doc["config"]["memory_mib"]) << 20) + (256 << 20),
                    )
        self.secret_names = secret_names
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._requests: dict[str, Job] = {}
        self._owned: dict[str, tuple[str, float]] = {}
        self._versions: dict[str, str] = {}
        self._initialized: set[str] = set()
        self._closed = threading.Event()
        self._monitor = threading.Thread(target=self._expire, daemon=True)
        self._monitor.start()

    def _expire(self) -> None:
        while not self._closed.wait(0.1):
            with self._lock:
                expired = [
                    identifier
                    for identifier, (_, deadline) in self._owned.items()
                    if time.monotonic() >= deadline
                ]
            for identifier in expired:
                try:
                    self.stop(identifier)
                except (ScriptError, OSError, TimeoutError):
                    pass

    def own(self, identifier: str) -> Path:
        with self._lock:
            if identifier not in self._owned:
                raise ScriptError("instance is not owned by this MCP server")
        return registry.resolve(identifier)

    def stop(self, identifier: str) -> dict[str, Any]:
        state = self.own(identifier)
        args = argparse.Namespace(registry_operation="stop", id=identifier, timeout=10)
        # Stop writes its result to stdout; keep protocol output on its own transport.
        command = [
            sys.executable,
            str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
            "stop",
            identifier,
            "--timeout",
            str(args.timeout),
        ]
        completed = subprocess.run(command, capture_output=True, timeout=20)
        if completed.returncode:
            raise ScriptError(
                "managed stop failed; retained instance evidence is available"
            )
        with self._lock:
            record = self._owned.pop(identifier, None)
        if record is None:
            return {"id": state.name, "state": "stopped"}
        handle, _ = record
        self.quota.release(handle)
        return {"id": state.name, "state": "stopped"}

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            for job in self._jobs.values():
                job.cancel.set()
            identifiers = list(self._owned)
        for identifier in identifiers:
            try:
                self.stop(identifier)
            except (ScriptError, OSError, TimeoutError):
                pass

    def _program(
        self,
        command: list[str],
        job: Job,
        emit: Emit,
        progress: object,
        values: tuple[str, ...],
        deadline: float,
        identifier_hint: str | None = None,
    ) -> dict[str, Any]:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdout is not None and process.stderr is not None
        frames: queue.Queue[tuple[str, bytes]] = queue.Queue(maxsize=64)

        def read(stream: str, pipe: BinaryIO) -> None:
            try:
                while data := os.read(pipe.fileno(), 32768):
                    frames.put((stream, data))
            finally:
                pipe.close()
                frames.put((stream, b""))

        for stream, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            threading.Thread(target=read, args=(stream, pipe), daemon=True).start()
        scrubbers = {stream: Scrubber(values) for stream in ("stdout", "stderr")}
        buffers = {stream: bytearray() for stream in scrubbers}
        closed: set[str] = set()
        identifier: str | None = identifier_hint
        marker = InstanceMarker(identifier_hint)
        completed = False
        progress_count = 0
        cancelled = False
        try:
            while len(closed) < 2:
                if not cancelled and (
                    job.cancel.is_set() or time.monotonic() >= deadline
                ):
                    cancelled = True
                    process.terminate()
                try:
                    stream, data = frames.get(timeout=0.1)
                except queue.Empty:
                    continue
                if not data:
                    closed.add(stream)
                safe = scrubbers[stream].feed(data, final=not data)
                if stream == "stderr":
                    safe = marker.feed(safe, final=not data)
                    identifier = marker.identifier
                buffers[stream].extend(safe)
                if len(buffers[stream]) > (2 << 20):
                    raise ScriptError("MCP operation exceeded its output bound")
                if safe and progress is not None:
                    progress_count += 1
                    emit(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/progress",
                            "params": {
                                "progressToken": progress,
                                "progress": progress_count,
                                "message": json.dumps(
                                    {
                                        "stream": stream,
                                        "data": base64.b64encode(safe).decode(),
                                    }
                                ),
                            },
                        }
                    )
            status = process.wait(timeout=10)
            completed = True
            if cancelled:
                if identifier is not None:
                    subprocess.run(
                        [
                            sys.executable,
                            str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
                            "stop",
                            identifier,
                            "--timeout",
                            "5",
                        ],
                        capture_output=True,
                        timeout=15,
                    )
                return {
                    "id": identifier,
                    "returncode": 124,
                    "category": "cancelled" if job.cancel.is_set() else "timeout",
                    "stdout": base64.b64encode(buffers["stdout"]).decode(),
                    "stderr": base64.b64encode(buffers["stderr"]).decode(),
                }
            return {
                "id": identifier,
                "returncode": status,
                "category": "exit",
                "stdout": base64.b64encode(buffers["stdout"]).decode(),
                "stderr": base64.b64encode(buffers["stderr"]).decode(),
            }
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            if not completed and identifier is not None:
                subprocess.run(
                    [
                        sys.executable,
                        str(BuildConstants.REPO_ROOT / "scripts/nvx.py"),
                        "stop",
                        identifier,
                        "--timeout",
                        "5",
                    ],
                    capture_output=True,
                    timeout=15,
                )

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        job: Job,
        emit: Emit,
        progress: object,
    ) -> dict[str, Any]:
        schema = next(
            (item["inputSchema"] for item in tools() if item["name"] == name), None
        )
        if (
            schema is None
            or set(arguments) - set(schema["properties"])
            or set(schema["required"]) - set(arguments)
        ):
            raise ScriptError("unknown tool or unsupported/missing argument")
        for key, value in arguments.items():
            expected = schema["properties"][key]["type"]
            if (
                "enum" in schema["properties"][key]
                and value not in schema["properties"][key]["enum"]
            ):
                raise ScriptError("invalid argument value: " + key)
            if (
                (expected == "string" and not isinstance(value, str))
                or (expected == "integer" and type(value) is not int)
                or (expected == "boolean" and type(value) is not bool)
                or (
                    expected == "array"
                    and (
                        not isinstance(value, list)
                        or any(
                            not isinstance(item, str)
                            for item in cast(list[object], value)
                        )
                    )
                )
            ):
                raise ScriptError("invalid argument type: " + key)
        cli = [sys.executable, str(BuildConstants.REPO_ROOT / "scripts/nvx.py")]
        if name in ("nvx_run", "nvx_exec"):
            argv = arguments["argv"]
            if not argv:
                raise ScriptError("argv must not be empty")
            wall = arguments.get("wall_ms", self.quota.limits.wall_ms)
            if not 1 <= wall <= self.quota.limits.wall_ms:
                raise ScriptError("wall quota exceeded")
            handle = arguments.get("handle", uuid.uuid4().hex)
            selected = tuple(arguments.get("secrets", []))
            if set(selected) - set(self.secret_names):
                raise ScriptError("secret name is not admitted by this server")
            values = tuple(os.environ[name] for name in selected if name in os.environ)
            retained = False
            if name == "nvx_run":
                memory = arguments.get("memory_mib", 512)
                self.quota.reserve(handle, memory, wall_ms=wall)
                command = [
                    *cli,
                    "run",
                    "--image",
                    arguments["image"],
                    "--hypervisor",
                    self.backend,
                    "--profile",
                    "default",
                    "--memory-mib",
                    str(memory),
                    "--memory-max",
                    str(min(memory << 20, 256 << 20)),
                    "--exec-timeout-ms",
                    str(wall),
                    "--stream",
                ]
                if arguments.get("keep_alive", False):
                    command.append("--keep-alive")
                for secret in selected:
                    command.extend(["--secret", secret])
                for scope in arguments.get("scopes", []):
                    command.extend(["--egress-allow", scope])
            else:
                self.own(arguments["id"])
                command = [
                    *cli,
                    "exec",
                    "--stream",
                    "--timeout",
                    str(wall / 1000),
                    arguments["id"],
                ]
            try:
                result = self._program(
                    [*command, "--", *argv],
                    job,
                    emit,
                    progress,
                    values,
                    time.monotonic() + wall / 1000,
                    identifier_hint=arguments["id"] if name == "nvx_exec" else None,
                )
                if (
                    name == "nvx_run"
                    and arguments.get("keep_alive")
                    and result["id"]
                    and result["category"] == "exit"
                    and registry.status(registry.resolve(result["id"]))["state"]
                    == "running"
                ):
                    with self._lock:
                        self._owned[result["id"]] = (
                            handle,
                            time.monotonic() + wall / 1000,
                        )
                    retained = True
                if name == "nvx_exec" and result["category"] in (
                    "cancelled",
                    "timeout",
                ):
                    with self._lock:
                        owner = self._owned.pop(arguments["id"], None)
                    if owner is not None:
                        self.quota.release(owner[0])
                return result
            finally:
                if name == "nvx_run" and not retained:
                    self.quota.release(handle)
        if name == "nvx_status":
            operation = arguments.get("operation", "list")
            if operation == "stop":
                return self.stop(arguments["id"])
            if operation == "inspect":
                return registry.status(self.own(arguments["id"]))
            with self._lock:
                identifiers = list(self._owned)
            return {
                "instances": [
                    registry.status(registry.resolve(identifier))
                    for identifier in identifiers
                ],
                "quota": self.quota.counts(),
            }
        if name == "nvx_files":
            state = self.own(arguments["id"])
            relative = Path(arguments["local"])
            path = (self.files_root / relative).resolve()
            if relative.is_absolute() or not path.is_relative_to(self.files_root):
                raise ScriptError("local file must be beneath the MCP files directory")
            if arguments["operation"] == "upload":
                registry.upload(state, path, arguments["guest"])
            elif arguments["operation"] == "download":
                registry.download(state, arguments["guest"], path)
            else:
                raise ScriptError("unsupported file operation")
            return {
                "id": state.name,
                "local": str(relative),
                "guest": arguments["guest"],
            }
        if name == "nvx_secrets":
            return {
                "names": [
                    {"name": name, "available": bool(os.environ.get(name))}
                    for name in self.secret_names
                ]
            }
        if name == "nvx_snapshot":
            operation = arguments.get("operation", "list")
            directory = self.root / "snapshots"
            directory.mkdir(mode=0o700, exist_ok=True)
            if operation == "list":
                return {
                    "snapshots": [
                        path.name
                        for path in directory.iterdir()
                        if (path / "warm.json").is_file()
                    ]
                }
            if operation == "capture":
                identifier = uuid.uuid4().hex
                memory = arguments.get("memory_mib", 512)
                self.quota.reserve_snapshot(identifier, (memory << 20) + (256 << 20))
                try:
                    self.quota.reserve(identifier, memory)
                    warm.capture(
                        arguments["image"],
                        self.backend,
                        directory / identifier,
                        resolve({"profile": "default"}),
                        memory_mib=memory,
                        cancel=job.cancel,
                    )
                    return {
                        "id": identifier,
                        "architecture_bound": True,
                        "backend": self.backend,
                    }
                except BaseException:
                    self.quota.release_snapshot(identifier)
                    raise
                finally:
                    self.quota.release(identifier)
            identifier = arguments["id"]
            if re.fullmatch("[0-9a-f]{32}", identifier) is None:
                raise ScriptError("invalid snapshot ID")
            document = warm.admit(directory / identifier)
            ImageCache().release(document["owner"] + "-snapshot")
            import shutil

            shutil.rmtree(directory / identifier)
            self.quota.release_snapshot(identifier)
            return {"id": identifier, "removed": True}
        raise ScriptError("unsupported tool")

    def handle(
        self, request: dict[str, Any], emit: Emit, client: str = "stdio"
    ) -> dict[str, Any] | None:
        identifier = request.get("id")
        method = request.get("method")
        if (
            request.get("jsonrpc") != "2.0"
            or not isinstance(method, str)
            or (identifier is not None and type(identifier) not in (str, int))
        ):
            return {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32600, "message": "invalid request"},
            }
        params = request.get("params", {})
        if not isinstance(params, dict):
            return {
                "jsonrpc": "2.0",
                "id": identifier,
                "error": {"code": -32602, "message": "params must be an object"},
            }
        params = cast(dict[str, Any], params)
        try:
            if method == "initialize":
                version = params.get("protocolVersion")
                if not isinstance(version, str):
                    raise ScriptError("initialize requires protocolVersion")
                self._versions[client] = PROTOCOL
                result: dict[str, Any] = {
                    "protocolVersion": PROTOCOL,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "nvx", "version": VERSION},
                }
            elif method == "notifications/initialized":
                if client not in self._versions:
                    raise ScriptError("initialize first")
                self._initialized.add(client)
                return None
            elif method == "notifications/cancelled":
                with self._lock:
                    job = self._requests.get(
                        client + ":" + str(params.get("requestId"))
                    )
                    if job is not None:
                        job.cancel.set()
                return None
            elif method == "ping":
                result = {}
            elif client not in self._initialized:
                raise ScriptError("complete MCP initialization before calling tools")
            elif method == "tools/list":
                result = {"tools": tools()}
            elif method == "tools/call":
                name = params.get("name")
                arguments = params.get("arguments", {})
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ScriptError("tool call requires a name and argument object")
                arguments = cast(dict[str, Any], arguments)
                handle = arguments.get("handle", uuid.uuid4().hex)
                if (
                    not isinstance(handle, str)
                    or re.fullmatch("[A-Za-z0-9_-]{1,128}", handle) is None
                ):
                    raise ScriptError("invalid idempotence handle")
                fingerprint = digest(canonical({"name": name, "arguments": arguments}))
                with self._lock:
                    job = self._jobs.get(handle)
                    duplicate = job is not None
                    if job is None:
                        if len(self._jobs) >= 512:
                            raise ScriptError(
                                "MCP operation history is full; restart after stopping owned instances"
                            )
                        job = Job(fingerprint)
                        self._jobs[handle] = job
                    elif job.fingerprint != fingerprint:
                        raise ScriptError(
                            "idempotence handle was reused with different arguments"
                        )
                    self._requests[client + ":" + str(identifier)] = job
                if duplicate:
                    if not job.done.wait(self.quota.limits.wall_ms / 1000 + 30):
                        raise ScriptError("idempotent operation has not completed")
                else:
                    try:
                        metadata = params.get("_meta", {})
                        progress = (
                            cast(dict[str, Any], metadata).get("progressToken")
                            if isinstance(metadata, dict)
                            else None
                        )
                        value = self.call(name, arguments, job, emit, progress)
                        job.result = {
                            "content": [
                                {
                                    "type": "text",
                                    "text": json.dumps(value, sort_keys=True),
                                }
                            ],
                            "isError": False,
                        }
                    except (
                        ScriptError,
                        ValueError,
                        KeyError,
                        OSError,
                        TimeoutError,
                    ) as error:
                        job.result = {
                            "content": [
                                {"type": "text", "text": str(redact(str(error)))}
                            ],
                            "isError": True,
                        }
                    finally:
                        job.done.set()
                assert job.result is not None
                result = job.result
            else:
                return {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": -32601, "message": "method not found"},
                }
            return (
                None
                if identifier is None
                else {"jsonrpc": "2.0", "id": identifier, "result": result}
            )
        except (ScriptError, ValueError, KeyError, OSError) as error:
            return (
                None
                if identifier is None
                else {
                    "jsonrpc": "2.0",
                    "id": identifier,
                    "error": {"code": -32602, "message": str(redact(str(error)))},
                }
            )


def http_server(
    service: Service, token: str, port: int = 0
) -> http.server.ThreadingHTTPServer:
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.send_error(405)

        def do_POST(self) -> None:
            self.connection.settimeout(10)
            if (
                self.path != "/mcp"
                or self.headers.get("Host")
                != f"127.0.0.1:{cast(http.server.HTTPServer, self.server).server_port}"
                or self.headers.get("Origin") is not None
                or not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + token
                )
            ):
                self.send_error(403)
                return
            version = self.headers.get("MCP-Protocol-Version")
            if version is not None and version != PROTOCOL:
                self.send_error(400)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if (
                    self.headers.get("Transfer-Encoding") is not None
                    or len(self.headers.get_all("Content-Length", [])) != 1
                    or not 0 < length <= MAX_MESSAGE
                ):
                    raise ValueError("invalid message length")
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError("request must be an object")
            except (ValueError, OSError):
                self.send_error(400)
                return
            if "id" not in request:
                service.handle(
                    cast(dict[str, Any], request), lambda value: None, "http"
                )
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()

            def emit(value: dict[str, Any]) -> None:
                try:
                    self.wfile.write(
                        b"event: message\ndata: " + canonical(value) + b"\n\n"
                    )
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionError, OSError):
                    pass  # Disconnect is not cancellation in this protocol version.

            result = service.handle(cast(dict[str, Any], request), emit, "http")
            if result is not None:
                emit(result)
            self.close_connection = True

    return BoundedServer(("127.0.0.1", port), Handler)


def command(args: argparse.Namespace) -> None:
    service = Service(
        args.backend,
        args.state_dir,
        Limits(
            args.max_concurrent,
            1,
            args.max_cpus,
            args.max_memory_mib,
            args.aggregate_memory_mib,
            args.max_wall_ms,
            args.snapshot_budget_mib << 20,
        ),
        tuple(args.secret_name),
    )

    def interrupted(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous_signal = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.transport == "http":
            token_path = args.state_dir / "http.capability"
            fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            token = secrets.token_hex(32)
            with os.fdopen(fd, "w") as output:
                output.write(token)
            server = http_server(service, token, args.port)
            print(
                json.dumps(
                    {
                        "url": f"http://127.0.0.1:{server.server_port}/mcp",
                        "capability_file": str(token_path.resolve()),
                        "protocolVersion": PROTOCOL,
                    }
                ),
                flush=True,
            )
            try:
                server.serve_forever()
            finally:
                service.close()
                server.server_close()
                token_path.unlink(missing_ok=True)
        else:
            lock = threading.Lock()
            slots = threading.BoundedSemaphore(32)
            threads: list[threading.Thread] = []

            def emit(value: dict[str, Any]) -> None:
                with lock:
                    sys.stdout.buffer.write(canonical(value) + b"\n")
                    sys.stdout.buffer.flush()

            def handle(raw: bytes) -> None:
                try:
                    value = json.loads(raw)
                    result = (
                        service.handle(cast(dict[str, Any], value), emit)
                        if isinstance(value, dict)
                        else {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32600, "message": "invalid request"},
                        }
                    )
                    if result is not None:
                        emit(result)
                except ValueError:
                    emit(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": -32700, "message": "parse error"},
                        }
                    )
                finally:
                    slots.release()

            while raw := sys.stdin.buffer.readline(MAX_MESSAGE + 1):
                if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                    raise ScriptError("MCP stdio message exceeds the 1 MiB limit")
                slots.acquire()
                thread = threading.Thread(target=handle, args=(raw,))
                threads = [active for active in threads if active.is_alive()]
                threads.append(thread)
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    parsed = {}
                if (
                    isinstance(parsed, dict)
                    and cast(dict[str, Any], parsed).get("method") != "tools/call"
                ):
                    threads.pop()
                    handle(raw)
                else:
                    thread.start()
            service.close()
            for thread in threads:
                thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        service.close()
        signal.signal(signal.SIGTERM, previous_signal)


def configure_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("mcp", help="portable local MCP integration")
    operations = parser.add_subparsers(required=True)
    child = operations.add_parser("serve")
    child.set_defaults(handler=command)
    child.add_argument("--transport", choices=("stdio", "http"), default="stdio")
    child.add_argument(
        "--backend", choices=("hvf", "kvm", "mshv", "whp"), default=default_backend()
    )
    child.add_argument("--state-dir", type=Path, default=ImageCache().root / "mcp")
    child.add_argument("--port", type=int, default=0)
    child.add_argument("--secret-name", action="append", default=[])
    for name, default in (
        ("max-concurrent", 4),
        ("max-cpus", 4),
        ("max-memory-mib", 1024),
        ("aggregate-memory-mib", 2048),
        ("max-wall-ms", 60000),
        ("snapshot-budget-mib", 4096),
    ):
        child.add_argument("--" + name, type=int, default=default)
