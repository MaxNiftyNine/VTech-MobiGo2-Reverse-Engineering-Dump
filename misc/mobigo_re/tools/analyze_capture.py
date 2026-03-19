from __future__ import annotations

import argparse
import json
import struct
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from scapy.all import PcapNgReader
from scapy.layers.usb import USBpcap


ABS_REPLY_LBA = 15280
ABS_REQUEST_LBA = 15536
ABS_CTRL_A_LBA = 15832
ABS_CTRL_B_LBA = 15834

KNOWN_OPCODES = {
    0x02: "open",
    0x03: "read",
    0x04: "write",
    0x05: "close",
    0x09: "stat",
    0x0C: "write-meta",
    0x0D: "write-meta16",
    0x0E: "timestamp",
    0x10: "type",
    0x11: "post-write",
}


@dataclass(frozen=True)
class Transaction:
    idx: int
    kind: str
    lba: int
    blocks: int
    data: bytes


@dataclass
class ReadExtraction:
    path: str
    size: int | None = None
    open_command_frame: int = 0
    open_reply_frame: int | None = None
    close_command_frame: int | None = None
    blob: bytearray = field(default_factory=bytearray)

    def final_bytes(self) -> bytes:
        if self.size is None:
            return bytes(self.blob)
        return bytes(self.blob[: self.size])


@dataclass
class UploadExtraction:
    path: str
    open_command_frame: int = 0
    begin_frame: int | None = None
    close_command_frame: int | None = None
    final_size: int | None = None
    unix_timestamp: int | None = None
    meta16_pre: int | None = None
    meta16_post: int | None = None
    blob: bytearray = field(default_factory=bytearray)

    def final_bytes(self) -> bytes:
        if self.final_size is None:
            return bytes(self.blob)
        return bytes(self.blob[: self.final_size])


def iter_transactions(pcap_path: str, device_id: int) -> list[Transaction]:
    transactions: list[Transaction] = []
    pending: dict[str, object] | None = None
    for idx, packet in enumerate(PcapNgReader(pcap_path), 1):
        up = USBpcap(bytes(packet))
        if up.device != device_id:
            continue

        payload = bytes(up.payload)
        if pending is None:
            if up.endpoint != 0x02 or up.info != 0 or up.dataLength < 31 or not payload.startswith(b"USBC"):
                continue
            cdb = payload[15:31]
            scsi_opcode = cdb[0]
            if scsi_opcode not in (0x28, 0x2A):
                continue
            pending = {
                "idx": idx,
                "kind": "R" if scsi_opcode == 0x28 else "W",
                "lba": struct.unpack(">I", cdb[2:6])[0],
                "blocks": struct.unpack(">H", cdb[7:9])[0],
                "data": bytearray(),
            }
            continue

        if pending["kind"] == "R":
            if up.endpoint == 0x81 and up.info == 1:
                if payload.startswith(b"USBS") and up.dataLength == 13:
                    transactions.append(
                        Transaction(
                            idx=int(pending["idx"]),
                            kind=str(pending["kind"]),
                            lba=int(pending["lba"]),
                            blocks=int(pending["blocks"]),
                            data=bytes(pending["data"]),
                        )
                    )
                    pending = None
                else:
                    pending["data"].extend(payload)
            continue

        if up.endpoint == 0x02 and up.info == 0 and not payload.startswith(b"USBC"):
            pending["data"].extend(payload)
        elif up.endpoint == 0x81 and up.info == 1 and payload.startswith(b"USBS") and up.dataLength == 13:
            transactions.append(
                Transaction(
                    idx=int(pending["idx"]),
                    kind=str(pending["kind"]),
                    lba=int(pending["lba"]),
                    blocks=int(pending["blocks"]),
                    data=bytes(pending["data"]),
                )
            )
            pending = None
    return transactions


def iter_mailbox_transactions(transactions: list[Transaction]) -> list[Transaction]:
    return [
        tx
        for tx in transactions
        if tx.lba in (ABS_REPLY_LBA, ABS_REQUEST_LBA, ABS_CTRL_A_LBA, ABS_CTRL_B_LBA)
    ]


