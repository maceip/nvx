# pyright: reportPrivateUsage=false
import base64
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

from nvx_tools import mcp, sandbox_lifecycle  # noqa: E402
from nvx_tools.common import ScriptError  # noqa: E402
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

    def test_private_instance_metadata_is_bounded_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instance.json"
            self.assertIsNone(mcp.read_instance_id(path))
            self.assertIsNone(mcp.read_instance_id(None))
            sandbox_lifecycle.publish_json(
                path, {"version": 1, "instance_id": "a" * 32}
            )
            self.assertEqual(mcp.read_instance_id(path), "a" * 32)
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ScriptError):
                sandbox_lifecycle.publish_json(
                    path, {"version": 1, "instance_id": "b" * 32}
                )
            self.assertEqual(mcp.read_instance_id(path), "a" * 32)
            for raw in (
                b"{}",
                b"[]",
                b"malformed",
                b"x" * 513,
                json.dumps({"version": 2, "instance_id": "a" * 32}).encode(),
                json.dumps({"version": 1, "instance_id": "not-an-id"}).encode(),
            ):
                path.write_bytes(raw)
                with self.subTest(raw=raw[:30]), self.assertRaises(ScriptError):
                    mcp.read_instance_id(path)

    def test_real_new_run_diagnostics_cannot_supply_an_instance_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = mcp.Service("hvf", Path(directory))
            identity_path = Path(directory) / "instance.json"
            stderr = b"Preparing OCI image\nNVX-ID: " + b"b" * 32 + b"\r\nerror"
            try:
                command = [
                    sys.executable,
                    "-c",
                    f"import os;os.write(2,{stderr!r});raise SystemExit(37)",
                ]
                result = service._program(
                    command,
                    mcp.Job("missing"),
                    lambda value: None,
                    None,
                    (),
                    time.monotonic() + 10,
                    identity_path=identity_path,
                )
                self.assertIsNone(result["id"])
                self.assertEqual(base64.b64decode(result["stderr"]), stderr)
                sandbox_lifecycle.publish_json(
                    identity_path, {"version": 1, "instance_id": "a" * 32}
                )
                result = service._program(
                    command,
                    mcp.Job("published"),
                    lambda value: None,
                    None,
                    (),
                    time.monotonic() + 10,
                    identity_path=identity_path,
                )
                self.assertEqual(result["id"], "a" * 32)
                self.assertEqual(base64.b64decode(result["stderr"]), stderr)
            finally:
                service.close()

    def test_real_exec_stderr_cannot_replace_its_owned_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = mcp.Service("hvf", Path(directory))
            frames: list[dict[str, Any]] = []
            owned = "a" * 32
            stderr = b"NVX-ID: " + b"b" * 32 + b"\nerror"
            try:
                result = service._program(
                    [
                        sys.executable,
                        "-c",
                        f"import os;os.write(2,{stderr!r});raise SystemExit(37)",
                    ],
                    mcp.Job("test"),
                    frames.append,
                    "progress",
                    (),
                    time.monotonic() + 10,
                    identifier_hint=owned,
                )
                self.assertEqual(result["id"], owned)
                self.assertEqual(result["returncode"], 37)
                self.assertEqual(base64.b64decode(result["stderr"]), stderr)
                streamed = b"".join(
                    base64.b64decode(json.loads(frame["params"]["message"])["data"])
                    for frame in frames
                )
                self.assertEqual(streamed, stderr)
            finally:
                service.close()

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
                execute = service._program

                def fixture_program(
                    command: list[str],
                    job: mcp.Job,
                    emit: Any,
                    progress: Any,
                    redactions: Any,
                    deadline: float,
                    identifier_hint: str | None = None,
                    identity_path: Path | None = None,
                ) -> dict[str, Any]:
                    return execute(
                        [
                            sys.executable,
                            "-c",
                            "import os;os.write(1,b'output');os.write(2,b'error');raise SystemExit(37)",
                        ],
                        job,
                        emit,
                        progress,
                        redactions,
                        deadline,
                        identifier_hint=identifier_hint,
                        identity_path=identity_path,
                    )

                streamed: list[tuple[str, bytes]] = []
                with patch.object(service, "_program", side_effect=fixture_program):
                    result = client.run(
                        "fixture",
                        ["/bin/true"],
                        handle="streaming",
                        timeout=3,
                        request_id=41,
                        output=lambda stream, data: streamed.append((stream, data)),
                    )
                    self.assertEqual(result["returncode"], 37)
                    self.assertEqual(
                        b"".join(
                            data for stream, data in streamed if stream == "stdout"
                        ),
                        b"output",
                    )
                    self.assertEqual(
                        b"".join(
                            data for stream, data in streamed if stream == "stderr"
                        ),
                        b"error",
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
