from __future__ import annotations

import abc
import math
import os
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


SECTOR_SIZE = 512
DEFAULT_VID = "0F88"
DEFAULT_PID = "2D40"

ABS_REPLY_LBA = 15280
ABS_REQUEST_LBA = 15536
ABS_CTRL_A_LBA = 15832
ABS_CTRL_B_LBA = 15834
PARTITION_START_LBA = 8

PATH_FIELD_BYTES = 42
CREATE_DIR_FIELD_BYTES = 30
OPEN_MODE_READ = 1
OPEN_MODE_WRITE = 2

CTRL_IDLE_BASE = 0x00000000
CTRL_ACTIVE_BASE = 0x00002800
CTRL_MAGIC = 0x00000006


class ProtocolError(RuntimeError):
    pass


class BackendError(RuntimeError):
    pass


class BlockBackend(abc.ABC):
    @abc.abstractmethod
    def size_bytes(self) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def read_sectors(self, lba: int, count: int) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def write_sectors(self, lba: int, data: bytes) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class FileBackend(BlockBackend):
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = os.fspath(path)
        self._fh = open(self.path, "r+b", buffering=0)

    def read_sectors(self, lba: int, count: int) -> bytes:
        self._fh.seek(lba * SECTOR_SIZE)
        data = self._fh.read(count * SECTOR_SIZE)
        if len(data) != count * SECTOR_SIZE:
            raise BackendError(f"short read at LBA {lba} for {count} sector(s)")
        return data

    def size_bytes(self) -> int:
        return os.fstat(self._fh.fileno()).st_size

    def write_sectors(self, lba: int, data: bytes) -> None:
        if len(data) % SECTOR_SIZE:
            raise ValueError("sector writes must be 512-byte aligned")
        self._fh.seek(lba * SECTOR_SIZE)
        self._fh.write(data)
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


class WinRawBackend(BlockBackend):
    def __init__(self, path: str) -> None:
        try:
            import win32con
            import win32file
            import winioctlcon
        except ImportError as exc:
            raise BackendError("pywin32 is required for Windows raw disk access") from exc

        self._win32con = win32con
        self._win32file = win32file
        self._winioctlcon = winioctlcon
        self.path = path
        self._handle = self._open_with_retries(path)
        if self._looks_like_volume(path):
            self._prepare_volume_for_raw_io()

    def _should_retry(self, exc: Exception) -> bool:
        winerror = getattr(exc, "winerror", None)
        if winerror is None and isinstance(exc, tuple) and exc:
            winerror = exc[0]
        return winerror in {21}

    def _retry(self, func):
        last_exc = None
        for _ in range(8):
            try:
                return func()
            except Exception as exc:
                last_exc = exc
                if not self._should_retry(exc):
                    raise
                time.sleep(0.25)
        if last_exc is not None:
            raise last_exc
        raise BackendError("unexpected retry loop failure")

    def _open_with_retries(self, path: str):
        return self._retry(
            lambda: self._win32file.CreateFile(
                path,
                self._win32con.GENERIC_READ | self._win32con.GENERIC_WRITE,
                self._win32con.FILE_SHARE_READ | self._win32con.FILE_SHARE_WRITE,
                None,
                self._win32con.OPEN_EXISTING,
                0,
                None,
            )
        )

    @staticmethod
    def _looks_like_volume(path: str) -> bool:
        return path.startswith("\\\\.\\") and len(path) == 6 and path[4].isalpha() and path[5] == ":"

    def _prepare_volume_for_raw_io(self) -> None:
        # Windows rejects raw sector writes to mounted volumes until the caller
        # locks and dismounts the filesystem view. On removable media Windows
        # sometimes denies these control codes even though direct sector I/O
        # still works on the opened volume handle, so treat them as best-effort.
        for code in (
            self._winioctlcon.FSCTL_LOCK_VOLUME,
            self._winioctlcon.FSCTL_DISMOUNT_VOLUME,
            self._winioctlcon.FSCTL_ALLOW_EXTENDED_DASD_IO,
        ):
            try:
                self._win32file.DeviceIoControl(self._handle, code, None, 0)
            except Exception:
                continue

    def read_sectors(self, lba: int, count: int) -> bytes:
        offset = lba * SECTOR_SIZE
        self._win32file.SetFilePointer(self._handle, offset, self._win32con.FILE_BEGIN)
        _, data = self._retry(lambda: self._win32file.ReadFile(self._handle, count * SECTOR_SIZE))
        if len(data) != count * SECTOR_SIZE:
            raise BackendError(f"short read at LBA {lba} for {count} sector(s)")
        return data

    def size_bytes(self) -> int:
        data = self._win32file.DeviceIoControl(
            self._handle,
            self._winioctlcon.IOCTL_DISK_GET_LENGTH_INFO,
            None,
            8,
        )
        return struct.unpack("<Q", data)[0]

    def write_sectors(self, lba: int, data: bytes) -> None:
        if len(data) % SECTOR_SIZE:
            raise ValueError("sector writes must be 512-byte aligned")
        offset = lba * SECTOR_SIZE
        self._win32file.SetFilePointer(self._handle, offset, self._win32con.FILE_BEGIN)
        self._retry(lambda: self._win32file.WriteFile(self._handle, data))

    def close(self) -> None:
        self._handle.Close()


