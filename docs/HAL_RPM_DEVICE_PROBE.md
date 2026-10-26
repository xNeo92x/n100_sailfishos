# N100: aktualisiertes HAL-RPM im RAM prüfen

Der korrigierte HAL-RPM-Bau wurde vom Nutzer als erfolgreich gemeldet. Der
[Nachweis](../validation/runtime-service-rpm-build-user-20261026.json) beschreibt
diese Terminalmeldung; die genauen Paketbytes werden im folgenden Schritt
erstmals mit Dateiliste, Paketkennung und Hash erfasst.

Der Test verwendet die Dateien des neu gebauten RPMs. Er ersetzt das bestehende
Sailfish-Rootfs nicht. `hwservicemanager`, `logd`, ihre RC-Dateien, die beiden
Bibliothekslisten, statisches linkerconfig und Runtime-APEX müssen exakt mit
dem zuvor gemeldeten Buildbericht übereinstimmen.

## Ergebnis auf dem N100

Der [Gerätebericht](../validation/hal-rpm-device-report-user-20261026.json)
bestätigt 260 reguläre Dateien (64.438.497 Bytes) aus dem Haupt-RPM
`droid-hal-billie2-0.0.6-202610261749.aarch64.rpm`. Beide Linker-Hilfsaufrufe
und alle drei Abhängigkeitslisten beenden sich mit 0; Cleanup ist erfolgreich.
Die Dienste wurden dadurch noch nicht gestartet. Die erzeugte Konfiguration
unter `/linkerconfig/ld.config.txt` fehlt im Test weiterhin.

Dieser Versuch ist abgeschlossen. Weiter mit dem
[VNDK-/Namespace-Eingabebuild](LINKER_NAMESPACE_INPUTS.md).

## Vorbereitung und Grenzen

Der Helfer sucht genau ein Hauptpaket `droid-hal-billie2-[0-9]*.aarch64.rpm` im
lokalen Repo. DHD entfernt ältere Hauptpakete vor dem Neubau. Bei Mehrdeutigkeit
stoppt der Helfer; mit `--rpm /voller/Pfad.rpm` kann eine konkrete Datei gewählt
werden. `rpm -qp` prüft Name und Architektur. `rpm2cpio` liest den Payload;
Installationsskripte werden nicht ausgeführt. Paket-Signaturen werden dabei
nicht als verifiziert ausgewiesen.

Der begrenzte CPIO-Leser prüft Pfade, Dateitypen, Größen, Duplikate, CRC-Felder
und harte Links einschließlich später gespeicherter Daten. Er schreibt keine
Datei direkt aus dem CPIO ins Host-Dateisystem. Der ausgewählte HAL-Systembaum
wird in ein neues gzip-Tar gepackt, mit Verzeichnissen und Dateien vor den
Symlinks und einer Prüfsummenliste. Geräte- oder andere Spezialdateien werden
abgelehnt. Die 32-Bit-lib-Dateien werden für diesen AArch64-Test ausgelassen.
Der ausgewählte Baum ist auf 384 MiB begrenzt; der RPM-CPIO-Ausgabestrom auf
512 MiB. Ein bestehendes Bundle-Verzeichnis wird nicht überschrieben.

Das Runtime-Payload ist an den bereits auf dem Gerät verifizierten SHA-256-Hash
gebunden. Deshalb benötigt diese Vorbereitung kein neues debugfs-Auslesen.
Auf dem Telefon werden alle fünf Runtime-Kerndateien erneut geprüft.

Die Dateien werden nur ins Initramfs-RAM geladen. Der neue HAL-Baum wird in
einer tmpfs/OverlayFS-Ansicht an `/system` und
`/usr/libexec/droid-hybris/system` eingebunden. Das ursprüngliche `/data`
bleibt read-only. Der Runtime-Mount bleibt `ro,noload` an einem Read-only-Loop.
Der Test verwendet `linker64 --list` für servicemanager, hwservicemanager und
logd. Er startet deren Dienst-Hauptfunktionen nicht. Linker-Konfiguration,
Stock-Vendor-Mounts und der vollständige systemd-/HAL-Start werden hier noch
nicht integriert. Aus dem Ergebnis folgt keine 32-Bit-Kompatibilitätsaussage.

