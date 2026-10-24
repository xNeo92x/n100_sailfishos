# Source audit and port decisions

Reviewed on 2026-10-24. Exact revisions are in
[`sources.lock.json`](../sources.lock.json). The source profile is an
**unbuilt development candidate** for the reported BE2013, not a hardware release.

| Primary source | Role |
| --- | --- |
| [Official HADK](https://docs.sailfishos.org/Develop/HADK/) | SDK and adaptation workflow |
| [Sailfish architecture](https://docs.sailfishos.org/Reference/Architecture/) | Android BSP / libhybris / Sailfish layering |
| [hybris-20.0 manifest](https://github.com/mer-hybris/android/tree/1e982642f81b76b4fb7830baba06d9d8c756454d) | Android 13 HAL build base |
| [Official N100 device tree](https://github.com/LineageOS/android_device_oneplus_billie2/tree/b2bde47cb6d0d737136915d65feac5cffacb1d53) | bengal BoardConfig, product, extraction scripts and fstab |
| [Blob inventory](https://github.com/LineageOS/android_device_oneplus_billie2/blob/b2bde47cb6d0d737136915d65feac5cffacb1d53/proprietary-files.txt) | Explicitly identifies Android 11 stock version 11.0.15.BE81AA |
| [Product definition](https://github.com/LineageOS/android_device_oneplus_billie2/blob/b2bde47cb6d0d737136915d65feac5cffacb1d53/lineage_billie2.mk) | Android-11 fingerprint, build 2310091801 |
| [Device dependencies](https://github.com/LineageOS/android_device_oneplus_billie2/blob/b2bde47cb6d0d737136915d65feac5cffacb1d53/lineage.dependencies) | SM4250 kernel and OnePlus hardware repository |
| [Lineage SM4250 kernel](https://github.com/LineageOS/android_kernel_oneplus_sm4250/tree/1eade87e4a56ffc797cb508bc7322f6783d90f67) | Matching kernel baseline, version 4.19.325 at inspection |
| [OnePlus hardware sources](https://github.com/LineageOS/android_hardware_oneplus/tree/a2095be16213e3643c157d3534c4c24918a76144) | Device-specific hardware modules |
| [HADK FAQ](https://github.com/mer-hybris/hadk-faq/blob/master/README.rst) | General adaptation/debugging and base-specific caveats |
| [droid-hal-device](https://github.com/mer-hybris/droid-hal-device) | RPM macros and build helpers |
| [droid-hal-configs](https://github.com/mer-hybris/droid-hal-configs) | Sparse files and package templates |
| [droid-hal-version](https://github.com/mer-hybris/droid-hal-version) | hw-release / umbrella package macros |

## Why the initial Android-10 base was replaced

The actual device report identifies BE2013 / OnePlusN100 EEA, Android 11 SDK 30,
platform bengal, a 4.19.152-perf+ stock kernel, dynamic partitions and file-encrypted
F2FS userdata. The original hybris-17.1 / Android-10 source choice cannot simply
reuse that installed firmware.

There was no suitable lineage-18.1 branch in the N100 repositories inspected.
Rather than treating an Android-12 device tree as an Android-11 tree, the current
profile pairs the official LineageOS-20 device/kernel sources with hybris-20.0.
The device's own blob inventory and product fingerprint show that this Android-13
build was designed around Android-11 stock components. The inspected hybris
manifest includes the extract utilities, SM8250 CAF stack used by bengal and
legacy Qualcomm vendor sepolicy dependencies. Additional OnePlus hardware
sources required by the device dependency list are included explicitly.

This is a source-level rationale. It does not establish EEA binary compatibility,
successful compilation, correct early mounts or boot. The regional fingerprint
differs from the global stock fingerprint in the source. Do not imply that a
reported SDK 30 must be changed to SDK 33 on the handset: Android 13 is the HAL
build version; Android 11 is the candidate firmware baseline.

## Integration changes

The generated kernel config now has its own name,
`vendor/sailfish-billie2_defconfig`, derived from the pinned kernel's existing
`vendor/bengal-perf_defconfig`. The BoardConfig overlay selects it while retaining
`OEM_TARGET_PRODUCT=$(PRODUCT_DEVICE)` and all other inspected upstream settings.
The board blob and fstab blob hashes are recorded in the source lock.

RPM configuration selects Android-13 common sparse files. Logical
system/system_ext/vendor/product/odm mounts are excluded from the legacy
physical-mount converter. That exclusion does not implement their replacement.
An ODM image in the Lineage build is not evidence of a stock ODM mapper entry;
none was visible in the supplied live report.

The source initializer requires a matching stock-generation report and records
a lock digest. Prepare/HAL/RPM helpers reject stale profile markers, so an
Android-10 source tree cannot be silently reused. This does not validate blobs.

The report parser distinguishes physical by-name aliases from mapper aliases,
and the collector preserves partial stdout/stderr on nonzero exit codes.
See [DEVICE_BE2013.md](DEVICE_BE2013.md) for observations and remaining inputs.

## Verification boundary

Host tests, syntax checks and source inspection are available. The development
environment has about 28 GiB free, no attached N100, no proprietary firmware dump
and no installed Sailfish target. Full Android build, RPM resolution, rootfs
creation, boot-image validation against stock and hardware tests remain unperformed.

The top-level manifest and device-facing projects are pinned; other Android
branch references and middleware fetched by build helpers still need a fully
resolved manifest/commit inventory from the first real build. A completely
reproducible binary release is not claimed. The original Android-10 contribution
remains available in Git history at commit `b5e3124`.
