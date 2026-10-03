import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nvx_tools import warm
from nvx_tools.common import ScriptError, sha256_file
from nvx_tools.image import canonical, digest
from nvx_tools.warm_tests import validate_batch, validate_clone


class WarmTests(unittest.TestCase):
    def test_bad_manifest_inventory_and_symlink_are_refused_before_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = (
                "scratch.ext4",
                "source/image.json",
                "source/policy.json",
                "source/versions.json",
                "snapshot/manifest.bin",
                "snapshot/state.bin",
                "snapshot/memory.bin",
            )
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture")
            document: dict[str, object] = {
                "warm_version": 1,
                "backend": "hvf",
                "architecture": "aarch64",
                "image": "fixture",
                "files": {name: sha256_file(root / name) for name in names},
            }

            def write(value: dict[str, object]) -> None:
                (root / "warm.json").write_bytes(
                    canonical({**value, "sha256": digest(canonical(value))})
                )

            with (
                patch("nvx_tools.warm.lifecycle.start") as spawn,
                patch("nvx_tools.warm.ImageCache") as cache,
            ):
                write({**document, "warm_version": 99})
                with self.assertRaisesRegex(ScriptError, "manifest failed integrity"):
                    warm.clone(root)
                write(
                    {
                        **document,
                        "files": {
                            name: sha256_file(root / name) for name in names[:-1]
                        },
                    }
                )
                with self.assertRaisesRegex(ScriptError, "inventory"):
                    warm.clone(root)
                write(document)
                policy = root / "source/policy.json"
                policy.unlink()
                policy.symlink_to(root / "scratch.ext4")
                with self.assertRaisesRegex(ScriptError, "file failed integrity"):
                    warm.clone(root)
                spawn.assert_not_called()
                cache.assert_not_called()

    def test_repair_oracle_rejects_missing_repair_and_private_channel_leaks(
        self,
    ) -> None:
        record = dict(
            mid="fresh",
            hostname="nvx-fresh",
            clock=100.0,
            uid=65534,
            channel="denied",
            egress=True,
            output=42,
            initialized=True,
        )
        validate_clone(record, "fresh", "source", 99.0, 101.0, True)
        for key, value in [
            ("mid", "source"),
            ("hostname", "nvx-source"),
            ("clock", 0.0),
            ("uid", 0),
            ("channel", "visible"),
            ("egress", False),
            ("output", 0),
            ("initialized", False),
        ]:
            with self.subTest(key=key), self.assertRaises(ScriptError):
                validate_clone(
                    {**record, key: value}, "fresh", "source", 99.0, 101.0, True
                )
        with self.assertRaisesRegex(ScriptError, "fresh host generation"):
            validate_clone(record, "fresh", "fresh", 99.0, 101.0, True)
        with self.assertRaisesRegex(ScriptError, "control unexpectedly passed"):
            validate_clone(record, "fresh", "source", 99.0, 101.0, False)
        validate_clone(
            {**record, "mid": "source"}, "fresh", "source", 99.0, 101.0, False
        )

    def test_batch_oracle_rejects_repeated_entropy_runtime_restart_and_incomplete_control(
        self,
    ) -> None:
        records = [
            dict(
                id=str(i),
                generation=str(i),
                mid=str(i),
                hostname="nvx-" + str(i),
                entropy=str(i),
                origin="retained",
            )
            for i in range(2)
        ]
        controls = [dict(mid="stale"), dict(mid="stale")]
        validate_batch(records, controls, count=2)
        for key in ("id", "generation", "mid", "hostname", "entropy", "origin"):
            changed = {
                **records[1],
                key: records[0][key] if key != "origin" else "restarted",
            }
            with self.subTest(key=key), self.assertRaises(ScriptError):
                validate_batch([records[0], changed], controls, count=2)
        with self.assertRaisesRegex(ScriptError, "incomplete"):
            validate_batch(records, controls[:1], count=2)
        with self.assertRaisesRegex(ScriptError, "stale machine identity"):
            validate_batch(records, [dict(mid="a"), dict(mid="b")], count=2)

    def test_policy_inventory_tamper_and_credential_restore_admission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = (
                "scratch.ext4",
                "snapshot/manifest.bin",
                "snapshot/state.bin",
                "snapshot/memory.bin",
                "source/image.json",
                "source/policy.json",
                "source/versions.json",
            )
            for name in names:
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(b"fixture")
            document = {
                "warm_version": 1,
                "backend": "hvf",
                "architecture": "aarch64",
                "image": "fixture",
                "files": {name: sha256_file(root / name) for name in names},
                "credential_proxy": {"restore": "fresh binding required"},
            }
            document["sha256"] = digest(canonical(document))
            (root / "warm.json").write_bytes(canonical(document))
            with (
                patch("nvx_tools.warm.ImageCache") as cache,
                patch(
                    "nvx_tools.warm.verify_snapshot",
                    return_value=SimpleNamespace(architecture="aarch64"),
                ),
                patch("nvx_tools.warm.lifecycle.start") as spawn,
            ):
                cache.return_value.resolve.return_value = ("fixture", {"layers": []})
                self.assertEqual(warm.admit(root)["architecture"], "aarch64")
                with self.assertRaisesRegex(ScriptError, "hypervisor"):
                    warm.admit(root, "kvm")
                with self.assertRaisesRegex(ScriptError, "fresh host binding"):
                    warm.clone(root)
                spawn.assert_not_called()
                cache.return_value.acquire.assert_not_called()
                (root / "source/policy.json").write_bytes(b"tampered")
                with self.assertRaisesRegex(ScriptError, "integrity"):
                    warm.admit(root)
