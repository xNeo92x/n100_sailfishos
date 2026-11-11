#!/usr/bin/env bash
# CachyOS helper for vendor probe V3 (stock vendor + RAM-only libGLESv3).
#   boot     temporary debug boot via fastboot (no partition is written)
#   serve    prepare/verify the V3 bundle, configure USB network, serve on :8081
#   collect  fetch the phone log from :8080 and write the evaluated JSON report
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck disable=SC1091
source "$HOME/n100-build/build.env"
bundle="$HOME/n100-build/vendor-probe-v3-20261111"
v2="$HOME/n100-build/vendor-probe-v2-20261026"
delta="$HOME/n100-build/graphics-runtime-20261111T175514Z/graphics-runtime.tar.gz"
pc_ip=192.168.2.14
phone_ip=192.168.2.15

usb_iface() {
  local found=() dev
  for dev in /sys/class/net/*; do
    [[ -e "$dev/device" ]] || continue
    [[ $(readlink -f "$dev/device") == */usb* ]] || continue
    found+=("${dev##*/}")
  done
  if [[ ${#found[@]} -ne 1 ]]; then
    echo "USB-Netz des Telefons nicht eindeutig gefunden (${found[*]:-keins})." >&2
    echo 'Ist das Telefon im Debug-Start und per USB verbunden? Ausgabe von "ip -br addr" an Claude geben.' >&2
    exit 1
  fi
  printf '%s\n' "${found[0]}"
}

case "${1:-}" in
  boot)
    echo '[CachyOS] Prüfe Fastboot-Verbindung, Slot und Debug-Bundle ...'
    python3 "$port_dir/tools/prepare-first-boot.py" check \
      --bundle "$HOME/n100-build/first-device-test" \
      --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z"
    echo '[CachyOS] Lade den Debug-Kernel nur in den Arbeitsspeicher (keine Partition wird beschrieben).'
    fastboot --verbose boot "$HOME/n100-build/first-device-test/debug-boot.img"
    echo '[CachyOS] Fertig. 30-60 Sekunden warten, dann: bash ~/n100_sailfishos/tools/run-vendor-v3-probe.sh serve'
    ;;
  serve)
    if [[ -e "$bundle" ]]; then
      echo '[CachyOS] Vorhandenes V3-Bundle wird geprüft ...'
      python3 - "$port_dir" "$bundle" <<'EOF'
import importlib.util, sys
from pathlib import Path
s = importlib.util.spec_from_file_location('v3', Path(sys.argv[1]) / 'tools/prepare-graphics-vendor-probe.py')
v3 = importlib.util.module_from_spec(s); s.loader.exec_module(v3)
r, m = v3.vp.reference(); b = v3.verify_bundle(Path(sys.argv[2]), r, m, v3.graphics())
print('Bundle unverändert. vendor-probe-v3.sh SHA-256: ' + b['script_sha256'])
EOF
    else
      python3 "$port_dir/tools/prepare-graphics-vendor-probe.py" prepare \
        --v2-bundle "$v2" --graphics-archive "$delta" --output-dir "$bundle"
    fi
    iface=$(usb_iface)
    echo "[CachyOS] USB-Netz: $iface (sudo-Passwort kann abgefragt werden)"
    sudo ip link set dev "$iface" up
    sudo ip addr replace "$pc_ip/24" dev "$iface"
    ping -I "$iface" -c 3 -W 2 "$phone_ip"
    echo
    echo '[CachyOS] Dateiserver läuft. Dieses Fenster OFFEN LASSEN.'
    echo "[CachyOS] In einem zweiten Terminal: telnet $phone_ip 23  -> dann den Telefon-Block aus docs/GRAPHICS_VENDOR_PROBE.md einfügen."
    echo '[CachyOS] Nach dem Telefontest hier mit Strg+C beenden.'
    exec python3 -m http.server 8081 --bind "$pc_ip" --directory "$bundle"
    ;;
  collect)
    out="$HOME/n100-build/vendor-v3-result-$(date -u +%Y%m%dT%H%M%SZ)"
    curl --fail --max-time 30 --output "$out.txt" "http://$phone_ip:8080/vendor-probe-v3.txt"
    python3 "$port_dir/tools/prepare-graphics-vendor-probe.py" report \
      --bundle-dir "$bundle" --probe-log "$out.txt" --output "$out.json"
    echo "[CachyOS] Ergebnis gespeichert: $out.json"
    ;;
  *)
    echo 'Aufruf: run-vendor-v3-probe.sh boot | serve | collect' >&2; exit 2 ;;
esac
