#!/usr/bin/env bash
# Build namespace-generator inputs in Ubuntu HABUILD_SDK; no phone access.
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
export PATH="/usr/sbin:/sbin:$PATH"
[[ "${MERSDKUBU:-}" == 1 ]] || { echo 'Enter Ubuntu HABUILD_SDK through the Platform SDK before building linker inputs.' >&2; exit 1; }
: "${ANDROID_ROOT:?Source n100-build/build.env first}"
cd "$ANDROID_ROOT"
python3 "$port_dir/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
test -f packages/modules/vndk/apex/Android.bp
test -d prebuilts/vndk/v30
command -v debugfs >/dev/null || { echo 'Ubuntu needs debugfs: sudo apt-get install e2fsprogs' >&2; exit 1; }
mkdir -p n100-build-logs
source build/envsetup.sh
breakfast billie2
host_bin=$(get_build_var HOST_OUT_EXECUTABLES)
[[ -n "$host_bin" ]] || { echo 'HOST_OUT_EXECUTABLES is empty' >&2; exit 1; }
# File targets select the actual Linux host variants of the generator tools.
mka com.android.vndk.v30 system_linker_config \
    "$host_bin/linkerconfig" "$host_bin/conv_linker_config" \
    2>&1 | tee n100-build-logs/linker-inputs.log
python3 "$port_dir/tools/check-linker-inputs.py" --android-root "$ANDROID_ROOT" \
    --host-bin "$host_bin" --output n100-build-logs/linker-inputs.json
repo manifest -r > n100-build-logs/linker-inputs-resolved-manifest.xml
echo 'VNDK-30 and host generator inputs built. Share n100-build-logs/linker-inputs.json.'
