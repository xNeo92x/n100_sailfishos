# Build workflow — Android 13 HAL / Android 11 firmware / aarch64

Status: **Android HAL, HAL/config RPMs and five middleware packages built on the user's host;
Android droidmedia, media/version RPMs and Sailfish rootfs remain unfinished**.
See [the user-reported HAL build evidence](../validation/hal-build-user-20261025.json).
The boot image passed structural checks; it has not been boot-tested.
No Sailfish release has been certified for this device. The locked upstream
packaging revisions are a starting point, not a tested release combination.
Keep logs of the first real build and fix failures before proceeding to hardware.
For the concrete CachyOS host workflow, firmware collector and experimental
userdata-image assembly, see [CACHYOS_BUILD.md](CACHYOS_BUILD.md).

## 1. Environments

Use the official [HADK](https://docs.sailfishos.org/Develop/HADK/) and
[Platform SDK instructions](https://docs.sailfishos.org/Tools/Platform_SDK/).
This repository supplements those instructions; it does not install an SDK by
running an unpinned remote script as root.

Prepare these separate environments, keeping the Android tree visible at the
same absolute path in each:

| Environment | Purpose | Required tools |
| --- | --- | --- |
| HOST (e.g. CachyOS) | Git, ADB report, USB access | Python 3.10+, git, android-tools |
| HABUILD_SDK | Android/Clang/kernel build | Official HADK Ubuntu build chroot, repo, git, Python 3, Android build prerequisites |
| PLATFORM_SDK | Target setup, RPM, middleware, rootfs | sdk-assistant, mb2, mic, createrepo_c, matching aarch64 target/tooling |

Use the Android-13-compatible build dependencies from HADK rather than attempting
to compile the old Android tree directly with the latest Arch host tools.
Reserve 200 GiB free or more for Android sources and intermediate outputs.
SDK downloads, dependency installation, network access and any sudo needed by
the official tools are performed on your own build machine.

Set the following **in both build shells**, using real paths on your machine:

```bash
export N100_PORT="$HOME/n100_sailfishos"
export ANDROID_ROOT="$HOME/hadk/android-billie2"
export N100_DEVICE_REPORT="$HOME/n100-device-report-v2.json"
source "$N100_PORT/config/port.env"
```

Create/install a target named `oneplus-billie2-aarch64` and its matching tooling
using the Platform SDK instructions. Set `RELEASE` to the **exact** release
installed in that target; do not use the literal example placeholder or assume
that setting this variable upgrades the SDK. Confirm the target with:

```bash
sdk-assistant list
```

The first working SDK/target/tooling version must be recorded in a build report.
If the selected Sailfish release requires older/newer middleware or packaging,
make explicit reviewed pin changes in `sources.lock.json`; do not use a moving
`master` checkout to hide incompatibilities.

## 2. Source checkout (HABUILD_SDK)

```bash
python3 "$N100_PORT/tools/n100.py" init --android-root "$ANDROID_ROOT" --device-report "$N100_DEVICE_REPORT" --sync --jobs 4
python3 "$N100_PORT/tools/n100.py" prepare --android-root "$ANDROID_ROOT"
```

Do not reuse the old Android-10 source directory. Use a new empty build tree.
The report gate checks the intended phone's **stock Android 11 / SDK 30** baseline,
not Android 13: the latter is the version used to build the new HAL. A generation
match permits source preparation only, not installation.

`init` uses the pinned mer-hybris manifest and installs `manifests/billie2.xml` as
a local manifest. Device, kernel, OnePlus hardware, libhybris, hybris-boot and hybris-patches are
pinned separately. The upstream manifest still contains branch references for
other Android components. The resulting **entire** resolved manifest is exported
as `n100-resolved-manifest.xml`; preserve it with the first build. Before that
export, the source set is not completely reproducible across future syncs.

`prepare` verifies those checkout revisions, refuses unrelated edits to the
inspected BoardConfig/fstab, and:

- generates `vendor/sailfish-billie2_defconfig` from the pinned kernel's
  `vendor/bengal-perf_defconfig` and `kernel/sailfish.config`;
- selects that config in BoardConfig, keeping `OEM_TARGET_PRODUCT` and other
  upstream board settings, and adds `selinux=1 androidboot.selinux=permissive`;
- creates independent source snapshots in `rpm`, `hybris/droid-configs` and
  `hybris/droid-hal-version-billie2`, with locked upstream Git submodules.

No Android fstab encryption options or partition tables are silently changed.
Repeating `prepare` with unchanged inputs is allowed. Changed adaptation inputs
or edits in an installed snapshot produce an error for manual review. If setup
is interrupted during a clone, inspect the incomplete directory before moving
it aside and retrying; it is not automatically deleted.

## 3. Proprietary components and hybris patches (HABUILD_SDK)

The Android device tree inherits `vendor/oneplus/billie2/billie2-vendor.mk` and
`BoardConfigVendor.mk`. Use the reviewed, pre-extracted vendor-source candidate:

```bash
python3 "$N100_PORT/tools/firmware.py" --work-dir "$N100_FIRMWARE_WORK" \
  vendor-source --android-root "$ANDROID_ROOT"
```

This installs the pinned TheMuppets lineage-20 dataset as a separate Git worktree.
Its full file inventory and declared raw-reference differences are checked again
before HAL compilation. See [VENDOR_SOURCE.md](VENDOR_SOURCE.md). Do not apply the
raw extraction script to this pre-extracted source or mix it with the partial
ADB dump. It supplies build inputs, not a stock EEA compatibility certification.

Manual raw extraction remains available via `extract-files.sh` and
`tools/firmware.py import`, requiring a complete input set and reviewed fixups.
The stock dump alone cannot satisfy all pins: the upstream WiFi Display system
section explicitly uses donor-device `lemonadep` files. Missing ADB files and
pinned donor blobs must not be silently ignored to force extraction through.

Check that both inherited files exist before continuing:

```bash
cd "$ANDROID_ROOT"
test -s vendor/oneplus/billie2/billie2-vendor.mk
test -s vendor/oneplus/billie2/BoardConfigVendor.mk
```

Apply the Android-13 hybris patches **once to a clean, synced base**:

```bash
cd "$ANDROID_ROOT"
bash hybris-patches/apply-patches.sh --mb
# Only create the marker if the patch application above finished successfully.
git -C hybris-patches rev-parse HEAD > .n100-hybris-patches-applied
```

Stop on rejected patches. The marker is a workflow aid, not evidence that
patches were correctly applied. Do not rerun `repo sync` or overwrite patched
projects without preserving the patch state. Use `repo forall -c 'git status
--short'` to inspect the tree.

## 4. Kernel and HAL (HABUILD_SDK)

`config/port.env` unsets the obsolete `TEMPORARY_DISABLE_PATH_RESTRICTIONS`
switch, including values inherited from an older shell. Android 13 aborts when
this variable is true. Keep the normal Android PATH restrictions enabled.

```bash
bash "$N100_PORT/tools/build-hal.sh"
```

This uses the saved `N100_DEVICE_REPORT` to generate a reviewed local physical
mountpoint override with `n100.py prepare-boot`. Existing custom overrides are
not overwritten. It checks the configured ccache executable, sources the Android
environment, selects `billie2`, runs `mka hybris-hal droidmedia` and checks
the final `.config` and `hybris-boot.img`. If the kernel build uses a different
object directory, supply its final `.config` explicitly:

```bash
python3 "$N100_PORT/tools/n100.py" check-kernel /path/to/final/.config
```

The textual fragment merge cannot resolve Kconfig dependencies. Any missing
symbol must be investigated in the kernel source and resolved explicitly.
Also run `hybris/mer-kernel-check` as described by HADK. Do not drop requirements
merely to make the local checker green.

A standalone resolution of the pinned kernel config has now succeeded, including
the local fragment check and upstream checker with zero fatal errors and 47
advisories. See [KERNEL_CONFIG_VALIDATION.md](KERNEL_CONFIG_VALIDATION.md) for exact
scope, hashes and reproduction. A subsequent standalone kernel/DTB/DTBO/module
build also succeeded; see [KERNEL_BUILD.md](KERNEL_BUILD.md). The subsequent
Android HAL build completed on the user's host and reported a structurally valid
45,064,192-byte hybris boot image; see
[HAL build evidence](../validation/hal-build-user-20261025.json). HAL/config RPMs and five middleware packages subsequently built successfully;
media/version RPMs and the Sailfish rootfs remain unfinished.
The HAL wrapper repeats both checks against the final Android-built configuration.

Output is expected in `out/target/product/billie2/`, including `hybris-boot.img`,
`hybris-recovery.img` and Android HAL outputs. Examine the DTB/DTBO targets and
the stock boot image with Android's unpacking tools before any boot experiment.
The inherited header is v2, page size 4096, boot partition size 100663296 bytes.
These numbers are source-tree expectations, not confirmation of your handset.
The current tree uses the observed `bengal` platform and builds vendor payloads.
Its AVB test keys, additional logical ODM partition and rebuilt kernel modules
need first-build review. Stock Android's observed partition layout has no visible
ODM mapper entry, while Lineage's build introduces an ODM image; do not mount an
assumed stock `odm_b`. The stock kernel is 4.19.152, whereas the pinned Lineage
kernel source identifies itself as 4.19.325. Rebuild compatible modules; never
reuse stock kernel modules based solely on the shared 4.19 major/minor version.

### Android media outputs before RPM packaging

The `droidmedia` Android target is separate from `hybris-hal`. It must be
built in HABUILD_SDK before `build-packages.sh media` packages its binaries
in PLATFORM_SDK. The default HAL wrapper now includes both targets.
To complete an existing HAL tree without requesting `hybris-hal` again,
enter the Ubuntu build chroot, source the same build environment, and run:

```bash
bash "$N100_PORT/tools/build-hal.sh" media
```

This mode requires `MERSDKUBU=1`, keeps its log in
`n100-build-logs/droidmedia.log`, and verifies all four required AArch64
ELF64 output headers. The RPM media stage repeats this check before the
legacy packer. See [the CachyOS recovery steps](CACHYOS_BUILD.md) for
entering and leaving the nested Ubuntu environment.

## 5. RPMs and middleware (PLATFORM_SDK)

With the target installed, shell variables sourced and `RELEASE` set:

```bash
bash "$N100_PORT/tools/build-packages.sh" hal
bash "$N100_PORT/tools/build-packages.sh" configs
bash "$N100_PORT/tools/build-packages.sh" middleware
bash "$N100_PORT/tools/build-packages.sh" media
bash "$N100_PORT/tools/build-packages.sh" version
```

These wrap the pinned upstream `rpm/dhd/helpers/build_packages.sh`. Packages go
to `droid-local-repo/billie2/`; upstream helpers retain their normal prompts and
logs. Middleware helpers may fetch further sources. Record their resolved Git
SHAs and the exact RPM NEVRAs before treating a rootfs as reproducible.

The initial package groups select HWC2/libhybris, binder Bluetooth/telephony,
droid audio and sensor middleware. They make **no claim that those features
already function**. Device-specific sensor calibration, audio routing, USB
gadget, modem, Wi-Fi, haptics and fingerprint integration await hardware logs.

The SELinux sparse links point at Android policy paths whose presence was observed
in the BE2013 Android-11 report. Verify on your own
firmware that the files exist, the policy version is suitable and the merged
file contexts are sufficient. Otherwise replace them with the actual device
policy/context files as directed by HADK FAQ. Do not copy Sony's policy binary.

## 6. Development rootfs (PLATFORM_SDK)

After successful dependency resolution, generated kickstarts and RPM builds:

```bash
bash "$N100_PORT/tools/build-packages.sh" rootfs
```

This invokes upstream `mic` with the generated `billie2` kickstart. It may
require sudo inside the SDK and network access to Sailfish repositories. It
produces a **development filesystem archive**, not an approved installer.
`tools/make-userdata-image.py` can package a successfully built billie2 rootfs
and boot image into regular experimental image files. It does not flash a device.
See [CACHYOS_BUILD.md](CACHYOS_BUILD.md) for inputs and remaining deployment gates.
There is no installer ZIP macro or partition-flashing automation in this port.
The dynamic-partition/data issues in [BRINGUP.md](BRINGUP.md) must be solved
before an on-device deployment can be described accurately.

Preserve the rootfs archive hash, HAL/image hashes, resolved Android manifest,
middleware SHAs, SDK release and package manifest with each build. Update
`status.json` to built only after that stage actually succeeds, and to booted
only with an actual device log.
