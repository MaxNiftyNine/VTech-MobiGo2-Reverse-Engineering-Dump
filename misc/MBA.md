# MobiGo MBA Format Reverse-Engineering Notes

## Scope

This write-up documents the MobiGo `.MBA` format as observed in:

- the eleven system files in this directory
- the installed titles under `../repo/misc/live_tree_dump_20260314_201609/A/USENG`
- the live patch container `DEFAULT_MGB_PTCH.BIN`
- the older tooling and disassembly notes in `../repo/misc/mobigo_re`
- the Generalplus `u'nSP` toolchain PDFs in `../repo/misc/Doc.zip`

The target is the **file format actually used by MobiGo**, not the generic `u'nSP` ISA. The payload is `u'nSP`, but the file is a loader/container wrapper around that payload.

## Executive Summary

`MBA` is not a raw flat `u'nSP` binary. The common executable flavor starts with the 8-byte magic `bM_gbMQa`, then a fixed metadata block, then a linked `u'nSP` payload with embedded code, tables, and resources.

High-confidence solved fields:

- `0x00..0x07`: magic `bM_gbMQa`
- `0x08..0x0B`: logical file size in **16-bit words**
- `0x0E..0x0F`: 16-bit version number
- `0x14..0x17`: entry point as an absolute `u'nSP` word address
- `0x18..0x1B`: base/load address as an absolute `u'nSP` word address
- `0x3C..0x3D`: header CRC (`CRC-16/CCITT`, seed `0xFFFF`)
- `0x80..0x9F`: ASCII title string

Medium-confidence findings:

- `0x10..0x13` is a module/content identifier
- `0x40..0x5F` is an optional loader bitmap area used by some titles
- `0x1C..0x24` stores loader geometry/state for the paged variant

Still unsolved:

- the low 16 bits of the dword at `0x0C`
- the exact semantics of `0x1C`, `0x20`, and `0x24`
- the exact meaning of every word in the bitmap/loader area for titles that leave it zeroed

## Observed Families

I only saw one executable magic in normal `.MBA` files:

- `bM_gbMQa`: executable MBA wrapper

I also found a related container family inside the patch/update ecosystem:

- `bM_gdSQl`: APP0-like container blob
- `MGB_ptchZ`: outer patch wrapper
- `JFS0`: section table used inside `MGB_ptchZ`

So "MBA format" on MobiGo really means "one member of a broader `bM_*` container family".

## Fixed Header Layout

All normal `.MBA` samples in this set begin with the same fixed header shape:

| Offset | Size | Meaning | Confidence | Notes |
| --- | ---: | --- | --- | --- |
| `0x00` | 8 | magic | High | `bM_gbMQa` |
| `0x08` | 4 | logical size in words | High | `size_bytes = dword * 2` |
| `0x0C` | 4 | `low16 = unknown`, `high16 = version` | High for split, low for low16 meaning | `downloads.json` uses `sVerOffset=14`, which lands on the upper halfword |
| `0x10` | 4 | module/content id | Medium | system titles use `0x000F3E5A..0x000F3E62`; user titles use `0x42..0x47` |
| `0x14` | 4 | entry address | High | absolute `u'nSP` word address |
| `0x18` | 4 | base/load address | High | absolute `u'nSP` word address |
| `0x1C` | 4 | loader geometry/state | Medium | `0x000C00DE` on many installed titles, `0x0000FFFF` on bundle-like titles |
| `0x20` | 4 | loader field | Low | varies by title class |
| `0x24` | 4 | loader field | Low | often `0x002800EA` on installed titles |
| `0x28` | 0x14 | reserved/unknown | Low | title-dependent, often zero |
| `0x3C` | 2 | header CRC | High | CRC over `0x00..0x3B` with the CRC field zeroed |
| `0x3E` | 2 | padding/unknown | Medium | zero in all checked samples |
| `0x40` | 0x20 | optional bitmap/state area | Medium | some titles use it heavily, others leave it zero |
| `0x60` | 0x20 | reserved/padding | Medium | usually zero |
| `0x80` | 0x20 | title | High | null-terminated ASCII |
| `0xA0` | ... | payload | High | linked `u'nSP` image with data/resources mixed in |

## Solved Fields

### 1. File size at `0x08`

This one is straightforward and fully verified:

- `USENG_UB.MBA`: `0x00009800 * 2 = 0x13000`
- `USENG_MM.MBA`: `0x00020800 * 2 = 0x41000`
- `BUNDLE_G1_135800G1.MBA`: `0x0010A000 * 2 = 0x214000`

