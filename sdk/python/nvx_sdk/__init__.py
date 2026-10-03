"""Standard-library client for NVX's authenticated loopback MCP endpoint."""

from __future__ import annotations

import base64
import http.client
import json
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

PROTOCOL = "2025-06-18"


class Client:
    def __init__(self, url: str, capability_file: str | Path):
        parsed = urllib.parse.urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != "127.0.0.1"
            or not parsed.port
            or parsed.path != "/mcp"
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("NVX SDK requires its printed loopback /mcp URL")
        self.port = parsed.port
        self.capability = Path(capability_file).read_text().strip()
        self._lock = threading.Lock()
        self._sequence = 0

    def _id(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        identifier: int | None,
        timeout: float,
        output: Callable[[str, bytes], None] | None = None,
    ) -> dict[str, Any]:
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if identifier is not None:
            message["id"] = identifier
        try:
            connection.request(
                "POST",
                "/mcp",
                json.dumps(message).encode(),
                {
                    "Authorization": "Bearer " + self.capability,
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": PROTOCOL,
                },
            )
            response = connection.getresponse()
            if response.status == 202 and identifier is None:
                return {}
            if response.status != 200:
                raise RuntimeError("NVX endpoint returned HTTP " + str(response.status))
            if response.getheader("Content-Type", "").startswith("text/event-stream"):
                document: dict[str, Any] | None = None
                while raw := response.readline((3 << 20) + 1):
                    if len(raw) > (3 << 20):
                        raise RuntimeError("NVX response frame exceeds its bound")
                    if not raw.startswith(b"data: "):
                        continue
                    frame = json.loads(raw[6:])
                    if (
                        frame.get("method") == "notifications/progress"
                        and output is not None
                    ):
                        data = json.loads(frame["params"]["message"])
                        output(
                            data["stream"],
                            base64.b64decode(data["data"], validate=True),
                        )
                    elif frame.get("id") == identifier:
                        document = frame
                if document is None:
                    raise RuntimeError("NVX stream ended without its response")
            else:
                document = json.loads(response.read(2 << 20))
            if document is None:
                raise RuntimeError("NVX response has no document")
            if "error" in document:
                raise RuntimeError(document["error"]["message"])
            return document["result"]
        except TimeoutError:
            if identifier is not None:
                self.cancel(identifier)
            raise
        finally:
            connection.close()

    def initialize(self) -> dict[str, Any]:
        result = self._request(
            "initialize",
            {
                "protocolVersion": PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "nvx-python-sdk", "version": "1.0"},
            },
            self._id(),
            10,
        )
        if result["protocolVersion"] != PROTOCOL:
            raise RuntimeError("NVX protocol version is unsupported by this SDK")
        self._request("notifications/initialized", {}, None, 10)
        return result

    def tools(self) -> list[dict[str, Any]]:
        return self._request("tools/list", {}, self._id(), 10)["tools"]

    def cancel(self, request_id: int) -> None:
        self._request(
            "notifications/cancelled",
            {"requestId": request_id, "reason": "SDK cancellation"},
            None,
            10,
        )

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 90,
        output: Callable[[str, bytes], None] | None = None,
        request_id: int | None = None,
    ) -> dict[str, Any]:
        identifier = request_id if request_id is not None else self._id()
        result = self._request(
            "tools/call",
            {
                "name": name,
                "arguments": arguments,
                "_meta": {"progressToken": identifier},
            },
            identifier,
            timeout,
            output,
        )
        if result.get("isError"):
            raise RuntimeError(result["content"][0]["text"])
        return json.loads(result["content"][0]["text"])

    def run(
        self, image: str, argv: list[str], *, handle: str, **options: Any
    ) -> dict[str, Any]:
        return self.call(
            "nvx_run", {"image": image, "argv": argv, "handle": handle, **options}
        )

    def exec(
        self, identifier: str, argv: list[str], *, handle: str, **options: Any
    ) -> dict[str, Any]:
        return self.call(
            "nvx_exec", {"id": identifier, "argv": argv, "handle": handle, **options}
        )
