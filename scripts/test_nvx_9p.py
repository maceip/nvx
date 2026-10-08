#!/usr/bin/env python3
"""Maintained tests for the 9P2000.L share server (scripts/nvx_tools/nvx_9p.py).

Drives a live server over loopback with a small frame-level client, the way
the Linux 9p client would: handshake, walk, getattr, open/read, readdir,
error paths, containment, and a read-write round-trip.
"""

import os
import socket
import stat
import struct
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import cast
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
from nvx_tools import nvx_9p  # noqa: E402


def frame(msgtype: int, tag: int, payload: bytes) -> bytes:
    body = struct.pack("<BH", msgtype, tag) + payload
    return struct.pack("<I", len(body) + 4) + body


def enc_str(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


class Client:
    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.tag = 0

    def _recvall(self, size: int) -> bytes:
        out = bytearray()
        while len(out) < size:
            chunk = self.sock.recv(size - len(out))
            if not chunk:
                raise AssertionError("server closed the connection")
            out += chunk
        return bytes(out)

    def call(self, msgtype: int, payload: bytes) -> tuple[int, bytes]:
        self.tag = (self.tag + 1) & 0xFFFF
        if self.tag == 0xFFFF:
            self.tag = 0
        self.sock.sendall(frame(msgtype, self.tag, payload))
        size = struct.unpack("<I", self._recvall(4))[0]
        body = self._recvall(size - 4)
        rtype, rtag = struct.unpack_from("<BH", body)
        assert rtag == self.tag, (rtag, self.tag)
        return rtype, body[3:]

    def version(self, msize: int = 8192, text: str = "9P2000.L") -> tuple[int, bytes]:
        return self.call(nvx_9p.T_VERSION, struct.pack("<I", msize) + enc_str(text))

    def attach(self, fid: int = 1) -> tuple[int, bytes]:
        return self.call(
            nvx_9p.T_ATTACH,
            struct.pack("<II", fid, nvx_9p.NOFID) + enc_str("root") + enc_str(""),
        )

    def walk(self, fid: int, newfid: int, *names: str) -> tuple[int, bytes]:
        payload = struct.pack("<IIH", fid, newfid, len(names))
        for name in names:
            payload += enc_str(name)
        return self.call(nvx_9p.T_WALK, payload)

    def lopen(self, fid: int, flags: int = 0) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_LOPEN, struct.pack("<II", fid, flags))

    def read(self, fid: int, offset: int = 0, count: int = 8192) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_READ, struct.pack("<IQI", fid, offset, count))

    def readdir(
        self, fid: int, offset: int = 0, count: int = 8192
    ) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_READDIR, struct.pack("<IQI", fid, offset, count))

    def getattr(self, fid: int, mask: int = 16383) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_GETATTR, struct.pack("<IQ", fid, mask))

    def clunk(self, fid: int) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_CLUNK, struct.pack("<I", fid))

    def statfs(self, fid: int) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_STATFS, struct.pack("<I", fid))

    def lcreate(
        self, fid: int, name: str, flags: int = 2, mode: int = 420
    ) -> tuple[int, bytes]:
        return self.call(
            nvx_9p.T_LCREATE,
            struct.pack("<I", fid)
            + enc_str(name)
            + struct.pack("<III", flags, mode, 0),
        )

    def write(self, fid: int, data: bytes, offset: int = 0) -> tuple[int, bytes]:
        return self.call(
            nvx_9p.T_WRITE, struct.pack("<IQI", fid, offset, len(data)) + data
        )

    def unlinkat(self, dirfid: int, name: str, flags: int = 0) -> tuple[int, bytes]:
        return self.call(
            nvx_9p.T_UNLINKAT,
            struct.pack("<I", dirfid) + enc_str(name) + struct.pack("<I", flags),
        )

    def mkdir(self, dfid: int, name: str, mode: int = 493) -> tuple[int, bytes]:
        return self.call(
            nvx_9p.T_MKDIR,
            struct.pack("<I", dfid) + enc_str(name) + struct.pack("<II", mode, 0),
        )

    def readlink(self, fid: int) -> tuple[int, bytes]:
        return self.call(nvx_9p.T_READLINK, struct.pack("<I", fid))


def rlerror_code(payload: bytes) -> int:
    (code,) = struct.unpack("<I", payload[:4])
    return code


class ServerCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "hello.txt").write_bytes(b"hello-9p-content")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "nested.txt").write_bytes(b"nested")
        os.symlink("hello.txt", self.root / "inside-link")
        self.sock: socket.socket | None = None
        self.share: nvx_9p.Share | None = None
        self.server: nvx_9p.Server | None = None
        self.thread: threading.Thread | None = None

    def tearDown(self) -> None:
        if self.sock is not None:
            self.sock.close()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=10)
        self.tmp.cleanup()

    def start(self, read_write: bool = False) -> Client:
        if read_write and os.name != "posix":
            self.skipTest("writable shares require POSIX directory descriptors")
        self.share = nvx_9p.Share(str(self.root), read_write=read_write)
        self.server = nvx_9p.Server(self.share, ("127.0.0.1", 0))
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        self.thread.start()
        self.sock = socket.create_connection(
            ("127.0.0.1", self.server.server_address[1])
        )
        return Client(self.sock)

    def handshake(self, client: Client) -> None:
        rtype, _payload = client.version()
        self.assertEqual(rtype, nvx_9p.R_VERSION)
        rtype, _payload = client.attach()
        self.assertEqual(rtype, nvx_9p.R_ATTACH)


class HandshakeTests(ServerCase):
    def test_version_and_attach(self) -> None:
        client = self.start()
        self.handshake(client)

    def test_wrong_version_rejected(self) -> None:
        client = self.start()
        rtype, payload = client.version(text="9P2000")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EOPNOTSUPP)

    def test_unknown_message_is_unimplemented(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, payload = client.call(200, b"")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_ENOSYS)


class ReadTests(ServerCase):
    def test_walk_getattr_read_round_trip(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 2, "hello.txt")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        (nwqid,) = struct.unpack("<H", payload[:2])
        self.assertEqual(nwqid, 1)
        rtype, payload = client.getattr(2)
        self.assertEqual(rtype, nvx_9p.R_GETATTR)
        (valid,) = struct.unpack("<Q", payload[:8])
        self.assertEqual(valid, nvx_9p.G_ALL)
        mode = struct.unpack_from("<I", payload, 8 + 13)[0]
        self.assertTrue(stat.S_ISREG(mode))
        size = struct.unpack_from("<Q", payload, 8 + 13 + 4 + 4 + 4 + 8 + 8)[0]
        self.assertEqual(size, len(b"hello-9p-content"))
        rtype, payload = client.lopen(2, 0)
        self.assertEqual(rtype, nvx_9p.R_LOPEN)
        rtype, payload = client.read(2, 6, 4)
        self.assertEqual(rtype, nvx_9p.R_READ)
        (_count,) = struct.unpack("<I", payload[:4])
        self.assertEqual(payload[4 : 4 + _count], b"9p-c")
        rtype, _payload = client.clunk(2)
        self.assertEqual(rtype, nvx_9p.R_CLUNK)

    def test_readdir_lists_entries(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, _payload = client.walk(1, 3, "sub")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lopen(3, 0)
        self.assertEqual(rtype, nvx_9p.R_LOPEN)
        rtype, payload = client.readdir(3, 0, 8192)
        self.assertEqual(rtype, nvx_9p.R_READDIR)
        (_count,) = struct.unpack("<I", payload[:4])
        self.assertIn(b"nested.txt", payload)

    def test_statfs(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, payload = client.statfs(1)
        self.assertEqual(rtype, nvx_9p.R_STATFS)
        (fstype,) = struct.unpack("<I", payload[:4])
        self.assertEqual(fstype, nvx_9p.V9FS_MAGIC)

    def test_missing_file_reports_enoent(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 9, "no-such-file")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_ENOENT)

    def test_readlink(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, _payload = client.walk(1, 11, "inside-link")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.readlink(11)
        self.assertEqual(rtype, nvx_9p.R_READLINK)
        (length,) = struct.unpack("<H", payload[:2])
        self.assertEqual(payload[2 : 2 + length], b"hello.txt")


class ContainmentTests(ServerCase):
    def test_dotdot_stays_inside(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 21, "..", "..", "hello.txt")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        (nwqid,) = struct.unpack("<H", payload[:2])
        self.assertEqual(nwqid, 3)

    def test_outside_symlink_open_denied(self) -> None:
        os.symlink("/etc/hostname", self.root / "evil-link")
        client = self.start()
        self.handshake(client)
        rtype, _payload = client.walk(1, 22, "evil-link")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lopen(22, 0)
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EACCES)

    def test_outside_symlink_traversal_denied(self) -> None:
        os.symlink("/etc", self.root / "evil-dir")
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 23, "evil-dir", "hostname")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EACCES)

    def test_slash_in_name_rejected(self) -> None:
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 24, "sub/nested.txt")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EINVAL)


