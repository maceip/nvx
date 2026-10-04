"""Local hot clone pool: each lease is used once, retired, and replaced."""

from __future__ import annotations

import argparse
import base64
import hmac
import http.client
import http.server
import json
import os
import queue
import secrets
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from . import registry, warm
from . import sandbox_lifecycle as lifecycle
from .build_constants import BuildConstants
from .common import ScriptError
from .host import default_backend
from .image import ImageCache, canonical
from .image_run import workload_argv
from .local_http import BoundedServer
from .output import write_stream
from .policy import resolve
from .quota import Limits, Quota


class Accounting:
    def __init__(self, states: list[Path]):
        if len({state.name for state in states}) != len(states):
            raise ScriptError("pool refused duplicate clone identities")
        self._lock = threading.Lock()
        self.ready = list(states)
        self.leased: set[str] = set()
        self.retired: set[str] = set()

    def lease(self) -> Path:
        with self._lock:
            if not self.ready:
                raise ScriptError("pool has no ready clone; retry after refill")
            state = self.ready.pop(0)
            self.leased.add(state.name)
            return state

    def retire(self, state: Path) -> None:
        with self._lock:
            if state.name not in self.leased:
                raise ScriptError("clone is not leased by this pool")
            self.leased.remove(state.name)
            self.retired.add(state.name)

    def add(self, state: Path) -> None:
        with self._lock:
            if (
                state.name in self.leased
                or state.name in self.retired
                or any(ready.name == state.name for ready in self.ready)
            ):
                raise ScriptError("pool refused a previously used clone")
            self.ready.append(state)

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "ready": len(self.ready),
                "leased": len(self.leased),
                "retired": len(self.retired),
            }


def _close(state: Path) -> None:
    try:
        if (state / "last_exec.json").exists():
            registry.stop_instance(state, 30)
        else:
            lifecycle.stop(state, 30)
    finally:
        ImageCache().release(state.name)


