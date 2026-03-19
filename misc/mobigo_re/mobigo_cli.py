from __future__ import annotations

import argparse
import binascii
import sys
from datetime import datetime, timezone
from pathlib import Path

from mobigo_proto import (
    DEFAULT_PID,
    DEFAULT_VID,
    MobiGoDevice,
    autodetect_windows_target,
    open_backend,
)
from mobigo_vendor_bridge import run_helper


def _parse_int(text: str) -> int:
    return int(text, 0)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Talk to a VTech MobiGo mailbox protocol device.")
    parser.add_argument("--target", help=r"Raw device path, volume path (for example \\.\E:), or image file")
    parser.add_argument("--raw-windows", action="store_true", help="Force Windows raw disk/volume access")
    parser.add_argument("--volume-relative", action="store_true", help="Treat the target as a mounted volume")
    parser.add_argument("--physical-relative", action="store_true", help="Treat the target as a whole disk / physical drive")
    parser.add_argument("--vid", default=DEFAULT_VID, help="USB VID for auto-detection on Windows")
    parser.add_argument("--pid", default=DEFAULT_PID, help="USB PID for auto-detection on Windows")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("idle-info", help="Read the 2-sector idle information block")

    exists_p = sub.add_parser("type", help="Check whether a path is missing, a file, or a directory")
    exists_p.add_argument("path")

    stat_p = sub.add_parser("stat", help="Return the observed kind and size for a remote path")
    stat_p.add_argument("path")

    mkdir_p = sub.add_parser("mkdir", help="Create a device directory using opcode 0x0A")
    mkdir_p.add_argument("path")

    read_p = sub.add_parser("read-file", help="Read a device file into a local file")
    read_p.add_argument("remote_path")
    read_p.add_argument("output")

    write_p = sub.add_parser("write-file-experimental", help="Experimental write path based on captured upload flow")
    write_p.add_argument("remote_path")
    write_p.add_argument("input")
    write_p.add_argument("--allow-create", action="store_true", help="Approve creating the remote file when it does not already exist")
    write_p.add_argument("--mtime", type=_parse_int, help="Optional Unix timestamp for command 0x0E; omit to match the common-file copy path")
    write_p.add_argument("--meta16", type=_parse_int, default=0, help="Upper 16 bits for command 0x0D; desktop DLL callers do not set this explicitly")

    dump_p = sub.add_parser("dump-device", help="Read the entire target block device or image into a local file")
    dump_p.add_argument("output")

    vendor_dump_p = sub.add_parser("dump-flash", help="Dump console flash through the vendor USB DLL transport")
    vendor_dump_p.add_argument("output")
    vendor_dump_p.add_argument("--size", default="0x04000000", help="Flash size in bytes (default: 0x04000000)")

    vendor_read_p = sub.add_parser("read-flash", help="Read a console flash region through the vendor USB DLL transport")
    vendor_read_p.add_argument("offset")
    vendor_read_p.add_argument("length")
    vendor_read_p.add_argument("output")

    vendor_write_p = sub.add_parser("write-flash", help="Write a console flash region through the vendor USB DLL transport")
    vendor_write_p.add_argument("offset")
    vendor_write_p.add_argument("input")

    return parser


def resolve_target(args: argparse.Namespace) -> str:
    if args.target:
        return args.target
    target = autodetect_windows_target(args.vid, args.pid)
    if not target:
        raise SystemExit("Could not auto-detect the MobiGo device; pass --target explicitly.")
    return target


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "dump-flash":
        result = run_helper("dump", args.output, args.size)
        if result.stdout:
            print(result.stdout.strip())
        return 0

    if args.command == "read-flash":
        result = run_helper("read", args.offset, args.length, args.output)
        if result.stdout:
            print(result.stdout.strip())
        return 0

    if args.command == "write-flash":
        result = run_helper("write", args.offset, args.input)
        if result.stdout:
            print(result.stdout.strip())
        return 0

    target = resolve_target(args)
    volume_relative = None
    if args.volume_relative:
        volume_relative = True
    elif args.physical_relative:
        volume_relative = False

    backend = open_backend(target, raw_windows=args.raw_windows)
    device = MobiGoDevice(backend, volume_relative=volume_relative)

    try:
        if args.command == "idle-info":
            data = device.idle_info()
            print(binascii.hexlify(data).decode())
            return 0

        if args.command == "type":
            kind = device.path_type(args.path)
            mapping = {0: "missing", 1: "file", 2: "directory"}
            print(mapping.get(kind, f"unknown({kind})"))
            return 0

        if args.command == "stat":
            info = device.stat(args.path)
            mapping = {0: "missing", 1: "file", 2: "directory"}
            print(f"kind={mapping.get(info.kind, info.kind)} size={info.size}")
            return 0

        if args.command == "mkdir":
            result = device.create_directory(args.path)
            print(f"result={result}")
            return 0

        if args.command == "read-file":
            data = device.read_file(args.remote_path)
            out_path = Path(args.output)
            out_path.write_bytes(data)
            print(f"wrote {len(data)} bytes to {out_path}")
            return 0

        if args.command == "write-file-experimental":
            blob = Path(args.input).read_bytes()
            device.write_file_experimental(
                args.remote_path,
                blob,
                allow_create=args.allow_create,
                unix_timestamp=args.mtime,
                meta16=args.meta16,
            )
            print(f"attempted upload of {len(blob)} bytes to {args.remote_path}")
            return 0

        if args.command == "dump-device":
            total = device.dump_backend(args.output)
            print(f"dumped {total} bytes to {args.output}")
            return 0

        parser.error(f"unsupported command: {args.command}")
        return 2
    finally:
        device.close()


if __name__ == "__main__":
    sys.exit(main())
