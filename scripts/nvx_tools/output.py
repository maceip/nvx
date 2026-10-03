"""Byte-preserving CLI streaming for managed output frames."""

import sys


def write_stream(stream: str, data: bytes) -> None:
    target = sys.stdout.buffer if stream == "stdout" else sys.stderr.buffer
    target.write(data)
    target.flush()
