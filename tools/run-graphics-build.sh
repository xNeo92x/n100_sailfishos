#!/usr/bin/env bash
# One-command launcher for the targeted libGLESv3 build.
# Run on the CachyOS host:  bash ~/n100_sailfishos/tools/run-graphics-build.sh
# It enters the Sailfish Platform SDK, then the Ubuntu HABUILD SDK, and runs
# tools/build-graphics-runtime.sh there. The whole console output is also
# written to $HOME/n100-build/graphics-console-<UTC>.log.
set -eo pipefail
self=$(readlink -f -- "${BASH_SOURCE[0]}")
stage=${1:-host}

load_env() {
  # shellcheck disable=SC1091
  source "$HOME/n100-build/build.env"
}

case "$stage" in
  host)
    if [[ -n "${MERSDKUBU:-}" || -n "${SAILFISH_SDK:-}" ]]; then
      echo 'Bitte auf CachyOS starten, nicht innerhalb eines SDK.' >&2; exit 1
    fi
    load_env
    log="$HOME/n100-build/graphics-console-$(date -u +%Y%m%dT%H%M%SZ).log"
    echo "[CachyOS] Starte Platform SDK. Gesamtprotokoll: $log"
    echo "[CachyOS] Falls nach dem Passwort gefragt wird: CachyOS-Passwort eingeben."
    set +e
    "$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot" bash "$self" platform 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
    echo
    if [[ $rc -eq 0 ]]; then
      echo "[CachyOS] FERTIG: Build und Prüfung erfolgreich (Exit 0)."
    else
      echo "[CachyOS] FEHLGESCHLAGEN (Exit $rc). Das Protokoll liegt unter $log"
    fi
    exit "$rc"
    ;;
  platform)
    load_env
    echo "[Platform SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    exec /usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu" bash "$self" habuild
    ;;
  habuild)
    [[ "${MERSDKUBU:-}" == 1 ]] || { echo 'HABUILD SDK nicht erreicht.' >&2; exit 1; }
    load_env
    export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
    echo "[HABUILD SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    exec bash "$N100_PORT/tools/build-graphics-runtime.sh"
    ;;
  *)
    echo "Unbekannte Stufe: $stage" >&2; exit 2
    ;;
esac
