"""Checksummed source archives preserve internal aliases and reject escapes."""

import tempfile
import unittest
from pathlib import Path

from nvx_tools.collect_alpine_sources import SourceError, materialize_recipe_links
from nvx_tools.common import verify_sha256_sums, write_sha256_sums


class SourceLinkTests(unittest.TestCase):
    def test_internal_alias_bytes_mode_and_reconstruction_survive_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = root / "recipes/commit/main/package"
            recipe.mkdir(parents=True)
            script = recipe / "post-install"
            script.write_bytes(b"#!/bin/sh\nexit 0\n")
            script.chmod(0o755)
            alias = recipe / "post-upgrade"
            alias.symlink_to("post-install")
            links = materialize_recipe_links(root, {})
            self.assertFalse(alias.is_symlink())
            self.assertEqual(alias.read_bytes(), script.read_bytes())
            self.assertEqual(alias.stat().st_mode, script.stat().st_mode)
            self.assertEqual(
                links, {"recipes/commit/main/package/post-upgrade": "post-install"}
            )
            self.assertEqual(materialize_recipe_links(root, links), links)
            write_sha256_sums(root)
            verify_sha256_sums(root)

    def test_external_alias_cannot_enter_a_source_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "recipes").mkdir()
            (root / "outside").write_bytes(b"foreign file")
            (root / "recipes/escape").symlink_to("../outside")
            with self.assertRaises(SourceError):
                materialize_recipe_links(root, {})


if __name__ == "__main__":
    unittest.main()
