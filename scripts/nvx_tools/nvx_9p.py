#!/usr/bin/env python3
"""Minimal 9P2000.L file server for sharing a host directory with NVX guests.

Stdlib only. Serves one rooted directory tree over TCP (default loopback
only). Read-only by default; writes require --read-write. All guest paths
are resolved and contained under the root on every operation.

Used with the consomme `gwloopback` mapping: the guest mounts
`-t 9p -o trans=tcp,version=9p2000.L,port=<port> <gateway> <mntpoint>`.
"""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import socketserver
import stat
import struct
import sys
import threading
from typing import Any, TypedDict, cast

VERSION = "9P2000.L"
NOFID = 0xFFFFFFFF
NOTAG = 0xFFFF
MAX_MESSAGE = 256 * 1024

# Message types (9P2000.L).
T_LERROR, R_LERROR = 6, 7
T_STATFS, R_STATFS = 8, 9
T_LOPEN, R_LOPEN = 12, 13
T_LCREATE, R_LCREATE = 14, 15
T_SYMLINK, R_SYMLINK = 16, 17
T_MKNOD, R_MKNOD = 18, 19
T_RENAME, R_RENAME = 20, 21
T_READLINK, R_READLINK = 22, 23
T_GETATTR, R_GETATTR = 24, 25
T_SETATTR, R_SETATTR = 26, 27
T_XATTRWALK, R_XATTRWALK = 30, 31
T_XATTRCREATE, R_XATTRCREATE = 32, 33
T_READDIR, R_READDIR = 40, 41
T_FSYNC, R_FSYNC = 50, 51
T_LOCK, R_LOCK = 52, 53
T_GETLOCK, R_GETLOCK = 54, 55
T_LINK, R_LINK = 70, 71
T_MKDIR, R_MKDIR = 72, 73
T_RENAMEAT, R_RENAMEAT = 74, 75
T_UNLINKAT, R_UNLINKAT = 76, 77
T_VERSION, R_VERSION = 100, 101
T_AUTH, R_AUTH = 102, 103
T_ATTACH, R_ATTACH = 104, 105
T_ERROR, R_ERROR = 106, 107
T_FLUSH, R_FLUSH = 108, 109
T_WALK, R_WALK = 110, 111
T_READ, R_READ = 116, 117
T_WRITE, R_WRITE = 118, 119
T_CLUNK, R_CLUNK = 120, 121
T_REMOVE, R_REMOVE = 122, 123

# Linux errno numbers (deliberately NOT os.errno: several differ on macOS,
# e.g. ENOSYS is 78 on Darwin but 38 on Linux).
L_EPERM = 1
L_ENOENT = 2
L_EIO = 5
L_EACCES = 13
L_EEXIST = 17
L_ENOTDIR = 20
L_EISDIR = 21
L_EINVAL = 22
L_EFBIG = 27
L_ENOSPC = 28
L_EROFS = 30
L_ENOSYS = 38
L_ENOTEMPTY = 39
L_ELOOP = 40
L_ENODATA = 61
L_EOPNOTSUPP = 95

_HOST_TO_LINUX_ERRNO = {
    errno.EPERM: L_EPERM,
    errno.ENOENT: L_ENOENT,
    errno.EIO: L_EIO,
    errno.EACCES: L_EACCES,
    errno.EEXIST: L_EEXIST,
    errno.ENOTDIR: L_ENOTDIR,
    errno.EISDIR: L_EISDIR,
    errno.EINVAL: L_EINVAL,
    errno.EFBIG: L_EFBIG,
    errno.ENOSPC: L_ENOSPC,
    errno.EROFS: L_EROFS,
    errno.ENOSYS: L_ENOSYS,
    errno.ENOTEMPTY: L_ENOTEMPTY,
    getattr(errno, "ELOOP", 40): L_ELOOP,
    getattr(errno, "ENODATA", 61): L_ENODATA,
    errno.EOPNOTSUPP: L_EOPNOTSUPP,
}

_LINUX_STRERROR = {
    L_EPERM: "operation not permitted",
    L_ENOENT: "no such file or directory",
    L_EIO: "I/O error",
    L_EACCES: "permission denied",
    L_EEXIST: "file exists",
    L_ENOTDIR: "not a directory",
    L_EISDIR: "is a directory",
    L_EINVAL: "invalid argument",
    L_EFBIG: "file too large",
    L_ENOSPC: "no space left on device",
    L_EROFS: "read-only file system",
    L_ENOSYS: "function not implemented",
    L_ENOTEMPTY: "directory not empty",
    L_ELOOP: "too many levels of symbolic links",
    L_ENODATA: "no data available",
    L_EOPNOTSUPP: "operation not supported",
}

