# N100: früher USB-Debug-Boot

Der nächste lokale Build erzeugt ein separates Image für eine USB-Shell vor
jedem automatischen Mount von `/data`. Der neue Kernel und das Debug-Image
müssen noch auf dem Buildhost erstellt werden. Ein Geräteboot ist ungetestet.

## Ergebnisse der echten Boot-Prüfung

Das hochgeladene Paket `boot-audit-20261025T181511Z.zip` wurde entpackt und
gegen sein SHA-256-Inventar geprüft. [Originalbericht](../validation/boot-audit-report-user-20261025.json)
und [Analyse](../validation/boot-audit-user-20261025.json) sind gespeichert.

- Die tatsächliche Start-Ramdisk ist Legacy-LZ4 und enthält 13 CPIO-Einträge.
  `/init` ist ausführbar; BusyBox ist AArch64 ELF64 ohne ELF-Interpreter.
- Die mitgelieferte `.config` hat `CONFIG_RD_LZ4=n`. Sie stammt aus dem aktuellen
  Android-Ausgabeverzeichnis; sie wurde nicht aus dem eingebetteten Kernel
  extrahiert. Der Build muss den Decoder explizit aktivieren und neu prüfen.
- Das ursprüngliche Configfs-Skript versucht zusätzlich `rndis_bam.rndis`,
  obwohl `CONFIG_USB_CONFIGFS_QCRNDIS=n` ist. Außerdem wählte der Debugpfad
  Massenspeicher und wurde erst nach den Userdata-Mountversuchen erreicht.

Der gepinnte `fdtoverlay`-Test ergibt:

| Basis-DTB | Overlay | Ergebnis auf dem Host |
| --- | --- | --- |
| Stock | Stock | erfolgreich |
| gebaut | gebaut | erfolgreich; Ergebnis-SHA-256 `4446e0b22c0e71c6b5386618df573b765379b0d43590d51f801608c32814c005` |
| gebaut | Stock | `FDT_ERR_NOTFOUND`; 17 Ziel-Symbole fehlen |

Die fehlenden Stock-Symbole betreffen unter anderem PMI632-Regler, Ladegerät,
Fuel Gauge und thermische Knoten. Eine passende Board-ID allein behebt diese
DTB/DTBO-Schnittstelle nicht. Für diesen Kernel ist das **passende gebaute DTBO**
erforderlich. Ein späterer Startversuch benötigt deshalb zusätzlich eine
geprüfte Vorgehensweise für DTBO und dessen Wiederherstellung. Das bloße
Booten eines anderen Boot-Images validiert diesen Teil nicht.

Erfolgreiches Anwenden eines Overlays auf dem Host ist kein Hardwaretest.
Die Originaldateien passen zur ausgelesenen Stock-Firmware; sie sind keine
separaten Rohsicherungen beider A/B-Slots.

## Umgesetzte Änderungen

`kernel/sailfish.config` fordert jetzt `CONFIG_BLK_DEV_INITRD=y`,
`CONFIG_RD_GZIP=y` und `CONFIG_RD_LZ4=y`. Frühere Kernel-Prüfungen beziehen
sich auf den archivierten [alten Fragmentstand](../validation/kernel-fragment-before-lz4.config);
ihre damaligen Prüfsummen werden nicht auf den neuen Stand umgeschrieben.

`tools/boot-debug.py prepare` akzeptiert nur den bekannten alten oder den
aktuellen generierten Defconfig-Stand sowie die gepinnte oder bereits
angepasste `init-script`-Datei. Es prüft alle Ziele vor Änderungen und erhält
unbekannte lokale Anpassungen. Die Quellen-Lockdatei bleibt gleich.

Das angepasste Init verwendet Configfs mit genau `rndis.usb0`. Ein separates
Debug-Image ergänzt `n100_debug=1` an der Kernel-Kommandozeile. Dieser Zweig
startet die Debug-Sitzung vor `mount_stowaways` und kehrt auch nach `continue`
nicht zum Mounten oder zu `switch_root` zurück. Der normale Bootpfad ohne
diesen Parameter bleibt für die spätere Rootfs-Arbeit vorgesehen.

Der neue Modus `build-hal.sh boot` baut `hybris-boot` samt Kernel und erzeugt
danach ein neues `n100-debug-boot-<UTC-Zeit>.img`. Er prüft die finale `.config`,
die tatsächlich eingepackte Ramdisk, die ausführbare Init-Sperre, BusyBox und
die zuvor geprüften DTB/DTBO-Hashes. Bei Abweichungen entsteht kein freigegebener
Debug-Bericht. Für die LZ4-Prüfung wird die Systembibliothek `liblz4.so.1`
verwendet; fehlt sie in Ubuntu, nennt der Helfer das Paket `liblz4-1`.

## Auf dem vorhandenen CachyOS-Buildhost bauen

Zuerst im **CachyOS-Terminal** aktualisieren und das Platform SDK öffnen:

```bash
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
"$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot"
```

Danach **im Platform SDK** den Ubuntu-Build-Chroot öffnen:

```bash
source "$HOME/n100-build/build.env"
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

Die bekannten Mount-/D-Bus-Meldungen sind für sich kein Nachweis, dass der
Eintritt fehlgeschlagen ist. `cat /etc/os-release` muss nun Ubuntu anzeigen;
der Buildhelfer verlangt zusätzlich `MERSDKUBU=1`.

Jetzt **in Ubuntu**:

```bash
source "$HOME/n100-build/build.env"
bash "$N100_PORT/tools/build-hal.sh" boot
```

Der Modus verwendet vorhandene Quellen und baut weder RPMs noch das Rootfs
neu. Die reguläre Ausgabe unter `out/target/product/billie2/hybris-boot.img`
wird durch den Android-Build aktualisiert. Das separate Debug-Image hat einen
neuen Zeitstempel und überschreibt weder frühere Debug-Dateien noch die
Image-Kopie unter `~/n100-build/images/billie2-20261025T160318Z`.

Am Ende erscheinen:

- `Debug boot image: .../n100-debug-boot-....img`
- `Build report: .../n100-debug-boot-....img.json`

Bitte nur die kleine **`.img.json`-Datei** bereitstellen. Das Buildlog liegt
bei Bedarf unter `$ANDROID_ROOT/n100-build-logs/boot-debug.log`.

## Vor dem Geräteversuch

Der Bootloader wurde zuletzt als gesperrt erfasst. Der neue Build führt keine
Telefonbefehle aus. Entsperren, das passende DTBO, AVB und ein konkreter
Wiederherstellungsablauf bleiben vor einem Geräteversuch zu klären.
Die Standardadresse der frühen USB-Shell ist `192.168.2.15`, Telnet-Port 23;
die tatsächliche USB-Enumeration muss erst auf dem Handy geprüft werden.

Das Debug-Image dient dem Kernel-/USB-Test. Es löst weder die dynamischen
Android-HAL-Mounts noch den normalen Sailfish-Start. Deshalb bleiben
`boot_tested` und `flashable_release` weiterhin `false`.
