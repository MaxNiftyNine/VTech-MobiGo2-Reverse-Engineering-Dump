from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class JfsEntry:
    kind: int
    span: int
    meta: int
    offset: int
    end: int


@dataclass(frozen=True)
class JfsTable:
    offset: int
    header_size: int
    field_08: int
    count: int
    payload_base: int
    entries: list[JfsEntry]


@dataclass(frozen=True)
class MgbPatch:
    total_size: int
    jfs: JfsTable


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def parse_jfs0(data: bytes, offset: int = 0) -> JfsTable:
    if data[offset : offset + 4] != b"JFS0":
        raise ValueError(f"missing JFS0 header at 0x{offset:x}")

    header_size = _u32(data, offset + 4)
    field_08 = _u32(data, offset + 8)
    count = _u32(data, offset + 0xC)
    payload_base = offset + header_size

    cursor = payload_base
    entries: list[JfsEntry] = []
    rec_base = offset + 0x10
    for index in range(count):
        kind, span, meta = struct.unpack_from("<III", data, rec_base + index * 12)
        entry = JfsEntry(kind=kind, span=span, meta=meta, offset=cursor, end=cursor + span)
        entries.append(entry)
        cursor += span

    return JfsTable(
        offset=offset,
        header_size=header_size,
        field_08=field_08,
        count=count,
        payload_base=payload_base,
        entries=entries,
    )


def parse_mgb_patch(data: bytes) -> MgbPatch:
    if data[:9] != b"MGB_ptchZ":
        raise ValueError("file does not start with MGB_ptchZ")
    total_size = _u32(data, 0x0C)
    jfs = parse_jfs0(data, 0x40)
    return MgbPatch(total_size=total_size, jfs=jfs)


def cmd_info(path: Path) -> int:
    data = path.read_bytes()
    if data.startswith(b"MGB_ptchZ"):
        patch = parse_mgb_patch(data)
        print(f"type=MGB_ptchZ size=0x{len(data):x} total_size=0x{patch.total_size:x}")
        print(f"jfs_offset=0x{patch.jfs.offset:x} header_size=0x{patch.jfs.header_size:x} count={patch.jfs.count} field_08=0x{patch.jfs.field_08:x}")
        for index, entry in enumerate(patch.jfs.entries):
            print(
                f"section[{index}] kind={entry.kind} span=0x{entry.span:x} meta=0x{entry.meta:x} "
                f"off=0x{entry.offset:x} end=0x{entry.end:x}"
            )
        return 0

    if data.startswith(b"JFS0"):
        table = parse_jfs0(data, 0)
        print(f"type=JFS0 size=0x{len(data):x} header_size=0x{table.header_size:x} count={table.count} field_08=0x{table.field_08:x}")
        for index, entry in enumerate(table.entries):
            print(
                f"section[{index}] kind={entry.kind} span=0x{entry.span:x} meta=0x{entry.meta:x} "
                f"off=0x{entry.offset:x} end=0x{entry.end:x}"
            )
        return 0

    raise SystemExit("unsupported file type")


def cmd_extract(path: Path, outdir: Path) -> int:
    data = path.read_bytes()
    outdir.mkdir(parents=True, exist_ok=True)

    if data.startswith(b"MGB_ptchZ"):
        patch = parse_mgb_patch(data)
        (outdir / "wrapper.bin").write_bytes(data[: patch.jfs.payload_base])
        (outdir / "body.bin").write_bytes(data[patch.jfs.payload_base :])
        entries = patch.jfs.entries
    elif data.startswith(b"JFS0"):
        table = parse_jfs0(data, 0)
        entries = table.entries
    else:
        raise SystemExit("unsupported file type")

    for index, entry in enumerate(entries):
        blob = data[entry.offset : entry.end]
        name = f"section_{index:02d}_kind{entry.kind}_off_{entry.offset:06x}.bin"
        (outdir / name).write_bytes(blob)
    print(f"extracted {len(entries)} sections to {outdir}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and extract MGB_ptchZ / JFS0 containers.")
    sub = parser.add_subparsers(dest="command", required=True)

    info_p = sub.add_parser("info")
    info_p.add_argument("input")

    extract_p = sub.add_parser("extract")
    extract_p.add_argument("input")
    extract_p.add_argument("outdir")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "info":
        return cmd_info(Path(args.input))
    if args.command == "extract":
        return cmd_extract(Path(args.input), Path(args.outdir))
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
