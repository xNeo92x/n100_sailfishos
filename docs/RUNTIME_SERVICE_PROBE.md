# N100: Android-Grunddienste kurz im RAM starten (Probe V4)

## Ziel

Erstmals werden Android-Dienste nicht nur auf Bibliotheken geprüft, sondern gestartet:
`servicemanager` (Binder-Kontextmanager) und `hwservicemanager` (HIDL). Grundlage ist
das [HAL-RPM mit SELinux-Kontexten](SELINUX_CONTEXTS.md)
(`droid-hal-billie2-0.0.6-202611111828`, Vergleich:
[Bericht](../validation/selinux-hal-rpm-user-20261111.json)).

## Was V4 macht

V4 ist das [am Gerät bestandene V2-Skript](VENDOR_DEPENDENCY_PROBE.md) mit folgenden
Änderungen (vom Test `tests/test_runtime_service_probe.py` erzwungen):

- Der HAL-Baum stammt aus dem neuen RPM. Der Vorbereiter verlangt, dass er exakt dem am
  Gerät getesteten V2-Baum plus vier Dateien entspricht: `lib64/libGLESv3.so` und die drei
  `etc/selinux/plat_*_contexts`. Symlinks sind unverändert.
- Nach den unveränderten System- und Vendor-Abhängigkeitslisten wird `binderfs` nur in der
  temporären Ansicht eingehängt (zuerst mit `stats=global`). `/dev/binder`, `/dev/hwbinder`
  und `/dev/vndbinder` werden als Symlinks wie in Androids `init.rc` angelegt. Existieren
  diese Pfade schon, bricht V4 ab.
- `servicemanager` und `hwservicemanager` laufen als root ohne `LD_LIBRARY_PATH` etwa zehn
  Sekunden. Protokolliert werden: Prozess nach 3 s und 6 s noch aktiv, offene Binder-
  Dateideskriptoren, `binder_logs/state`, Ausgabe und Exitcode.
- Danach werden beide beendet (TERM, notfalls KILL). Der Cleanup-Trap stoppt eigene Prozesse
  vor dem Aushängen und entfernt am Ende Symlinks und `/dev/binderfs`.

Nicht gestartet werden logd, der Android-Property-Dienst, droid-hal-init, Vendor-HALs und
systemd. „Bleibt aktiv“ belegt daher nur, dass beide Manager starten und sich nicht sofort
beenden – nicht ihre vollständige Funktion. Auf `userdata` und Stock-Partitionen wird nichts
geschrieben; nach einem Neustart ist alles weg.

Bundle: `~/n100-build/runtime-probe-v4-20261111`, Manifest:
[runtime-probe-v4-manifest-user-20261111.json](../validation/runtime-probe-v4-manifest-user-20261111.json).
SHA-256 von `runtime-probe-v4.sh`:
`3e60503fef57664e865860c49018b6c2625cc871c5eaa6e5083ad3d9eba812e7`.

## Ablauf

### 1. CachyOS: Telefon temporär starten

Telefon im Bootloader-Fastboot (ausgeschaltet: Lauter + Leiser + Ein/Aus) per USB verbinden:

```bash
bash ~/n100_sailfishos/tools/run-runtime-v4-probe.sh boot
```

Läuft das Telefon noch im Debug-Start vom letzten Test, diesen Schritt überspringen.

### 2. CachyOS: Dateien bereitstellen

Nach 30–60 Sekunden:

```bash
bash ~/n100_sailfishos/tools/run-runtime-v4-probe.sh serve
```

**Fenster offen lassen.**

### 3. Telefon: Probe ausführen

Zweites CachyOS-Terminal: `telnet 192.168.2.15 23`. Am Prompt `~ #` (Shell **auf dem
Telefon**) diesen Block einfügen:

```sh
mkdir -p /tmp/n100-super
grep -q ' /data ' /proc/mounts || { grep -qx 'PARTNAME=userdata' /sys/class/block/sda16/uevent && mkdir -p /data && mount -t ext4 -o ro,noload /dev/sda16 /data; }
/bin/busybox wget -O /tmp/n100-runtime-probe-v4.sh http://192.168.2.14:8081/runtime-probe-v4.sh &&
echo '3e60503fef57664e865860c49018b6c2625cc871c5eaa6e5083ad3d9eba812e7  /tmp/n100-runtime-probe-v4.sh' | sha256sum -c - &&
/bin/sh /tmp/n100-runtime-probe-v4.sh run 2>&1 | tee /tmp/n100-super/runtime-probe-v4.txt
/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super 2>/dev/null; echo N100_READY_FOR_COLLECT
```

Warten bis `N100_READY_FOR_COLLECT` erscheint (etwa eine Minute länger als V3).

### 4. CachyOS: Ergebnis einsammeln

```bash
bash ~/n100_sailfishos/tools/run-runtime-v4-probe.sh collect
```

Danach im ersten Terminal den Dateiserver mit Strg+C beenden.
