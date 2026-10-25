# Hardware bring-up and current blockers

This is a development checklist, not an installation guide. No command here
authorizes or automates partition writes. The generated image has passed a host ext4 check; no on-device boot has been tested.

## 1. Establish the device baseline

The first BE2013 report has been analyzed in [DEVICE_BE2013.md](DEVICE_BE2013.md).
It confirms stock Android 11, SDK 30, platform bengal, slot B, locked bootloader,
dynamic partitions and file-encrypted F2FS userdata. Keep Android installed for
the next read-only collection; no unlock or downgrade is needed for diagnosis.

Collect `tools/n100.py probe` while the existing Android installation still boots.
Record the exact BE201x model, firmware fingerprint, Android/vendor SDK level,
slot suffix, bootloader state, filesystem type and block-device names. Preserve
the current firmware and a working device-specific restore procedure before
changing anything. Bootloader unlocking normally wipes user data.

The candidate firmware baseline is OxygenOS/Android 11; the HAL build base is
Android 13 (hybris-20.0 / LineageOS 20). Those versions intentionally describe
different layers: the official Lineage device's blob list uses Android-11 vendor
components with an Android-13 build. That is evidence for a candidate approach,
not proof of compatibility with the reported EEA firmware or Sailfish.
The Ubuntu Touch port uses an older Android-10 baseline; its downgrade and
installation instructions do not apply to this candidate.

## 2. Kernel and boot-image validation

- The exact EEA OTA target has now been matched and stock boot/DTBO/VBMeta
  reconstructed with payload hashes verified. Stock boot v2 / 4096-byte pages
  match the built format. See [STOCK_FIRMWARE.md](STOCK_FIRMWARE.md) for original
  headers, hashes and the full package download. Board/DTB/DTBO runtime
  compatibility and restoration remain unvalidated.
- Confirm the pinned Lineage `bengal-perf_defconfig` and `OEM_TARGET_PRODUCT=billie2`
  select the correct board. The generated `sailfish-billie2_defconfig` adds the
  Sailfish fragment without replacing that vendor-specific selection.
- Inspect the vendor DTS selection and match the handset's board/variant ID.
- Confirm the built kernel's final config against `tools/n100.py check-kernel`
  and upstream `hybris/mer-kernel-check`.
- Compare kernel module vermagic with the installed vendor modules. The published
  base has `CONFIG_MODVERSIONS`; incompatible modules may prevent storage, Wi-Fi
  or other hardware from starting even if the kernel enters initramfs.
- Validate boot v2 structure, 4096-byte pages, ramdisk, DTB and partition size.
  `inspect-boot` performs structural checks only; it does not check signatures,
  kernel execution or whether the ramdisk is actually the hybris initramfs.
- Inspect the ramdisk to confirm hybris `/init`, busybox, required storage drivers
  and USB gadget support are present. A successful Android build alone is insufficient.

Keep SELinux compiled in and use `selinux=1 androidboot.selinux=permissive` for
the initial development bring-up. Do not disable SELinux in Kconfig.

### Collect the built boot inputs for offline review

The user's [stock image report](../validation/stock-images-user-20261025.json)
now matches all five independently extracted original image records. The ZIP
and the original boot/DTBO/recovery/VBMeta files have been saved on the user's
PC. A tested restoration procedure and a boot experiment remain outstanding.

The actual built Sailfish DTB and initramfs have not yet been received for
offline inspection. The collector below reads local files only and creates
one bounded ZIP. Run it on CachyOS, outside the SDK chroots:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only