The value matches the logical MBA size for every sample I checked.

### 2. Version in the upper half of `0x0C`

`downloads.json` says every MBA has `sHasVer=true|sVerOffset=14`. The offset is decimal, so it points at bytes `0x0E..0x0F`, which are the **upper halfword** of the dword at `0x0C`.

Examples:

- `USENG_UB.MBA`: `0x000428F8` -> version `4`
- `USENG_MM.MBA`: `0x000769AB` -> version `7`
- `BUNDLE_SY_135800SY.MBA`: `0x0005387A` -> version `5`
- `BUNDLE_LD_135800LD.MBA`: `0x0001C25E` -> version `1`

The lower 16 bits of the same dword are not solved yet. They do **not** match the header CRC, full-file CRC16, CRC32, Adler-32, or a trivial 16-bit sum.

### 3. Entry point at `0x14`

`0x14` behaves like an absolute entry point in `u'nSP` word-address space.

For the installed modules:

- `USENG_UB.MBA`: base `0x224800`, entry `0x226261`, derived file offset `0x34C2`
- `USENG_MM.MBA`: base `0x224800`, entry `0x22D8A5`, derived file offset `0x1214A`

Those offsets land inside real code. Existing local disassembly around those addresses is coherent.

For the loading app:

- `BUNDLE_LD_135800LD.MBA`: base `0x0C8800`, entry `0x0C889A`, derived file offset `0x134`

That one is especially useful because it shows the entry point can sit very near the front of the payload and is not tied to `0x1000`.

### 4. Base/load address at `0x18`

`0x18` is the absolute base address used to translate file offsets into runtime addresses:

`runtime_word_addr = base + (file_offset / 2)`

This is strongly confirmed by the older patch tooling:

- `USENG_UB.MBA` uses base `0x224800`
- a patched hook at file offset `0x1774` corresponds to runtime address `0x2253BA`
- the older UB patch notes and disassembly operate in that exact address range

Observed base classes:

- `0x224800`: installed menu/USB modules and several installed user titles
- `0x0C8800`: bundle/update-style titles and the nested `APP0` MBA inside the patch container

## Two Practical MBA Subtypes

### Installed/app-slot style

This class includes:

- `USENG_MM.MBA`
- `USENG_UB.MBA`
- installed titles `00000066.MBA` through `00000071.MBA`

Common traits:

- base address `0x224800`
- `field_1C = 0x000C00DE`
- `field_24 = 0x002800EA`
- non-`0xFFFF` loader fields
- optional non-zero bitmap/state words at `0x40..0x5F`

### Bundle/update style

This class includes:

- `BUNDLE_G1/G2/G3/G4`
- `BUNDLE_LD`
- `BUNDLE_SY`
- `BUNDLE_TM`
- `USENG_EBOOK.MBA`
- nested `APP0` found inside `DEFAULT_MGB_PTCH.BIN`

Common traits:

- base address `0x0C8800`
- loader fields often use `0x0000FFFF` sentinels
- the `0x40..0x5F` area is zero in the samples I checked

So there is not one monolithic MBA layout in practice. There is a shared header format with at least two loader profiles.

## The Bitmap Area at `0x40..0x5F`

This is the most interesting "more than just a binary" part of the format.

When populated, the bitmap area behaves like a **4 KiB chunk map**:

- `USENG_UB.MBA`: size `0x13000` = 19 pages, bitmap has 19 set bits
- `USENG_MM.MBA`: size `0x41000` = 65 pages, bitmap has 65 set bits
- `00000066.MBA`: size `0x4B000` = 75 pages, bitmap has 75 set bits
- `00000068.MBA`: size `0x4B000` = 75 pages, bitmap has 75 set bits
- `00000070.MBA`: size `0x41000` = 65 pages, bitmap has 65 set bits
- `00000071.MBA`: size `0x49000` = 73 pages, bitmap has 73 set bits

Examples of the actual ranges:

- `USENG_UB.MBA`: bits `0..10`, `188..191`, `218..221`
- `USENG_MM.MBA`: bits `0..26`, `158..191`, `218..221`
- `00000066.MBA`: bits `0..30`, `152..171`, `198..221`

Why I think this is chunk/page metadata:

1. The bit count exactly matches `size_bytes / 0x1000` when present.
2. The set bits are sparse and non-contiguous, which makes no sense as a plain "this file is contiguous" flag.
3. `field_1C = 0x000C00DE` is consistent with a geometry-like encoding: `0x0C` looks like a 4 KiB shift and `0x00DE` equals 222, which matches the highest observed set-bit index plus one.

