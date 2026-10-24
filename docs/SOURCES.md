# Source audit and port decisions

Reviewed on 2026-10-24. Exact integration revisions are in
[`sources.lock.json`](../sources.lock.json); the following are primary sources.

| Source | Role |
| --- | --- |
| [Sailfish OS GitHub](https://github.com/sailfishos) | OS components; not a ready-made N100 board-support tree |
| [Official HADK](https://docs.sailfishos.org/Develop/HADK/) | Hardware adaptation process and build environments |
| [Official architecture](https://docs.sailfishos.org/Reference/Architecture/) | Android BSP / libhybris / Sailfish layering |
| [mer-hybris Android 10 manifest](https://github.com/mer-hybris/android/tree/f2ffbb25b3669c957848405688f614a5298f940c) | hybris-17.1 Android base |
| [HADK FAQ](https://github.com/mer-hybris/hadk-faq/blob/master/README.rst) | Android-10 patches, SELinux and path-restriction requirements |
| [N100 Android-10 device tree](https://github.com/danascape/android_device_oneplus_billie2/tree/9a017853a4bcfac1e74858152d5bf9e4d906839a) | billie2 BoardConfig, product, extraction scripts and fstab |
| [OnePlus vendor kernel](https://github.com/OnePlusOSS/android_kernel_oneplus_sm4250/tree/6db26f433f9a8722d0858562804da13f3090bfd7) | SM4250 Android-10 kernel / bengal defconfig |
| [Current Lineage N100 tree](https://github.com/LineageOS/android_device_oneplus_billie2) | Confirms device identity; newer branch is not mixed into Android 10 |
| [N100 Ubuntu Touch](https://devices.ubuntu-touch.io/device/billie2/) | Adjacent Linux adaptation evidence, not a Sailfish installation source |
| [droid-hal-device](https://github.com/mer-hybris/droid-hal-device) | RPM macros and build helpers |
| [droid-hal-configs](https://github.com/mer-hybris/droid-hal-configs) | Android-specific sparse files and config/package templates |
| [droid-hal-version](https://github.com/mer-hybris/droid-hal-version) | hw-release / umbrella package template |
| [Sony Android-10 adaptation](https://github.com/mer-hybris/droid-config-sony-seine/tree/eaa09db67b94352ef801417363008dc4005d9213/sparse/etc/selinux) | FAQ reference for SELinux file layout only; no Sony binary policy reused |

## Why Android 10

The N100 has an available Android-10 device tree and vendor kernel, and
mer-hybris explicitly documents hybris-17.1 adaptation. The latest Lineage
branch targets a much newer Android base and is not a drop-in Sailfish HAL.
Android base version and Sailfish userspace version are separate concepts.
Choosing Android 10 does not pin Sailfish to an Android-10-era OS release.

This is a **candidate combination**. The device tree is an old development tree
with a very terse final commit, not evidence of a release-quality Android ROM.
Its kernel config path does not exist in the chosen OnePlus kernel. The setup
tool supplies that config path, but DTS/model selection and successful compilation
remain to be demonstrated. Do not mistake a pinned revision for a tested one.

## Facts from the inspected files

`BoardConfig.mk` declares arm64 plus arm secondary architecture, HWC2, Clang,
`vendor/billie2-perf_defconfig`, kernel source `kernel/oneplus/sm4250`, header v2,
4096-byte pages, DTB in boot, separate DTBO and dynamic partitions.
The platform identifier is `lito` in that device tree despite the physical SoC
being SM4250. It is retained for compatibility with the inspected product/vendor
configuration until build/runtime evidence supports a change.

`device.mk` inherits proprietary vendor files and VNDK 29. The fstab lists
logical system/product/vendor partitions, A/B selection, F2FS encrypted userdata
and separate Qualcomm firmware/persist mounts. Those are precisely why a generic
old-device flashing or mount recipe would be inappropriate.

The stock kernel's `bengal-perf_defconfig` has `CONFIG_VT` disabled and contains
OnePlus hardware-specific options. The fragment keeps the stock base, adds
systemd/debug requirements and retains SELinux support. A text merge is not a
successful kernel build; verify Kconfig's final result.

## Verification boundary

The initial contribution has only host-side tests, source inspection and syntax
validation. The development environment had about 28 GiB free, no connected N100,
no proprietary firmware dump and no installed Sailfish target. Therefore a full
Android build, RPM resolution, rootfs creation, flash, boot and hardware tests
could not be performed. `status.json` deliberately records these as false/untested.

The local manifest locks the device-facing projects and top-level manifest;
transitive Android branches and middleware fetched by build helpers need a
resolved manifest/commit inventory from the first actual build. No fully
reproducible binary release is claimed.
