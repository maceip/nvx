"""AArch64 executable-segment disassembly.

Capstone is the packaged decoder. Recovering instructions from ``PF_X``
bytes is the bar in front of the open-source diff engines: Google
BinExport and BinDiff consume a disassembly database, and Ghidra Version
Tracking matches the same functions and basic blocks. This module does
not match functions. It turns an executable ELF segment, including one
carved from ``memory.bin``, into instructions at their virtual addresses.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, cast

from nvx_tools.common import ScriptError

_ELF_MAGIC = b"\x7fELF"
_ELF64 = 2
_ELF_LE = 1
_EM_AARCH64 = 183
_ET_EXEC = 2
_ET_DYN = 3
_PT_LOAD = 1
_PF_X = 1
_PHDR_SIZE = 56


@dataclass(frozen=True)
class ExecutableSegment:
    """One executable ``PT_LOAD`` segment."""

    vaddr: int
    data: bytes


@dataclass(frozen=True)
class Instruction:
    """One decoded AArch64 instruction."""

    address: int
    size: int
    mnemonic: str
    op_str: str


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "little")


def executable_segments(elf: bytes) -> list[ExecutableSegment]:
    """Return the executable ``PT_LOAD`` segments of one little-endian ELF64."""
    if len(elf) < 64 or elf[:4] != _ELF_MAGIC:
        raise ScriptError("executable image is not an ELF")
    if elf[4] != _ELF64 or elf[5] != _ELF_LE:
        raise ScriptError("executable image is not little-endian ELF64")
    if _u16(elf, 18) != _EM_AARCH64:
        raise ScriptError("executable image is not AArch64")
    if _u16(elf, 16) not in (_ET_EXEC, _ET_DYN):
        raise ScriptError("executable image is not an executable or shared object")
    if _u16(elf, 54) != _PHDR_SIZE:
        raise ScriptError("executable image has an unexpected program-header size")
    phnum = _u16(elf, 56)
    if not 0 < phnum <= 64:
        raise ScriptError("executable image has an invalid program-header count")
    phoff = _u64(elf, 32)
    segments: list[ExecutableSegment] = []
    for index in range(phnum):
        header = phoff + index * _PHDR_SIZE
        if header + _PHDR_SIZE > len(elf):
            raise ScriptError("executable image program header is truncated")
        if _u32(elf, header) != _PT_LOAD or _u32(elf, header + 4) & _PF_X == 0:
            continue
        offset = _u64(elf, header + 8)
        vaddr = _u64(elf, header + 16)
        filesz = _u64(elf, header + 32)
        if filesz == 0:
            continue
        end = offset + filesz
        if end > len(elf):
            raise ScriptError("executable segment extends past the image")
        segments.append(ExecutableSegment(vaddr, elf[offset:end]))
    if not segments:
        raise ScriptError("executable image has no executable segment")
    return segments


def disassemble(code: bytes, address: int) -> list[Instruction]:
    """Decode AArch64 instructions with the packaged Capstone engine."""
    if len(code) % 4 != 0:
        raise ScriptError("AArch64 executable bytes must be a multiple of 4")
    try:
        module = cast(Any, importlib.import_module("capstone"))
    except ImportError as err:
        raise ScriptError(
            "Capstone is not installed; pip install -r requirements-dev.txt"
        ) from err
    engine = module.Cs(module.CS_ARCH_ARM64, module.CS_MODE_ARM)
    rows: list[Instruction] = []
    for insn in engine.disasm(code, address):
        rows.append(
            Instruction(
                address=int(insn.address),
                size=int(insn.size),
                mnemonic=str(insn.mnemonic),
                op_str=str(insn.op_str),
            )
        )
    if len(rows) != len(code) // 4:
        raise ScriptError("Capstone did not decode every AArch64 instruction")
    return rows
