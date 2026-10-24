# Sailfish OS für das OnePlus Nord N100 (`billie2`)

**Experimenteller Portierungsstart / pre-bringup. Es gibt noch kein gebautes,
bootgetestetes oder installierbares Sailfish-Image aus diesem Projekt.**

Das Repository enthält die erste gerätespezifische Anpassung und Werkzeuge für
einen Sailfish-OS-Port auf Grundlage von Android 10 (`hybris-17.1`, VNDK 29).
Boot, Display, WLAN und Telefonie sind **ungetestet**. Der präzise Status steht
in [status.json](status.json). Grüne Host-Tests belegen ausschließlich die
Funktion der Portierungswerkzeuge.

Das **Nord N100 ist `billie2`** (Snapdragon 460 / SM4250, Adreno 610).
`billie` ist das Nord N10 und darf nicht als Ersatz verwendet werden.
`lito` bleibt als Build-Plattformname des untersuchten Android-10-Gerätebaums
erhalten; das ist keine Umbenennung der Hardware.

## Enthalten

- Gepinntes Android-Manifest und [Quellen-Lockdatei](sources.lock.json).
- Vorbereitung des Android-Gerätebaums und Erzeugung der dort referenzierten,
  im OnePlus-Quellstand fehlenden `vendor/billie2-perf_defconfig` aus der
  herstellereigenen `bengal-perf_defconfig` plus Sailfish-Ergänzungen.
- Erste RPM-Definitionen für `droid-hal-billie2`, `droid-config-billie2` und
  `droid-hal-version-billie2`, Paketgruppen und SELinux-Konfiguration.
- HAL-/RPM-/Rootfs-Buildhelfer für die offiziellen HADK-Umgebungen.
- Auslesen der Gerätebasis über ADB, Kernel-Konfigurationsprüfung und Prüfung
  von Android-Bootimages mit Header v2.
- Host-Tests und GitHub-Actions-Validierung.

Android-, Kernel- und Sailfish-Upstreamquellen werden über festgelegte Git-
Revisionen eingebunden. Die großen Quellbäume, proprietäre Firmware und SDK-
Downloads werden nicht als Kopien in dieses Repository eingecheckt.

## Zuerst: Gerätebasis feststellen

Auf deinem CachyOS-Rechner mit aktiviertem USB-Debugging und freigegebenem ADB:

```bash
sudo pacman -S --needed git python android-tools
git clone https://github.com/xNeo92x/n100_sailfishos.git
cd n100_sailfishos
python3 tools/n100.py probe --output ../n100-device-report.json
```

Das verändert keine Einstellungen oder Partitionen am Telefon. Bei mehreren
angeschlossenen Geräten zusätzlich `--serial DEINE_ADB_KENNUNG` angeben.
ADB-Seriennummer und IMEI werden nicht in den Bericht übernommen. Fingerprints,
Mountpfade und Eingabegerätenamen stehen darin; Bericht vor dem Teilen prüfen.
Für einige Diagnosepfade kann Android den Zugriff verweigern, was protokolliert
wird. Fehlende Einträge bedeuten nicht, dass die Partition nicht existiert.

**Benötigt werden Modellnummer, installierte OxygenOS-/Android-Version und das
tatsächliche Partitionslayout.** Die konkrete Stock-Firmware ist noch nicht
freigegeben. Android 11 oder ein neueres LineageOS darf nicht beliebig mit der
hier gewählten Android-10-HAL vermischt werden.

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
using an Android 10 / hybris-17.1 base. **No HAL/RPM/rootfs build or hardware boot
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
