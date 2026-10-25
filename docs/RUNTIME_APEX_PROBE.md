# N100: gebaute Android-Runtime vorübergehend prüfen

Der neue [Gerätebericht](../validation/android-bringup-report-user-20261025.json)
bestätigt alle vier schreibgeschützten Slot-B-Mounts und erfolgreichen Cleanup.
Das Stock-System enthält einen Root-Baum mit Android unter `system/`; dieser
darf nicht mit dem gebauten Android-13-HAL-Systembaum gleichgesetzt werden.
Vendor meldet die passende EEA-Firmware mit SDK 30 und Incremental 2310092135.

Jetzt ist die Runtime-Lücke direkt belegt: `/apex` enthält nur `.` und `..`,
`com.android.runtime` und dessen Linker fehlen. Im Android-Build liegt das
passende Runtime-APEX-Paket samt beider ARM-Linker und Bionic-Bibliotheken vor.
Die frühere Ausgabe mit `ls -ld /apex` allein hätte diesen Schluss nicht erlaubt;
die neue Inhalts-/Zielprüfung liefert den Nachweis.

## Umfang

Der folgende Versuch extrahiert nur `apex_payload.img` aus dem bereits
gemeldeten APEX-Paket. Das Paket muss genau 8.187.904 Bytes und SHA-256
`293e90ce25eec01a38b723731eea03d378750e7ee0287df93305d43073e8bdf0`
haben. ZIP-CRC, Payload-Länge, ext4-Magic und die fünf Kern-ELF-Dateien werden
geprüft. `debugfs` liest das Image ohne `-w`; es schreibt nur neue Dateien im
lokalen Bundle-Verzeichnis. Eine OEM-Signaturprüfung wird nicht behauptet.

Das Paket ist die festgelegte Quelle. Die tatsächlichen Kern-Dateihashes aus
dem Payload werden erfasst und mit den zuvor gemeldeten flachen Build-Dateien
verglichen. Unterschiedliche installierte/gebündelte Varianten werden sichtbar
protokolliert; sie werden nicht still auf identische Bytes festgelegt.

Auf dem Telefon wird die Kopie nur im RAM gespeichert und an ein Read-only-
Loop-Gerät gebunden. Ein ext4-Mount mit `ro,noload` liefert den Runtime-Inhalt.
Ein neuer OverlayFS-Blick auf das Sailfish-Rootfs verwendet ausschließlich
tmpfs für Upper- und Work-Verzeichnis. In dieser temporären Ansicht werden
der gebaute HAL-Systembaum nach `/system` und die Runtime an ihren APEX-Pfad
gebunden. Das ursprüngliche `/data` bleibt schreibgeschützt.

Der Test ruft `linker64 --help` und danach `linker64 --list` für vier gebaute
Werkzeuge auf. Das listet Abhängigkeiten; die jeweiligen Dienst-Hauptfunktionen
werden nicht gestartet. LD_LIBRARY_PATH wird nur für diese Android-Linker-
Aufrufe gesetzt. Es wird keine dauerhafte Linker-Konfiguration erfunden.
Fehlende Programme oder Abhängigkeiten bleiben als Ergebnis sichtbar.

Der Exit-Handler versucht, alle eigenen Mounts und das eigene Loop-Gerät zu
entfernen. Andere Loop-Dateien werden nicht abgetrennt. Danach fehlt die Runtime
im ursprünglichen Rootfs weiterhin: dieser Versuch ist noch keine dauerhafte
Installation oder vollständiger Sailfish-/HAL-Start.

## 1. CachyOS: Bundle vorbereiten und bereitstellen

Einen noch laufenden PC-HTTP-Server auf Port 8081 zuerst mit Strg+C beenden.
Dann in einer Bash außerhalb der SDK-Chroots:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 "$N100_PORT/tools/prepare-runtime-probe.py" prepare \
  --android-root "$ANDROID_ROOT" \
  --output-dir "$HOME/n100-build/runtime-probe-v1"
