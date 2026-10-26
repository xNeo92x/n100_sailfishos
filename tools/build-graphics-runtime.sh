#!/usr/bin/env bash
# Build the missing real GLES wrapper inside Ubuntu HABUILD_SDK.
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
[[ "${MERSDKUBU:-}" == 1 ]] || { echo 'Enter Ubuntu HABUILD_SDK through Platform SDK first.' >&2; exit 1; }
: "${ANDROID_ROOT:?Source n100-build/build.env first}"
cd "$ANDROID_ROOT"
python3 "$port_dir/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
python3 "$port_dir/tools/prepare-native-unwind.py" --android-root "$ANDROID_ROOT"
python3 "$port_dir/tools/check-graphics-runtime.py" --android-root "$ANDROID_ROOT" --source-only
work_dir="${N100_GRAPHICS_WORK:-$HOME/n100-build/graphics-runtime-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ ! -e "$work_dir" && ! -L "$work_dir" ]] || { echo 'Graphics work directory exists; choose a new one.' >&2; exit 1; }
mkdir -p "$work_dir"
echo "Build- und Prüfverzeichnis: $work_dir"
source build/envsetup.sh
breakfast billie2
mka libGLESv3 2>&1 | tee "$work_dir/build.log"
python3 "$port_dir/tools/check-graphics-runtime.py" --android-root "$ANDROID_ROOT" --output-dir "$work_dir"
repo manifest -r > "$work_dir/resolved-manifest.xml"
echo "Graphics inputs prepared. Share $work_dir/report.json"
