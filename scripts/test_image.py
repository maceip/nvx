import errno
import io
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tarfile import TarInfo
from tarfile import open as tar_open
from unittest.mock import patch

from nvx_tools.common import ScriptError
from nvx_tools.image import (
    ImageCache,
    apply_layer,
    canonical,
    convert,
    ensure,
    split_prefix,
)


def layer(path: Path, entries: list[tuple[str, bytes | str]]) -> Path:
    with tar_open(path, "w") as archive:
        for name, content in entries:
            member = TarInfo(name)
            member.mode = 0o755 if name.endswith("/") else 0o644
            if name.endswith("/"):
                member.type = b"5"
                archive.addfile(member)
            elif isinstance(content, str):
                member.type = b"2"
                member.linkname = content
                archive.addfile(member)
            else:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
    return path


class ImageTests(unittest.TestCase):
    def test_converter_setup_failure_preserves_the_actionable_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = ImageCache(Path(directory))

            def run(
                command: list[str], **options: object
            ) -> subprocess.CompletedProcess[bytes]:
                if command[1] == "build":
                    output = options["stdout"]
                    assert hasattr(output, "write")
                    output.write(b"dpkg: No space left on device\n")
                    return subprocess.CompletedProcess(command, 100)
                return subprocess.CompletedProcess(command, 0)

            with (
                patch("nvx_tools.image.ImageCache", return_value=cache),
                patch("nvx_tools.image.subprocess.run", side_effect=run),
            ):
                with self.assertRaisesRegex(
                    ScriptError,
                    "OCI converter setup failed:[\\s\\S]*No space left on device",
                ):
                    convert("fixture", pull=False)

    def test_whiteout_and_opaque_are_order_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "root"
            root.mkdir()
            apply_layer(
                root,
                layer(
                    work / "a.tar",
                    [("a/", b""), ("a/old", b"old"), ("deleted", b"bye")],
                ),
                metadata=False,
            )
            apply_layer(
                root,
                layer(
                    work / "b.tar",
                    [
                        ("a/new", b"new"),
                        ("a/.wh..wh..opq", b""),
                        (".wh.deleted", b""),
                        ("deleted", b"replaced"),
                    ],
                ),
                metadata=False,
            )
            self.assertFalse((root / "a/old").exists())
            self.assertEqual((root / "a/new").read_bytes(), b"new")
            self.assertEqual((root / "deleted").read_bytes(), b"replaced")
            self.assertFalse((root / ".wh.deleted").exists())

    def test_overlay_metadata_and_symlink_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "root"
            root.mkdir()
            for prefix in ("trusted", "user"):
                path = work / "bad.tar"
                with tar_open(path, "w") as archive:
                    member = TarInfo("file")
                    member.pax_headers = {f"SCHILY.xattr.{prefix}.overlay.opaque": "y"}
                    archive.addfile(member)
                with self.assertRaisesRegex(ScriptError, "overlay metadata"):
                    apply_layer(root, path, metadata=False)
            path = layer(
                work / "escape.tar",
                [("escape", "../../outside"), ("escape/file", b"bad")],
            )
            with self.assertRaisesRegex(ScriptError, "escapes"):
                apply_layer(root, path, metadata=False)
            self.assertFalse((work / "outside").exists())

    def test_absolute_guest_symlink_stays_inside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            root = work / "root"
            root.mkdir()
            apply_layer(
                root,
                layer(
                    work / "a.tar",
                    [
                        ("usr/", b""),
                        ("usr/bin/", b""),
                        ("bin", "/usr/bin"),
                        ("bin/tool", b"guest"),
                    ],
                ),
                metadata=False,
            )
            self.assertEqual((root / "usr/bin/tool").read_bytes(), b"guest")

    def test_exact_digest_prefix_only(self) -> None:
        bases = {"short": ["a"], "long": ["a", "b"]}
        self.assertEqual(split_prefix(["a", "b", "c"], bases), ("long", 2))
        self.assertEqual(
            split_prefix(["same-bytes-different-digest", "b"], bases), (None, 0)
        )
        self.assertEqual(split_prefix(["b", "a"], bases), (None, 0))

    def fixture(self, root: Path) -> tuple[ImageCache, str]:
        cache = ImageCache(root / "cache")
        data = root / "blob"
        data.write_bytes(b"erofs test fixture")
        blob = cache.admit(data)
        data.write_bytes(b"private scratch template")
        template = cache.admit(data)
        manifest = {
            "image_version": 1,
            "converter_version": 1,
            "layers": [
                {
                    "role": "custom",
                    "digest": blob,
                    "uuid": "11111111-1111-1111-1111-111111111111",
                }
            ],
            "scratch_template": template,
        }
        value = cache.publish(manifest, "example:1")
        self.assertEqual(cache.publish(manifest, "example:1"), value)
        return cache, value

    def test_determinism_cache_accounting_and_corrupt_admission(self) -> None:
        self.assertEqual(canonical({"b": 2, "a": 1}), canonical({"a": 1, "b": 2}))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache, value = self.fixture(root)
            doc = cache.resolve(value)[1]
            self.assertEqual(doc["layers"][0]["role"], "custom")
            cache.admit(root / "blob")
            self.assertEqual((cache.hits, cache.misses), (1, 2))
            path = cache.root / "images" / value[7:]
            path.write_bytes(path.read_bytes().replace(b"custom", b"distro"))
            with self.assertRaisesRegex(ScriptError, "manifest is corrupt"):
                cache.resolve(value)

    def test_missing_ref_prepares_once_but_corruption_never_reconverts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache, value = self.fixture(Path(directory))
            with (
                patch("nvx_tools.image.ImageCache", return_value=cache),
                patch("nvx_tools.image.convert") as convert,
            ):
                self.assertEqual(ensure("example:1")[0], value)
                convert.assert_not_called()
                manifest = cache.resolve(value)[1]

                def prepare(ref: str, *, pull: bool) -> str:
                    self.assertTrue(pull)
                    return cache.publish(manifest, ref)

                convert.side_effect = prepare
                self.assertEqual(ensure("missing:1")[0], value)
                convert.assert_called_once_with("missing:1", pull=True)
                convert.reset_mock()
                (cache.root / "images" / value[7:]).write_bytes(b"corrupt")
                with self.assertRaises(ScriptError):
                    ensure("example:1")
                convert.assert_not_called()

    def test_sandbox_and_snapshot_refcounts_prevent_gc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache, value = self.fixture(Path(directory))
            cache.hold("sandbox", value, "sandbox")
            cache.hold("snapshot", value, "snapshot")
            with self.assertRaisesRegex(ScriptError, "referenced"):
                cache.rm(value)
            cache.release("sandbox")
            with self.assertRaisesRegex(ScriptError, "referenced"):
                cache.rm(value)
            cache.release("snapshot")
            cache.rm(value)
            self.assertEqual(list((cache.root / "blobs").iterdir()), [])

    def test_private_concurrent_scratch_and_duplicate_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache, value = self.fixture(Path(directory))

            def acquire(owner: str) -> Path:
                return cache.acquire(owner, value)

            with ThreadPoolExecutor(2) as pool:
                paths = list(pool.map(acquire, ["first", "second"]))
            self.assertNotEqual(*paths)
            paths[0].write_bytes(b"first tenant")
            self.assertEqual(paths[1].read_bytes(), b"private scratch template")
            with self.assertRaisesRegex(ScriptError, "already in use"):
                cache.acquire("first", value)
            self.assertEqual(paths[0].read_bytes(), b"first tenant")
            for owner in ["first", "second"]:
                cache.release(owner)

    def test_scratch_space_failure_is_actionable_and_releases_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache, value = self.fixture(Path(directory))
            with patch(
                "nvx_tools.image.shutil.copyfileobj",
                side_effect=OSError(errno.ENOSPC, "full"),
            ):
                with self.assertRaisesRegex(ScriptError, "out of space"):
                    cache.acquire("failed", value)
            self.assertFalse((cache.root / "leases/failed").exists())
            self.assertFalse((cache.root / "leases/failed.ext4").exists())
