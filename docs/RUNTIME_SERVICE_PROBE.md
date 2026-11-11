# N100: Android-Grunddienste kurz im RAM starten (Probe V4/V5)

## Ergebnis V4 (11.11.2026)

[Rohbericht](../validation/runtime-v4-device-report-user-20261111.json). System- und
Vendor-Abhängigkeitslisten sowie Cleanup bestanden; binderfs (`stats=global`) mit
`binder`, `hwbinder`, `vndbinder` funktioniert.

- **hwservicemanager läuft** stabil, hält einen Binder-FD und ist laut `binder_logs/state`
  Kontextmanager von `hwbinder` (`node 1: u0000000000000000`). Er lädt
  `plat_hwservice_contexts` und das Stock-`vendor_hwservice_contexts`.
- **servicemanager bricht mit SIGABRT ab** (Exit 134). Ursache im Quelltext:
  `selinux_status_open()` liefert ohne eingehängtes selinuxfs sofort −1
  (`external/selinux/libselinux/src/sestatus.c`), und `Access::Access()` verlangt
  `CHECK(selinux_status_open(true) >= 0)` (`Access.cpp:91`). hwservicemanager ignoriert
  denselben Rückgabewert.
- Beide melden `ioctl 40046210 returned -22`: `BINDER_ENABLE_ONEWAY_SPAM_DETECTION` kennt der
  4.19-Kernel nicht; libbinder behandelt das als Warnung.

## Ergebnis V5 (11.11.2026)

[Bestanden](../validation/runtime-v5-device-report-user-20261111.json): **servicemanager und
hwservicemanager laufen beide** (3 s/6 s aktiv, je ein Binder-FD, sauber per TERM beendet).
`binder_logs/state` zeigt je einen Kontextmanager-Knoten (`u0000000000000000`) für `binder`
und `hwbinder`. Cleanup 0, System- und Vendor-Listen bestanden.

Einschränkung: selinuxfs meldet ohne Richtlinie `deny_unknown=1`. libselinux lehnt dann die
unbekannte Klasse `service_manager` ab (`checkAccess.c`), servicemanager dürfte also
Registrierungen verweigern. Das ist mit einem echten Client noch zu prüfen.
Referenz-Ports (z. B. [droid-config-sony-nile](https://github.com/mer-hybris/droid-config-sony-nile))
laden für hw-/vndservicemanager `libselinux_stubs.so` per `LD_PRELOAD`; die Stub-Bibliothek
enthält aber kein `selinux_android_service_context_handle`.

## Probe V5

V5 ist V4 plus genau einen Schritt vor dem Dienststart: selinuxfs wird nur in der
temporären Ansicht unter `/sys/fs/selinux` eingehängt, **ohne eine Richtlinie zu laden**
(ist schon eine geladen, bricht V5 ab). Ohne geladene Richtlinie bildet der Kernel jeden
Kontext auf `kernel` ab und erlaubt jeden Zugriff; libselinux erlaubt unbekannte Klassen,
solange `deny_unknown` 0 ist. Ausgehängt wird im Cleanup vor `/sys`.

SHA-256 von `runtime-probe-v5.sh`:
`38b9cb0aef522a7c120c2c2559cf2a7fd29bfbb973ffa4f7599572aae330a9b9`
(Bundle `~/n100-build/runtime-probe-v5-20261111`,
[Manifest](../validation/runtime-probe-v5-manifest-user-20261111.json)).
Ablauf wie unten, mit `run-runtime-v5-probe.sh` und diesem Telefon-Block:

```sh
mkdir -p /tmp/n100-super
grep -q ' /data ' /proc/mounts || { grep -qx 'PARTNAME=userdata' /sys/class/block/sda16/uevent && mkdir -p /data && mount -t ext4 -o ro,noload /dev/sda16 /data; }
/bin/busybox wget -O /tmp/n100-runtime-probe-v5.sh http://192.168.2.14:8081/runtime-probe-v5.sh &&
echo '38b9cb0aef522a7c120c2c2559cf2a7fd29bfbb973ffa4f7599572aae330a9b9  /tmp/n100-runtime-probe-v5.sh' | sha256sum -c - &&
/bin/sh /tmp/n100-runtime-probe-v5.sh run 2>&1 | tee /tmp/n100-super/runtime-probe-v5.txt
/bin/busybox httpd -p 192.168.2.15:8080 -h /tmp/n100-super 2>/dev/null; echo N100_READY_FOR_COLLECT
```

## Beschreibung V4

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
