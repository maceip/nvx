"""Authenticated control-session client for managed NVX microVM workloads."""

from __future__ import annotations

import json
import math
import os
import secrets
import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, cast

from .common import ScriptError, remaining_timeout

OUTER_HEADER = struct.Struct("<4sHBB16sQQI")
APP_HEADER = struct.Struct("<4sBBHQiI")
OUTER_MAX_PAYLOAD = 65_536
APP_MAX_ARGUMENTS = 64
APP_MAX_ARGUMENT_BYTES = 4096

OUTER_HOST_ATTACH = 2
OUTER_RESET = 3
OUTER_DATA = 5
OUTER_WAIT = 6
OUTER_READY = 7
OUTER_ERROR = 8

APP_PING = 1
APP_EXEC = 2
APP_STOP = 3
APP_METRICS = 4
APP_WARM = 5
APP_READY = 0x81
APP_STDOUT = 0x82
APP_STDERR = 0x83
APP_EXIT = 0x84
APP_STOPPED = 0x85
APP_METRICS_RESULT = 0x86
APP_ERROR = 0xFF
MANAGED_EXIT_CATEGORIES = frozenset(
    {"exit", "timeout", "output-limit", "signal", "failed"}
)


@dataclass(frozen=True)
class ManagedExecResult:
    returncode: int
    category: str
    stdout: bytes
    stderr: bytes


class _SocketStream:
    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection

    @classmethod
    def connect(cls, path: Path, timeout: float) -> _SocketStream:
        deadline = time.monotonic() + timeout
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            raise RuntimeError("Unix-domain sockets are unavailable")
        while True:
            connection = socket.socket(cast(int, family), socket.SOCK_STREAM)
            try:
                connection.settimeout(min(0.25, max(0.01, deadline - time.monotonic())))
                connection.connect(os.fspath(path))
                return cls(connection)
            except OSError as error:
                connection.close()
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"managed control endpoint did not become available: {path}"
                    ) from error
                time.sleep(0.025)

    def read_exact(self, length: int, deadline: float) -> bytes:
        output = bytearray()
        while len(output) != length:
            remaining = remaining_timeout(deadline)
            if remaining <= 0:
                raise TimeoutError("managed control response timed out")
            self._connection.settimeout(min(remaining, 0.25))
            try:
                chunk = self._connection.recv(length - len(output))
            except TimeoutError:
                continue
            if not chunk:
                raise ConnectionError("managed control endpoint closed")
            output.extend(chunk)
        return bytes(output)

    def write_all(self, data: bytes) -> None:
        self._connection.sendall(data)

    def close(self) -> None:
        self._connection.close()


if os.name == "nt":
    import ctypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _Overlapped(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", ctypes.c_uint32),
            ("OffsetHigh", ctypes.c_uint32),
            ("hEvent", ctypes.c_void_p),
        ]

    def _bind(name: str, argtypes: list[Any], restype: Any) -> Any:
        function = getattr(_kernel32, name)
        function.argtypes = argtypes
        function.restype = restype
        return function

    _create_file = _bind(
        "CreateFileW",
        [
            ctypes.c_wchar_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ],
        ctypes.c_void_p,
    )
    _create_event = _bind(
        "CreateEventW",
        [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_wchar_p],
        ctypes.c_void_p,
    )
    _io_arguments = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(_Overlapped),
    ]
    _read_file = _bind("ReadFile", _io_arguments, ctypes.c_int)
    _write_file = _bind("WriteFile", _io_arguments, ctypes.c_int)
    _wait_event = _bind(
        "WaitForSingleObject", [ctypes.c_void_p, ctypes.c_uint32], ctypes.c_uint32
    )
    _get_result = _bind(
        "GetOverlappedResult",
        [
            ctypes.c_void_p,
            ctypes.POINTER(_Overlapped),
            ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_int,
        ],
        ctypes.c_int,
    )
    _cancel_io = _bind(
        "CancelIoEx", [ctypes.c_void_p, ctypes.POINTER(_Overlapped)], ctypes.c_int
    )
    _close_handle = _bind("CloseHandle", [ctypes.c_void_p], ctypes.c_int)
else:
    ctypes = cast(Any, None)
    _Overlapped = cast(Any, None)
    _create_file = _create_event = _read_file = _write_file = cast(Any, None)
    _wait_event = _get_result = _cancel_io = _close_handle = cast(Any, None)


def _raise_pipe_error(operation: str, error: int | None = None) -> NoReturn:
    code = int(ctypes.get_last_error()) if error is None else error
    if code in (109, 232, 233, 995):
        raise ConnectionError("managed control endpoint closed")
    raise OSError(code, f"{operation} failed")


