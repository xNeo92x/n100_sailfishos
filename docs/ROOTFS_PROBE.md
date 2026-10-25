# N100: Sailfish-Rootfs nach userdata schreiben und schreibgeschützt prüfen

Der Kernel 4.19.325-perf+ läuft auf AArch64, die frühe USB-Telnet-Shell funktioniert,
und der neue Kernel erkennt sda10 = super, sda15 = metadata und sda16 = userdata.
ext4 und F2FS sind registriert; Device Mapper ist initialisiert.
[Gerätenachweis](../validation/kernel-storage-user-20261025.json).

Der nächste Versuch schreibt das vorhandene 8-GiB-Sailfish-Image auf die
physische Userdata-Partition. Danach wird derselbe bewährte Debug-Kernel
temporär gestartet. Das Rootfs wird ausdrücklich schreibgeschützt eingehängt
und seine Shell in einem Chroot getestet. Das startet weder systemd noch die
grafische Oberfläche oder Android-HAL-Dienste.

**Dieser Schritt ersetzt die Android-Datenpartition durch das Sailfish-ext4-Image.**
Datensicherung und Datenverlust wurden bereits bestätigt. Nach diesem Schritt
stellt das Rückspielen des Stock-DTBO allein das Android-Datenlayout nicht
wieder her. Eine vollständige Rückkehr zu Android benötigt dann auch einen
passenden Userdata-/Recovery-Restore, der auf diesem Gerät noch nicht erprobt ist.

## 1. Aus der Debug-Sitzung in Bootloader-Fastboot wechseln

Telnet mit `exit` verlassen. Das Telefon mit Ein/Aus + Lauter etwa zehn Sekunden
ausschalten. Anschließend aus dem ausgeschalteten Zustand mit
Lauter + Leiser + Ein/Aus in den zuvor verwendeten Bootloader-Fastboot wechseln.
Beim Anstecken nicht nur beide Lautstärketasten halten.

Es darf nur dieses N100 angeschlossen sein. Slot B bleibt aktiv. Der aktuelle
Testzustand hat weiterhin das passende gebaute DTBO in `dtbo_b`; es wurde noch
keine Stock-DTBO-Wiederherstellung gemeldet. Wenn zwischenzeitlich das
Stock-DTBO wiederhergestellt wurde, muss vor diesem Debug-Kernel erneut das
geprüfte `first-device-test/dtbo-test.img` nach `dtbo_b` geschrieben werden.

## 2. Vorprüfung, Userdata-Flash und temporärer Debugstart

Ein neues Terminal auf **CachyOS** öffnen und nötigenfalls mit `bash` eine Bash
starten. Keine SDK-Chroots verwenden. Dann:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
n100_bundle="$HOME/n100-build/first-device-test"
n100_userdata="$HOME/n100-build/images/billie2-20261025T160318Z/userdata.img"

python3 "$N100_PORT/tools/prepare-first-boot.py" check \
  --bundle "$n100_bundle" \
  --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z" \
  --userdata "$n100_userdata"

fastboot -S 512M flash userdata "$n100_userdata"
fastboot boot "$n100_bundle/debug-boot.img"
)
```

Die Vorprüfung liest das 8-GiB-Image in 1-MiB-Blöcken und verlangt den zuvor
gemeldeten SHA-256-Hash
`3c7bc615c566ccd3634dd829bdea41a2df32aa9419f360f7969a189aff6e3e3d`.
Zusätzlich prüft sie die drei Testdateien und das Stock-DTBO-Original,
genau ein Gerät, `bengal`, entsperrten Bootloader, Slot B,
Bootloader-Fastboot, die Boot-/DTBO-/Userdata-Kapazitäten und mindestens
512 MiB maximale Downloadgröße. Die Userdata-Partition hat 48.062.869.504 Bytes;
das Image hat 8.589.934.592 Bytes. Der übrige Partitionsraum wird dabei nicht
automatisch in das ext4-Dateisystem aufgenommen.

Die Option `-S 512M` lässt Fastboot das Image in Sparse-Übertragungen von
höchstens 512 MiB aufteilen. Der AOSP-Client unterstützt dabei auch den Import
eines rohen Images; die gemessene maximale Downloadgröße von 804.280.320 Bytes
liegt darüber. [AOSP-Implementierung](https://android.googlesource.com/platform/system/core/+/d441d52540a4e8edd0fa33d8c6a7c43ce609bd8f/fastboot/fastboot.cpp).
Die tatsächliche Annahme aller Teile durch das N100 ist noch nicht getestet.
Anzahl und Gesamtdauer der Teile werden nicht vorab versprochen.

Den Flash vollständig abwarten und die Ausgabe aufbewahren. Bei einem Fehler
stoppt der Block vor dem nächsten Befehl. In Fastboot bleiben und die genaue
Meldung bereitstellen; keine zusätzlichen Format-/Erase-Befehle ausführen.
Wenn nur der anschließende temporäre Bootauftrag scheitert, ist Userdata
möglicherweise bereits vollständig beschrieben. Ein normaler Android-Neustart
ist kein Ersatz für den geplanten Debugstart.

## Wenn „Sending boot.img“ nach dem Userdata-Flash stehen bleibt

Der erste Userdata-Flash wurde inzwischen mit drei bestätigten Sparse-Teilen
und `Finished. Total time: 44.923s` gemeldet.
[Geräteprotokoll](../validation/userdata-flash-user-20261025.json).
Die vorangehende Meldung „Invalid sparse file format at header magic“ hat diesen
Ablauf nicht abgebrochen. Ob das Dateisystem auf dem Gerät lesbar ist, muss
weiterhin durch den Mount geprüft werden.

Der anschließende RAM-Boot blieb laut letzter Ausgabe bei
`Sending 'boot.img' (44008 KB)` stehen. Für diesen Versuch fehlen bisher die
Download- und Boot-Bestätigung. Die Ursache und die genaue Wartezeit sind
noch nicht bekannt; das ist kein Beleg für ein beschädigtes Bootimage.

Der anschließend gemeldete Wiederholungsversuch hat Download und Bootauftrag
in 1,225 Sekunden erfolgreich bestätigt, ohne Userdata erneut zu flashen.
[Neuer Gerätebefund](../validation/post-userdata-debug-boot-retry-user-20261025.json).
Die verwendete Neustartmethode wurde nicht mitgeliefert; eine bestimmte
Ursache des ersten Hängers wird deshalb nicht behauptet. Aktuell mit
Abschnitt 3 und 4 fortfahren. Die Wiederanlaufschritte unten sind Referenz.

Wenn diese Zeile länger als zwei Minuten unverändert bleibt:

1. Den laufenden `fastboot boot` mit **Strg+C** abbrechen und auf die Rückkehr
   zum Host-Prompt warten. Der zuvor abgeschlossene Userdata-Flash wird nicht
   wiederholt. Der aktuell hängende Befehl lädt ein Bootimage in den RAM und
   beschreibt keine Boot-Partition.
2. Den Bootloader gezielt neu starten:

```bash
timeout 15s fastboot reboot-bootloader
```

Auf die erneute Bootloader-Anzeige warten. Falls der Befehl nicht reagiert
oder nach dem Timeout kein Fastboot-Gerät erscheint, USB abziehen, mit
Ein/Aus + Lauter etwa zehn Sekunden ausschalten, dann aus ausgeschaltetem
Zustand mit Lauter + Leiser + Ein/Aus in Bootloader-Fastboot starten und
wieder verbinden. Keinen normalen Android-Neustart anfordern.

3. Ausschließlich den geprüften temporären Bootversuch wiederholen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_bundle="$HOME/n100-build/first-device-test"
python3 "$N100_PORT/tools/prepare-first-boot.py" check \
  --bundle "$n100_bundle" \
  --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z"
fastboot --verbose boot "$n100_bundle/debug-boot.img"
)
```

