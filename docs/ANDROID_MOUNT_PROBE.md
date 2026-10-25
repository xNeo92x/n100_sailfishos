# N100: Slot-B-Android-Partitionen schreibgeschützt prüfen

**Am Gerät abgeschlossen:** Alle vier Dateisysteme wurden read-only gemountet;
`N100_PROBE_RESULT exit=0 cleanup_failed=0` bestätigt den gemeldeten Cleanup.
Der neue Test belegt auch das leere Rootfs-APEX-Verzeichnis und fehlende
Runtime-Linker. [Nachweis](../validation/android-mount-probe-user-20261025.json).
Weiter mit [RUNTIME_APEX_PROBE.md](RUNTIME_APEX_PROBE.md); die folgenden
Probe-Schritte müssen für diesen Befund nicht wiederholt werden.

Der [hochgeladene Bericht](../validation/super-metadata-report-user-20261025.json)
meldet gültige Geometrie-/Header-/Tabellen-Prüfsummen und identische primäre und
Backup-Metadaten für Slot B. Die rohe Binärkopie wurde hier nicht unabhängig
geprüft; der folgende Geräte-Test bindet die Tabellen deshalb an einen frischen
SHA-256-Abgleich der gesamten Metadatenregion.

| Partition | 512-Byte-Sektoren | Start in Super |
| --- | ---: | ---: |
| system_b | 2961328 | 10486976 |
| system_ext_b | 2133024 | 13448384 |
| product_b | 3307080 | 15581632 |
| vendor_b | 1856272 | 18888896 |

Es ist kein ODM-Eintrag vorhanden. Das bisherige Rootfs enthält den gebauten
Android-13-HAL-Systembaum unter `/usr/libexec/droid-hybris/system`, aber kein
`/system` oder `/vendor`. Für `/apex` wurden bisher nur Verzeichnisdaten,
kein Inhalt erfasst. Die Android-Linker verweisen auf
`/apex/com.android.runtime/bin/linker*`; ob die Ziele existieren, ist noch offen.
Der Test erfasst deshalb den tatsächlichen Inhalt und die Zielverfügbarkeit.
Das gestartete `systemd --version` meldet Version 238; das war kein PID-1-Start.

## Testumfang

`tools/probe-android-mounts.sh` ist ein temporärer Test ausschließlich für den
gemeldeten Metadatenstand. Er verweigert andere Slots, Super-Geräte/-Größen,
geänderte Metadaten sowie bereits vorhandene reservierte Probe-Mappings oder
Rootfs-Bindmounts. Die Tabellen sind ein geprüfter Snapshot, keine allgemeine
Implementierung für spätere OTAs.

Der Helfer bindet `/dev`, `/proc` und `/sys` vorübergehend in den vorhandenen
Sailfish-Chroot, damit dessen native `dmsetup`-Datei den Kernel ansprechen kann.
Er nutzt `--noudevsync`, da in der Initramfs kein vollständiges udev läuft.
Alle vier neuen Mappings werden mit `--readonly` erzeugt. Vor jedem Mount muss
auch der Kernel-Read-only-Status des konkreten `dm-*`-Geräts 1 sein.

Die ext4-Mounts erfolgen mit `ro,noload` in einem neuen Verzeichnis unter `/tmp`.
Stock-Systemdateien werden weder nach `/system` noch über den gebauten HAL-Baum
gemountet. Es wird kein Android-Binary ausgeführt. Der Exit-Handler versucht,
die eigenen Scratch-Mounts, Mappings und drei Bindmounts wieder zu entfernen.
Er verwendet dabei weder Force-/Lazy-Unmount noch erzwungenes DM-Remove.
Die vorhandene schreibgeschützte `/data`-Einbindung bleibt bestehen.

Am Ende muss `N100_PROBE_RESULT exit=0 cleanup_failed=0` stehen. Bei Fehlern
oder fehlgeschlagenem Cleanup zuerst die Ausgabe auswerten; den Helfer nicht
blind erneut starten. Der Probe-Test selbst wurde noch nicht auf dem N100
ausgeführt. Grundlagen: [dm-linear](https://docs.kernel.org/admin-guide/device-mapper/linear.html)
und [dmsetup](https://man7.org/linux/man-pages/man8/dmsetup.8.html).

## 1. CachyOS: aktualisieren und den Helfer über USB bereitstellen

In einem Host-Terminal eine Bash verwenden:

```bash
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 -m http.server 8081 --bind 192.168.2.14 --directory "$N100_PORT/tools"
```

Dieses Terminal bleibt beim HTTP-Server stehen. Der Server serviert nur den
Werkzeugordner an der bereits eingerichteten USB-Adresse.

## 2. Telefon: Helfer laden und ausführen

In der vorhandenen Telnet-Shell einmal Enter drücken und `set +e` einzeln
eingeben. Dann:

```sh
/bin/busybox wget -O /tmp/n100-android-probe.sh http://192.168.2.14:8081/probe-android-mounts.sh &&
/bin/sh /tmp/n100-android-probe.sh run 2>&1 | tee /tmp/n100-super/android-mounts-probe.txt
```

Nur bei erfolgreichem Download ausführen. Der Helfer liegt als Datei vor;
die größere Befehlsfolge muss damit nicht in den Telnet-Prompt eingefügt werden.
Der bereits laufende Telefon-HTTP-Server auf `192.168.2.15:8080` stellt das
Protokoll weiterhin aus `/tmp/n100-super` bereit.

## 3. Separates CachyOS-Terminal: Protokoll und Android-APEX-Ausgabe erfassen

Nachdem der Helfer beendet ist, in einem zweiten Host-Terminal:

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_capture="$HOME/n100-build/android-bringup-$(date -u +%Y%m%dT%H%M%SZ)"
curl --fail --max-time 30 \
  --output "$n100_capture.txt" \
  http://192.168.2.15:8080/android-mounts-probe.txt

python3 "$N100_PORT/tools/inspect-android-output.py" \
  --android-root "$ANDROID_ROOT" \
  --probe-log "$n100_capture.txt" \
  --output "$n100_capture.json"
)
```

Die ausgegebene **`.json`-Datei** bereitstellen. Das lokale Werkzeug erfasst
Runtime-APEX-Kandidaten und Linker aus dem vorhandenen Android-Build. Es listet
ZIP-Inhalte und passende ELF-Header/Hashes, extrahiert oder installiert aber
keine APEX-Dateien. Symlinks außerhalb des Produktbaums werden nicht verfolgt.
Damit lässt sich bei Bedarf die Runtime aus der passenden Android-13-Buildbasis
vorbereiten und die tatsächliche Pfadlücke gezielt beheben.

Danach kann der Host-HTTP-Server aus Schritt 1 mit Strg+C beendet werden.
Das N100 bleibt in der bestehenden Initramfs-/Telnet-Sitzung. Kein erneuter
Flash und kein normaler Android-Neustart sind für diesen Test erforderlich.