class _NamedPipeStream:
    def __init__(self, handle: int) -> None:
        self._handle: int | None = handle

    @classmethod
    def connect(cls, path: Path, timeout: float) -> _NamedPipeStream:
        normalized = os.fspath(path).replace("/", "\\")
        deadline = time.monotonic() + timeout
        while True:
            # OPEN_EXISTING, GENERIC_READ | GENERIC_WRITE, FILE_FLAG_OVERLAPPED.
            handle = _create_file(normalized, 0xC0000000, 0, None, 3, 0x40000000, None)
            if handle is not None and handle != ctypes.c_void_p(-1).value:
                return cls(int(handle))
            error = int(ctypes.get_last_error())
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"managed control endpoint did not become available: {path}"
                ) from OSError(error, "CreateFileW failed")
            if error not in (2, 3, 5, 231, 233):
                _raise_pipe_error("CreateFileW", error)
            time.sleep(0.025)

    def _transfer(
        self, buffer: Any, length: int, *, write: bool, deadline: float | None
    ) -> int:
        if self._handle is None:
            raise ConnectionError("managed control endpoint closed")
        event = _create_event(None, True, False, None)
        if not event:
            _raise_pipe_error("CreateEventW")
        overlapped = _Overlapped()
        overlapped.hEvent = event
        transferred = ctypes.c_uint32()
        pending = False
        try:
            operation = _write_file if write else _read_file
            if not operation(
                self._handle, buffer, length, None, ctypes.byref(overlapped)
            ):
                error = int(ctypes.get_last_error())
                if error != 997:  # ERROR_IO_PENDING.
                    _raise_pipe_error("WriteFile" if write else "ReadFile", error)
                pending = True
                wait_ms = (
                    0xFFFFFFFF
                    if deadline is None
                    else min(
                        0xFFFFFFFE,
                        max(0, math.ceil((deadline - time.monotonic()) * 1000)),
                    )
                )
                status = _wait_event(event, wait_ms)
                if status == 258:  # WAIT_TIMEOUT.
                    raise TimeoutError("managed control response timed out")
                if status != 0:  # WAIT_OBJECT_0.
                    _raise_pipe_error("WaitForSingleObject")
            if not _get_result(
                self._handle, ctypes.byref(overlapped), ctypes.byref(transferred), False
            ):
                _raise_pipe_error("GetOverlappedResult")
            pending = False
            return int(transferred.value)
        finally:
            if pending:
                # Cancellation only requests completion. Keep the buffer, event
                # and OVERLAPPED alive until the kernel has finished using them.
                _cancel_io(self._handle, ctypes.byref(overlapped))
                _get_result(
                    self._handle,
                    ctypes.byref(overlapped),
                    ctypes.byref(transferred),
                    True,
                )
            _close_handle(event)

    def read_exact(self, length: int, deadline: float) -> bytes:
        output = bytearray()
        while len(output) != length:
            if time.monotonic() >= deadline:
                raise TimeoutError("managed control response timed out")
            buffer = ctypes.create_string_buffer(length - len(output))
            count = self._transfer(buffer, len(buffer), write=False, deadline=deadline)
            if count == 0:
                raise ConnectionError("managed control endpoint closed")
            output.extend(buffer.raw[:count])
        return bytes(output)

    def write_all(self, data: bytes) -> None:
        offset = 0
        while offset != len(data):
            buffer = ctypes.create_string_buffer(data[offset:], len(data) - offset)
            count = self._transfer(buffer, len(buffer), write=True, deadline=None)
            if count <= 0:
                raise ConnectionError("managed control endpoint closed")
            offset += count

    def close(self) -> None:
        if self._handle is not None:
            handle, self._handle = self._handle, None
            if not _close_handle(handle):
                _raise_pipe_error("CloseHandle")