def _decode_command_name(data: bytes) -> str:
    if len(data) < 4:
        return "short"
    opcode = struct.unpack_from("<I", data, 0)[0]
    return KNOWN_OPCODES.get(opcode, f"0x{opcode:02x}")


def _extract_ascii_path(data: bytes) -> str:
    return data[4:].split(b"\x00", 1)[0].decode("ascii", "replace")


def _safe_output_name(path: str) -> str:
    return path.replace(":", "").replace("\\", "__").replace("/", "__")


def print_summary(transactions: list[Transaction]) -> None:
    lbac = Counter()
    for tx in transactions:
        if tx.lba in (ABS_REPLY_LBA, ABS_REQUEST_LBA, ABS_CTRL_A_LBA, ABS_CTRL_B_LBA):
            lbac[(tx.kind, tx.lba, tx.blocks)] += 1
            print(
                f"{tx.idx:5d} {tx.kind} lba={tx.lba:5d} blocks={tx.blocks:3d} "
                f"first16={tx.data[:16].hex()}"
            )

    print("\nSummary:")
    for key, count in lbac.most_common():
        kind, lba, blocks = key
        print(f"{kind} lba={lba} blocks={blocks}: {count}")


def extract_read_files(transactions: list[Transaction]) -> list[ReadExtraction]:
    extracted: list[ReadExtraction] = []
    current: ReadExtraction | None = None
    waiting_open_reply = False
    active_read_remaining: int | None = None
    active_read_needs_ack = False

    for tx in transactions:
        if tx.kind == "W" and tx.lba == ABS_REQUEST_LBA and len(tx.data) >= 4:
            opcode = struct.unpack_from("<I", tx.data, 0)[0]
            if opcode == 0x02:
                current = ReadExtraction(
                    path=_extract_ascii_path(tx.data),
                    open_command_frame=tx.idx,
                )
                waiting_open_reply = True
                active_read_remaining = None
                active_read_needs_ack = False
                continue

            if opcode == 0x03 and current is not None and not waiting_open_reply:
                active_read_remaining = struct.unpack_from("<I", tx.data, 8)[0]
                active_read_needs_ack = False
                continue

            if opcode == 0x05 and current is not None:
                current.close_command_frame = tx.idx
                if current.final_bytes():
                    extracted.append(current)
                current = None
                waiting_open_reply = False
                active_read_remaining = None
                active_read_needs_ack = False
                continue

        if tx.kind != "R" or tx.lba != ABS_REPLY_LBA:
            continue

        if waiting_open_reply and current is not None:
            current.size = struct.unpack_from("<I", tx.data, 4)[0]
            current.open_reply_frame = tx.idx
            waiting_open_reply = False
            continue

        if current is None or active_read_remaining is None:
            continue

        if active_read_remaining > 0:
            take = min(len(tx.data), active_read_remaining)
            current.blob.extend(tx.data[:take])
            active_read_remaining -= take
            if active_read_remaining == 0:
                active_read_needs_ack = True
            continue

        if active_read_needs_ack:
            active_read_remaining = None
            active_read_needs_ack = False

    return extracted


