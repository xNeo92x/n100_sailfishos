# Complete pre-extracted vendor build candidate

The BE2013 collection returned 487 of 1,161 requested files, leaving 674 missing
(672 under vendor and two under system_ext). Forty-three readable files differ
from raw reference pins. The old collector discarded ADB stderr; the submitted
console output therefore cannot distinguish denied reads from absent paths or
transport failures. Repeating the same collection was not an adequate remedy.

The host check passed: x86_64 Linux, btrfs, 1,401.7 GiB free, 30.3 GiB RAM and
16 CPU threads. Storage/RAM are not the observed blocker.

## Why a stock dump cannot supply every reference

The pinned device list explicitly labels its WiFi Display system section:
`from lemonadep - SKQ1.210821.001`. Those donor-device files are not ordinary
N100 stock inputs. A raw N100 dump's SHA-1 differences are therefore not solely
an EEA/global regional issue. No raw-reference pins or ELF fixups are disabled
to force that dump through extraction.

## Separate vendor dataset

The alternative is the billie2-specific TheMuppets dataset, already extracted
and carrying its Android build files. Its identity is pinned separately in
[`config/vendor-source.json`](../config/vendor-source.json):

| Input | Selected identity |
| --- | --- |
| Repository | `TheMuppets/proprietary_vendor_oneplus_billie2` |
| Branch | `lineage-20` |
| Commit | `20645052cd9a7b9907294c334cf23c4e0796ea58` |
| Matched device-tree commit | `b2bde47cb6d0d737136915d65feac5cffacb1d53` |
| Required blob paths present | 1,161 / 1,161 |
| Build files present | `Android.bp`, `Android.mk`, `BoardConfigVendor.mk`, `billie2-vendor.mk` |
| Blob contents | 503,073,022 bytes |
| Raw-reference pins matched | 34 / 46 |
| Declared packaged/reference differences | 12 WiFi Display files |
| Unresolved Git LFS pointers | None |

The dataset's history identifies its stock update as OOS 11.0.15.BE81AA. The
vendor and device-tree heads both contain the matching CACertService addition.
This is evidence for selecting a build candidate, not certification for the
EEA handset's firmware or Sailfish runtime.

The twelve WiFi Display differences are **known content differences**, not
proved equivalent transformations of the raw reference files. Their actual
SHA-1/SHA-256 values and original input references are recorded explicitly.
They are retained as part of this entire pinned, pre-extracted dataset. No
files from the partial stock dump are spliced into it. Their functionality
and ABI compatibility remain untested; file completeness must not be mistaken
for a successful HAL build or working WiFi Display.

## Fetch and install

On the host, without ADB or a connected phone:

```bash
python3 tools/firmware.py --work-dir "$HOME/n100-build" vendor-source
```

After initializing/preparing the Android tree, inside HABUILD_SDK:

```bash
source "$HOME/n100-build/build.env"
python3 "$N100_PORT/tools/firmware.py" --work-dir "$N100_FIRMWARE_WORK" \
  vendor-source --android-root "$ANDROID_ROOT"
```

The first command caches the pinned repository and writes a vendor-source audit.
The second adds a detached Git worktree at `vendor/oneplus/billie2`. Existing
matching worktrees can be checked again; differing outputs are not overwritten.
Both commands check every tracked file's bytes/size against the fixed canonical
SHA-256 inventory. Unexpected reference differences, edits, missing files,
symlinks and unresolved LFS pointers stop validation. The whole inventory hash
also binds the build makefiles, not just the binaries.

Before compilation `build-hal.sh` rechecks a vendor-source installation marked
by `.n100-vendor-source.json`. That marker binds the separate pin configuration;
changing it requires a new assessment. Do not run `extract-files.sh` or the raw
`import` command on the pre-extracted worktree.

## Validation boundary

[`validation/vendor-source.json`](../validation/vendor-source.json) records the
real downloaded dataset check. Installing it as a worktree in a fixture Android
directory, verifying it and repeating the command were exercised locally.
This does not constitute a full Android source sync, SDK setup, HAL build or
hardware boot. The next stages remain those in [CACHYOS_BUILD.md](CACHYOS_BUILD.md).

The raw ADB collector now retains stdout/stderr/exit code and classifies failure
reasons. It prints counts and saves a detailed verification file. Known
permanent permission/path failures require `collect --retry-failed` to retry;
the pre-extracted source path does not require another collection.

## Primary source references

- [Pinned device proprietary list](https://github.com/LineageOS/android_device_oneplus_billie2/blob/b2bde47cb6d0d737136915d65feac5cffacb1d53/proprietary-files.txt)
- [Pinned vendor snapshot](https://github.com/TheMuppets/proprietary_vendor_oneplus_billie2/tree/20645052cd9a7b9907294c334cf23c4e0796ea58)
- [Vendor stock-update commit](https://github.com/TheMuppets/proprietary_vendor_oneplus_billie2/commit/93a9c06463614bd16481bcde0d8588e82780e7b1)
