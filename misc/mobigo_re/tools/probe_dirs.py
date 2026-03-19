from __future__ import annotations

import argparse
import binascii
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mobigo_proto import MobiGoDevice, autodetect_windows_target, open_backend, _pack_path_command


DIR_ENTRY_SIZE = 28
ATTR_FILE = 0x00010000
ATTR_DIR = 0x00020000


def read_ascii_z(buffer: bytes, offset: int, max_length: int) -> str:
    count = 0
    while count < max_length and buffer[offset + count] != 0:
        count += 1
    return buffer[offset : offset + count].decode("ascii", "replace")


def parse_directory_page(reply: bytes) -> list[tuple[int, str, int, int]]:
    entries: list[tuple[int, str, int, int]] = []
    for offset in range(0, len(reply) - DIR_ENTRY_SIZE + 1, DIR_ENTRY_SIZE):
        raw_cursor = int.from_bytes(reply[offset : offset + 4], "little", signed=True)
        if (raw_cursor & 0xFFFF) == 0xFFFF or raw_cursor == 0:
            break

        name = read_ascii_z(reply, offset + 4, 12)
        if not name.strip():
            break

        attributes = int.from_bytes(reply[offset + 16 : offset + 20], "little")
        size = int.from_bytes(reply[offset + 24 : offset + 28], "little")
        entries.append((raw_cursor, name, attributes, size))

    return entries


def join_remote_path(parent: str, child: str) -> str:
    base = parent.rstrip("\\")
    if not base:
        return "\\" + child
    return base + "\\" + child


def is_directory(attributes: int) -> bool:
    return (attributes & ATTR_DIR) != 0


def list_directory(device: MobiGoDevice, path: str) -> list[tuple[int, str, int, int]]:
    request = _pack_path_command(0x06, path)
    reply = device._single_command(request)  # noqa: SLF001
    return parse_directory_page(reply)


def walk_tree(
    device: MobiGoDevice,
    root: str,
) -> dict[str, dict[str, object]]:
    pending = [root]
    seen: set[str] = set()
    listing: dict[str, dict[str, object]] = {}

    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.add(current)

        entries = list_directory(device, current)
        listing[current] = {
            "kind": "dir",
            "entries": [
                {
                    "name": name,
                    "path": join_remote_path(current, name),
                    "attributes": attributes,
                    "size": size,
                    "kind": "dir" if is_directory(attributes) else "file",
                }
                for _, name, attributes, size in entries
            ],
        }

        for _, name, attributes, _size in entries:
            if not is_directory(attributes):
                continue
            pending.append(join_remote_path(current, name))

    return listing


def preview_hex(data: bytes, limit: int = 64) -> str:
    return binascii.hexlify(data[:limit]).decode("ascii")


def probe(device: MobiGoDevice, path: str) -> None:
    request = _pack_path_command(0x06, path)
    reply = device._single_command(request)  # noqa: SLF001
    entries = parse_directory_page(reply)

    print(f"path={path}")
    print(f"reply={preview_hex(reply)}")
    if not entries:
        print("entries=<none>")
        return

    for cursor, name, attributes, size in entries:
        print(f"entry cursor={cursor} attr=0x{attributes:08x} size={size} name={name}")


def print_tree_listing(tree: dict[str, dict[str, object]]) -> None:
    for directory in sorted(tree):
        print(f"[{directory}]")
        entries = tree[directory]["entries"]
        if not entries:
            print("  <empty>")
            continue
        for entry in entries:
            print(
                "  "
                f"{entry['kind']} size={entry['size']} attr=0x{entry['attributes']:08x} "
                f"{entry['path']}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe raw MobiGo directory-list commands.")
    parser.add_argument("--target", help=r"Raw device path, volume path (for example \\.\D:), or image file")
    parser.add_argument("--raw-windows", action="store_true")
    parser.add_argument("--volume-relative", action="store_true")
    parser.add_argument("--physical-relative", action="store_true")
    parser.add_argument("--tree", action="store_true", help="Recursively list directories starting at the given path(s)")
    parser.add_argument("--json", help="Optional JSON output path for --tree output")
    parser.add_argument(
        "paths",
        nargs="*",
        default=["A:\\", "\\", "A:\\BUNDLE", "A:\\DEFAULT", "A:\\USENG", "\\DEFAULT", "\\ETC", "\\USENG"],
    )
    args = parser.parse_args()

    target = args.target or autodetect_windows_target()
    if not target:
        raise SystemExit("Could not auto-detect the MobiGo device; pass --target explicitly.")

    volume_relative = None
    if args.volume_relative:
        volume_relative = True
    elif args.physical_relative:
        volume_relative = False

    backend = open_backend(target, raw_windows=args.raw_windows or target.startswith("\\\\.\\"))
    device = MobiGoDevice(backend, volume_relative=volume_relative)
    try:
        if args.tree:
            tree: dict[str, dict[str, object]] = {}
            for path in args.paths:
                tree.update(walk_tree(device, path))
            print_tree_listing(tree)
            if args.json:
                Path(args.json).write_text(json.dumps(tree, indent=2, sort_keys=True), encoding="utf-8")
            return 0

        for path in args.paths:
            try:
                probe(device, path)
            except Exception as exc:
                print(f"path={path}")
                print(f"error={exc}")
            print("---")
    finally:
        device.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