Die Vorprüfung hält bei geändertem Slot oder Modus an. Bei erneutem Hänger
diese Ausgabe und `lsusb` bereitstellen; keinen erneuten Userdata-Flash als
Diagnose durchführen. Nach erfolgreichem Boot mit Abschnitt 3 fortfahren.

## 3. USB-Verbindung erneut herstellen

Nach erfolgreichem Bootauftrag etwa 30–60 Sekunden warten. Auf dem CachyOS-PC:

```bash
sudo ip link set dev enp16s0u10 up
sudo ip addr replace 192.168.2.14/24 dev enp16s0u10
telnet 192.168.2.15 23
```

Das Interface gilt für denselben USB-Anschluss wie im erfolgreichen ersten
Test. Bei anderem Namen zuerst `ip -br addr` prüfen. Die Debug-Sperre hält
weiterhin vor jedem automatischen Userdata-Mount an.

## 4. Rootfs auf dem Telefon schreibgeschützt prüfen

Erst in der **Telnet-Shell auf dem N100** den Block ausführen:

```sh
(
set -e
grep -qx 'PARTNAME=userdata' /sys/class/block/sda16/uevent
if grep -q ' /data ' /proc/mounts; then
    echo "STOP: /data ist bereits gemountet; zuerst den Zustand auswerten."
    exit 1
fi
mkdir -p /data
mount -t ext4 -o ro,noload /dev/sda16 /data
ls -l /data/.stowaways/sailfishos/bin/sh
ls -l /data/.stowaways/sailfishos/usr/lib/systemd/systemd
chroot /data/.stowaways/sailfishos /bin/sh -c 'cat /etc/os-release && uname -r'
grep ' /data ' /proc/mounts
)
```

Der Block bricht bei einer fehlgeschlagenen Labelprüfung oder einem Mountfehler
ab. `ro,noload` verhindert auch den schreibenden Journal-Replay, den ein
gewöhnlicher ext4-Read-only-Mount sonst ausführen kann
([Kernel-Dokumentation](https://github.com/torvalds/linux/blob/master/Documentation/admin-guide/ext4.rst)).
Das Image wurde mit expliziten ext4-Features gebaut, ohne unkontrollierte neue
Host-Defaults. Die Mountbarkeit durch diesen Kernel muss trotzdem erst am
Gerät nachgewiesen werden.

Die Chroot-Ausgabe soll Sailfish OS 5.1.0.11 aus dem aufgespielten Rootfs und
den bereits laufenden Kernel zeigen. Dies ist ein neuer Prozess innerhalb
des vorhandenen Debug-Kernels; es findet kein `switch_root` statt. Es werden
auch keine proc/sys/dev-Bindmounts oder Android-Partitionen dafür angelegt.

Die vollständige Flash-Ausgabe und diesen Rootfs-Prüfblock bereitstellen.
Bei Fehlern zusätzlich `dmesg | tail -n 60` aus der Telnet-Shell auslesen.

## Noch offen

Ein erfolgreicher Chroot-Test validiert noch keinen vollständigen Sailfish-Boot.
Dynamische System-/Vendor-Mappings, APEX, Firmware-Mounts, systemd-Start,
Display und weitere Hardware sind anschließende Bring-up-Arbeit.
Für diese Phase werden weder Super noch VBMeta oder Boot beschrieben und
keine Slots umgeschaltet. `flashable_release` bleibt `false`.
