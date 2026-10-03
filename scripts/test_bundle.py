import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from nvx_tools.bundle import FILES, create, verify
from nvx_tools.common import ScriptError, sha256_file
from nvx_tools.receipt import seal


class BundleTests(unittest.TestCase):
    def test_deterministic_archive_integrity_and_excluded_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            for name in FILES:
                if name != "receipt.json":
                    (state / name).write_bytes(b"fixture-" + name.encode())
            (state / "control.capability").write_bytes(b"never-in-bundle")
            golden = cast(
                dict[str, Any],
                json.loads(
                    (Path(__file__).parent / "testdata/receipt-v1.json").read_bytes()
                ),
            )
            body = golden["body"]
            body["evidence"] = {
                name: sha256_file(state / name)
                for name in FILES
                if name != "receipt.json"
            }
            (state / "receipt.json").write_text(json.dumps(seal(body)))
            first, second = root / "first.tar.gz", root / "second.tar.gz"
            create(state, first)
            create(state, second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertTrue(verify(first)["verified"])
            with self.assertRaisesRegex(ScriptError, "already exists"):
                create(state, first)
            damaged = bytearray(first.read_bytes())
            damaged[len(damaged) // 2] ^= 1
            (root / "damaged").write_bytes(damaged)
            with self.assertRaises(ScriptError):
                verify(root / "damaged")
            (state / "runtime.json").write_text("{}")
            with self.assertRaisesRegex(ScriptError, "stop"):
                create(state, root / "active.tar.gz")
