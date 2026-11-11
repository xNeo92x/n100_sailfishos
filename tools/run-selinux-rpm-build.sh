#!/usr/bin/env bash
# One-command launcher: build the platform SELinux context files (Ubuntu HABUILD SDK),
# rebuild the droid-hal-billie2 RPM (Platform SDK) and compare it on CachyOS with the
# device-tested RPM. Expected additions: libGLESv3 (64/32-bit) and three context files.
# Run on the CachyOS host:  bash ~/n100_sailfishos/tools/run-selinux-rpm-build.sh
set -eo pipefail
self=$(readlink -f -- "${BASH_SOURCE[0]}")
port_dir=$(cd -- "$(dirname -- "$self")/.." && pwd)
stage=${1:-host}
# shellcheck disable=SC1091
source "$HOME/n100-build/build.env"
reference="$HOME/n100-build/rpm-archive/droid-hal-billie2-0.0.6-202610262022.aarch64.rpm"
reference_sha=b6714b06b16f38e25d0298e4efe4e39b63b1b92128678e49c00b49b7fd5a3bc6
gles_rpm=droid-hal-billie2-0.0.6-202611111819.aarch64.rpm

case "$stage" in
  host)
    if [[ -n "${MERSDKUBU:-}" || -n "${SAILFISH_SDK:-}" ]]; then
      echo 'Bitte auf CachyOS starten, nicht innerhalb eines SDK.' >&2; exit 1
    fi
    [[ "$(sha256sum < "$reference")" == "$reference_sha  -" ]] || { echo 'Gesichertes Referenz-RPM fehlt oder ist verändert.' >&2; exit 1; }
    # Keep the previous GLES RPM too; DHD deletes older main RPMs when it rebuilds.
    repo="$ANDROID_ROOT/droid-local-repo/billie2"
    [[ -f "$HOME/n100-build/rpm-archive/$gles_rpm" || ! -f "$repo/$gles_rpm" ]] || cp "$repo/$gles_rpm" "$HOME/n100-build/rpm-archive/"
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    log="$HOME/n100-build/selinux-rpm-console-$stamp.log"
    echo "[CachyOS] Starte Platform SDK. Gesamtprotokoll: $log"
    echo "[CachyOS] Falls nach dem Passwort gefragt wird: CachyOS-Passwort eingeben."
    set +e
    "$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot" bash "$self" platform 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
    if [[ $rc -ne 0 ]]; then
      echo; echo "[CachyOS] FEHLGESCHLAGEN beim Bauen (Exit $rc). Protokoll: $log"; exit "$rc"
    fi
    echo "[CachyOS] RPM gebaut. Vergleiche mit dem am Telefon getesteten Paket ..."
    set +e
    python3 "$port_dir/tools/check-gles-hal-rpm.py" --with-selinux-contexts --reference "$reference" \
      --repo "$repo" --output-dir "$HOME/n100-build/selinux-hal-rpm-$stamp" 2>&1 | tee -a "$log"
    rc=${PIPESTATUS[0]}
    set -e
    echo
    if [[ $rc -eq 0 ]]; then echo '[CachyOS] FERTIG: Kontextdateien und RPM gebaut und geprüft.'
    else echo "[CachyOS] RPM gebaut, aber die Prüfung meldet Abweichungen (Exit $rc). Protokoll: $log"; fi
    exit "$rc"
    ;;
  platform)
    echo "[Platform SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    echo '[Platform SDK] Starte Ubuntu HABUILD SDK für die SELinux-Kontextdateien ...'
    /usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu" bash "$self" habuild
    echo '[Platform SDK] Zurück im Platform SDK. Baue das HAL-RPM ...'
    export RELEASE=5.1.0.11
    exec bash "$N100_PORT/tools/build-packages.sh" hal
    ;;
  habuild)
    [[ "${MERSDKUBU:-}" == 1 ]] || { echo 'HABUILD SDK nicht erreicht.' >&2; exit 1; }
    export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
    echo "[HABUILD SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    exec bash "$N100_PORT/tools/build-hal.sh" runtime
    ;;
  *) echo "Unbekannte Stufe: $stage" >&2; exit 2 ;;
esac
