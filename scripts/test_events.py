import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.events import MAX_FIELD_BYTES, EventLog, read_events, redact


class EventTests(unittest.TestCase):
    def test_ordering_and_concurrent_writes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            threads = [
                threading.Thread(target=EventLog(path, "test").emit, args=("test",))
                for _ in range(30)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            records = read_events(path)
            self.assertEqual([row["sequence"] for row in records], list(range(1, 31)))
            mode = path.stat().st_mode
            self.assertTrue(stat.S_ISREG(mode))
            if os.name == "nt":
                # Windows reports the read-only attribute as synthetic Unix bits.
                self.assertEqual(mode & (stat.S_IREAD | stat.S_IWRITE), 0o600)
            else:
                self.assertEqual(mode & 0o777, 0o600)

    def test_redacts_nested_secrets_and_token_shapes(self) -> None:
        tokens = (
            "sk-test0123456789",
            "Bearer some-token",
            "ghp_abcdefghi",
            "private-value",
        )
        value = {"Authorization": "sensitive", "nested": list(tokens)}
        encoded = json.dumps(redact(value, secrets=("private-value",)))
        for token in (*tokens, "sensitive"):
            self.assertNotIn(token, encoded)
        self.assertIn("[redacted]", encoded)
        self.assertEqual(redact("ordinary diagnostic"), "ordinary diagnostic")

    def test_truncates_oversize_fields(self) -> None:
        self.assertEqual(
            redact("x" * (MAX_FIELD_BYTES + 1)), "x" * MAX_FIELD_BYTES + "[truncated]"
        )

    def test_rejects_incomplete_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text('{"event_version":1}')
            with self.assertRaisesRegex(ScriptError, "incomplete"):
                read_events(path)