class ControlSession:
    def __init__(self, stream: _SocketStream | _NamedPipeStream) -> None:
        self._stream = stream
        self._instance_id = bytes(16)
        self._epoch = 0
        self._send_sequence = 0
        self._receive_sequence = 0

    @classmethod
    def connect(
        cls,
        endpoint: Path,
        capability: bytes,
        timeout: float,
    ) -> ControlSession:
        if len(capability) != 32 or capability == bytes(32):
            raise ValueError("control capability must be 32 nonzero bytes")
        stream = (
            _NamedPipeStream.connect(endpoint, timeout)
            if os.name == "nt"
            else _SocketStream.connect(endpoint, timeout)
        )
        session = cls(stream)
        try:
            session._write_outer(
                OUTER_HOST_ATTACH,
                bytes(16),
                0,
                0,
                capability,
            )
            deadline = time.monotonic() + timeout
            while True:
                record_type, instance_id, epoch, sequence, payload = (
                    session._read_outer(deadline)
                )
                if payload:
                    raise ScriptError(
                        "control attach response carried an invalid payload"
                    )
                if record_type == OUTER_WAIT:
                    continue
                if record_type == OUTER_ERROR:
                    raise ScriptError("control capability authentication failed")
                if record_type != OUTER_READY or instance_id == bytes(16) or epoch == 0:
                    raise ScriptError(
                        "control endpoint returned an invalid attach response"
                    )
                session._instance_id = instance_id
                session._epoch = epoch
                session._receive_sequence = sequence + 1
                return session
        except BaseException:
            stream.close()
            raise

    def _write_outer(
        self,
        record_type: int,
        instance_id: bytes,
        epoch: int,
        sequence: int,
        payload: bytes,
    ) -> None:
        if len(payload) > OUTER_MAX_PAYLOAD:
            raise ValueError("control payload exceeds the outer protocol limit")
        header = OUTER_HEADER.pack(
            b"NVXS",
            1,
            record_type,
            0,
            instance_id,
            epoch,
            sequence,
            len(payload),
        )
        self._stream.write_all(header + payload)

    def _read_outer(
        self,
        deadline: float,
    ) -> tuple[int, bytes, int, int, bytes]:
        header = self._stream.read_exact(OUTER_HEADER.size, deadline)
        magic, version, record_type, flags, instance_id, epoch, sequence, length = (
            OUTER_HEADER.unpack(header)
        )
        if magic != b"NVXS" or version != 1 or flags != 0 or length > OUTER_MAX_PAYLOAD:
            raise ScriptError("control endpoint returned an invalid outer record")
        payload = self._stream.read_exact(length, deadline) if length else b""
        return record_type, instance_id, epoch, sequence, payload

    def _send_app(
        self,
        kind: int,
        request_id: int,
        payload: bytes = b"",
    ) -> None:
        frame = (
            APP_HEADER.pack(
                b"NVXC",
                1,
                kind,
                0,
                request_id,
                0,
                len(payload),
            )
            + payload
        )
        self._write_outer(
            OUTER_DATA,
            self._instance_id,
            self._epoch,
            self._send_sequence,
            frame,
        )
        self._send_sequence += 1

    def _read_app(
        self,
        deadline: float,
    ) -> tuple[int, int, int, bytes]:
        record_type, instance_id, epoch, sequence, frame = self._read_outer(deadline)
        if record_type == OUTER_RESET:
            raise ConnectionError("managed control session was reset")
        if (
            record_type != OUTER_DATA
            or instance_id != self._instance_id
            or epoch != self._epoch
            or sequence != self._receive_sequence
            or len(frame) < APP_HEADER.size
        ):
            raise ScriptError("control endpoint returned an invalid data record")
        self._receive_sequence += 1
        magic, version, kind, flags, request_id, status, length = APP_HEADER.unpack(
            frame[: APP_HEADER.size]
        )
        payload = frame[APP_HEADER.size :]
        if magic != b"NVXC" or version != 1 or flags != 0 or length != len(payload):
            raise ScriptError("control endpoint returned an invalid application frame")
        return kind, request_id, status, payload

    @staticmethod
    def _request_id() -> int:
        return secrets.randbits(64) or 1

    def ping(self, timeout: float) -> None:
        request_id = self._request_id()
        self._send_app(APP_PING, request_id)
        kind, response_id, status, payload = self._read_app(time.monotonic() + timeout)
        if kind != APP_READY or response_id != request_id or status != 0 or payload:
            raise ScriptError("managed guest did not acknowledge readiness")

    def generation(self, timeout: float) -> str:
        request_id = self._request_id()
        self._send_app(6, request_id)
        kind, response_id, status, payload = self._read_app(time.monotonic() + timeout)
        if (
            kind != APP_READY
            or response_id != request_id
            or status
            or len(payload) != 32
            or any(c not in b"0123456789abcdef" for c in payload)
        ):
            raise ScriptError("guest returned an invalid trusted generation sample")
        return payload.decode("ascii")

    def warm(
        self,
        timeout: float,
        *,
        repair: bool = True,
        runtime: tuple[str, ...] = (),
        microvm_snapshot: bool = False,
    ) -> None:
        """Hold the single-threaded dispatcher with its private disk frozen."""
        request_id = self._request_id()
        mode = (2 if runtime else 1) | (4 if microvm_snapshot else 0)
        payload = bytes((mode, int(repair)))
        if runtime:
            payload += self._encode_exec(runtime, 0)
        self._send_app(APP_WARM, request_id, payload)
        kind, response_id, status, payload = self._read_app(time.monotonic() + timeout)
        if (kind, response_id, status, payload) != (
            APP_READY,
            request_id,
            0,
            b"warm-v1",
        ):
            raise ScriptError("managed guest did not acknowledge its warm barrier")

    @staticmethod
    def _encode_exec(arguments: tuple[str, ...], timeout_ms: int) -> bytes:
        if not 1 <= len(arguments) <= APP_MAX_ARGUMENTS:
            raise ValueError("managed exec requires 1 through 64 arguments")
        encoded: list[bytes] = []
        for argument in arguments:
            value = argument.encode("utf-8")
            if not value or len(value) > APP_MAX_ARGUMENT_BYTES or b"\0" in value:
                raise ValueError("managed exec argument is empty or exceeds 4096 bytes")
            encoded.append(struct.pack("<I", len(value)) + value)
        if not arguments[0].startswith("/"):
            raise ValueError("managed exec entrypoint must be absolute")
        if not 0 <= timeout_ms <= 3_600_000:
            raise ValueError("managed exec timeout must be 0 through 3600000 ms")
        payload = struct.pack("<IHH", timeout_ms, len(arguments), 0) + b"".join(encoded)
        if len(payload) + APP_HEADER.size > OUTER_MAX_PAYLOAD:
            raise ValueError("managed exec request exceeds the protocol limit")

        return payload

    def exec(
        self,
        arguments: tuple[str, ...],
        *,
        timeout_ms: int,
        response_timeout: float,
        output: Callable[[str, bytes], None] | None = None,
    ) -> ManagedExecResult:
        if not 0 < response_timeout < float("inf"):
            raise ValueError(
                "managed exec response timeout must be positive and finite"
            )
        payload = self._encode_exec(arguments, timeout_ms)

        request_id = self._request_id()
        self._send_app(APP_EXEC, request_id, payload)
        stdout = bytearray()
        stderr = bytearray()
        deadline = time.monotonic() + response_timeout
        while True:
            kind, response_id, status, response = self._read_app(deadline)
            if response_id != request_id:
                raise ScriptError("managed guest returned a mismatched request ID")
            if kind == APP_STDOUT:
                stdout.extend(response)
                if output is not None:
                    output("stdout", response)
            elif kind == APP_STDERR:
                stderr.extend(response)
                if output is not None:
                    output("stderr", response)
            elif kind == APP_EXIT:
                try:
                    category = response.decode("ascii")
                except UnicodeDecodeError as error:
                    raise ScriptError(
                        "managed guest returned an invalid exit category"
                    ) from error
                if category not in MANAGED_EXIT_CATEGORIES:
                    raise ScriptError(
                        "managed guest returned an unsupported exit category"
                    )
                return ManagedExecResult(status, category, bytes(stdout), bytes(stderr))
            elif kind == APP_ERROR:
                raise ScriptError(
                    "managed guest rejected exec "
                    f"(status={status}, category={response.decode('ascii', 'replace')})"
                )
            else:
                raise ScriptError("managed guest returned an invalid exec response")

    def metrics(self, timeout: float) -> dict[str, int]:
        request_id = self._request_id()
        self._send_app(APP_METRICS, request_id)
        kind, response_id, status, payload = self._read_app(time.monotonic() + timeout)
        if kind != APP_METRICS_RESULT or response_id != request_id or status != 0:
            raise ScriptError("managed guest did not return trusted resource metrics")
        value: object = json.loads(payload)
        keys = {
            "resource_version",
            "wall_ms",
            "cpu_usec",
            "memory_peak_bytes",
            "pids_peak",
            "memory_oom_kills",
            "pids_denials",
        }
        if not isinstance(value, dict) or set(cast(dict[str, object], value)) != keys:
            raise ScriptError("unsupported resource metrics schema")
        document = cast(dict[str, object], value)
        if document["resource_version"] != 1 or any(
            type(item) is not int or item < 0 for item in document.values()
        ):
            raise ScriptError("invalid resource metrics")
        return cast(dict[str, int], document)

    def stop(self, timeout: float) -> None:
        request_id = self._request_id()
        self._send_app(APP_STOP, request_id)
        kind, response_id, status, payload = self._read_app(time.monotonic() + timeout)
        if kind != APP_STOPPED or response_id != request_id or status != 0 or payload:
            raise ScriptError("managed guest did not acknowledge stop")

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> ControlSession:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
