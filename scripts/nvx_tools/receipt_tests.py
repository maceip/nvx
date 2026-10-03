"""Live host flow audit: actual collector traffic and independently verified evidence."""

from __future__ import annotations

import http.server
import json
import re
import threading
from pathlib import Path
from typing import Any, cast

from .common import ScriptError
from .image import ImageCache
from .policy_tests import execute
from .receipt import verify


class Collector(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"nvx-collector")

    def log_message(self, format: str, *args: object) -> None:
        pass


def run(backend: str, output: Path, timeout: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_port
    script = f"""
import json,socket
result={{}}
for label,address,port in [('allowed','192.168.127.1',{port}),('denied','192.0.2.1',443)]:
    s=socket.socket();s.settimeout(3)
    try:
        s.connect((address,port));s.sendall(b'GET / HTTP/1.0\\r\\nHost: nvx-test\\r\\n\\r\\n');response=b''
        while True:
            data=s.recv(4096)
            if not data:break
            response+=data
        result[label]='ok' if b'nvx-collector' in response else 'bad-response'
    except OSError as e:result[label]=e.errno
    finally:s.close()
print(json.dumps(result))
"""
    try:
        identities: list[set[tuple[str, int, str, str]]] = []
        for index in range(2):
            label = f"flow-{index}"
            outcome = output / f"{label}-outcome.json"
            result = execute(
                backend,
                script,
                output,
                label,
                timeout,
                profile="ci",
                extra=(
                    "--network-egress-allow",
                    f"192.168.127.1:tcp:{port}",
                    "--host-loopback",
                    "allow",
                    "--outcome-report",
                    str(outcome),
                ),
            )
            if result != {"allowed": "ok", "denied": 111}:
                raise ScriptError(f"collector verdict failed: {result}")
            stderr = (output / f"{label}.stderr").read_bytes()
            match = re.search(rb"^NVX-ID: ([0-9a-f]{32})$", stderr, re.M)
            if match is None:
                raise ScriptError("missing instance ID")
            state = ImageCache().root / "instances" / match[1].decode()
            document = verify(state / "receipt.json")
            body = document["body"]
            flows = cast(list[dict[str, Any]], body["network"]["flows"])
            identity = {
                (flow["dst"], flow["dst_port"], flow["proto"], flow["verdict"])
                for flow in flows
            }
            required = {
                ("192.168.127.1", port, "tcp", "allowed"),
                ("192.0.2.1", 443, "tcp", "denied"),
            }
            if identity != required or not body["network"]["complete"]:
                raise ScriptError("receipt lacks the actual allowed and denied tuples")
            resources = body["resources"]
            if (
                resources["memory_peak_bytes"] <= 0
                or resources["cpu_usec"] <= 0
                or resources["pids_peak"] <= 0
            ):
                raise ScriptError("receipt omitted measured resource peaks")
            compatibility = json.loads(outcome.read_bytes())
            if compatibility["schema_version"] != 1 or compatibility["outcome"] != {
                "operation": "exec",
                "category": "exit",
                "status_code": 0,
            }:
                raise ScriptError("existing outcome schema changed")
            (output / f"{label}-receipt.json").write_bytes(
                (state / "receipt.json").read_bytes()
            )
            identities.append(identity)
        if identities[0] != identities[1]:
            raise ScriptError("flow receipts differ after excluding volatile fields")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    print("NVX-RECEIPT-FLOW-LOG-OK")
