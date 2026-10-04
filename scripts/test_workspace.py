import io
import tarfile
import tempfile
import unittest
from pathlib import Path

from nvx_tools.common import ScriptError
from nvx_tools.workspace import collect_outputs, parse_workspace, unpack_outputs


class WorkspaceTests(unittest.TestCase):
    def test_archive_rejects_traversal_links_and_duplicate_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index, name in enumerate(
                (
                    "../escape",
                    "/absolute",
                    "C:/escape",
                    "C:escape",
                    "file:stream",
                    "link",
                    "duplicate",
                )
            ):
                archive = root / f"{index}.tar"
                with tarfile.open(archive, "w") as output:
                    member = tarfile.TarInfo(name)
                    if name == "link":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "../../escape"
                    else:
                        member.size = 1
                    output.addfile(member, io.BytesIO(b"x"))
                    if name == "duplicate":
                        output.addfile(member, io.BytesIO(b"y"))
                target = root / str(index)
                target.mkdir()
                with self.assertRaises(ScriptError):
                    unpack_outputs(archive, target)
            self.assertFalse((root / "escape").exists())

    def test_reserved_targets_and_readonly_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(parse_workspace(directory + ":/src").access, "ro")
            self.assertEqual(parse_workspace(directory + ":/src:rw").access, "rw")
            for target in (
                "/",
                "/etc",
                "/proc",
                "/sys",
                "/dev",
                "/.nvx-agent",
                "/etc/subdir",
            ):
                with self.assertRaises(ScriptError):
                    parse_workspace(directory + ":" + target)

    def test_clean_only_no_overwrite_and_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "file").write_bytes(b"\0\xff" * 512)
            with self.assertRaisesRegex(ScriptError, "clean"):
                collect_outputs(source, root / "bad", clean=False)
            self.assertFalse((root / "bad").exists())
            collect_outputs(source, root / "out", clean=True)
            self.assertEqual(
                (root / "out/file").read_bytes(), (source / "file").read_bytes()
            )
            with self.assertRaisesRegex(ScriptError, "never overwritten"):
                collect_outputs(source, root / "out", clean=True)
            (source / "escape").symlink_to(root / "out/file")
            with self.assertRaisesRegex(ScriptError, "symlink"):
                collect_outputs(source, root / "escape-out", clean=True)
            self.assertFalse((root / "escape-out").exists())
