# MobiGo Flash Dump Analysis

## Scope

This document analyzes the live flash dump:

- file: `artifacts/live/mobigo_flash_64m_20260313.bin`
- size: `0x04000000` bytes (`67108864`)
- SHA-256: `CDB29C3C416ED602724678A6B6C163EF087276E9A6D67DD7A0140933BD9C90D8`

The goal here is not just to describe raw bytes, but to answer the practical question: what kind of software stack is on the console, how the USB-visible storage relates to flash, and what the major on-flash regions appear to do.

## Executive summary

The dump does not look like a general-purpose OS image. There is no evidence of a Linux-style stack, no ELF/uImage/squashfs signatures, no kernel strings, and no ordinary filesystem tree governing the device at boot.

What the dump does show is:

- a monolithic embedded firmware layout
- a lower flash area dominated by repeated status/config pages
- one or more opaque executable/data regions
- embedded UI resources, including on-screen keyboard tables and USB status strings
- an embedded FAT16 volume image used for the USB Mass Storage export
- a custom content/index layer inside that volume, not a real FAT-managed directory tree
- strong vendor/platform indicators pointing to a Generalplus/Sunplus USB mass-storage stack

The working model is:

1. Native firmware boots from dedicated flash regions, not from the FAT volume.
2. That firmware exposes a synthetic or semi-synthetic USB Mass Storage disk.
3. The FAT structures on that disk are mostly decoys.
4. Real content management is handled by custom package/index structures embedded inside the exported volume and by direct flash access through the vendor DLL transport.

## High-confidence findings

### 1. This is not a normal OS image

Observed negatives:

- no ELF headers
- no uImage headers
- no ZIP/7z/XZ/LZMA-alone containers
- no obvious Linux strings
- no standard root filesystem signatures
- no normal FAT directory tree controlling the device

Observed positives:

- embedded UTF-16 UI strings
- embedded keyboard layouts
- proprietary package headers
- a vendor-specific USB storage descriptor block

That combination is much more consistent with a custom appliance firmware than with a small PC-style OS.

### 2. The USB-visible disk is embedded inside flash, but not as a literal raw disk image

The FAT16 boot sector is present inside flash at absolute offset `0x007AE632`.

Boot sector fields:

- OEM: `MSWIN4.1`
- bytes/sector: `512`
- sectors/cluster: `1`
- reserved sectors: `8`
- FAT count: `2`
- root entries: `512`
- sectors/FAT: `64`
- total sectors: `16560`
- volume label: `NO NAME`
- filesystem label: `FAT16`

Important offsets inside flash:

| Item | Absolute flash offset | Relative to embedded volume |
|---|---:|---:|
| Boot sector | `0x7AE632` | `0x000000` |
| Descriptor blob after boot | `0x7AE832` | `0x000200` |
| FAT #1 | `0x7AF632` | `0x001000` |
| FAT #2 | `0x7B7632` | `0x009000` |
| Root directory | `0x7BF632` | `0x011000` |
| Data area start | `0x7C3632` | `0x015000` |

The embedded volume length implied by the BPB is:

- `16560 * 512 = 0x00816000` bytes

So the embedded volume spans:

- start: `0x007AE632`
- end: `0x00FC4632`

### 3. The FAT structures are effectively decoys even inside flash

The boot sector is valid, but both FAT copies and the root directory are not populated like a normal filesystem:

- FAT #1 samples at `0x7AF632`, `0x7AF832`, `0x7AFA32` are all zero
- FAT #2 samples at `0x7B7632`, `0x7B7832` are all zero
- root directory scan from `0x7BF632` found no normal active entries

This matters because it confirms that the USB-visible FAT volume is not the device’s true content database.

The console is presenting a FAT16 shell, but the actual file/content logic is elsewhere.

### 4. The raw flash does contain the exported volume, but the host-visible disk is partly synthesized

