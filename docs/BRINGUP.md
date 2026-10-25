# Hardware bring-up and current blockers

This is a development checklist, not an installation guide. No command here
authorizes or automates partition writes. The generated image has passed a host ext4 check; the first physical debug boot reached the hybris initramfs shell over USB.
Full rootfs/HAL boot remains untested.

The latest user observation confirms an unlocked bootloader, active slot B,
successful DTBO write and accepted temporary boot. USB ICMP and Telnet now work
in the early initramfs. The initial mount list confirmed the stop before
userdata; userdata has since been mounted manually read-only. The runtime inventory confirms
4.19.325-perf+ AArch64, expected super/metadata/userdata labels and capacities,
registered ext4/F2FS and initialized Device Mapper. Continue with the explicit
[userdata flash and read-only rootfs probe](ROOTFS_PROBE.md), which has now
succeeded: Sailfish OS 5.1.0.11 runs in a read-only chroot. The next step is
[actual super metadata and rootfs mount-path inspection](SUPER_METADATA.md).
That report is now reviewed: four slot-B partitions are declared, and native
Sailfish dmsetup is available. The exact snapshot-bound read-only mapping test
and Android-13 runtime inventory are in [ANDROID_MOUNT_PROBE.md](ANDROID_MOUNT_PROBE.md).
The exact debug/DTBO candidate, verified stock rollback file preparation and
explicit first-test commands are in [FIRST_DEVICE_TEST.md](FIRST_DEVICE_TEST.md).

## 1. Establish the device baseline

The first BE2013 report has been analyzed in [DEVICE_BE2013.md](DEVICE_BE2013.md).
It confirms stock Android 11, SDK 30, platform bengal, slot B, locked bootloader,
dynamic partitions and file-encrypted F2FS userdata. The baseline collection is complete. The debug image is built; unlocking and
the first kernel test are the next device phases. No downgrade is planned.

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

### Completed boot audit and debug packaging

The actual uploaded boot bundle was analyzed; collection does not need repeating.
The earlier config had `RD_LZ4=n` but already enabled `DECOMPRESS_LZ4=y`;
it did not prove a missing decoder. Stock DTBO fails against the built base DTB
with `FDT_ERR_NOTFOUND`, while the matching built pair applies on the host.

The user rebuilt the kernel and successfully packaged the separate early
RNDIS debug image. Its report confirms the branch before userdata mounting,
static AArch64 BusyBox, matching base DTB and exact previously reviewed DTBO
table/overlay bytes. Full DTBO hashes differ outside the reviewed content.
The complete new binaries were not uploaded for independent inspection.

See [the build assessment](../validation/debug-image-build-user-20261025.json),
[EARLY_USB_DEBUG.md](EARLY_USB_DEBUG.md) and the concrete
[first device test plan](FIRST_DEVICE_TEST.md). No further rebuild is required
for this candidate. The first physical debug attempt has since reached the early USB shell.
Kernel execution, n100_debug=1, ICMP and Telnet are confirmed. Restoration,
storage mapping and full Sailfish boot remain unverified.

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
The binary images were not uploaded for independent inspection; full rootfs boot remains untested.

A future deployment to userdata replaces Android user data, rather than decrypting
or migrating it. The tool performs no device writes. Physical partition capacities were subsequently verified and the image sizes fit.
Boot compatibility, backups, restoration and the locked bootloader still require
validation before installation. The dynamic Android HAL mounts in section 3
remain unresolved: a userdata rootfs image alone does not supply them.

## 5. First kernel boot experiment

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
| dtbo_a / dtbo_b | 25,165,824 bytes each (24 MiB) | Matching built DTBO: 25,165,824 bytes | Yes |
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
completed. The exact package and five reconstructed stock images are now saved
on the user's PC; see the current [device test plan](FIRST_DEVICE_TEST.md).

### First kernel/debug-shell boot

Follow [FIRST_DEVICE_TEST.md](FIRST_DEVICE_TEST.md) for the current candidate.
A temporary boot image still requires the matching built DTBO in a physical
partition; the combined experiment therefore includes a persistent change.


Only after validating the boot header, storage access and recovery path should
an experienced porter attempt a temporary boot of the generated debugging image,
if the actual bootloader supports it. The first temporary boot now reaches the early hybris USB shell.
This verifies kernel/initramfs execution; it does not verify a full OS boot. No fallback flash is prescribed here. Keep both A/B slots and
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
| Initramfs | Kernel alive, storage and USB debug shell | Kernel, USB shell and userdata read-only ext4 mount verified |
| Rootfs | Successful switch-root, systemd, journal | Read-only Sailfish chroot verified; switch-root/systemd pending |
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
