# Kernel configuration validation

The exact kernel source pinned by `sources.lock.json` was checked out and its
generated `vendor/sailfish-billie2_defconfig` passed through the real Kconfig
resolver for `ARCH=arm64 OEM_TARGET_PRODUCT=billie2`.

This records the earlier **configuration-only build**. Kernel C/assembly, DTB/DTBO, modules,
Android HAL and boot images were not compiled. No device boot was attempted.
A subsequent binary build is recorded separately in [KERNEL_BUILD.md](KERNEL_BUILD.md).
The exact input/output hashes, compiler and tool revisions of this earlier check are recorded in
[`validation/kernel-config.json`](../validation/kernel-config.json).

## Findings and changes

The initial resolved configuration satisfied the local fragment, but the
upstream `mer_verify_kernel_config` found three fatal requirements not covered
by that fragment:

- `CONFIG_DUMMY=y`: upstream Sailfish checks require this disabled.
- `CONFIG_STATIC_USERMODEHELPER=y`: stock Android forces kernel userspace-helper
  calls through an Android-specific helper; upstream Sailfish checks require it
  disabled for its own userspace.
- Missing `CONFIG_NLS_UTF8`: upstream checks require UTF-8 filename support.

The fragment now explicitly disables the first two and enables NLS UTF-8.
It also enables the Bluetooth core, virtual HCI driver required by Bluebinder,
RFCOMM, BNEP and HIDP. These are configuration prerequisites, not proof that the
stock Bluetooth HAL works with Sailfish.

The configuration was regenerated after those changes. Results:

| Check | Result |
| --- | --- |
| Kernel Kconfig resolution | Succeeded |
| All local fragment requirements retained | Succeeded |
| Pinned upstream checker exit status | 0 |
| Fatal upstream checker errors | 0 |
| Optional/advisory warnings | 47 |
| Kernel binary or hardware validation | Not performed |

The remaining warnings are listed by symbol in the validation JSON. They include
optional network filesystems, optional networking/HID features and legacy or
architecture-specific advisories. The SELinux boot-parameter default remains
enabled despite the checker's optional suggestion to disable it: the Android
HAL bring-up keeps SELinux present and uses permissive mode in the kernel
command line. Warnings were not hidden or converted to passed features.

## Reproduction

Generate the merged defconfig as the `prepare` step does, keeping the upstream
base unchanged. With the toolchain and host Kconfig dependencies in PATH:

```bash
make -C "$ANDROID_ROOT/kernel/oneplus/sm4250" \
  O="$HOME/n100-kernel-config-check" \
  ARCH=arm64 OEM_TARGET_PRODUCT=billie2 CC=clang LD=ld.lld \
  HOSTCC=gcc HOSTCXX=g++ LLVM=1 LLVM_IAS=1 \
  CROSS_COMPILE=aarch64-linux-gnu- vendor/sailfish-billie2_defconfig

python3 "$N100_PORT/tools/n100.py" check-kernel "$HOME/n100-kernel-config-check/.config"
perl "$ANDROID_ROOT/hybris/mer-kernel-check/mer_verify_kernel_config" \
  "$HOME/n100-kernel-config-check/.config"
```

The check used Android Clang r416183b and the Android prebuilt bison/flex/m4
tools. When using those host tools outside an Android build shell, set
`BISON_PKGDATADIR` to their `common/bison` directory and `M4` to their `m4` binary.
Tool paths and compiler versions can affect Kconfig results, so compare the
recorded inputs before expecting identical output hashes.

The HAL build wrapper now also runs the pinned upstream checker against the
final kernel `.config` and retains its log. That is necessary because the real
Android build environment may resolve additional options differently from this
standalone configuration check.
