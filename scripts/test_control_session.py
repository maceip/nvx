#!/usr/bin/env python3
# pyright: reportPrivateUsage=false

import os
import secrets
import socket
import struct
import sys
import threading
import time
import unittest
from collections.abc import Callable, Generator
from contextlib import contextmanager
from multiprocessing.connection import Listener
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from nvx_tools import control_session  # noqa: E402


@contextmanager
def _named_pipe_peer(
    handler: Callable[[Any], None],
) -> Generator[control_session._NamedPipeStream, None, None]:
    endpoint = Path(r"\\.\pipe\nvx-control-test-" + secrets.token_hex(16))
    failures: list[BaseException] = []
    listener = Listener(str(endpoint), family="AF_PIPE")

    def serve() -> None:
        try:
            with listener.accept() as connection:
                handler(connection)
        except BaseException as error:
            failures.append(error)

    peer = threading.Thread(target=serve, daemon=True)
    peer.start()
    client = control_session._NamedPipeStream.connect(endpoint, 5)
    try:
        yield client
    finally:
        client.close()
        peer.join(5)
        listener.close()
    if peer.is_alive() or failures:
        raise AssertionError(f"named-pipe test peer failed: {failures}")


def _read_exact(connection: socket.socket, length: int) -> bytes:
    output = bytearray()
    while len(output) != length:
        chunk = connection.recv(length - len(output))
        if not chunk:
            raise RuntimeError("test control connection closed")
        output.extend(chunk)
    return bytes(output)


def _read_outer(connection: socket.socket):
    header = _read_exact(connection, control_session.OUTER_HEADER.size)
    values = control_session.OUTER_HEADER.unpack(header)
    payload = _read_exact(connection, values[-1]) if values[-1] else b""
    return (*values[:-1], payload)


def _write_app(
    connection: socket.socket,
    *,
    instance_id: bytes,
    sequence: int,
    kind: int,
    request_id: int,
    status: int,
    payload: bytes,
) -> None:
    app = (
        control_session.APP_HEADER.pack(
            b"NVXC",
            1,
            kind,
            0,
            request_id,
            status,
            len(payload),
        )
        + payload
    )
    connection.sendall(
        control_session.OUTER_HEADER.pack(
            b"NVXS",
            1,
            control_session.OUTER_DATA,
            0,
            instance_id,
            1,
            sequence,
            len(app),
        )
        + app
    )


