from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from mba_tool import MAGIC_GBMQA, crc16_ccitt_word_le
from unsp_tools import assemble_unsp


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts" / "live" / "DEFAULT_UB_before_patch_20260314.MBA"
DEFAULT_OUTPUT = ROOT / "artifacts" / "poc" / "UB_widget_poc.MBA"

BASE_WORD_ADDRESS = 0x224800
HOOK_CALL_FILE_OFFSET = 0x1774
RETURN_ADDRESS = 0x2253BC
CAVE_FILE_OFFSET = 0xE3F6
RESTORE_DS_ZERO = b"\x00\xfe"


@dataclass(frozen=True)
class WidgetCall:
    resource_id: int
    arg3: int
    arg4: int


DEFAULT_WIDGET_CALLS = (
    WidgetCall(0xC00B, 0x0020, 0x0040),
    WidgetCall(0xC00C, 0x0060, 0x0040),
    WidgetCall(0xC00B, 0x0020, 0x0080),
    WidgetCall(0xC00C, 0x0060, 0x0080),
)


def _parse_int(text: str) -> int:
    return int(text, 0)


def _parse_call(text: str) -> WidgetCall:
    parts = [part.strip() for part in text.split(",")]
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("widget call must be RESOURCE_ID,ARG3,ARG4")
    return WidgetCall(*(_parse_int(part) & 0xFFFF for part in parts))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a UB.MBA homebrew patch that spawns extra widgets on the USB screen."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Source UB.MBA")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Patched UB.MBA output path")
    parser.add_argument(
        "--call",
        action="append",
        type=_parse_call,
        default=[],
        help="Extra widget call as RESOURCE_ID,ARG3,ARG4. Repeatable. Defaults to a 2x2 test pattern.",
    )
    return parser


def _widget_call_source(call: WidgetCall) -> list[str]:
    return [
        "        sub sp,#6",
        f"        ld r2,#{call.resource_id:#06x}",
        "        ld r3,#0",
        "        add r4,sp,#1",
        "        st r2,(r4++)",
        "        st r3,(r4)",
        f"        ld r3,#{call.arg3:#06x}",
        "        add r4,sp,#3",
        "        st r3,(r4)",
        f"        ld r3,#{call.arg4:#06x}",
        "        add r4,sp,#4",
        "        st r3,(r4)",
        "        ld r3,#0",
        "        add r4,sp,#5",
        "        st r3,(r4)",
        "        ld r3,#0",
        "        add r4,sp,#6",
        "        st r3,(r4)",
        "        call 0x223c0e",
        "        add sp,#6",
    ]


def build_stub_source(widget_calls: tuple[WidgetCall, ...]) -> str:
    lines = ["        org 0"]
    for call in widget_calls:
        lines.extend(_widget_call_source(call))
    lines.append("        sub sp,#1")
    return "\n".join(lines) + "\n"


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
    widget_calls = tuple(args.call) if args.call else DEFAULT_WIDGET_CALLS

    if blob[:8] != MAGIC_GBMQA:
        raise SystemExit("expected a bM_gbMQa UB.MBA input")

    cave_size = 0xC0A
    cave_slice = blob[CAVE_FILE_OFFSET : CAVE_FILE_OFFSET + cave_size]
    if any(cave_slice):
        raise SystemExit("chosen cave is not zero-filled in the input image")

    cave_address = BASE_WORD_ADDRESS + (CAVE_FILE_OFFSET // 2)
    hook_bytes = bytearray()
    with TemporaryDirectory() as td:
        body_path = Path(td) / "stub_body.bin"
        return_path = Path(td) / "return.bin"
        assemble_unsp(
            build_stub_source(widget_calls),
            body_path,
        )
        assemble_unsp(f"        org 0\n        goto 0x{RETURN_ADDRESS:06x}\n", return_path)
        hook_bytes = bytearray(body_path.read_bytes() + RESTORE_DS_ZERO + return_path.read_bytes())

    if len(hook_bytes) > cave_size:
        raise SystemExit(f"stub is too large for the cave: {len(hook_bytes)} > {cave_size}")

    with TemporaryDirectory() as td:
        hook_path = Path(td) / "hook.bin"
        assemble_unsp(f"        org 0\n        goto 0x{cave_address:06x}\n", hook_path)
        trampoline = hook_path.read_bytes()

    if len(trampoline) != 4:
        raise SystemExit(f"expected a 2-word goto trampoline, got {len(trampoline)} bytes")

    blob[HOOK_CALL_FILE_OFFSET : HOOK_CALL_FILE_OFFSET + len(trampoline)] = trampoline
    blob[CAVE_FILE_OFFSET : CAVE_FILE_OFFSET + len(hook_bytes)] = hook_bytes

    header_crc = compute_header_crc(blob)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)

    print(f"wrote {output_path}")
    print(f"hook_file_offset=0x{HOOK_CALL_FILE_OFFSET:x}")
    print(f"cave_file_offset=0x{CAVE_FILE_OFFSET:x}")
    print(f"cave_address=0x{cave_address:06x}")
    print(f"stub_size=0x{len(hook_bytes):x}")
    print(f"header_crc=0x{header_crc:04x}")
    for index, call in enumerate(widget_calls, start=1):
        print(f"call{index}=resource_id=0x{call.resource_id:04x} arg3=0x{call.arg3:04x} arg4=0x{call.arg4:04x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
