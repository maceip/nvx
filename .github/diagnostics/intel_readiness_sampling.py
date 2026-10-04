"""Passively sample an owned exact-build VMM after its last MMIO trace marker.

Unlike the management-inspection replay, this hook preserves every guest
argument and the original EOF-sealed capability pipe.
"""

import json
import subprocess
import threading
import time
from pathlib import Path

_OriginalPopen = subprocess.Popen
_MARKER = b"physical_address=0xd0001100"
_RECORD = Path(__file__).parent / "sampling.jsonl"


def _record(event, **fields):
    try:
        with _RECORD.open("a") as output:
            output.write(json.dumps({"event": event, **fields}) + "\n")
    except OSError:
        pass


def _sample_after_marker(process, log_path):
    started = time.monotonic()
    try:
        while process.poll() is None and time.monotonic() - started < 15:
            if _MARKER in log_path.read_bytes():
                report = log_path.parent / "native-thread-sample.txt"
                command = ["/usr/bin/sample", str(process.pid), "3",
                           "-mayDie", "-file", str(report)]
                _record("sampling_started", pid=process.pid, marker_seen=True,
                        command=command, elapsed_seconds=time.monotonic() - started)
                with (log_path.parent / "native-thread-sample-tool.log").open("wb") as output:
                    sampler = _OriginalPopen(command, stdin=subprocess.DEVNULL,
                                             stdout=output, stderr=subprocess.STDOUT)
                    try:
                        status = sampler.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        sampler.kill()
                        status = sampler.wait(timeout=5)
                _record("sampling_finished", pid=process.pid, returncode=status,
                        report_exists=report.is_file())
                return
            threading.Event().wait(0.05)
        _record("sampling_not_started", pid=process.pid, marker_seen=False)
    except Exception as error:
        _record("sampling_error", pid=process.pid, error_type=type(error).__name__)


class SamplingPopen(_OriginalPopen):
    def __init__(self, args, *positional, **kwargs):
        # Delegate unchanged arguments and descriptors before inspecting anything.
        super().__init__(args, *positional, **kwargs)
        try:
            if not (isinstance(args, (list, tuple)) and args
                    and Path(args[0]).name == "openvmm"
                    and "--microvm-control-auth-stdin" in args):
                return
            assert "--microvm-control-repl" not in args
            log_path = Path(kwargs["stdout"].name)
            _record("sampling_launch", pid=self.pid, log_path=str(log_path),
                    guest_arguments_unchanged=True, stdin_unchanged=True)
            threading.Thread(target=_sample_after_marker,
                             args=(self, log_path), daemon=True).start()
        except Exception as error:
            _record("sampling_setup_error", pid=self.pid, error_type=type(error).__name__)


subprocess.Popen = SamplingPopen