Important caveat:

- `00000067.MBA` and `00000069.MBA` leave this area zero even though they are the same general title class.

So the safest statement is:

- the bitmap area is real
- when populated it clearly describes 4 KiB chunk allocation
- some titles omit it or use a different loader mode

## Header CRC at `0x3C`

This is fully solved and easy to verify.

Algorithm:

- CRC: `CRC-16/CCITT`
- polynomial: `0x1021`
- init: `0xFFFF`
- input range: bytes `0x00..0x3B`
- word byte order: low byte first, then high byte
- treat bytes `0x3C..0x3D` as zero during the calculation

Verified examples:

- `USENG_UB.MBA` -> stored `0xDE84`
- `USENG_MM.MBA` -> stored `0xAA87`
- `BUNDLE_G1_135800G1.MBA` -> stored `0xA4D3`

The included `verify_mba.py` recomputes this value.

## Where the Real Payload Starts

The fixed, human-readable metadata header ends at `0x9F`.

I would **not** describe everything up to `0x1000` as "just header", because the files do not support that. What I actually observed is:

- title always lives at `0x80..0x9F`
- non-trivial payload bytes begin at `0xA0`
- direct control-flow opcodes already appear near the front of some files
- denser code/data regions are definitely present by `0x1000`

So the right mental model is:

- `0x00..0x9F`: fixed MBA metadata
- `0xA0..EOF`: linked image, which may begin with startup tables, init data, and code before the first obviously dense code block

This also matches the Generalplus tooling model from `Doc.zip`: the standard linker emits a raw binary image, and the resource tools can inject data sections and generated resource tables into that image. MBA looks like VTech's wrapper around that kind of linked output.

## Why MBA Is "More Than Just Binary"

The payload is `u'nSP`, but the file format adds at least five things that matter to the loader:

1. A logical size field in words.
2. A version field that the desktop updater explicitly reads.
3. An absolute entry point and base address.
4. Optional chunk/allocation metadata in the `0x40..0x5F` region.
5. Per-title metadata such as title strings and module ids.

That means an MBA is closer to a small executable container than to a naked ROM dump.

## Related Containers Inside `MGB_PTCH.BIN`

`DEFAULT_MGB_PTCH.BIN` is not itself an MBA, but it proves the ecosystem around MBA is layered:

- outer magic: `MGB_ptchZ`
- inner section table: `JFS0`
- embedded `bM_gdSQl` containers
- embedded `bM_gbMQa` executable titled `APP0`

Verified hits inside `DEFAULT_MGB_PTCH.BIN`:

- `bM_gdSQl` at `0x658D8`
- `bM_gdSQl` at `0xAD498`
- `bM_gbMQa` at `0xE84D8` with title `APP0`

So MBA is one layer in a larger VTech container stack, not the whole update story.

## Verification Commands

The older repo already contains useful tools:

```powershell
python ..\repo\misc\mobigo_re\tools\mba_tool.py scan ..\repo\misc\live_tree_dump_20260314_201609\A --glob *.MBA
python ..\repo\misc\mobigo_re\tools\unsp_tools.py scan-flow USENG_UB.MBA --start-word 0x1A61 --count-words 0x80 --base-address 0x224800
```

I also added a local verifier for the solved parts:

```powershell
python .\verify_mba.py summary
python .\verify_mba.py info .\USENG_UB.MBA
python .\verify_mba.py find .\DEFAULT_MGB_PTCH.BIN
```

What the verifier checks:

- size-in-words conversion
- version split from `0x0C`
- entry/base address model
- header CRC recomputation
- bitmap bit counts

## Current Best Interpretation

The most defensible reconstruction is:

1. `bM_gbMQa` is the normal MobiGo executable wrapper.
2. The wrapper stores loader metadata, not just a title and checksum.
3. The wrapped payload is a linked `u'nSP` image with code and resources mixed together.
4. Some titles use an additional 4 KiB chunk map in the header, implying a sparse install/layout model.
5. The patch/update path wraps these executables again inside `bM_gdSQl`, `JFS0`, and `MGB_ptchZ` containers.

## Unknowns Worth Pursuing Next

- Identify the exact semantics of the low 16 bits at `0x0C`.
- Fully decode the installed-title chunk map and relate the bit positions to flash or patch-body storage.
- Explain why some installed titles populate the bitmap area while others leave it zero.
- Reverse the `bM_gdSQl` header separately and map how it relates to nested `APP0`.
- Work out whether `0x20` and `0x24` are geometry fields, slot bounds, or something closer to capability flags.
