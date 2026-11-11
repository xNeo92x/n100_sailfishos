# N100: erster vollständiger Sailfish-Start (Versuch 1)

## Ausgangslage

Die RAM-Proben V3–V6 haben gezeigt: Vendor-Abhängigkeiten des Composers sind aufgelöst,
binderfs funktioniert, servicemanager und hwservicemanager laufen, und servicemanager
beantwortet Clients mit der Stub-Vorladung. Diese Korrekturen stecken jetzt im neuen
[Userdata-Image](../validation/rootfs-image-build-user-20261111.json)
(`userdata.img` SHA-256 `ba6eecd9…18df`, HAL-RPM `0.0.6-202611111916`).

## Boot-Image

`tools/make-full-debug-boot.py` leitet aus dem am Gerät bewährten `debug-boot.img`
(`6dccc17b…a6ac`) `full-debug-boot.img` (`74d16a51…84fd`) ab. Geändert sind nur 14 Byte der
Kernel-Befehlszeile: `n100_debug=1` → `bootmode=debug`. Kernel, Ramdisk und DTB sind bit-gleich;
das passende Test-DTBO ist weiterhin in `dtbo_b` erforderlich.

Mit `bootmode=debug` folgt das hybris-Init seinem regulären Debug-Pfad:

1. `/dev/sda16` wird eingehängt, `/target` ist das Sailfish-Rootfs; Telnet-Shell auf Port 23.
2. Nach `continue` erfolgt `switch_root` nach `/init-debug`. Existiert
   `/target/init_enter_debug2`, hält es dort erneut an, Telnet auf **Port 2323**.
3. Nach dem zweiten `continue` startet systemd. Die Telnet-Shell auf 2323 bleibt in der
   Regel offen.

## Was geschrieben wird

**Einmalig** wird die Userdata-Partition mit dem neuen Image überschrieben
(`fastboot -S 512M flash userdata`). Dort liegt bisher nur das ältere Sailfish-Testsystem.
Die Boot-Partition bleibt unverändert, das Image wird nur per `fastboot boot` in den RAM
geladen. Ein normaler Neustart ohne Kabel startet daher wieder das, was in `boot_b` liegt.
Das Skript fragt vor dem Flashen ausdrücklich nach `JA`.

## Ablauf

### 1. CachyOS: Telefon in Fastboot, flashen und temporär starten

Telefon ausschalten (Ein/Aus + Lauter etwa zehn Sekunden), dann mit Lauter + Leiser + Ein/Aus
in den Bootloader-Fastboot starten und per USB verbinden.

```bash
bash ~/n100_sailfishos/tools/run-full-boot-test.sh flash
```

### 2. CachyOS: Netz und Dateiserver

Nach 30–60 Sekunden:

```bash
bash ~/n100_sailfishos/tools/run-full-boot-test.sh serve
```

Fenster offen lassen.

### 3. Telefon, Port 23 (Initramfs, Rootfs eingehängt)

Zweites Terminal: `telnet 192.168.2.15 23`, dann am Prompt:

```sh
grep -E ' /data | /target ' /proc/mounts; head -n 2 /target/etc/os-release
touch /target/init_enter_debug2; echo continue > /init-ctl/stdin
```

Die Telnet-Verbindung endet danach. Etwa 20 Sekunden warten.

### 4. Telefon, Port 2323 (echtes Rootfs, vor systemd)

`telnet 192.168.2.15 2323`, dann:

```sh
head -n 2 /etc/os-release; echo continue > /init-ctl/stdin
```

Jetzt startet systemd. **Etwa zwei Minuten warten**, in derselben Telnet-Sitzung bleiben und
dann:

```sh
/bin/busybox-static wget -O /tmp/fullboot-diag.sh http://192.168.2.14:8081/fullboot-diag.sh &&
echo '86dbe6418e39aca59282166b6fb727a8758353fcc71257bbe1b8315b18c2e397  /tmp/fullboot-diag.sh' | sha256sum -c - &&
sh /tmp/fullboot-diag.sh
```

Warten bis `N100_READY_FOR_COLLECT` erscheint.

### 5. CachyOS: Bericht abholen

```bash
bash ~/n100_sailfishos/tools/run-full-boot-test.sh collect
```

Danach den Dateiserver mit Strg+C beenden.

## Wenn die Verbindung abreißt

Sailfish kann beim Start den USB-Modus umschalten. Bricht Telnet ab oder antwortet das
Telefon nicht mehr, nichts erneut flashen. Das Telefon ausschalten, wieder in Fastboot starten
und den bewährten frühen Debug-Start (`run-runtime-v6-probe.sh boot`) verwenden. Von dort lassen
sich die Protokolle des Versuchs schreibgeschützt aus `/data/.stowaways/sailfishos` lesen.