def _align_up(value: int, alignment: int) -> int:
    return (value + alignment - 1) // alignment * alignment


def _pack_control(active: bool, sector_count: int) -> bytes:
    if not 0 <= sector_count <= 0xFF:
        raise ValueError("sector_count must fit in one byte")
    base = CTRL_ACTIVE_BASE if active else CTRL_IDLE_BASE
    word1 = CTRL_MAGIC | (sector_count << 24)
    return struct.pack("<II", base, word1).ljust(SECTOR_SIZE, b"\x00")


def _pack_command(opcode: int, *args: int, payload: bytes = b"") -> bytes:
    body = struct.pack("<I", opcode)
    if args:
        body += struct.pack("<" + "I" * len(args), *args)
    body += payload
    if len(body) > SECTOR_SIZE:
        raise ValueError("single-sector command payload too large")
    return body.ljust(SECTOR_SIZE, b"\x00")


def _normalize_ascii_path(path: str) -> bytes:
    path = path.replace("/", "\\")
    return path.encode("ascii") + b"\x00"


def _pack_fixed_path(path: str, field_bytes: int) -> bytes:
    payload = _normalize_ascii_path(path)
    if len(payload) > field_bytes:
        raise ValueError(f"path is too long for {field_bytes}-byte field: {path!r}")
    return payload.ljust(field_bytes, b"\x00")


def _pack_path_command(opcode: int, path: str, *, field_bytes: int = PATH_FIELD_BYTES) -> bytes:
    return _pack_command(opcode, payload=_pack_fixed_path(path, field_bytes))


def _pack_open_command(path: str, mode: int) -> bytes:
    payload = _pack_fixed_path(path, PATH_FIELD_BYTES) + struct.pack("<H", mode & 0xFFFF)
    return _pack_command(0x02, payload=payload)


@dataclass(frozen=True)
class RemoteFileInfo:
    kind: int
    size: int = 0
    handle: int = 0
    raw_kind: int = 0
    created_via_open: bool = False

    @property
    def exists(self) -> bool:
        return self.kind != 0

    @property
    def is_file(self) -> bool:
        return self.kind == 1

    @property
    def is_dir(self) -> bool:
        return self.kind == 2


