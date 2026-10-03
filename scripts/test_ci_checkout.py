"""Checkout the declared fork at the exact gitlink, never a branch tip."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from nvx_tools.ci import github_submodule_repository
from nvx_tools.common import ScriptError


class CheckoutTests(unittest.TestCase):
    def test_github_url_forms(self) -> None:
        for prefix in (
            "https://github.com/",
            "git@github.com:",
            "ssh://git@github.com/",
        ):
            for suffix in ("", ".git"):
                self.assertEqual(
                    github_submodule_repository(prefix + "maceip/openvmm" + suffix),
                    "maceip/openvmm",
                )
        for url in (
            "https://token@github.com/owner/repo.git",
            "https://other.example/owner/repo.git",
            "https://github.com/owner/repo.git?token=private",
            "https://github.com/owner/repo/extra",
            "https://github.com/owner/..",
        ):
            with self.assertRaises(ScriptError) as caught:
                github_submodule_repository(url)
            self.assertNotIn(url, str(caught.exception))

    def test_resolver_uses_index_and_declared_fork(self) -> None:
        resolver = (
            Path(__file__).resolve().parents[1]
            / ".github/actions/checkout-openvmm/resolve.py"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitmodules").write_text(
                '[submodule "openvmm"]\npath = openvmm\n'
                "url = https://github.com/example-fork/openvmm.git\nbranch = newer-tip\n"
            )
            revision = "0123456789abcdef" * 2 + "01234567"
            subprocess.run(
                [
                    "git",
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"160000,{revision},openvmm",
                ],
                cwd=root,
                check=True,
            )
            output = root / "outputs"
            subprocess.run(
                [sys.executable, str(resolver)],
                cwd=root,
                env={**os.environ, "GITHUB_OUTPUT": str(output)},
                check=True,
            )
            self.assertEqual(
                output.read_text(), f"sha={revision}\nrepository=example-fork/openvmm\n"
            )


if __name__ == "__main__":
    unittest.main()
