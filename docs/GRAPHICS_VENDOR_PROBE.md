# N100: Vendor-Probe V3 mit gebautem libGLESv3

## Ausgangslage

Der [gezielte Build](../validation/graphics-runtime-build-user-20261111.json) hat die
echte Android-13-Bibliothek `libGLESv3.so` erzeugt (AArch64, SHA-256
`e1efbb5b…f958`). Alle Abhängigkeiten sind identisch mit dem bereits am Gerät getesteten
HAL-Stand; Runtime-APEX und Bionic sind unverändert. Neu ist ausschließlich diese
eine Datei. Ein Gerätetest steht noch aus.

## Was V3 macht

V3 ist das [am Gerät bestandene V2-Skript](VENDOR_DEPENDENCY_PROBE.md) mit genau
diesen Ergänzungen (vom Test `tests/test_graphics_vendor_probe.py` erzwungen):

- Das Delta-Archiv `graphics-runtime.tar.gz` wird geladen und per Größe und SHA-256
  geprüft. Es darf nur `lib64/libGLESv3.so` und die Prüfsummenliste enthalten.
- Die Bibliothek wird nur in die **RAM-Kopie** des HAL-Baums kopiert, bevor dieser als
  `/system` eingebunden wird. Existiert dort bereits eine `libGLESv3.so`, bricht V3 ab.
  Auf `userdata` und den Stock-Partitionen wird nichts geschrieben.
- Für übersprungene Programme (z. B. Audio) wird zusätzlich der gelesene ELF-Kopf
  protokolliert, um die offene ABI-Frage zu klären.

Bestanden ist V3 nur, wenn alle V2-Bedingungen erfüllt sind, der Composer `libGLESv3.so`
tatsächlich aus `/system/lib64/libGLESv3.so` lädt und keine Bibliothek fehlt. Dienste
werden weiterhin **nicht** gestartet.

Bundle: `~/n100-build/vendor-probe-v3-20261111`, Manifest im Repository:
[vendor-probe-v3-manifest-user-20261111.json](../validation/vendor-probe-v3-manifest-user-20261111.json).
SHA-256 von `vendor-probe-v3.sh`:
`668d4b27b3702e2ede6ccc22cb452a28199ca3a12838b7680213606d69277d59`.

## Ablauf

### 1. CachyOS: Telefon temporär starten

Telefon im Bootloader-Fastboot (ausgeschaltet: Lauter + Leiser + Ein/Aus) per USB
verbinden. Dann in einem Terminal:

```bash
bash ~/n100_sailfishos/tools/run-vendor-v3-probe.sh boot
```

Die Vorprüfung hält bei falschem Slot oder Modus an. Es wird nichts geflasht.
Hängt die Übertragung länger als zwei Minuten: Strg+C und
[Wiederanlauf](ROOTFS_PROBE.md) beachten.

### 2. CachyOS: Dateien bereitstellen

Nach 30–60 Sekunden im selben Terminal:

```bash
bash ~/n100_sailfishos/tools/run-vendor-v3-probe.sh serve
```

Das Skript prüft das Bundle, richtet das USB-Netz ein (sudo-Passwort), pingt das
Telefon und startet den Dateiserver. **Fenster offen lassen.**

### 3. Telefon: Probe ausführen

In einem **zweiten** CachyOS-Terminal `telnet 192.168.2.15 23` aufrufen. Am Prompt
`~ #` (das ist die Shell **auf dem Telefon**) diesen Block einfügen:

```sh
mkdir -p /tmp/n100-super
grep -q ' /data ' /proc/mounts || { grep -qx 'PARTNAME=userdata' /sys/class/block/sda16/uevent && mkdir -p /data && mount -t ext4 -o ro,noload /dev/sda16 /data; }
/bin/busybox wget -O /tmp/n100-vendor-probe-v3.sh http://192.168.2.14:8081/vendor-probe-v3.sh &&
echo '668d4b27b3702e2ede6ccc22cb452a28199ca3a12838b7680213606d69277d59  /tmp/n100-vendor-probe-v3.sh' | sha256sum -c - &&
/bin/sh /tmp/n100-vendor-probe-v3.sh run 2>&1 | tee /tmp/n100-super/vendor-probe-v3.txt
/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super 2>/dev/null; echo N100_READY_FOR_COLLECT
```

Warten, bis `N100_READY_FOR_COLLECT` erscheint. Fehlermeldungen sind in Ordnung –
nicht wiederholen, sondern zuerst einsammeln.

### 4. CachyOS: Ergebnis einsammeln

Im zweiten Terminal (Telnet mit `exit` verlassen oder ein drittes öffnen):

```bash
bash ~/n100_sailfishos/tools/run-vendor-v3-probe.sh collect
```

Danach im ersten Terminal den Dateiserver mit Strg+C beenden. Das Telefon kann im
Debug-Start bleiben.