class ReadOnlyTests(ServerCase):
    def test_create_rejected_read_only(self) -> None:
        client = self.start(read_write=False)
        self.handshake(client)
        rtype, _payload = client.walk(1, 31)
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lcreate(31, "new.txt")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EROFS)
        self.assertFalse((self.root / "new.txt").exists())

    def test_write_rejected_read_only(self) -> None:
        client = self.start(read_write=False)
        self.handshake(client)
        rtype, _payload = client.walk(1, 32, "hello.txt")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lopen(32, 1)  # O_WRONLY
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EROFS)
        self.assertEqual((self.root / "hello.txt").read_bytes(), b"hello-9p-content")


class ReadWriteTests(ServerCase):
    def test_create_write_read_delete_round_trip(self) -> None:
        client = self.start(read_write=True)
        self.handshake(client)
        rtype, _payload = client.walk(1, 41)
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, _payload = client.lcreate(41, "guest.txt")
        self.assertEqual(rtype, nvx_9p.R_LCREATE)
        rtype, payload = client.write(41, b"from-guest")
        self.assertEqual(rtype, nvx_9p.R_WRITE)
        (written,) = struct.unpack("<I", payload[:4])
        self.assertEqual(written, len(b"from-guest"))
        rtype, _payload = client.clunk(41)
        self.assertEqual(rtype, nvx_9p.R_CLUNK)
        self.assertEqual((self.root / "guest.txt").read_bytes(), b"from-guest")
        # Read it back through a fresh fid, then delete it.
        rtype, _payload = client.walk(1, 42, "guest.txt")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, _payload = client.lopen(42, 0)
        self.assertEqual(rtype, nvx_9p.R_LOPEN)
        rtype, payload = client.read(42, 0, 64)
        (_count,) = struct.unpack("<I", payload[:4])
        self.assertEqual(payload[4 : 4 + _count], b"from-guest")
        rtype, _payload = client.unlinkat(1, "guest.txt")
        self.assertEqual(rtype, nvx_9p.R_UNLINKAT)
        rtype, payload = client.read(42, 0, 64)
        self.assertEqual(rtype, nvx_9p.R_READ)
        self.assertEqual(payload[4:], b"from-guest")
        rtype, _payload = client.clunk(42)
        self.assertEqual(rtype, nvx_9p.R_CLUNK)
        self.assertFalse((self.root / "guest.txt").exists())

    def test_directory_swap_cannot_escape_on_open(self) -> None:
        if os.name == "nt":
            self.skipTest("POSIX symlink race")
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            (outside / "victim").write_bytes(b"outside")
            (self.root / "sub" / "victim").write_bytes(b"inside")
            client = self.start(read_write=True)
            self.handshake(client)
            self.assertEqual(client.walk(1, 43, "sub", "victim")[0], nvx_9p.R_WALK)
            original_open = nvx_9p.open_shared_file
            assert self.share is not None
            original_parent = self.share.parent_fd
            calls = 0

            def swap() -> None:
                (self.root / "sub").rename(self.root / "saved")
                (self.root / "sub").symlink_to(outside, target_is_directory=True)

            def open_swap(path: str, flags: int, mode: int = 0o666) -> int:
                swap()
                return original_open(path, flags, mode)

            def parent_swap(path: str, follow_final: bool = False):
                nonlocal calls
                calls += 1
                if calls == 2:
                    swap()
                return original_parent(path, follow_final=follow_final)

            with (
                patch.object(nvx_9p, "open_shared_file", side_effect=open_swap),
                patch.object(self.share, "parent_fd", parent_swap),
            ):
                result = client.lopen(43, nvx_9p.O_TRUNC | 1)
            self.assertEqual(result[0], nvx_9p.R_LERROR)
            self.assertEqual((outside / "victim").read_bytes(), b"outside")

    def test_directory_swap_cannot_escape_namespace_mutations(self) -> None:
        if os.name != "posix":
            self.skipTest("POSIX directory descriptors")
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            (outside / "victim").write_bytes(b"outside")
            (outside / "src").write_bytes(b"outside-src")
            outside_mode = stat.S_IMODE((outside / "victim").stat().st_mode)
            client = self.start(read_write=True)
            self.handshake(client)
            assert self.share is not None
            for index, operation in enumerate(
                (
                    "create",
                    "unlink",
                    "remove",
                    "rename",
                    "renameat",
                    "link",
                    "mkdir",
                    "symlink",
                    "setattr",
                    "setattr-mode",
                )
            ):
                with self.subTest(operation=operation):
                    if operation == "setattr-mode" and (
                        os.chmod not in os.supports_dir_fd
                        or os.chmod not in os.supports_follow_symlinks
                    ):
                        continue
                    base = self.root / f"dir-{index}"
                    base.mkdir()
                    (base / "victim").write_bytes(b"inside")
                    (base / "src").write_bytes(b"inside-src")
                    fid = 50 + index * 2
                    self.assertEqual(client.walk(1, fid, base.name)[0], nvx_9p.R_WALK)
                    self.assertEqual(
                        client.walk(fid, fid + 1, "victim")[0], nvx_9p.R_WALK
                    )
                    old_child = cast(
                        Callable[[nvx_9p.Connection, nvx_9p.FidEntry, str], str],
                        vars(nvx_9p.Connection)["_child"],
                    )
                    old_parent = self.share.parent_fd
                    swapped = False

                    def swap(base: Path = base, index: int = index) -> None:
                        nonlocal swapped
                        if not swapped:
                            base.rename(self.root / f"saved-{index}")
                            base.symlink_to(outside, target_is_directory=True)
                            swapped = True

                    def child(
                        conn: nvx_9p.Connection,
                        entry: nvx_9p.FidEntry,
                        name: str,
                        original: Callable[
                            [nvx_9p.Connection, nvx_9p.FidEntry, str], str
                        ] = old_child,
                    ) -> str:
                        result = original(conn, entry, name)
                        swap()
                        return result

                    def parent(
                        path: str,
                        follow_final: bool = False,
                        operation: str = operation,
                        original: Callable[
                            [str, bool], AbstractContextManager[tuple[int, str]]
                        ] = old_parent,
                    ) -> AbstractContextManager[tuple[int, str]]:
                        if operation in ("remove", "setattr", "setattr-mode"):
                            swap()
                        return original(path, follow_final)

                    with (
                        patch.object(nvx_9p.Connection, "_child", child),
                        patch.object(self.share, "parent_fd", parent),
                    ):
                        if operation == "create":
                            reply = client.lcreate(fid, "victim")
                        elif operation == "unlink":
                            reply = client.unlinkat(fid, "victim")
                        elif operation == "remove":
                            reply = client.call(
                                nvx_9p.T_REMOVE, struct.pack("<I", fid + 1)
                            )
                        elif operation == "rename":
                            reply = client.call(
                                nvx_9p.T_RENAME,
                                struct.pack("<II", fid + 1, fid) + enc_str("src"),
                            )
                        elif operation == "renameat":
                            reply = client.call(
                                nvx_9p.T_RENAMEAT,
                                struct.pack("<I", fid)
                                + enc_str("victim")
                                + struct.pack("<I", fid)
                                + enc_str("src"),
                            )
                        elif operation == "link":
                            reply = client.call(
                                nvx_9p.T_LINK,
                                struct.pack("<II", fid, fid + 1) + enc_str("src"),
                            )
                        elif operation == "mkdir":
                            reply = client.mkdir(fid, "newdir")
                        elif operation == "symlink":
                            reply = client.call(
                                nvx_9p.T_SYMLINK,
                                struct.pack("<I", fid)
                                + enc_str("newlink")
                                + enc_str("victim")
                                + struct.pack("<I", 0),
                            )
                        else:
                            reply = client.call(
                                nvx_9p.T_SETATTR,
                                struct.pack(
                                    "<IQIIIQQQQQ",
                                    fid + 1,
                                    nvx_9p.S_MODE
                                    if operation == "setattr-mode"
                                    else nvx_9p.S_SIZE,
                                    0o777 if operation == "setattr-mode" else 0,
                                    0,
                                    0,
                                    0,
                                    0,
                                    0,
                                    0,
                                    0,
                                ),
                            )
                    self.assertTrue(swapped)
                    self.assertEqual(reply[0], nvx_9p.R_LERROR)
                    self.assertEqual((outside / "victim").read_bytes(), b"outside")
                    self.assertEqual(
                        stat.S_IMODE((outside / "victim").stat().st_mode), outside_mode
                    )
                    self.assertEqual((outside / "src").read_bytes(), b"outside-src")
                    self.assertFalse((outside / "newdir").exists())
                    self.assertFalse((outside / "newlink").exists())

    def test_in_share_symlink_writable_and_outside_symlink_denied(self) -> None:
        with tempfile.TemporaryDirectory() as outside_dir:
            outside = Path(outside_dir)
            (outside / "victim").write_bytes(b"outside")
            (self.root / "external").symlink_to(outside / "victim")
            client = self.start(read_write=True)
            self.handshake(client)
            self.assertEqual(client.walk(1, 90, "inside-link")[0], nvx_9p.R_WALK)
            self.assertEqual(client.lopen(90, nvx_9p.O_NOFOLLOW)[0], nvx_9p.R_LERROR)
            self.assertEqual(client.lopen(90, 1)[0], nvx_9p.R_LOPEN)
            self.assertEqual(client.write(90, b"safe")[0], nvx_9p.R_WRITE)
            self.assertTrue((self.root / "hello.txt").read_bytes().startswith(b"safe"))
            self.assertEqual(client.walk(1, 91, "external")[0], nvx_9p.R_WALK)
            result = client.lopen(91, nvx_9p.O_TRUNC | 1)
            self.assertEqual(result[0], nvx_9p.R_LERROR)
            self.assertEqual((outside / "victim").read_bytes(), b"outside")

    def test_chmod_unreadable_file_without_opening_it(self) -> None:
        file = self.root / "no-access"
        file.write_bytes(b"keep")
        file.chmod(0)
        try:
            client = self.start(read_write=True)
            self.handshake(client)
            self.assertEqual(client.walk(1, 92, "no-access")[0], nvx_9p.R_WALK)
            request = struct.pack(
                "<IQIIIQQQQQ", 92, nvx_9p.S_MODE, 0o600, 0, 0, 0, 0, 0, 0, 0
            )
            assert self.share is not None
            with patch.object(
                self.share, "secure_open", side_effect=PermissionError("no read access")
            ):
                response, payload = client.call(nvx_9p.T_SETATTR, request)
            if (
                os.chmod in os.supports_dir_fd
                and os.chmod in os.supports_follow_symlinks
            ):
                self.assertEqual(response, nvx_9p.R_SETATTR)
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)
            else:
                self.assertEqual(response, nvx_9p.R_LERROR)
                self.assertEqual(rlerror_code(payload), nvx_9p.L_EOPNOTSUPP)
                self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0)
        finally:
            file.chmod(0o600)
        self.assertEqual(file.read_bytes(), b"keep")

    def test_writable_share_rejects_missing_dirfd_support(self) -> None:
        with patch.object(nvx_9p.os, "supports_dir_fd", set[object]()):
            with self.assertRaises(nvx_9p.Error) as caught:
                nvx_9p.Share(str(self.root), read_write=True)
        self.assertEqual(caught.exception.linux_errno, nvx_9p.L_EOPNOTSUPP)

    def test_mkdir(self) -> None:
        client = self.start(read_write=True)
        self.handshake(client)
        rtype, _payload = client.mkdir(1, "newdir")
        self.assertEqual(rtype, nvx_9p.R_MKDIR)
        self.assertTrue((self.root / "newdir").is_dir())

    def test_mknod_refused(self) -> None:
        client = self.start(read_write=True)
        self.handshake(client)
        payload = (
            struct.pack("<I", 1) + enc_str("node") + struct.pack("<III", 0o600, 0, 0)
        )
        rtype, response = client.call(nvx_9p.T_MKNOD, payload)
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(response), nvx_9p.L_EPERM)

    def test_missing_root_rejected(self) -> None:
        with self.assertRaises(nvx_9p.Error):
            nvx_9p.Share(str(self.root / "nope"))


if __name__ == "__main__":
    unittest.main()
