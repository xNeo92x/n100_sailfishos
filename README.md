# Sailfish OS für das OnePlus Nord N100 (`billie2`)

**Experimenteller Port / pre-bringup. Android-HAL, alle fünf RPM-Stufen und ein
Sailfish-OS-5.1.0.11-Rootfs sind gebaut. `userdata.img` (8 GiB) und
`hybris-boot.img` wurden erzeugt; ext4-Prüfung erfolgreich. Der Kernel und die frühe Initramfs laufen auf dem N100;
USB-Ping und Telnet-Debug-Shell sind bestätigt. Das aufgespielte Sailfish-Rootfs
5.1.0.11 läuft im schreibgeschützten Chroot. Der vollständige Start mit
systemd und Android-HAL steht noch aus.**

Die Boot-Prüfung fand eine Inkompatibilität des Stock-DTBO mit dem gebauten
Basis-DTB. Für den frühen USB-Debugstart wurden Init und Decoderanforderungen
angepasst; der neue Kernel und das separate Debug-Image sind erfolgreich gebaut.
Die Init-Sperre vor dem Userdata-Mount und die geprüften Gerätebaum-Inhalte sind
im [Buildbericht](validation/debug-image-build-user-20261025.json) festgehalten.
Der [erste Geräteversuch](docs/FIRST_DEVICE_TEST.md) hat die frühe
[USB-Debug-Shell](docs/USB_DEBUG_SHELL.md) erreicht. Speichergeräte, Partitionsnamen
und der [schreibgeschützte Rootfs-Test](docs/ROOTFS_PROBE.md) sind bestätigt.
Die [Super-Metadaten](docs/SUPER_METADATA.md) für Slot B sind ausgewertet.
Alle vier [schreibgeschützten Android-Mounts](docs/ANDROID_MOUNT_PROBE.md) und
deren Cleanup sind am Gerät bestätigt. Auch der Android-Linker und die
Abhängigkeitsauflösung von `servicemanager` funktionieren in einer temporären
RAM-Ansicht; auch `linkerconfig --help` und Cleanup sind bestätigt. Die fehlenden
Dienste `hwservicemanager` und `logd` sind inzwischen nachgebaut und strukturell
geprüft. Auch der korrigierte HAL-RPM-Neubau und die Bibliotheksauflösung aller drei
Dienste aus diesem RPM sind am Gerät bestätigt; Cleanup erfolgreich. Weiter mit
dem [Bau der fehlenden nativen Linker-Provider](docs/LINKER_PROVIDERS.md).
Die Host-Konfiguration ist inzwischen erzeugt. Die strenge Diagnose meldet noch
`libneuralnetworks.so` und `libdexfile.so`; eine Geräteprüfung der Namespaces steht aus.

Das Repository enthält die erste gerätespezifische Anpassung und Werkzeuge für
einen Sailfish-OS-Port mit **Android 13 als HAL-Buildbasis** (`hybris-20.0`,
LineageOS 20) und **Stock-Android-11-Firmware als Kandidat**.
Der vollständige Sailfish-Start, Display, WLAN und Telefonie sind **unbestätigt**. Der präzise Status steht
in [status.json](status.json). Grüne Host-Tests belegen ausschließlich die
Funktion der Portierungswerkzeuge.

Das **Nord N100 ist `billie2`** (Snapdragon 460 / SM4250, Adreno 610).
`billie` ist das Nord N10 und darf nicht als Ersatz verwendet werden.
Die aktuelle Buildbasis verwendet `bengal`, passend zum ausgelesenen Gerät.

## Weiter zum installierbaren Image auf CachyOS

Der vollständige Build soll auf deinem CachyOS-PC laufen. Die neue
[Schrittfolge](docs/CACHYOS_BUILD.md) verbindet Hostprüfung, lokale Treiber-
Sammlung, Vendor-Extraktion, HAL-/RPM-/Rootfs-Build und die Erstellung eines
experimentellen `userdata.img`. Beginne im Repository mit:

```bash
git pull --ff-only
sudo pacman -S --needed git python android-tools curl tar xz e2fsprogs
python3 tools/build-host.py --work-dir "$HOME/n100-build" --initialize
python3 tools/firmware.py --work-dir "$HOME/n100-build" vendor-source
```

