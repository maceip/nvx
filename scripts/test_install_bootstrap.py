"""The authenticated bootstrap refuses archive paths and special file attacks."""

import importlib.util
import io
import tarfile
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "install_nvx", Path(__file__).with_name("install-nvx.py")
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)


class BootstrapTests(unittest.TestCase):
    def test_safe_archive_and_escape_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, kind, safe in (
                ("nvx/scripts/nvx.py", tarfile.REGTYPE, True),
                ("../escaped", tarfile.REGTYPE, False),
                ("/absolute", tarfile.REGTYPE, False),
                ("nvx/link", tarfile.SYMTYPE, False),
                ("nvx/hard", tarfile.LNKTYPE, False),
                ("nvx/device", tarfile.CHRTYPE, False),
                ("nvx\\escape", tarfile.REGTYPE, False),
            ):
                archive = root / "archive.tar.gz"
                with tarfile.open(archive, "w:gz") as writer:
                    row = tarfile.TarInfo(name)
                    row.type = kind
                    if kind == tarfile.REGTYPE:
                        row.size = 5
                        row.mode = 0o755
                        writer.addfile(row, io.BytesIO(b"hello"))
                    else:
                        row.linkname = "../../escaped"
                        writer.addfile(row)
                if safe:
                    package = _module.extract_bootstrap(archive, root / "safe")
                    self.assertEqual(
                        (package / "scripts/nvx.py").read_bytes(), b"hello"
                    )
                else:
                    with self.assertRaises(ValueError):
                        _module.extract_bootstrap(archive, root / "unsafe")
                    self.assertFalse((root / "unsafe").exists())


if __name__ == "__main__":
    unittest.main()
