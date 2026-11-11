#!/usr/bin/env bash
# One-command launcher: build the Sailfish rootfs (Platform SDK, mic) with the current local
# RPM repo, verify that it contains the reviewed HAL RPM, and create the experimental
# userdata.img on CachyOS. Nothing is written to the phone.
# Run on the CachyOS host:  bash ~/n100_sailfishos/tools/run-rootfs-image-build.sh
set -eo pipefail
self=$(readlink -f -- "${BASH_SOURCE[0]}")
port_dir=$(cd -- "$(dirname -- "$self")/.." && pwd)
stage=${1:-host}
# shellcheck disable=SC1091
source "$HOME/n100-build/build.env"
hal_version=0.0.6-202611111916

case "$stage" in
  host)
    if [[ -n "${MERSDKUBU:-}" || -n "${SAILFISH_SDK:-}" ]]; then
      echo 'Bitte auf CachyOS starten, nicht innerhalb eines SDK.' >&2; exit 1
    fi
    [[ -f "$ANDROID_ROOT/droid-local-repo/billie2/droid-hal-billie2-$hal_version.aarch64.rpm" ]] || {
      echo "Erwartetes HAL-RPM $hal_version fehlt im lokalen Repo." >&2; exit 1; }
    stamp=$(date -u +%Y%m%dT%H%M%SZ)
    log="$HOME/n100-build/rootfs-image-console-$stamp.log"
    echo "[CachyOS] Starte Platform SDK für den Rootfs-Bau. Gesamtprotokoll: $log"
    echo "[CachyOS] Falls nach dem Passwort gefragt wird: CachyOS-Passwort eingeben."
    set +e
    "$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot" bash "$self" platform "$stamp" 2>&1 | tee "$log"
    rc=${PIPESTATUS[0]}
    set -e
    if [[ $rc -ne 0 ]]; then
      echo; echo "[CachyOS] FEHLGESCHLAGEN beim Rootfs-Bau (Exit $rc). Protokoll: $log"; exit "$rc"
    fi
    rootfs_dir="$ANDROID_ROOT/SailfishOScommunity-release-5.1.0.11-billie2-rootfs-$stamp"
    archive="sfe-billie2-5.1.0.11-rootfs-$stamp.tar.bz2"
    set +e
    (
      set -e
      echo "[CachyOS] Prüfe Rootfs-Archiv und Paketliste ..."
      (cd "$rootfs_dir" && sha256sum -c "$archive.sha256")
      packages=$(ls "$rootfs_dir"/*.packages)
      grep -Fx "droid-hal-billie2.aarch64 $hal_version" "$packages" || {
        echo "Rootfs enthält nicht droid-hal-billie2 $hal_version:" >&2
        grep '^droid-hal-billie2\.' "$packages" >&2 || :
        exit 1; }
      image_dir="$HOME/n100-build/images/billie2-$stamp"
      echo "[CachyOS] Erzeuge userdata.img (sudo) in $image_dir ..."
      sudo python3 "$port_dir/tools/make-userdata-image.py" \
        --rootfs "$rootfs_dir/$archive" --boot "$rootfs_dir/hybris-boot.img" \
        --output-dir "$image_dir" --size-gib 8
      echo "Image-Verzeichnis: $image_dir"
    ) 2>&1 | tee -a "$log"
    rc=${PIPESTATUS[0]}
    set -e
    echo
    if [[ $rc -eq 0 ]]; then echo '[CachyOS] FERTIG: Rootfs und userdata.img gebaut. Auf das Telefon wurde nichts geschrieben.'
    else echo "[CachyOS] FEHLGESCHLAGEN bei Prüfung oder Image-Erstellung (Exit $rc). Protokoll: $log"; fi
    exit "$rc"
    ;;
  platform)
    stamp=${2:?missing stamp}
    echo "[Platform SDK] $(. /etc/os-release; echo "$PRETTY_NAME")"
    export RELEASE=5.1.0.11
    export EXTRA_NAME="-rootfs-$stamp"
    mkdir -p "$ANDROID_ROOT/n100-build-logs"
    exec bash "$N100_PORT/tools/build-packages.sh" rootfs
    ;;
  *) echo "Unbekannte Stufe: $stage" >&2; exit 2 ;;
esac