The boot sector in flash matches the USB-exported FAT boot sector, but the surrounding structure does not match the raw physical-drive dump as a byte-for-byte carved disk:

- the embedded flash volume starts directly at a boot sector
- the host-visible physical drive had an MBR/gap before the boot sector
- the flash bytes before the embedded boot sector are not an MBR

Conclusion:

- the firmware stores the exported partition image in flash
- the USB disk that Windows sees is not just a direct block-for-block flash mapping
- the MBR/partition framing is at least partly synthesized by the firmware’s storage layer

## Flash region map

The dump naturally breaks into several regions.

### Region A: repeated status/config page area

- range: `0x000000` to `0x07FFFF`
- structure: identical `0x400`-byte records repeated across the entire region

Each `0x400`-byte record appears to consist of:

- first `0x200` bytes: identical info block
- second `0x200` bytes: identical metadata block

Examples:

- info block head at `0x000000`: `aa00086e6553556700581150e000a32d...`
- metadata block head at `0x000200`: `0980dbc3bd0100000000050014000500...`

This region is not executable-looking. It behaves like a repeated mirror page or factory/config/status area.

One useful point: this same `aa 00 08 6e ...` block is what also appeared during live `DLL_LSReadFlash(0)` reads and in the mailbox sectors during earlier probing. That strongly suggests it is a canonical device-info/status structure, not random flash residue.

### Region B: opaque firmware/data region

- range: roughly `0x080000` to `0x27FFFF`

This region has low entropy but is not empty. It looks like structured binary data or code for a non-self-identifying architecture.

Notable facts:

- no conventional executable container signature
- many word-like repeating patterns
- no long readable strings

This is a strong candidate for main firmware code and/or tightly packed binary tables.

### Region C: sentinel-filled area

- range: roughly `0x280000` to `0x67FFFF`
- typical 64 KiB block head: `ffff000000000000...`

These blocks are not all `0xFF`; instead they are mostly:

- first bytes: `FF FF 00 00`
- remainder: zero

That makes them look more like firmware-level sentinel pages than erased NAND.

Best interpretation:

- reserved/unmapped address range
- invalid page fill returned by the vendor transport
- intentionally sparse logical region

I would not treat this range as meaningful application content.

### Region D: upper mixed firmware/resource region

- range: roughly `0x680000` to `0x7AE631`

This is one of the most interesting parts of the dump. It contains:

- opaque binary/code-like material
- package-like headers
- version strings
- keyboard layout tables

Key strings in this region:

- `GLB_GP-F_4A_USBD_1.0.0` at `0x79F1DB`
- `PGpssiippsD` at `0x700000`
- `gM_BaNdn` at `0x704004`

The `GLB_GP-F_4A_USBD_1.0.0` string is especially important because it looks like a component/module identifier for the USB device/storage layer rather than game content.

### Region E: embedded exported volume

- range: `0x7AE632` to `0xFC4631`

This region contains:

- FAT16 boot sector
- USB descriptor/product strings
- custom package/index structures
- user-visible status strings
- sparse content data

This is the flash-resident backing store for the device’s USB-exported disk.

## Vendor/platform evidence

Several strings tie the USB/storage layer to Generalplus/Sunplus:

- `GENERALPLUS`
- `GENPLUS`
- `Sunplus USB-MSDC DISK A 1.00GP162002`
- `VTECH   USB-MSDC DISK A 1.00GP-PROD.`
- `GLBVT4AUSB101100G`
- `GLB_GP-F_4A_USBD_1.0.0`

These strings appear around `0x7AE832` through `0x7AEEC0` and around `0x79F1DB`.

Strong inference:

- the console uses a Generalplus/Sunplus USB Mass Storage implementation
- the VTech-visible disk is layered on top of that vendor USB block-storage stack

I am deliberately not naming a precise CPU family from this alone, because the dump does not expose an unambiguous executable format or vector table. The vendor/storage provenance is strong; the exact core architecture is not yet proven from the flash dump alone.

