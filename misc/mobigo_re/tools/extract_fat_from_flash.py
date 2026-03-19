from __future__ import annotations

import argparse
from pathlib import Path


BOOT_SIG = b"\xeb\x3e\x90MSWIN4.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract the embedded FAT16 volume from a raw MobiGo flash dump.")
    parser.add_argument("flash_image", help="Path to the 64 MiB raw flash dump")
    parser.add_argument("output_image", help="Path for the carved FAT image")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    flash_path = Path(args.flash_image)
    output_path = Path(args.output_image)

    data = flash_path.read_bytes()
    boot_offset = data.find(BOOT_SIG)
    if boot_offset < 0:
        raise SystemExit("Could not find embedded FAT16 boot sector signature.")

    sector = data[boot_offset : boot_offset + 512]
    bytes_per_sector = int.from_bytes(sector[11:13], "little")
    total_sectors_16 = int.from_bytes(sector[19:21], "little")
    total_sectors_32 = int.from_bytes(sector[32:36], "little")
    total_sectors = total_sectors_16 or total_sectors_32
    if bytes_per_sector == 0 or total_sectors == 0:
        raise SystemExit("Boot sector fields are invalid.")

    fs_size = bytes_per_sector * total_sectors
    end_offset = boot_offset + fs_size
    if end_offset > len(data):
        raise SystemExit("Embedded FAT16 volume extends past end of flash dump.")

    carved = data[boot_offset:end_offset]
    output_path.write_bytes(carved)

    print(f"boot_offset=0x{boot_offset:X}")
    print(f"bytes_per_sector={bytes_per_sector}")
    print(f"total_sectors={total_sectors}")
    print(f"fs_size=0x{fs_size:X}")
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
