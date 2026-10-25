# N100: frühe USB-Debug-Shell verbinden

Der erste physische Test hat `dtbo_b` erfolgreich beschrieben und der
Bootloader hat den temporären Bootauftrag angenommen. Danach zeigte der Host
das USB-Netzinterface `enp16s0u10` ohne IP-Adresse sowie USB-ID `18d1:d001`.
[Gerätebefund](../validation/first-debug-boot-user-20261025.json).

Das ist mit dem frühen Debug-Gadget vereinbar. Die Zuordnung des Interfaces
zum N100 und die Ausführung des Kernels sind noch nicht durch Shell-/Kernel-Logs
bestätigt. Der Text „Nexus 4 (fastboot)“ aus der lokalen USB-ID-Datenbank ist
allein kein Nachweis für Bootloader-Fastboot: Unser
[Configfs-Skript](../adaptation/initramfs/usb-configfs.sh) verwendet dieselbe
VID/PID-Kombination ausdrücklich für RNDIS.

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

`dtbo_b` enthält weiterhin das gebaute Test-DTBO. Vor dem normalen Android-Start
die [Stock-DTBO-Wiederherstellung](FIRST_DEVICE_TEST.md#4-stock-dtbo-wiederherstellen-und-android-starten)
ausführen. Ein bloßer Neustart ist noch keine Wiederherstellung.
Der Restore ist auf Dateiebene geprüft; ein physischer erfolgreicher Restore
wurde bislang nicht gemeldet.

Die temporäre Hostadresse kann nach Ende des Tests entfernt werden:

```bash
sudo ip addr del 192.168.2.14/24 dev enp16s0u10
```
