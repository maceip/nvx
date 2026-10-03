import argparse
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from nvx_tools.common import ScriptError
from nvx_tools.proxy_service import read_configuration
from nvx_tools.secrets import bindings, environment


class SecretTests(unittest.TestCase):
    def test_maximum_bindings_survive_json_expansion_and_oversize_is_rejected(
        self,
    ) -> None:
        values = ["\xff" * 4096] * 4
        raw = json.dumps({"bindings": values}).encode() + b"\n"
        self.assertGreater(len(raw), 65536)
        self.assertEqual(read_configuration(io.BytesIO(raw))["bindings"], values)
        with self.assertRaisesRegex(ScriptError, "size limit"):
            read_configuration(io.BytesIO(b"x" * ((256 << 10) + 1)))

    def test_exact_scopes_named_environment_and_header_conflicts(self) -> None:
        args = argparse.Namespace(
            secret=["NVX_FIXTURE_KEY"],
            network_egress_allow=["example.com:443"],
            secret_header=[],
        )
        with patch.dict(os.environ, {"NVX_FIXTURE_KEY": "host-only-fixture"}):
            scopes, selected = bindings(args)
            self.assertEqual(scopes, frozenset({("example.com", 443)}))
            self.assertEqual(selected[0].value, "host-only-fixture")
            self.assertNotIn("host-only-fixture", repr(selected))
            args.network_egress_allow.append("evil.example:443")
            with self.assertRaisesRegex(ScriptError, "multiple scopes"):
                bindings(args)
            args.secret = ["NVX_FIXTURE_KEY@example.com:443"]
            self.assertEqual(bindings(args)[1][0].host, "example.com")
            args.secret.append("NVX_FIXTURE_KEY@example.com:443")
            with self.assertRaisesRegex(ScriptError, "same scope/header"):
                bindings(args)
        args.secret = ["NVX_DELIBERATELY_MISSING_KEY@example.com:443"]
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ScriptError, "missing"),
        ):
            bindings(args)

    def test_guest_configuration_contains_only_disposable_proxy_capability(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            self.assertEqual(environment(state), ())
            (state / "proxy-runtime.json").write_text('{"port":12345}')
            (state / "config.json").write_text('{"net":"192.168.127.0/24"}')
            (state / "proxy.capability").write_text("disposable")
            self.assertEqual(
                environment(state),
                (
                    "NVX_PROXY_URL=http://192.168.127.1:12345",
                    "NVX_PROXY_CAPABILITY=disposable",
                ),
            )
