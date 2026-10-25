# N100: erster Geräteversuch mit dem gebauten Debug-Image

Stand: 25. Oktober 2026. Der Build ist abgeschlossen; ein Start auf dem N100
wurde noch nicht versucht. Dieser Plan beschreibt die nächsten Gerätephasen.
Der Benutzer hat die abgeschlossene persönliche Sicherung und den Datenverlust
beim Entsperren am 25. Oktober 2026 bestätigt. Nächster Schritt ist der Unlock
am CachyOS-Host; seine Durchführung ist noch nicht gemeldet.

## Konkrete Testdateien

Alle folgenden Pfade liegen auf Eugens CachyOS-PC.
Produktverzeichnis: `/home/eugen/n100-build/android/out/target/product/billie2`.

| Datei im Produktverzeichnis | Bytes | SHA-256 |
| --- | ---: | --- |
| `n100-debug-boot-20261025T191724Z.img` | 45.064.192 | `6dccc17b73f572a42400274ca8ce50595d9e4a8127732e4e9d53d29d1727a6ac` |
| `dtbo.img` | 25.165.824 | `c14f4bce02f0254a5eb381940767cb00bda9c740d5ad2cefa271f69bd859d46e` |

[Originaler Packerbericht](../validation/debug-image-report-user-20261025.json)
und [Auswertung](../validation/debug-image-build-user-20261025.json).
Der Bericht prüft die frühe Init-Sperre, statische AArch64-BusyBox und die
Gerätebaum-Inhalte. Diese Prüfung belegt keine Ausführung auf der Hardware.

Das Debug-Image wartet vor jedem automatischen Userdata-Mount in der frühen
USB-Debug-Sitzung. Für diesen Versuch wird das 8-GiB-`userdata.img` nicht
benötigt. RPMs und Rootfs müssen dafür nicht neu gebaut werden.

## Phase 1: Datensicherung und Entsperren

Der Bootloader war im letzten echten Preflight gesperrt. Vor einem Unlock müssen
Fotos, Dateien, App-Daten und benötigte Anmeldedaten außerhalb des Telefons
gesichert und die Wiederherstellbarkeit geprüft sein. Das vorhandene Firmware-ZIP
ist keine Sicherung persönlicher Daten.