Der PC braucht mindestens 200 GiB freien Speicher; dein gemeldeter Rechner
erfüllt diese Voraussetzung. `vendor-source` lädt und prüft eine vollständige,
gepinnte LineageOS-20-Vendor-Buildquelle mit 1.161 Blobs. Der bisherige ADB-Dump
bleibt separat erhalten. Ein erneuter Diagnose-Upload ist dafür nicht nötig. Ein späteres Userdata-Image ersetzt die
Android-Nutzerdaten. Die Image-Erstellung selbst schreibt ausschließlich lokale
Dateien; die noch offene Geräte-/HAL-Integration bleibt Voraussetzung für den
ersten Flash-Versuch.

## Ergebnis des ersten Geräteberichts

| Merkmal | Ausgelesener Wert |
| --- | --- |
| Modell / Region | BE2013 / EEA |
| Android / API | 11 / 30 |
| Vendor-API / VNDK | 30 / 30 |
| Firmware-Anzeige | BE2013_7_231009 |
| Sicherheitsstand | 2023-10-05 |
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
vorgesehen.** Der HAL-Build ist inzwischen erfolgreich abgeschlossen; der vollständige Sailfish-Start
auf dem Gerät ist noch nicht bestätigt.
Details stehen in [docs/DEVICE_BE2013.md](docs/DEVICE_BE2013.md).

## Enthalten

- Gepinntes Android-Manifest und [Quellen-Lockdatei](sources.lock.json).
- Offizieller LineageOS-20-Gerätebaum, dazu passender Kernel und die OnePlus-
  Hardwarebibliotheken; eigene `vendor/sailfish-billie2_defconfig` aus der
  Kernel-Basiskonfiguration plus Sailfish-Ergänzungen.
- Erste RPM-Definitionen für `droid-hal-billie2`, `droid-config-billie2` und
  `droid-hal-version-billie2`, Paketgruppen und SELinux-Konfiguration.
- HAL-/RPM-/Rootfs-Buildhelfer für die offiziellen HADK-Umgebungen.
- Erfolgreicher Android-HAL-Build auf dem CachyOS-Buildhost und strukturell
  geprüftes `hybris-boot.img`; [gemeldeter Buildnachweis](validation/hal-build-user-20261025.json).
- Erfolgreicher HAL-RPM-Build für Sailfish OS 5.1.0.11 im Platform SDK;
  [gemeldeter RPM-Buildnachweis](validation/hal-rpm-build-user-20261025.json).
- Erfolgreicher Build der zwölf Gerätekonfigurations-RPMs und des Kickstarts;
  [gemeldeter Konfigurations-Buildnachweis](validation/configs-rpm-build-user-20261025.json).
- Erfolgreicher Build von libhybris, beiden PulseAudio-Droid-Modulen,
  mce-plugin-libhybris und qt5-qpa-hwcomposer-plugin;
  [gemeldeter Middleware-Buildnachweis](validation/middleware-rpm-build-user-20261025.json).
- Erfolgreicher Versionspaket-Build nach Installation der benötigten Target-Pakete;
  [gemeldeter Abschluss aller RPM-Stufen](validation/version-rpm-build-user-20261025.json).
- Erfolgreicher Sailfish-Rootfs-Build mit Archiv, SHA-256-Datei und Paketliste;
  [gemeldeter Rootfs-Buildnachweis](validation/rootfs-build-user-20261025.json).
- Echte Entwicklungsimages auf dem CachyOS-Host erstellt, Rootfs-Prüfsumme
  bestätigt und ext4-Prüfung abgeschlossen;
  [Buildnachweis mit Manifest und Hashes](validation/image-build-user-20261025.json).
- Auslesen der Gerätebasis über ADB, Kernel-Konfigurationsprüfung und Prüfung
  von Android-Bootimages mit Header v2.
- Host-Tests und GitHub-Actions-Validierung.
- Mit dem echten Kernelquellstand aufgelöste und vom Sailfish-Upstream-Checker
  geprüfte Kernelkonfiguration: keine Fehler, 47 optionale Hinweise;
  [Prüfumfang](docs/KERNEL_CONFIG_VALIDATION.md).
- Erfolgreich kompilierter ARM64-Kernel (4.19.325), DTB, DTBO und drei Module;
  Overlay am Host erfolgreich angewendet. [Build und Prüfnachweise](docs/KERNEL_BUILD.md).
- Vollständig geprüfte Sammlung von 33 Stock-Konfigurationsdateien und
  [Auswertung](docs/CONFIG_BUNDLE.md) für Mounts, Verschlüsselung und HAL-Fragmente.

