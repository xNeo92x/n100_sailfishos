# N100: erster Kernel-/USB-Test

Der Debug-Build ist abgeschlossen. Der Benutzer hat die Datensicherung und den
Factory Reset bestätigt; der neue [Gerätebefund](../validation/unlock-state-user-20261025.json)
meldet `unlocked: yes` und Slot `b`. Der erste temporäre Debugstart hat die frühe USB-Shell erreicht;
Ping, Telnet und n100_debug=1 sind bestätigt. Weiter mit [USB_DEBUG_SHELL.md](USB_DEBUG_SHELL.md); die Testblöcke
unten sind die dokumentierte Wiederholungsanleitung. Kein weiterer Build ist für diesen Kandidaten erforderlich.

## Testumfang und feste Dateien

Produktverzeichnis auf dem CachyOS-PC:
`/home/eugen/n100-build/android/out/target/product/billie2`.

| Datei | Bytes | SHA-256 |
| --- | ---: | --- |
| `n100-debug-boot-20261025T191724Z.img` | 45.064.192 | `6dccc17b73f572a42400274ca8ce50595d9e4a8127732e4e9d53d29d1727a6ac` |
| gebautes `dtbo.img` | 25.165.824 | `c14f4bce02f0254a5eb381940767cb00bda9c740d5ad2cefa271f69bd859d46e` |
| erzeugtes `dtbo-stock-24MiB.img` | 25.165.824 | `2d663aaa05568bff83c7e96abe72eef85d4e9e14c5c9e70da8b7274278c23706` |

[Debug-Packerbericht](../validation/debug-image-report-user-20261025.json) und
[Host-Prüfungen](../validation/first-device-test-preparation.json).
Das Debug-Image enthält `n100_debug=1` und bleibt vor dem automatischen
Userdata-Mount in der frühen USB-Sitzung. Das 8-GiB-`userdata.img` wird für
diesen Versuch nicht verwendet. Dynamische Android-Mounts, APEX und normaler
Sailfish-Start bleiben spätere Portierungsarbeit.

Das Stock-DTBO passt am Host nicht auf den gebauten Basis-DTB. Deshalb wird nur
`dtbo_b` mit dem passenden gebauten DTBO beschrieben, während das Debug-Bootimage
per `fastboot boot` temporär in den RAM geladen wird. Es gibt keinen
automatischen Fallback zum Flashen des Boot-Images. Der DTBO-Schreibvorgang ist
dauerhaft: **vor einem normalen Android-Neustart das Stock-DTBO zurückspielen**.

## 1. Testdateien auf CachyOS vorbereiten

