"""Host-only scoped credential injection for HTTP and terminated upstream HTTPS."""

from __future__ import annotations

import hmac
import http.client
import http.server
import ipaddress
import socket
import ssl
import threading
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .common import ScriptError
from .events import EventLog

MAX_BODY_BYTES = 16 << 20
HOP = frozenset(
    (
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    )
)
STRIP = HOP | {
    "authorization",
    "x-api-key",
    "cookie",
    "host",
    "content-length",
    "expect",
    "accept-encoding",
    "x-nvx-proxy-capability",
}


@dataclass(frozen=True)
class Binding:
    name: str
    host: str
    port: int
    value: str = field(repr=False)
    header: str = "Authorization"
    prefix: str = "Bearer "
    scheme: str = "https"


@dataclass(frozen=True)
class Forward:
    method: str
    scheme: str
    host: str
    port: int
    path: str
    headers: dict[str, str] = field(repr=False)


def host_port(value: str) -> tuple[str, int]:
    parsed = urllib.parse.urlsplit("//" + value)
    try:
        port = parsed.port
    except ValueError as error:
        raise ScriptError("invalid proxy scope port") from error
    if (
        not parsed.hostname
        or port is None
        or not 1 <= port <= 65535
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        raise ScriptError("proxy scope must be HOST:PORT without credentials or a path")
    return _hostname(parsed.hostname), port


def _hostname(value: str) -> str:
    try:
        result = value.encode("idna").decode().lower().rstrip(".")
    except UnicodeError as error:
        raise ScriptError("invalid proxy hostname") from error
    if not result or result.startswith("."):
        raise ScriptError("invalid proxy hostname")
    return result


def prepare(
    method: str,
    target: str,
    headers: list[tuple[str, str]],
    allowed: frozenset[tuple[str, int]],
    bindings: tuple[Binding, ...],
) -> Forward:
    if method not in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        raise ScriptError(
            "proxy method is not supported; HTTPS uses the /https/HOST:PORT/ route"
        )
    if "\r" in target or "\n" in target or "\0" in target:
        raise ScriptError("invalid proxy target")
    if target.startswith("/https/") or target.startswith("/http/"):
        scheme, _, rest = target[1:].partition("/")
        target = scheme + "://" + rest
    parsed = urllib.parse.urlsplit(target)
    try:
        port = (
            parsed.port
            if parsed.port is not None
            else (443 if parsed.scheme == "https" else 80)
        )
    except ValueError as error:
        raise ScriptError("invalid target port") from error
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or not 1 <= port <= 65535
    ):
        raise ScriptError(
            "proxy target requires an HTTP(S) origin without user credentials"
        )
    host = _hostname(parsed.hostname)
    if (host, port) not in allowed:
        raise ScriptError("proxy host is outside the exact allowlist")
    lowered: dict[str, str] = {}
    for name, value in headers:
        key = name.lower()
        if key in lowered or any(c in name + value for c in "\r\n\0"):
            raise ScriptError("duplicate or invalid request header")
        lowered[key] = value
    excluded = STRIP | {
        name.strip().lower()
        for name in lowered.get("connection", "").split(",")
        if name.strip()
    }
    forwarded = {name: value for name, value in lowered.items() if name not in excluded}
    forwarded["accept-encoding"] = "identity"
    for binding in bindings:
        if (host, port) == (binding.host, binding.port):
            if parsed.scheme != binding.scheme:
                raise ScriptError("credential scope requires its configured transport")
            if (
                binding.header.lower()
                in (STRIP - {"authorization", "x-api-key", "cookie"})
                or not binding.value
                or any(
                    c in binding.value + binding.header + binding.prefix
                    for c in "\r\n\0"
                )
            ):
                raise ScriptError("host credential is unavailable or invalid")
            forwarded[binding.header.lower()] = binding.prefix + binding.value
    path = (parsed.path or "/") + ("?" + parsed.query if parsed.query else "")
    return Forward(method, parsed.scheme, host, port, path, forwarded)


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str):
        super().__init__(host, port, timeout=30)
        self.address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class _PinnedHTTPS(_PinnedHTTP):
    def __init__(self, host: str, port: int, address: str, context: ssl.SSLContext):
        super().__init__(host, port, address)
        self.context = context

    def connect(self) -> None:
        super().connect()
        assert self.sock is not None
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)


