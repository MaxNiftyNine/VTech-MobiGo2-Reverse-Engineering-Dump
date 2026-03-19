from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


MAGIC_GBMQA = b"bM_gbMQa"
MAGIC_GDSQL = b"bM_gdSQl"


def crc16_ccitt_word_le(data: bytes, init: int = 0xFFFF) -> int:
    crc = init & 0xFFFF
    for i in range(0, len(data), 2):
        low = data[i]
        high = data[i + 1] if i + 1 < len(data) else 0
        for byte in (low, high):
            crc ^= byte << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
    return crc


def _read_ascii_title(data: bytes) -> str:
    raw = data[0x80:0xA0].split(b"\x00", 1)[0]
    chars = []
    for byte in raw:
        if 0x20 <= byte <= 0x7E:
            chars.append(chr(byte))
        elif byte in (0x09, 0x0A, 0x0D):
            chars.append(" ")
        else:
            chars.append(".")
    title = "".join(chars).strip(" .")
    return title


@dataclass(frozen=True)
class MobiHeader:
    offset: int
    path: Path
    magic: bytes
    size_words: int
    size_bytes: int
    field_0c: int
    field_10: int
    field_14: int
    field_18: int
    field_1c: int
    field_20: int
    field_24: int
    header_u16_3c: int
    title: str
    actual_size: int


def parse_header_bytes(data: bytes, *, path: Path, offset: int = 0) -> MobiHeader:
    if len(data) < 0xA0:
        raise SystemExit(f"{path} is too small to hold a MobiGo header at offset 0x{offset:x}")

    magic = data[:8]
    if magic not in (MAGIC_GBMQA, MAGIC_GDSQL):
        raise SystemExit(f"{path} does not start with a supported magic at offset 0x{offset:x}")

    fields = struct.unpack_from("<8sIIIIIIII", data, 0)
    title = _read_ascii_title(data)
    header_u16_3c = struct.unpack_from("<H", data, 0x3C)[0]

    return MobiHeader(
        offset=offset,
        path=path,
        magic=magic,
        size_words=fields[1],
        size_bytes=fields[1] * 2,
        field_0c=fields[2],
        field_10=fields[3],
        field_14=fields[4],
        field_18=fields[5],
        field_1c=fields[6],
        field_20=fields[7],
        field_24=fields[8],
        header_u16_3c=header_u16_3c,
        title=title,
        actual_size=len(data),
    )


def parse_header(path: Path) -> MobiHeader:
    return parse_header_bytes(path.read_bytes(), path=path, offset=0)


def iter_targets(target: Path, pattern: str) -> list[Path]:
    if target.is_file():
        return [target]
    return sorted(p for p in target.rglob(pattern) if p.is_file())


def find_headers(path: Path) -> list[MobiHeader]:
    data = path.read_bytes()
    headers: list[MobiHeader] = []
    for magic in (MAGIC_GBMQA, MAGIC_GDSQL):
        start = 0
        while True:
            hit = data.find(magic, start)
            if hit < 0:
                break
            try:
                headers.append(parse_header_bytes(data[hit:], path=path, offset=hit))
            except SystemExit:
                pass
            start = hit + 1
    headers.sort(key=lambda item: item.offset)
    return headers


def print_info(header: MobiHeader) -> None:
    print(header.path)
    print(f"  offset: 0x{header.offset:x}")
    print(f"  magic: {header.magic.decode('ascii', 'replace')}")
    print(f"  title: {header.title or '<empty>'}")
    print(f"  size_words: 0x{header.size_words:x}")
    print(f"  size_bytes: 0x{header.size_bytes:x} ({header.size_bytes})")
    print(f"  actual_size: 0x{header.actual_size:x} ({header.actual_size})")
    print(f"  header_u16_3c: 0x{header.header_u16_3c:04x}")
    print(f"  field_0c: 0x{header.field_0c:x}")
    print(f"  field_10: 0x{header.field_10:x}")
    print(f"  field_14: 0x{header.field_14:x}")
    print(f"  field_18: 0x{header.field_18:x}")
    print(f"  field_1c: 0x{header.field_1c:x}")
    print(f"  field_20: 0x{header.field_20:x}")
    print(f"  field_24: 0x{header.field_24:x}")
    if header.magic == MAGIC_GBMQA and header.actual_size >= 0x3E:
        blob = bytearray(header.path.read_bytes())
        blob[0x3C:0x3E] = b"\x00\x00"
        calc = crc16_ccitt_word_le(blob[:0x3C], 0xFFFF)
        print(f"  calc_header_crc_0x3c: 0x{calc:04x}")


def print_scan(headers: list[MobiHeader]) -> None:
    print("path\toffset\tmagic\ttitle\tsize_bytes\tactual_size\theader_u16_3c\tfield_10\tfield_14")
    for header in headers:
        print(
            f"{header.path}\t0x{header.offset:x}\t{header.magic.decode('ascii', 'replace')}\t{header.title}\t"
            f"0x{header.size_bytes:x}\t0x{header.actual_size:x}\t0x{header.header_u16_3c:04x}\t"
            f"0x{header.field_10:x}\t0x{header.field_14:x}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect MobiGo bM_gbMQa / bM_gdSQl headers.")
    sub = parser.add_subparsers(dest="command", required=True)

    info_p = sub.add_parser("info", help="Print parsed header fields for one file")
    info_p.add_argument("path")

    scan_p = sub.add_parser("scan", help="Scan a directory tree or single file for MobiGo headers")
    scan_p.add_argument("path")
    scan_p.add_argument("--glob", default="*.MBA", help="Recursive pattern when the target is a directory")

    find_p = sub.add_parser("find", help="Find embedded MobiGo headers inside a larger file")
    find_p.add_argument("path")

    crc_p = sub.add_parser("fix-header-crc", help="Recompute the 0x3c MBA header CRC")
    crc_p.add_argument("input")
    crc_p.add_argument("--output", help="Write the repaired file to a separate path")
    crc_p.add_argument("--in-place", action="store_true", help="Rewrite the input file directly")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "info":
        print_info(parse_header(Path(args.path)))
        return 0

    if args.command == "scan":
        target = Path(args.path)
        headers = []
        for path in iter_targets(target, args.glob):
            try:
                headers.append(parse_header(path))
            except SystemExit:
                continue
        print_scan(headers)
        return 0

    if args.command == "find":
        print_scan(find_headers(Path(args.path)))
        return 0

    if args.command == "fix-header-crc":
        input_path = Path(args.input)
        blob = bytearray(input_path.read_bytes())
        if len(blob) < 0x3E or blob[:8] != MAGIC_GBMQA:
            raise SystemExit("fix-header-crc expects a bM_gbMQa MBA file")
        blob[0x3C:0x3E] = b"\x00\x00"
        crc = crc16_ccitt_word_le(blob[:0x3C], 0xFFFF)
        struct.pack_into("<H", blob, 0x3C, crc)
        if args.in_place:
            output_path = input_path
        elif args.output:
            output_path = Path(args.output)
        else:
            output_path = input_path.with_suffix(input_path.suffix + ".fixed")
        output_path.write_bytes(blob)
        print(f"wrote {output_path} header_crc=0x{crc:04x}")
        return 0

    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
