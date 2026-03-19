# MobiGo / VTech USB RE

Reverse engineering notes and working tooling for talking to a VTech MobiGo handheld over USB.

## Repo layout

- `mobigo_cli.py`: main CLI entry point
- `mobigo_proto.py`: mailbox transport implementation over the mass-storage export
- `mobigo_vendor_bridge.py`: Python wrapper for the vendor 32-bit DLL path
- `tools/analyze_capture.py`: USBPcap analyzer plus file reconstruction for mailbox traffic
- `tools/mba_tool.py`: parser for `bM_gbMQa` / `bM_gdSQl` headers
- `tools/mgb_patch.py`: parser / extractor for `MGB_ptchZ` and `JFS0` containers
- `tools/unsp_tools.py`: thin `unSP` helper for assembly and control-flow scanning
- `native/mobigo_ls_helper.cs`: x86 helper used to call `VTech2010USBDllU.dll`
- `artifacts/`: local dumps and generated outputs

## Quick start

Mailbox transport over the exported USB disk:

```powershell
python mobigo_cli.py idle-info
python mobigo_cli.py type "\USENG\MM.MBA"
python mobigo_cli.py read-file "A:\PHO\00000012.PHO" artifacts\live\00000012.pho
```

Direct flash transport through the vendor DLL:

```powershell
python mobigo_cli.py dump-flash artifacts\live\mobigo_flash_64m.bin
python mobigo_cli.py read-flash 0x0 0x200 artifacts\live\flash_0.bin
```

Capture analysis:

```powershell
python tools\analyze_capture.py mobi2.pcapng
python tools\analyze_capture.py mobi2.pcapng --extract-reads artifacts\capture_reads --extract-uploads artifacts\capture_uploads
python tools\mba_tool.py scan artifacts\capture_reads
python tools\mba_tool.py fix-header-crc artifacts\live\DEFAULT_UB_fixed.MBA --output artifacts\live\DEFAULT_UB_fixed_crc.MBA
```

Patch container inspection:

```powershell
python tools\mgb_patch.py info artifacts\live\MGB_PTCH.BIN
python tools\mgb_patch.py extract artifacts\live\MGB_PTCH.BIN artifacts\live\mgb_patch_sections
```

`unSP` helpers:

```powershell
python tools\unsp_tools.py asm build\vasm\retf.bin --snippet "        retf" --print-hex
python tools\unsp_tools.py scan-flow artifacts\live\DEFAULT_UB_fixed.MBA --start-word 0x1a40 --count-words 0x80 --base-address 0x224800
python tools\build_ub_widget_poc.py
```

## Capture summary

The handheld does not use a custom USB class. It enumerates as:

- USB VID:PID `0x0f88:0x2d40`
- Product string `VTECH USB-MSDC DISK A 1.00`
- USB Mass Storage Bulk-Only transport

The proprietary protocol sits above USB MSC and uses raw sectors on the exported disk as a mailbox.

## Mailbox layout

Absolute LBAs seen in the capture:

- reply mailbox: `15280`
- request mailbox: `15536`
- doorbell A: `15832`
- doorbell B: `15834`

If you access the mounted volume instead of the whole disk, subtract the partition start LBA `8`.

Doorbell words:

- word 0: `0x00000000` for idle, `0x00002800` for active
- word 1: `(sector_count << 24) | 0x00000006`

## Decoded commands

- `0x02 <path>`: open path for transfer, returns handle and size
- `0x02 <fixed_path> <u16 mode>`: open path for transfer, with mode `1=read`, `2=write`
- `0x03 <handle> <aligned_len>`: read chunk
- `0x04 <handle> <chunk_len>`: write chunk
- `0x05 <handle>`: close handle
- `0x0A <path>`: create directory
- `0x09 <path>`: stat path, size at reply offset `+4`
- `0x10 <path>`: path type, returns `0=missing`, `1=file`, `2=directory`
- `0x0C <size> <handle>`: pre/post write metadata
- `0x0D <u16 handle> <u16 upper_state>`: secondary write metadata; the desktop DLL only passes the handle explicitly and leaves the upper 16 bits to inherited register state
- `0x0E <handle> <unix_timestamp>`: timestamp update

