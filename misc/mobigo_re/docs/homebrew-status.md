# MobiGo Homebrew Status

This note tracks the current state of executable targets, packaging, and patch delivery for the
original MobiGo.

## Current executable targets

The live device exposes these logical files under `A:\DEFAULT\...`:

- `MM.MBA`: installed main-menu executable, live size `0x41000`
- `UB.MBA`: installed USB-mode executable, live size `0x13000`
- `CG`: graphics asset blob, live size `0x19100`
- `CS`: asset blob, live size `0xBEC0`
- `WT`: asset blob, live size `0x27C10`
- `MGB_PTCH.BIN`: live patch container, size `0x233F72`

`CG`, `CS`, and `WT` are byte-identical to the desktop `ComBin2` copies. `MM.MBA` and `UB.MBA`
are not. These two are the code-bearing files that matter for homebrew.

The USB capture also shows header reads from transferred `A:\BUNDLE\...` executables. Titles
recovered from the first sector:

- `135800G1.MBA` -> `MGB_G1`
- `135800G2.MBA` -> `MGB_G2`
- `135800G3.MBA` -> `MGB_G3`
- `135800G4.MBA` -> `MGB_G4`
- `135800LD.MBA` -> `Loading App`
- `135800SY.MBA` -> `MGB_SYS`
- `135800TM.MBA` -> `MGB_TM`

That matters because it proves transferred content uses the same `bM_gbMQa` executable wrapper
family as the built-in applications.

## Installed MBA structure

Both live MBA files start with `bM_gbMQa` and then a header area that is mostly metadata and filler.
The actual code/data payload starts at file offset `0x1000`.

Observed header strings:

- `DEFAULT_MM_fixed.MBA`:
  - title at `0x80`: `Main Meun`
  - header checksum-like field at `0x3c`: `0xAA87`
- `DEFAULT_UB_fixed.MBA`:
  - title at `0x80`: `USB APP`
  - header checksum-like field at `0x3c`: `0xDE84`

For `MM.MBA`, the code/data region is partially mirrored:

- file offsets `0x8000..0x17fff` and `0x30000..0x37fff` are identical
- file offsets `0x10000..0x17fff` and `0x38000..0x3ffff` are identical

This implies a stable patch to `MM.MBA` will likely need to update both copies of mirrored code.

`UB.MBA` has a useful amount of zero-filled slack inside the loaded region:

- `0xE3F6..0xF000`
- `0x12BB8..0x13000`

Those ranges are good candidates for a custom stub if the runtime maps the whole file.

The dword at file offset `0x08` is a size-in-words field. Multiplying it by `2` matches the
logical file size for:

- live `MM.MBA` and `UB.MBA`
- desktop `ComBin2\MM.MBA` and `ComBin2\UB.MBA`
- captured bundle MBA headers from `A:\BUNDLE\...`

The 16-bit word at file offset `0x3c` is now solved:

- algorithm: `CRC-16/CCITT`
- seed: `0xffff`
- input: first `0x1e` 16-bit words of the file
- byte order inside each word: low byte first, then high byte
- the checksum field itself is treated as `0x0000` during calculation

That is enough to repair a patched `MM.MBA` or `UB.MBA` header after editing.

## Absolute address model

The header fields in the installed MBA files line up cleanly with the live code:

- `UB.MBA`:
  - `field_18 = 0x224800`
  - `field_14 = 0x226261`
- `MM.MBA`:
  - `field_18 = 0x224800`
  - `field_14 = 0x22d8a5`

Treating `field_18` as the absolute code base produces coherent disassembly:

- internal calls stay inside the module at `0x225xxx` / `0x226xxx`
- shared firmware calls land below the module at `0x223cxx` / `0x223dxx`
- `UB.MBA` startup code around file offset `0x3480` disassembles cleanly with
  `base_address=0x224800`

That is the current best working model for patching and hook placement.

## Patch container findings

`A:\DEFAULT\MGB_PTCH.BIN` starts with `MGB_ptchZ` and wraps a `JFS0` section table.

The `JFS0` table at offset `0x40` has:

- header size `0x88`
- 10 entries
- section records laid out as `kind`, `span`, `meta`

The body after the `0xC8`-byte wrapper matches flash at `0x704000` closely enough to treat it as
the serialized on-device patch body.

Nested container finding:

- desktop `ComBin2\APP0` starts with `bM_gdSQl`
- `APP0` contains another `bM_gdSQl` at `0x47bc0`
- `APP0` contains an embedded `bM_gbMQa` titled `APP0` at `0x48000`
- the live `MGB_PTCH.BIN` also contains a `bM_gdSQl` blob at `0x4d8d8` with the same `0x47c00`
  size field as the outer `APP0` container

That is the clearest current link between the desktop install assets and the live patch body, even
though the blobs are not byte-identical.