# QID types.
QT_DIR = 0x80
QT_SYMLINK = 0x02
QT_FILE = 0x00

# getattr masks.
G_MODE, G_NLINK, G_UID, G_GID = 0x1, 0x2, 0x4, 0x8
G_RDEV, G_ATIME, G_MTIME, G_CTIME = 0x10, 0x20, 0x40, 0x80
G_INO, G_SIZE, G_BLOCKS = 0x100, 0x200, 0x400
G_BTIME, G_GEN, G_DATA_VERSION = 0x800, 0x1000, 0x2000
G_ALL = 0x3FFF

# setattr masks.
S_MODE, S_UID, S_GID, S_SIZE = 0x1, 0x2, 0x4, 0x8
S_ATIME, S_MTIME = 0x10, 0x20

# Linux open flags of interest (same on all Linux arches).
O_ACCMODE = 0o3
O_CREAT = 0o100
O_EXCL = 0o200
O_TRUNC = 0o1000
O_APPEND = 0o2000
O_DIRECTORY = 0o40000
O_NOFOLLOW = 0o400000

V9FS_MAGIC = 0x01021997


class Error(Exception):
    def __init__(self, linux_errno: int) -> None:
        super().__init__(_LINUX_STRERROR.get(linux_errno, "error"))
        self.linux_errno = linux_errno


def host_error(exc: Exception) -> Error:
    if isinstance(exc, OSError) and exc.errno is not None:
        return Error(_HOST_TO_LINUX_ERRNO.get(exc.errno, L_EIO))
    return Error(L_EIO)


class Reader:
    def __init__(self, data: bytes) -> None:
        self._view = memoryview(data)
        self._pos = 0

    def remaining(self) -> int:
        return len(self._view) - self._pos

    def take(self, size: int, fmt: str) -> Any:
        if self.remaining() < size:
            raise Error(L_EIO)
        values = struct.unpack_from(fmt, self._view, self._pos)
        self._pos += size
        return values[0] if len(values) == 1 else values

    def u8(self) -> int:
        return self.take(1, "<B")

    def u16(self) -> int:
        return self.take(2, "<H")

    def u32(self) -> int:
        return self.take(4, "<I")

    def u64(self) -> int:
        return self.take(8, "<Q")

    def data(self, size: int) -> bytes:
        if self.remaining() < size:
            raise Error(L_EIO)
        out = bytes(self._view[self._pos : self._pos + size])
        self._pos += size
        return out

    def string(self) -> bytes:
        return self.data(self.u16())


class Writer:
    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def u8(self, value: int) -> None:
        self._parts.append(struct.pack("<B", value))

    def u16(self, value: int) -> None:
        self._parts.append(struct.pack("<H", value))

    def u32(self, value: int) -> None:
        self._parts.append(struct.pack("<I", value & 0xFFFFFFFF))

    def u64(self, value: int) -> None:
        self._parts.append(struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))

    def data(self, value: bytes) -> None:
        self._parts.append(bytes(value))

    def string(self, value: str | bytes) -> None:
        if isinstance(value, str):
            value = value.encode("utf-8", "surrogateescape")
        self.u16(len(value))
        self.data(value)

    def qid(self, qid: tuple[int, int, int]) -> None:
        self.u8(qid[0])
        self.u32(qid[1])
        self.u64(qid[2])

    def bytes(self) -> bytes:
        return b"".join(self._parts)


def qid_for(st: os.stat_result) -> tuple[int, int, int]:
    if stat.S_ISDIR(st.st_mode):
        kind = QT_DIR
    elif stat.S_ISLNK(st.st_mode):
        kind = QT_SYMLINK
    else:
        kind = QT_FILE
    return (kind, int(st.st_mtime) & 0xFFFFFFFF, st.st_ino & 0xFFFFFFFFFFFFFFFF)


def dirent_type(st: os.stat_result) -> int:
    mode = st.st_mode
    if stat.S_ISDIR(mode):
        return 4
    if stat.S_ISREG(mode):
        return 8
    if stat.S_ISLNK(mode):
        return 10
    if stat.S_ISFIFO(mode):
        return 1
    if stat.S_ISCHR(mode):
        return 2
    if stat.S_ISBLK(mode):
        return 6
    if stat.S_ISSOCK(mode):
        return 12
    return 0


