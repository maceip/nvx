import unittest

from nvx_tools.explain import explain


class ExplainTests(unittest.TestCase):
    def test_denial_table(self) -> None:
        cases = (
            (
                {
                    "kind": "network.denied",
                    "fields": {"dst": "203.0.113.9", "port": 443},
                },
                "blocked: egress to 203.0.113.9:443 is outside the allowlist; use --profile ci --egress-allow 203.0.113.9:443",
            ),
            (
                {"kind": "resource.denied", "fields": {"resource": "pids"}},
                "blocked: pids limit reached; increase --pids-max",
            ),
            (
                {"kind": "filesystem.denied", "fields": {"path": "/host"}},
                "blocked: /host is outside the declared workspace",
            ),
            ({"kind": "future.event"}, "no explanation available for this event"),
        )
        for event, expected in cases:
            with self.subTest(event=event):
                self.assertEqual(explain(event), expected)
