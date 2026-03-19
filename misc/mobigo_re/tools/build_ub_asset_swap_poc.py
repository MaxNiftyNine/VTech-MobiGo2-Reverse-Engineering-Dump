from __future__ import annotations

import argparse
import struct
from pathlib import Path

from mba_tool import MAGIC_GBMQA, crc16_ccitt_word_le


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts" / "live" / "DEFAULT_UB_before_patch_20260314.MBA"
DEFAULT_OUTPUT = ROOT / "artifacts" / "poc" / "UB_usb_asset_swap_poc.MBA"

USB_DISCONNECTED_ID_OFFSET = 0x1702
USB_CONNECTED_ID_OFFSET = 0x173E


def _parse_int(text: str) -> int:
    return int(text, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a UB.MBA patch that swaps the USB screen resource IDs."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Source UB.MBA")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Patched UB.MBA output path")
    parser.add_argument(
        "--resource-id",
        type=_parse_int,
        default=0x3FF8,
        help="Replacement built-in resource id for both USB branches",
    )
    return parser


def compute_header_crc(blob: bytearray) -> int:
    blob[0x3C:0x3E] = b"\x00\x00"
    crc = crc16_ccitt_word_le(blob[:0x3C], 0xFFFF)
    struct.pack_into("<H", blob, 0x3C, crc)
    return crc


def main() -> int:
    args = build_parser().parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    blob = bytearray(input_path.read_bytes())

    if blob[:8] != MAGIC_GBMQA:
        raise SystemExit("expected a bM_gbMQa UB.MBA input")

    value = args.resource_id & 0xFFFF
    struct.pack_into("<H", blob, USB_DISCONNECTED_ID_OFFSET, value)
    struct.pack_into("<H", blob, USB_CONNECTED_ID_OFFSET, value)

    header_crc = compute_header_crc(blob)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)

    print(f"wrote {output_path}")
    print(f"resource_id=0x{value:04x}")
    print(f"header_crc=0x{header_crc:04x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
