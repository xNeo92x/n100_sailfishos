# BE2013: first stock-device report

The user supplied a read-only ADB report from the actual phone. A reduced
board/firmware fixture is checked in under `tests/fixtures/`; the original report
is not published. The machine-readable assessment is
[`devices/be2013-stock11-assessment.json`](../devices/be2013-stock11-assessment.json).

## Observed facts

| Item | Observation |
| --- | --- |
| Product / model | OnePlusN100 / BE2013 |
| Product region | EEA, from the stock build fingerprint |
| Platform / hardware | bengal / qcom |
| Android version / SDK | 11 / 30 |
| Build fingerprint | OnePlus/OnePlusN100_EEA/OnePlusN100:11/RKQ1.201217.002/2310092135:user/release-keys |
| Vendor fingerprint | Same as the build fingerprint |
| Running kernel | 4.19.152-perf+, aarch64, built October 2023 |
| Slot at collection | `_b` |
| Bootloader / verification | `ro.boot.flash.locked=1`, `verifiedbootstate=green` |
| Data | encrypted / file, F2FS mounted with inlinecrypt |
| Android root | verity mapping, ext4 |
| Other logical mounts | system_ext, vendor, product |
| Physical super / metadata / userdata | sda10 / sda15 / sda16 in this report |
| Separate recovery slots | recovery_a and recovery_b exist |

The collector also observed `system_a/b`, `system_ext_a/b`, `vendor_a/b` and
`product_a/b` in `/dev/block/mapper`. Both-slot mappings being present does not
mean both slots are bootable or use identical firmware.

The live `/data` source is `/dev/block/dm-12`, while physical `userdata` is
`/dev/block/sda16`. These are different namespaces. The analyzer explicitly
keeps physical by-name aliases separate from mapper aliases so one cannot
overwrite the other. `dm-*` numbers and the active slot are session observations,
never values to hardcode into the port's boot scripts.

## Consequences for the source base

The original Android-10 source combination is unsuitable for reusing this phone's
installed Android-11 baseline. The new **development candidate** is:

- Android HAL build: `mer-hybris/android`, `hybris-20.0` (Android 13).
- Device: official `LineageOS/android_device_oneplus_billie2`, `lineage-20`.
- Kernel: matching Lineage SM4250 source, with the device's
  `OEM_TARGET_PRODUCT` selection retained.
- OnePlus-specific hardware sources: included as required by `lineage.dependencies`.
- Proprietary baseline: Android 11, subject to exact EEA blob and fixup verification.

The inspected Lineage product explicitly uses an Android-11 fingerprint with
build `2310091801`, while the EEA phone reports `2310092135`. Its blob list names
global `11.0.15.BE81AA`. This is evidence of the same generation, not proof that
the two regional binary payloads are interchangeable. Do not change the phone's
firmware merely to match the global fingerprint embedded in that source file.

The new kernel source identifies itself as 4.19.325, versus stock 4.19.152. A
compatible set of kernel/modules/DTB/DTBO must be built and tested together.
The source tree's expected boot header v2 / 4096-byte pages and partition sizes
still require comparison against real stock image headers; the report contains
block names but no actual partition sizes or image dumps.

The updated RPM sparse selection uses `android_version_major 13`, referring to
the **HAL build**, not the installed stock firmware's version. Logical
`system_ext` and ODM mounts are excluded from legacy physical-mount generation
alongside system/vendor/product. Their replacement remains unimplemented.

## Diagnostic gap and correction

The old script ran `cat /vendor/etc/fstab.qcom /odm/etc/fstab.qcom` as one command
and discarded its captured stdout when the exit code was nonzero. Consequently,
the first report cannot tell us whether the first file was readable and the
second missing, or whether both reads failed. This was a collector limitation,
not evidence that the stock fstab is absent.

The updated script reads candidates separately, preserves stdout/stderr/return
code on failures and adds firmware display/incremental values, vendor SDK,
VINTF manifest, SELinux version, partition sizes and mapper names. Paths denied
by stock SELinux remain marked unavailable; no root or unlock is attempted.

From the existing clone, with Android running and ADB authorized:

```bash
git pull --ff-only
python3 tools/n100.py probe --output ../n100-device-report-v2.json
python3 tools/n100.py analyze-report ../n100-device-report-v2.json
```

If the report filename already exists, `probe` creates a timestamped sibling
instead and prints its absolute path. Use that printed filename for analysis
and upload, so an older report is not accidentally reused. Do not delete the
old report just to collect another one.

The next inputs are the complete stock fstab/VINTF details and ultimately the
matching stock filesystem/boot image files for proprietary extraction and boot
header comparison. A report containing filenames and mount points is not a
substitute for those binaries. There is still no image to flash or validate on
this device; leave Android running for the read-only collection.
