#!/usr/bin/env bash
# Rebuild the native Runtime APEX, without asking for the full ART package.
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
export PATH="/usr/sbin:/sbin:$PATH"
[[ "${MERSDKUBU:-}" == 1 ]] || { echo 'Enter Ubuntu HABUILD_SDK through the Platform SDK first.' >&2; exit 1; }
: "${ANDROID_ROOT:?Source n100-build/build.env first}"
command -v debugfs >/dev/null || { echo 'Install e2fsprogs inside Ubuntu first.' >&2; exit 1; }
cd "$ANDROID_ROOT"
python3 "$port_dir/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
python3 "$port_dir/tools/prepare-native-unwind.py" --android-root "$ANDROID_ROOT"
work_dir="${N100_NATIVE_RUNTIME_WORK:-$HOME/n100-build/native-runtime-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ ! -e "$work_dir" && ! -L "$work_dir" ]] || { echo 'Runtime work directory already exists; choose a new one.' >&2; exit 1; }
mkdir -p "$work_dir"
echo "Build- und Prüfverzeichnis: $work_dir"
source build/envsetup.sh
breakfast billie2
mka libunwindstack crash_dump libc_malloc_debug com.android.runtime hwservicemanager logd linkerconfig \
    2>&1 | tee "$work_dir/build.log"
python3 "$port_dir/tools/check-hal-runtime.py" --android-root "$ANDROID_ROOT" --output "$work_dir/hal-outputs.json"
python3 "$port_dir/tools/check-native-runtime.py" --android-root "$ANDROID_ROOT" \
    --hal-report "$work_dir/hal-outputs.json" --output "$work_dir/report.json"
repo manifest -r > "$work_dir/resolved-manifest.xml"
echo "Native Runtime build finished. Share $work_dir/report.json"