## 1. Im Platform SDK: Bundle vorbereiten und bereitstellen

Einen noch laufenden PC-HTTP-Server auf Port 8081 zuerst mit Strg+C beenden.
In der Platform-SDK-Shell, in der der HAL-RPM-Bau erfolgt ist:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 "$N100_PORT/tools/prepare-runtime-rpm-probe.py" prepare \
  --android-root "$ANDROID_ROOT" \
  --output-dir "$HOME/n100-build/runtime-rpm-probe-20261026"
python3 -m http.server 8081 --bind 192.168.2.14 \
  --directory "$HOME/n100-build/runtime-rpm-probe-20261026"
)
```

Dieses Terminal beim HTTP-Server offen lassen. Bei einem Vorbereitungsfehler
die Ausgabe bereitstellen; auf dem Telefon ist dann noch nichts geändert.
`rpm` und `rpm2cpio` gehören zur verwendeten SDK-Paketbauumgebung.

## 2. In der bestehenden Telefon-Telnet-Shell

```sh
/bin/busybox wget -O /tmp/n100-runtime-rpm-probe.sh http://192.168.2.14:8081/runtime-probe.sh &&
/bin/sh /tmp/n100-runtime-rpm-probe.sh run 2>&1 | tee /tmp/n100-super/runtime-rpm-probe.txt
```

Der Helfer lädt APEX-Payload und HAL-Archiv selbst. Er prüft beide Download-
Hashes und alle regulären HAL-Dateien. Die erwarteten Einzelstatus sind:

```text
N100_LINKER_HELP_EXIT=0
N100_LINKERCONFIG_HELP_EXIT=0
N100_DEPENDENCY_EXIT /system/bin/servicemanager 0
N100_DEPENDENCY_EXIT /system/bin/hwservicemanager 0
N100_DEPENDENCY_EXIT /system/bin/logd 0
N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0
```

Auch fehlende Abhängigkeiten sind Diagnoseergebnisse. Die letzte Zeile allein
ersetzt deren Einzelstatus nicht. Nach erfolgreichem Cleanup werden die großen
HAL-/Payload-Dateien aus dem eigenen RAM-Arbeitsverzeichnis entfernt; Logs
bleiben erhalten. Bei Cleanup-Fehlern bleiben die Dateien zur Prüfung erhalten.
Dann nicht blind wiederholen.

## 3. Separates CachyOS-Bash-Terminal: Bericht speichern

Nach Rückkehr des Telefon-Prompts:

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_capture="$HOME/n100-build/runtime-rpm-result-$(date -u +%Y%m%dT%H%M%SZ)"
curl --fail --max-time 30 \
  --output "$n100_capture.txt" \
  http://192.168.2.15:8080/runtime-rpm-probe.txt
python3 "$N100_PORT/tools/prepare-runtime-rpm-probe.py" report \
  --bundle-dir "$HOME/n100-build/runtime-rpm-probe-20261026" \
  --probe-log "$n100_capture.txt" \
  --output "$n100_capture.json"
)
```

Die ausgegebene JSON-Datei bereitstellen. Dieser Schritt prüft die Bundle-
Integrität erneut und hängt das Geräteprotokoll an. Er klassifiziert den
Geräteversuch nicht automatisch als erfolgreichen Systemstart.

## Quellen und Host-Tests

- [rpm2cpio](https://rpm.org/docs/4.20.x/man/rpm2cpio.8).
- [Newc-/CRC-Format und harte Links](https://www.kernel.org/doc/html/latest/driver-api/early-userspace/buffer-format.html).
- [Gepinnter DHD-Buildhelfer und lokale RPM-Ausgabe](https://github.com/mer-hybris/droid-hal-device/blob/775f6c05123ca7e58422df1fcfd51943eb56920f/helpers/util.sh).

Host-Tests prüfen CPIO- und Hardlink-Verarbeitung, Pfad- und CRC-Fehler,
Paketauswahl, Build-Hashabweichungen, Tar-Reihenfolge, Bundle-Erzeugung und
Shell-Guards mit Fixtures. Das echte RPM liegt auf dem Nutzer-PC; seine
Auslesung und die neue Mount-/Linkerfolge müssen dort bzw. auf dem Telefon
ausgeführt werden.