The current parser:

```powershell
python tools\mgb_patch.py info artifacts\live\MGB_PTCH.BIN
python tools\mgb_patch.py extract artifacts\live\MGB_PTCH.BIN artifacts\live\mgb_patch_sections
```

## `unSP` tooling

An official `vasm` build with `unSP` support is now present locally:

- assembler path: `build\vasm\vasmunsp_oldstyle.exe`

This is enough to assemble raw little-endian `unSP` binary stubs:

```powershell
python tools\unsp_tools.py asm build\vasm\retf.bin --snippet "        retf" --print-hex
```

Known opcode smoke test:

- `retf` assembles to `909a` in the on-disk little-endian byte order

The `unSP` helper also has a control-flow scanner:

```powershell
python tools\unsp_tools.py scan-flow artifacts\live\DEFAULT_MM_fixed.MBA --start-word 0x800 --count-words 0x80 --base-address 0x7800
```

Example early `MM.MBA` results:

- `0x801c`: `jne 0x8021`
- `0x8039`: `call 0x223c1a`
- `0x806a`: `call 0x223c18`
- `0x807e`: `call 0x22480b`

This is enough to start locating initialization paths and choosing hook points.

Example early `UB.MBA` results:

- `0x8017`: `call 0x223c7c`
- `0x8039`: `call 0x223c82`
- `0x816c`: `goto 0x2249ee`
- `0x8715`: `goto 0x224f69`
- `0x87f0`: `goto 0x225056`

The current local workflow also includes a standalone `u'nSP` disassembler build in the ignored
`build\` tree:

- `build\unsp_dump.exe`

That is what made the `0x224800` absolute-address model and the first `UB.MBA` hook practical.

## Stage-0 `UB.MBA` hook

A reproducible first-stage homebrew patch builder now exists:

```powershell
python tools\build_ub_widget_poc.py
```

Current behavior:

- hook point: file offset `0x1732`
- original instruction replaced: `call 0x223c0e`
- trampoline: `goto 0x22b9fb`
- code cave: `0xE3F6..0xF000`
- cave absolute address: `0x22B9FB`

The injected stub:

1. executes the original `call 0x223c0e`
2. restores the original `sp += 6`
3. performs a second custom `223c0e` call with configurable arguments
4. returns to the original flow at `0x22539c`

Default builder parameters:

- resource id: `0xC00C`
- argument 3: `0x0020`
- argument 4: `0x00C0`

Current artifact:

- `artifacts\poc\UB_widget_poc.MBA`

This is not a text-rendering payload yet, but it is a real arbitrary-code cave hook in the live
`UB.MBA` image shape. The remaining deployment problem is transport, not patch construction.

## Delivery and recovery status

Working:

- live read of logical `A:\DEFAULT\...` files
- direct flash read through `VTech2010USBDllU.dll`
- full flash backup already captured

Partially ready:

- mailbox write path exists but still carries one unknown `0x0D` metadata field
- vendor helper now has a `DLL_LSWriteFlash` wrapper in source, but it still needs cautious live
  validation before relying on it as a recovery mechanism
- the currently connected device is not staying in transfer-ready mailbox mode: path queries return
  `0xffff` and raw volume reads intermittently fail with `The device is not ready`
- the capture-backed upload extractor now shows one real upload sequence for `A:\PHO\00000013.PHO`:
  - pre-write `meta16 = 0x004f`
  - post-write `meta16 = 0x166d`
  - final size `0x27f08`
  - payload starts with `_inF` / `DLCv 1.0` / `info`

Important DLL finding on `0x0D`:

- the desktop function that emits opcode `0x0D` only receives the file handle explicitly
- assembly shows it writes `DX` from the handle and then stores full `EDX`, leaving the upper 16
  bits inherited from prior register state
- practical reading: the extra `meta16` value is probably not a deliberate API input, and may be
  ignored by the device-side handler

## Shortest visible PoC path

The shortest low-risk visible change is probably not a full custom app first. The current best
options are:

1. Patch `UB.MBA` metadata or early code and trigger it by entering USB mode.
2. Patch `MM.MBA` mirrored code and trigger it from the main menu.
3. Use `CG`/`CS`/`WT` asset replacements for a visible proof while the code path is still being
   mapped.

For real code execution, `UB.MBA` is the better first target:

- smaller file
- clearer role (`USB APP`)
- obvious slack space for a stub
- fewer `retf` sites than `MM.MBA`

## Immediate next tasks

1. Recover one text or label path well enough to replace the current widget-only PoC with a real
   on-screen string payload.
2. Either get the handheld back into transfer-ready mailbox mode or finish mapping `UB.MBA` into
   the flash-side patch container so the vendor flash path can deploy the patch.
3. Validate a restore-capable live write path before touching the installed `UB.MBA`.
