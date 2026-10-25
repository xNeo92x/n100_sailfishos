# N100: Super-Metadaten für die dynamischen Android-Partitionen

Der schreibgeschützte ext4-Mount von Userdata und die Ausführung von Sailfish
OS 5.1.0.11 im Chroot sind am N100 bestätigt.
[Nachweis](../validation/rootfs-chroot-user-20261025.json).
Ein Chroot ist noch kein Start von systemd oder Android-HAL-Diensten.

Für die nächste Integrationsstufe benötigen wir die tatsächlichen LP-Metadaten
aus `super` und die Mount-/Symlink-Pfade im gebauten Rootfs. Es werden keine
Sektoradressen geraten und keine Änderungen an Super vorgenommen.

## 1. In der bestehenden Telnet-Shell auf dem Telefon

Einmal Enter drücken und `set +e` zunächst einzeln eingeben. Dadurch sind
fehlende optionale Diagnosepfade nicht fatal. Keine äußeren Klammern und keine
Prompt-Zeichen mitkopieren. Das vorherige `Q(` war kein gültiger Subshell-Start.

Dann diese Befehle ausführen:

```sh
mkdir -p /tmp/n100-super
grep -qx 'PARTNAME=super' /sys/class/block/sda10/uevent && dd if=/dev/sda10 of=/tmp/n100-super/super-prefix.bin bs=4096 count=512

chroot /data/.stowaways/sailfishos /bin/sh -c '
for p in /system /system_ext /vendor /product /odm /apex /usr/libexec/droid-hybris /usr/libexec/droid-hybris/system /usr/libexec/droid-hybris/vendor /usr/libexec/droid-hybris/apex; do
    ls -ld "$p"
done
ls -l /usr/libexec/droid-hybris/system/bin/linker*
command -v dmsetup
/usr/lib/systemd/systemd --version
' > /tmp/n100-super/rootfs-layout.txt 2>&1

/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super
```

`dd` liest die ersten 2 MiB von der zuvor anhand des Partitionslabels
identifizierten Super-Partition; die Kopie liegt im RAM der Initramfs.
Der Abschnitt kann auch den Beginn von Partitionsinhalt enthalten. Der
Offline-Leser verwendet ausschließlich die durch Geometrie/Header begrenzten
Metadaten und schreibt deren JSON-Auswertung, keine Payload-Dateien.
Das Rootfs bleibt schreibgeschützt. `systemd --version` ist nur eine
Versions-/Ladeprüfung und startet keine Dienste.

Der temporäre BusyBox-HTTP-Dienst ist an die USB-Adresse gebunden und serviert
nur dieses RAM-Verzeichnis. Seine Optionen sind in der
[BusyBox-Dokumentation](https://busybox.net/downloads/BusyBox.html#httpd) beschrieben.
Bei einem Fehler die genaue Ausgabe bereitstellen, keine Partitionen neu flashen.

## 2. In einem separaten CachyOS-Terminal

Die Telnet-Sitzung kann offen bleiben. Direkt auf dem Host eine Bash verwenden:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
n100_capture="$HOME/n100-build/super-prefix-$(date -u +%Y%m%dT%H%M%SZ)"

curl --fail --max-time 30 \
  --output "$n100_capture.bin" \
  http://192.168.2.15:8080/super-prefix.bin
curl --fail --max-time 30 \
  --output "$n100_capture.rootfs.txt" \
  http://192.168.2.15:8080/rootfs-layout.txt

python3 "$N100_PORT/tools/inspect-super.py" "$n100_capture.bin" \
  --slot b \
  --rootfs-layout "$n100_capture.rootfs.txt" \
  --output "$n100_capture.json"
)
```

Die ausgegebene **`.json`-Datei** im Gespräch bereitstellen. Die Binärkopie kann
auf dem PC bleiben. Slot B ist der zuletzt am Gerät bestätigte aktive Slot;
vor zukünftigen Mapping-/Mount-Schritten wird der tatsächliche Slot erneut
am laufenden Kernel geprüft.

## Prüfumfang

`tools/inspect-super.py` arbeitet ausschließlich offline auf einer regulären,
höchstens 16 MiB großen Eingabedatei. Der Leser prüft beide Geometrie-Kopien,
die gewählte Metadaten-Slotnummer, Header-/Tabellen-Prüfsummen, Tabellen- und
Bereichsgrenzen sowie Gruppen-/Gerätezuordnungen. Er unterstützt LP-Versionen
10.0 bis 10.2 und das bestätigte N100-Super-Gerät von 10.737.418.240 Bytes.
Ein zu kurzer Ausschnitt wird mit der erforderlichen Mindestlänge abgelehnt.

Eine gültige primäre Metadaten-Kopie wird bevorzugt; eine gültige Backup-Kopie
wird bei beschädigter primärer Kopie ausgewiesen. Unterschiedliche gültige
Kopien und die Fehler einzelner Kopien bleiben im Bericht sichtbar. Die
Bereichsprüfung ist keine AVB- oder Dateisystemprüfung und erstellt keine
Device-Mapper-Geräte oder Mounts.

Format und Offsets folgen der AOSP-LP-Spezifikation:
[metadata_format.h](https://android.googlesource.com/platform/system/core/+/master/fs_mgr/liblp/include/liblp/metadata_format.h),
[Offsetberechnung](https://android.googlesource.com/platform/system/core/+/f4cd54e51290e011bfbe9786368da42868d30844/fs_mgr/liblp/utility.cpp).
[Elf synthetische Tests](../tests/test_super_metadata.py) decken Prüfsummen,
Grenzen, Backup-Verwendung, divergierende Kopien, Namensprüfung und exklusives
Speichern ab. Echte N100-LP-Metadaten stehen für diesen Leser noch aus.

Nach Auswertung folgen passende Read-only-Mappings und die konkrete frühe
Mount-Integration. Ein vollständiger Sailfish-/HAL-Start bleibt ungetestet.
