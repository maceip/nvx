import argparse
import io
import json
import struct
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from nvx_tools.execdiff import (
    ExecFunction,
    carve_executables,
    command_modules,
    compare_modules,
    function_graph,
)


def _image(code: bytes, entry: int = 0x1000) -> bytes:
    phoff = 64
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    struct.pack_into("<H", header, 16, 3)
    struct.pack_into("<H", header, 18, 183)
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<Q", header, 24, entry)
    struct.pack_into("<Q", header, 32, phoff)
    struct.pack_into("<H", header, 52, 64)
    struct.pack_into("<H", header, 54, 56)
    struct.pack_into("<H", header, 56, 1)
    image = bytearray(header)
    image.extend(bytes(phoff + 56 - len(image)))
    code_off = len(image)
    image.extend(code)
    struct.pack_into(
        "<IIQQQQQQ",
        image,
        phoff,
        1,
        5,
        code_off,
        entry,
        entry,
        len(code),
        len(code),
        4,
    )
    return bytes(image)


class ExecDiffTests(unittest.TestCase):
    def test_identical_executable_bytes_match_across_addresses(self) -> None:
        code = bytes.fromhex("1f2003d5c0035fd6")
        left = carve_executables(b"\x00" * 16 + _image(code))
        right = carve_executables(b"\x00" * 64 + _image(code))
        rows = compare_modules(left, right)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "identical")
        self.assertEqual(rows[0]["similarity"], 1.0)
        self.assertNotEqual(left[0].gpa, right[0].gpa)

    def test_added_executable_is_secondary(self) -> None:
        shared = _image(bytes.fromhex("c0035fd6"))
        extra = _image(bytes.fromhex("1f2003d5c0035fd6"), entry=0x2000)
        rows = compare_modules(
            carve_executables(shared),
            carve_executables(shared + extra),
        )
        self.assertEqual(
            sorted(row["status"] for row in rows), ["identical", "secondary"]
        )

    def test_function_graph_splits_a_branch(self) -> None:
        # nop; b +8; nop; ret  — the branch skips one nop into ret.
        # b encoding: imm26 = 2 (8 bytes / 4), opcode 0x14000000.
        code = bytes.fromhex("1f2003d5020000141f2003d5c0035fd6")
        memory = _image(code)
        graph = function_graph(
            memory,
            0,
            0x1000,
            [ExecFunction(0x1000, len(code), "entry")],
        )
        self.assertGreaterEqual(len(graph["blocks"]), 2)
        self.assertEqual(graph["blocks"][0]["instructions"][0]["mnemonic"], "nop")
        self.assertIn(0x100C, graph["blocks"][0]["successors"])

    def test_modules_command_writes_json(self) -> None:
        image = _image(bytes.fromhex("c0035fd6"))
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("primary", "secondary"):
                folder = root / name
                folder.mkdir()
                (folder / "memory.bin").write_bytes(image)
            args = argparse.Namespace(
                primary=root / "primary", secondary=root / "secondary"
            )
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                command_modules(args)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["modules"][0]["status"], "identical")


if __name__ == "__main__":
    unittest.main()