python3 "$N100_PORT/tools/collect-boot-audit.py" \
  --work-dir "$HOME/n100-build" \
  --android-root "$ANDROID_ROOT" \
  --hybris-boot "$HOME/n100-build/images/billie2-20261025T160318Z/hybris-boot.img" \
  --output "$HOME/n100-build/boot-audit-$(date -u +%Y%m%dT%H%M%SZ).zip"
)
```

It locates the matching `stock-BE2013-*/stock-images.json` automatically. If
multiple matching directories exist, specify `--stock-dir /absolute/path`
rather than guessing a directory. All five original image hashes and the
previously reported `hybris-boot.img` hash are verified before bundle creation.
The collector refuses changed input images and existing output files.

The ZIP contains `audit.json`, the Sailfish ramdisk, both base DTBs, the stock
DTBO image, a 64-byte kernel header and the current Android kernel `.config`.
If present, `out/target/product/billie2/dtbo.img` is included as the built DTBO.
The config and optional built DTBO have recorded hashes, but are not thereby
proven to belong to the pinned kernel image. No complete boot/kernel image,
userdata image, personal files or phone identifiers are collected.

Upload the ZIP at the printed path. The next analysis must review the actual
initramfs `/init`, required executables, storage selection and USB gadget setup;
compare device-tree IDs and test applying the original DTBO to the built base
DTB. Collection alone does not perform or pass those checks. The kernel config
snapshot is an additional build input, not proof of its embedded provenance.

The collector's [eight tests and original-image integration check](../validation/boot-audit-collector.json)
passed. The real user-built hybris input is still needed. Do not interpret
these host checks as a phone boot test.

### Actual boot audit: rebuild required

The uploaded boot bundle has now been analyzed. The supplied kernel config
snapshot disables the decoder for its actual legacy LZ4 ramdisk. Stock DTBO
application to the built base DTB fails with `FDT_ERR_NOTFOUND`; the matching
built pair applies successfully. Seventeen missing Stock fixup labels explain
the host failure. The original USB configfs path also requested unsupported
`rndis_bam` alongside RNDIS.

Source fixes and a boot-only build mode now prepare a RNDIS-only early debug
branch before userdata mounting. The actual new kernel/debug image has not yet
been built or booted. Follow [EARLY_USB_DEBUG.md](EARLY_USB_DEBUG.md) for the
next build command and [the evidence](../validation/boot-audit-user-20261025.json)
for exact hashes, test matrix and limitations. The previous bundle collection
command above is retained for reference; it does not need repeating.

## 3. Dynamic partitions and Android userspace — unresolved

The inspected Lineage fstab declares `system`, `system_ext`, `product`, `vendor`
and `odm` as
`logical`, `slotselect`, `first_stage_mount` partitions inside `super`.
The observed stock layout has mappings for the first four, but no visible ODM
mapping. An ODM image generated by Lineage is a build artifact, not evidence of
an existing stock ODM partition. Ordinary mounts of `/dev/block/by-name/system` or `/dev/block/by-name/vendor`
therefore cannot be assumed valid. Linux device-mapper targets must first exist
and correspond to the actual active slot/super metadata.

The initial HAL spec excludes these logical entries from the legacy `makefstab`
conversion, preventing it from generating misleading physical-partition mount
units. **That does not implement their replacement.** Before the port can boot:

1. Inspect `lpdump` output and the device's `/dev/block/mapper` mappings in Android
   or a known working recovery. `adb shell ls` permission failures are inconclusive.
2. Determine whether the hybris initramfs can perform the required first-stage
   mapping, or whether a device-specific mapping/preinit integration is required.
   Use the appropriate Android `liblp` tooling; do not hardcode sector offsets.
3. Implement and verify a consistent `/system`, `/vendor`, `/product` and `/apex`
   strategy, including `/system_ext` and any built ODM payload. These must be visible at the paths expected by the bionic linker,
   Android service manager, vendor services and libhybris before droid-hal-init.
4. Implement ordering and dependencies in the initramfs / systemd units. Keep
   essential Qualcomm persist/firmware/DSP/Bluetooth mounts consistent with
   the real device layout and slot selection.
5. Verify actual vendor SELinux policy paths and combined contexts, binder device
   nodes and permissions. Resolve linker namespace/APEX errors from logcat.

Stock Android partition reuse and packaging a matching Android system payload
are alternative strategies; neither is fully implemented here. Pick one based
on the first real boot log. Do not install a generic `/system` mount unit from
an older, non-dynamic-partition device.

## 4. Sailfish rootfs and encrypted userdata — unresolved

The upstream hybris initramfs searches for `/data/.stowaways/sailfishos` and
switches root into that directory. Its direct `mount $DATA_PARTITION /data`
does not by itself unlock Android file-based or metadata encryption.

The configuration bundle has now supplied the three stock fstabs. Two describe
persistent F2FS data with different encryption flags; the nodata variant uses
tmpfs. The active variant remains unknown. See [CONFIG_BUNDLE.md](CONFIG_BUNDLE.md).
Neither the filesystem name nor a dm node number proves that hybris initramfs
can unlock the existing Android filesystem.

The new **development image path** creates a fresh unencrypted ext4 file with the
built Sailfish rootfs in `.stowaways/sailfishos`, matching the pinned hybris init
script without a `data_subdir` override. `tools/make-userdata-image.py` populates
it with `mke2fs -d` and checks it with `e2fsck`. The first real 8 GiB Sailfish userdata image has now been generated on the user's
CachyOS host and passed e2fsck. The uploaded manifest and reported checks are
recorded in [the image build evidence](../validation/image-build-user-20261025.json).
The binary images were not uploaded for independent inspection; device boot remains untested.

A future deployment to userdata replaces Android user data, rather than decrypting
or migrating it. The tool performs no device writes. Physical partition capacities were subsequently verified and the image sizes fit.
Boot compatibility, backups, restoration and the locked bootloader still require
validation before installation. The dynamic Android HAL mounts in section 3
remain unresolved: a userdata rootfs image alone does not supply them.

## 5. First nonpersistent boot experiment

### Read bootloader variables before any boot experiment

Connect only the N100 with authorized USB debugging while Android is running.
The following host-side diagnostic restarts it into the bootloader, queries
a fixed list of variables, and requests a normal reboot afterwards.
It does not issue unlock, flash, erase, format or set-active commands.
`getvar is-userspace` distinguishes the bootloader from fastbootd; unsupported
variables may return errors and are retained in the log.
[AOSP command reference](https://source.android.com/docs/core/architecture/bootloader/fastbootd).

```bash
(
set -o pipefail
log="$HOME/n100-build/fastboot-preflight-$(date -u +%Y%m%dT%H%M%SZ).log"
adb get-state || exit 1
adb reboot bootloader || exit 1
for key in product current-slot unlocked secure is-userspace \
  partition-size:boot_a partition-size:boot_b \
  partition-size:dtbo_a partition-size:dtbo_b \
  partition-size:userdata partition-type:userdata max-download-size; do
    printf '\n--- %s ---\n' "$key"
    timeout 15s fastboot getvar "$key"
done 2>&1 | tee "$log"
if ! timeout 20s fastboot reboot; then
    echo 'Normal reboot could not be confirmed; share this terminal output.' >&2
    exit 1
fi
printf 'Diagnoselog: %s\n' "$log"
)
```

Each query has a timeout so an absent USB connection does not wait indefinitely.
If the final reboot times out, the diagnostic has not confirmed a working
fastboot connection; report that output. Share only this targeted log, which
does not request serial numbers or IMEIs. The log filename has a UTC timestamp.

Compare the physical userdata capacity with 8,589,934,592 bytes and each boot
slot capacity with 45,064,192 bytes. These checks alone do not establish
stock DTBO compatibility, support for temporary boot, a restore procedure
or working dynamic HAL mounts. The previous Android report observed a locked
bootloader; its current state must be read before planning a boot test.

### Observed BE2013 preflight — 25 October 2026

The uploaded [fastboot log](../validation/fastboot-preflight-20261025T163232Z.log)
confirmed a working connection to bootloader fastboot (`is-userspace: no`),
`product: bengal`, active slot `b`, `unlocked: no` and `secure: yes`.
The Android report establishes model BE2013; `bengal` alone does not identify
the exact model or firmware. [Machine-readable assessment](../validation/fastboot-preflight-user-20261025.json).

| Physical partition | Capacity | Built image | Size fits |
| --- | ---: | ---: | --- |
| boot_a / boot_b | 100,663,296 bytes each (96 MiB) | 45,064,192 bytes | Yes |
| dtbo_a / dtbo_b | 25,165,824 bytes each (24 MiB) | No stock DTBO comparison yet | Not evaluated |
| userdata | 48,062,869,504 bytes (44.76 GiB) | 8,589,934,592 bytes | Yes |

The current userdata type is `f2fs`; the new image is fresh unencrypted ext4.
Replacing userdata would discard the Android data; it is not a filesystem
conversion that preserves those data. The reported maximum download is
804,280,320 bytes. The boot image is below it; transferring the raw 8 GiB
userdata file needs a separately verified sparse/chunked transfer path.
No userdata flash command has been validated.

The remaining blockers are the locked bootloader, stock boot/DTB/DTBO
compatibility, an established restore path, A/B firmware selection and the
dynamic Android HAL mounts described above. Unlocking causes a factory reset
according to [AOSP](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking).
A size match alone does not justify unlocking or writing these development images.

The following query supplied the exact OxygenOS/OTA identity while stock
Android was running. It is retained for reference; collection is complete. The `BE2013_7_231009` display value
and fingerprint remain the baseline; do not infer an exact OTA ZIP solely
from the security patch date or use the global BE2011 firmware as EEA proof.

```bash
(
adb get-state || exit 1
for key in ro.oxygen.version ro.build.version.ota ro.build.display.id ro.build.version.incremental; do
  printf '\n--- %s ---\n' "$key"
  adb shell getprop "$key"
done
) 2>&1 | tee "$HOME/n100-build/firmware-identity.log"
```

The property queries require neither root nor an unlocked bootloader and
do not reboot the phone or change its settings. Blank properties are retained.
The uploaded identity log now confirms `OnePlusN100Oxygen_14.E.35_GLO_0350_2310092319`,
with display `BE2013_7_231009` and incremental `2310092135` unchanged.
The OxygenOS version property is empty. See [STOCK_FIRMWARE.md](STOCK_FIRMWARE.md)
for the matching EU full OTA, verified ZIP target metadata and original boot
header evidence. The catalog helper's HTTP 403 was fixed and its live query
completed; the next step is saving the stock package on the user's PC.

### First kernel/debug-shell boot

Only after validating the boot header, storage access and recovery path should
an experienced porter attempt a temporary boot of the generated debugging image,
if the actual bootloader supports it. `fastboot boot` support has not been checked
for this handset. No fallback flash is prescribed here. Keep both A/B slots and
the original DTBO/vbmeta/boot files documented; never relock the bootloader on
unverified custom images.

The initial milestone is a stable hybris debug shell over USB, not a graphical
login. Consult HADK for USB RNDIS setup. The default initramfs address is
`192.168.2.15`, telnet port 23 before switch-root and 2323 afterwards. Those
interfaces depend on the actual gadget driver and may not appear until adapted.
Use only an isolated USB link for the unauthenticated development debug shell.

Useful first logs: `/init.log`, `/diagnosis.log`, `/proc/cmdline`, `dmesg`,
`/proc/mounts`, `systemctl status droid-hal-init`, `journalctl -b` and
`/usr/libexec/droid-hybris/system/bin/logcat`. Preserve logs before rebooting.

## 6. Hardware milestones

| Stage | Evidence needed | Initial state |
| --- | --- | --- |
| Initramfs | Kernel alive, storage and USB debug shell | Untested |
| Rootfs | Successful switch-root, systemd, journal | Untested |
| Android HAL | servicemanager/hwservicemanager/vendor services running | Untested |
| Graphics | HWC2 + Adreno EGL, simple Qt surface, Lipstick | Untested |
| Input | Correct touchscreen axes, buttons, permissions | Untested |
| Power | Charging, fuel gauge, suspend/resume, thermal control | Untested |
| Audio | Speaker, earpiece, mic, wired/Bluetooth routes | Untested |
| Network | WLAN reconnect/suspend, Bluetooth pairing | Untested |
| Modem | SIM, SMS, mobile data, incoming/outgoing audio calls | Untested |
| Location/camera | GPS fix, camera preview/capture, sensor calibration | Untested |
| Daily use | Repeated cold boots, battery drain, stability | Untested |

Test telephony without depending on this development device for emergency calls.
VoLTE, IMS and carrier-specific behavior are separate work, not implied by a
working Android modem. AppSupport is not bundled or promised by this port.

Update `status.json` with log/build evidence for each milestone. Do not mark a
feature working merely because a matching package was installed.
