# N100: neues natives Runtime-RPM im RAM testen

## Ergebnis auf dem Gerät

Die [Geräteprobe](../validation/native-runtime-device-user-20261026.json) ist
erfolgreich: neues Payload und alle fünf Kerndateien geprüft, alle drei
Abhängigkeitslisten und beide Hilfsaufrufe mit Status 0, Cleanup erfolgreich.
Die Dienste wurden noch nicht gestartet; die generierte Linker-Konfiguration
fehlt weiterhin. Weiter mit der [neuen Host-Linker-Vorschau](NATIVE_LINKER_PREVIEW.md).
Die folgenden Schritte dokumentieren den abgeschlossenen Probeablauf.

Das [hochgeladene Manifest](../validation/native-runtime-rpm-manifest-user-20261026.json)
bestätigt das Hauptpaket `droid-hal-billie2-0.0.6-202610262022.aarch64.rpm`:
23.065.994 Bytes, SHA-256
`b6714b06b16f38e25d0298e4efe4e39b63b1b92128678e49c00b49b7fd5a3bc6`.
Buildbericht, Runtime-APEX, Payload und flache Bionic-Dateien stimmen überein.
Unter den 260 regulären HAL-Dateien sind seit dem historischen Gerätetest nur
Runtime-APEX, `lib64/libc.so` und `lib64/libunwindstack.so` geändert.
Die Dateien liegen auf dem Nutzer-PC; hier wurde das gemeldete Manifest geprüft.

Die neue Probe übernimmt die bereits verwendete RAM-Mountfolge und prüft nun
ausdrücklich die neuen Pins. `/data` muss weiterhin read-only sein. Die Runtime
wird über einen Read-only-Loop mit `ro,noload` eingebunden, der HAL-Baum in einer
temporären Overlay-Ansicht. Linker-Hilfe, statische linkerconfig-Hilfe und
`linker64 --list` für servicemanager, hwservicemanager und logd werden geprüft.
Die Dienst-Hauptfunktionen und systemd werden nicht gestartet. Cleanup löst die
eigenen Mounts und den eigenen Loop; große RAM-Dateien werden nur bei erfolgreichem
Cleanup entfernt. Reguläre Linker-Namespaces und NNAPI bleiben separate Aufgaben.

## 1. PC: Bundle vorbereiten und HTTP-Server starten

Das geht auf CachyOS oder im Platform SDK; Ubuntu ist nicht erforderlich.
Ein älterer PC-HTTP-Server auf Port 8081 muss zuerst mit Strg+C beendet werden.
Die Telefon-Debugshell und USB-Verbindung müssen wie beim vorigen Test bestehen,
PC-Adresse `192.168.2.14/24` auf `enp16s0u10`.

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 "$N100_PORT/tools/prepare-native-runtime-probe.py" prepare \
  --output-dir "$HOME/n100-build/native-runtime-probe-20261026"
python3 -m http.server 8081 --bind 192.168.2.14 \
  --directory "$HOME/n100-build/native-runtime-probe-20261026"
)
```

Der Helfer findet genau das zuvor geprüfte `native-runtime-rpm-*`-Bundle anhand
des unveränderten Manifest-Hashes. Bei Mehrdeutigkeit explizit `--input-dir`
angeben. Bereits bestehende Ausgaben werden abgelehnt. Payload und Archiv werden
vor und nach dem Kopieren geprüft. Kein erneuter Build und kein rpm-Aufruf nötig.
Das ausgegebene Skript hat SHA-256
`ac463d77799ac68504a85e86ece83c232096c83b53ac42dc3bf953e3a5a275fb`.

## 2. Telefon: in der bestehenden Telnet-Shell auf Port 23

```sh
mkdir -p /tmp/n100-super
/bin/busybox wget -O /tmp/n100-native-runtime-probe.sh http://192.168.2.14:8081/runtime-probe.sh &&
echo 'ac463d77799ac68504a85e86ece83c232096c83b53ac42dc3bf953e3a5a275fb  /tmp/n100-native-runtime-probe.sh' | sha256sum -c - &&
/bin/sh /tmp/n100-native-runtime-probe.sh run 2>&1 | tee /tmp/n100-super/native-runtime-probe.txt
```

Den Test bis zum Telefon-Prompt laufen lassen. Erwartet werden alle drei
`N100_DEPENDENCY_EXIT ... 0`, beide Hilfe-Status 0 und
`N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0`.
Die letzte Zeile allein ist kein Nachweis erfolgreicher Abhängigkeitslisten.
Bei Cleanup-Fehlern die Ausgabe sichern und nicht blind wiederholen.

## 3. PC: Gerätebericht speichern

Der bestehende Telefon-HTTP-Server auf Port 8080 liefert `/tmp/n100-super` aus,
wie beim vorigen RPM-Gerätetest. Falls er inzwischen beendet wurde, in der
Telefon-Shell einmal `/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super`
starten. Ein bereits laufender Server muss nicht erneut gestartet werden.

In einem separaten CachyOS-Bash-Terminal:

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_capture="$HOME/n100-build/native-runtime-result-$(date -u +%Y%m%dT%H%M%SZ)"
curl --fail --max-time 30 --output "$n100_capture.txt" \
  http://192.168.2.15:8080/native-runtime-probe.txt
python3 "$N100_PORT/tools/prepare-native-runtime-probe.py" report \
  --bundle-dir "$HOME/n100-build/native-runtime-probe-20261026" \
  --probe-log "$n100_capture.txt" --output "$n100_capture.json"
)
```

Die ausgegebene JSON-Datei bereitstellen. Der Sammler prüft die Bundle- und
Skriptintegrität erneut und bewahrt das Geräteprotokoll vollständig auf.
`dependency_probe_passed` erfordert das neue Profil, passende Payload- und
Core-Prüfmarken, die HAL-Dateiprüfung, alle Einzelstatus 0 und Cleanup 0 genau
einmal. Alte, abgeschnittene oder widersprüchliche Statusfolgen gelten nicht als
erfolgreich. Ein positiver Befund bedeutet weiterhin keinen vollständigen
Sailfish-/HAL-Start und keine Freigabe als installierbares Release.
