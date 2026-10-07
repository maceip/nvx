import hashlib
import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.snapshot import format_report, verify_snapshot


def varint(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def field(number: int, value: int | bytes) -> bytes:
    if isinstance(value, int):
        return varint(number << 3) + varint(value)
    return varint((number << 3) | 2) + varint(len(value)) + value


class SnapshotTests(unittest.TestCase):
    def test_architecture_length_and_symlink_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.bin").write_bytes(
                b"".join(
                    [
                        field(1, 5),
                        field(4, 4),
                        field(7, b"aarch64"),
                        field(8, 3),
                        field(12, b"OPENVMM_SNAPSHOT_V5\0"),
                    ]
                )
            )
            (root / "memory.bin").write_bytes(b"ram!")
            (root / "state.bin").write_bytes(b"vmm")
            self.assertEqual(
                verify_snapshot(root, expected_arch="aarch64").architecture, "aarch64"
            )
            with self.assertRaisesRegex(ScriptError, "cross-architecture"):
                verify_snapshot(root, expected_arch="x86_64")
            (root / "memory.bin").write_bytes(b"bad")
            with self.assertRaisesRegex(ScriptError, "manifest records"):
                verify_snapshot(root)
            (root / "memory.bin").unlink()
            (root / "memory.bin").symlink_to(root / "state.bin")
            with self.assertRaisesRegex(ScriptError, "symlink"):
                verify_snapshot(root)

    def test_v6_payload_digest_and_legacy_negative_control(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, memory = b"state", b"ram!"
            base = [field(4, len(memory)), field(7, b"aarch64"), field(8, len(state))]
            (root / "state.bin").write_bytes(state)
            (root / "memory.bin").write_bytes(memory)
            (root / "manifest.bin").write_bytes(
                b"".join(
                    [
                        field(1, 6),
                        *base,
                        field(12, b"OPENVMM_SNAPSHOT_V6\0"),
                        field(9, hashlib.sha256(state).digest()),
                        field(10, hashlib.sha256(memory).digest()),
                    ]
                )
            )
            verify_snapshot(root)
            (root / "memory.bin").write_bytes(b"bad!")
            with self.assertRaisesRegex(
                ScriptError, "memory.bin SHA-256 digest mismatch"
            ):
                verify_snapshot(root)
            # Retained legacy v5 only promises structural checks; the same
            # tampered payload is admitted there. The verdict must flip.
            (root / "manifest.bin").write_bytes(
                b"".join([field(1, 5), *base, field(12, b"OPENVMM_SNAPSHOT_V5\0")])
            )
            self.assertEqual(verify_snapshot(root).version, 5)

    def test_v6_tier_scratch_claim_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, memory, scratch = b"state", b"ram!", b"disk-bytes"
            scratch_digest = hashlib.sha256(scratch).digest()
            block = b"".join(
                [
                    field(1, b"scratch"),
                    field(3, len(scratch)),
                    field(4, b"sha256"),
                    field(5, scratch_digest),
                    field(6, b"scratch.img"),
                ]
            )
            contract = b"".join([field(3, b"hvf"), field(21, block)])
            (root / "state.bin").write_bytes(state)
            (root / "memory.bin").write_bytes(memory)
            (root / "scratch.img").write_bytes(scratch)
            (root / "manifest.bin").write_bytes(
                b"".join(
                    [
                        field(1, 6),
                        field(4, len(memory)),
                        field(7, b"aarch64"),
                        field(8, len(state)),
                        field(9, hashlib.sha256(state).digest()),
                        field(10, hashlib.sha256(memory).digest()),
                        field(11, contract),
                        field(12, b"OPENVMM_SNAPSHOT_V6\0"),
                        field(15, b"workload-start"),
                        field(16, b"clone"),
                        field(17, 7),
                    ]
                )
            )
            report = verify_snapshot(root)
            self.assertEqual(report.snapshot_tier, "workload-start")
            self.assertEqual(report.source_hypervisor, "hvf")
            self.assertEqual(report.scratch_policy, "paired")
            self.assertEqual(report.scratch_sha256, scratch_digest.hex())
            self.assertFalse(report.resume_claimed)
            text = format_report(report)
            self.assertIn("snapshot tier: workload-start", text)
            self.assertIn("source hypervisor: hvf", text)
            self.assertIn("scratch policy: paired", text)
            self.assertIn("resume claim: absent", text)
            self.assertIn("consumed sections: invariants,image-binding,sandbox", text)

            (root / "scratch.img").write_bytes(b"tampered!!")
            with self.assertRaisesRegex(ScriptError, "scratch.img SHA-256"):
                verify_snapshot(root)
            (root / "scratch.img").unlink()
            fresh = b"".join(
                [
                    field(1, b"scratch"),
                    field(3, 512),
                    field(4, b"fresh"),
                    field(6, b""),
                ]
            )
            (root / "manifest.bin").write_bytes(
                b"".join(
                    [
                        field(1, 6),
                        field(4, len(memory)),
                        field(7, b"aarch64"),
                        field(8, len(state)),
                        field(9, hashlib.sha256(state).digest()),
                        field(10, hashlib.sha256(memory).digest()),
                        field(11, b"".join([field(3, b"kvm"), field(21, fresh)])),
                        field(12, b"OPENVMM_SNAPSHOT_V6\0"),
                        field(15, b"platform"),
                        field(16, b"clone"),
                    ]
                )
            )
            fresh_report = verify_snapshot(root)
            self.assertEqual(fresh_report.scratch_policy, "fresh")
            self.assertEqual(fresh_report.scratch_sha256, "")
            (root / "scratch.img").write_bytes(b"nope")
            with self.assertRaisesRegex(ScriptError, "must not include scratch.img"):
                verify_snapshot(root)
            (root / "scratch.img").unlink()
            (root / "resume.claim").write_bytes(b"claimed")
            claimed = verify_snapshot(root)
            self.assertTrue(claimed.resume_claimed)
            self.assertIn("resume claim: present", format_report(claimed))
            (root / "resume.claim").unlink()
            (root / "resume.claim").symlink_to(root / "state.bin")
            with self.assertRaisesRegex(ScriptError, "symlink"):
                verify_snapshot(root)