Ein neues Terminal direkt auf **CachyOS** verwenden, außerhalb der SDK-Chroots:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
python3 "$N100_PORT/tools/prepare-first-boot.py" prepare \
  --android-root "$ANDROID_ROOT" \
  --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z" \
  --output "$HOME/n100-build/first-device-test"
)
```

Der Helfer prüft alle Eingaben vor dem Erstellen von Ausgaben und verweigert
vorhandene Zielverzeichnisse. Er kopiert den exakten Debug-Boot-/DTBO-Stand und
erzeugt die Rückspiel-Datei aus dem unveränderten Original-DTBO der passenden
EEA-Voll-OTA. Er führt keine Telefonbefehle aus.

Das originale DTBO ist 8 MiB groß, die physische Partition 24 MiB. Der Helfer
erhält DTBO-Daten und eingebettetes VBMeta und versetzt den unveränderten
AVB-Footer ans Ende eines vollständigen 24-MiB-Images. Das Ergebnis ist
bytegleich mit `avbtool resize_image` aus dem gepinnten LineageOS-AVB-Stand.
Dessen `verify_image` bestätigt den eingebetteten Stock-DTBO-Hashdeskriptor.
Die Originaldatei wird nicht geändert.

Diese Host-Prüfungen belegen keine physische Wiederherstellung. Die OTA-Dateien
sind keine Rohsicherungen beider Slots. Das originale Top-Level-VBMeta enthält
einen DTBO-Hashdeskriptor über 146.035 Stock-Bytes; der neue Test-DTBO-Inhalt
weicht davon ab. Das Verhalten dieses entsperrten Bootloaders bei AVB-Abweichungen
und temporärem Boot bleibt bis zum Versuch offen. Keine VBMeta-Änderung ist
für diesen Versuch vorgesehen. [AVB-Befund](../validation/stock-avb-info-20261025.log).

## 2. Rückkehr in Fastboot über Tasten vor dem Flash prüfen

Das Telefon vollständig ausschalten. Dann **Lauter + Leiser + Ein/Aus**
zum Start in Bootloader-Fastboot halten. Diesen Weg zuerst mit unverändertem
DTBO ausprobieren, damit er bei einem Kernelhänger bekannt ist.
Die gerätespezifische [postmarketOS-Dokumentation](https://wiki.postmarketos.org/wiki/OnePlus_Nord_N100_(oneplus-billie2))
nennt diese Kombination.

Nur das N100 per USB anschließen. Der folgende Helfer muss den Modus als
`is-userspace: no` erkennen. Bei abweichendem Modus oder wenn der Tastenweg
nicht funktioniert, vor einem Schreibvorgang die konkrete Anzeige klären.
Nicht nur die beiden Lautstärketasten beim Anstecken halten: das kann bei
Qualcomm-Geräten einen anderen Modus auslösen.

## 3. DTBO schreiben und Debug-Kernel temporär starten

Der folgende Block prüft zuerst die drei Testdateien und das Stock-Original,
genau ein Fastboot-Gerät, `bengal`, `unlocked: yes`, aktiven Slot B,
Bootloader-Fastboot, Partitionsgrößen und Downloadkapazität. Er ändert weder
Slot A noch Boot/VBMeta/Userdata. Alle Fastboot-Meldungen aufbewahren.

```bash
(
set -e
source "$HOME/n100-build/build.env"
n100_bundle="$HOME/n100-build/first-device-test"
python3 "$N100_PORT/tools/prepare-first-boot.py" check \
  --bundle "$n100_bundle" \
  --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z"

if ! fastboot flash dtbo_b "$n100_bundle/dtbo-test.img"; then
    echo "DTBO-Flash fehlgeschlagen: Stock-DTBO wiederherstellen."
    fastboot flash dtbo_b "$n100_bundle/dtbo-stock-24MiB.img"
    fastboot reboot
    exit 1
fi
if ! fastboot boot "$n100_bundle/debug-boot.img"; then
    echo "Temporärer Start fehlgeschlagen: Stock-DTBO wiederherstellen."
    fastboot flash dtbo_b "$n100_bundle/dtbo-stock-24MiB.img"
    fastboot reboot
    exit 1
fi
echo "Debugstart gesendet. Vor normalem Android-Neustart Stock-DTBO wiederherstellen."
)
```

Bei einem Fehler im temporären Start versucht der Block die Wiederherstellung.
Ist das Gerät dabei nicht mehr in Fastboot, kann der Restore-Befehl auf USB
warten. Dann den unten beschriebenen Tastenweg verwenden; kein weiterer Flash
auf Verdacht. Ein erfolgreich beendetes `fastboot boot` bedeutet nur, dass der
Bootloader den Auftrag angenommen hat.

Ein leeres Display ist beim frühen USB-Test möglich. Etwa 30–60 Sekunden nach
dem Bootauftrag auf CachyOS prüfen:

```bash
ip -br addr
lsusb
```

Die Fastboot-Ausgabe und diese beiden Ausgaben im Gespräch bereitstellen.
Erster Erfolg ist ein neu erscheinendes RNDIS-Netzinterface. Die Ramdisk bietet
DHCP für den USB-Host und eine Telnet-Shell auf `192.168.2.15:23`; die tatsächliche
Enumeration und Erreichbarkeit müssen erst am Gerät bestätigt werden.
Die unauthentifizierte Entwicklungsshell nur auf der isolierten USB-Verbindung
verwenden. Erste Shell-Nachweise sind `/proc/cmdline`, `/proc/mounts`,
`dmesg`, `/init.log` und gegebenenfalls `/diagnosis.log`.

## 4. Stock-DTBO wiederherstellen und Android starten

Vor dem normalen Android-Start zurück in Bootloader-Fastboot. Bei einem
hängenden Debug-Kernel zum erzwungenen Ausschalten **Ein/Aus + Lauter etwa
10 Sekunden** halten; danach aus dem ausgeschalteten Zustand den zuvor
geprüften Fastboot-Tastenweg verwenden. Die
[N100-Herstelleranleitung](https://manuals.plus/m/5dcc971ef81e6d18fe67dd04cef2115a599566491f9bedfaa3da9169baa5b620_optim.pdf)
beschreibt das erzwungene Ausschalten mit Ein/Aus + Lauter.

Dann am CachyOS-Host:

```bash
(
set -e
source "$HOME/n100-build/build.env"
python3 "$N100_PORT/tools/prepare-first-boot.py" check \
  --bundle "$HOME/n100-build/first-device-test" \
  --stock-dir "$HOME/n100-build/stock-BE2013-20261025T180040Z"
fastboot flash dtbo_b "$HOME/n100-build/first-device-test/dtbo-stock-24MiB.img"
fastboot reboot
)
```

Bei verändertem Slot hält die Prüfung an; nicht einfach beide DTBO-Slots
beschreiben. Erst den neuen Zustand klären. Bei Restore-Fehlern vor dem
normalen Neustart die konkrete Ausgabe prüfen. Den Bootloader mit
unverifizierten Entwicklungsimages nicht wieder sperren.

## Entsperren bereits erledigt

Der Benutzer hat den Datenverlust bestätigt und anschließend `unlocked: yes`,
`current-slot: b` und erfolgreichen `fastboot reboot` gemeldet.
[OnePlus nennt für das N100 `fastboot oem unlock`](https://service.oneplus.com/in/search/search-detail?id=op588).
Dieser Schritt muss nicht wiederholt werden. Die neue Zustandsmeldung
bestätigt noch keinen Boot des Sailfish-Kernels.