## Direct USB transport

The vendor stack also exposes a lower-level flash transport through `VTech2010USBDllU.dll`. The Python CLI wraps that path through an x86 helper built from `native/mobigo_ls_helper.cs`, which avoids the 64-bit Python / 32-bit DLL mismatch.

Observed call sequence:

- `DLL_LSInitUSBDevices(candidate, 1)`
- `DLL_LSGetTotalUSBDeviceNumber(candidate)`
- `DLL_LSFindDevSN(ctx64, 0)`
- `DLL_LSOpenUSBDevice(candidate, ctx64, &err)`
- `DLL_LSReadFlash(ctx64, offset, length, buffer, length)`

Current observed flash boundary:

- Offsets at and above `0x04000000` return a synthetic status-like pattern instead of meaningful flash data.
- `dump-flash` therefore defaults to `64 MiB`.

## Files produced during live testing

Keep large dumps under `artifacts/live/`. Current examples:

- USB volume image
- physical drive image
- direct `64 MiB` flash dump

These are excluded from Git by default.

## Live runtime findings

- `A:\DEFAULT\MGB_PTCH.BIN` is the live patch container. It starts with `MGB_ptchZ`, contains a `JFS0` section table, and its body is closely related to flash starting at `0x704000`.
- `A:\DEFAULT\MM.MBA` and `A:\DEFAULT\UB.MBA` are the installed executable modules:
  - `MM.MBA` carries the title string `Main Meun`
  - `UB.MBA` carries the title string `USB APP`
- `A:\DEFAULT\CG`, `A:\DEFAULT\CS`, and `A:\DEFAULT\WT` are installed assets and match the desktop `ComBin2` copies byte-for-byte.
- `read_file()` now prefers `stat()` size over `open()` size for logical `A:\DEFAULT\...` files, because `open()` reports the backing patch-container size instead of the logical file length.
- A locally built official `vasm` `unSP` assembler lives at `build\vasm\vasmunsp_oldstyle.exe`.
- A first-stage `UB.MBA` cave-hook builder now exists at `tools\build_ub_widget_poc.py`:
  - hook point `0x1732`
  - zero-filled cave `0xE3F6..0xF000`
  - current output `artifacts\poc\UB_widget_poc.MBA`

## Capture-derived content findings

- The USB capture includes enough mailbox traffic to reconstruct complete `PHO` uploads and several logical reads.
- Read opens use `opcode 0x02` with a fixed 42-byte path field and trailing mode `1`; the captured photo upload uses the same open opcode with trailing mode `2`.
- `\USENG\MBASORT.LST` lists six installed MBA entries: `00000066.MBA`, `00000067.MBA`, `00000068.MBA`, `00000069.MBA`, `00000070.MBA`, and `00000071.MBA`.
- First-sector reads from the capture show that `A:\BUNDLE\...` files are real `bM_gbMQa` executables, not simple assets:
  - `135800G1.MBA` -> title `MGB_G1`
  - `135800G2.MBA` -> title `MGB_G2`
  - `135800G3.MBA` -> title `MGB_G3`
  - `135800G4.MBA` -> title `MGB_G4`
  - `135800LD.MBA` -> title `Loading App`
  - `135800SY.MBA` -> title `MGB_SYS`
  - `135800TM.MBA` -> title `MGB_TM`
- The MBA dword at offset `0x08` is a size-in-words field; multiplying it by `2` matches the observed logical file size.
- The MBA header word at `0x3c` is a `CRC-16/CCITT` value over the first `0x1e` 16-bit words with seed `0xffff`.

## Limits

- The read path is working.
- The upload path is implemented from observed traffic, but command `0x0D` still contains one unknown 16-bit field.
- The currently connected handheld is not staying in transfer-ready mailbox mode, so live logical-file
  writes are still blocked on either a better UI-state setup or a flash-side deployment path.
