# Auf dem CachyOS-PC zum Image

Ziel sind ein passendes `hybris-boot.img` und ein `userdata.img` mit dem gebauten
Sailfish-System. Der Kernel ist gebaut. **HAL, RPM-Pakete und Rootfs sind noch
nicht gebaut; deshalb gibt es derzeit kein freigegebenes Installationsimage.**
Der folgende Ablauf bereitet diese Builds auf deinem PC vor. Die beiden SDKs
bleiben erforderlich; native Arch-Bibliotheken ersetzen sie nicht.

## 1. Host und Firmwaredateien vorbereiten

Im geklonten Repository:

```bash
git pull --ff-only
sudo pacman -S --needed git python android-tools curl tar xz e2fsprogs
python3 tools/build-host.py --work-dir "$HOME/n100-build" --initialize
```

Die Prüfung verlangt Linux x86_64, mindestens 200 GiB freien Speicher sowie ein
geeignetes Dateisystem. Plane besser 250–300 GiB und mindestens 16 GiB RAM ein.
Bei fehlendem Platz kannst du `--work-dir` auf ein anderes lokales Linux-
Dateisystem setzen. In beiden SDKs muss der Android-Baum unter demselben Pfad
erreichbar sein; das Home-Verzeichnis ist dafür der einfachste Ausgangspunkt.
Die Prüfung installiert keine SDKs und führt keine Telefonbefehle aus.

Lass Stock Android 11 laufen, aktiviere USB-Debugging und bestätige den
ADB-Zugriff. Sichere dann die tatsächlich benötigten Dateien:

```bash
python3 tools/firmware.py --work-dir "$HOME/n100-build" collect
```

Der Sammler verwendet die Liste aus dem **gepinnten** LineageOS-20-Gerätebaum:
1.161 Dateien, darunter zehn Umbenennungen zwischen Stock- und Buildpfad.
Er fragt die Geräteidentität ab, führt ausschließlich Lesezugriffe auf dem
Telefon aus und benötigt weder Root noch eine Bootloader-Entsperrung. Dateien
bleiben unter `~/n100-build/firmware/files`; das Inventar enthält Prüfsummen,
Firmware-Fingerprint, fehlende Dateien und Abweichungen bei Upstream-Pins.
Du musst diese proprietären Dateien nicht hochladen.

Bei Unterbrechungen denselben Befehl wiederholen. Bereits inventarisierte,
unveränderte Dateien werden übersprungen; nicht verfügbare erneut versucht.
Ein unvollständiges `.partial` nach einem Prozessabbruch wird zur Prüfung
gemeldet. Änderungen vorhandener Dateien oder ein Firmwarewechsel werden nicht
überschrieben. Zum Prüfen ohne Telefon:

```bash
python3 tools/firmware.py --work-dir "$HOME/n100-build" verify
```

46 Dateien haben Upstream-SHA-1-Pins. Unterschiede zwischen deiner EEA-Firmware
und der globalen Referenz werden **vor** der Anwendung der ELF-Fixups gemeldet.
Ein vollständiger Dump kann deshalb trotzdem noch eine Prüfung benötigen.
Bei Zugriffsverweigerungen bleibt die Sammlung unvollständig; das Werkzeug
versucht keinen Root-Zugriff. Dann ist ein passender Stock-Firmware-Dump als
zusätzliche Quelle nötig.

## 2. Offizielle SDKs einrichten