def extract_uploads(transactions: list[Transaction]) -> list[UploadExtraction]:
    extracted: list[UploadExtraction] = []
    last_open_path: str | None = None
    last_open_frame: int | None = None
    current: UploadExtraction | None = None
    pending_chunk_len: int | None = None

    for tx in transactions:
        if tx.kind == "W" and tx.lba == ABS_REQUEST_LBA and len(tx.data) >= 4:
            opcode = struct.unpack_from("<I", tx.data, 0)[0]

            if opcode == 0x02:
                last_open_path = _extract_ascii_path(tx.data)
                last_open_frame = tx.idx
                continue

            if opcode == 0x0C and last_open_path is not None:
                size = struct.unpack_from("<I", tx.data, 4)[0]
                if size == 0 and current is None:
                    current = UploadExtraction(
                        path=last_open_path,
                        open_command_frame=last_open_frame or tx.idx,
                        begin_frame=tx.idx,
                    )
                    continue
                if current is not None and size != 0:
                    current.final_size = size
                    continue

            if opcode == 0x0D and current is not None:
                meta16 = struct.unpack_from("<H", tx.data, 6)[0]
                if current.meta16_pre is None:
                    current.meta16_pre = meta16
                else:
                    current.meta16_post = meta16
                continue

            if opcode == 0x0E and current is not None:
                current.unix_timestamp = struct.unpack_from("<I", tx.data, 8)[0]
                continue

            if opcode == 0x04 and current is not None:
                pending_chunk_len = struct.unpack_from("<I", tx.data, 8)[0]
                continue

            if opcode == 0x05 and current is not None:
                current.close_command_frame = tx.idx
                extracted.append(current)
                current = None
                pending_chunk_len = None
                continue

        if (
            tx.kind == "W"
            and tx.lba == ABS_REQUEST_LBA
            and tx.blocks > 1
            and current is not None
            and pending_chunk_len is not None
        ):
            take = min(len(tx.data), pending_chunk_len)
            current.blob.extend(tx.data[:take])
            pending_chunk_len -= take
            if pending_chunk_len == 0:
                pending_chunk_len = None

    return extracted


def write_read_extractions(output_dir: Path, files: list[ReadExtraction]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for item in files:
        final = item.final_bytes()
        out_path = output_dir / _safe_output_name(item.path)
        out_path.write_bytes(final)
        manifest.append(
            {
                "path": item.path,
                "size": item.size,
                "captured_size": len(final),
                "open_command_frame": item.open_command_frame,
                "open_reply_frame": item.open_reply_frame,
                "close_command_frame": item.close_command_frame,
                "output": out_path.name,
            }
        )
    (output_dir / "_manifest_reads.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def write_upload_extractions(output_dir: Path, uploads: list[UploadExtraction]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for item in uploads:
        final = item.final_bytes()
        out_path = output_dir / _safe_output_name(item.path)
        out_path.write_bytes(final)
        manifest.append(
            {
                "path": item.path,
                "final_size": item.final_size,
                "captured_size": len(final),
                "meta16_pre": item.meta16_pre,
                "meta16_post": item.meta16_post,
                "unix_timestamp": item.unix_timestamp,
                "open_command_frame": item.open_command_frame,
                "begin_frame": item.begin_frame,
                "close_command_frame": item.close_command_frame,
                "output": out_path.name,
            }
        )
    (output_dir / "_manifest_uploads.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize and reconstruct MobiGo mailbox traffic from a USBPcap capture.")
    parser.add_argument("pcap")
    parser.add_argument("--device", type=int, default=11, help="USB device address inside the capture")
    parser.add_argument("--extract-reads", help="Output directory for mailbox read extractions")
    parser.add_argument("--extract-uploads", help="Output directory for captured upload payloads")
    parser.add_argument("--quiet-summary", action="store_true", help="Skip the per-transaction mailbox summary")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    transactions = iter_transactions(args.pcap, args.device)
    mailbox = iter_mailbox_transactions(transactions)

    if not args.quiet_summary:
        print_summary(mailbox)

    if args.extract_reads:
        reads = extract_read_files(mailbox)
        out_dir = Path(args.extract_reads)
        write_read_extractions(out_dir, reads)
        print(f"\nExtracted {len(reads)} read file(s) into {out_dir}")
        for item in reads:
            print(f"read  {item.path} size={item.size} captured={len(item.final_bytes())}")

    if args.extract_uploads:
        uploads = extract_uploads(mailbox)
        out_dir = Path(args.extract_uploads)
        write_upload_extractions(out_dir, uploads)
        print(f"\nExtracted {len(uploads)} upload payload(s) into {out_dir}")
        for item in uploads:
            print(
                f"write {item.path} size={item.final_size} captured={len(item.final_bytes())} "
                f"meta16_pre={item.meta16_pre} meta16_post={item.meta16_post}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
