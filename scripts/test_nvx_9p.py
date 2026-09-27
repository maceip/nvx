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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from nvx_tools import nvx_9p  # noqa: E402


def frame(msgtype, tag, payload):
    body = struct.pack("<BH", msgtype, tag) + payload
    return struct.pack("<I", len(body) + 4) + body


def enc_str(text):
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


class Client:
    def __init__(self, sock):
        self.sock = sock
        self.tag = 0

    def _recvall(self, size):
        out = bytearray()
        while len(out) < size:
            chunk = self.sock.recv(size - len(out))
            if not chunk:
                raise AssertionError("server closed the connection")
            out += chunk
        return bytes(out)

    def call(self, msgtype, payload):
        self.tag = (self.tag + 1) & 0xFFFF
        if self.tag == 0xFFFF:
            self.tag = 0
        self.sock.sendall(frame(msgtype, self.tag, payload))
        size = struct.unpack("<I", self._recvall(4))[0]
        body = self._recvall(size - 4)
        rtype, rtag = struct.unpack_from("<BH", body)
        assert rtag == self.tag, (rtag, self.tag)
        return rtype, body[3:]

    def version(self, msize=8192, text="9P2000.L"):
        return self.call(nvx_9p.T_VERSION, struct.pack("<I", msize) + enc_str(text))

    def attach(self, fid=1):
        return self.call(nvx_9p.T_ATTACH,
                         struct.pack("<II", fid, nvx_9p.NOFID) + enc_str("root") + enc_str(""))

    def walk(self, fid, newfid, *names):
        payload = struct.pack("<IIH", fid, newfid, len(names))
        for name in names:
            payload += enc_str(name)
        return self.call(nvx_9p.T_WALK, payload)

    def lopen(self, fid, flags=0):
        return self.call(nvx_9p.T_LOPEN, struct.pack("<II", fid, flags))

    def read(self, fid, offset=0, count=8192):
        return self.call(nvx_9p.T_READ, struct.pack("<IQI", fid, offset, count))

    def readdir(self, fid, offset=0, count=8192):
        return self.call(nvx_9p.T_READDIR, struct.pack("<IQI", fid, offset, count))

    def getattr(self, fid, mask=0x3FFF):
        return self.call(nvx_9p.T_GETATTR, struct.pack("<IQ", fid, mask))

    def clunk(self, fid):
        return self.call(nvx_9p.T_CLUNK, struct.pack("<I", fid))

    def statfs(self, fid):
        return self.call(nvx_9p.T_STATFS, struct.pack("<I", fid))

    def lcreate(self, fid, name, flags=2, mode=0o644):
        return self.call(nvx_9p.T_LCREATE,
                         struct.pack("<I", fid) + enc_str(name) + struct.pack("<III", flags, mode, 0))

    def write(self, fid, data, offset=0):
        return self.call(nvx_9p.T_WRITE, struct.pack("<IQI", fid, offset, len(data)) + data)

    def unlinkat(self, dirfid, name, flags=0):
        return self.call(nvx_9p.T_UNLINKAT,
                         struct.pack("<I", dirfid) + enc_str(name) + struct.pack("<I", flags))

    def mkdir(self, dfid, name, mode=0o755):
        return self.call(nvx_9p.T_MKDIR,
                         struct.pack("<I", dfid) + enc_str(name) + struct.pack("<II", mode, 0))

    def readlink(self, fid):
        return self.call(nvx_9p.T_READLINK, struct.pack("<I", fid))


def rlerror_code(payload):
    (code,) = struct.unpack("<I", payload[:4])
    return code


class ServerCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "hello.txt").write_bytes(b"hello-9p-content")
        (self.root / "sub").mkdir()
        (self.root / "sub" / "nested.txt").write_bytes(b"nested")
        os.symlink("hello.txt", self.root / "inside-link")
        self.sock = None
        self.share = None
        self.server = None
        self.thread = None

    def tearDown(self):
        if self.sock is not None:
            self.sock.close()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=10)
        self.tmp.cleanup()

    def start(self, read_write=False):
        self.share = nvx_9p.Share(str(self.root), read_write=read_write)
        self.server = nvx_9p.Server(self.share, ("127.0.0.1", 0))
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.sock = socket.create_connection(("127.0.0.1", self.server.server_address[1]))
        return Client(self.sock)

    def handshake(self, client):
        rtype, payload = client.version()
        self.assertEqual(rtype, nvx_9p.R_VERSION)
        rtype, _payload = client.attach()
        self.assertEqual(rtype, nvx_9p.R_ATTACH)


class HandshakeTests(ServerCase):
    def test_version_and_attach(self):
        client = self.start()
        self.handshake(client)

    def test_wrong_version_rejected(self):
        client = self.start()
        rtype, payload = client.version(text="9P2000")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EOPNOTSUPP)

    def test_unknown_message_is_unimplemented(self):
        client = self.start()
        self.handshake(client)
        rtype, payload = client.call(200, b"")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_ENOSYS)


