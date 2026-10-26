#!/usr/bin/env bash
# Build native APEX providers, inspect their contents and regenerate the PC preview.
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
export PATH="/usr/sbin:/sbin:$PATH"
[[ "${MERSDKUBU:-}" == 1 ]] || { echo 'Enter Ubuntu HABUILD_SDK through the Platform SDK first.' >&2; exit 1; }
: "${ANDROID_ROOT:?Source n100-build/build.env first}"
command -v debugfs >/dev/null || { echo 'Install e2fsprogs inside Ubuntu first.' >&2; exit 1; }
cd "$ANDROID_ROOT"
python3 "$port_dir/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
work_dir="${N100_LINKER_PROVIDER_WORK:-$HOME/n100-build/linker-providers-$(date -u +%Y%m%dT%H%M%SZ)}"
[[ ! -e "$work_dir" && ! -L "$work_dir" ]] || { echo 'Provider work directory already exists; choose a new one.' >&2; exit 1; }
mkdir -p "$work_dir"
echo "Build- und Prüfverzeichnis: $work_dir"
source build/envsetup.sh
breakfast billie2
mka com.android.art com.android.neuralnetworks com.android.i18n com.android.os.statsd \
    2>&1 | tee "$work_dir/build.log"
python3 "$port_dir/tools/check-linker-providers.py" \
    --android-root "$ANDROID_ROOT" --output-dir "$work_dir/provider-inputs"
repo manifest -r > "$work_dir/resolved-manifest.xml"
python3 "$port_dir/tools/generate-linker-preview.py" \
    --android-root "$ANDROID_ROOT" \
    --hal-bundle "$HOME/n100-build/runtime-rpm-probe-20261026" \
    --input-report "$ANDROID_ROOT/n100-build-logs/linker-inputs.json" \
    --provider-report "$work_dir/provider-inputs/report.json" \
    --output-dir "$work_dir/preview"
echo "Provider build and PC preview finished. Share $work_dir/preview/report.json"
