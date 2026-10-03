# pyright: reportPrivateUsage=false
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sdk/python"))

from nvx_tools import mcp  # noqa: E402
from nvx_tools.quota import Limits  # noqa: E402


class MCPTests(unittest.TestCase):
    def initialized(self, service: mcp.Service) -> None:
        service.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": mcp.PROTOCOL},
            },
            lambda value: None,
        )
        service.handle(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            lambda value: None,
        )

    def test_shared_protocol_vectors_and_secret_metadata(self) -> None:
        vectors: dict[str, Any] = json.loads(
            (Path(__file__).parent / "testdata/mcp-v1.json").read_bytes()
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict(os.environ, {"NVX_TEST_CREDENTIAL": "nvx-test-secret-value"}),
        ):
            service = mcp.Service(
                "hvf", Path(directory), secret_names=("NVX_TEST_CREDENTIAL",)
            )
            try:
                response = service.handle(vectors["initialize"], lambda value: None)
                self.assertEqual(response, vectors["initialize_response"])
                self.initialized(service)
                listed = service.handle(
                    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    lambda value: None,
                )
                assert listed is not None
                self.assertEqual(
                    [item["name"] for item in listed["result"]["tools"]],
                    vectors["tool_names"],
                )
                result = service.call(
                    "nvx_secrets", {}, mcp.Job("test"), lambda value: None, None
                )
                self.assertEqual(
                    result,
                    {"names": [{"name": "NVX_TEST_CREDENTIAL", "available": True}]},
                )
                self.assertNotIn("nvx-test-secret-value", json.dumps(result))
            finally:
                service.close()

    def test_before_initialization_and_quota_denial_spawn_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = mcp.Service(
                "hvf", Path(directory), Limits(aggregate_memory_mib=128)
            )
            try:
                response = service.handle(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                    lambda value: None,
                )
                assert response is not None
                self.assertIn("error", response)
                self.initialized(service)
                with patch.object(service, "_program") as spawn:
                    result = service.handle(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "nvx_run",
                                "arguments": {
                                    "image": "fixture",
                                    "argv": ["/bin/true"],
                                },
                            },
                        },
                        lambda value: None,
                    )
                    assert result is not None
                    self.assertTrue(result["result"]["isError"])
                    spawn.assert_not_called()
            finally:
                service.close()

    def test_idempotence_replays_once_and_rejects_changed_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = mcp.Service("hvf", Path(directory))
            try:
                self.initialized(service)
                request: dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "nvx_run",
                        "arguments": {
                            "image": "fixture",
                            "argv": ["/bin/true"],
                            "handle": "same",
                        },
                    },
                }
                with patch.object(
                    service,
                    "_program",
                    return_value={"id": None, "category": "exit", "returncode": 0},
                ) as program:
                    first = service.handle(request, lambda value: None)
                    self.assertEqual(service.handle(request, lambda value: None), first)
                    program.assert_called_once()
                    request["params"]["arguments"]["argv"] = ["/bin/false"]
                    changed = service.handle(request, lambda value: None)
                    assert changed is not None
                    self.assertIn("error", changed)
            finally:
                service.close()

    def test_real_process_streams_before_completion_and_cancellation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = mcp.Service("hvf", Path(directory))
            job = mcp.Job("test")
            frames: list[dict[str, Any]] = []

            def emit(frame: dict[str, Any]) -> None:
                frames.append(frame)
                job.cancel.set()

            try:
                result = service._program(
                    [
                        sys.executable,
                        "-c",
                        "import os,time;os.write(1,b'started');time.sleep(20)",
                    ],
                    job,
                    emit,
                    "progress",
                    (),
                    time.monotonic() + 10,
                )
                self.assertEqual(result["category"], "cancelled")
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0]["params"]["progress"], 1)
            finally:
                service.close()

    def test_split_token_is_redacted_in_stream(self) -> None:
        scrubber = mcp.Scrubber(("secret-value",))
        output = (
            scrubber.feed(b"prefix secre")
            + scrubber.feed(b"t-value suffix")
            + scrubber.feed(b"", final=True)
        )
        self.assertEqual(output, b"prefix [redacted] suffix")

    def test_authenticated_http_and_python_sdk(self) -> None:
        from nvx_sdk import Client

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = mcp.Service("hvf", root)
            capability = root / "capability"
            capability.write_text("fixture-capability")
            server = mcp.http_server(service, "fixture-capability")
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                client = Client(
                    f"http://127.0.0.1:{server.server_port}/mcp", capability
                )
                self.assertEqual(client.initialize()["protocolVersion"], mcp.PROTOCOL)
                self.assertEqual(
                    [tool["name"] for tool in client.tools()], list(mcp.TOOL_NAMES)
                )
                self.assertEqual(client.call("nvx_secrets", {}), {"names": []})
                with patch.object(
                    service,
                    "_program",
                    return_value={"id": None, "category": "exit", "returncode": 37},
                ):
                    self.assertEqual(
                        client.run("fixture", ["/bin/true"], handle="fixture")[
                            "returncode"
                        ],
                        37,
                    )
                capability.write_text("wrong-capability")
                with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                    Client(
                        f"http://127.0.0.1:{server.server_port}/mcp", capability
                    ).initialize()
            finally:
                server.shutdown()
                server.server_close()
                worker.join(timeout=2)
                service.close()