python3 -m http.server 8081 --bind 192.168.2.14 \
  --directory "$HOME/n100-build/runtime-probe-v1"
)
```

Das neue Bundle-Verzeichnis wird nicht überschrieben. `debugfs` gehört auf dem
CachyOS-Host zum bereits für den Imagebau verwendeten e2fsprogs-Paket. Wenn die
Vorbereitung scheitert, die Ausgabe bereitstellen; es wurde dann noch nichts
auf dem Telefon gemacht. Nach erfolgreicher Vorbereitung bleibt dieses Terminal
beim HTTP-Server stehen und muss offen bleiben.

## 2. Telefon: Runtime-Probe ausführen

In der bestehenden Telnet-Shell einmal Enter und `set +e` einzeln eingeben.
Dann:

```sh
/bin/busybox wget -O /tmp/n100-runtime-probe.sh http://192.168.2.14:8081/runtime-probe.sh &&
/bin/sh /tmp/n100-runtime-probe.sh run 2>&1 | tee /tmp/n100-super/runtime-probe.txt
```

Der Helfer lädt das rund 8-MB-Payload selbst, prüft dessen Größe und Hash sowie
die Kern-Dateihashes nach dem Mount erneut. Der Kernel muss das Loop-Gerät als
read-only melden. OverlayFS, Loop und tmpfs-Xattrs sind im geprüften Kernelstand
aktiviert; die konkrete neue Mount-/Linker-Folge ist trotzdem ein Geräteversuch.

Erwartete Markierungen:

```text
N100_LINKER_HELP_EXIT=0
N100_DEPENDENCY_EXIT ... <Status>
N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0
```

Eine fehlende Abhängigkeit ist ein verwertbares Diagnoseergebnis. Ein Ergebnis
0 am Ende allein bedeutet nicht, dass alle Abhängigkeitslisten erfolgreich
waren; deren Einzelstatus bleibt maßgeblich. Bei fehlgeschlagenem Cleanup den
Helfer nicht blind wiederholen. Das ursprüngliche Rootfs bleibt unverändert.

## 3. Separates CachyOS-Terminal: Bericht speichern

Nach Abschluss des Helfers in einer zweiten Host-Bash:

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_capture="$HOME/n100-build/runtime-result-$(date -u +%Y%m%dT%H%M%SZ)"
curl --fail --max-time 30 \
  --output "$n100_capture.txt" \
  http://192.168.2.15:8080/runtime-probe.txt
python3 "$N100_PORT/tools/prepare-runtime-probe.py" report \
  --bundle-dir "$HOME/n100-build/runtime-probe-v1" \
  --probe-log "$n100_capture.txt" \
  --output "$n100_capture.json"
)
```

Die ausgegebene `.json`-Datei im Gespräch bereitstellen. Anschließend kann der
PC-HTTP-Server mit Strg+C beendet werden. Der Telefon-HTTP-Dienst auf Port 8080
und Telnet bleiben für die bestehende Debug-Sitzung verfügbar.

## Quellen und Grenzen

- [APEX-Format](https://source.android.com/docs/core/ota/apex).
- [Android-Linker: --help und --list](https://android.1.googlesource.com/platform/bionic/+/53408dbc63d7aff71cc7900d3aef4e96b7730b3e/linker/linker_main.cpp).
- [OverlayFS und Upper-/Work-Verzeichnisse](https://www.kernel.org/doc/html/v5.6/filesystems/overlayfs.html).

Die Host-Tests umfassen ein echtes ext4-Testimage mit ZIP-Verpackung,
debugfs-Auslesen, ABI-Prüfung, Bundle-Erstellung und Integritätsprüfung des
späteren Berichts. Das tatsächliche N100-APEX-Binary liegt nur auf dem Build-PC;
es wurde hier nicht unabhängig entpackt oder auf dem Gerät ausgeführt.
