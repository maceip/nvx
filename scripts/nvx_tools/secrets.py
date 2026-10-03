"""Named host-environment credentials and per-instance proxy capabilities."""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from .build_constants import BuildConstants
from .common import ScriptError
from .proxy import Binding, host_port


def bindings(
    args: argparse.Namespace,
) -> tuple[frozenset[tuple[str, int]], tuple[Binding, ...]]:
    if len(args.secret) > 4 or len(args.network_egress_allow) > 32:
        raise ScriptError(
            "credential proxy permits at most four bindings and 32 scopes"
        )
    scopes = frozenset(host_port(value) for value in args.network_egress_allow)
    if not scopes:
        raise ScriptError("--secret requires an exact --egress-allow HOST:PORT scope")
    headers: dict[str, str] = {}
    for value in getattr(args, "secret_header", ()):
        name, separator, header = value.partition(":")
        if not separator or re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*", header) is None:
            raise ScriptError("--secret-header must be NAME:HEADER")
        headers[name] = header
    selected: list[Binding] = []
    occupied: set[tuple[str, int, str]] = set()
    for spec in args.secret:
        name, separator, scope = spec.partition("@")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None:
            raise ScriptError("--secret must name a host environment variable")
        if not separator and len(scopes) != 1:
            raise ScriptError("multiple scopes require --secret NAME@HOST:PORT")
        host, port = host_port(scope) if separator else next(iter(scopes))
        if (host, port) not in scopes:
            raise ScriptError("secret binding is outside --egress-allow scopes")
        value = os.environ.get(name)
        if not value or len(value.encode()) > 8192 or any(c in value for c in "\r\n\0"):
            raise ScriptError(f"host credential {name} is missing or invalid")
        header = headers.get(name, "Authorization")
        identity = (host, port, header.lower())
        if identity in occupied:
            raise ScriptError("multiple secrets target the same scope/header")
        occupied.add(identity)
        selected.append(
            Binding(
                name,
                host,
                port,
                value,
                header,
                "Bearer " if header.lower() == "authorization" else "",
            )
        )
    if set(headers) - {item.name for item in selected}:
        raise ScriptError("secret header override names an unselected secret")
    return scopes, tuple(selected)


def start(
    state: Path,
    args: argparse.Namespace,
    scopes: frozenset[tuple[str, int]],
    selected: tuple[Binding, ...],
) -> tuple[subprocess.Popen[bytes], str, int]:
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    capability = secrets.token_hex(32)
    process = subprocess.Popen(
        [sys.executable, "-m", "nvx_tools.proxy_service"],
        cwd=BuildConstants.REPO_ROOT / "scripts",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    assert process.stdin is not None and process.stdout is not None
    stdout = process.stdout
    response: queue.Queue[bytes] = queue.Queue()
    threading.Thread(
        target=lambda: response.put(stdout.readline(4096)), daemon=True
    ).start()
    try:
        process.stdin.write(
            (
                json.dumps(
                    {
                        "state": str(state),
                        "capability": capability,
                        "allowed": sorted(scopes),
                        "bindings": [asdict(item) for item in selected],
                        "ca": str(args.proxy_ca.resolve()) if args.proxy_ca else None,
                    }
                )
                + "\n"
            ).encode()
        )
        process.stdin.close()
        value = cast(dict[str, Any], json.loads(response.get(timeout=30)))
        port = int(value["port"])
        if not 1 <= port <= 65535:
            raise ValueError("invalid port")
        fd = os.open(
            state / "proxy.capability", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(fd, "w") as output:
            output.write(capability)
        (state / "proxy-runtime.json").write_text(
            json.dumps({"pid": process.pid, "port": port}) + "\n"
        )
        return process, capability, port
    except (ValueError, KeyError, OSError, queue.Empty) as error:
        process.terminate()
        process.wait(timeout=10)
        raise ScriptError("host credential proxy could not start") from error


def environment(state: Path) -> tuple[str, ...]:
    runtime = state / "proxy-runtime.json"
    if not runtime.exists():
        return ()
    port = json.loads(runtime.read_bytes())["port"]
    config = json.loads((state / "config.json").read_bytes())
    import ipaddress

    gateway = str(ipaddress.ip_network(config["net"]).network_address + 1)
    return (
        f"NVX_PROXY_URL=http://{gateway}:{port}",
        "NVX_PROXY_CAPABILITY=" + (state / "proxy.capability").read_text(),
    )


def finish(state: Path, timeout: float) -> None:
    """Wait for the host worker's final log, then remove its disposable capability."""
    deadline = time.monotonic() + timeout
    while (state / "proxy-runtime.json").exists():
        if time.monotonic() >= deadline:
            raise ScriptError("credential proxy did not finish after VM teardown")
        time.sleep(0.05)
    (state / "proxy.capability").unlink(missing_ok=True)
    settings_path = state / "finish.json"
    if settings_path.exists():
        destination = json.loads(settings_path.read_bytes()).get("proxy_log")
        if destination:
            target = Path(destination)
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as log:
                log.write((state / "proxy.jsonl").read_bytes())
