from __future__ import annotations

import argparse
import json
import struct
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from scapy.all import PcapNgReader
from scapy.layers.usb import USBpcap


ABS_REPLY_LBA = 15280
ABS_REQUEST_LBA = 15536
ABS_CTRL_A_LBA = 15832
ABS_CTRL_B_LBA = 15834
DIR_ENTRY_SIZE = 28


@dataclass(frozen=True)
class Transaction:
    frame: int
    kind: str
    lba: int
    blocks: int
    data: bytes


@dataclass
class Evidence:
    kinds: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)

    def add(self, kind: str, source: str) -> None:
        self.kinds.add(kind)
        self.sources.add(source)


def iter_transactions(pcap_path: Path, device_id: int) -> list[Transaction]:
    transactions: list[Transaction] = []
    pending: dict[str, object] | None = None

    for idx, packet in enumerate(PcapNgReader(str(pcap_path)), 1):
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
                "frame": idx,
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
                            frame=int(pending["frame"]),
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
                    frame=int(pending["frame"]),
                    kind=str(pending["kind"]),
                    lba=int(pending["lba"]),
                    blocks=int(pending["blocks"]),
                    data=bytes(pending["data"]),
                )
            )
            pending = None

    return transactions


def autodetect_device_id(pcap_path: Path) -> int | None:
    for device_id in range(1, 32):
        try:
            transactions = iter_transactions(pcap_path, device_id)
        except Exception:
            continue

        reqs = [
            tx
            for tx in transactions
            if tx.kind == "W" and tx.lba == ABS_REQUEST_LBA and len(tx.data) >= 4
        ]
        if reqs:
            return device_id

    return None


def read_ascii_z(buffer: bytes, offset: int, max_length: int) -> str:
    count = 0
    while count < max_length and buffer[offset + count] != 0:
        count += 1
    return buffer[offset : offset + count].decode("ascii", "replace")


def parse_directory_page(reply: bytes) -> list[tuple[str, int, int]]:
    entries: list[tuple[str, int, int]] = []
    for offset in range(0, len(reply) - DIR_ENTRY_SIZE + 1, DIR_ENTRY_SIZE):
        raw_cursor = struct.unpack_from("<i", reply, offset)[0]
        if (raw_cursor & 0xFFFF) == 0xFFFF or raw_cursor == 0:
            break

        name = read_ascii_z(reply, offset + 4, 12)
        if not name.strip():
            break

        attributes = struct.unpack_from("<I", reply, offset + 16)[0]
        size = struct.unpack_from("<I", reply, offset + 24)[0]
        entries.append((name, attributes, size))

    return entries


def add_parent_dirs(path: str, evidence: dict[str, Evidence], source: str, kind: str) -> None:
    normalized = path.replace("/", "\\").rstrip("\\")
    if not normalized:
        return

    parts = normalized.split("\\")
    if len(parts) == 1:
        evidence.setdefault(normalized, Evidence()).add(kind, source)
        return

    current = parts[0]
    if current == "":
        current = "\\"
    evidence.setdefault(normalized, Evidence()).add(kind, source)

    for part in parts[1:-1]:
        if current == "\\":
            current = "\\" + part
        else:
            current = current + "\\" + part
        evidence.setdefault(current, Evidence()).add("parent-dir", source)


def normalize_to_a_namespace(path: str) -> str:
    normalized = path.replace("/", "\\")
    if normalized.startswith("A:\\"):
        return normalized
    if normalized.startswith("\\USENG"):
        return "A:" + normalized
    if normalized.startswith("\\DEFAULT"):
        return "A:" + normalized
    if normalized.startswith("\\ETC"):
        return "A:" + normalized
    return normalized


def infer_from_pcaps(pcap_paths: list[Path]) -> dict[str, Evidence]:
    evidence: dict[str, Evidence] = {}

    for pcap_path in pcap_paths:
        device_id = autodetect_device_id(pcap_path)
        if device_id is None:
            continue

        transactions = iter_transactions(pcap_path, device_id)
        source = pcap_path.name

        for index, tx in enumerate(transactions):
            if tx.kind != "W" or tx.lba != ABS_REQUEST_LBA or len(tx.data) < 4:
                continue

            opcode = struct.unpack_from("<I", tx.data, 0)[0]
            if opcode in (0x02, 0x09, 0x0A, 0x10):
                path = tx.data[4:].split(b"\x00", 1)[0].decode("ascii", "replace")
                if not path:
                    continue

                label = {
                    0x02: "open-probe",
                    0x09: "stat-probe",
                    0x0A: "mkdir-probe",
                    0x10: "type-probe",
                }[opcode]
                evidence.setdefault(path, Evidence()).add(label, source)
                add_parent_dirs(path, evidence, source, "parent-dir")
                continue

            if opcode != 0x06:
                continue

            path = tx.data[4:].split(b"\x00", 1)[0].decode("ascii", "replace")
            if not path:
                continue

            evidence.setdefault(path, Evidence()).add("dir-list-target", source)
            add_parent_dirs(path, evidence, source, "parent-dir")

            reply = None
            for follow in transactions[index + 1 : index + 8]:
                if follow.kind == "R" and follow.lba == ABS_REPLY_LBA:
                    reply = follow.data
                    break

            if reply is None:
                continue

            for name, attributes, size in parse_directory_page(reply):
                child = path.rstrip("\\") + "\\" + name
                child = child.replace("\\\\", "\\")
                entry_kind = "dir-entry-dir" if (attributes & 0x10) != 0 else "dir-entry-file"
                evidence.setdefault(child, Evidence()).add(entry_kind, source)
                evidence.setdefault(child, Evidence()).add(f"size={size}", source)
                add_parent_dirs(child, evidence, source, "parent-dir")

    return evidence


def build_a_tree(evidence: dict[str, Evidence]) -> dict[str, Evidence]:
    normalized: dict[str, Evidence] = {}
    for path, info in evidence.items():
        mapped = normalize_to_a_namespace(path)
        target = normalized.setdefault(mapped, Evidence())
        target.kinds.update(info.kinds)
        target.sources.update(info.sources)
    return normalized


def print_tree(paths: dict[str, Evidence]) -> None:
    for path in sorted(paths):
        info = paths[path]
        kinds = ",".join(sorted(info.kinds))
        sources = ",".join(sorted(info.sources))
        print(f"{path} [{kinds}] {{{sources}}}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Infer MobiGo filesystem paths from USB captures.")
    parser.add_argument("pcaps", nargs="+", help="PCAP/PCAPNG files to inspect")
    parser.add_argument("--json", help="Optional JSON output path")
    parser.add_argument("--raw-only", action="store_true", help="Print only raw protocol namespace paths")
    args = parser.parse_args()

    pcap_paths = [Path(value) for value in args.pcaps]
    raw_paths = infer_from_pcaps(pcap_paths)
    a_paths = build_a_tree(raw_paths)

    output = {
        "raw": {
            path: {
                "kinds": sorted(info.kinds),
                "sources": sorted(info.sources),
            }
            for path, info in sorted(raw_paths.items())
        },
        "a_namespace": {
            path: {
                "kinds": sorted(info.kinds),
                "sources": sorted(info.sources),
            }
            for path, info in sorted(a_paths.items())
        },
    }

    if args.json:
        Path(args.json).write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")

    if args.raw_only:
        print_tree(raw_paths)
    else:
        print_tree(a_paths)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
