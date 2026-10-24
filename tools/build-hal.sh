#!/usr/bin/env bash
# Run inside the Android build chroot (HABUILD_SDK).
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
: "${ANDROID_ROOT:?Set ANDROID_ROOT to the prepared Android tree}"
cd "$ANDROID_ROOT"
python3 "$port_dir/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
test -f rpm/droid-hal-billie2.spec
test -f kernel/oneplus/sm4250/arch/arm64/configs/vendor/sailfish-billie2_defconfig
test -f .n100-hybris-patches-applied || {
    echo 'Apply hybris-patches/apply-patches.sh --mb and record the marker as in docs/BUILD.md.' >&2
    exit 1
}
mkdir -p n100-build-logs
# Android envsetup is not compatible with set -u.
source build/envsetup.sh
breakfast billie2
mka hybris-hal 2>&1 | tee n100-build-logs/hybris-hal.log
python3 "$port_dir/tools/n100.py" check-kernel out/target/product/billie2/obj/KERNEL_OBJ/.config
python3 "$port_dir/tools/n100.py" inspect-boot out/target/product/billie2/hybris-boot.img
repo manifest -r > n100-build-logs/resolved-manifest.xml
echo 'HAL build finished. RPM, middleware, rootfs and hardware bring-up remain separate stages.'
