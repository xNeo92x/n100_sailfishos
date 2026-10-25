# N100: früher USB-Debug-Boot

Das separate USB-Debug-Image ist auf dem Buildhost erfolgreich erstellt.
Der [Packerbericht](../validation/debug-image-report-user-20261025.json) bestätigt
die Debug-Sperre vor jedem automatischen Mount von `/data` und die geprüften
DTB/DTBO-Inhalte. Ein Geräteboot ist ungetestet. Weiter geht es mit dem
[ersten Geräteversuch](FIRST_DEVICE_TEST.md).

## Ergebnisse der echten Boot-Prüfung

Das hochgeladene Paket `boot-audit-20261025T181511Z.zip` wurde entpackt und
gegen sein SHA-256-Inventar geprüft. [Originalbericht](../validation/boot-audit-report-user-20261025.json)
und [Analyse](../validation/boot-audit-user-20261025.json) sind gespeichert.

- Die tatsächliche Start-Ramdisk ist Legacy-LZ4 und enthält 13 CPIO-Einträge.
  `/init` ist ausführbar; BusyBox ist AArch64 ELF64 ohne ELF-Interpreter.
- Präzisierung zur alten `.config`: `CONFIG_RD_LZ4=n`, aber bereits
  `CONFIG_DECOMPRESS_LZ4=y` und `CONFIG_LZ4_DECOMPRESS=y`. Die frühere
  Einordnung als sicher fehlender Decoder war zu weitgehend. Die explizite
  `RD_LZ4=y`-Anforderung bleibt als Buildvertrag erhalten. Der neue Build hat
  die Fragmentprüfung bestanden. Die alte Konfiguration war ein Snapshot aus
  dem Ausgabeverzeichnis, kein unabhängig aus dem Kernel extrahiertes IKCONFIG.
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
konkrete Vorgehensweise für DTBO und dessen Wiederherstellung. Das bloße
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
die zuvor geprüften DTB- und DTBO-Inhalts-Hashes. Bei Abweichungen entsteht kein freigegebener
Debug-Bericht. Für die LZ4-Prüfung wird die Systembibliothek `liblz4.so.1`
verwendet; fehlt sie in Ubuntu, nennt der Helfer das Paket `liblz4-1`.

## Debug-Image erfolgreich verpackt

Am 25. Oktober um 19:17 UTC entstand:
`out/target/product/billie2/n100-debug-boot-20261025T191724Z.img`

- Größe: 45.064.192 Bytes; SHA-256
  `6dccc17b73f572a42400274ca8ce50595d9e4a8127732e4e9d53d29d1727a6ac`.
- Debug-Flag `n100_debug=1`, ausführbare Init-Sperre und statische
  AArch64-BusyBox laut Packerprüfung vorhanden.
- Basis-DTB und die komplette 186.363-Byte-DTBO-Tabelle einschließlich Overlay
  stimmen mit dem zuvor am Host angewendeten Stand überein.
- Das passende `out/target/product/billie2/dtbo.img` ist 24 MiB groß,
  SHA-256 `c14f4bce02f0254a5eb381940767cb00bda9c740d5ad2cefa271f69bd859d46e`.
  Sein kompletter Dateihash weicht vom früheren Build ab; die geprüften
  Gerätebaum-Inhalte sind identisch. Die genaue Änderung außerhalb der Tabelle
  wurde mangels hochgeladener neuer Binärdatei nicht unabhängig untersucht.

Der korrigierte Packer vergleicht DTB, Tabelle und Overlays getrennt von den
AVB-Metadaten. Er erhält die vollständigen Image-Bytes und protokolliert deren
tatsächliche Hashes. OEM-Signaturen werden dadurch nicht geprüft.
[Prüfumfang](../validation/debug-image-build-user-20261025.json) und
[Guard-Nachweis](../validation/dtbo-content-guard.json).

Der Packschritt muss für diesen Stand nicht wiederholt werden. Die alte
`hybris-boot.img`-Kopie unter `~/n100-build/images/billie2-20261025T160318Z`
ist ein früheres Artefakt und wird nicht für den Debugversuch verwendet.

## Vollständiger Debug-Neubau bei Bedarf

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
