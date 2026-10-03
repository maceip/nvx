import json
import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.policy import read_config, resolve


class PolicyTests(unittest.TestCase):
    def test_precedence_and_deterministic_render(self) -> None:
        self.assertEqual(resolve().pids_max, 128)
        config = {"profile": "ci", "pids_max": 32, "allow": ["192.0.2.1:443"]}
        policy = resolve({"pids_max": 64}, config)
        self.assertEqual(policy.pids_max, 64)
        self.assertEqual(policy.profile, "ci")
        self.assertEqual(
            json.dumps(policy.document(), sort_keys=True),
            json.dumps(resolve({"pids_max": 64}, config).document(), sort_keys=True),
        )

    def test_contradiction_and_risky_negative_control(self) -> None:
        with self.assertRaisesRegex(ScriptError, "profile ci"):
            resolve({"allow": ["192.0.2.1:443"]})
        with self.assertRaisesRegex(ScriptError, "require.*risky"):
            resolve({"uid": 0})
        with self.assertRaisesRegex(ScriptError, "unknown"):
            resolve({"unknown": True})
        risky = resolve({"profile": "risky"})
        self.assertEqual(
            (risky.seccomp, risky.uid, risky.pids_max, risky.egress),
            ("unconfined", 0, None, "allow"),
        )
        self.assertFalse(risky.device_filter)
        self.assertFalse(risky.masked_paths)
        self.assertFalse(risky.no_new_privs)
        self.assertEqual(resolve({"seccomp": "unconfined"}).seccomp, "unconfined")

    def test_device_probe_retains_only_mknod_in_ci(self) -> None:
        policy = resolve({"profile": "ci", "caps": ["MKNOD"]})
        self.assertTrue(policy.device_filter)
        self.assertTrue(policy.no_new_privs)
        self.assertEqual(policy.caps, ("MKNOD",))
        with self.assertRaises(ScriptError):
            resolve({"caps": ["MKNOD"]})
        with self.assertRaises(ScriptError):
            resolve({"profile": "ci", "caps": ["SYS_ADMIN"]})

    def test_toml_parse_errors_identify_source_and_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nvx.toml"
            path.write_text("[policy]\npids_max = 17\n")
            self.assertEqual(resolve(config=read_config(path)).pids_max, 17)
            path.write_text("[policy]\npids_max = ]\n")
            with self.assertRaisesRegex(ScriptError, r"line 2"):
                read_config(path)

    def test_seccomp_header_matches_reviewed_allowlist(self) -> None:
        root = Path(__file__).resolve().parents[1] / "guest/common"
        doc = json.loads((root / "seccomp-nvx-default.json").read_text())
        header = "".join(
            f"#ifdef __NR_{name}\nNVX_ALLOW(__NR_{name})\n#endif\n"
            for name in doc["syscalls"]
        )
        self.assertTrue((root / "seccomp-nvx-default.h").read_text().endswith(header))
        self.assertNotIn("getpriority", doc["syscalls"])
        self.assertNotIn("mount", doc["syscalls"])
        self.assertNotIn("bpf", doc["syscalls"])
