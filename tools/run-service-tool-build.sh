#!/usr/bin/env bash
# One-command launcher: build the AOSP `service` binder client in the Ubuntu HABUILD SDK
# and check it on CachyOS against the device-tested HAL tree. Phone not needed.
# Run on the CachyOS host:  bash ~/n100_sailfishos/tools/run-service-tool-build.sh
set -eo pipefail
self=$(readlink -f -- "${BASH_SOURCE[0]}")
port_dir=$(cd -- "$(dirname -- "$self")/.." && pwd)
stage=${1:-host}
# shellcheck disable=SC1091
source "$HOME/n100-build/build.env"

case "$stage" in
  host)
    if [[ -n "${MERSDKUBU:-}" || -n "${SAILFISH_SDK:-}" ]]; then
      echo 'Bitte auf CachyOS starten, nicht innerhalb eines SDK.' >&2; exit 1
    fi
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    log="$HOME/n100-build/service-tool-console-$stamp.log"
    echo "[CachyOS] Starte Platform SDK. Gesamtprotokoll: $log"
    echo "[CachyOS] Falls nach dem Passwort gefragt wird: CachyOS-Passwort eingeben."
    set +e
    "$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot" bash "$self" platform 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
    if [[ $rc -ne 0 ]]; then
      echo; echo "[CachyOS] FEHLGESCHLAGEN beim Bauen (Exit $rc). Protokoll: $log"; exit "$rc"
    fi
    set +e
    python3 "$port_dir/tools/check-service-tool.py" \
      --binary "$ANDROID_ROOT/out/target/product/billie2/system/bin/service" \
      --hal-archive "$HOME/n100-build/runtime-probe-v5-20261111/hal-system.tar.gz" \
      --output-dir "$HOME/n100-build/service-tool-$stamp" 2>&1 | tee -a "$log"
    rc=${PIPESTATUS[0]}
    set -e
    echo
    if [[ $rc -eq 0 ]]; then echo '[CachyOS] FERTIG: service-Werkzeug gebaut und geprüft.'
    else echo "[CachyOS] Gebaut, aber die Prüfung meldet Probleme (Exit $rc). Protokoll: $log"; fi
    exit "$rc"
    ;;
  platform)
    echo "[Platform SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    exec /usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu" bash "$self" habuild
    ;;
  habuild)
    [[ "${MERSDKUBU:-}" == 1 ]] || { echo 'HABUILD SDK nicht erreicht.' >&2; exit 1; }
    export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
    echo "[HABUILD SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    cd "$ANDROID_ROOT"
    python3 "$N100_PORT/tools/n100.py" verify-tree --android-root "$ANDROID_ROOT"
    mkdir -p n100-build-logs
    source build/envsetup.sh
    breakfast billie2
    mka service 2>&1 | tee n100-build-logs/service-tool.log
    ;;
  *) echo "Unbekannte Stufe: $stage" >&2; exit 2 ;;
esac
