# Exact EEA stock firmware inputs

The uploaded [identity log](../validation/firmware-identity-user-20261025.log)
was collected from authorized ADB on 25 October 2026. Its values agree with
the earlier BE2013 Android report:

| Property | Observed value |
| --- | --- |
| `ro.build.version.ota` | `OnePlusN100Oxygen_14.E.35_GLO_0350_2310092319` |
| `ro.build.display.id` | `BE2013_7_231009` |
| `ro.build.version.incremental` | `2310092135` |
| `ro.oxygen.version` | Empty |

The previously collected fingerprint identifies the EEA build. The `GLO`
token in the OTA string does not establish that a North American BE81AA
package is interchangeable. No exact OTA ZIP, stock boot or DTBO image has
yet been verified. A public version label inferred from the patch date is
insufficient for package selection.

## Query the full-update catalog on the build host

The development environment received HTTP 403 from the OS Updater catalog.
`tools/query-stock-firmware.py` provides a host-side read-only alternative:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 "$N100_PORT/tools/query-stock-firmware.py" \
  --output "$HOME/n100-build/stock-firmware-catalog-$(date -u +%Y%m%dT%H%M%SZ).json"
)
```

Run this in CachyOS. It requires Python 3 and internet access, with no SDK or
phone connection. It requests only the public device list, the EU N100 update
methods and the most recent stable full update's metadata. Region/method IDs
are resolved from names, never guessed; ambiguity is an error. Existing reports
are preserved. Share the JSON at the printed path. If access fails, share the
terminal error instead; do not change the phone's firmware as a workaround.

The API endpoints and models were checked against the app's own source at
[50327ea3fe9ffdcd4c4134b022793ad3f0d33cf2](https://github.com/oxygen-updater/os-updater/tree/50327ea3fe9ffdcd4c4134b022793ad3f0d33cf2):
`app/build.gradle.kts`, `apis/ServerApi.kt`, `models/DeviceRequestFilter.kt`,
`models/Device.kt`, `models/UpdateMethod.kt`, `models/UpdateData.kt` under
`app/src/main/java/com/oxygenupdater` (except the Gradle file).

The helper does not download an OTA, execute server-returned URLs, install
packages or invoke ADB/fastboot. Its five local tests cover EU/full selection,
ambiguous/wrong-region refusal, invalid IDs, both API field spellings and exact
OTA comparison. Live end-to-end access remains unverified.

## Package acceptance and next milestone

A matching catalog result is a candidate source, not a validated restore image.
Before using its binaries:

1. Obtain the full OTA ZIP and retain its source URL, size and SHA-256.
2. Inspect `META-INF/com/android/metadata`: target fingerprint/incremental and
   product must match this EEA baseline. Check the payload manifest for full,
   source-independent boot and DTBO images; an incremental OTA alone cannot
   reconstruct arbitrary stock images.
3. Extract and inspect stock boot/DTB/DTBO, compare with the built kernel set,
   and retain original AVB-related images and a concrete restore procedure.
   Stock boot/DTBO alone are not a full-device backup.
4. Prepare the first temporary kernel/USB debug-shell experiment. A successful
   kernel boot would still leave dynamic Android HAL mounts and hardware
   bring-up described in [BRINGUP.md](BRINGUP.md).

Firmware identity collection does not change `boot_tested` or `flashable_release`.
