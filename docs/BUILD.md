# Build workflow — Android 10 / aarch64

Status: **commands and integration prepared; no full build executed**.
No Sailfish release has been certified for this device. The locked upstream
packaging revisions are a starting point, not a tested release combination.
Keep logs of the first real build and fix failures before proceeding to hardware.

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

Use the Android-10-compatible build dependencies from HADK rather than attempting
to compile the old Android tree directly with the latest Arch host tools.
Reserve 200 GiB free or more for Android sources and intermediate outputs.
SDK downloads, dependency installation, network access and any sudo needed by
the official tools are performed on your own build machine.

Set the following **in both build shells**, using real paths on your machine:

```bash
export N100_PORT="$HOME/n100_sailfishos"
export ANDROID_ROOT="$HOME/hadk/android-billie2"
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
python3 "$N100_PORT/tools/n100.py" init --android-root "$ANDROID_ROOT" --sync --jobs 4
python3 "$N100_PORT/tools/n100.py" prepare --android-root "$ANDROID_ROOT"
```

`init` uses the pinned mer-hybris manifest and installs `manifests/billie2.xml` as
a local manifest. Device, kernel, libhybris, hybris-boot and hybris-patches are
pinned separately. The upstream manifest still contains branch references for
other Android components. The resulting **entire** resolved manifest is exported
as `n100-resolved-manifest.xml`; preserve it with the first build. Before that
export, the source set is not completely reproducible across future syncs.

`prepare` verifies those checkout revisions, refuses unrelated edits to the
inspected BoardConfig/fstab, and:

- generates the missing `vendor/billie2-perf_defconfig` from OnePlus's
  `vendor/bengal-perf_defconfig` and `kernel/sailfish.config`;
- adds `selinux=1` while retaining the upstream `androidboot.selinux=permissive`;
- creates independent source snapshots in `rpm`, `hybris/droid-configs` and
  `hybris/droid-hal-version-billie2`, with locked upstream Git submodules.

No Android fstab encryption options or partition tables are silently changed.
Repeating `prepare` with unchanged inputs is allowed. Changed adaptation inputs
or edits in an installed snapshot produce an error for manual review. If setup
is interrupted during a clone, inspect the incomplete directory before moving
it aside and retrying; it is not automatically deleted.

## 3. Proprietary components and hybris patches (HABUILD_SDK)

The Android device tree inherits `vendor/oneplus/billie2/billie2-vendor.mk` and
`BoardConfigVendor.mk`. Neither is included here. Extract the device's proprietary
components with the pinned device tree's `extract-files.sh` / `setup-makefiles.sh`
and the pinned Lineage extract utilities. Read those scripts first and supply
the matching mounted Android filesystem dump as their source. Do not substitute
a vendor tree from Android 11/13/15 or a different OnePlus phone. Preserve a local
hash list and firmware fingerprint alongside the build log. Proprietary blobs
remain local unless their redistribution rights are known.

Check that both inherited files exist before continuing:

```bash
cd "$ANDROID_ROOT"
test -s vendor/oneplus/billie2/billie2-vendor.mk
test -s vendor/oneplus/billie2/BoardConfigVendor.mk
```

Apply the Android-10 hybris patches **once to a clean, synced base**:

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

```bash
bash "$N100_PORT/tools/build-hal.sh"
```

This sources the Android environment, selects `billie2`, exports
`TEMPORARY_DISABLE_PATH_RESTRICTIONS=true`, runs `mka hybris-hal` and checks
the final `.config` and `hybris-boot.img`. If the kernel build uses a different
object directory, supply its final `.config` explicitly:

```bash
python3 "$N100_PORT/tools/n100.py" check-kernel /path/to/final/.config
```

The textual fragment merge cannot resolve Kconfig dependencies. Any missing
symbol must be investigated in the kernel source and resolved explicitly.
Also run `hybris/mer-kernel-check` as described by HADK. Do not drop requirements
merely to make the local checker green.

Output is expected in `out/target/product/billie2/`, including `hybris-boot.img`,
`hybris-recovery.img` and Android HAL outputs. Examine the DTB/DTBO targets and
the stock boot image with Android's unpacking tools before any boot experiment.
The inherited header is v2, page size 4096, boot partition size 100663296 bytes.
These numbers are source-tree expectations, not confirmation of your handset.
The inspected tree's `lito` platform name, AVB test keys, dynamic partition group
and `BUILD_WITHOUT_VENDOR` need first-build review. In particular, a stock vendor
partition does not prove the `vendorimage` hybris build dependency is satisfied.

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

The SELinux sparse links point at expected Android-10 paths. Verify on your own
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
There is no installer ZIP macro or partition-flashing automation in this port.
The dynamic-partition/data issues in [BRINGUP.md](BRINGUP.md) must be solved
before an on-device deployment can be described accurately.

Preserve the rootfs archive hash, HAL/image hashes, resolved Android manifest,
middleware SHAs, SDK release and package manifest with each build. Update
`status.json` to built only after that stage actually succeeds, and to booted
only with an actual device log.
