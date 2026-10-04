"""Actual guest MCP operations through the shipped Python HTTP client."""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from pathlib import Path

from . import mcp
from .build_constants import BuildConstants
from .common import ScriptError


def run(backend: str, output: Path, timeout: float) -> None:
    sys.path.insert(0, str(BuildConstants.REPO_ROOT / "sdk/python"))
    from nvx_sdk import Client

    output.mkdir(parents=True, exist_ok=True)
    service = mcp.Service(backend, output / "server")
    token = os.urandom(32).hex()
    capability = output / "client.capability"
    capability.write_text(token)
    capability.chmod(0o600)
    server = mcp.http_server(service, token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = Client(f"http://127.0.0.1:{server.server_port}/mcp", capability)
    try:
        client.initialize()
        if [item["name"] for item in client.tools()] != list(mcp.TOOL_NAMES):
            raise ScriptError("MCP tool contract changed")
        result = client.run(
            "alpine:3.20",
            ["/bin/sh", "-c", "printf first;printf error >&2;exit 37"],
            handle="keepalive",
            keep_alive=True,
        )
        if (
            result["returncode"] != 37
            or base64.b64decode(result["stdout"]) != b"first"
            or base64.b64decode(result["stderr"]) != b"error"
        ):
            raise ScriptError("MCP run lost its workload streams or exit 37")
        identifier = result["id"]
        repeated = client.run(
            "alpine:3.20",
            ["/bin/sh", "-c", "printf first;printf error >&2;exit 37"],
            handle="keepalive",
            keep_alive=True,
        )
        if repeated != result:
            raise ScriptError("MCP idempotence spawned a duplicate workload")
        status = client.call("nvx_status", {"operation": "inspect", "id": identifier})
        if status["state"] != "running" or status["rss_bytes"] <= 0:
            raise ScriptError("MCP status omitted the live process")
        executed = client.exec(
            identifier,
            ["/bin/sh", "-c", "printf second;printf stderr >&2;exit 37"],
            handle="exec",
        )
        if (
            executed["id"] != identifier
            or executed["returncode"] != 37
            or base64.b64decode(executed["stderr"]) != b"stderr"
        ):
            raise ScriptError("MCP exec lost its instance, stream, or exit code")
        raw = os.urandom(1 << 20)
        (service.files_root / "input.bin").write_bytes(raw)
        client.call(
            "nvx_files",
            {
                "id": identifier,
                "operation": "upload",
                "local": "input.bin",
                "guest": "/tmp/roundtrip.bin",
            },
        )
        client.call(
            "nvx_files",
            {
                "id": identifier,
                "operation": "download",
                "local": "output.bin",
                "guest": "/tmp/roundtrip.bin",
            },
        )
        if (service.files_root / "output.bin").read_bytes() != raw:
            raise ScriptError("MCP file transfer is not byte exact")
        client.call("nvx_status", {"operation": "stop", "id": identifier})
        before = client.call("nvx_status", {})["quota"]
        try:
            client.run(
                "alpine:3.20", ["/bin/true"], handle="quota-denied", memory_mib=1025
            )
        except RuntimeError as error:
            if "quota" not in str(error):
                raise
        else:
            raise ScriptError("MCP admitted a workload above its memory quota")
        if client.call("nvx_status", {})["quota"] != before:
            raise ScriptError("denied MCP request consumed a quota slot")
        snapshot = client.call(
            "nvx_snapshot",
            {"operation": "capture", "image": "alpine:3.20"},
            timeout=timeout,
        )
        listed = client.call("nvx_snapshot", {"operation": "list"})
        if snapshot["id"] not in listed["snapshots"]:
            raise ScriptError("MCP snapshot was not retained")
        client.call("nvx_snapshot", {"operation": "remove", "id": snapshot["id"]})
        if client.call("nvx_secrets", {}) != {"names": []}:
            raise ScriptError("MCP secrets returned unexpected values")
        frames = bytearray()
        cancelled = False

        def output_frame(stream: str, data: bytes) -> None:
            nonlocal cancelled
            if stream == "stdout":
                frames.extend(data)
                if b"NVX-CANCEL-READY" in frames and not cancelled:
                    cancelled = True
                    client.cancel(8675309)

        interrupted = client.call(
            "nvx_run",
            {
                "image": "alpine:3.20",
                "argv": ["/bin/sh", "-c", "echo NVX-CANCEL-READY;sleep 60"],
                "handle": "cancel",
            },
            timeout=timeout,
            request_id=8675309,
            output=output_frame,
        )
        if (
            not cancelled
            or interrupted["category"] != "cancelled"
            or interrupted["returncode"] != 124
        ):
            raise ScriptError("MCP cancellation did not stop the guest workload")
        if client.call("nvx_status", {})["quota"]["sandboxes"]:
            raise ScriptError("MCP cancellation leaked a live quota slot")
        (output / "result.json").write_text(
            json.dumps(
                {
                    "tools": list(mcp.TOOL_NAMES),
                    "exit": 37,
                    "copy_bytes": len(raw),
                    "idempotent": True,
                    "quota_denied": True,
                    "cancelled": True,
                }
            )
            + "\n"
        )
    finally:
        service.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        capability.unlink(missing_ok=True)
    print("NVX-MCP-PORTABLE-OK")
