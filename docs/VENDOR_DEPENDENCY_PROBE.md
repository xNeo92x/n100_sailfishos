# N100: Stock-Vendor-Abhängigkeiten im RAM prüfen

Die [System-Namespace-Probe](../validation/namespace-device-user-20261026.json)
ist auf dem N100 bestanden. Alle drei Basisdienste lösen ihre Bibliotheken mit
der erzeugten Konfiguration und ohne LD_LIBRARY_PATH auf. Die frühere Warnung
über eine fehlende Konfiguration ist weg. Beide APEX-Loops und alle Mounts wurden
erfolgreich aufgeräumt. Dienst-Hauptfunktionen und systemd wurden nicht gestartet.

Der nächste Test ergänzt den echten Stock-Vendor in derselben temporären Ansicht.
Er prüft ausgewählte reguläre AArch64-Programme aus `/vendor/bin/hw`, deren Namen
`composer`, `allocator`, `audio` oder `sensors` enthalten. Andere Formate und
32-Bit-Programme werden ausdrücklich als übersprungen protokolliert. Es laufen
nur `linker64 --list`-Aufrufe; die Dienste selbst werden nicht gestartet.

## Daten- und Mountumfang

- Das bereits am Gerät geprüfte Namespace-Bundle liefert den gebauten Android-13-
  HAL-Systemteil, Runtime, VNDK-30 und die unveränderte generierte Konfiguration.
  Die frühere strenge NNAPI-Diagnose bleibt weiterhin fehlgeschlagen.
- Slot B, Super-Gerät `8:10`, Größe und der SHA-256 der gesamten bekannten
  405.504-Byte-Metadatenregion müssen unverändert sein. Die geprüfte lineare
  `vendor_b`-Tabelle lautet `0 1856272 linear 8:10 18888896`.
- Native Sailfish-`dmsetup` erstellt ein eigenes Mapping mit zufälligem Namenssuffix
  und `--readonly`. Der Kernel-RO-Status und die tatsächliche Tabelle werden geprüft;
  das Dateisystem wird mit `ro,noload` gemountet. Der Vendor-Fingerprint muss exakt
  zur zuvor ausgelesenen BE2013-Firmware `2310092135` passen.
- `/vendor` und `/odm` aus dem Stock-Vendor werden nur in der RAM-Ansicht eingebunden.
  Der gebaute `/system`-Baum bleibt erhalten. `/data` bleibt read-only.
- Binder-Geräte, der vorhandene SELinux-Status und Android-Systembenutzer/-gruppen
  werden als Vorprüfung protokolliert. Es werden keine UID- oder Geräteknoten geändert.
- Die drei Basisdienste werden erneut geprüft. Danach folgen höchstens 24 passende
  reguläre AArch64-Vendor-Dateien, jeweils mit SHA-256 und eigenem Exitstatus.
  Alle Aufrufe entfernen LD_LIBRARY_PATH, LD_PRELOAD und LD_CONFIG_FILE.
- Cleanup entfernt zuerst Vendor-/ODM-Mounts und das eigene Mapping, solange
  native dmsetup noch verfügbar ist; danach folgen die übrigen Mounts und Loops.
  Kein erzwungenes Mapping-Remove und kein Lazy-/Force-Unmount.

## 1. PC: vorbereiten und bereitstellen

Im aktuellen PC-Terminal; Ubuntu kann weiter verwendet werden. Zuerst den alten
HTTP-Server auf Port 8081 mit Strg+C beenden.

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 "$N100_PORT/tools/prepare-vendor-probe.py" prepare \
  --namespace-bundle "$HOME/n100-build/namespace-probe-20261026" \
  --output-dir "$HOME/n100-build/vendor-probe-20261026"
python3 -m http.server 8081 --bind 192.168.2.14 \
  --directory "$HOME/n100-build/vendor-probe-20261026"
)
```

Das Terminal offen lassen. Ein Neubau ist nicht erforderlich. Der Helfer prüft
die bisher erfolgreiche Geräteprobe, das genaue Quell-Bundle und alle Dateien
vor und nach dem Kopieren. Vorhandene Ausgabe-Verzeichnisse werden abgelehnt.
Der SHA-256 des neuen Skripts lautet
`7acbcbe673709c2ff0480e565f25d679ab8d4b2c1d0f4fe536e73f7f16fb9193`.

## 2. Telefon: in der bestehenden Telnet-Shell auf Port 23

```sh
mkdir -p /tmp/n100-super
/bin/busybox wget -O /tmp/n100-vendor-probe.sh http://192.168.2.14:8081/vendor-probe.sh &&
echo '7acbcbe673709c2ff0480e565f25d679ab8d4b2c1d0f4fe536e73f7f16fb9193  /tmp/n100-vendor-probe.sh' | sha256sum -c - &&
/bin/sh /tmp/n100-vendor-probe.sh run 2>&1 | tee /tmp/n100-super/vendor-probe.txt
```

Bis zur Rückkehr des Telefon-Prompts warten. Fehlende Bibliotheken und deren
Exitstatus bleiben sichtbar. Auch bei einem solchen Fehler den Bericht sichern.
Bei Cleanup-Fehlern nicht blind wiederholen.

## 3. PC: Bericht speichern

In einem separaten PC-Terminal; der bestehende Telefon-HTTP-Server auf Port 8080
liefert `/tmp/n100-super` aus:

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_capture="$HOME/n100-build/vendor-result-$(date -u +%Y%m%dT%H%M%SZ)"
curl --fail --max-time 30 --output "$n100_capture.txt" \
  http://192.168.2.15:8080/vendor-probe.txt
python3 "$N100_PORT/tools/prepare-vendor-probe.py" report \
  --bundle-dir "$HOME/n100-build/vendor-probe-20261026" \
  --probe-log "$n100_capture.txt" --output "$n100_capture.json"
)
```

Die ausgegebene **JSON-Datei bereitstellen**. Falls der Telefon-HTTP-Server
beendet wurde, dort einmal
`/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super` starten.

Die Auswertung trennt den erneut geprüften Systemteil von den Vendor-Ergebnissen.
Erfolg erfordert mindestens ein ausgewähltes Programm, konsistente Anzahl,
Dateihashes und Einzelstatus, die Metadaten-/RO-/Fingerprint-Prüfmarken sowie
Cleanup 0 und keine Loader-Warnungen. Ein solcher Befund bestätigt weder die
32-Bit-Dienste noch Display, Audio, Sensoren oder vollständige Vendor-Kompatibilität.
NNAPI und der vollständige Sailfish-/systemd-/HAL-Start bleiben offen.
