# Sailfish OS für das OnePlus Nord N100 (`billie2`)

**Experimenteller Portierungsstart / pre-bringup. Es gibt noch kein gebautes,
bootgetestetes oder installierbares Sailfish-Image aus diesem Projekt.**

Das Repository enthält die erste gerätespezifische Anpassung und Werkzeuge für
einen Sailfish-OS-Port mit **Android 13 als HAL-Buildbasis** (`hybris-20.0`,
LineageOS 20) und **Stock-Android-11-Firmware als Kandidat**.
Boot, Display, WLAN und Telefonie sind **ungetestet**. Der präzise Status steht
in [status.json](status.json). Grüne Host-Tests belegen ausschließlich die
Funktion der Portierungswerkzeuge.

Das **Nord N100 ist `billie2`** (Snapdragon 460 / SM4250, Adreno 610).
`billie` ist das Nord N10 und darf nicht als Ersatz verwendet werden.
Die aktuelle Buildbasis verwendet `bengal`, passend zum ausgelesenen Gerät.

## Ergebnis des ersten Geräteberichts

| Merkmal | Ausgelesener Wert |
| --- | --- |
| Modell / Region | BE2013 / EEA |
| Android / API | 11 / 30 |
| Stock-Kernel | 4.19.152-perf+ |
| Plattform | bengal |
| Aktiver Slot zum Messzeitpunkt | B |
| Bootloader / Verified Boot | gesperrt / green |
| Datenpartition | F2FS, dateibasiert verschlüsselt |
| Systemlayout | dynamische system-, system_ext-, vendor- und product-Partitionen |

Die frühere Android-10-Basis passt nicht zur installierten Firmware und wurde
durch den neueren Entwicklungskandidaten ersetzt. Der offizielle LineageOS-20-
Gerätebaum verwendet Android-11-Stock-Blobs; die EEA-Version und deren Fixups
müssen noch geprüft werden. **Ein Android-Downgrade ist für diesen Ansatz nicht
vorgesehen.** Er ist noch nicht gebaut oder als startfähig bestätigt.
Details stehen in [docs/DEVICE_BE2013.md](docs/DEVICE_BE2013.md).

## Enthalten

- Gepinntes Android-Manifest und [Quellen-Lockdatei](sources.lock.json).
- Offizieller LineageOS-20-Gerätebaum, dazu passender Kernel und die OnePlus-
  Hardwarebibliotheken; eigene `vendor/sailfish-billie2_defconfig` aus der
  Kernel-Basiskonfiguration plus Sailfish-Ergänzungen.
- Erste RPM-Definitionen für `droid-hal-billie2`, `droid-config-billie2` und
  `droid-hal-version-billie2`, Paketgruppen und SELinux-Konfiguration.
- HAL-/RPM-/Rootfs-Buildhelfer für die offiziellen HADK-Umgebungen.
- Auslesen der Gerätebasis über ADB, Kernel-Konfigurationsprüfung und Prüfung
  von Android-Bootimages mit Header v2.
- Host-Tests und GitHub-Actions-Validierung.

Android-, Kernel- und Sailfish-Upstreamquellen werden über festgelegte Git-
Revisionen eingebunden. Die großen Quellbäume, proprietäre Firmware und SDK-
Downloads werden nicht als Kopien in dieses Repository eingecheckt.

## Ergänzenden Bericht sammeln

Auf deinem CachyOS-Rechner mit aktiviertem USB-Debugging und freigegebenem ADB:

```bash
sudo pacman -S --needed git python android-tools
# Im bereits geklonten Repository:
cd n100_sailfishos
git pull --ff-only
python3 tools/n100.py probe --output ../n100-device-report-v2.json
```

Das verändert keine Einstellungen oder Partitionen am Telefon. Bei mehreren
angeschlossenen Geräten zusätzlich `--serial DEINE_ADB_KENNUNG` angeben.
ADB-Seriennummer und IMEI werden nicht in den Bericht übernommen. Fingerprints,
Mountpfade und Eingabegerätenamen stehen darin; Bericht vor dem Teilen prüfen.
Für einige Diagnosepfade kann Android den Zugriff verweigern, was protokolliert
wird. Fehlende Einträge bedeuten nicht, dass die Partition nicht existiert.

