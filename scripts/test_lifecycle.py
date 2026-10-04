import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock, patch

from nvx_tools import control_session
from nvx_tools.common import ScriptError
from nvx_tools.sandbox import SandboxLaunch, SandboxLayer
from nvx_tools.sandbox_lifecycle import (
    cleanup_endpoint,
    connect_when_ready,
    deprovision,
    microvm_network_endpoint,
    provision,
    sealed_capability_pipe,
    start,
    startup_failure,
)


class LifecycleTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Unix snapshot namespace regression")
    def test_capture_socket_shares_the_snapshot_parent_for_short_and_long_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="nvx-capture-", dir="/tmp"
        ) as directory:
            root = Path(directory)
            layer = root / "layer"
            scratch = root / "scratch"
            layer.write_bytes(b"layer")
            scratch.write_bytes(b"scratch")
            launch = SandboxLaunch(
                (
                    SandboxLayer(
                        "custom", layer, "900ba5a7-a33d-577b-a21c-d4ee7930be62"
                    ),
                ),
                scratch,
            )

            def artifact_file(path: Path, _label: str) -> Path:
                return path

            for long_path in (False, True):
                with self.subTest(long_path=long_path):
                    parent = root / ("nested-" + "x" * 100 if long_path else "short")
                    state = parent / "source"
                    provision(
                        state,
                        launch,
                        hypervisor="kvm",
                        memory_mib=512,
                        net=None,
                        network_profile=None,
                        network_egress="deny",
                        network_ingress="deny",
                        network_egress_allow=(),
                        network_egress_deny=(),
                        host_loopback=None,
                        network_proxy=None,
                        host_loopback_forward=(),
                        cmdline="",
                    )
                    process = MagicMock(spec=subprocess.Popen)
                    process.pid = 201
                    process.poll.return_value = None
                    with (
                        patch(
                            "nvx_tools.sandbox_lifecycle.platform.machine",
                            return_value="x86_64",
                        ),
                        patch(
                            "nvx_tools.sandbox_lifecycle.require_file",
                            side_effect=artifact_file,
                        ),
                        patch(
                            "nvx_tools.sandbox_lifecycle.subprocess.Popen",
                            return_value=process,
                        ) as spawn,
                        patch("nvx_tools.sandbox_lifecycle.connect_when_ready"),
                    ):
                        start(state, 5, snapshot_destination=parent / "snapshot")
                    args = spawn.call_args.args[0]
                    endpoint = Path(
                        args[args.index("--microvm-control-console") + 1].removeprefix(
                            "listen="
                        )
                    )
                    captured = Path(args[args.index("--snapshot-destination") + 1])
                    self.assertEqual(
                        endpoint.parent.resolve(), captured.parent.resolve()
                    )
                    self.assertLess(len(os.fsencode(endpoint)), 100)
                    runtime = json.loads((state / "runtime.json").read_text())
                    self.assertEqual(Path(runtime["snapshot_destination"]), captured)
                    if long_path:
                        self.assertNotEqual(captured, parent / "snapshot")
                        (captured.parent / "partial-capture").write_bytes(b"fixture")
                    cleanup_endpoint(runtime)
                    if long_path:
                        self.assertFalse(captured.parent.exists())

    def test_direct_policy_provisioning_supplies_a_matching_x86_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layer = root / "layer"
            scratch = root / "scratch"
            layer.write_bytes(b"layer")
            scratch.write_bytes(b"scratch")
            launch = SandboxLaunch(
                (
                    SandboxLayer(
                        "custom", layer, "900ba5a7-a33d-577b-a21c-d4ee7930be62"
                    ),
                ),
                scratch,
            )

            def artifact_file(path: Path, _label: str) -> Path:
                return path

            with patch(
                "nvx_tools.sandbox_lifecycle.platform.machine", return_value="x86_64"
            ):
                for backend in ("hvf", "kvm", "mshv", "whp"):
                    state = root / backend
                    provision(
                        state,
                        launch,
                        hypervisor=backend,
                        memory_mib=512,
                        net=None,
                        network_profile=None,
                        network_egress="deny",
                        network_ingress="deny",
                        network_egress_allow=(),
                        network_egress_deny=(),
                        host_loopback=None,
                        network_proxy=None,
                        host_loopback_forward=(),
                        cmdline="",
                    )
                    config = json.loads((state / "config.json").read_text())
                    self.assertEqual(config["net"], "192.168.127.2/24")
                    self.assertEqual(config["network_profile"], "portable")
                    self.assertEqual(config["network_egress"], "deny")
                    process = MagicMock(spec=subprocess.Popen)
                    process.pid = 201
                    process.poll.return_value = None
                    with (
                        patch(
                            "nvx_tools.sandbox_lifecycle.require_file",
                            side_effect=artifact_file,
                        ),
                        patch(
                            "nvx_tools.sandbox_lifecycle.subprocess.Popen",
                            return_value=process,
                        ) as spawn,
                        patch("nvx_tools.sandbox_lifecycle.connect_when_ready"),
                    ):
                        start(state, 5, restore=Path("/snapshot"))
                    args = spawn.call_args.args[0]
                    for flag in (
                        "--microvm-lifecycle",
                        "--microvm-workload-identity",
                        "--memory",
                        "--net",
                    ):
                        self.assertNotIn(flag, args)
                    self.assertEqual(
                        args[args.index("--network-profile") + 1], "portable"
                    )
                    self.assertIn("--network-egress", args)
                    self.assertIn("--restore-snapshot", args)
                    self.assertIn("--restore-entropy", args)

    @unittest.skipIf(os.name == "nt", "Unix control transport regression")
    def test_cold_start_retains_waiting_attachment_until_guest_is_ready(self) -> None:
        capability = bytes(range(32))
        failures: list[BaseException] = []
        with tempfile.TemporaryDirectory(prefix="nvx-ready-", dir="/tmp") as directory:
            root = Path(directory)
            endpoint = root / "control.sock"
            log = root / "openvmm.log"
            log.write_bytes(b"booting\n")
            process = MagicMock(spec=subprocess.Popen)
            process.poll.return_value = None
            family = cast(int, getattr(socket, "AF_UNIX", None))
            with socket.socket(family, socket.SOCK_STREAM) as listener:
                listener.bind(str(endpoint))
                listener.listen(1)
                listener.settimeout(5)

                def serve() -> None:
                    try:
                        connection, _ = listener.accept()
                        with connection:
                            connection.settimeout(5)
                            with connection.makefile("rb") as incoming:
                                frame = incoming.read(
                                    control_session.OUTER_HEADER.size + 32
                                )
                            self.assertEqual(frame[-32:], capability)
                            connection.sendall(
                                control_session.OUTER_HEADER.pack(
                                    b"NVXS",
                                    1,
                                    control_session.OUTER_WAIT,
                                    0,
                                    bytes(16),
                                    0,
                                    0,
                                    0,
                                )
                            )
                            # A boot taking longer than the old one-second
                            # attach timeout must preserve this connection.
                            threading.Event().wait(1.25)
                            connection.sendall(
                                control_session.OUTER_HEADER.pack(
                                    b"NVXS",
                                    1,
                                    control_session.OUTER_READY,
                                    0,
                                    bytes([1]) * 16,
                                    1,
                                    0,
                                    0,
                                )
                            )
                    except BaseException as error:
                        failures.append(error)

                worker = threading.Thread(target=serve, daemon=True)
                worker.start()
                with connect_when_ready(endpoint, capability, process, log, 15):
                    pass
                worker.join(5)
                self.assertFalse(worker.is_alive())
                self.assertEqual(failures, [])

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
