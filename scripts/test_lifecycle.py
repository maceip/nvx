import os
import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.sandbox_lifecycle import deprovision


class LifecycleTests(unittest.TestCase):
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
