#!/usr/bin/env bash
# Run inside the Android build chroot (HABUILD_SDK).
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
mode=${1:-all}
if [[ $# -gt 1 || ( "$mode" != all && "$mode" != media && "$mode" != boot && "$mode" != runtime ) ]]; then
    echo 'Usage: build-hal.sh [all|media|boot|runtime]' >&2
    exit 2
fi
if [[ ( "$mode" == media || "$mode" == boot || "$mode" == runtime ) && "${MERSDKUBU:-}" != 1 ]]; then
    echo 'Enter the Ubuntu HABUILD_SDK with ubu-chroot before building Android outputs.' >&2
    exit 1
fi
: "${ANDROID_ROOT:?Set ANDROID_ROOT to the prepared Android tree}"
cd "$ANDROID_ROOT"
python3 "$port_dir/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
if [[ "$mode" == all || "$mode" == boot ]]; then
    python3 "$port_dir/tools/boot-debug.py" prepare --android-root "$ANDROID_ROOT"
fi
test -f rpm/droid-hal-billie2.spec
test -f kernel/oneplus/sm4250/arch/arm64/configs/vendor/sailfish-billie2_defconfig
test -s vendor/oneplus/billie2/billie2-vendor.mk || {
    echo 'Missing vendor blobs: collect/import with tools/firmware.py first.' >&2
    exit 1
}
test -s vendor/oneplus/billie2/BoardConfigVendor.mk
if test -f .n100-vendor-source.json; then
    python3 "$port_dir/tools/firmware.py" verify-vendor --android-root "$ANDROID_ROOT"
fi
test -f .n100-hybris-patches-applied || {
    echo 'Apply hybris-patches/apply-patches.sh --mb and record the marker as in docs/BUILD.md.' >&2
    exit 1
}
: "${N100_DEVICE_REPORT:?Source build.env or set N100_DEVICE_REPORT to the saved stock report}"
if [[ "$mode" != runtime ]]; then
    python3 "$port_dir/tools/n100.py" prepare-boot --android-root "$ANDROID_ROOT" \
        --device-report "$N100_DEVICE_REPORT"
fi
mkdir -p n100-build-logs
# Android envsetup is not compatible with set -u.
python3 "$port_dir/tools/fix-webview-link.py" --android-root "$ANDROID_ROOT"
source build/envsetup.sh
# Match build/make/core/ccache.mk: ccache is used only with both variables set.
if [[ -n "${CCACHE_EXEC:-}" && -n "${USE_CCACHE:-}" && "${USE_CCACHE:-}" != false ]]; then
    if ! command -v -- "$CCACHE_EXEC" >/dev/null 2>&1; then
        echo "Configured ccache executable is missing: $CCACHE_EXEC" >&2
        echo "Inside Ubuntu HABUILD_SDK run: sudo apt-get install ccache" >&2
        exit 1
    fi
fi
breakfast billie2
if [[ "$mode" == media ]]; then
    mka droidmedia 2>&1 | tee n100-build-logs/droidmedia.log
elif [[ "$mode" == boot ]]; then
    mka hybris-boot 2>&1 | tee n100-build-logs/boot-debug.log
elif [[ "$mode" == runtime ]]; then
    mka hwservicemanager logd linkerconfig libGLESv3 2>&1 | tee n100-build-logs/runtime.log
else
    mka hybris-hal droidmedia hwservicemanager logd linkerconfig libGLESv3 2>&1 | tee n100-build-logs/hybris-hal.log
fi
if [[ "$mode" == all || "$mode" == runtime ]]; then
    python3 "$port_dir/tools/check-hal-runtime.py" --android-root "$ANDROID_ROOT" \
        --output n100-build-logs/runtime-outputs.json
fi
if [[ "$mode" == all || "$mode" == media ]]; then
    python3 "$port_dir/tools/check-droidmedia.py" --android-root "$ANDROID_ROOT"
fi
if [[ "$mode" == all || "$mode" == boot ]]; then
    python3 "$port_dir/tools/n100.py" check-kernel out/target/product/billie2/obj/KERNEL_OBJ/.config
    perl hybris/mer-kernel-check/mer_verify_kernel_config out/target/product/billie2/obj/KERNEL_OBJ/.config 2>&1 | tee n100-build-logs/kernel-config-check.log
    python3 "$port_dir/tools/n100.py" inspect-boot out/target/product/billie2/hybris-boot.img
    python3 "$port_dir/tools/boot-debug.py" pack \
        --image out/target/product/billie2/hybris-boot.img \
        --kernel-config out/target/product/billie2/obj/KERNEL_OBJ/.config \
        --dtbo out/target/product/billie2/dtbo.img \
        --output "out/target/product/billie2/n100-debug-boot-$(date -u +%Y%m%dT%H%M%SZ).img"
fi
repo manifest -r > n100-build-logs/resolved-manifest.xml
if [[ "$mode" == media ]]; then
    echo 'Android droidmedia build finished. Return to Platform SDK and run build-packages.sh media.'
elif [[ "$mode" == boot ]]; then
    echo 'Early USB debug image built. Share the printed .img.json report. RPM/rootfs were not rebuilt.'
elif [[ "$mode" == runtime ]]; then
    echo 'Android runtime services built and checked. Return to Platform SDK and rebuild the HAL RPM. Phone contents were not changed.'
else
    echo 'HAL and Android droidmedia builds finished. RPM, middleware, rootfs and hardware bring-up remain separate stages.'
fi
