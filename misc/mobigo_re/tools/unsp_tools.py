from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VASM = ROOT / "build" / "vasm" / "vasmunsp_oldstyle.exe"


NO_OPERAND_WORDS = {
    0o115220: "retf",
    0o115230: "reti",
    0o170520: "break",
    0o170545: "nop",
}

SHORT_JUMPS = [
    ("jb", 0o007000),
    ("jcs", 0o017000),
    ("jsc", 0o027000),
    ("jss", 0o037000),
    ("jne", 0o047000),
    ("jz", 0o057000),
    ("jpl", 0o067000),
    ("jmi", 0o077000),
    ("jbe", 0o107000),
    ("ja", 0o117000),
    ("jle", 0o127000),
    ("jg", 0o137000),
    ("jvc", 0o147000),
    ("jvs", 0o157000),
    ("jmp", 0o167000),
]

LONG_TARGETS = [
    ("call", 0o170100),
    ("goto", 0o177200),
]

LJMP_IMM = 0o117417


@dataclass(frozen=True)
class DecodedInstruction:
    file_offset: int
    address: int
    size_words: int
    text: str


def _read_words(path: Path) -> list[int]:
    data = path.read_bytes()
    if len(data) % 2:
        raise SystemExit(f"{path} has an odd size; expected 16-bit aligned data")
    return [word for (word,) in struct.iter_unpack("<H", data)]


def _asm_text_from_args(args: argparse.Namespace) -> str:
    if args.asm_file:
        return Path(args.asm_file).read_text(encoding="ascii")
    if args.snippet:
        return f"        org 0\n{args.snippet.rstrip()}\n"
    raise SystemExit("provide either --asm-file or --snippet")


def assemble_unsp(asm_text: str, output_path: Path, vasm_path: Path = DEFAULT_VASM) -> None:
    if not vasm_path.exists():
        raise SystemExit(f"assembler not found: {vasm_path}")

    with tempfile.TemporaryDirectory() as td:
        asm_path = Path(td) / "snippet.asm"
        asm_path.write_text(asm_text, encoding="ascii")
        cmd = [
            str(vasm_path),
            "-Fbin",
            "-ole",
            "-o",
            str(output_path),
            str(asm_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SystemExit(result.stdout + result.stderr)


def search_pattern(blob: bytes, needle: bytes) -> list[int]:
    hits: list[int] = []
    start = 0
    while True:
        idx = blob.find(needle, start)
        if idx == -1:
            return hits
        hits.append(idx)
        start = idx + 1


def decode_control_flow(words: list[int], *, start_word: int, count_words: int | None, base_address: int) -> list[DecodedInstruction]:
    out: list[DecodedInstruction] = []
    limit = len(words) if count_words is None else min(len(words), start_word + count_words)
    idx = start_word
    while idx < limit:
        word = words[idx]
        file_offset = idx * 2
        address = base_address + idx

        if word in NO_OPERAND_WORDS:
            out.append(DecodedInstruction(file_offset, address, 1, NO_OPERAND_WORDS[word]))
            idx += 1
            continue

        if word == LJMP_IMM and idx + 1 < limit:
            target = words[idx + 1]
            out.append(DecodedInstruction(file_offset, address, 2, f"ljmp 0x{target:04x}"))
            idx += 2
            continue

        matched = False
        for name, opcode in LONG_TARGETS:
            if (word & 0xFFC0) == opcode and idx + 1 < limit:
                target = ((word & 0x003F) << 16) | words[idx + 1]
                out.append(DecodedInstruction(file_offset, address, 2, f"{name} 0x{target:06x}"))
                idx += 2
                matched = True
                break
        if matched:
            continue

        for name, opcode in SHORT_JUMPS:
            if (word & 0xFF80) == opcode:
                raw = word & 0x007F
                disp = -(raw & 0x003F) if (raw & 0x0040) else (raw & 0x003F)
                target = address + 1 + disp
                out.append(DecodedInstruction(file_offset, address, 1, f"{name} 0x{target:06x}"))
                matched = True
                break
        idx += 1
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Small unSP helper around vasm and targeted control-flow decoding.")
    sub = parser.add_subparsers(dest="command", required=True)

    asm_p = sub.add_parser("asm", help="Assemble a snippet or source file into a raw little-endian binary.")
    asm_p.add_argument("output")
    asm_src = asm_p.add_mutually_exclusive_group(required=True)
    asm_src.add_argument("--asm-file")
    asm_src.add_argument("--snippet")
    asm_p.add_argument("--vasm", default=str(DEFAULT_VASM))
    asm_p.add_argument("--print-hex", action="store_true")

    search_p = sub.add_parser("search", help="Assemble a snippet and search for its byte pattern in a binary.")
    search_p.add_argument("binary")
    search_src = search_p.add_mutually_exclusive_group(required=True)
    search_src.add_argument("--asm-file")
    search_src.add_argument("--snippet")
    search_p.add_argument("--vasm", default=str(DEFAULT_VASM))

    flow_p = sub.add_parser("scan-flow", help="Scan a raw unSP binary for direct control-flow instructions.")
    flow_p.add_argument("binary")
    flow_p.add_argument("--start-word", type=lambda x: int(x, 0), default=0)
    flow_p.add_argument("--count-words", type=lambda x: int(x, 0))
    flow_p.add_argument("--base-address", type=lambda x: int(x, 0), default=0)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "asm":
        asm_text = _asm_text_from_args(args)
        output_path = Path(args.output)
        assemble_unsp(asm_text, output_path, Path(args.vasm))
        if args.print_hex:
            print(output_path.read_bytes().hex())
        else:
            print(f"wrote {output_path}")
        return 0

    if args.command == "search":
        asm_text = _asm_text_from_args(args)
        with tempfile.TemporaryDirectory() as td:
            tmp_out = Path(td) / "snippet.bin"
            assemble_unsp(asm_text, tmp_out, Path(args.vasm))
            needle = tmp_out.read_bytes()
        blob = Path(args.binary).read_bytes()
        hits = search_pattern(blob, needle)
        print(f"pattern={needle.hex()}")
        if not hits:
            print("no matches")
            return 1
        for hit in hits:
            print(f"0x{hit:x}")
        return 0

    if args.command == "scan-flow":
        words = _read_words(Path(args.binary))
        decoded = decode_control_flow(
            words,
            start_word=args.start_word,
            count_words=args.count_words,
            base_address=args.base_address,
        )
        for insn in decoded:
            print(f"off=0x{insn.file_offset:06x} addr=0x{insn.address:06x} {insn.text}")
        return 0

    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
