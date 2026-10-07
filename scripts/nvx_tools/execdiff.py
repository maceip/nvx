"""Compare executable ELF images inside snapshot RAM.

Module identity is the SHA-256 of the executable ``PT_LOAD`` bytes. A
selected image then yields the dynamic functions mapped into that segment
and the basic-block graph of one function. Capstone decodes the
instructions. Matching of changed functions across two builds belongs to
BinDiff or Ghidra Version Tracking; identical executable bytes are already
a similarity of 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nvx_tools.common import ScriptError
from nvx_tools.disasm import Instruction, disassemble

_ELF_MAGIC = b"\x7fELF"
_PT_LOAD = 1
_PT_DYNAMIC = 2
_PF_X = 1
_DT_NULL = 0
_DT_HASH = 4
_DT_STRTAB = 5
_DT_SYMTAB = 6
_DT_STRSZ = 10
_DT_SYMENT = 11
_DT_SONAME = 14
_STT_FUNC = 2
_SHT_DYNSYM = 11
_MAX_FUNCTION_BYTES = 16 * 1024
_MAX_IMAGE_BYTES = 96 * 1024 * 1024


@dataclass(frozen=True)
class ExecModule:
    """One unique executable image found in a memory file."""

    digest: str
    gpa: int
    exec_bytes: int
    entry: int
    name: str
    span: int


@dataclass(frozen=True)
class ExecFunction:
    """One named or entry function inside an executable image."""

    address: int
    size: int
    name: str


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def _i64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "little", signed=True)


def _u64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "little")


def _cstring(data: bytes, offset: int) -> str:
    end = data.find(b"\x00", offset)
    if end < 0:
        end = min(len(data), offset + 512)
    return data[offset:end].decode("utf-8", "replace")


@dataclass(frozen=True)
class _Load:
    offset: int
    vaddr: int
    filesz: int
    flags: int


def _loads(elf_base: int, memory: bytes) -> tuple[list[_Load], int, int]:
    """Return load segments, the ELF entry, and the in-file span."""
    if (
        elf_base < 0
        or elf_base + 64 > len(memory)
        or memory[elf_base : elf_base + 4] != _ELF_MAGIC
    ):
        raise ScriptError("not an ELF")
    if (
        memory[elf_base + 4] != 2
        or memory[elf_base + 5] != 1
        or _u16(memory, elf_base + 18) != 183
    ):
        raise ScriptError("not AArch64 ELF64")
    if _u16(memory, elf_base + 16) not in (2, 3) or _u16(memory, elf_base + 54) != 56:
        raise ScriptError("unexpected ELF header")
    phnum = _u16(memory, elf_base + 56)
    if not 0 < phnum <= 64:
        raise ScriptError("invalid program headers")
    phoff = _u64(memory, elf_base + 32)
    entry = _u64(memory, elf_base + 24)
    loads: list[_Load] = []
    span = 64
    dynamic: tuple[int, int] | None = None
    for index in range(phnum):
        header = elf_base + phoff + index * 56
        if header + 56 > len(memory):
            raise ScriptError("truncated program header")
        p_type = _u32(memory, header)
        p_flags = _u32(memory, header + 4)
        p_offset = _u64(memory, header + 8)
        p_vaddr = _u64(memory, header + 16)
        p_filesz = _u64(memory, header + 32)
        if p_offset > _MAX_IMAGE_BYTES or p_filesz > _MAX_IMAGE_BYTES:
            raise ScriptError("program header is out of range")
        span = max(span, p_offset + p_filesz)
        if span > _MAX_IMAGE_BYTES:
            raise ScriptError("executable image is too large")
        if p_type == _PT_LOAD and p_filesz:
            loads.append(_Load(p_offset, p_vaddr, p_filesz, p_flags))
        elif p_type == _PT_DYNAMIC and p_filesz:
            dynamic = (p_offset, p_filesz)
    if not any(load.flags & _PF_X for load in loads):
        raise ScriptError("no executable segment")
    return loads, entry, span if dynamic is None else max(span, dynamic[0] + dynamic[1])


def _va_to_offset(loads: list[_Load], vaddr: int) -> int | None:
    for load in loads:
        if load.vaddr <= vaddr < load.vaddr + load.filesz:
            return load.offset + (vaddr - load.vaddr)
    return None


def _dynamic(memory: bytes, elf_base: int, loads: list[_Load]) -> dict[int, int]:
    phoff = _u64(memory, elf_base + 32)
    phnum = _u16(memory, elf_base + 56)
    tags: dict[int, int] = {}
    for index in range(phnum):
        header = elf_base + phoff + index * 56
        if _u32(memory, header) != _PT_DYNAMIC:
            continue
        offset = _u64(memory, header + 8)
        filesz = _u64(memory, header + 32)
        cursor = elf_base + offset
        end = cursor + filesz
        if end > len(memory):
            return {}
        while cursor + 16 <= end:
            tag = _i64(memory, cursor)
            value = _u64(memory, cursor + 8)
            cursor += 16
            if tag == _DT_NULL:
                break
            tags[tag] = value
        break
    for tag in (_DT_STRTAB, _DT_SYMTAB, _DT_HASH):
        if tag not in tags:
            continue
        mapped = _va_to_offset(loads, tags[tag])
        if mapped is None:
            del tags[tag]
        else:
            tags[tag] = mapped
    return tags


def _soname(
    memory: bytes, elf_base: int, loads: list[_Load], tags: dict[int, int]
) -> str:
    if _DT_SONAME not in tags or _DT_STRTAB not in tags:
        return ""
    start = elf_base + tags[_DT_STRTAB] + tags[_DT_SONAME]
    if start >= len(memory):
        return ""
    return _cstring(memory, start)


def _symbol_count(memory: bytes, elf_base: int, tags: dict[int, int]) -> int:
    if _DT_HASH not in tags:
        return 0
    offset = elf_base + tags[_DT_HASH]
    if offset + 8 > len(memory):
        return 0
    return _u32(memory, offset + 4)


def _functions_from_dynamic(
    memory: bytes, elf_base: int, loads: list[_Load], entry: int
) -> list[ExecFunction]:
    tags = _dynamic(memory, elf_base, loads)
    count = _symbol_count(memory, elf_base, tags)
    if count <= 0 or _DT_SYMTAB not in tags or _DT_STRTAB not in tags:
        count = _dynsym_count_from_sections(memory, elf_base)
        if count <= 0:
            return [ExecFunction(entry, 0, "entry")] if entry else []
        if _DT_SYMTAB not in tags or _DT_STRTAB not in tags:
            return [ExecFunction(entry, 0, "entry")] if entry else []
    syment = tags.get(_DT_SYMENT, 24)
    if syment < 24:
        syment = 24
    functions: dict[int, ExecFunction] = {}
    sym = elf_base + tags[_DT_SYMTAB]
    strings = elf_base + tags[_DT_STRTAB]
    exec_ranges = [
        (load.vaddr, load.vaddr + load.filesz) for load in loads if load.flags & _PF_X
    ]
    for index in range(count):
        offset = sym + index * syment
        if offset + syment > len(memory):
            break
        info = memory[offset + 4]
        if info & 0xF != _STT_FUNC:
            continue
        value = _u64(memory, offset + 8)
        size = _u64(memory, offset + 16)
        if value == 0 or not any(start <= value < end for start, end in exec_ranges):
            continue
        name = _cstring(memory, strings + _u32(memory, offset))
        current = functions.get(value)
        if current is None or (current.name == "entry" and name):
            functions[value] = ExecFunction(value, size, name or "entry")
    if entry and entry not in functions:
        functions[entry] = ExecFunction(entry, 0, "entry")
    ordered = sorted(functions.values(), key=lambda item: item.address)
    sized: list[ExecFunction] = []
    for index, function in enumerate(ordered):
        if function.size:
            sized.append(function)
            continue
        nxt = (
            ordered[index + 1].address if index + 1 < len(ordered) else function.address
        )
        sized.append(
            ExecFunction(
                function.address, max(0, nxt - function.address), function.name
            )
        )
    return sized


def _dynsym_count_from_sections(memory: bytes, elf_base: int) -> int:
    shoff = _u64(memory, elf_base + 40)
    shentsize = _u16(memory, elf_base + 58)
    shnum = _u16(memory, elf_base + 60)
    if shentsize != 64 or not 0 < shnum <= 128:
        return 0
    table = elf_base + shoff
    if table < elf_base or table + shnum * 64 > len(memory):
        return 0
    for index in range(shnum):
        offset = table + index * 64
        if _u32(memory, offset + 4) != _SHT_DYNSYM:
            continue
        size = _u64(memory, offset + 32)
        entsize = _u64(memory, offset + 56) or 24
        return size // entsize
    return 0


def carve_executables(memory: bytes) -> list[ExecModule]:
    """Find unique AArch64 executable images in a guest RAM file."""
    modules: dict[str, ExecModule] = {}
    covered: list[tuple[int, int]] = []
    cursor = 0
    while True:
        found = memory.find(_ELF_MAGIC, cursor)
        if found < 0:
            break
        cursor = found + 4
        if any(start <= found < end for start, end in covered):
            continue
        try:
            loads, entry, span = _loads(found, memory)
        except ScriptError:
            continue
        exec_parts: list[bytes] = []
        for load in loads:
            if load.flags & _PF_X == 0:
                continue
            start = found + load.offset
            end = start + load.filesz
            if end > len(memory):
                exec_parts = []
                break
            exec_parts.append(memory[start:end])
        if not exec_parts:
            continue
        blob = b"".join(exec_parts)
        digest = hashlib.sha256(blob).hexdigest()
        covered.append((found, found + span))
        tags = _dynamic(memory, found, loads)
        name = _soname(memory, found, loads, tags)
        module = ExecModule(digest, found, len(blob), entry, name, span)
        current = modules.get(digest)
        if current is None or found < current.gpa:
            modules[digest] = module
    return sorted(modules.values(), key=lambda item: item.exec_bytes, reverse=True)


def compare_modules(
    primary: list[ExecModule], secondary: list[ExecModule]
) -> list[dict[str, Any]]:
    """Match executable images by the hash of their executable bytes."""
    left = {item.digest: item for item in primary}
    right = {item.digest: item for item in secondary}
    rows: list[dict[str, Any]] = []
    for digest in left.keys() | right.keys():
        first = left.get(digest)
        second = right.get(digest)
        if first and second:
            status = "identical"
            similarity = 1.0
        elif first:
            status = "primary"
            similarity = 0.0
        else:
            status = "secondary"
            similarity = 0.0
        chosen = second or first
        assert chosen is not None
        name = chosen.name or (second.name if second and second.name else "")
        if not name and first and first.name:
            name = first.name
        if not name:
            name = f"{chosen.exec_bytes // 1024} KiB"
        rows.append(
            {
                "digest": digest,
                "exec_bytes": chosen.exec_bytes,
                "status": status,
                "similarity": similarity,
                "name": name,
                "entry": chosen.entry,
                "primary_gpa": first.gpa if first else None,
                "secondary_gpa": second.gpa if second else None,
            }
        )
    rows.sort(key=lambda row: (row["status"] == "identical", -row["exec_bytes"]))
    return rows


def _branch_target(op_str: str) -> int | None:
    marker = op_str.rfind("#")
    if marker < 0:
        return None
    token = op_str[marker + 1 :].split(",")[0].strip()
    try:
        return int(token, 0)
    except ValueError:
        return None


def _flow(mnemonic: str) -> str:
    if mnemonic in {"bl", "blr"}:
        return "call"
    if mnemonic in {"ret", "retab", "retaa", "br"} or mnemonic == "b":
        return "jump"
    if mnemonic.startswith("b.") or mnemonic in {"cbz", "cbnz", "tbz", "tbnz"}:
        return "cond"
    return "next"


def function_graph(
    memory: bytes, gpa: int, address: int, functions: list[ExecFunction]
) -> dict[str, Any]:
    """Basic blocks and direct calls for one function in a carved image."""
    chosen = next((item for item in functions if item.address == address), None)
    if chosen is None:
        raise ScriptError(f"function {address:#x} is not in this executable image")
    size = chosen.size if 0 < chosen.size <= _MAX_FUNCTION_BYTES else 64
    loads, _, _ = _loads(gpa, memory)
    file_off = _va_to_offset(loads, address)
    if file_off is None:
        raise ScriptError(f"function {address:#x} is outside the executable segment")
    start = gpa + file_off
    code = memory[start : start + size]
    if len(code) < 4:
        raise ScriptError("function has no executable bytes")
    code = code[: len(code) - (len(code) % 4)]
    instructions = disassemble(code, address)
    end = address + len(code)
    names = {item.address: item.name for item in functions}
    leaders = {address, end}
    for insn in instructions:
        kind = _flow(insn.mnemonic)
        nxt = insn.address + insn.size
        target = _branch_target(insn.op_str)
        if kind == "jump":
            leaders.add(nxt)
            if target is not None and address <= target < end:
                leaders.add(target)
        elif kind == "cond":
            leaders.add(nxt)
            if target is not None and address <= target < end:
                leaders.add(target)
        elif kind == "call":
            leaders.add(nxt)
    ordered = sorted(leaders)
    by_addr = {insn.address: insn for insn in instructions}
    blocks: list[dict[str, Any]] = []
    calls: dict[int, str] = {}
    for index, leader in enumerate(ordered[:-1]):
        rows: list[Instruction] = []
        cursor = leader
        stop = ordered[index + 1]
        while cursor in by_addr and cursor < stop:
            insn = by_addr[cursor]
            rows.append(insn)
            cursor += insn.size
        if not rows:
            continue
        last = rows[-1]
        kind = _flow(last.mnemonic)
        target = _branch_target(last.op_str)
        successors: list[int] = []
        if kind == "cond":
            if target is not None and address <= target < end:
                successors.append(target)
            fall = last.address + last.size
            if fall < end:
                successors.append(fall)
        elif kind == "jump":
            if target is not None and address <= target < end:
                successors.append(target)
        elif kind == "call":
            if target is not None:
                calls[target] = names.get(target, f"{target:#x}")
            fall = last.address + last.size
            if fall < end:
                successors.append(fall)
        elif kind == "next":
            fall = last.address + last.size
            if fall < end:
                successors.append(fall)
        blocks.append(
            {
                "address": leader,
                "successors": successors,
                "instructions": [
                    {
                        "address": insn.address,
                        "mnemonic": insn.mnemonic,
                        "op": insn.op_str,
                    }
                    for insn in rows
                ],
            }
        )
    return {
        "address": chosen.address,
        "name": chosen.name,
        "blocks": blocks,
        "calls": [
            {"address": dest, "name": label} for dest, label in sorted(calls.items())
        ],
    }


def _memory_file(snapshot: Path) -> bytes:
    path = snapshot / "memory.bin"
    if snapshot.is_file():
        path = snapshot
    if not path.is_file() or path.is_symlink():
        raise ScriptError(f"snapshot memory is missing: {path}")
    return path.read_bytes()


def command_modules(args: argparse.Namespace) -> None:
    primary = carve_executables(_memory_file(args.primary))
    secondary = carve_executables(_memory_file(args.secondary))
    print(
        json.dumps(
            {
                "primary": str(args.primary),
                "secondary": str(args.secondary),
                "modules": compare_modules(primary, secondary),
            }
        )
    )


def _module_at(memory: bytes, gpa: int) -> tuple[list[_Load], int, list[ExecFunction]]:
    loads, entry, _span = _loads(gpa, memory)
    return loads, entry, _functions_from_dynamic(memory, gpa, loads, entry)


def command_functions(args: argparse.Namespace) -> None:
    memory = _memory_file(args.snapshot)
    _loads_ignored, entry, functions = _module_at(memory, args.gpa)
    print(
        json.dumps(
            {
                "gpa": args.gpa,
                "entry": entry,
                "functions": [
                    {"address": item.address, "size": item.size, "name": item.name}
                    for item in functions
                ],
            }
        )
    )


def command_graph(args: argparse.Namespace) -> None:
    memory = _memory_file(args.snapshot)
    _loads_ignored, _entry, functions = _module_at(memory, args.gpa)
    print(json.dumps(function_graph(memory, args.gpa, args.address, functions)))


def configure_parser(parser: argparse.ArgumentParser) -> None:
    """Register ``nvx.py execdiff``."""
    subparsers = parser.add_subparsers(dest="execdiff_command", required=True)
    modules = subparsers.add_parser(
        "modules", help="match executable images in two snapshot memory files"
    )
    modules.add_argument("primary", type=Path)
    modules.add_argument("secondary", type=Path)
    modules.set_defaults(handler=command_modules)
    functions = subparsers.add_parser(
        "functions", help="list dynamic functions in one carved executable image"
    )
    functions.add_argument("snapshot", type=Path)
    functions.add_argument("--gpa", required=True, type=lambda value: int(value, 0))
    functions.set_defaults(handler=command_functions)
    graph = subparsers.add_parser(
        "graph", help="basic blocks and direct calls for one function"
    )
    graph.add_argument("snapshot", type=Path)
    graph.add_argument("--gpa", required=True, type=lambda value: int(value, 0))
    graph.add_argument("--address", required=True, type=lambda value: int(value, 0))
    graph.set_defaults(handler=command_graph)