Android-, Kernel- und Sailfish-Upstreamquellen werden über festgelegte Git-
Revisionen eingebunden. Die großen Quellbäume, proprietäre Firmware und SDK-
Downloads werden nicht als Kopien in dieses Repository eingecheckt.

## Konfigurationssammlung abgeschlossen

Das hochgeladene `n100-configs.zip` wurde ausgewertet: Alle 33 Dateien stimmen
mit Größe und SHA-256 im Inventar überein. Die drei fstab-Dateien sind
Alternativen; die beim Boot gewählte Variante ist noch nicht belegt. Die
VINTF-Fragmente enthalten unter anderem AIDL für Power und Vibration. Die
SELinux-Binärpolicy hat Version 30; ihre Laufzeitkompatibilität ist nicht geprüft.
Details: [Konfigurationsauswertung](docs/CONFIG_BUNDLE.md).
**Ein weiterer Diagnosebericht ist für diese Auswertung nicht erforderlich.**

Ein vorhandenes ZIP lässt sich ohne Telefon erneut prüfen:

```bash
python3 tools/n100.py inspect-configs ../n100-configs.zip --output ../n100-config-audit.json
```

Bei Bedarf kann `collect-configs --output ../n100-configs.zip` die Sammlung
wiederholen. Es benötigt weder Root noch Bootloader-Entsperrung und verändert
keine Dateien auf dem Telefon. Vorhandene Ausgabedateien bleiben erhalten;
neue erhalten bei einer Namenskollision einen Zeitstempel.

## Einzelnen Diagnosebericht sammeln

Auf deinem CachyOS-Rechner mit aktiviertem USB-Debugging und freigegebenem ADB:

```bash
sudo pacman -S --needed git python android-tools
# Im bereits geklonten Repository:
cd n100_sailfishos
git pull --ff-only
python3 tools/n100.py probe --output ../n100-device-report-v2.json
```

Existiert die gewünschte Berichtsdatei bereits, speichert `probe` den neuen
Durchlauf automatisch mit einem Zeitstempel im Dateinamen. Vorhandene Berichte
bleiben erhalten. Der vollständige Speicherpfad wird am Ende angezeigt;
`../` bezeichnet hier den Ordner oberhalb des Repositorys.

Das verändert keine Einstellungen oder Partitionen am Telefon. Bei mehreren
angeschlossenen Geräten zusätzlich `--serial DEINE_ADB_KENNUNG` angeben.
ADB-Seriennummer und IMEI werden nicht in den Bericht übernommen. Fingerprints,
Mountpfade und Eingabegerätenamen stehen darin; Bericht vor dem Teilen prüfen.
Für einige Diagnosepfade kann Android den Zugriff verweigern, was protokolliert
wird. Fehlende Einträge bedeuten nicht, dass die Partition nicht existiert.

Der aktualisierte Sammler liest auch die beobachteten Stock-fstab-Namen einzeln aus und behält stdout,
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

**Noch offene Arbeit:** vollständige Firmware-/HAL-Kompatibilität,
Einrichtung der dynamischen Android-Partitionen, funktionierender Zugriff auf
`/data`, Rootfs-Integration und weitere Hardwaretests. Der erste Kernel-/Initramfs-
Start mit USB-Debug-Shell ist am Gerät bestätigt; Stock-DTBO-Restore und der
vollständige Sailfish-Start bleiben offen. Es gibt deshalb kein Flash-Skript und keine Download-Datei,
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
**The user reports a completed Android HAL build and a structurally valid boot
image, followed by successful HAL and device configuration RPM builds for
Sailfish OS 5.1.0.11. Five middleware packages and the media RPM also built successfully.
The version RPM and the device-specific Sailfish rootfs also built successfully.
The 8 GiB userdata image was created and passed e2fsck on the build host.
The kernel and early hybris initramfs now run on the N100; USB ICMP and the
Telnet debug shell are confirmed, with no userdata mounted. Full Sailfish boot
and hardware services remain unvalidated. This is not an installable release.** The repository provides
pinned sources, initial device/kernel/RPM integration, build helpers and read-only
device diagnostics. Dynamic-partition mounting and access to Android-encrypted
userdata remain critical bring-up work. The standalone kernel, DTB, DTBO and three
modules compile successfully; the device configuration bundle has been inspected.
Follow the linked build and bring-up notes. Do not use Nord N10 (`billie`) images.

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
