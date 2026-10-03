import argparse
import contextlib
import io
import plistlib
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nvx_tools.doctor import (
    Check,
    artifact_check,
    binary_arch,
    command_doctor,
    device_check,
    entitlement_check,
)


class DoctorTests(unittest.TestCase):
    def test_windows_pe_architecture_and_corrupt_header_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "openvmm.exe"
            header = bytearray(136)
            header[:2] = b"MZ"
            struct.pack_into("<I", header, 60, 128)
            header[128:132] = b"PE\0\0"
            struct.pack_into("<H", header, 132, 0x8664)
            path.write_bytes(header)
            self.assertEqual(binary_arch(path), "x86_64")
            header[128:132] = b"BAD!"
            path.write_bytes(header)
            self.assertEqual(binary_arch(path), "unknown")

    def test_artifact_arch_and_deleted_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "vmlinux"
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", header, 18, 183)
            path.write_bytes(header)
            self.assertEqual(binary_arch(path), "aarch64")
            self.assertTrue(artifact_check(path, "aarch64").ok)
            self.assertFalse(artifact_check(path, "x86_64").ok)
            path.unlink()
            result = artifact_check(path, "aarch64")
            self.assertFalse(result.ok)
            self.assertIn("download", result.advice)

    def test_missing_hypervisor(self) -> None:
        check = device_check(Path("/nvx-doctor-missing-device"))
        self.assertFalse(check.ok)
        self.assertIn("read/write", check.advice)

    def test_entitlement_present_and_unsigned_control(self) -> None:
        binary = Path("openvmm")
        output = plistlib.dumps({"com.apple.security.hypervisor": True})
        with patch(
            "nvx_tools.doctor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, output, b""),
        ):
            self.assertTrue(entitlement_check(binary).ok)
        with patch(
            "nvx_tools.doctor.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, b"", b"unsigned"),
        ):
            result = entitlement_check(binary)
            self.assertFalse(result.ok)
            self.assertEqual(result.advice, "run nvx setup")

    def test_doctor_exit_status_flips_on_failed_check(self) -> None:
        args = argparse.Namespace(backend="hvf", json=True)
        for ok, expected in ((True, 0), (False, 1)):
            with (
                patch(
                    "nvx_tools.doctor.checks",
                    return_value=[Check("artifact", ok, "injected")],
                ),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(command_doctor(args), expected)