## UI/resource evidence

### Keyboard tables

At `0x73CF20` and nearby, the dump contains UTF-16 keyboard layout tables.

Observed entries include:

- `0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz`
- `WERTYUIOPASDFGHJKL`
- `ZXCVBNM`
- `BCDEFGHIJKLMNOPQRS`
- `TUVWXYZ`
- `WERTZUIOPASDFGHJKL`

This is clear evidence that:

- the firmware contains its own UI resource tables
- the device has an on-screen keyboard layer
- the firmware likely supports multiple layouts/locales directly, not via an external OS

### USB and file-operation status strings

At `0x7D0DC2` and nearby:

- `USB DISCONNECTED`
- `USB CONNECTED`
- `USB BUSY`
- `USB FINISHED`
- `No Files`
- `Delete File Failed`
- `Delete Folder Failed`

These are internal UI strings, not host-side software strings. They indicate:

- the console tracks USB state internally
- it has a local file-management UI
- it can delete files/folders from its own content store

That fits a monolithic firmware with a built-in file browser/content manager.

## Custom content/index structures inside the exported volume

The most important conclusion from the embedded volume is that it is not managed through ordinary FAT directory entries.

Instead, there are custom structures in the data area.

### MM.MBA-like header

At absolute `0x7C7C00` (volume-relative `0x195CE`) there is a header beginning:

```text
62 4D 5F 67 62 4D 51 61
```

ASCII:

```text
bM_gbMQa
```

That matches the signature used by the vendor `ComBin2\\MM.MBA` file, but the numeric fields differ from the static vendor copy.

Vendor file header:

- signature: `bM_gbMQa`
- field set begins with `0x00021800`, `0x000445C0`, `0x000F3E5A`, ...

On-flash header:

- same signature
- field set begins with `0x00007800`, `0x0003BCFB`, `0x000F3E59`, ...

Interpretation:

- the console stores an installed/runtime variant of the package header
- it is related to the vendor `MM.MBA` package format
- it is not just a raw copy of the update payload

### APP0-format headers

The `APP0` file format signature from the vendor update package appears three times in flash:

- `0x6BA000`
- `0x745000`
- `0x78CBC0`

Each begins with:

```text
bM_gdSQl
```

Two are byte-identical for at least the first `0x80` bytes:

- `0x6BA000`
- `0x745000`

The third at `0x78CBC0` shares the same header but diverges immediately after the first fixed fields.

Interpretation:

- the device likely maintains multiple APP0-related instances, copies, or stages
- at least part of the application/package management layer is duplicated or mirrored

### Manifest/check strings

At `0x7D0AA8` onward there is a UTF-16 manifest/check area containing:

- `CHECK BLOCK 00`
- `CHECK CG`
- `CHECK CS`
- `CHECK WT`
- `CHECK APP0`
- `CHECK DONE`
- `DEFAULT\CG`
- `DEFAULT\CS`
- `DEFAULT\WT`
- `DEFAULT\MGB_PTCH.BIN`
- `\MM.MBA`
- `\UB.MBA`
- `ETC\DMODE`
- `BUNDLE\SY`
- `BUNDLE\LD`
- `NO MM.MBA FOUND`

This is one of the most informative regions in the dump.

It strongly suggests a content-install/validation workflow roughly like:

1. check required component blocks
2. validate or locate default component packages (`CG`, `CS`, `WT`, `APP0`)
3. locate `MM.MBA` / `UB.MBA`
4. interact with `ETC\DMODE`
5. enumerate or mount bundle directories like `BUNDLE\SY` and `BUNDLE\LD`

That is exactly the kind of logic you would expect in a firmware-managed application/content loader, not in a normal FAT filesystem driver.

## Relationship to vendor update files

Comparison against `VTech\\DownloadManager\\Applications\\MobiGo_US_eng\\ComBin*` shows three different outcomes:

### Exact/near-exact evidence

