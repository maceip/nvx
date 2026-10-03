import copy
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from nvx_tools.common import ScriptError, sha256_file
from nvx_tools.receipt import parse_flows, publish, seal, sign, verify

GOLDEN = Path(__file__).parent / "testdata/receipt-v1.json"


class ReceiptTests(unittest.TestCase):
    def test_golden_v1_and_version_rules(self) -> None:
        document = verify(GOLDEN)
        self.assertEqual(document, seal(document["body"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "receipt.json"
            document["receipt_version"] = 2
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ScriptError, "version"):
                verify(path)
        body = copy.deepcopy(document["body"])
        body["new_field"] = 1
        with self.assertRaisesRegex(ScriptError, "new receipt version"):
            seal(body)

    def test_tamper_receipt_and_evidence_and_no_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "events.jsonl"
            evidence.write_bytes(b"abc\n")
            body = json.loads(GOLDEN.read_bytes())["body"]
            body["evidence"] = {"events.jsonl": sha256_file(evidence)}
            path = root / "receipt.json"
            publish(path, seal(body))
            verify(path)
            with self.assertRaisesRegex(ScriptError, "already exists"):
                publish(path, seal(body))
            evidence.write_bytes(b"xbc\n")
            with self.assertRaisesRegex(ScriptError, "evidence"):
                verify(path)
            document = json.loads(path.read_bytes())
            document["body"]["argv"][1] = "jello"
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ScriptError, "digest mismatch"):
                verify(path)

    def test_real_flow_schema_and_aggregation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "flows"
            row: dict[str, Any] = dict(
                flow_version=1,
                sequence=1,
                time_ns=1,
                src="10.0.0.2",
                src_port=1234,
                dst="192.0.2.1",
                dst_port=443,
                proto="tcp",
                verdict="denied",
                bytes=40,
            )
            rows = [row, dict(row, sequence=2, time_ns=2)]
            path.write_text("".join(json.dumps(item) + "\n" for item in rows))
            parsed = parse_flows(path)
            self.assertTrue(parsed["complete"])
            self.assertEqual(parsed["flows"][0]["packets"], 2)
            self.assertEqual(parsed["flows"][0]["bytes"], 80)
            path.write_text(json.dumps(dict(row, sequence=2)) + "\n")
            with self.assertRaisesRegex(ScriptError, "ordering"):
                parse_flows(path)
            path.write_text(json.dumps({"flow_version": 1, "truncated": True}) + "\n")
            self.assertFalse(parse_flows(path)["complete"])
            path.write_text("x" * 4097)
            with self.assertRaisesRegex(ScriptError, "oversized"):
                parse_flows(path)

    def test_signature_requires_trusted_key_and_rejects_modified_signed_body(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key.pem"
            public = root / "public.pem"
            path = root / "receipt.json"
            private = Ed25519PrivateKey.generate()
            key.write_bytes(
                private.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            public.write_bytes(
                private.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            document = cast(dict[str, Any], json.loads(GOLDEN.read_bytes()))
            # Stock macOS LibreSSL and subprocess PATH cannot affect signatures.
            with patch("subprocess.run", side_effect=AssertionError("no subprocess")):
                sign(document, key)
                path.write_text(json.dumps(document))
                verify(path, public_key=public)
            with self.assertRaisesRegex(ScriptError, "trusted source"):
                verify(path)
            document["body"]["argv"][1] = "tampered"
            document["body_sha256"] = seal(document["body"])["body_sha256"]
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ScriptError, "verification failed"):
                verify(path, public_key=public)

    def test_wrong_key_and_malformed_signature_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = root / "key.pem"
            public = root / "wrong-public.pem"
            path = root / "receipt.json"
            first, second = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
            key.write_bytes(
                first.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            public.write_bytes(
                second.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
            document = cast(dict[str, Any], json.loads(GOLDEN.read_bytes()))
            sign(document, key)
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ScriptError, "verification failed"):
                verify(path, public_key=public)
            document["signature"]["value"] = (
                "YQ=="  # valid base64, invalid signature length
            )
            path.write_text(json.dumps(document))
            with self.assertRaisesRegex(ScriptError, "verification failed"):
                verify(path, public_key=public)
            key.write_text("not a PEM key")
            with self.assertRaisesRegex(ScriptError, "signing/verification failed"):
                sign(document, key)
