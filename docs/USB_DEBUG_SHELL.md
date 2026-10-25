# N100: frühe USB-Debug-Shell

Der erste physische Debugstart ist bestätigt: USB-Ping hat 3/3 Antworten geliefert,
Telnet auf `192.168.2.15:23` öffnet die Hybris-Initramfs-Shell, und
`/proc/cmdline` enthält `n100_debug=1`. Die sechs Einträge aus
`/proc/mounts` zeigen nur Rootfs, devtmpfs, devpts, sysfs, proc und configfs;
Userdata und Sailfish-Rootfs sind nicht gemountet.
[Gerätenachweis](../validation/early-debug-shell-user-20261025.json).

Die genaue Kernelversion fehlt noch, weil der erste Befehl als `Quname -a`
ankam. Der Fehler von `ps -ef` betrifft eine nicht unterstützte BusyBox-Option;
anschließend liefen netstat, Mount-Ausgabe und die wartende Init-Schleife weiter.

Die USB-ID `18d1:d001` wird von unserem
[Configfs-Skript](../adaptation/initramfs/usb-configfs.sh) für RNDIS verwendet.
Die lokale lsusb-Bezeichnung „Nexus 4 (fastboot)“ bezeichnet hier nicht den
tatsächlich laufenden Modus; die erreichte Shell belegt die Kernel-/Initramfs-Ausführung.

## Speicherinventur abgeschlossen

Der neue Befund bestätigt Kernel `4.19.325-perf+` auf AArch64, die erwarteten
Geräte/Labels für Super, Metadata und Userdata sowie registriertes ext4/F2FS
und initialisierten Device Mapper. [Gerätenachweis](../validation/kernel-storage-user-20261025.json).
Die folgende Inventur muss nicht wiederholt werden. Weiter mit dem
[Userdata-Flash und schreibgeschützten Rootfs-Test](ROOTFS_PROBE.md).

### Inventurbefehle als Referenz

In der bereits geöffneten Shell **auf dem N100**:

```sh
uname -a
cat /proc/partitions
cat /proc/filesystems
ls -l /dev/sda10 /dev/sda15 /dev/sda16

for n in sda10 sda15 sda16; do
    echo "=== $n ==="
    grep -E '^(DEVNAME|PARTNAME)=' "/sys/class/block/$n/uevent"
done

dmesg | grep -Ei 'ufs|scsi|device-mapper|f2fs|ext4' | tail -n 80
```

Diese Befehle lesen Kernel-/Geräteinformationen und ändern keine Partitionen.
Die zuvor unter Android identifizierten Kandidaten sind sda10 = super,
sda15 = metadata und sda16 = userdata. Die Inventur prüft, ob der neue Kernel
dieselben Geräte und Namen bereitstellt, und welche Dateisystemtreiber
tatsächlich registriert sind. Fehlende Geräte oder andere Namen sind Befunde,
kein Anlass, andere Partitionen auf Verdacht zu verwenden.

Die Ausgabe im Gespräch bereitstellen. Danach folgen der konkrete Rootfs-Startpfad
und die Integration der dynamischen Android-Partitionen. Android speichert
deren Bereichszuordnung in den Super-Metadaten; die Zuordnung muss vor einem
passenden Mount verfügbar sein
([AOSP](https://source.android.com/docs/core/ota/dynamic_partitions/implement)).
Der erfolgreiche USB-Test ersetzt diese noch offene Portierungsarbeit nicht.

Die folgenden Abschnitte dokumentieren den bereits erfolgreich verwendeten
Host-Netzaufbau für spätere Tests. In der bestehenden Verbindung müssen sie
nicht wiederholt werden.

## Hostadresse setzen

Auf dem **CachyOS-PC** das beobachtete USB-Interface temporär konfigurieren:

```bash
sudo ip link set dev enp16s0u10 up
sudo ip addr replace 192.168.2.14/24 dev enp16s0u10
ping -I enp16s0u10 -c 3 -W 2 192.168.2.15
```

Die Adresse gehört zum PC; die Ramdisk verwendet `192.168.2.15`.
Es wird kein Gateway und keine Standardroute gesetzt. Die Konfiguration wird
nicht als dauerhafte NetworkManager-Verbindung gespeichert.
Die Befehle entsprechen der [ip-address-Dokumentation](https://man7.org/linux/man-pages/man8/ip-address.8.html).

Falls das Interface inzwischen anders heißt oder verschwunden ist, nicht ein
anderes vorhandenes Interface auf Verdacht konfigurieren. Den aktuellen Zustand
mit `ip -br addr` prüfen.

## Telnet und erste Nachweise

Bei erreichbarer Gegenseite:

```bash
telnet 192.168.2.15 23
```

Fehlt der Telnet-Client auf CachyOS, ist er im
[Arch-Paket inetutils](https://archlinux.org/packages/core/x86_64/inetutils/files/)
enthalten:

```bash
sudo pacman -S --needed inetutils
```

Die folgenden Befehle erst **in der Shell auf dem Telefon** ausführen:

```sh
uname -a
cat /proc/cmdline
cat /proc/mounts
tail -n 80 /init.log
```

Die Ausgabe im Gespräch bereitstellen. Erwartet werden der gebaute ARM64-Kernel,
`n100_debug=1` in der Kommandozeile sowie keine automatisch gemountete
Userdata-Partition. Erst die tatsächlichen Ausgaben bestätigen diese Punkte.
Noch kein Rootfs starten oder weitere Partitionen mounten, bevor die Logs
ausgewertet sind. Die unauthentifizierte Entwicklungsshell ist für den isolierten
USB-Link vorgesehen.

## Wenn keine Verbindung entsteht

Ping-/Telnet-Fehler zusammen mit diesen Ausgaben vom CachyOS-PC bereitstellen:

```bash
ip -br addr show dev enp16s0u10
readlink -f /sys/class/net/enp16s0u10/device/driver
udevadm info --query=property --path=/sys/class/net/enp16s0u10
ip route get 192.168.2.15
```

Damit lassen sich Treiber/USB-Zuordnung und der tatsächlich verwendete Netzweg
prüfen. Ausbleibende Pingantworten beweisen allein keinen Kernelhänger.
Zusätzliche Neubauten oder Flash-Versuche sind für die Host-IP-Konfiguration
nicht erforderlich.

## Rückweg

Diese DTBO-Rückspielanleitung bezieht sich auf die erste Kernel-/USB-Phase.
Nach dem Userdata-Flash aus [ROOTFS_PROBE.md](ROOTFS_PROBE.md) ist zusätzlich
das Android-Datenlayout ersetzt; Stock-DTBO allein stellt es nicht wieder her.

`dtbo_b` enthält weiterhin das gebaute Test-DTBO. Vor dem normalen Android-Start
die [Stock-DTBO-Wiederherstellung](FIRST_DEVICE_TEST.md#4-stock-dtbo-wiederherstellen-und-android-starten)
ausführen. Ein bloßer Neustart ist noch keine Wiederherstellung.
Der Restore ist auf Dateiebene geprüft; ein physischer erfolgreicher Restore
wurde bislang nicht gemeldet.

Die temporäre Hostadresse kann nach Ende des Tests entfernt werden:

```bash
sudo ip addr del 192.168.2.14/24 dev enp16s0u10
```
