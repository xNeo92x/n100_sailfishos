# N100: generierte Linker-Konfiguration im RAM prüfen

## Ergebnis auf dem N100

Der [Nutzerlauf](../validation/namespace-device-user-20261026.json) ist erfolgreich:
alle drei Abhängigkeitslisten und beide Hilfsaufrufe mit Status 0, keine
Loader-Warnung, Cleanup 0. Runtime und VNDK waren read-only gemountet; die
generierte Konfiguration funktioniert für diesen begrenzten Systemdienst-Test
ohne LD_LIBRARY_PATH. Weiter mit der [Stock-Vendor-Abhängigkeitsprobe](VENDOR_DEPENDENCY_PROBE.md).
Die folgenden Schritte dokumentieren den abgeschlossenen Test.

Die [native Host-Vorschau](../validation/native-linker-preview-user-20261026.json)
erzeugt die Konfiguration. 245 reguläre HAL-ELFs wurden mit readelf untersucht;
keines nennt `libdexfile.so` oder `libneuralnetworks.so` als direkte Abhängigkeit.
Die flachen Abhängigkeitsketten der drei Basisdienste sind vollständig.
Das beweist keine dynamischen `dlopen`-Aufrufe und keine Stock-Vendor-Kompatibilität.

**Die strenge Generatorprüfung bleibt fehlgeschlagen:** NNAPI fehlt in drei
Anforderungen der System-/Vendor-/Unrestricted-Sektionen. Diese Fehler werden
nicht entfernt und kein Ersatz-Provider wird vorgetäuscht. Für den begrenzten
Test der drei Basisdienste wird die unveränderte normale Konfiguration verwendet.
Das Ergebnis kann keine Freigabe der Vendor-/NNAPI-Namespaces liefern.

## Umfang

Die bewährte RAM-Ansicht erhält zusätzlich das echte, per SHA-256 geprüfte
VNDK-30-Payload und alle drei generierten Linker-Dateien. Runtime und VNDK
werden über eigene Read-only-Loops als `ext4 ro,noload` gemountet. `/data` bleibt
read-only; `/linkerconfig` existiert nur in der temporären Overlay-Ansicht.
Die Konfiguration wird bytegleich aus dem gespeicherten Vorschau-Bericht
übernommen und zusätzlich mit den lokalen erzeugten Dateien verglichen.

`linker64 --list` prüft servicemanager, hwservicemanager und logd. Vor dem Aufruf
werden `LD_LIBRARY_PATH`, `LD_PRELOAD` und `LD_CONFIG_FILE` entfernt. Der Sammler
verlangt die neue Profilkennung, alle Datei-Prüfmarken, beide Hilfsstatus, alle
drei Abhängigkeitsstatus und Cleanup 0. Warnungen über fehlende oder verworfene
Konfiguration verhindern ein positives Ergebnis. Die Dienste werden nicht
gestartet. Der EXIT-Trap löst eigene Mounts und beide eigenen Loop-Geräte; große
RAM-Dateien werden nur nach erfolgreichem Cleanup entfernt.

## 1. Am PC vorbereiten

Das aktuelle Ubuntu-Terminal kann verwendet werden; es ist kein neuer Build
nötig. Den älteren PC-HTTP-Server auf Port 8081 zuerst mit Strg+C beenden.
USB-Verbindung und frühe Telefon-Debugshell bleiben bestehen.

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only

python3 "$N100_PORT/tools/prepare-namespace-probe.py" prepare \
  --preview-dir "$HOME/n100-build/native-linker-preview-20261026T205157Z" \
  --hal-bundle "$HOME/n100-build/native-runtime-probe-20261026" \
  --output-dir "$HOME/n100-build/namespace-probe-20261026"

python3 -m http.server 8081 --bind 192.168.2.14 \
  --directory "$HOME/n100-build/namespace-probe-20261026"
)
```

Das Terminal offen lassen. Der Helfer prüft den genauen Vorschau-Bericht,
das bereits am Telefon geprüfte HAL-Bundle, beide APEX-Payloads und die erzeugten
Dateien. Vor und nach dem Kopieren werden die Transport-Pins geprüft. Vorhandene
Ausgabeverzeichnisse werden nicht überschrieben. Das neue Skript hat SHA-256
`6175afdc0a5a3b4a2b6ed3053c1edf1bd628480ab88c6945b288878ba0f50b4f`.

## 2. In der bestehenden Telefon-Telnet-Shell auf Port 23

```sh
mkdir -p /tmp/n100-super
/bin/busybox wget -O /tmp/n100-namespace-probe.sh http://192.168.2.14:8081/namespace-probe.sh &&
echo '6175afdc0a5a3b4a2b6ed3053c1edf1bd628480ab88c6945b288878ba0f50b4f  /tmp/n100-namespace-probe.sh' | sha256sum -c - &&
/bin/sh /tmp/n100-namespace-probe.sh run 2>&1 | tee /tmp/n100-super/namespace-probe.txt
```

Bis zur Rückkehr des Telefon-Prompts warten. Erfolg erfordert insbesondere:

```text
N100_NAMESPACE_CONFIG_READY
N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/servicemanager 0
N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/hwservicemanager 0
N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/logd 0
N100_NAMESPACE_PROBE_RESULT exit=0 cleanup_failed=0
```

Auch ein Fehlschlag ist ein verwertbares Diagnoseergebnis. Bei Cleanup-Fehlern
die Ausgabe sichern und nicht blind wiederholen.

## 3. In einem separaten PC-Terminal Bericht speichern

Der bestehende Telefon-HTTP-Server auf Port 8080 liefert `/tmp/n100-super` aus.
Falls er beendet wurde, auf dem Telefon einmal
`/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super` starten.

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_capture="$HOME/n100-build/namespace-result-$(date -u +%Y%m%dT%H%M%SZ)"
curl --fail --max-time 30 --output "$n100_capture.txt" \
  http://192.168.2.15:8080/namespace-probe.txt
python3 "$N100_PORT/tools/prepare-namespace-probe.py" report \
  --bundle-dir "$HOME/n100-build/namespace-probe-20261026" \
  --probe-log "$n100_capture.txt" --output "$n100_capture.json"
)
```

Die ausgegebene JSON-Datei bereitstellen. Ein positiver Probe-Befund bedeutet
nur die Auflösung der drei AArch64-Systemdienste mit dieser Konfiguration.
NNAPI, Vendor-Namespaces, automatische DM-/APEX-Integration und der vollständige
Sailfish-/systemd-Start bleiben offen.
