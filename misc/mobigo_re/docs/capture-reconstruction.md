# Capture Reconstruction Notes

This note records what can now be reconstructed directly from `mobi2.pcapng`.

## What the capture contains

The USB capture does not just show mailbox opcodes. It contains enough request/response traffic to
rebuild several logical files and to identify the first-sector headers of the transferred game
packages.

The checked-in tool for this is:

```powershell
python tools\analyze_capture.py C:\Users\Max\Desktop\mobi2.pcapng --extract-reads artifacts\capture_reads --extract-uploads artifacts\capture_uploads
```

That produces:

- per-file read extractions plus `_manifest_reads.json`
- per-upload payload extractions plus `_manifest_uploads.json`

## Reconstructed logical reads

The capture includes complete reads of:

- `\ETC\PROFILE.DAT`
- `\USENG\BOKSORT.LST`
- `\USENG\MBASORT.LST`
- `A:\PHO\00000001.PHO` through `A:\PHO\00000012.PHO`

The capture also includes first-sector peeks of these executable modules:

- `A:\BUNDLE\G1\135800G1.MBA`
- `A:\BUNDLE\G2\135800G2.MBA`
- `A:\BUNDLE\G3\135800G3.MBA`
- `A:\BUNDLE\G4\135800G4.MBA`
- `A:\BUNDLE\LD\135800LD.MBA`
- `A:\BUNDLE\SY\135800SY.MBA`
- `A:\BUNDLE\TM\135800TM.MBA`
- `A:\USENG\EBOOK.MBA`
- `A:\USENG\MM.MBA`
- `A:\USENG\UB.MBA`
- `A:\DEFAULT\MGB_PTCH.BIN`

For the MBA files, the capture only reads `0x200` bytes, which is still enough to recover the
container magic, title, and size field.

## MBA titles and sizes from the capture

Using `tools\mba_tool.py`, the first-sector MBA headers decode as:

- `A:\BUNDLE\G1\135800G1.MBA`: title `MGB_G1`, size `0x214000`
- `A:\BUNDLE\G2\135800G2.MBA`: title `MGB_G2`, size `0x13e000`
- `A:\BUNDLE\G3\135800G3.MBA`: title `MGB_G3`, size `0x3f0000`
- `A:\BUNDLE\G4\135800G4.MBA`: title `MGB_G4`, size `0x2e2000`
- `A:\BUNDLE\LD\135800LD.MBA`: title `Loading App`, size `0x1e000`
- `A:\BUNDLE\SY\135800SY.MBA`: title `MGB_SYS`, size `0x174000`
- `A:\BUNDLE\TM\135800TM.MBA`: title `MGB_TM`, size `0x13000`
- `A:\USENG\EBOOK.MBA`: title `MGB_EBK`, size `0x1d000`
- `A:\USENG\MM.MBA`: title `Main Meun`, size `0x41000`
- `A:\USENG\UB.MBA`: title `USB APP`, size `0x13000`

Important consequence: transferred content is not just data blobs. The `BUNDLE` tree contains real
`bM_gbMQa` executable modules with the same high-level container format as the built-in apps.

## `MBASORT.LST`

The capture fully reconstructs `\USENG\MBASORT.LST`:

```text
0006
00000068.MBA 1
00000067.MBA 1
00000066.MBA 1
00000071.MBA 1
00000070.MBA 1
00000069.MBA 1
```

This strongly suggests the main software also tracks a logical installed-title list separate from
the `A:\BUNDLE\...` paths the desktop software uses during transfer.

## Observed upload sequence

The capture includes one complete upload to `A:\PHO\00000013.PHO`.

Observed command flow:

1. `0x02` open `A:\PHO\00000013.PHO`
2. `0x0C size=0`
3. `0x0D meta16=0x004f`
4. `0x0E unix_timestamp=0x69b487de`
5. repeated `0x04 <wire_len>` plus bulk data writes
6. `0x0C size=0x27f08`
7. `0x0D meta16=0x166d`
8. `0x05` close
9. `0x11 0x41` post-write command

The reconstructed upload payload starts with:

```text
_inF
00000008
DLCv 1.0
info
```

The pre-write `meta16` value `0x004f` and final `meta16` value `0x166d` are still unexplained. The
important point is that the capture-backed tooling can now recover both the uploaded file body and
the associated metadata values for further correlation work.

Additional DLL-backed protocol detail:

- `0x02` open packets use a fixed 42-byte path field plus a trailing 16-bit mode
- captured reads use mode `1`
- the captured write to `A:\PHO\00000013.PHO` uses mode `2`
- `0x0A` is the directory-create opcode used for `\DEFAULT`

The desktop DLL’s `0x0D` helper does not appear to accept a second explicit metadata argument. Its
assembly only writes `DX` from the file handle and then stores the full `EDX` register to the
packet, leaving the upper 16 bits inherited from prior register state. That strongly suggests the
observed `meta16` values may be incidental rather than intentional API inputs.

## Opaque read handle detail

The open reply and the subsequent read/close requests do not always carry the same obvious 32-bit
handle value.

Examples:

- `\ETC\PROFILE.DAT` open reply begins with `0x00002800`, and the following `0x03` uses the same
  low-word style handle.
- `A:\BUNDLE\G1\135800G1.MBA` open reply begins with `0x00002a00`, but the following `0x03`
  carries bytes `00 2a 33 35`.
- `A:\USENG\MM.MBA` open reply begins with `0x00003c00`, but the following `0x03` carries bytes
  `00 3c 41 00`.

Practical reading: the read/close path carries an opaque context token that includes the low-word
file handle plus extra state. This does not block extraction, but it means the 32-bit handle field
should not be over-simplified in future write tooling.
