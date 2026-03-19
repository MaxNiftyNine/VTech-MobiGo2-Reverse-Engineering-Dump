from __future__ import annotations

import argparse
import struct
from pathlib import Path

from mba_tool import MAGIC_GBMQA, crc16_ccitt_word_le


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts" / "live" / "DEFAULT_UB_before_patch_20260314.MBA"
DEFAULT_OUTPUT = ROOT / "artifacts" / "poc" / "UB_usb_shift_poc.MBA"

BRANCH_A_X_OFFSET = 0x1710
BRANCH_A_Y_OFFSET = 0x171A
BRANCH_B_X_OFFSET = 0x174C
BRANCH_B_Y_OFFSET = 0x1756


def _parse_int(text: str) -> int:
    return int(text, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a stable USB-screen PoC by shifting the original UB.MBA widget position."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Source UB.MBA")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Patched UB.MBA output path")
    parser.add_argument("--x", type=_parse_int, default=0x0020, help="Replacement X coordinate")
    parser.add_argument("--y", type=_parse_int, default=0x0080, help="Replacement Y coordinate")
    return parser


def compute_header_crc(blob: bytearray) -> int:
    blob[0x3C:0x3E] = b"\x00\x00"
    crc = crc16_ccitt_word_le(blob[:0x3C], 0xFFFF)
    struct.pack_into("<H", blob, 0x3C, crc)
    return crc


def _store_u16(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", blob, offset, value & 0xFFFF)


def main() -> int:
    args = build_parser().parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    blob = bytearray(input_path.read_bytes())

    if blob[:8] != MAGIC_GBMQA:
        raise SystemExit("expected a bM_gbMQa UB.MBA input")

    _store_u16(blob, BRANCH_A_X_OFFSET, args.x)
    _store_u16(blob, BRANCH_A_Y_OFFSET, args.y)
    _store_u16(blob, BRANCH_B_X_OFFSET, args.x)
    _store_u16(blob, BRANCH_B_Y_OFFSET, args.y)

    header_crc = compute_header_crc(blob)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)

    print(f"wrote {output_path}")
    print(f"x=0x{args.x & 0xFFFF:04x} y=0x{args.y & 0xFFFF:04x}")
    print(f"header_crc=0x{header_crc:04x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
