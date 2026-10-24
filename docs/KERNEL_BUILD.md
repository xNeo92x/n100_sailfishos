# Standalone kernel build

The pinned LineageOS billie2 kernel (4.19.325) was compiled for ARM64 with the
Sailfish configuration fragment. This is a kernel build, not a bootable Sailfish
image. Android HAL, RPMs, rootfs, boot-image packing and hardware tests remain
outstanding. No flash operation was performed.

## Validated outputs

| Output / check | Result |
| --- | --- |
| ARM64 `Image` | 42,949,120 bytes; ARM64 header verified |
| `bengal.dtb` | 279,793 bytes |
| `billie2-qrd-overlay.dtbo` | 186,299 bytes |
| Overlay applied with Android `fdtoverlay` | Exit 0; merged DTB 433,050 bytes |
| Kernel modules | `lcd.ko`, `rmnet_perf.ko`, `rmnet_shs.ko` |
| Local fragment and upstream config checks | Passed; 0 fatal errors, 47 optional advisories |
| Device boot / runtime hardware | Not tested |

Exact input revisions, sizes and SHA-256 hashes are in
[`validation/kernel-build.json`](../validation/kernel-build.json). The final
resolved configuration hash matches the previous configuration-only validation.
Module compilation does not establish that they load or operate on hardware.
The merged DTB is a host validation output, not a selected boot-image payload.

## Reproduce

Use Linux x86_64, Python 3, Git, GCC/G++, GNU make and Perl. The helper downloads
the pinned Android Clang and bison/flex/m4/bc host tools. Allocate roughly 12 GiB
of free space and 8 GiB RAM for this standalone build; a full Android/Sailfish
build needs substantially more, as described in [BUILD.md](BUILD.md).

```bash
python3 tools/build-kernel.py --work-dir "$HOME/n100-kernel-build" --jobs 4
```

The work directory must be outside this repository. Sources and build outputs
are retained there. Existing checkouts must match the pinned revision and have
no tracked edits. The generated defconfig may remain as an untracked file; the
helper refuses to overwrite it with different content. Use a fresh work directory
after changing inputs. Build logs and `build-result.json` are under
`kernel-config-output/`.

Kernel build metadata uses a fixed username, hostname and source-commit timestamp.
Hashes document this particular build; byte-identical output across different
paths, host compilers and environments has not been established.

The repository also provides the manually triggered **Build experimental kernel**
GitHub Actions workflow. It compiles these outputs and retains them as workflow
artifacts for 30 days. It does not publish a release or perform a device boot.
The binary outputs from the local validation run are not committed into Git.

## Device-tree compiler choice

An initial attempt using the kernel's in-tree DTC failed on repeated labels in
overlay fragments. The Lineage Android kernel build uses its external Android
DTC through `DTC_EXT`. Using the pinned `LineageOS/android_external_dtc` succeeds
without editing DTS files or forcing invalid output. Both the standalone helper
and the local Android manifest now pin that compiler source revision.

The helper generates the parser header expected by Android's lexer before building
the host DTC. Two GCC warning categories are exempted from `-Werror` for this old
host tool (`sign-compare`, `cast-qual`); kernel errors are not suppressed.

The stock device runs 4.19.152-perf+. Keep the newly built kernel and its modules
together. A shared 4.19 version family does not establish stock-module ABI
compatibility. Integrating this output with Android 13 HAL and Android 11 stock
firmware still requires the full build and on-device bring-up.
