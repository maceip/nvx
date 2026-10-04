import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.sandbox_lifecycle import (
    deprovision,
    microvm_network_endpoint,
    sealed_capability_pipe,
    startup_failure,
)


class LifecycleTests(unittest.TestCase):
    def test_capability_is_complete_and_closed_before_child_starts(self) -> None:
        capability = bytes(range(32))
        with sealed_capability_pipe(capability) as reader:
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import os; data=os.read(0,32); "
                    "assert data==bytes(range(32)); assert os.read(0,1)==b''",
                ],
                stdin=reader,
                capture_output=True,
                timeout=5,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        for invalid in (bytes(32), b"short", bytes(33)):
            with self.assertRaises(ScriptError), sealed_capability_pipe(invalid):
                self.fail("invalid capability admitted")

    def test_microvm_network_uses_guest_address_and_rejects_reserved_hosts(
        self,
    ) -> None:
        self.assertEqual(
            microvm_network_endpoint("192.168.127.0/24"), "192.168.127.2/24"
        )
        self.assertEqual(microvm_network_endpoint("10.23.4.7/24"), "10.23.4.7/24")
        for value in (
            "192.168.127.1/24",
            "192.168.127.255/24",
            "192.168.127.0/31",
            "::/64",
        ):
            with self.assertRaises(ScriptError):
                microvm_network_endpoint(value)

    def test_startup_failure_preserves_cause_with_bounded_redacted_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "openvmm.log"
            log.write_text(
                "old output\n" * 10000
                + "fatal: missing PCI interrupt; Bearer fixture-sensitive-token\n"
            )
            failure = str(startup_failure(log, 1))
            self.assertIn("status 1", failure)
            self.assertIn("missing PCI interrupt", failure)
            self.assertNotIn("fixture-sensitive-token", failure)
            self.assertLess(len(failure), 4500)

    @unittest.skipIf(
        os.name == "nt", "creating Windows symlinks requires runner privileges"
    )
    def test_final_symlink_cannot_deprovision_another_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            (state / "config.json").write_bytes(b"fixture")
            alias = Path(directory) / "alias"
            alias.symlink_to(state, target_is_directory=True)
            with self.assertRaisesRegex(ScriptError, "not a plain directory"):
                deprovision(alias)
            self.assertEqual((state / "config.json").read_bytes(), b"fixture")

    def test_deprovision_owns_metrics_and_lock_but_preserves_unknown_files(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir(mode=0o700)
            for name in (
                "config.json",
                "resources.json",
                "events.jsonl",
                "events.lock",
            ):
                (state / name).write_bytes(b"fixture")
            unknown = state / "user-notes.txt"
            unknown.write_bytes(b"user owned")
            with self.assertRaisesRegex(ScriptError, "not owned"):
                deprovision(state)
            self.assertEqual(unknown.read_bytes(), b"user owned")
            self.assertTrue((state / "resources.json").exists())
            unknown.unlink()
            deprovision(state)
            self.assertFalse(state.exists())
