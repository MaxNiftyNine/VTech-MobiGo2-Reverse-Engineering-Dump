from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path

from mba_tool import MAGIC_GBMQA, crc16_ccitt_word_le


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts" / "live" / "DEFAULT_MM_fixed.MBA"
DEFAULT_OUTPUT = ROOT / "artifacts" / "poc" / "MM_usb_visual_poc.MBA"

MIRROR_DELTA = 0x28000


@dataclass(frozen=True)
class WidgetPatch:
    name: str
    resource_offset: int
    resource_expected: int
    x_offset: int
    y_offset: int


PRIMARY_WIDGETS = (
    WidgetPatch("usb_disconnected_primary", 0x00FC68, 0x3FF5, 0x00FC76, 0x00FC80),
    WidgetPatch("usb_connected_primary", 0x00FCA4, 0x3FF6, 0x00FCB2, 0x00FCBC),
    WidgetPatch("usb_connected_followup_a", 0x00FF1A, 0x3FF6, 0x00FF28, 0x00FF32),
    WidgetPatch("usb_connected_followup_b", 0x010274, 0x3FF6, 0x010282, 0x01028C),
)


def _parse_int(text: str) -> int:
    return int(text, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build an MM.MBA USB-screen patch by editing all visible-screen widget constructors."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Source MM.MBA")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Patched MM.MBA output path")
    parser.add_argument("--x", type=_parse_int, default=0x0000, help="Replacement X coordinate")
    parser.add_argument("--y", type=_parse_int, default=0x0000, help="Replacement Y coordinate")
    parser.add_argument(
        "--resource-id",
        type=_parse_int,
        help="Optional replacement built-in resource id for all patched widget constructors",
    )
    parser.add_argument(
        "--no-mirror",
        action="store_true",
        help="Patch only the primary code copy and leave the mirrored copy unchanged",
    )
    return parser


def compute_header_crc(blob: bytearray) -> int:
    blob[0x3C:0x3E] = b"\x00\x00"
    crc = crc16_ccitt_word_le(blob[:0x3C], 0xFFFF)
    struct.pack_into("<H", blob, 0x3C, crc)
    return crc


def _load_u16(blob: bytes, offset: int) -> int:
    return struct.unpack_from("<H", blob, offset)[0]


def _store_u16(blob: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", blob, offset, value & 0xFFFF)


def _iter_sites(blob: bytes, include_mirror: bool) -> list[WidgetPatch]:
    sites = list(PRIMARY_WIDGETS)
    if include_mirror:
        for patch in PRIMARY_WIDGETS:
            mirrored = WidgetPatch(
                name=f"{patch.name}_mirror",
                resource_offset=patch.resource_offset + MIRROR_DELTA,
                resource_expected=patch.resource_expected,
                x_offset=patch.x_offset + MIRROR_DELTA,
                y_offset=patch.y_offset + MIRROR_DELTA,
            )
            if mirrored.y_offset >= len(blob):
                raise SystemExit(f"mirror site for {patch.name} falls outside the input image")
            sites.append(mirrored)
    return sites


def main() -> int:
    args = build_parser().parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    blob = bytearray(input_path.read_bytes())

    if blob[:8] != MAGIC_GBMQA:
        raise SystemExit("expected a bM_gbMQa MM.MBA input")

    sites = _iter_sites(blob, include_mirror=not args.no_mirror)
    replacement_resource = None if args.resource_id is None else (args.resource_id & 0xFFFF)

    for site in sites:
        current_resource = _load_u16(blob, site.resource_offset)
        if current_resource != site.resource_expected:
            raise SystemExit(
                f"{site.name} resource at 0x{site.resource_offset:x} is 0x{current_resource:04x}, "
                f"expected 0x{site.resource_expected:04x}"
            )
        _store_u16(blob, site.x_offset, args.x)
        _store_u16(blob, site.y_offset, args.y)
        if replacement_resource is not None:
            _store_u16(blob, site.resource_offset, replacement_resource)

    header_crc = compute_header_crc(blob)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)

    print(f"wrote {output_path}")
    print(f"x=0x{args.x & 0xFFFF:04x} y=0x{args.y & 0xFFFF:04x}")
    if replacement_resource is None:
        print("resource_id=unchanged")
    else:
        print(f"resource_id=0x{replacement_resource:04x}")
    print(f"patched_sites={len(sites)}")
    print(f"header_crc=0x{header_crc:04x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
