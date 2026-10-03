"""Single-threaded, non-root Python fork server; private FDs never reach jobs."""

import builtins
import ctypes
import hashlib
import http.client
import json
import os
import struct
import sys
import traceback

STATUS = 198
builtins.__nvx_warm_start__ = os.urandom(16).hex()
# Deny another process with the workload UID access to the parent's /proc FDs.
if ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) != 0:  # PR_SET_DUMPABLE
    raise RuntimeError('cannot protect warm parent descriptors')


def read_exact(size):
    data = bytearray()
    while len(data) < size:
        part = os.read(0, size - len(data))
        if not part:
            raise EOFError
        data.extend(part)
    return data


if len(os.listdir('/proc/self/task')) != 1:
    raise RuntimeError('warm runtime must be single threaded')
os.write(STATUS, b'R')
while True:
    try:
        length = struct.unpack('<I', read_exact(4))[0]
        if not 8 <= length <= 65536:
            raise ValueError('invalid warm command length')
        payload = read_exact(length)
        _, argc, reserved = struct.unpack_from('<IHH', payload)
        if reserved or not 1 <= argc <= 64:
            raise ValueError('invalid warm command header')
        argv = []
        offset = 8
        for _ in range(argc):
            size = struct.unpack_from('<I', payload, offset)[0]
            offset += 4
            if not 0 < size <= 4096 or offset + size > length:
                raise ValueError('invalid warm argument')
            argv.append(bytes(payload[offset:offset + size]).decode())
            offset += size
        if offset != length or not argv[0].startswith('/'):
            raise ValueError('invalid warm command')
    except EOFError:
        break
    child = os.fork()
    if child == 0:
        # Remove both trusted channels before executing any untrusted code.
        os.close(STATUS)
        os.close(0)
        fd = os.open('/dev/null', os.O_RDONLY)
        if fd != 0:
            os.dup2(fd, 0)
            os.close(fd)
        code = 125
        try:
            wrapper = ['/bin/sh', '-c', 'cd "$1"; shift; exec env "$@"', 'nvx']
            if argv[:4] == wrapper:
                os.chdir(argv[4])
                argv = argv[5:]
                while argv and '=' in argv[0]:
                    name, value = argv.pop(0).split('=', 1)
                    os.environ[name] = value
            if not argv:
                raise ValueError('empty workload')
            if os.path.basename(argv[0]) in ('python', 'python3', 'python3.12') and argv[1:2] == ['-c']:
                sys.argv = ['-c', *argv[3:]]
                exec(compile(argv[2], '<string>', 'exec'), {'__name__': '__main__'})
                code = 0
            else:
                os.execvpe(argv[0], argv, os.environ)
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else (0 if error.code is None else 1)
        except BaseException:
            traceback.print_exc()
            code = 1
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code & 255)
    _, status = os.waitpid(child, 0)
    result = os.waitstatus_to_exitcode(status)
    if result < 0:
        result = 128 - result
    os.write(STATUS, b'E' + struct.pack('<I', result))
