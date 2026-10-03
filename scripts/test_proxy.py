import http.client
import http.server
import json
import secrets
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from nvx_tools.common import ScriptError
from nvx_tools.events import read_events
from nvx_tools.proxy import Binding, CredentialProxy, prepare

VECTORS = Path(__file__).parent / "testdata/proxy-v1.json"


class ProxyTests(unittest.TestCase):
    def test_redirect_is_not_followed_and_instance_capability_replay_is_refused(
        self,
    ) -> None:
        observed: list[str] = []

        class Redirect(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                observed.append(self.path)
                self.send_response(302)
                self.send_header("Location", "http://evil.example.test/steal")
                self.send_header("Set-Cookie", "upstream=private")
                self.end_headers()

            def log_message(self, format: str, *args: object) -> None:
                pass

        upstream = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowed = frozenset({("127.0.0.1", upstream.server_port)})
            first = CredentialProxy(
                allowed, (), "first-instance-capability", root / "first.jsonl", "first"
            )
            second = CredentialProxy(
                allowed,
                (),
                "second-instance-capability",
                root / "second.jsonl",
                "second",
            )
            first.start()
            second.start()

            def request(
                proxy: CredentialProxy, path: str, capability: str
            ) -> tuple[int, str | None]:
                client = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
                try:
                    client.request(
                        "GET", path, headers={"X-NVX-Proxy-Capability": capability}
                    )
                    response = client.getresponse()
                    response.read()
                    self.assertIsNone(response.getheader("Set-Cookie"))
                    return response.status, response.getheader("Location")
                finally:
                    client.close()

            try:
                path = f"/http/127.0.0.1:{upstream.server_port}/redirect"
                self.assertEqual(
                    request(first, path, "first-instance-capability"),
                    (302, "http://evil.example.test/steal"),
                )
                self.assertEqual(observed, ["/redirect"])
                self.assertEqual(
                    request(
                        first,
                        "/http/evil.example.test:80/steal",
                        "first-instance-capability",
                    )[0],
                    403,
                )
                self.assertEqual(
                    request(second, path, "first-instance-capability")[0], 403
                )
                self.assertEqual(observed, ["/redirect"])
            finally:
                first.stop()
                second.stop()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)

    def test_dns_rebinding_cannot_change_the_pinned_upstream(self) -> None:
        import socket

        observed: list[str] = []

        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                observed.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"pinned")

            def log_message(self, format: str, *args: object) -> None:
                pass

        upstream = http.server.HTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        thread.start()
        original = socket.getaddrinfo
        lookups: list[str] = []
        rebound = False

        def resolve(host: str, port: int, *args: Any, **kwargs: Any) -> Any:
            if host == "upstream.example.test":
                lookups.append(host)
                return original(
                    "127.0.0.2" if rebound else "127.0.0.1", port, *args, **kwargs
                )
            return original(host, port, *args, **kwargs)

        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("socket.getaddrinfo", side_effect=resolve),
            ):
                proxy = CredentialProxy(
                    frozenset({("upstream.example.test", upstream.server_port)}),
                    (),
                    "fixture-capability",
                    Path(directory) / "proxy.jsonl",
                    "dns",
                )
                proxy.start()
                rebound = True
                try:
                    for _ in range(2):
                        client = http.client.HTTPConnection(
                            "127.0.0.1", proxy.port, timeout=5
                        )
                        client.request(
                            "GET",
                            f"/http/upstream.example.test:{upstream.server_port}/check",
                            headers={"X-NVX-Proxy-Capability": "fixture-capability"},
                        )
                        response = client.getresponse()
                        self.assertEqual(
                            (response.status, response.read()), (200, b"pinned")
                        )
                        client.close()
                    self.assertEqual(lookups, ["upstream.example.test"])
                    self.assertEqual(observed, ["/check", "/check"])
                finally:
                    proxy.stop()
        finally:
            upstream.shutdown()
            upstream.server_close()
            thread.join(timeout=2)

    def test_language_neutral_golden_vectors(self) -> None:
        vectors = cast(list[dict[str, Any]], json.loads(VECTORS.read_bytes()))
        for row in vectors:
            with self.subTest(name=row["name"]):
                allowed = frozenset((host, port) for host, port in row["allowed"])
                bindings = tuple(Binding(**item) for item in row["bindings"])
                if row.get("error"):
                    with self.assertRaises(ScriptError):
                        prepare(
                            row["method"],
                            row["target"],
                            row["headers"],
                            allowed,
                            bindings,
                        )
                else:
                    result = prepare(
                        row["method"], row["target"], row["headers"], allowed, bindings
                    )
                    self.assertEqual(
                        dict(
                            host=result.host,
                            port=result.port,
                            path=result.path,
                            headers=result.headers,
                        ),
                        row["expect"],
                    )

    def test_actual_forwarding_scoped_injection_and_no_reflection_or_log_leak(
        self,
    ) -> None:
        requests: list[dict[str, str]] = []
        token = "fixture-" + secrets.token_hex(24)

        class Upstream(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append(dict(self.headers.items()))
                self.send_response(200)
                self.send_header("X-Echo", token)
                self.end_headers()
                self.wfile.write(token.encode())

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "proxy.jsonl"
            cap = secrets.token_hex(32)
            proxy = CredentialProxy(
                frozenset({("127.0.0.1", server.server_port)}),
                (
                    Binding(
                        "FIXTURE", "127.0.0.1", server.server_port, token, scheme="http"
                    ),
                ),
                cap,
                log,
                "fixture",
            )
            proxy.start()
            try:
                client = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
                client.request(
                    "GET",
                    f"/http/127.0.0.1:{server.server_port}/echo",
                    headers={
                        "X-NVX-Proxy-Capability": cap,
                        "Authorization": "Bearer caller",
                        "X-Api-Key": "caller",
                    },
                )
                response = client.getresponse()
                body = response.read()
                self.assertEqual(response.status, 200)
                self.assertEqual(body, b"[redacted]")
                self.assertEqual(response.getheader("X-Echo"), "[redacted]")
                client.close()
                self.assertEqual(requests[0].get("authorization"), "Bearer " + token)
                self.assertNotIn("x-api-key", {name.lower() for name in requests[0]})
                client = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
                client.request(
                    "GET",
                    "/https/evil.example.test:443/",
                    headers={"X-NVX-Proxy-Capability": cap},
                )
                response = client.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                client.close()
                client = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=5)
                client.request("GET", f"/http/127.0.0.1:{server.server_port}/")
                response = client.getresponse()
                response.read()
                self.assertEqual(response.status, 403)
                client.close()
                self.assertEqual(len(requests), 1)
            finally:
                proxy.stop()
            text = log.read_text()
            self.assertNotIn(token, text)
            self.assertNotIn(cap, text)
            rows = read_events(log)
            self.assertEqual([row["sequence"] for row in rows], [1, 2, 3])
            self.assertIn("evil.example.test", text)
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