class MobiGoDevice:
    def __init__(self, backend: BlockBackend, volume_relative: Optional[bool] = None) -> None:
        self.backend = backend
        self.partition_lba = self._detect_partition_lba(volume_relative)
        self.reply_lba = ABS_REPLY_LBA - self.partition_lba
        self.request_lba = ABS_REQUEST_LBA - self.partition_lba
        self.ctrl_a_lba = ABS_CTRL_A_LBA - self.partition_lba
        self.ctrl_b_lba = ABS_CTRL_B_LBA - self.partition_lba

    def close(self) -> None:
        self.backend.close()

    def dump_backend(self, output_path: str | os.PathLike[str]) -> int:
        total = self.backend.size_bytes()
        sectors = total // SECTOR_SIZE
        chunk_sectors = 256
        out_path = Path(output_path)
        with out_path.open("wb") as fh:
            for lba in range(0, sectors, chunk_sectors):
                count = min(chunk_sectors, sectors - lba)
                fh.write(self.backend.read_sectors(lba, count))
        return total

    def _detect_partition_lba(self, volume_relative: Optional[bool]) -> int:
        if volume_relative is True:
            return PARTITION_START_LBA
        if volume_relative is False:
            return 0

        sector0 = self.backend.read_sectors(0, 1)
        if sector0.startswith(b"\xeb\x3e\x90MSWIN4.1"):
            return PARTITION_START_LBA

        try:
            sector8 = self.backend.read_sectors(PARTITION_START_LBA, 1)
        except BackendError:
            return PARTITION_START_LBA
        if sector8.startswith(b"\xeb\x3e\x90MSWIN4.1"):
            return 0

        # Default to volume-relative: safer when the user points us at a mounted volume.
        return PARTITION_START_LBA

    def _ring_a(self, sector_count: int, active: bool = True) -> None:
        self.backend.write_sectors(self.ctrl_a_lba, _pack_control(active=active, sector_count=sector_count))

    def _ring_b(self, sector_count: int, active: bool = True) -> None:
        self.backend.write_sectors(self.ctrl_b_lba, _pack_control(active=active, sector_count=sector_count))

    def idle_info(self) -> bytes:
        self._ring_a(2, active=False)
        return self.backend.read_sectors(self.reply_lba, 2)

    def _single_command(self, request: bytes, response_sectors: int = 1) -> bytes:
        self._ring_b(1, active=True)
        self.backend.write_sectors(self.request_lba, request)
        self._ring_a(response_sectors, active=True)
        return self.backend.read_sectors(self.reply_lba, response_sectors)

    @staticmethod
    def _decode_kind(raw_kind: int) -> int:
        # Live devices sometimes return status in the upper 16 bits while the
        # low 16 bits still carry the actual kind code.
        return raw_kind & 0xFFFF

    def path_type(self, path: str) -> int:
        reply = self._single_command(_pack_path_command(0x10, path))
        raw_kind = struct.unpack_from("<I", reply, 0)[0]
        return self._decode_kind(raw_kind)

    def stat(self, path: str) -> RemoteFileInfo:
        raw_kind = struct.unpack_from("<I", self._single_command(_pack_path_command(0x10, path)), 0)[0]
        kind = self._decode_kind(raw_kind)
        if kind == 0:
            return RemoteFileInfo(kind=0, size=0, handle=0, raw_kind=raw_kind)
        reply = self._single_command(_pack_path_command(0x09, path))
        size = struct.unpack_from("<I", reply, 4)[0]
        return RemoteFileInfo(kind=kind, size=size, handle=0, raw_kind=raw_kind)

    def open_for_read(self, path: str) -> RemoteFileInfo:
        raw_kind = struct.unpack_from("<I", self._single_command(_pack_path_command(0x10, path)), 0)[0]
        kind = self._decode_kind(raw_kind)
        if kind != 1:
            return RemoteFileInfo(kind=kind, size=0, handle=0, raw_kind=raw_kind)
        reply = self._single_command(_pack_open_command(path, OPEN_MODE_READ))
        handle, size = struct.unpack_from("<II", reply, 0)
        return RemoteFileInfo(kind=1, size=size, handle=handle, raw_kind=raw_kind)

    def open_for_write(self, path: str) -> RemoteFileInfo:
        raw_kind = struct.unpack_from("<I", self._single_command(_pack_path_command(0x10, path)), 0)[0]
        kind = self._decode_kind(raw_kind)
        reply = self._single_command(_pack_open_command(path, OPEN_MODE_WRITE))
        handle, size = struct.unpack_from("<II", reply, 0)
        if handle == 0:
            return RemoteFileInfo(kind=kind, size=0, handle=0, raw_kind=raw_kind)
        created_via_open = kind == 0
        return RemoteFileInfo(
            kind=1,
            size=size,
            handle=handle,
            raw_kind=raw_kind,
            created_via_open=created_via_open,
        )

    def create_directory(self, path: str) -> int:
        reply = self._single_command(_pack_path_command(0x0A, path, field_bytes=CREATE_DIR_FIELD_BYTES))
        return struct.unpack_from("<h", reply, 0)[0]

    def close_handle(self, handle: int) -> bytes:
        return self._single_command(_pack_command(0x05, handle))

    def _bulk_read_chunk(self, handle: int, request_len: int) -> bytes:
        request_len = _align_up(request_len, SECTOR_SIZE)
        sectors = request_len // SECTOR_SIZE
        if sectors <= 1:
            return self._single_command(_pack_command(0x03, handle, request_len), response_sectors=1)

        self._ring_b(1, active=True)
        self.backend.write_sectors(self.request_lba, _pack_command(0x03, handle, request_len))

        remaining = sectors
        out = bytearray()
        while remaining > 0:
            # The capture only ever uses 128-sector and 64-sector bulk doorbells.
            step = min(remaining, 0x80)
            self._ring_a(step, active=True)
            out.extend(self.backend.read_sectors(self.reply_lba, step))
            remaining -= step

        # The capture shows a final 1-sector acknowledgement after multi-sector reads.
        self._ring_a(1, active=True)
        _ = self.backend.read_sectors(self.reply_lba, 1)
        return bytes(out)

    def read_file(self, path: str) -> bytes:
        stat_info = self.stat(path)
        if not stat_info.is_file:
            raise ProtocolError(f"{path!r} is not a file on the device")

        info = self.open_for_read(path)
        if not info.is_file:
            raise ProtocolError(f"{path!r} could not be opened for read")

        expected_size = stat_info.size
        if expected_size and info.size != expected_size:
            # Some logical files under A:\DEFAULT\... are backed by a larger
            # patch container: open() reports the container size while the read
            # stream still starts at the logical file payload.
            info = RemoteFileInfo(kind=info.kind, size=expected_size, handle=info.handle)

        try:
            remaining = info.size
            data = bytearray()
            while remaining > 0:
                chunk = min(remaining, 0x28000)
                wire_len = _align_up(chunk, SECTOR_SIZE)
                payload = self._bulk_read_chunk(info.handle, wire_len)
                data.extend(payload[:chunk])
                remaining -= chunk
            return bytes(data)
        finally:
            self.close_handle(info.handle)

    def write_file_experimental(
        self,
        path: str,
        data: bytes,
        *,
        allow_create: bool = False,
        unix_timestamp: int | None = None,
        meta16: int = 0,
    ) -> None:
        info = self.open_for_write(path)
        if not info.is_file:
            raise ProtocolError(f"{path!r} could not be opened for write")

        handle = info.handle
        create_flow = info.created_via_open
        if create_flow and not allow_create:
            self.close_handle(handle)
            raise ProtocolError(
                f"{path!r} does not exist; pass --allow-create to approve creating a new file"
            )
        timestamp = unix_timestamp
        if create_flow and timestamp is None:
            timestamp = int(time.time())

        self._single_command(_pack_command(0x0C, 0, handle))
        # The desktop DLL only passes the handle explicitly here. The upper
        # 16 bits observed on the wire come from inherited register state in
        # the caller, so keep this user-controlled but treat it as best-effort.
        pre_meta16 = meta16
        if create_flow and pre_meta16 == 0:
            pre_meta16 = 0x004F
        cmd_0d_pre = struct.pack("<IHH", 0x0D, handle & 0xFFFF, pre_meta16 & 0xFFFF).ljust(SECTOR_SIZE, b"\x00")
        self._single_command(cmd_0d_pre)
        if timestamp is not None:
            self._single_command(_pack_command(0x0E, handle, timestamp))

        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + 0x80 * SECTOR_SIZE]
            wire = chunk.ljust(_align_up(len(chunk), SECTOR_SIZE), b"\x00")
            sectors = len(wire) // SECTOR_SIZE

            self._ring_b(1, active=True)
            self.backend.write_sectors(self.request_lba, _pack_command(0x04, handle, len(wire)))
            self._ring_b(sectors, active=True)
            self.backend.write_sectors(self.request_lba, wire)
            self._ring_a(1, active=True)
            _ = self.backend.read_sectors(self.reply_lba, 1)
            offset += len(chunk)

        self._single_command(_pack_command(0x0C, len(data), handle))
        cmd_0d_post = struct.pack("<IHH", 0x0D, handle & 0xFFFF, meta16 & 0xFFFF).ljust(SECTOR_SIZE, b"\x00")
        self._single_command(cmd_0d_post)
        self.close_handle(handle)
        if create_flow:
            post_write = _pack_command(0x11, 0x41)
            self._single_command(post_write)
            self._single_command(post_write)