class ReadTests(ServerCase):
    def test_walk_getattr_read_round_trip(self):
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
        (count,) = struct.unpack("<I", payload[:4])
        self.assertEqual(payload[4:4 + count], b"9p-c")
        rtype, _payload = client.clunk(2)
        self.assertEqual(rtype, nvx_9p.R_CLUNK)

    def test_readdir_lists_entries(self):
        client = self.start()
        self.handshake(client)
        rtype, _payload = client.walk(1, 3, "sub")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lopen(3, 0)
        self.assertEqual(rtype, nvx_9p.R_LOPEN)
        rtype, payload = client.readdir(3, 0, 8192)
        self.assertEqual(rtype, nvx_9p.R_READDIR)
        (count,) = struct.unpack("<I", payload[:4])
        self.assertIn(b"nested.txt", payload)

    def test_statfs(self):
        client = self.start()
        self.handshake(client)
        rtype, payload = client.statfs(1)
        self.assertEqual(rtype, nvx_9p.R_STATFS)
        (fstype,) = struct.unpack("<I", payload[:4])
        self.assertEqual(fstype, nvx_9p.V9FS_MAGIC)

    def test_missing_file_reports_enoent(self):
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 9, "no-such-file")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_ENOENT)

    def test_readlink(self):
        client = self.start()
        self.handshake(client)
        rtype, _payload = client.walk(1, 11, "inside-link")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.readlink(11)
        self.assertEqual(rtype, nvx_9p.R_READLINK)
        (length,) = struct.unpack("<H", payload[:2])
        self.assertEqual(payload[2:2 + length], b"hello.txt")


class ContainmentTests(ServerCase):
    def test_dotdot_stays_inside(self):
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 21, "..", "..", "hello.txt")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        (nwqid,) = struct.unpack("<H", payload[:2])
        self.assertEqual(nwqid, 3)

    def test_outside_symlink_open_denied(self):
        os.symlink("/etc/hostname", self.root / "evil-link")
        client = self.start()
        self.handshake(client)
        rtype, _payload = client.walk(1, 22, "evil-link")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lopen(22, 0)
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EACCES)

    def test_outside_symlink_traversal_denied(self):
        os.symlink("/etc", self.root / "evil-dir")
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 23, "evil-dir", "hostname")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EACCES)

    def test_slash_in_name_rejected(self):
        client = self.start()
        self.handshake(client)
        rtype, payload = client.walk(1, 24, "sub/nested.txt")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EINVAL)


class ReadOnlyTests(ServerCase):
    def test_create_rejected_read_only(self):
        client = self.start(read_write=False)
        self.handshake(client)
        rtype, _payload = client.walk(1, 31)
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lcreate(31, "new.txt")
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EROFS)
        self.assertFalse((self.root / "new.txt").exists())

    def test_write_rejected_read_only(self):
        client = self.start(read_write=False)
        self.handshake(client)
        rtype, _payload = client.walk(1, 32, "hello.txt")
        self.assertEqual(rtype, nvx_9p.R_WALK)
        rtype, payload = client.lopen(32, 1)  # O_WRONLY
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(payload), nvx_9p.L_EROFS)
        self.assertEqual((self.root / "hello.txt").read_bytes(), b"hello-9p-content")


class ReadWriteTests(ServerCase):
    def test_create_write_read_delete_round_trip(self):
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
        (count,) = struct.unpack("<I", payload[:4])
        self.assertEqual(payload[4:4 + count], b"from-guest")
        rtype, _payload = client.unlinkat(1, "guest.txt")
        self.assertEqual(rtype, nvx_9p.R_UNLINKAT)
        self.assertFalse((self.root / "guest.txt").exists())

    def test_mkdir(self):
        client = self.start(read_write=True)
        self.handshake(client)
        rtype, _payload = client.mkdir(1, "newdir")
        self.assertEqual(rtype, nvx_9p.R_MKDIR)
        self.assertTrue((self.root / "newdir").is_dir())

    def test_mknod_refused(self):
        client = self.start(read_write=True)
        self.handshake(client)
        payload = struct.pack("<I", 1) + enc_str("node") + struct.pack("<III", 0o600, 0, 0)
        rtype, response = client.call(nvx_9p.T_MKNOD, payload)
        self.assertEqual(rtype, nvx_9p.R_LERROR)
        self.assertEqual(rlerror_code(response), nvx_9p.L_EPERM)

    def test_missing_root_rejected(self):
        with self.assertRaises(nvx_9p.Error):
            nvx_9p.Share(str(self.root / "nope"))


if __name__ == "__main__":
    unittest.main()