def serve(state: Path) -> None:
    settings = json.loads((state / "config.json").read_bytes())
    template = Path(settings["template"])
    document = warm.admit(template)
    size = int(settings["size"])
    quota = Quota(
        Limits(
            concurrent=size,
            aggregate_cpus=size,
            aggregate_memory_mib=settings["memory_budget_mib"],
        )
    )
    memory = int(document["config"]["memory_mib"])
    # Reserve the whole pool before the first clone; denial cannot spawn a VM.
    for index in range(size):
        quota.reserve(str(index), memory)
    clones: list[Path] = []
    closing = threading.Event()
    lock = threading.Lock()
    refills: list[threading.Thread] = []

    def interrupted(signum: int, frame: object) -> None:
        raise KeyboardInterrupt

    previous_signal = signal.signal(signal.SIGTERM, interrupted)
    server: BoundedServer | None = None
    try:
        for _ in range(size):
            clones.append(warm.clone(template))
        accounting = Accounting(clones)
        capability = (state / "capability").read_text()

        def refill(old: Path) -> None:
            try:
                accounting.retire(old)
                _close(old)
                if not closing.is_set():
                    new = warm.clone(template)
                    with lock:
                        clones.append(new)
                    if closing.is_set():
                        _close(new)
                    else:
                        accounting.add(new)
            except (ScriptError, OSError, TimeoutError) as error:
                (state / ("refill-error-" + old.name + ".txt")).write_text(str(error))

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                self.connection.settimeout(10)
                if not hmac.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + capability
                ):
                    self.send_error(403)
                    return
                clone: Path | None = None
                streaming = False
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if (
                        self.path != "/"
                        or self.headers.get("Origin") is not None
                        or self.headers.get("Transfer-Encoding") is not None
                        or len(self.headers.get_all("Content-Length", [])) != 1
                        or not 0 < length <= 65536
                    ):
                        raise ScriptError("invalid pool request length")
                    request = json.loads(self.rfile.read(length))
                    if request.get("operation") == "status":
                        result: dict[str, Any] = accounting.counts()
                    elif request.get("operation") == "stop":
                        closing.set()
                        result = {"stopping": True}
                        assert server is not None
                        threading.Thread(target=server.shutdown, daemon=True).start()
                    elif request.get("operation") == "run":
                        arguments: object = request["argv"]
                        if (
                            not isinstance(arguments, list)
                            or not arguments
                            or any(
                                not isinstance(item, str)
                                for item in cast(list[object], arguments)
                            )
                        ):
                            raise ScriptError(
                                "pool argv requires a nonempty string list"
                            )
                        started = time.perf_counter_ns()
                        clone = accounting.lease()
                        image = json.loads((clone / "image.json").read_bytes())
                        command = workload_argv(
                            image["manifest"], tuple(cast(list[str], arguments))
                        )
                        (clone / "argv.json").write_bytes(canonical(list(command)))
                        streaming = request.get("stream") is True
                        if streaming:
                            self.send_response(200)
                            self.send_header("Content-Type", "application/x-ndjson")
                            self.send_header("Connection", "close")
                            self.end_headers()

                        def emit(stream: str, data: bytes) -> None:
                            try:
                                self.wfile.write(
                                    canonical(
                                        {
                                            "stream": stream,
                                            "data": base64.b64encode(data).decode(),
                                        }
                                    )
                                    + b"\n"
                                )
                                self.wfile.flush()
                            except OSError:
                                pass

                        executed = registry.exec_guest(
                            clone,
                            command,
                            max(1, document["policy"]["wall_timeout_ms"] / 1000),
                            output=emit if streaming else None,
                        )
                        elapsed = (time.perf_counter_ns() - started) / 1_000_000
                        result = {
                            "id": clone.name,
                            "returncode": executed.returncode,
                            "category": executed.category,
                            "stdout": base64.b64encode(executed.stdout).decode(),
                            "stderr": base64.b64encode(executed.stderr).decode(),
                            "request_to_completion_ms": elapsed,
                        }
                    else:
                        raise ScriptError("unknown pool operation")
                    if streaming:
                        self.wfile.write(canonical({"result": result}) + b"\n")
                        self.wfile.flush()
                        self.close_connection = True
                        return
                    raw = canonical(result)
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (
                    ScriptError,
                    ValueError,
                    KeyError,
                    OSError,
                    TimeoutError,
                ) as error:
                    raw = canonical({"error": str(error)})
                    if streaming:
                        try:
                            self.wfile.write(raw + b"\n")
                        except OSError:
                            pass
                        return
                    self.send_response(409)
                    self.send_header("Content-Length", str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                finally:
                    if clone is not None:
                        thread = threading.Thread(target=refill, args=(clone,))
                        with lock:
                            refills.append(thread)
                            thread.start()

        server = BoundedServer(("127.0.0.1", 0), Handler)
        lifecycle.replace_json(
            state / "runtime.json", {"pid": os.getpid(), "port": server.server_port}
        )
        print("NVX-POOL-READY", flush=True)
        server.serve_forever(poll_interval=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        closing.set()
        if server is not None:
            # Close admission and join handlers before inspecting their refill list.
            server.server_close()
        for thread in refills:
            thread.join()
        signal.signal(signal.SIGTERM, previous_signal)
        for clone in clones:
            if (clone / "runtime.json").exists():
                try:
                    _close(clone)
                except (ScriptError, OSError, TimeoutError):
                    pass
        (state / "runtime.json").unlink(missing_ok=True)
        (state / "capability").unlink(missing_ok=True)


def request(
    identifier: str,
    operation: str,
    argv: tuple[str, ...] = (),
    *,
    output: Callable[[str, bytes], None] | None = None,
) -> dict[str, Any]:
    if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
        raise ScriptError("invalid pool ID")
    state = ImageCache().root / "pools" / identifier
    runtime = json.loads((state / "runtime.json").read_bytes())
    connection = http.client.HTTPConnection("127.0.0.1", runtime["port"], timeout=90)
    try:
        connection.request(
            "POST",
            "/",
            canonical(
                {
                    "operation": operation,
                    "argv": list(argv),
                    "stream": output is not None,
                }
            ),
            {
                "Authorization": "Bearer " + (state / "capability").read_text(),
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        if output is not None and response.status == 200:
            while line := response.readline((3 << 20) + 1):
                if len(line) > 3 << 20:
                    raise ScriptError("pool stream exceeded its frame limit")
                frame = json.loads(line)
                if "error" in frame:
                    raise ScriptError(frame["error"])
                if "result" in frame:
                    return cast(dict[str, Any], frame["result"])
                output(frame["stream"], base64.b64decode(frame["data"], validate=True))
            raise ScriptError("pool stream ended without a result")
        result = cast(dict[str, Any], json.loads(response.read(2 << 20)))
        if response.status != 200:
            raise ScriptError(str(result.get("error", "pool request failed")))
        return result
    finally:
        connection.close()


def start(template: Path, size: int, memory_budget_mib: int = 4096) -> str:
    if not 1 <= size <= 32:
        raise ScriptError("pool size must be between 1 and 32")
    document = warm.admit(template)
    memory = int(document["config"]["memory_mib"])
    quota = Quota(
        Limits(
            concurrent=size, aggregate_cpus=size, aggregate_memory_mib=memory_budget_mib
        )
    )
    for index in range(size):
        quota.reserve(str(index), memory)
    identifier = uuid.uuid4().hex
    state = ImageCache().root / "pools" / identifier
    state.mkdir(mode=0o700, parents=True)
    (state / "config.json").write_bytes(
        canonical(
            {
                "template": str(template.resolve()),
                "size": size,
                "memory_budget_mib": memory_budget_mib,
            }
        )
    )
    fd = os.open(state / "capability", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write(secrets.token_hex(32))
    with (state / "worker.log").open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "nvx_tools.pool", str(state)],
            cwd=BuildConstants.REPO_ROOT / "scripts",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
    assert process.stdout is not None
    stdout = process.stdout
    ready: queue.Queue[bytes] = queue.Queue()
    threading.Thread(
        target=lambda: ready.put(stdout.readline(256)), daemon=True
    ).start()
    try:
        if ready.get(timeout=180 + size * 90).strip() != b"NVX-POOL-READY":
            raise ScriptError(
                "pool worker failed; inspect " + str(state / "worker.log")
            )
    except (queue.Empty, ScriptError):
        process.terminate()
        process.wait(timeout=30)
        raise
    return identifier


def stop(identifier: str, timeout: float = 180) -> dict[str, Any]:
    result = request(identifier, "stop")
    state = ImageCache().root / "pools" / identifier
    deadline = time.monotonic() + timeout
    while (state / "runtime.json").exists():
        if time.monotonic() >= deadline:
            raise ScriptError("pool cleanup timed out; inspect worker.log")
        time.sleep(0.02)
    return result


def command(args: argparse.Namespace) -> None:
    if args.pool_operation == "start":
        template = args.template
        if template is None:
            if args.image is None:
                raise ScriptError("pool start requires --template or --image")
            template = ImageCache().root / "warm" / uuid.uuid4().hex
            warm.capture(
                args.image,
                args.backend,
                template,
                resolve({"profile": args.profile}),
                memory_mib=args.memory_mib,
            )
        print(start(template, args.size, args.memory_budget_mib))
    else:
        if args.pool_operation == "stop":
            stop(args.id)
            result = {"stopped": True}
        else:
            result = request(args.id, args.pool_operation)
        print(json.dumps(result, sort_keys=True))


def command_run(args: argparse.Namespace) -> None:
    for name in (
        "workspace",
        "out",
        "secret",
        "env",
        "mount",
        "receipt",
        "receipt_signing_key",
        "outcome_report",
        "keep_alive",
        "image",
        "kernel",
        "initrd",
        "save_snapshot",
        "restore_snapshot",
        "memory_mib",
        "profile",
        "policy",
        "network_egress_allow",
    ):
        if getattr(args, name, None):
            raise ScriptError(
                "pool leases use their template configuration; omit --"
                + name.replace("_", "-")
            )
    argv = tuple(args.workload)
    if argv[:1] == ("--",):
        argv = argv[1:]
    if not argv:
        raise ScriptError("pool run requires COMMAND [ARG...]")
    result = request(
        args.pool, "run", argv, output=write_stream if args.stream else None
    )
    print("NVX-ID: " + result["id"], file=sys.stderr)
    if not args.stream:
        sys.stdout.buffer.write(base64.b64decode(result["stdout"], validate=True))
        sys.stderr.buffer.write(base64.b64decode(result["stderr"], validate=True))
    raise SystemExit(int(result["returncode"]))


def configure_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser("pool", help="keep repaired single-use clones hot")
    operations = parser.add_subparsers(dest="pool_operation", required=True)
    for name in ("start", "status", "stop"):
        child = operations.add_parser(name)
        child.set_defaults(handler=command)
        if name == "start":
            child.add_argument("--template", type=Path)
            child.add_argument("--image")
            child.add_argument(
                "--backend",
                choices=("hvf", "kvm", "mshv", "whp"),
                default=default_backend(),
            )
            child.add_argument(
                "--profile", choices=("default", "ci", "risky"), default="default"
            )
            child.add_argument("--size", type=int, default=8)
            child.add_argument("--memory-mib", type=int, default=512)
            child.add_argument("--memory-budget-mib", type=int, default=4096)
        else:
            child.add_argument("id")


if __name__ == "__main__":
    serve(Path(sys.argv[1]))