def autodetect_windows_target(vid: str = DEFAULT_VID, pid: str = DEFAULT_PID) -> Optional[str]:
    if os.name != "nt":
        return None

    ps = rf"""
$disk = Get-CimInstance Win32_DiskDrive | Where-Object {{
    ($_.PNPDeviceID -match 'VID_{vid}&PID_{pid}') -or
    ($_.PNPDeviceID -match 'USBSTOR\\\\DISK&VEN_VTECH&PROD_USB-MSDC_DISK_A') -or
    ($_.Model -like 'VTECH USB-MSDC DISK A*')
}} | Select-Object -First 1
if (-not $disk) {{
    return
}}
$parts = Get-CimAssociatedInstance -InputObject $disk -Association Win32_DiskDriveToDiskPartition
$logical = foreach ($part in $parts) {{
    Get-CimAssociatedInstance -InputObject $part -Association Win32_LogicalDiskToPartition
}}
[pscustomobject]@{{
    physical = $disk.DeviceID
    logical = ($logical | Select-Object -ExpandProperty DeviceID -ErrorAction SilentlyContinue | Select-Object -First 1)
}} | ConvertTo-Json -Compress
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return None

    import json

    obj = json.loads(result.stdout)
    logical = obj.get("logical")
    if logical:
        return rf"\\.\{logical}"
    physical = obj.get("physical")
    if physical:
        return physical
    return None


def open_backend(target: str, raw_windows: bool = False) -> BlockBackend:
    if os.name == "nt" and (raw_windows or target.startswith("\\\\.\\")):
        return WinRawBackend(target)
    return FileBackend(target)
