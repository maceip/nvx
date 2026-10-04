import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import nvx
from nvx_tools.build import assert_required_kernel_config
from nvx_tools.common import ScriptError
from nvx_tools.policy import read_config, resolve
from nvx_tools.policy_tests import execute


class PolicyTests(unittest.TestCase):
    def test_raw_x86_sandboxes_have_a_controlled_default_network(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layer = root / "layer.erofs"
            scratch = root / "scratch.ext4"
            layer.write_bytes(b"layer")
            scratch.write_bytes(b"scratch")

            def available(path: Path, _description: str) -> Path:
                return path

            for backend in ("hvf", "kvm", "mshv", "whp"):
                with (
                    self.subTest(backend=backend),
                    patch("nvx.platform.machine", return_value="x86_64"),
                    patch(
                        "nvx.sys.platform", "darwin" if backend == "hvf" else "linux"
                    ),
                    patch("nvx.require_file", side_effect=available),
                ):
                    arguments = [
                        "sandbox",
                        "run",
                        "--hypervisor",
                        backend,
                        "--layer",
                        f"custom,{layer},900ba5a7-a33d-577b-a21c-d4ee7930be62",
                        "--scratch",
                        str(scratch),
                        "--entrypoint",
                        "/bin/sleep",
                        "--arg",
                        "2",
                        "--dry-run",
                    ]
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(nvx.main(arguments), 0)
                    self.assertIn(
                        "--net 192.168.127.2/24 --network-profile portable",
                        output.getvalue(),
                    )
                    self.assertIn("--network-egress deny", output.getvalue())
                    with patch("nvx.sandbox_lifecycle.provision") as provision:
                        self.assertEqual(
                            nvx.main(
                                [
                                    *arguments[:1],
                                    "provision",
                                    *arguments[2:-1],
                                    "--state-dir",
                                    str(root / "state"),
                                ]
                            ),
                            0,
                        )
                    self.assertEqual(
                        provision.call_args.kwargs["net"], "192.168.127.2/24"
                    )
                    self.assertEqual(
                        provision.call_args.kwargs["network_profile"], "portable"
                    )
                    self.assertEqual(
                        provision.call_args.kwargs["network_egress"], "deny"
                    )

    def test_host_probe_timeout_preserves_partial_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch(
                "nvx_tools.policy_tests.subprocess.run",
                side_effect=subprocess.TimeoutExpired(
                    ["probe"],
                    15,
                    output=b"partial stdout",
                    stderr=b"converting image\nBearer private-token-value",
                ),
            ):
                with self.assertRaises(ScriptError) as caught:
                    execute("hvf", "probe", output, "control", 5)
            self.assertIn("host probe timed out after 15s", str(caught.exception))
            self.assertIn("converting image", str(caught.exception))
            self.assertNotIn("private-token-value", str(caught.exception))
            self.assertEqual(
                (output / "control.stdout").read_bytes(), b"partial stdout"
            )
            self.assertEqual(
                (output / "control.stderr").read_bytes(),
                b"converting image\nBearer private-token-value",
            )

    def test_guest_kernels_support_unconfined_namespace_and_socket_controls(
        self,
    ) -> None:
        root = Path(__file__).resolve().parents[1] / "kernel"
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "config"
            for architecture, name in (
                ("x86_64", "config-microvm"),
                ("aarch64", "config-microvm-aarch64"),
            ):
                source = root / name
                with patch(
                    "nvx_tools.build.host_guest_arch", return_value=architecture
                ):
                    assert_required_kernel_config(source)
                    for feature in ("CONFIG_USER_NS", "CONFIG_PACKET"):
                        with self.subTest(architecture=name, feature=feature):
                            generated.write_text(
                                source.read_text().replace(
                                    f"{feature}=y", f"# {feature} is not set"
                                )
                            )
                            with self.assertRaisesRegex(ScriptError, feature):
                                assert_required_kernel_config(generated)

    def test_probe_failure_keeps_the_startup_cause_and_redacts_credentials(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "nvx_tools.policy_tests.subprocess.run",
                return_value=subprocess.CompletedProcess(
                    [],
                    1,
                    b"",
                    b"fatal: root identity rejected\nBearer private-token-value",
                ),
            ):
                with self.assertRaises(ScriptError) as caught:
                    execute("whp", "probe", Path(directory), "control", 5)
                self.assertIn("root identity rejected", str(caught.exception))
                self.assertNotIn("private-token-value", str(caught.exception))

    def test_wall_deadline_can_expire_before_output_but_requires_timeout_status(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch(
                "nvx_tools.policy_tests.subprocess.run",
                return_value=subprocess.CompletedProcess([], 124, b"", b""),
            ):
                self.assertEqual(
                    execute("kvm", "probe", output, "deadline", 5, expected_status=124),
                    {"phase": "timed-out-before-output"},
                )
            with patch(
                "nvx_tools.policy_tests.subprocess.run",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ):
                with self.assertRaisesRegex(
                    ScriptError, "did not return a probe record"
                ):
                    execute("kvm", "probe", output, "empty", 5)
                with self.assertRaisesRegex(ScriptError, "failed with status 0"):
                    execute("kvm", "probe", output, "control", 5, expected_status=124)

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
