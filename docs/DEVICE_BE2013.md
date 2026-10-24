# BE2013: stock-device reports and configuration evidence

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
| Vendor SDK / VNDK | 30 / 30, confirmed by the V2 report |
| Firmware display / incremental | BE2013_7_231009 / 2310092135 |
| Framework and vendor security patch | 2023-10-05 |
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

## V2 findings

The V2 report confirms Vendor SDK 30, VNDK 30 and SELinux compatibility version
30.0. The latter is not a measurement of the compiled SELinux policy binary's
internal version. The rootfs encryption parameters are still unknown until the
actual stock fstabs are read.

The exact `fstab.qcom` candidates do not exist on this stock build. The listing
does expose `/vendor/etc/fstab.default`, `/vendor/etc/fstab.emmc` and
`/vendor/etc/fstab_nodata.qcom`. Their roles must be determined from their contents;
the nodata variant must not be assumed to be the normal boot fstab.

The main vendor VINTF manifest declares 63 HALs. Canonical comparison with the
pinned Lineage main manifest finds all 62 upstream declarations equivalent,
plus the stock-only `vendor.oneplus.hardware.display` declaration. The inventory
deduplicates the equivalent `<interface>` and `<fqname>` representations. It
does not validate runtime services, binary ABIs or manifest fragments. The
comparison is recorded in `devices/be2013-vintf-comparison.json`.

Relevant declarations include audio/effects 6.0, Bluetooth 1.0 and Bluetooth
audio 2.0, camera provider 2.4, radio config 1.1 and a vendor Egis fingerprint
interface. Hardware features must not be marked working based on declarations.
Several essential interfaces are represented by separate files, including
display composer/allocator, sensors 2.0, GNSS, fingerprint, vibrator, power and
Wi-Fi. Their filenames were listed but their contents were not collected by V2.

`/odm/etc/vintf/manifest_2.xml` and `manifest_8.xml` exist despite the absence of
a separate ODM mapper entry in the observed stock mount layout. A directory
path is not proof of a separate partition. `/proc/partitions` and input-device
details are explicitly denied by the current Android policy; no root bypass is
part of the collection.

The next collection obtains the actual files in one archive:

```bash
git pull --ff-only
python3 tools/n100.py collect-configs --output ../n100-configs.zip
```

It discovers the real stock fstab and vendor/ODM/system_ext VINTF XML files,
pulls them and the relevant SELinux policy/context files with ADB, and packages
them locally with `report.json` and a SHA-256 `inventory.json`. No phone writes,
root, reboot or unlock commands are used. A partial bundle remains useful:
unavailable files are recorded instead of being silently omitted. The archive
has per-file and aggregate size bounds, and existing archives are not overwritten.

This ZIP is configuration input, not a proprietary driver dump, boot image or
complete firmware backup. Matching stock binaries and an actual build/boot test
are still needed. Do not publish compiled policy or binary payloads into the
source repository automatically; retain these as local build inputs.