class CredentialProxy:
    def __init__(
        self,
        allowed: frozenset[tuple[str, int]],
        bindings: tuple[Binding, ...],
        capability: str,
        log: Path,
        instance: str,
        *,
        context: ssl.SSLContext | None = None,
    ):
        self.allowed = allowed
        self.bindings = bindings
        self.capability = capability
        self.context = context or ssl.create_default_context()
        self.events = EventLog(
            log,
            instance,
            secrets=tuple(binding.value for binding in bindings) + (capability,),
        )
        # Pin DNS before listening. Every request reuses the admitted address,
        # so a later DNS answer cannot redirect an injected credential.
        self.addresses: dict[tuple[str, int], str] = {}
        for host, port in sorted(allowed):
            answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not answers:
                raise ScriptError("proxy scope hostname has no address")
            self.addresses[(host, port)] = str(ipaddress.ip_address(answers[0][4][0]))
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, format: str, *args: object) -> None:
                pass

            def do_CONNECT(self) -> None:
                self.respond(405, b"Use the HTTPS gateway route")
                owner.events.emit(
                    "proxy.request",
                    host="unknown",
                    method="CONNECT",
                    request_bytes=0,
                    response_bytes=0,
                    verdict="denied",
                )

            def send_error(
                self, code: int, message: str | None = None, explain: str | None = None
            ) -> None:
                self.respond(code, b"Proxy request refused")
                owner.events.emit(
                    "proxy.request",
                    host="unknown",
                    method=self.command or "unknown",
                    request_bytes=0,
                    response_bytes=0,
                    verdict="denied",
                )

            def do_GET(self) -> None:
                self.handle_request()

            do_HEAD = do_GET
            do_POST = do_GET
            do_PUT = do_GET
            do_PATCH = do_GET
            do_DELETE = do_GET
            do_OPTIONS = do_GET

            def respond(
                self, status: int, payload: bytes, headers: dict[str, str] | None = None
            ) -> None:
                self.send_response(status)
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)
                self.close_connection = True

            def handle_request(self) -> None:
                host = "unknown"
                verdict = "denied"
                request_bytes = response_bytes = 0
                try:
                    self.connection.settimeout(30)
                    auth = self.headers.get_all("X-NVX-Proxy-Capability", [])
                    if len(auth) != 1 or not hmac.compare_digest(
                        auth[0], owner.capability
                    ):
                        self.respond(403, b"Proxy capability rejected")
                        return
                    lengths = self.headers.get_all("Content-Length", [])
                    if (
                        self.headers.get_all("Transfer-Encoding", [])
                        or len(lengths) > 1
                    ):
                        raise ScriptError("ambiguous or unsupported body framing")
                    length = int(lengths[0]) if lengths else 0
                    if not 0 <= length <= MAX_BODY_BYTES:
                        raise ScriptError("request body exceeds limit")
                    target = self.path
                    if target.startswith(("/https/", "/http/")):
                        scheme, _, rest = target[1:].partition("/")
                        target = scheme + "://" + rest
                    host = urllib.parse.urlsplit(target).hostname or "unknown"
                    request = prepare(
                        self.command,
                        self.path,
                        list(self.headers.items()),
                        owner.allowed,
                        owner.bindings,
                    )
                    host = request.host
                    body = self.rfile.read(length)
                    if len(body) != length:
                        raise ScriptError("incomplete request body")
                    request_bytes = len(body)
                    address = owner.addresses[(request.host, request.port)]
                    connection = (
                        _PinnedHTTPS(request.host, request.port, address, owner.context)
                        if request.scheme == "https"
                        else _PinnedHTTP(request.host, request.port, address)
                    )
                    try:
                        connection.request(
                            request.method,
                            request.path,
                            body=body,
                            headers=request.headers,
                        )
                        upstream = connection.getresponse()
                        payload = upstream.read(MAX_BODY_BYTES + 1)
                        if len(payload) > MAX_BODY_BYTES:
                            raise ScriptError("upstream response exceeds limit")
                        excluded = HOP | {
                            "content-length",
                            "set-cookie",
                            "authorization",
                            "x-api-key",
                        }
                        excluded = excluded | {
                            name.strip().lower()
                            for name in upstream.getheader("Connection", "").split(",")
                            if name.strip()
                        }
                        response = {
                            name: value
                            for name, value in upstream.getheaders()
                            if name.lower() not in excluded
                        }
                        # An upstream error/echo must not reflect host key bytes
                        # into the guest, its output, or a later snapshot.
                        for binding in owner.bindings:
                            payload = payload.replace(
                                binding.value.encode(), b"[redacted]"
                            )
                            response = {
                                name: value.replace(binding.value, "[redacted]")
                                for name, value in response.items()
                            }
                        response_bytes = len(payload)
                        verdict = "allowed"
                        self.respond(upstream.status, payload, response)
                    finally:
                        connection.close()
                except (ScriptError, OSError, ValueError, http.client.HTTPException):
                    self.respond(403, b"Proxy request refused")
                finally:
                    owner.events.emit(
                        "proxy.request",
                        host=host,
                        method=self.command,
                        request_bytes=request_bytes,
                        response_bytes=response_bytes,
                        verdict=verdict,
                    )

        class Server(http.server.ThreadingHTTPServer):
            slots = threading.BoundedSemaphore(32)

            def process_request(
                self,
                request: socket.socket | tuple[bytes, socket.socket],
                client_address: tuple[str, int],
            ) -> None:
                if not self.slots.acquire(blocking=False):
                    self.shutdown_request(request)
                    return
                try:
                    super().process_request(request, client_address)
                except BaseException:
                    self.slots.release()
                    raise

            def process_request_thread(
                self,
                request: socket.socket | tuple[bytes, socket.socket],
                client_address: tuple[str, int],
            ) -> None:
                try:
                    super().process_request_thread(request, client_address)
                finally:
                    self.slots.release()

        self.server = Server(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self.server.server_port

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def status(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "scopes": [f"{host}:{port}" for host, port in sorted(self.allowed)],
            "names": sorted({binding.name for binding in self.bindings}),
        }