class FidEntry(TypedDict):
    path: str
    file: OpenFile | None


class Share:
    """One rooted, optionally read-only directory tree."""

    def __init__(self, root: str, read_write: bool = False) -> None:
        self.root = os.path.realpath(root)
        if not os.path.isdir(self.root):
            raise Error(L_ENOTDIR)
        self.read_write = read_write
        self._lock = threading.Lock()
        self._fids: dict[int, FidEntry] = {}

    def contain(self, path: str) -> str:
        real = os.path.realpath(path)
        if real != self.root and not real.startswith(self.root + os.sep):
            raise Error(L_EACCES)
        return real

    def resolve(self, *names: str) -> str:
        """Join names under the root and containment-check the result."""
        path = self.root
        for name in names:
            if name in ("", "."):
                continue
            if name == "..":
                path = os.path.dirname(path)
                if len(path) < len(self.root):
                    path = self.root
                continue
            path = os.path.join(path, name)
        return self.contain(path)

    def check_write(self) -> None:
        if not self.read_write:
            raise Error(L_EROFS)

    def fid_get(self, fid: int) -> FidEntry:
        with self._lock:
            try:
                return self._fids[fid]
            except KeyError:
                raise Error(L_EINVAL) from None

    def fid_set(self, fid: int, entry: FidEntry) -> None:
        with self._lock:
            if fid in self._fids:
                raise Error(L_EINVAL)
            self._fids[fid] = entry

    def fid_drop(self, fid: int) -> FidEntry | None:
        with self._lock:
            return self._fids.pop(fid, None)

    def stat_path(self, path: str) -> os.stat_result:
        try:
            return os.stat(path)
        except OSError as exc:
            raise host_error(exc) from None

    def lstat_path(self, path: str) -> os.stat_result:
        try:
            return os.lstat(path)
        except OSError as exc:
            raise host_error(exc) from None