class ControlSessionTests(unittest.TestCase):
    def test_warm_snapshot_flag_preserves_legacy_and_runtime_wire_modes(self):
        for snapshot, runtime, mode in (
            (False, (), 1),
            (False, ("/usr/bin/python3",), 2),
            (True, (), 5),
            (True, ("/usr/bin/python3",), 6),
        ):
            client, server = socket.socketpair()
            instance = bytes.fromhex("11" * 16)
            session = control_session.ControlSession(
                control_session._SocketStream(client)
            )
            session._instance_id = instance
            session._epoch = 1
            failures: list[BaseException] = []

            def serve(
                server: socket.socket = server,
                instance: bytes = instance,
                mode: int = mode,
                runtime: tuple[str, ...] = runtime,
                failures: list[BaseException] = failures,
            ) -> None:
                try:
                    frame = _read_outer(server)[-1]
                    header = control_session.APP_HEADER.unpack(
                        frame[: control_session.APP_HEADER.size]
                    )
                    self.assertEqual(header[2], control_session.APP_WARM)
                    payload = frame[control_session.APP_HEADER.size :]
                    self.assertEqual(payload[:2], bytes((mode, 1)))
                    if runtime:
                        self.assertEqual(
                            struct.unpack("<IHH", payload[2:10]), (0, 1, 0)
                        )
                        length = struct.unpack("<I", payload[10:14])[0]
                        self.assertEqual(payload[14 : 14 + length], b"/usr/bin/python3")
                    else:
                        self.assertEqual(len(payload), 2)
                    _write_app(
                        server,
                        instance_id=instance,
                        sequence=0,
                        kind=control_session.APP_READY,
                        request_id=header[4],
                        status=0,
                        payload=b"warm-v1",
                    )
                except BaseException as error:
                    failures.append(error)

            worker = threading.Thread(target=serve, daemon=True)
            worker.start()
            try:
                session.warm(5, runtime=runtime, microvm_snapshot=snapshot)
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(failures, [])
            finally:
                session.close()
                server.close()

    def test_exec_streams_output_and_returns_bounded_status(self):
        client, server = socket.socketpair()
        instance = bytes.fromhex("11" * 16)
        session = control_session.ControlSession(control_session._SocketStream(client))
        session._instance_id = instance
        session._epoch = 1

        def serve() -> None:
            (
                magic,
                version,
                record_type,
                flags,
                actual_instance,
                epoch,
                sequence,
                frame,
            ) = _read_outer(server)
            self.assertEqual(
                (
                    magic,
                    version,
                    record_type,
                    flags,
                    actual_instance,
                    epoch,
                    sequence,
                ),
                (b"NVXS", 1, control_session.OUTER_DATA, 0, instance, 1, 0),
            )
            (
                app_magic,
                app_version,
                kind,
                app_flags,
                request_id,
                status,
                payload_length,
            ) = control_session.APP_HEADER.unpack(
                frame[: control_session.APP_HEADER.size]
            )
            self.assertEqual(
                (app_magic, app_version, kind, app_flags, status),
                (b"NVXC", 1, control_session.APP_EXEC, 0, 0),
            )
            payload = frame[control_session.APP_HEADER.size :]
            self.assertEqual(payload_length, len(payload))
            timeout_ms, argc, reserved = struct.unpack("<IHH", payload[:8])
            self.assertEqual((timeout_ms, argc, reserved), (5000, 3, 0))

            _write_app(
                server,
                instance_id=instance,
                sequence=0,
                kind=control_session.APP_STDOUT,
                request_id=request_id,
                status=0,
                payload=b"hello",
            )
            _write_app(
                server,
                instance_id=instance,
                sequence=1,
                kind=control_session.APP_STDERR,
                request_id=request_id,
                status=0,
                payload=b"warning",
            )
            _write_app(
                server,
                instance_id=instance,
                sequence=2,
                kind=control_session.APP_EXIT,
                request_id=request_id,
                status=7,
                payload=b"exit",
            )
            server.close()

        worker = threading.Thread(target=serve)
        worker.start()
        result = session.exec(
            ("/bin/sh", "-c", "echo hello"),
            timeout_ms=5000,
            response_timeout=5,
        )
        worker.join(timeout=5)
        session.close()

        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.category, "exit")
        self.assertEqual(result.stdout, b"hello")
        self.assertEqual(result.stderr, b"warning")

    def test_exec_rejects_unbounded_or_relative_arguments(self):
        client, server = socket.socketpair()
        session = control_session.ControlSession(control_session._SocketStream(client))
        with self.assertRaisesRegex(ValueError, "absolute"):
            session.exec(("relative",), timeout_ms=0, response_timeout=1)
        with self.assertRaisesRegex(ValueError, "64"):
            session.exec(
                tuple("/bin/true" for _ in range(65)),
                timeout_ms=0,
                response_timeout=1,
            )
        session.close()
        server.close()

    def test_exec_rejects_invalid_response_timeout_before_sending(self):
        client, server = socket.socketpair()
        session = control_session.ControlSession(control_session._SocketStream(client))

        for timeout in (0.0, -1.0, float("nan"), float("inf")):
            with (
                self.subTest(timeout=timeout),
                self.assertRaisesRegex(ValueError, "response timeout"),
            ):
                session.exec(
                    ("/bin/true",),
                    timeout_ms=0,
                    response_timeout=timeout,
                )

        server.setblocking(False)
        with self.assertRaises(BlockingIOError):
            server.recv(1)
        session.close()
        server.close()

    def test_exec_rejects_unknown_exit_category(self):
        client, server = socket.socketpair()
        instance = bytes.fromhex("22" * 16)
        session = control_session.ControlSession(control_session._SocketStream(client))
        session._instance_id = instance
        session._epoch = 1

        def serve() -> None:
            *_, frame = _read_outer(server)
            request_id = control_session.APP_HEADER.unpack(
                frame[: control_session.APP_HEADER.size]
            )[4]
            _write_app(
                server,
                instance_id=instance,
                sequence=0,
                kind=control_session.APP_EXIT,
                request_id=request_id,
                status=125,
                payload=b"guest-provided-detail",
            )
            server.close()

        worker = threading.Thread(target=serve)
        worker.start()
        with self.assertRaisesRegex(
            control_session.ScriptError, "unsupported exit category"
        ):
            session.exec(("/bin/true",), timeout_ms=0, response_timeout=5)
        worker.join(timeout=5)
        session.close()


@unittest.skipUnless(os.name == "nt", "native Windows named-pipe I/O")
class NamedPipeStreamTests(unittest.TestCase):
    def test_bidirectional_transfer_and_partial_read_boundaries(self):
        payload = b"x" * 4096

        def serve(connection: Any) -> None:
            self.assertEqual(connection.recv_bytes(), payload)
            connection.send_bytes(b"ab")
            connection.send_bytes(b"cd")

        with _named_pipe_peer(serve) as client:
            client.write_all(payload)
            self.assertEqual(client.read_exact(3, time.monotonic() + 5), b"abc")
            self.assertEqual(client.read_exact(1, time.monotonic() + 5), b"d")

    def test_timed_out_pending_read_is_cancelled_before_reuse(self):
        release = threading.Event()

        def serve(connection: Any) -> None:
            if not release.wait(5):
                raise AssertionError("timeout test never released its peer")
            connection.send_bytes(b"R")

        with _named_pipe_peer(serve) as client:
            started = time.monotonic()
            try:
                with self.assertRaisesRegex(TimeoutError, "response timed out"):
                    client.read_exact(1, started + 0.025)
                self.assertLess(time.monotonic() - started, 1)
            finally:
                release.set()
            self.assertEqual(client.read_exact(1, time.monotonic() + 5), b"R")

    def test_peer_disconnect_wakes_pending_read(self):
        release = threading.Event()

        def serve(connection: Any) -> None:
            if not release.wait(5):
                raise AssertionError("disconnect test never released its peer")

        with _named_pipe_peer(serve) as client:
            release.set()
            with self.assertRaisesRegex(ConnectionError, "endpoint closed"):
                client.read_exact(1, time.monotonic() + 5)


if __name__ == "__main__":
    unittest.main()
