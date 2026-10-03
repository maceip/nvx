import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.hvf import boot_tokens, network_arguments
from nvx_tools.sandbox import SandboxLaunch, SandboxLayer


class HvfTests(unittest.TestCase):
    def test_arm_layer_order_and_writable_scratch(self) -> None:
        launch = SandboxLaunch(
            (
                SandboxLayer("custom", Path("/custom"), "c"),
                SandboxLayer("distro", Path("/distro"), "d"),
            ),
            Path("/scratch"),
        )
        self.assertEqual(
            launch.openvmm_arguments("hvf"),
            [
                "--virtio-blk",
                "file:/distro,ro",
                "--virtio-blk",
                "file:/custom,ro",
                "--virtio-blk",
                "file:/scratch",
            ],
        )
        tokens = launch.kernel_command_line("", "hvf").split()
        self.assertIn("nvx_layer=distro,/dev/vda,d", tokens)
        self.assertIn("nvx_layer=custom,/dev/vdb,c", tokens)
        self.assertIn("nvx_scratch=/dev/vdc,ext4", tokens)
        self.assertIn("nvx_workload_uid=65534", tokens)
        self.assertIn("nvx_control_tty=hvc1", boot_tokens(True))

    def test_linux_arm_kvm_uses_the_same_direct_device_layout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layer = root / "layer.erofs"
            scratch = root / "scratch.ext4"
            layer.write_bytes(b"layer")
            scratch.write_bytes(b"scratch")
            launch = SandboxLaunch(
                (
                    SandboxLayer(
                        "custom", layer, "11111111-1111-1111-1111-111111111111"
                    ),
                ),
                scratch,
            )
            self.assertEqual(
                launch.openvmm_arguments("kvm", architecture="aarch64"),
                launch.openvmm_arguments("hvf"),
            )
            self.assertEqual(
                launch.kernel_command_line(backend="kvm", architecture="aarch64"),
                launch.kernel_command_line(backend="hvf"),
            )

    def test_network_identity_and_default_deny(self) -> None:
        args, cmdline = network_arguments({"net": "192.168.127.9/24"})
        self.assertEqual(
            args, ["--virtio-net", "consomme:192.168.127.0/24,egress=deny,ingress=deny"]
        )
        self.assertIn("virtnet_ip=192.168.127.2", cmdline)
        self.assertEqual(network_arguments({}), ([], ""))
        with self.assertRaises(ScriptError):
            network_arguments({"network_egress": "allow"})
        with self.assertRaises(ScriptError):
            network_arguments({"net": "192.168.127.0/31"})
        with self.assertRaises(ScriptError):
            network_arguments({"network_ingress": "allow"})

    def test_proxy_permits_only_an_exact_gateway_port(self) -> None:
        args, _ = network_arguments(
            {"net": "192.168.127.0/24", "network_proxy": "192.168.127.1:8443"}
        )
        self.assertIn("gwproxy=8443", args[1])
        self.assertNotIn("gwloopback", args[1])
        for proxy in (
            "192.168.127.2:8443",
            "192.168.127.1:0",
            "192.168.127.1:8443,gwloopback",
        ):
            with self.assertRaises(ScriptError):
                network_arguments({"net": "192.168.127.0/24", "network_proxy": proxy})
