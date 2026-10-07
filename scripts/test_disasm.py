import struct
import unittest

from nvx_tools.common import ScriptError
from nvx_tools.disasm import disassemble, executable_segments


def _elf(exec_vaddr: int, exec_bytes: bytes, data_bytes: bytes = b"") -> bytes:
    """Build a minimal AArch64 PIE with one executable LOAD and optional data."""
    phnum = 2 if data_bytes else 1
    phoff = 64
    header = bytearray(64)
    header[0:4] = b"\x7fELF"
    header[4] = 2
    header[5] = 1
    struct.pack_into("<H", header, 16, 3)
    struct.pack_into("<H", header, 18, 183)
    struct.pack_into("<I", header, 20, 1)
    struct.pack_into("<Q", header, 32, phoff)
    struct.pack_into("<H", header, 52, 64)
    struct.pack_into("<H", header, 54, 56)
    struct.pack_into("<H", header, 56, phnum)
    image = bytearray(header)
    image.extend(bytes(phoff + phnum * 56 - len(image)))
    exec_off = len(image)
    image.extend(exec_bytes)
    data_off = len(image)
    image.extend(data_bytes)

    def phdr(index: int, flags: int, offset: int, vaddr: int, filesz: int) -> None:
        at = phoff + index * 56
        struct.pack_into(
            "<IIQQQQQQ", image, at, 1, flags, offset, vaddr, vaddr, filesz, filesz, 4
        )

    phdr(0, 5, exec_off, exec_vaddr, len(exec_bytes))
    if data_bytes:
        phdr(1, 6, data_off, exec_vaddr + 0x10000, len(data_bytes))
    return bytes(image)


class DisasmTests(unittest.TestCase):
    def test_decodes_fixed_aarch64_words(self) -> None:
        # nop; ret
        rows = disassemble(bytes.fromhex("1f2003d5c0035fd6"), 0x1000)
        self.assertEqual(
            [(row.address, row.mnemonic, row.op_str) for row in rows],
            [
                (0x1000, "nop", ""),
                (0x1004, "ret", ""),
            ],
        )

    def test_executable_segment_keeps_only_pf_x(self) -> None:
        code = bytes.fromhex("c0035fd6")
        image = _elf(0x4000, code, b"not code")
        segments = executable_segments(image)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].vaddr, 0x4000)
        self.assertEqual(segments[0].data, code)
        self.assertEqual(
            disassemble(segments[0].data, segments[0].vaddr)[0].mnemonic, "ret"
        )

    def test_rejects_a_non_aarch64_image(self) -> None:
        image = bytearray(_elf(0x1000, b"\x00\x00\x00\x00"))
        struct.pack_into("<H", image, 18, 62)
        with self.assertRaisesRegex(ScriptError, "AArch64"):
            executable_segments(bytes(image))
