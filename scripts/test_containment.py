import copy
import json
import unittest
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from nvx_tools.common import ScriptError
from nvx_tools.containment import PROBES, assert_pair, render, validate_document

GOLDEN = Path(__file__).parent / "testdata/containment-probes-v1.json"


class ContainmentTests(unittest.TestCase):
    def test_committed_probe_table_and_missing_negative_control_rejected(self) -> None:
        self.assertEqual(
            json.loads(GOLDEN.read_bytes()), [asdict(probe) for probe in PROBES]
        )
        for probe in PROBES:
            assert_pair(probe.family, True, False)
            for protected, control in ((False, False), (True, True), (False, True)):
                with self.assertRaisesRegex(ScriptError, "negative control"):
                    assert_pair(probe.family, protected, control)

    def test_complete_document_and_render_scope(self) -> None:
        document: dict[str, Any] = {
            "containment_version": 1,
            "backend": "hvf",
            "results": [
                {
                    **asdict(probe),
                    "verdict": "CONTAINED",
                    "control_verdict": "UNCONTAINED",
                    "evidence": {"probe": "observed"},
                }
                for probe in PROBES
            ],
        }
        validate_document(document)
        table = render(document)
        self.assertIn("legacy structural-only", json.dumps(document))
        self.assertEqual(table.count("| hvf |"), 8)
        bad = copy.deepcopy(document)
        cast(list[dict[str, Any]], bad["results"])[0].pop("control_verdict")
        with self.assertRaisesRegex(ScriptError, "negative control"):
            validate_document(bad)
        bad = copy.deepcopy(document)
        cast(list[dict[str, Any]], bad["results"]).pop()
        with self.assertRaisesRegex(ScriptError, "every probe"):
            validate_document(bad)
