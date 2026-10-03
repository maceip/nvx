import io
import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nvx_tools.build import kernel_provenance_inputs
from nvx_tools.build_constants import BuildConstants
from nvx_tools.common import (
    ScriptError,
    artifact_path,
    openvmm_binary_path,
    write_sha256_sums,
)
from nvx_tools.release import (
    _latest_release_asset,  # pyright: ignore[reportPrivateUsage]
    _validate_linux_source_archive,  # pyright: ignore[reportPrivateUsage]
    _validate_source_manifest_metadata,  # pyright: ignore[reportPrivateUsage]
    download_latest_release,
)
from nvx_tools.runtime_release import install, inventory


class ReleaseTests(unittest.TestCase):
    def test_source_pins_validate_each_native_architecture(self) -> None:
        manifest = json.loads(
            (BuildConstants.REPO_ROOT / "SOURCE-MANIFEST.json").read_bytes()
        )
        for architecture in ("x86_64", "aarch64"):
            with (
                self.subTest(architecture=architecture),
                patch("nvx_tools.build.platform.machine", return_value=architecture),
            ):
                _validate_source_manifest_metadata(manifest, kernel_provenance_inputs())

    def test_arm_source_pins_refuse_x86_archive_and_patch_substitution(self) -> None:
        manifest = json.loads(
            (BuildConstants.REPO_ROOT / "SOURCE-MANIFEST.json").read_bytes()
        )
        with patch("nvx_tools.build.platform.machine", return_value="aarch64"):
            inputs = kernel_provenance_inputs()
            manifest["architectures"]["aarch64"]["alpine"]["minirootfs_sha256"] = (
                manifest["alpine"]["minirootfs_sha256"]
            )
            with self.assertRaisesRegex(ScriptError, "Alpine minirootfs_sha256"):
                _validate_source_manifest_metadata(manifest, inputs)
            manifest["architectures"]["aarch64"]["linux"]["patches"] = manifest[
                "linux"
            ]["patches"]
            with self.assertRaisesRegex(ScriptError, "Linux patches"):
                _validate_source_manifest_metadata(manifest, inputs)

    def test_matching_arm_sources_do_not_require_x86_driver(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "build").mkdir()
            config = b"CONFIG_ARM64=y\n"
            manifest = b'{"linux":{"patches":["x86-only.patch"]}}'
            (root / "build/vmlinux.config").write_bytes(config)
            (root / "SOURCE-MANIFEST.json").write_bytes(manifest)
            (root / "build/vmlinux.provenance.json").write_text(
                json.dumps({"source": {"patches": []}})
            )
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", header, 18, 183)
            (root / "build/vmlinux").write_bytes(header)
            archive_path = root / "source.tar.gz"
            with tarfile.open(archive_path, "w:gz") as archive:
                for name, body in (
                    ("source/vmlinux.config", config),
                    ("source/SOURCE-MANIFEST.json", manifest),
                    ("source/linux-6.18.38/arch/arm64/Kconfig", b"arm source"),
                ):
                    row = tarfile.TarInfo(name)
                    row.size = len(body)
                    archive.addfile(row, io.BytesIO(body))
            with (
                patch.object(BuildConstants, "REPO_ROOT", root),
                patch.object(BuildConstants, "BUILD_DIR", root / "build"),
            ):
                _validate_linux_source_archive(archive_path)
                struct.pack_into("<H", header, 18, 62)
                (root / "build/vmlinux").write_bytes(header)
                with self.assertRaisesRegex(ScriptError, "matching kernel sources"):
                    _validate_linux_source_archive(archive_path)

    def test_untrusted_release_is_never_installed(self) -> None:
        from nvx_tools.release import (
            _ReleaseAsset,  # pyright: ignore[reportPrivateUsage]
        )

        def fixture_download(url: str, destination: Path, **kwargs: object) -> None:
            destination.write_bytes(b"archive")

        with (
            patch(
                "nvx_tools.release._latest_release_asset_with_fallback",
                return_value=(
                    _ReleaseAsset(
                        "v0.1.0",
                        "nvx-0.1.0-linux-arm64.tar.gz",
                        "https://example.test/archive",
                        7,
                    ),
                    None,
                ),
            ),
            patch("nvx_tools.release.download", side_effect=fixture_download),
            patch(
                "nvx_tools.release_auth.verify_release_attestation",
                side_effect=ScriptError("invalid signature"),
            ),
            patch("nvx_tools.release._install_release_archive") as install_archive,
        ):
            with self.assertRaisesRegex(ScriptError, "invalid signature"):
                download_latest_release("example/nvx", "linux-arm64")
            install_archive.assert_not_called()
            download_latest_release("example/nvx", "linux-arm64", allow_unsigned=True)
            install_archive.assert_called_once()

    def test_shipped_cli_resolves_both_release_layouts_and_checkout_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(BuildConstants, "REPO_ROOT", root),
                patch.object(BuildConstants, "BUILD_DIR", root / "build"),
            ):
                # A checkout must not pick files from guest source directories.
                (root / "guest").mkdir()
                (root / "guest/initramfs.cpio.gz").write_bytes(b"not a release")
                self.assertEqual(
                    artifact_path("initramfs.cpio.gz"),
                    root / "build/initramfs.cpio.gz",
                )
                (root / "bin").mkdir()
                (root / "bin/nvx").write_bytes(b"launcher")
                # Legacy source-inclusive x86 archives use guest/ and provenance/.
                (root / "provenance").mkdir()
                (root / "provenance/initramfs.provenance.json").write_bytes(b"{}")
                self.assertEqual(
                    artifact_path("initramfs.cpio.gz"),
                    root / "guest/initramfs.cpio.gz",
                )
                self.assertEqual(
                    artifact_path("initramfs.provenance.json"),
                    root / "provenance/initramfs.provenance.json",
                )
                for system, executable in (("posix", "openvmm"), ("nt", "openvmm.exe")):
                    (root / "bin" / executable).write_bytes(b"vmm")
                    with patch("nvx_tools.common.os.name", system):
                        self.assertEqual(
                            openvmm_binary_path(), root / "bin" / executable
                        )
                # Current self-contained archives and checkout build outputs win.
                (root / "build").mkdir()
                (root / "build/initramfs.cpio.gz").write_bytes(b"current")
                self.assertEqual(
                    artifact_path("initramfs.cpio.gz"),
                    root / "build/initramfs.cpio.gz",
                )
                self.assertEqual(artifact_path("missing"), root / "build/missing")

    def test_arm_download_discovers_versioned_assets_for_the_selected_platform(
        self,
    ) -> None:
        for platform in ("darwin-arm64", "linux-arm64"):
            with self.subTest(platform=platform):
                legacy = {
                    "name": f"nvx-{platform}.tar.gz",
                    "url": "https://api.github.com/assets/legacy",
                    "size": 123,
                }
                versioned = {
                    **legacy,
                    "name": f"nvx-0.1.0-{platform}.tar.gz",
                    "url": "https://api.github.com/assets/versioned",
                }
                wrong_platform = {
                    **versioned,
                    "name": "nvx-0.1.0-windows-whp.zip",
                }
                with patch("nvx_tools.release.credential_safe_opener") as opener:
                    opener.return_value.open.return_value = io.BytesIO(
                        json.dumps(
                            [{"tag_name": "v0.1.0", "assets": [legacy]}]
                        ).encode()
                    )
                    with self.assertRaisesRegex(ScriptError, "no GitHub release"):
                        _latest_release_asset("example/nvx", platform, None)
                    opener.return_value.open.return_value = io.BytesIO(
                        json.dumps(
                            [
                                {
                                    "tag_name": "v0.1.0",
                                    "assets": [legacy, wrong_platform, versioned],
                                }
                            ]
                        ).encode()
                    )
                    selected = _latest_release_asset("example/nvx", platform, None)
                self.assertEqual(selected.name, versioned["name"])
                self.assertEqual(selected.url, versioned["url"])
                self.assertEqual(selected.tag, "v0.1.0")

    def test_architecture_inventory_and_unknown_platform(self) -> None:
        self.assertIn("Image", inventory("darwin-arm64"))
        self.assertEqual(inventory("darwin-arm64"), inventory("linux-arm64"))
        self.assertNotIn("Image", inventory("linux-kvm"))
        self.assertIn("initramfs-ubuntu.cpio.gz", inventory("windows-whp"))
        with self.assertRaises(ScriptError):
            inventory("darwin-x86_64")

    def test_install_verifies_inventory_tamper_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / "package"
            (package / "build").mkdir(parents=True)
            (package / "bin").mkdir()
            for name in (
                "bin/nvx",
                "bin/openvmm",
                "scripts/nvx.py",
                "scripts/nvx_tools/mcp.py",
                "sdk/python/nvx_sdk/__init__.py",
                "sdk/typescript/dist/index.js",
                "requirements-signing.txt",
                "licenses/LICENSE-OPENVMM",
                "licenses/COPYING-LINUX",
                "build/openvmm.provenance.json",
                "build/vmlinux.provenance.json",
                "build/initramfs.provenance.json",
            ):
                path = package / name
                path.parent.mkdir(exist_ok=True, parents=True)
                path.write_bytes(b"fixture launcher")
            for name in inventory("linux-arm64"):
                (package / "build" / name).write_bytes(name.encode())
            header = bytearray(64)
            header[:6] = b"\x7fELF\x02\x01"
            struct.pack_into("<H", header, 18, 183)
            (package / "bin/openvmm").write_bytes(header)
            (package / "build/vmlinux").write_bytes(header)
            (package / "NVX-RELEASE.json").write_text(
                json.dumps(
                    {
                        "release_version": 1,
                        "platform": "linux-arm64",
                        "architecture": "aarch64",
                        "guest_artifacts": list(inventory("linux-arm64")),
                    }
                )
            )
            write_sha256_sums(package)
            launcher = install(package, root / "installed")
            with patch("nvx_tools.runtime_release.binary_arch", return_value="x86_64"):
                with self.assertRaisesRegex(ScriptError, "architecture"):
                    install(package, root / "wrong-architecture")
            with patch(
                "nvx_tools.runtime_release._core_platform", return_value="darwin"
            ):
                with self.assertRaisesRegex(ScriptError, "architecture"):
                    install(package, root / "wrong-platform")
            self.assertEqual(launcher.read_bytes(), b"fixture launcher")
            with self.assertRaises(ScriptError):
                install(package, root / "installed")
            (package / "build/Image").write_bytes(b"tampered")
            with self.assertRaises(ScriptError):
                install(package, root / "tampered")
            write_sha256_sums(package)
            (package / "build/Image").unlink()
            write_sha256_sums(package)
            with self.assertRaisesRegex(ScriptError, "incomplete"):
                install(package, root / "missing")