Der aktualisierte Sammler liest fstab-Dateien einzeln aus und behält stdout,
stderr und Exitcode bei fehlgeschlagenen Diagnosen. Das behebt den Verlust
von Teilergebnissen im ersten Bericht. Zusätzlich sammelt er Vendor-API,
Firmware-Anzeige, VINTF-Manifest, SELinux-Version und Partitionsgrößen.

Ein gespeicherter Bericht kann ohne Telefon offline ausgewertet werden:

```bash
python3 tools/n100.py analyze-report ../n100-device-report-v2.json
```

Bootloader-Entsperrung und Root sind für diesen Schritt nicht erforderlich.
Das Build-Setup verlangt einen passenden Gerätebericht und lehnt die
Wiederverwendung der alten Android-10-Quellstruktur ab.

## Bauen und weiterentwickeln

1. [Build-Anleitung](docs/BUILD.md): SDK, Quellsynchronisierung, Anpassungen,
   HAL, RPM und Rootfs.
2. [Hardware-Bring-up](docs/BRINGUP.md): dynamische Partitionen, entschlüsseltes
   `/data`, USB-Debugging, Grafik und weitere Hardware.
3. [Quellen und technische Entscheidungen](docs/SOURCES.md).

Der Bau benötigt einen Linux-x86_64-Rechner, das Sailfish Platform SDK samt
passendem aarch64-Target und das Android-Build-Chroot. Mindestens **200 GiB freie
Build-Kapazität**, besser 250–300 GiB, sowie 16 GiB RAM sind eine sinnvolle
Planungsgröße. CachyOS kann der Host sein; Android und RPM werden in den
getrennten HADK-Umgebungen gebaut.

**Noch offene Arbeit:** vollständiger Build, verifizierte Firmwarebasis,
Kernel/DTB/DTBO-Abgleich, Einrichtung der dynamischen Partitionen für den frühen
Boot, funktionierender Zugriff auf `/data`, Rootfs-Integration sowie Tests auf
dem echten Gerät. Es gibt deshalb kein Flash-Skript und keine Download-Datei,
die als fertiger Port ausgegeben wird. Das Bootloader-Entsperren löscht die
Nutzerdaten; Partitionen sollten erst nach vollständiger Sicherung und mit
bekanntem Wiederherstellungsweg verändert werden.

## Werkzeugtests

```bash
python3 -m unittest discover -s tests -v
bash -n tools/build-hal.sh tools/build-packages.sh
```

## English

Experimental Sailfish OS adaptation work for the OnePlus Nord N100 (`billie2`),
using an Android 13 / hybris-20.0 HAL build base with Android 11 stock firmware
as a candidate baseline. The first BE2013 report confirms Android 11, bengal,
locked bootloader, dynamic partitions and encrypted F2FS userdata.
**No HAL/RPM/rootfs build or hardware boot
has been validated. This is not an installable release.** The repository provides
pinned sources, initial device/kernel/RPM integration, build helpers and read-only
device diagnostics. Dynamic-partition mounting and access to Android-encrypted
userdata remain critical bring-up work. Start with the device report command above;
follow the linked build and bring-up notes. Do not use Nord N10 (`billie`) images.

## Herkunft und Lizenzen

Die neu geschriebenen Python-/Shell-Werkzeuge und Dokumentation stehen unter
[MIT](LICENSE). Abgeleitete RPM-Definitionen behalten die Upstream-Lizenz;
siehe [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Dieses Projekt wurde mit ChatGPT erstellt. Es ist ein inoffizielles Community-
Projekt und keine Zusage für Jolla Store, Android AppSupport oder lizenzierte
Zusatzfunktionen.

Danke an [Sailfish OS](https://github.com/sailfishos),
[mer-hybris](https://github.com/mer-hybris),
[LineageOS](https://github.com/LineageOS),
[danascape](https://github.com/danascape) und
[OnePlusOSS](https://github.com/OnePlusOSS) für die Portierungswerkzeuge und
Geräte-/Kernelquellen.
