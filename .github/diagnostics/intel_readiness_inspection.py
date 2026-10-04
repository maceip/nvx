"""Enable bounded, read-only REPL inspection in an exact-build guest replay.

The workflow copies this file to an ignored sitecustomize.py directory. Only
the owned OpenVMM launch with a prepared authentication pipe is intercepted.
"""

import hashlib
import json
import os
import subprocess
import threading
import time
from pathlib import Path

_OriginalPopen = subprocess.Popen
_MARKER = b"physical_address=0xd0001100"
_COMMANDS = (
    b"inspect -r -l 2\n"
    b"inspect -r -l 3 vm\n"
    b"inspect -r -l 8 vm/partition\n"
    b"inspect -r -l 8 vm/virtiofs-3489665024\n"
)
_RECORD = Path(__file__).parent / "inspection.jsonl"


def _record(event, **fields):
    data = json.dumps({"event": event, **fields}) + "\n"
    with _RECORD.open("a") as output:
        output.write(data)


def _inspect_after_marker(process, writer, log_path):
    started = time.monotonic()
    marker_seen = False
    try:
        while process.poll() is None and time.monotonic() - started < 15:
            if _MARKER in log_path.read_bytes():
                marker_seen = True
                written = os.write(writer, _COMMANDS)
                assert written == len(_COMMANDS)
                _record("inspection_sent", pid=process.pid,
                        marker_seen=True, elapsed_seconds=time.monotonic() - started,
                        commands=_COMMANDS.decode().splitlines())
                break
            threading.Event().wait(0.05)
        if not marker_seen:
            _record("inspection_not_sent", pid=process.pid, marker_seen=False)
        # Keep stdin open until the original readiness timeout stops this owned
        # process. EOF must not become a new guest termination condition.
        while process.poll() is None and time.monotonic() - started < 90:
            threading.Event().wait(0.1)
    except Exception as error:
        _record("inspection_error", pid=process.pid, error_type=type(error).__name__)
    finally:
        os.close(writer)
        _record("inspection_finished", pid=process.pid, returncode=process.poll())


class InspectionPopen(_OriginalPopen):
    def __init__(self, args, *positional, **kwargs):
        if not (isinstance(args, (list, tuple)) and args
                and Path(args[0]).name == "openvmm"
                and "--microvm-control-auth-stdin" in args):
            super().__init__(args, *positional, **kwargs)
            return
        assert "--microvm-control-repl" not in args
        assert not positional
        original_stdin = kwargs["stdin"]
        original_fd = (original_stdin if isinstance(original_stdin, int)
                       else original_stdin.fileno())
        capability = os.read(original_fd, 32)
        assert len(capability) == 32 and capability != bytes(32)
        assert os.read(original_fd, 1) == b""
        reader, writer = os.pipe()
        try:
            assert os.write(writer, capability) == 32
            command = [*args, "--microvm-control-repl"]
            options = {**kwargs, "stdin": reader}
            super().__init__(command, **options)
        except BaseException:
            os.close(writer)
            raise
        finally:
            os.close(reader)
        log_path = Path(kwargs["stdout"].name)
        _record("inspection_launch", pid=self.pid, log_path=str(log_path),
                added_option="--microvm-control-repl", capability_bytes=32,
                original_command_sha256=hashlib.sha256(
                    json.dumps(args).encode()).hexdigest())
        threading.Thread(target=_inspect_after_marker,
                         args=(self, writer, log_path), daemon=True).start()


subprocess.Popen = InspectionPopen
