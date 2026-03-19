from __future__ import annotations

import argparse
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts" / "poc" / "usb_connected_text_patch.bin"
FIELD_CHARS = 20
DEFAULT_TEXT = "MAX WAS HERE!"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a fixed-width UTF-16LE patch blob for the APP0 USB status text."
    )
    parser.add_argument("--text", default=DEFAULT_TEXT, help=f"Replacement text, max {FIELD_CHARS} ASCII chars")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output blob path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    text = args.text
    if len(text) > FIELD_CHARS:
        raise SystemExit(f"text too long: {len(text)} > {FIELD_CHARS}")
    if any(ord(ch) > 0x7F for ch in text):
        raise SystemExit("text must be ASCII")

    padded = text.ljust(FIELD_CHARS)
    blob = padded.encode("utf-16le") + b"\x00\x00"

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(blob)

    print(f"wrote {output_path}")
    print(f"text={text!r}")
    print(f"field_chars={FIELD_CHARS}")
    print(f"blob_len=0x{len(blob):x}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
