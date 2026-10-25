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

## Matching full OTA source found

The corrected catalog query completed on 25 October 2026. The EU device
record explicitly identifies `OnePlusN100_EEA,BE2013`; its `Stable (full)`
result labels the release **11.0.15.BE83BA** and matches the installed OTA
identity exactly. The original helper's generic User-Agent received HTTP 403
on both developer and user hosts. Using the app protocol User-Agent
`Oxygen_updater_7.1.0` fixed the live query. No repeated user query is needed.

Sources and observed bytes:

- [Original catalog response](../validation/stock-firmware-catalog-20261025.json).
- [Package metadata extracted by bounded HTTPS range](../validation/stock-firmware-ota-metadata-20261025.txt).
- [Payload, stock image hashes and header assessment](../validation/stock-firmware-source-20261025.json).
- [Full OTA ZIP](https://gauss-otacostmanual-sg.allawnofs.com/remove-bfe275a691ef231f29b614f1971b6dd3/ota/23/10/16/b24a7b83-7d65-4c67-b44f-c5f402f04911.zip).

The ZIP metadata's EEA fingerprint and incremental `2310092135` exactly match
the phone. Payload version 2 / minor 0 contains 25 partitions, no old partition
information and only REPLACE, REPLACE_BZ and REPLACE_XZ operations. Thus boot
and DTBO are source-independent, not patches requiring an older stock dump.

Boot, DTBO, vbmeta and vbmeta_system were reconstructed locally from bounded
ranges. Every compressed-operation hash and final partition hash matched the
payload manifest. Proprietary image bytes are retained as analysis inputs and
are not committed into this source repository. The complete ZIP was subsequently downloaded independently; its MD5 matches
the catalog and its SHA-256 matches the user's download. OEM signature
verification has not been performed.

| Stock image | Bytes | Structural finding |
| --- | ---: | --- |
| boot | 100,663,296 | Android boot v2, 4096-byte pages |
| dtbo | 8,388,608 | DT table v0; one 145,971-byte overlay |
| vbmeta | 8,192 | Partition hash matched; AVB policy not yet assessed |
| vbmeta_system | 4,096 | Partition hash matched; AVB policy not yet assessed |

The stock boot contains a 15,746,308-byte kernel, 872,482-byte ramdisk and
328,855-byte DTB. The built Sailfish image uses the same header version/page
size. This is a format match, not kernel/DTB/DTBO runtime validation.

## Save the full stock package on the CachyOS build PC

The following downloads and verifies the expected 2,564,194,119-byte package.
MD5 is checked against the HTTPS catalog to detect corruption; a local SHA-256
is also recorded. These checks do not independently validate an OEM signing key.
Nothing is sent to the phone, installed, unlocked or flashed.

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
archive="$HOME/n100-build/downloads/BE2013-11.0.15.BE83BA-full.zip"
mkdir -p "$HOME/n100-build/downloads"
curl --fail --location --retry 3 --continue-at - \
  --output "$archive" \
  'https://gauss-otacostmanual-sg.allawnofs.com/remove-bfe275a691ef231f29b614f1971b6dd3/ota/23/10/16/b24a7b83-7d65-4c67-b44f-c5f402f04911.zip'
test "$(stat -c %s "$archive")" -eq 2564194119
printf '%s  %s\n' '1e0899169c583e567883f997493e46b2' "$archive" | md5sum --check
sha256sum "$archive" | tee "$archive.sha256"
)
```

The user has completed this download. The MD5 check returned `OK` and SHA-256
is `d890682b7b64d1b144ce7e69c539feaa43809b4c867004150eb137722a979e4d`.
An independent complete download reproduced both checksums; see
[download evidence](../validation/stock-firmware-download-user-20261025.json).
Preserve the ZIP and its `.sha256` file. The ZIP is recovery input; it does not
establish a tested restoration procedure or back up personal data.

## Extract and verify original boot/recovery inputs locally

Run this in CachyOS, outside both SDK chroots. It uses Python 3's standard
library and the existing ZIP; no extra package, network access or phone connection
is required. Output totals about 200 MiB. Existing output directories are refused.

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only

python3 "$N100_PORT/tools/extract-stock-boot.py" \
  --archive "$HOME/n100-build/downloads/BE2013-11.0.15.BE83BA-full.zip" \
  --output "$HOME/n100-build/stock-BE2013-$(date -u +%Y%m%dT%H%M%SZ)"
)
```

The helper first checks the exact archive size/SHA-256 and the known OTA and
payload metadata hashes. It then handles only full version-2, minor-0 payloads
and the source-independent REPLACE, REPLACE_BZ and REPLACE_XZ operations.
Every compressed blob and reconstructed partition is SHA-256 checked against
the pinned manifest, with bounded decompression and complete nonoverlapping
destination extents. It never unpacks arbitrary ZIP paths or calls device tools.

The new directory contains `boot.img`, `dtbo.img`, `recovery.img`, `vbmeta.img`,
`vbmeta_system.img` and `stock-images.json`. Share only the small JSON report.
If extraction fails after output creation, keep those files as incomplete; a
success report is written only after all five image hashes pass. Use a new output
directory on retry.

[Seven local tests and a successful real-package extraction](../validation/stock-image-extractor-20261025.json)
cover the complete flow and failure cases. These original files are verified
restore inputs, not a tested full-device restoration procedure. The helper does
not erase, flash, unlock, change slots or boot the phone.

## Catalog helper and validation

`tools/query-stock-firmware.py` remains available if the URL expires or the
catalog needs rechecking. Its API endpoints, models and User-Agent were checked
against the app's own source at
[50327ea3fe9ffdcd4c4134b022793ad3f0d33cf2](https://github.com/oxygen-updater/os-updater/tree/50327ea3fe9ffdcd4c4134b022793ad3f0d33cf2):
`app/build.gradle.kts`, and `apis/ServerApi.kt`, `utils/Network.kt`,
`models/DeviceRequestFilter.kt`, `models/Device.kt`, `models/UpdateMethod.kt`,
`models/UpdateData.kt` under `app/src/main/java/com/oxygenupdater`.

The helper requests only public metadata, resolves EU/full IDs from names,
preserves existing reports and never invokes ADB/fastboot. Six local tests
passed, including the HTTP User-Agent regression, plus the real live query.
An OTA string match in its output is deliberately not a package acceptance flag.

## Next milestone

Preserve the original stock files and establish a restore procedure. Compare
the actual board/DTB/DTBO selection and inspect the built hybris ramdisk before
the first temporary kernel/USB debug-shell experiment. The locked bootloader,
A/B firmware selection, dynamic Android HAL mounts and hardware bring-up in
[BRINGUP.md](BRINGUP.md) remain unresolved. No boot test has been performed.