- `AG` content begins at `0x8A8B90`
- the first `6688` bytes match the vendor `ComBin2\\AG` exactly
- a later 256-byte slice at vendor offset `0x1000` also matches at flash offset `0x8A9B90`

This shows at least part of the vendor payload is present in flash as stored content.

### Header-only or transformed evidence

- `APP0`
- `MM.MBA`
- `UB.MBA`

These do not appear as full raw contiguous vendor files, but their package signatures or path references do appear in flash.

Interpretation:

- these are either stored in a transformed format
- or only runtime headers/index records are stored plainly
- or the installed payloads are laid out non-contiguously

### Not found as plain contiguous payloads

- `JFS0.BIN`
- several other `ComBin` payloads

That does not prove absence. It only proves they are not present as a plain contiguous byte-for-byte copy in the current dump.

## Why the USB FAT volume is not the real filesystem

There are several independent reasons:

1. The FATs are empty/zeroed.
2. The root directory is empty.
3. Meaningful file/package names are stored elsewhere in custom data structures.
4. Some content begins at byte offsets that are not aligned to normal FAT entry semantics.
5. The host-visible MBR framing is synthesized rather than stored literally in flash.

So the exported volume is best understood as:

- a compatibility facade for host USB access
- backed by custom firmware logic
- with real content managed by proprietary tables and package headers

## What this says about “the OS”

The safest in-depth answer is:

- there is no sign of a general-purpose operating system
- the console appears to run a custom embedded firmware
- that firmware contains its own UI resources, file-operation logic, USB state machine, and content validation logic
- the USB mass-storage device is an interface layer over that firmware, not the console’s native storage abstraction

In other words, the “OS” here is probably just the firmware itself.

It likely includes:

- boot/startup code
- USB device/storage stack
- content/package manager
- file browser or file-action UI
- on-screen keyboard resources
- localization/resource tables

What is still unknown from this dump alone:

- exact CPU/core architecture
- exact boot vector location
- whether any code region is compressed/encrypted
- the full meaning of the `bM_...` package header fields

## Most likely boot/content model

This is the current best reconstruction:

1. Device powers up into native firmware from the low/mid flash regions.
2. Firmware mounts or interprets an internal custom content store.
3. Firmware exposes a USB Mass Storage disk using Generalplus/Sunplus USB logic.
4. The exposed FAT16 disk is mostly a shell around proprietary tables and package records.
5. The host-side VTech software can either:
   - talk to the USB-exported disk at the mailbox layer, or
   - bypass it and read flash directly via the vendor DLL transport.

That matches both:

- the packet capture analysis
- the vendor DLL call chain
- the live flash dump contents

## Practical implications for further reversing

If the next goal is deeper firmware RE, the most valuable next steps are:

1. Treat `0x080000` to roughly `0x27FFFF` and `0x680000` to `0x7AE631` as candidate firmware/code regions.
2. Define the `bM_gdSQl` and `bM_gbMQa` headers as actual structs and compare them against vendor package files.
3. Reverse the manifest/index area beginning around `0x7D0AA8`.
4. Carve the embedded volume from `0x7AE632` and study references between:
   - `APP0`
   - `MM.MBA`
   - `UB.MBA`
   - `ETC\DMODE`
   - `BUNDLE\SY`
   - `BUNDLE\LD`
5. Identify which parts of the flash are true code and which are data/resources by architecture-aware disassembly rather than signature scanning alone.

## Confidence notes

High confidence:

- embedded FAT16 volume location and BPB
- FAT/root being decoy or unused
- presence of Generalplus/Sunplus USB/storage strings
- presence of internal UI/resource strings
- presence of custom package headers
- monolithic firmware interpretation

Medium confidence:

- APP0/MM.MBA/UB.MBA being runtime-installed package/index records
- the sentinel-filled area being reserved/unmapped rather than meaningful content

Low confidence:

- exact CPU architecture
- exact semantics of the package header integers