def open_shared_file(path: str, flags: int, mode: int = 0o666) -> int:
    """Keep guest open-file unlink/rename semantics on Windows, too."""
    if os.name != "nt":
        return os.open(path, flags, mode)
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    access_mode = flags & (os.O_WRONLY | os.O_RDWR)
    access = 0x80000000 if access_mode == os.O_RDONLY else 0x40000000
    if access_mode == os.O_RDWR:
        access |= 0x80000000
    if flags & os.O_CREAT:
        disposition = 1 if flags & os.O_EXCL else (2 if flags & os.O_TRUNC else 4)
    else:
        disposition = 5 if flags & os.O_TRUNC else 3
    # FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE. The path has
    # already passed the share's containment and read-only checks.
    handle = create_file(path, access, 0x7, None, disposition, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return msvcrt.open_osfhandle(
            handle, access_mode | (flags & os.O_APPEND) | os.O_BINARY | os.O_NOINHERIT
        )
    except BaseException:
        kernel32.CloseHandle(handle)
        raise


class OpenFile:
    def __init__(self, path: str, fd: int | None, append: bool) -> None:
        self.path = path
        self.fd = fd
        self.append = append

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None

    def fileno(self) -> int:
        if self.fd is None:
            raise Error(L_EINVAL)
        return self.fd


class Connection:
    def __init__(self, share: Share) -> None:
        self.share = share
        self.msize = MAX_MESSAGE

    # -- frame helpers -------------------------------------------------

    def reply(self, rtype: int, tag: int, payload: bytes) -> bytes:
        body = struct.pack("<BH", rtype, tag) + payload
        return struct.pack("<I", len(body) + 4) + body

    def rlerror(self, tag: int, exc: Exception) -> bytes:
        code = exc.linux_errno if isinstance(exc, Error) else L_EIO
        w = Writer()
        w.u32(code)
        return self.reply(R_LERROR, tag, w.bytes())

    # -- dispatch ------------------------------------------------------

    def handle(self, msgtype: int, tag: int, payload: bytes) -> bytes:
        handler = {
            T_VERSION: self.on_version,
            T_AUTH: self.on_auth,
            T_ATTACH: self.on_attach,
            T_FLUSH: self.on_flush,
            T_WALK: self.on_walk,
            T_READ: self.on_read,
            T_WRITE: self.on_write,
            T_CLUNK: self.on_clunk,
            T_REMOVE: self.on_remove,
            T_STATFS: self.on_statfs,
            T_LOPEN: self.on_lopen,
            T_LCREATE: self.on_lcreate,
            T_SYMLINK: self.on_symlink,
            T_MKNOD: self.on_mknod,
            T_RENAME: self.on_rename,
            T_READLINK: self.on_readlink,
            T_GETATTR: self.on_getattr,
            T_SETATTR: self.on_setattr,
            T_XATTRWALK: self.on_xattrwalk,
            T_XATTRCREATE: self.on_xattrcreate,
            T_READDIR: self.on_readdir,
            T_FSYNC: self.on_fsync,
            T_LOCK: self.on_lock,
            T_GETLOCK: self.on_getlock,
            T_LINK: self.on_link,
            T_MKDIR: self.on_mkdir,
            T_RENAMEAT: self.on_renameat,
            T_UNLINKAT: self.on_unlinkat,
        }.get(msgtype)
        if handler is None:
            return self.rlerror(tag, Error(L_ENOSYS))
        try:
            rtype, body = handler(Reader(payload))
        except Error as exc:
            return self.rlerror(tag, exc)
        except Exception:  # never leak a traceback onto the wire
            return self.rlerror(tag, Error(L_EIO))
        return self.reply(rtype, tag, body)

    # -- base protocol -------------------------------------------------

    def on_version(self, r: Reader) -> tuple[int, bytes]:
        msize = r.u32()
        version = r.string().decode("utf-8", "replace")
        if version != VERSION:
            raise Error(L_EOPNOTSUPP)
        self.msize = min(max(msize, 512), MAX_MESSAGE)
        w = Writer()
        w.u32(self.msize)
        w.string(VERSION)
        return R_VERSION, w.bytes()

    def on_auth(self, r: Reader) -> tuple[int, bytes]:
        raise Error(L_EOPNOTSUPP)

    def on_attach(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        _afid = r.u32()
        _uname = r.string()
        _aname = r.string()
        st = self.share.stat_path(self.share.root)
        self.share.fid_set(fid, {"path": self.share.root, "file": None})
        w = Writer()
        w.qid(qid_for(st))
        return R_ATTACH, w.bytes()

    def on_flush(self, r: Reader) -> tuple[int, bytes]:
        _oldtag = r.u16()
        return R_FLUSH, b""

    def on_walk(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        newfid = r.u32()
        names = [r.string().decode("utf-8", "surrogateescape") for _ in range(r.u16())]
        entry = self.share.fid_get(fid)
        path = entry["path"]
        if entry["file"] is not None:
            raise Error(L_EINVAL)
        if not names:
            if newfid != fid:
                self.share.fid_set(newfid, {"path": path, "file": None})
            return self._rwalk(path)
        current = path
        qids: list[tuple[int, int, int]] = []
        for index, name in enumerate(names):
            last = index == len(names) - 1
            if "/" in name or "\0" in name:
                raise Error(L_EINVAL)
            if last:
                # Final element: resolve lexically without following a
                # trailing symlink, so readlink/O_NOFOLLOW keep working.
                # Open/read paths re-resolve and containment-check.
                if name in ("", "."):
                    lexical = current
                elif name == "..":
                    lexical = (
                        current
                        if current == self.share.root
                        else os.path.dirname(current)
                    )
                else:
                    lexical = os.path.join(current, name)
                real = os.path.realpath(lexical)
                if real != self.share.root and not real.startswith(
                    self.share.root + os.sep
                ):
                    # Either lexically outside, or a symlink pointing
                    # outside: only the link itself may be referenced.
                    if os.path.lexists(lexical) and os.path.islink(lexical):
                        current = lexical
                    else:
                        raise Error(L_EACCES)
                else:
                    current = lexical if os.path.islink(lexical) else real
                st = self.share.lstat_path(current)
            else:
                rel = os.path.relpath(current, self.share.root)
                parts = [] if rel == "." else [rel]
                current = self.share.resolve(*(parts + [name]))
                try:
                    st = self.share.stat_path(current)
                except Error:
                    if qids:
                        w = Writer()
                        w.u16(len(qids))
                        for qid in qids:
                            w.qid(qid)
                        return R_WALK, w.bytes()
                    raise
                if not stat.S_ISDIR(st.st_mode):
                    raise Error(L_ENOTDIR)
            qids.append(qid_for(st))
        self.share.fid_set(newfid, {"path": current, "file": None})
        w = Writer()
        w.u16(len(qids))
        for qid in qids:
            w.qid(qid)
        return R_WALK, w.bytes()

    def _rwalk(self, path: str) -> tuple[int, bytes]:
        self.share.stat_path(path)
        w = Writer()
        w.u16(0)
        return R_WALK, w.bytes()

    # -- I/O -----------------------------------------------------------

    def _open_flags(self, flags: int, for_dir: bool) -> int:
        access = flags & O_ACCMODE
        if access != 0 or (flags & (O_CREAT | O_TRUNC | O_APPEND)):
            self.share.check_write()
        if for_dir and access != 0:
            raise Error(L_EISDIR)
        return access

    def on_lopen(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        flags = r.u32()
        entry = self.share.fid_get(fid)
        if entry["file"] is not None:
            raise Error(L_EINVAL)
        path = self.share.contain(entry["path"])
        st = self.share.lstat_path(path)
        is_dir = stat.S_ISDIR(st.st_mode)
        if (flags & O_DIRECTORY) and not is_dir:
            raise Error(L_ENOTDIR)
        if (flags & O_NOFOLLOW) and stat.S_ISLNK(st.st_mode):
            raise Error(L_ELOOP)
        access = self._open_flags(flags, is_dir)
        fd = None
        if not is_dir:
            if flags & O_TRUNC:
                mode = "w+b" if access == 2 else "wb"
            elif access == 0:
                mode = "rb"
            elif access == 1:
                mode = "ab" if (flags & O_APPEND) else "r+b"
            else:
                mode = "r+b"
            try:
                if mode == "rb":
                    raw_flags = os.O_RDONLY
                elif mode in ("wb", "ab"):
                    raw_flags = os.O_WRONLY | os.O_CREAT
                    if mode == "ab":
                        raw_flags |= os.O_APPEND
                else:
                    raw_flags = os.O_RDWR
                raw = open_shared_file(path, raw_flags)
                if flags & O_TRUNC:
                    os.ftruncate(raw, 0)
                fd = raw
            except OSError as exc:
                raise host_error(exc) from None
            entry["file"] = OpenFile(path, fd, bool(flags & O_APPEND))
        w = Writer()
        w.qid(qid_for(st))
        w.u32(self.msize)
        return R_LOPEN, w.bytes()

    def on_lcreate(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        name = r.string().decode("utf-8", "surrogateescape")
        flags = r.u32()
        mode = r.u32()
        _gid = r.u32()
        self.share.check_write()
        entry = self.share.fid_get(fid)
        if entry["file"] is not None:
            raise Error(L_EINVAL)
        path = self.share.resolve(
            os.path.relpath(entry["path"], self.share.root)
            if entry["path"] != self.share.root
            else ".",
            name,
        )
        access = flags & O_ACCMODE
        raw_flags = os.O_WRONLY if access == 1 else os.O_RDWR
        raw_flags |= os.O_CREAT | os.O_TRUNC
        if flags & O_EXCL:
            raw_flags |= os.O_EXCL
        if flags & O_APPEND:
            raw_flags |= os.O_APPEND
        try:
            raw = open_shared_file(path, raw_flags, 0o666 & (mode | 0o600))
            if os.name == "nt":
                os.chmod(path, mode & 0o777)
            else:
                os.fchmod(raw, mode & 0o777)
        except OSError as exc:
            raise host_error(exc) from None
        entry["path"] = path
        entry["file"] = OpenFile(path, raw, bool(flags & O_APPEND))
        st = self.share.stat_path(path)
        w = Writer()
        w.qid(qid_for(st))
        w.u32(self.msize)
        return R_LCREATE, w.bytes()

    def _readdir_data(self, path: str, offset: int, count: int) -> bytes:
        # Stable byte-offset slicing over a name-sorted listing. Entry
        # offsets point past each entry so the client can resume.
        try:
            names = sorted(os.listdir(path))
        except OSError as exc:
            raise host_error(exc) from None
        entries: list[bytes] = []
        for name in names:
            full = os.path.join(path, name)
            try:
                st = self.share.lstat_path(full)
            except Error:
                continue
            item = Writer()
            item.qid(qid_for(st))
            item.u64(0)  # offset placeholder
            item.u8(dirent_type(st))
            item.string(name)
            entries.append(item.bytes())
        blob = bytearray()
        for raw in entries:
            off = len(blob) + len(raw)
            blob += raw[:13] + struct.pack("<Q", off) + raw[21:]
        start = min(offset, len(blob))
        return bytes(blob[start : start + count])

    def chunk(self, count: int) -> int:
        # Keep replies (headers included) within the negotiated msize.
        return max(0, min(count, self.msize - 64))

    def on_read(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        offset = r.u64()
        count = r.u32()
        entry = self.share.fid_get(fid)
        if entry["file"] is None:
            data = self._readdir_data(entry["path"], offset, self.chunk(count))
        else:
            try:
                if os.name == "nt":
                    os.lseek(entry["file"].fileno(), offset, os.SEEK_SET)
                    data = os.read(entry["file"].fileno(), self.chunk(count))
                else:
                    data = os.pread(entry["file"].fileno(), self.chunk(count), offset)
            except OSError as exc:
                raise host_error(exc) from None
        w = Writer()
        w.u32(len(data))
        w.data(data)
        return R_READ, w.bytes()

    def on_readdir(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        offset = r.u64()
        count = r.u32()
        entry = self.share.fid_get(fid)
        if entry["file"] is not None:
            raise Error(L_EINVAL)
        data = self._readdir_data(entry["path"], offset, self.chunk(count))
        w = Writer()
        w.u32(len(data))
        w.data(data)
        return R_READDIR, w.bytes()

    def on_write(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        offset = r.u64()
        count = r.u32()
        data = r.data(count)
        self.share.check_write()
        entry = self.share.fid_get(fid)
        handle = entry["file"]
        if handle is None:
            raise Error(L_EINVAL)
        try:
            if handle.append:
                written = os.write(handle.fileno(), data)
            else:
                if os.name == "nt":
                    os.lseek(handle.fileno(), offset, os.SEEK_SET)
                    written = os.write(handle.fileno(), data)
                else:
                    written = os.pwrite(handle.fileno(), data, offset)
        except OSError as exc:
            raise host_error(exc) from None
        w = Writer()
        w.u32(written)
        return R_WRITE, w.bytes()

    def on_clunk(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        entry = self.share.fid_drop(fid)
        if entry is not None and entry["file"] is not None:
            entry["file"].close()
        return R_CLUNK, b""

    def on_remove(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        self.share.check_write()
        entry = self.share.fid_drop(fid)
        if entry is None:
            raise Error(L_EINVAL)
        if entry["file"] is not None:
            entry["file"].close()
        try:
            if os.path.isdir(entry["path"]) and not os.path.islink(entry["path"]):
                os.rmdir(entry["path"])
            else:
                os.unlink(entry["path"])
        except OSError as exc:
            raise host_error(exc) from None
        return R_REMOVE, b""

    # -- metadata ------------------------------------------------------

    def _getattr_body(self, st: os.stat_result) -> bytes:
        mode = st.st_mode
        w = Writer()
        w.u64(G_ALL)
        w.qid(qid_for(st))
        w.u32(mode)
        w.u32(st.st_uid)
        w.u32(st.st_gid)
        w.u64(st.st_nlink)
        w.u64(getattr(st, "st_rdev", 0))
        w.u64(st.st_size)
        w.u64(int(getattr(st, "st_blksize", 4096)))
        w.u64(int(getattr(st, "st_blocks", (st.st_size + 511) // 512)))
        for stamp in (st.st_atime, st.st_mtime, st.st_ctime):
            w.u64(int(stamp))
            w.u64(int((stamp % 1) * 1_000_000_000))
        birth = getattr(st, "st_birthtime", st.st_ctime)
        w.u64(int(birth))
        w.u64(int((birth % 1) * 1_000_000_000))
        w.u64(0)  # gen
        w.u64(0)  # data_version
        return w.bytes()

    def on_getattr(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        _mask = r.u64()
        entry = self.share.fid_get(fid)
        path = self.share.contain(entry["path"])
        st = self.share.stat_path(path)
        return R_GETATTR, self._getattr_body(st)

    def on_setattr(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        valid = r.u64()
        mode = r.u32()
        _uid = r.u32()
        _gid = r.u32()
        size = r.u64()
        atime_sec = r.u64()
        _atime_nsec = r.u64()
        mtime_sec = r.u64()
        _mtime_nsec = r.u64()
        self.share.check_write()
        entry = self.share.fid_get(fid)
        path = self.share.contain(entry["path"])
        try:
            if valid & S_MODE:
                os.chmod(path, mode & 0o7777, follow_symlinks=False)
            if valid & (S_UID | S_GID):
                _current = os.stat(path, follow_symlinks=False)
                if os.name == "nt":
                    raise Error(L_EPERM)
                os.chown(
                    path,
                    _uid if valid & S_UID else _current.st_uid,
                    _gid if valid & S_GID else _current.st_gid,
                    follow_symlinks=False,
                )
            if valid & S_SIZE:
                raw = open_shared_file(path, os.O_WRONLY)
                try:
                    os.ftruncate(raw, size)
                finally:
                    os.close(raw)
            if valid & (S_ATIME | S_MTIME):
                current_ns = os.stat(path, follow_symlinks=False)
                atime = (
                    atime_sec * 1_000_000_000
                    if valid & S_ATIME
                    else current_ns.st_atime_ns
                )
                mtime = (
                    mtime_sec * 1_000_000_000
                    if valid & S_MTIME
                    else current_ns.st_mtime_ns
                )
                os.utime(path, ns=(atime, mtime), follow_symlinks=False)
        except OSError as exc:
            raise host_error(exc) from None
        return R_SETATTR, b""

    def on_statfs(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        entry = self.share.fid_get(fid)
        try:
            if os.name == "nt":
                space = shutil.disk_usage(entry["path"])
                values = [
                    4096,
                    space.total // 4096,
                    space.free // 4096,
                    space.free // 4096,
                    0,
                    0,
                    0,
                    255,
                ]
            else:
                fs = os.statvfs(entry["path"])
                values = [
                    fs.f_bsize,
                    fs.f_blocks,
                    fs.f_bfree,
                    fs.f_bavail,
                    fs.f_files,
                    fs.f_ffree,
                    int(fs.f_fsid),
                    fs.f_namemax,
                ]
        except OSError as exc:
            raise host_error(exc) from None
        w = Writer()
        w.u32(V9FS_MAGIC)
        w.u32(values[0])
        for value in values[1:7]:
            w.u64(value)
        w.u32(values[7])
        return R_STATFS, w.bytes()

    def on_readlink(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        entry = self.share.fid_get(fid)
        try:
            target = os.readlink(entry["path"])
        except OSError as exc:
            raise host_error(exc) from None
        w = Writer()
        w.string(target)
        return R_READLINK, w.bytes()

    # -- namespace mutations (read-write mode only) --------------------

    def _child(self, entry: FidEntry, name: str) -> str:
        if entry["file"] is not None:
            raise Error(L_EINVAL)
        if "/" in name or "\0" in name or name in ("", ".", ".."):
            raise Error(L_EINVAL)
        base = entry["path"]
        try:
            if not stat.S_ISDIR(os.stat(base).st_mode):
                raise Error(L_ENOTDIR)
        except OSError as exc:
            raise host_error(exc) from None
        rel = os.path.relpath(base, self.share.root)
        parts = [] if rel == "." else [rel]
        return self.share.resolve(*(parts + [name]))

    def on_symlink(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        name = r.string().decode("utf-8", "surrogateescape")
        target = r.string().decode("utf-8", "surrogateescape")
        _gid = r.u32()
        self.share.check_write()
        entry = self.share.fid_get(fid)
        path = self._child(entry, name)
        try:
            os.symlink(target, path)
            st = self.share.lstat_path(path)
        except OSError as exc:
            raise host_error(exc) from None
        w = Writer()
        w.qid(qid_for(st))
        return R_SYMLINK, w.bytes()

    def on_mknod(self, r: Reader) -> tuple[int, bytes]:
        raise Error(L_EPERM)

    def on_rename(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        newdirfid = r.u32()
        newname = r.string().decode("utf-8", "surrogateescape")
        self.share.check_write()
        entry = self.share.fid_get(fid)
        newdir = self.share.fid_get(newdirfid)
        dest = self._child(newdir, newname)
        if entry["file"] is not None:
            entry["file"].close()
            entry["file"] = None
        try:
            os.rename(entry["path"], dest)
        except OSError as exc:
            raise host_error(exc) from None
        entry["path"] = dest
        return R_RENAME, b""

    def on_link(self, r: Reader) -> tuple[int, bytes]:
        dirfid = r.u32()
        fid = r.u32()
        name = r.string().decode("utf-8", "surrogateescape")
        self.share.check_write()
        target = self.share.fid_get(fid)
        newdir = self.share.fid_get(dirfid)
        dest = self._child(newdir, name)
        try:
            os.link(self.share.contain(target["path"]), dest)
        except OSError as exc:
            raise host_error(exc) from None
        return R_LINK, b""

    def on_mkdir(self, r: Reader) -> tuple[int, bytes]:
        dfid = r.u32()
        name = r.string().decode("utf-8", "surrogateescape")
        mode = r.u32()
        _gid = r.u32()
        self.share.check_write()
        entry = self.share.fid_get(dfid)
        path = self._child(entry, name)
        try:
            os.mkdir(path, mode & 0o777)
            st = self.share.stat_path(path)
        except OSError as exc:
            raise host_error(exc) from None
        w = Writer()
        w.qid(qid_for(st))
        return R_MKDIR, w.bytes()

    def on_renameat(self, r: Reader) -> tuple[int, bytes]:
        olddirfid = r.u32()
        oldname = r.string().decode("utf-8", "surrogateescape")
        newdirfid = r.u32()
        newname = r.string().decode("utf-8", "surrogateescape")
        self.share.check_write()
        olddir = self.share.fid_get(olddirfid)
        newdir = self.share.fid_get(newdirfid)
        src = self._child(olddir, oldname)
        dest = self._child(newdir, newname)
        try:
            os.rename(src, dest)
        except OSError as exc:
            raise host_error(exc) from None
        return R_RENAMEAT, b""

    def on_unlinkat(self, r: Reader) -> tuple[int, bytes]:
        dirfid = r.u32()
        name = r.string().decode("utf-8", "surrogateescape")
        flags = r.u32()
        self.share.check_write()
        entry = self.share.fid_get(dirfid)
        path = self._child(entry, name)
        try:
            if flags & 0x200:  # AT_REMOVEDIR
                os.rmdir(path)
            else:
                os.unlink(path)
        except OSError as exc:
            raise host_error(exc) from None
        return R_UNLINKAT, b""

    # -- misc ----------------------------------------------------------

    def on_xattrwalk(self, r: Reader) -> tuple[int, bytes]:
        raise Error(L_ENODATA)

    def on_xattrcreate(self, r: Reader) -> tuple[int, bytes]:
        raise Error(L_ENODATA)

    def on_fsync(self, r: Reader) -> tuple[int, bytes]:
        fid = r.u32()
        entry = self.share.fid_get(fid)
        if entry["file"] is not None:
            try:
                os.fsync(entry["file"].fileno())
            except OSError as exc:
                raise host_error(exc) from None
        return R_FSYNC, b""

    def on_lock(self, r: Reader) -> tuple[int, bytes]:
        raise Error(L_ENOSYS)

    def on_getlock(self, r: Reader) -> tuple[int, bytes]:
        raise Error(L_ENOSYS)


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        conn = Connection(cast(Server, self.server).share)
        while True:
            header = self.rfile.read(4)
            if len(header) == 0:
                return
            if len(header) < 4:
                return
            (size,) = struct.unpack("<I", header)
            if size < 7 or size > 64 * 1024 * 1024:
                return
            body = self.rfile.read(size - 4)
            if len(body) < size - 4:
                return
            msgtype, tag = struct.unpack_from("<BH", body)
            try:
                response = conn.handle(msgtype, tag, body[3:])
            except Exception:  # last-resort guard; handle() already converts
                response = conn.rlerror(tag, Error(L_EIO))
            try:
                self.wfile.write(response)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, share: Share, address: tuple[str, int]) -> None:
        self.share = share
        super().__init__(address, Handler)


def serve(
    root: str, bind: str = "127.0.0.1", port: int = 5564, read_write: bool = False
) -> int:
    share = Share(root, read_write=read_write)
    server = Server(share, (bind, port))
    actual = server.server_address[1]
    mode = "rw" if read_write else "ro"
    print(f"NVX-9P-SERVE-OK: {bind}:{actual} root={share.root} mode={mode}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Serve a host directory to NVX guests over 9P2000.L."
    )
    parser.add_argument("--root", required=True, help="host directory tree to share")
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="local address to listen on (default: loopback only)",
    )
    parser.add_argument(
        "--port", type=int, default=5564, help="TCP port to listen on (default: 5564)"
    )
    parser.add_argument(
        "--read-write",
        action="store_true",
        help="allow the guest to modify the tree (default: read-only)",
    )
    args = parser.parse_args(argv)
    try:
        return serve(args.root, args.bind, args.port, args.read_write)
    except Error as exc:
        print(f"nvx-9p: {exc} ({exc.linux_errno})", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"nvx-9p: cannot serve: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
