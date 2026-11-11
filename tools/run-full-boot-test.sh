#!/usr/bin/env bash
# CachyOS helper for the first full Sailfish boot attempt (docs/FULL_BOOT_TEST.md).
#   flash   preflight, verify images, WRITE the new userdata.img, then RAM-boot the
#           full-boot debug image (boot partition is not written)
#   boot    only the RAM boot (e.g. after a reboot into fastboot)
#   serve   configure the USB network and serve fullboot-diag.sh on :8081 (keep open)
#   collect fetch the diagnostics report from the phone (:8080)
#   serve-image  serve the verified userdata.img on :8081 so the phone can write it itself
#           (bootloader sparse flashing dropped blocks above ~3 GiB; see docs/FULL_BOOT_TEST.md)
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$HOME/n100-build/build.env"
image_dir="$HOME/n100-build/images/billie2-20261111T192343Z"
userdata_sha=ba6eecd996dd7d729cc25601738fba820592c6bc7612b0734379cf92833f18df
boot_img="$HOME/n100-build/first-device-test/full-debug-boot.img"
boot_sha=74d16a51cb4a79c6f67ae7c84ff3cb17722cc6b0cfafee2d543fe2bb8fce84fd
pc_ip=192.168.2.14
phone_ip=192.168.2.15

check_file() {
  echo "[CachyOS] Prüfe $(basename "$1") ..."
  [[ "$(sha256sum < "$1")" == "$2  -" ]] || { echo "Prüfsumme von $1 stimmt nicht." >&2; exit 1; }
}
preflight() {
  python3 "$port_dir/tools/prepare-first-boot.py" check \
    --bundle "$HOME/n100-build/first-device-test" \
    --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z"
}
ram_boot() {
  check_file "$boot_img" "$boot_sha"
  echo '[CachyOS] Lade das Vollstart-Debug-Image nur in den Arbeitsspeicher (Boot-Partition bleibt unverändert).'
  fastboot --verbose boot "$boot_img"
  echo '[CachyOS] Fertig. 30-60 Sekunden warten, dann: bash ~/n100_sailfishos/tools/run-full-boot-test.sh serve'
}

case "${1:-}" in
  flash)
    check_file "$image_dir/userdata.img" "$userdata_sha"
    preflight
    echo
    echo 'ACHTUNG: Jetzt wird die Userdata-Partition des Telefons mit dem neuen Sailfish-Image überschrieben.'
    echo 'Dabei geht das bisherige Sailfish-Testsystem auf userdata verloren (es gibt dort keine Android-Daten mehr).'
    read -r -p 'Zum Fortfahren genau JA eingeben: ' answer
    [[ "$answer" == JA ]] || { echo 'Abgebrochen, nichts geschrieben.'; exit 1; }
    fastboot -S 512M flash userdata "$image_dir/userdata.img"
    ram_boot
    ;;
  boot)
    preflight
    ram_boot
    ;;
  serve)
    found=()
    for dev in /sys/class/net/*; do
      [[ -e "$dev/device" && $(readlink -f "$dev/device") == */usb* ]] && found+=("${dev##*/}")
    done
    [[ ${#found[@]} -eq 1 ]] || { echo "USB-Netz nicht eindeutig gefunden (${found[*]:-keins}). Ausgabe von 'ip -br addr' an Claude geben." >&2; exit 1; }
    echo "[CachyOS] USB-Netz: ${found[0]} (sudo-Passwort kann abgefragt werden)"
    sudo ip link set dev "${found[0]}" up
    sudo ip addr replace "$pc_ip/24" dev "${found[0]}"
    ping -I "${found[0]}" -c 3 -W 2 "$phone_ip" || echo '[CachyOS] Telefon antwortet (noch) nicht auf Ping.'
    serve_dir=$(mktemp -d)
    cp "$port_dir/tools/fullboot-diag.sh" "$serve_dir/"
    echo "[CachyOS] fullboot-diag.sh SHA-256: $(sha256sum < "$serve_dir/fullboot-diag.sh" | cut -d' ' -f1)"
    echo '[CachyOS] Dateiserver läuft. Dieses Fenster OFFEN LASSEN; danach mit Strg+C beenden.'
    exec python3 -m http.server 8081 --bind "$pc_ip" --directory "$serve_dir"
    ;;
  serve-image)
    check_file "$image_dir/userdata.img" "$userdata_sha"
    found=()
    for dev in /sys/class/net/*; do
      [[ -e "$dev/device" && $(readlink -f "$dev/device") == */usb* ]] && found+=("${dev##*/}")
    done
    [[ ${#found[@]} -eq 1 ]] || { echo "USB-Netz nicht eindeutig gefunden (${found[*]:-keins})." >&2; exit 1; }
    sudo ip link set dev "${found[0]}" up
    sudo ip addr replace "$pc_ip/24" dev "${found[0]}"
    serve_dir=$(mktemp -d)
    ln -s "$image_dir/userdata.img" "$serve_dir/userdata.img"
    echo "[CachyOS] userdata.img SHA-256: $userdata_sha"
    echo '[CachyOS] Image-Server läuft. Dieses Fenster OFFEN LASSEN; danach mit Strg+C beenden.'
    exec python3 -m http.server 8081 --bind "$pc_ip" --directory "$serve_dir"
    ;;
  collect)
    out="$HOME/n100-build/fullboot-result-$(date -u +%Y%m%dT%H%M%SZ).txt"
    curl --fail --max-time 30 --output "$out" "http://$phone_ip:8080/diag.txt"
    echo "[CachyOS] Bericht gespeichert: $out ($(wc -c < "$out") Bytes)"
    ;;
  *) echo 'Aufruf: run-full-boot-test.sh flash | boot | serve | serve-image | collect' >&2; exit 2 ;;
esac
