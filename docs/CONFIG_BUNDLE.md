# BE2013 stock configuration findings

The supplied `n100-configs.zip` contains a report, inventory and 33 collected
configuration files. Every collected file's size and SHA-256 matched the
inventory. This verifies internal integrity, not independent authenticity of the
device contents. The derived summary is in
[`devices/be2013-config-summary.json`](../devices/be2013-config-summary.json),
including the archive hash. Raw SELinux policies and contexts are not republished.
The three fstab test fixtures retain their original BSD notices.

## Mount and encryption alternatives

| File | `/data` declaration | Relevant distinction |
| --- | --- | --- |
| `fstab.default` | F2FS, `inlinecrypt`, `fileencryption=ice` | Wrapped keys, metadata key directory, 512M reserve, UFS sysfs path |
| `fstab.emmc` | F2FS, `inlinecrypt`, AES XTS/CTS v2 | `emmc_optimized+wrappedkey_v0`, metadata encryption, 128M reserve |
| `fstab_nodata.qcom` | tmpfs | Does not provide normal persistent userdata |

The files are alternatives. A filename or directory listing does not prove which
one init selects. Their flags are preserved separately, rather than merged into
a guessed configuration. The first two list logical system, system_ext, vendor
and product partitions; the nodata variant omits system_ext. There is no stock
logical ODM entry in these files. The Lineage build's additional ODM image must
be addressed during integration.

Both persistent-data variants refer to `/metadata/vold/metadata_encryption`.
This does not give Sailfish access to Android-encrypted userdata. Physical
userdata and its current decrypted mapper are distinct; mapper numbers must not
be hardcoded. No data-formatting or encryption changes follow from this report.

## VINTF and SELinux

Fragments declare HIDL graphics composer 2.4, allocator/mapper 3.0 and 4.0,
sensors 2.0, Wi-Fi 1.4, GNSS 2.1 and fingerprint 2.1. These declarations do not
establish running services or Sailfish middleware compatibility.

Power and vibrator use **AIDL**, with `IPower/default` and `IVibrator/default`.
Their XML does not specify a version; the inventory preserves this omission.
ODM `manifest_2.xml` declares two radio slots, while `manifest_8.xml` declares
one. Both include radio 1.4 / SAP 1.2 declarations. They represent alternate
SKUs; the active variant is unknown and their services must not be combined.

The stock binary policy header identifies policy version **30**. The separate
compatibility text is **30.0**. These are different fields. Only the binary
header was checked; policy rules and runtime compatibility were not validated.
Do not replace the Android 13 HAL build's policy/contexts with the stock Android
11 files solely because this header is readable.

## Repeat the offline check

```bash
python3 tools/n100.py inspect-configs ../n100-configs.zip --output ../n100-config-audit.json
```

The inspector verifies bounded ZIP members against the inventory without
extracting them. It rejects unexpected paths, symlinks, missing/unlisted members,
duplicates and mismatched hashes/sizes. It parses fstab variants and VINTF
documents and inspects the SELinux policy header. A further ADB report is not
needed for this analysis; subsequent work is full HAL/rootfs integration and
boot validation.