[AOSP beschreibt](https://source.android.com/docs/core/architecture/bootloader/locking_unlocking)
das Entsperren mit Bestätigung am Telefon und anschließendem Factory Reset.
Der Benutzer hat die Sicherung und den Factory Reset ausdrücklich bestätigt.
Der bisher gemessene Bootloaderzustand bleibt bis zum neuen Gerätebefund
als gesperrt gespeichert.

[OnePlus nennt für das Nord N100](https://service.oneplus.com/in/search/search-detail?id=op588)
`fastboot oem unlock`. Der dort beschriebene Token-Service betrifft
T-Mobile-Versionen; Nicht-T-Mobile-Geräte können laut Hersteller ohne diesen
Service entsperrt werden. Für das ausgelesene EEA-Gerät ist kein Token-Schritt
vorgesehen. Bei einer unerwarteten Token-Anforderung zuerst die Meldung prüfen.

1. Ein neues Terminal direkt unter **CachyOS** öffnen; der Unlock läuft
   außerhalb von Ubuntu-Build-Chroot und Platform SDK.
2. Am N100 unter **Einstellungen → System → Entwickleroptionen** die
   **OEM-Entsperrung** und **USB-Debugging** aktivieren. Falls die
   Entwickleroptionen fehlen, unter **Über das Telefon** siebenmal auf die
   Buildnummer tippen. Nur dieses Telefon verbinden und die ADB-Abfrage zulassen.
3. In den Bootloader wechseln und die Produktkennung prüfen:

```bash
adb get-state
adb reboot bootloader
fastboot getvar product
```

`adb get-state` muss vor dem Neustart `device` melden; die Produktkennung
muss wie im bisherigen Befund `bengal` lauten. Bei Abweichungen oder
Verbindungsfehlern nicht mit dem Unlock fortfahren.

4. Danach den eigentlichen Unlock auslösen:

```bash
fastboot oem unlock
```

Am Telefon mit den Lautstärketasten **UNLOCK THE BOOTLOADER** auswählen und
mit der Ein/Aus-Taste bestätigen. Anschließend folgen Datenlöschung und
Neustart. Falls das Gerät nach Abschluss am Fastboot-Bildschirm stehen bleibt,
`fastboot reboot` ausführen. Bei einem Fehler die genaue Meldung prüfen;
keine Critical-Unlock- oder Erase-Befehle auf Verdacht ergänzen.

5. Stock-Android minimal einrichten und USB-Debugging sowie ADB-Autorisierung
   erneut aktivieren. Dann den neuen Zustand ablesen und Android wieder starten:

```bash
adb get-state
adb reboot bootloader
fastboot getvar unlocked
fastboot getvar current-slot
fastboot reboot
```

Diese Ausgabe im Gespräch bereitstellen. Erwartet wird `unlocked: yes`;
der aktuelle Slot wird aus dem neuen Befund übernommen. Der erfolgreiche
Stock-Start nach dem Reset prüft den Ausgangspunkt vor dem DTBO-Versuch.
Dieser Schritt enthält noch keine Image-Flash-Befehle.

## Phase 2: DTBO und Rückweg festlegen

Das Stock-DTBO lässt sich auf den gebauten Basis-DTB am Host nicht anwenden.
Daher reicht ein temporärer Boot-Download allein für diesen Kandidaten nicht:
Das passende gebaute DTBO muss für den Start verfügbar sein. Ein DTBO-Flash
ändert eine physische Partition dauerhaft, auch wenn das Boot-Image selbst
anschließend nur über `fastboot boot` in den RAM geladen wird.

Unmittelbar vor dem Versuch aktuellen Slot, entsperrten Zustand und
Bootloader-Fastboot erneut ablesen. Früher beobachtet: Slot B, Boot je 96 MiB,
DTBO je 24 MiB, maximaler Download 804.280.320 Bytes. Der Slot wird nicht aus
diesem alten Befund fest vorgegeben. Kein Slotwechsel und keine Änderungen an
beiden Slots für den ersten Versuch.

Die Originaldateien aus der passenden EEA-Voll-OTA liegen auf dem PC unter
`/home/eugen/n100-build/stock-BE2013-20261025T180040Z`:

| Originaldatei | SHA-256 |
| --- | --- |
| `dtbo.img` (8 MiB) | `5425e6e33ef63a06da926df47a42190ff263a3362bb8f1315a68fe92a443938e` |
| `boot.img` (96 MiB) | `28e3497eb8e77042ef983c0654758e54e97cf07d9fae4c9e2798535c58a535f1` |
| `vbmeta.img` (8192 Bytes) | `6ea6f938f7309ff65ab28596b27c6d885ca067165b9f67bf201d3fb7978dc180` |

Diese rekonstruierten OTA-Dateien sind keine Rohsicherungen beider Slots.
Ein noch nicht aktualisierter anderer Slot kann einen anderen Stand enthalten.
Vor dem ersten DTBO-Schreibvorgang müssen der konkrete Zielsuffix und der
Wiederherstellungsablauf einschließlich Rückkehr in Fastboot feststehen.

Die Stock-DTBO-Datei ist kürzer als die gemessene physische DTBO-Partition.
Ein bloßes Anhängen von Nullen verschiebt den AVB-Footer nicht korrekt.
Auch die Annahme, ein kurzer Flash entferne sämtliche alten Bytes am
Partitionsende, ist hier nicht am Gerät geprüft. Die Rückwegplanung muss dies
berücksichtigen; ein getesteter Restore wird nicht behauptet.

## AVB-Befund für die Planung

Die unabhängig extrahierte Stock-`vbmeta.img` enthält einen
SHA256_RSA4096-Header mit Flags 0 und einen DTBO-Hashdeskriptor für die ersten
146.035 Bytes des Stock-DTBO. Dessen eingebetteter Footer verweist auf
unsignierte Metadaten mit Algorithmus NONE; derselbe Salt und Digest stehen
auch im übergeordneten VBMeta. Das Vorhandensein eines Footers bedeutet daher
keine OEM-Signatur des Debug-DTBO.

[Host-Ausgabe](../validation/stock-avb-info-20261025.log).
Die OEM-Schlüsselauthentizität ist damit nicht unabhängig belegt. Der gebaute
DTBO-Inhalt passt nicht zum Stock-Hashdeskriptor. Das Verhalten des konkreten
entsperrten N100 bei diesen Abweichungen und bei temporärem Boot ist ungetestet.
Ein automatisches Flashen von VBMeta oder Abschalten der Verifikation ist
für diesen Versuch nicht implementiert. Bei Ablehnung zuerst den genauen
Bootloaderfehler auswerten.

## Phase 3: früher Kernel-/USB-Start

Nach Abschluss der vorigen Phasen wird ausschließlich das konkrete Debug-Image
mit passendem DTBO getestet. Es gibt keinen automatischen Fallback zum Flashen
des Boot-Images, falls temporäres Booten abgelehnt wird.

Erster Erfolg: Kernel erreicht Initramfs, RNDIS erscheint am Host und die
Telnet-Debug-Shell auf `192.168.2.15:23` ist erreichbar. Die Host-Adresse und
das tatsächlich neu erschienene USB-Netzinterface werden erst nach Enumeration
zugeordnet. Die unauthentifizierte Shell nur über eine isolierte USB-Verbindung
verwenden.

Erste Nachweise aus dieser Shell sind `/proc/cmdline`, `/proc/mounts`,
`dmesg`, `/init.log` und gegebenenfalls `/diagnosis.log`. `n100_debug=1`
muss vorhanden sein; ein automatischer Userdata-Mount oder Rootfs-Start ist
nicht vorgesehen. Ein leeres Display allein unterscheidet einen Kernelhänger
nicht von einem erfolgreichen USB-Debugstart.

Die anschließende Rootfs-/HAL-Arbeit umfasst weiterhin dynamische
Android-Partitionen, APEX und Firmware-Mounts. Ein erfolgreicher Debugstart
macht den Port noch nicht zu einem installierbaren Alltagsimage.