Installiere das Platform SDK nach der
[offiziellen Anleitung](https://docs.sailfishos.org/Tools/Platform_SDK/Installation/)
unter `/srv/sailfishos/sdks/sfossdk`. Lade die Archive über HTTPS mit normaler
Zertifikatsprüfung herunter. Das SDK braucht sudo und einen Mount ohne `nosuid`.
Danach im Platform SDK die Ubuntu-HABUILD-Umgebung nach
[HADK: SDKs einrichten](https://hadk.sailfishos.org/setupsdk/) hinzufügen.
Die aktuell dokumentierte Ubuntu-Umgebung ist Focal mit dem Archiv
`ubuntu-focal-20210531-android-rootfs.tar.bz2`.

Lade passende Tooling- und aarch64-Target-Archive derselben Sailfish-Version
nach [Target Installation](https://docs.sailfishos.org/Tools/Platform_SDK/Target_Installation/).
Erstelle zuerst das Tooling und dann das Gerätetarget
`oneplus-billie2-aarch64`. Die tatsächliche Version mit `sdk-assistant list`
prüfen und als `RELEASE` setzen. Für dieses Gerät ist noch keine Version
durch einen vollständigen Build bestätigt; `latest` ist keine Release-Freigabe.

Die erzeugte `~/n100-build/build.env` in **jeder** SDK-Shell laden:

```bash
source "$HOME/n100-build/build.env"
```

Das setzt `ANDROID_ROOT`, den Portpfad und den Pfad zum bereits bei der
Firmware-Sammlung gespeicherten Gerätebericht. Es ist kein erneuter Upload nötig.

## 3. Android HAL bauen — HABUILD_SDK

```bash
source "$HOME/n100-build/build.env"
python3 "$N100_PORT/tools/n100.py" init --android-root "$ANDROID_ROOT" \
  --device-report "$N100_DEVICE_REPORT" --sync --jobs 4
python3 "$N100_PORT/tools/n100.py" prepare --android-root "$ANDROID_ROOT"
python3 "$N100_PORT/tools/firmware.py" --work-dir "$N100_FIRMWARE_WORK" \
  import --android-root "$ANDROID_ROOT"
```

Der Import verlangt eine vollständige, unveränderte und hinsichtlich der
gepinnten Blobs passende Sammlung. Vorhandene Vendor-Ausgaben werden nicht
gelöscht oder überschrieben. Er führt die Original-Extraktion samt Fixups aus;
deren Erfolg und die erzeugten Makefiles werden geprüft.

Anschließend die Hybris-Patches auf den sauberen Baum anwenden:

```bash
cd "$ANDROID_ROOT"
bash hybris-patches/apply-patches.sh --mb && \
  git -C hybris-patches rev-parse HEAD > .n100-hybris-patches-applied
bash "$N100_PORT/tools/build-hal.sh"
```

Bei fehlgeschlagenen Patches oder einem Buildfehler anhalten und den konkreten
Fehler beheben. Logs stehen in `$ANDROID_ROOT/n100-build-logs`. Der Buildhelfer
prüft die Kernelkonfiguration und die Struktur des Bootimages. Ein Android-13-
Build kann gegenüber dem alten Focal-Chroot weitere Build-Abhängigkeiten
benötigen; die erste vollständige Kombination ist noch zu validieren.

## 4. RPMs und Rootfs bauen — PLATFORM_SDK

`RELEASE` muss der wirklich installierten Target-Version entsprechen:

```bash
source "$HOME/n100-build/build.env"
export RELEASE=DEINE_TATSAECHLICHE_TARGET_VERSION
bash "$N100_PORT/tools/build-packages.sh" all
```

Das Ergebnis des letzten Schritts ist ein `sfe-billie2-*.tar.bz2`. Die
Paketauflösung und weitere Middleware-Anpassungen sind noch nicht erprobt.
Das Rootfs muss bereits die billie2-Anpassung und die AArch64-Laufzeit enthalten;
ein generisches Sailfish-Archiv genügt nicht.

## 5. Experimentelle Image-Dateien erzeugen

Mit den echten Ausgabepfaden aus dem erfolgreichen Build, auf dem Host:

```bash
sudo python3 tools/make-userdata-image.py \
  --rootfs /absoluter/pfad/zum/sfe-billie2-RELEASE.tar.bz2 \
  --boot "$HOME/n100-build/android/out/target/product/billie2/hybris-boot.img" \
  --output-dir "$HOME/n100-build/images/erster-build" --size-gib 8
```

Das Werkzeug erzeugt reguläre Dateien, greift auf kein Telefon zu und verwendet
keine Blockgeräte. Es prüft Archivpfade, Sailfish-/billie2-Identität und
AArch64-Header von systemd sowie Android init. Root-Rechte werden ausschließlich
für den Erhalt von Eigentümern, Berechtigungen und erweiterten Attributen beim
Entpacken benötigt. Nur das selbst gebaute, vertrauenswürdige Archiv verwenden.
`mke2fs -d` füllt das ext4-Dateisystem ohne Loop-Mount; `e2fsck -f -n` prüft es.
Der Platzbedarf umfasst das entpackte Rootfs und das vollständige Image.
Die gewählten 8 GiB sind die anfängliche Dateisystemgröße; eine größere
Telefonpartition wird dadurch nicht automatisch vollständig ausgenutzt.

| Ausgabe | Inhalt |
| --- | --- |
| `hybris-boot.img` | Kopie des strukturell geprüften Bootimages |
| `userdata.img` | ext4 mit `.stowaways/sailfishos` für Hybris |
| `SHA256SUMS` | Prüfsummen beider Images |
| `image-manifest.json` | Eingabe-Hashes, Prüfungen und verbleibende Installationsblocker |

Das ist ein **neues, unverschlüsseltes Entwicklungs-Dateisystem**. Ein späteres
Flashen auf `userdata` ersetzt die Android-Nutzerdaten; bestehende Android-
Verschlüsselung wird damit nicht entschlüsselt. Kein Verkleinern oder Umbauen
des vorhandenen Dateisystems findet statt. Ein Debug-Marker hält den ersten
Start im Hybris-Debugmodus. Eine GUI oder funktionierende Hardware wird nicht
behauptet. Der ext4-Dateisystembau wurde mit Testdaten geprüft; ein echtes
Sailfish-Userdata-Image wurde noch nicht erzeugt.

## 6. Vor dem ersten Aufspielen noch erforderlich

Das Erzeugen der Dateien ist kein Nachweis ihrer Gerätekompatibilität.
Vor Flash-Befehlen müssen insbesondere Stock-Bootheader und DTBO, tatsächliche
Partitionskapazität, A/B-Slot, AVB, Wiederherstellung sowie die Bereitstellung
der Android-HAL-Partitionen geklärt sein. Der Bootloader ist laut bisherigem
Bericht gesperrt; sein Entsperren löscht Nutzerdaten. Ein geprüftes Backup und
ein passender Wiederherstellungsweg werden dafür benötigt.

Die fehlende dynamische Mount-/HAL-Integration ist in [BRINGUP.md](BRINGUP.md)
aufgeführt. `userdata.img` löst ausschließlich die Bereitstellung des Linux-
Rootfs. Es stellt keine System-, Vendor- oder ODM-Partitionen bereit. Deshalb
bleibt `flashable_release` auch bei erfolgreicher Dateierstellung `false`, bis
die vollständige Kette auf dem Gerät validiert ist.
