# Auf dem CachyOS-PC zum Image

Ziel sind ein passendes `hybris-boot.img` und ein `userdata.img` mit dem gebauten
Sailfish-System. **Kernel und Android-HAL sind auf dem CachyOS-Buildhost erfolgreich
gebaut; HAL-RPM, Gerätekonfiguration, fünf Middleware-Pakete und das Medien-RPM
sowie das Versionspaket und das Sailfish-Rootfs sind fertig. Die Erstellung
der Image-Dateien ist abgeschlossen; der Geräteboot steht noch aus.** Der gemeldete HAL-Build vom
25. Oktober 2026 erzeugte ein strukturell geprüftes `hybris-boot.img` mit
45.064.192 Byte. [Buildnachweis](../validation/hal-build-user-20261025.json).
Die Binärdatei wurde nicht hochgeladen; ihre Startfähigkeit ist noch ungetestet.
Es gibt derzeit kein freigegebenes Installationsimage.
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

Die bisherige Sammlung auf deinem BE2013 ergab 487 von 1.161 Dateien; 674
Dateien fehlen. Die alte Ausgabe enthält die eigentlichen ADB-Fehlertexte
nicht, deshalb lässt sich daraus kein sicherer Grund für die fehlenden Dateien
ablesen. Ein erneuter unveränderter Sammellauf ist nicht der nächste Schritt.

Verwende stattdessen die vollständige, separat geprüfte Vendor-Buildquelle:

```bash
python3 tools/firmware.py --work-dir "$HOME/n100-build" vendor-source
```

Dieser Befehl benötigt **keine Telefonverbindung und kein ADB**. Er holt den
fest gepinnten `lineage-20`-Stand von
[TheMuppets/proprietary_vendor_oneplus_billie2](https://github.com/TheMuppets/proprietary_vendor_oneplus_billie2)
und prüft alle 1.161 benötigten Binärdateien sowie vier Builddateien über einen
festgelegten SHA-256-Inventarhash. Entpackt sind es rund 480 MiB an Blobs.
Das ist eine vollständige, vorab extrahierte Buildquelle; ihre Funktion mit
deiner EEA-Firmware oder Sailfish ist noch nicht durch einen Boot bestätigt.

Der Cache liegt unter `~/n100-build/vendor-source`. Das Audit wird als
`vendor-source-audit.json` gespeichert. Dein bisheriger Stock-Dump unter
`~/n100-build/firmware` bleibt separat erhalten. Es werden keine Bestandteile
beider Quellen vermischt und keine Blobs in dieses öffentliche Port-Repository
kopiert. Nochmals ausführen prüft den unveränderten Cache erneut.

Die Upstream-Liste verlangt für WiFi Display ausdrücklich `lemonadep`-Dateien
vom OnePlus 9 Pro mit Android 12. Ein reiner N100-Stock-Dump kann diese Pins
nicht vollständig erfüllen. Die frühere Erklärung ausschließlich über
EEA-/Global-Unterschiede war deshalb unvollständig. Die vorab extrahierte
Vendor-Quelle stimmt mit 34 der 46 Raw-Referenzpins überein. Zwölf WiFi-Display-
Dateien unterscheiden sich; deren exakte Inhalte und Hashes sind separat in
`config/vendor-source.json` festgehalten. Diese Deklaration bestätigt ihre
Herkunft und Integrität, nicht ihre Laufzeitkompatibilität. Jede weitere oder
veränderte Abweichung stoppt die Prüfung.

Der bei deiner bisherigen Sammlung gespeicherte Bericht unter
`~/n100-build/firmware/device-report.json` wird für die Quellinitialisierung
wiederverwendet. Nur bei einer **neuen Einrichtung ohne vorhandenen Bericht**
ist einmalig folgender Lesezugriff auf das laufende Stock Android nötig:

```bash
python3 tools/n100.py probe --output "$HOME/n100-build/firmware/device-report.json"
```

Die ursprüngliche Raw-Sammlung bleibt als Diagnose-/Extraktionswerkzeug
verfügbar. Sie speichert nun stderr, stdout, Exitcode und Fehlerkategorie,
meldet eine kurze Zusammenfassung und schreibt Details in `verification.json`.
Bekannte permanente Pfad-/Zugriffsfehler werden nur mit `collect --retry-failed`
erneut versucht. Eine unvollständige Raw-Sammlung ist keine Voraussetzung für
`vendor-source`.

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

### Konkrete Einrichtung für Sailfish OS 5.1.0.11

Auf dem Buildhost ist das Platform SDK 5.1.0.11 vorhanden. Die beiden
versionsgebundenen Archive unten waren am 25. Oktober 2026 über HTTPS mit
HTTP 200 erreichbar. `sdk-assistant list` war vor dieser Einrichtung leer.

Im **Platform SDK** zuerst Tooling und danach das aarch64-Gerätetarget anlegen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
sudo zypper install kmod createrepo_c
sdk-assistant create SailfishOS-5.1.0.11 \
  https://releases.sailfishos.org/sdk/targets/Sailfish_OS-5.1.0.11-Sailfish_SDK_Tooling-i486.tar.7z
sdk-assistant create oneplus-billie2-aarch64 \
  https://releases.sailfishos.org/sdk/targets/Sailfish_OS-5.1.0.11-Sailfish_SDK_Target-aarch64.tar.7z \
  --tooling SailfishOS-5.1.0.11
sdk-assistant list
)
```

Rückfragen zum Anlegen mit `y` bestätigen. Bereits erfolgreich angelegte
Einträge nicht erneut anlegen. Falls nur das zweite Anlegen fehlschlägt, zuerst
den konkreten Fehler beheben und anschließend nur diesen Target-Schritt
wiederholen. Die Tooling-Zuordnung wird ausdrücklich angegeben; der generische
Targetname aus der SDK-Dokumentation wird durch den HADK-Gerätenamen ersetzt.
Das i486-Tooling läuft auf dem Buildhost, das aarch64-Target stellt die
Zielumgebung für die N100-Pakete bereit.

Nach erfolgreicher Einrichtung und abgeschlossenem HAL-Build zunächst die
Droid-HAL-RPMs bauen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" hal
)
```

Damit beginnt die erste Paket-/Target-Validierung dieser Kombination. Den
weiteren Config-/Middleware-/Rootfs-Build erst nach erfolgreichem HAL-RPM-Build
fortsetzen. `RELEASE` in jeder neuen Paket-Build-Shell erneut setzen.

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
python3 "$N100_PORT/tools/n100.py" sync-build-deps --android-root "$ANDROID_ROOT"
python3 "$N100_PORT/tools/n100.py" prepare --android-root "$ANDROID_ROOT"
python3 "$N100_PORT/tools/firmware.py" --work-dir "$N100_FIRMWARE_WORK" \
  vendor-source --android-root "$ANDROID_ROOT"
```

Der Befehl prüft den vollständigen Vendor-Cache und bindet ihn als Git-Worktree
unter `vendor/oneplus/billie2` ein. Vorhandene andere Vendor-Ausgaben werden nicht
überschrieben. Der HAL-Build prüft den installierten Stand und Inventarhash erneut.
Bei dieser vorab extrahierten Quelle **nicht zusätzlich `extract-files.sh` oder
`firmware.py import` ausführen**: Die gelieferten Builddateien und Binärdateien
werden direkt verwendet; Fixups werden nicht ein zweites Mal angewandt.

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

### Nach Abbruch wegen des veralteten PATH-Schalters fortsetzen

Die Meldung `TEMPORARY_DISABLE_PATH_RESTRICTIONS was a temporary migration
method, and is now obsolete.` ist unter Android 13 ein fataler Buildfehler.
Eine ältere Port-Konfiguration setzte diesen Schalter noch auf `true`.
Die korrigierte `config/port.env` entfernt ihn auch aus geerbten Umgebungen.

Wenn Vorbereitung, Vendor-Installation und Hybris-Patches bereits erfolgreich
waren und `.n100-hybris-patches-applied` geschrieben wurde, im Ubuntu-Chroot
nur die Port-Werkzeuge aktualisieren und den Build fortsetzen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:$PATH"
bash "$N100_PORT/tools/build-hal.sh"
)
```

Kein erneutes Patchen oder `repo sync` ist dafür nötig. Die Korrektur behebt
den nachgewiesenen Konfigurationsabbruch. Der spätere erfolgreiche HAL-Build
ist im Buildnachweis am Anfang dieser Anleitung dokumentiert.

### Chromium-WebView-Link nach Upstream-Umbenennung

Das gepinnte Basemanifest verlinkt `external/chromium-webview/Android.mk`
auf `patches/Android.mk`. Der dort verwendete bewegliche `main`-Branch hat
diese Datei im Commit
[`091983e4`](https://github.com/LineageOS/android_external_chromium-webview_patches/commit/091983e4d4a41a61eebd3ca3c01d0bcc128ffb6b)
ohne Inhaltsänderung in `os_pickup.mk` umbenannt. Damit kann der alte Link
ins Leere zeigen und ckati mit `No such file or directory` abbrechen.

`build-hal.sh` prüft diesen Link nun vor dem Android-Build. Der Helfer
`tools/fix-webview-link.py` korrigiert ausschließlich den erwarteten kaputten
Manifest-Link auf `patches/os_pickup.mk`, wenn dessen Git-Blob-ID dem
geprüften Makefile `26ca6b19efe40f19362473d217596156075032b2` entspricht.
Ein funktionierendes altes Layout bleibt erhalten. Reguläre lokale Dateien,
abweichende Linkziele und ungeprüfte neue Makefile-Inhalte führen zum Abbruch.

Zum Fortsetzen die Port-Werkzeuge wie im Fortsetzungsblock oben mit
`git -C "$N100_PORT" pull --ff-only` aktualisieren und ausschließlich
`build-hal.sh` erneut ausführen. Kein erneutes Patchen oder vollständiger
`repo sync` ist nötig. Ein späterer Sync kann den alten Manifest-Link erneut
erzeugen; die Prüfung vor jedem Build behandelt auch diesen Fall.
Die Änderung repariert die Eingabestruktur. Der HAL-Build ist inzwischen
abgeschlossen; der Geräteboot bleibt ungetestet.

### Fehlende Produktmodule SimpleSettingsConfig und apns-conf.xml

Der bewegliche `lineage-20.0`-Stand von `vendor/lineage` verlangt inzwischen
`SimpleSettingsConfig` und `apns-conf.xml`. Die beiden Quellen fehlen im
älteren, gepinnten Hybris-Basemanifest. Die Paketprüfung bleibt aktiviert;
die Abhängigkeiten werden ergänzt.

`manifests/build-dependencies.xml` bindet separat diese geprüften Quellen ein:

| Pfad | Repository | Commit |
| --- | --- | --- |
| `packages/apps/SimpleSettingsConfig` | `LineageOS/android_packages_apps_SimpleSettingsConfig` | `f5620aa22a44ac3a9794d0fda5c146efd7e0a11e` |
| `vendor/apn` | `LineageOS/android_vendor_apn` | `cfcbd9d61d6c22e5b60b2f10274d6cd1ff460f5e` |

Der APN-Stand liegt direkt vor der Umstellung des Generators auf zusätzliche
Upstream-Eingaben am 20. Oktober 2026. Er enthält die vollständigen XML-Quellen
und den bisherigen Shell-/xmllint-Generator für die Android-13-Buildumgebung.

Für einen bereits vorbereiteten und gepatchten Baum im Ubuntu-Chroot:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:$PATH"
python3 "$N100_PORT/tools/n100.py" sync-build-deps --android-root "$ANDROID_ROOT"
bash "$N100_PORT/tools/build-hal.sh"
)
```

`sync-build-deps` ergänzt ein eigenes lokales Manifest, synchronisiert
ausschließlich die zwei genannten Projekte mit einem Job und prüft die
Commit-Stände. Andere Projekte und angewendete Hybris-Patches werden dabei
nicht synchronisiert. Vorhandene fremde Commit-Stände, lokale Änderungen,
Verzeichnissymlinks oder Änderungen am Zusatzmanifest stoppen den Vorgang.
Ein erneuter vollständiger Sync oder eine erneute Patch-Anwendung ist unnötig.

Neue Quellinitialisierungen nehmen dieses Zusatzmanifest bereits vor dem ersten
Sync auf. Der HAL-Build prüft, dass Manifest und beide Commit-Stände vorliegen.
Das behebt die nachgewiesenen fehlenden Produktmodule. Der HAL-Build ist
inzwischen abgeschlossen; Mobilfunkfunktion und Geräteboot bleiben ungetestet.

### Fehlendes ccache und billie2-Mountpoint-Fixup

Das erste Ninja-Log erreichte 28.446 geplante Schritte. Es scheiterte an zwei
unabhängigen Eingaben: dem fehlenden `/usr/bin/ccache` sowie der noch fehlenden
`billie2`-Zuordnung in Hybris. Die Kconfig-Fehler traten zusammen mit den
fehlgeschlagenen Compiler-Aufrufen auf; sie sind noch kein Nachweis eines
fehlerhaften Kernel-Konfigurationsfragments.

Installiere `ccache` innerhalb des Ubuntu-Chroots:

```bash
sudo apt-get update
sudo apt-get install -y ccache
```

`build-hal.sh` prüft den konfigurierten Cache-Aufruf vor dem Build und führt
`n100.py prepare-boot` mit `N100_DEVICE_REPORT` aus. Der Befehl liest den
vorhandenen Stock-Bericht, prüft Gerätegeneration und Hybris-Quellstand und
erzeugt `$ANDROID_ROOT/hybris/fixup-mountpoints`. Das gepinnte Hybris-Makefile
bevorzugt diesen lokalen Geschwisterpfad gegenüber dem eingebauten Fixup.

Der generierte Helfer ersetzt ausschließlich die im Bericht belegten
physischen By-name-Aliase, etwa `/dev/block/bootdevice/by-name/userdata` durch
`/dev/sda16` beim vorhandenen BE2013-Bericht. Im initramfs liefert devtmpfs die
kurzen Gerätenamen. Es werden keine `dm-*`-Nummern oder unsuffigierten
Boot-Aliase aus dem aktiven Slot abgeleitet. Ein lokales Audit unter
`.n100-boot-inputs.json` hält Berichtshash, Zuordnungen und Skripthash fest.
Abweichende vorhandene Fixup-Dateien werden nicht überschrieben.

Nach Aktualisierung der Port-Werkzeuge genügt ein erneuter Aufruf von
`build-hal.sh`; kein neuer ADB-Bericht, vollständiger Sync oder erneutes
Patchen ist erforderlich. Dies repariert die Build-Eingaben für die
experimentelle unverschlüsselte Rootfs-Variante. Es entschlüsselt bestehende
Android-Nutzerdaten nicht und implementiert keine dynamischen HAL-Mounts.

Die ebenfalls protokollierten doppelten Installationsregeln für Fingerabdruck,
Display und weitere Vendor-Dateien sind noch zu prüfen. Sie werden nicht als
behoben oder als Laufzeitkompatibilitätsnachweis gewertet. Der HAL-Build ist
inzwischen abgeschlossen; ein Geräteboot steht noch aus.

## 4. RPMs und Rootfs bauen — PLATFORM_SDK

Nach erfolgreichem HAL-Build das Ubuntu-Chroot mit genau einmal `exit`
verlassen. Zurück im Platform SDK zuerst Umgebung und vorhandene Build-Tools
prüfen:

```bash
source "$HOME/n100-build/build.env"
cat /etc/os-release
sdk-assistant list
```

Hier muss Sailfish OS erscheinen. Benötigt werden ein Tooling
`SailfishOS-VERSION` und das Gerätetarget `oneplus-billie2-aarch64`.
Bereits vorhandene passende Build-Tools weiterverwenden. Andernfalls zuerst
Tooling und Target nach Abschnitt 2 installieren und die tatsächliche Version
ermitteln. Die Version des Platform-Chroots allein belegt kein installiertes
Gerätetarget.

`RELEASE` muss der wirklich installierten Target-Version entsprechen:

```bash
source "$HOME/n100-build/build.env"
export RELEASE=DEINE_TATSAECHLICHE_TARGET_VERSION
bash "$N100_PORT/tools/build-packages.sh" all
```

### Python-2-Abbruch im HAL-RPM-Build

Tooling `SailfishOS-5.1.0.11` und Target `oneplus-billie2-aarch64` wurden
auf dem Buildhost erfolgreich angelegt. Der erste HAL-RPM-Build erreichte die
Buildphase und scheiterte beim direkten Aufruf von
`build/make/tools/fs_config/fs_config_generator.py aidarray`, weil die
Tooling-Umgebung kein `python2` enthält. Die dort ausgewählte Android-13-Datei
enthält echte Python-2-Syntax; ein reiner Shebang-Wechsel genügt nicht.

Vor dem HAL-RPM-Build führt der Wrapper jetzt `n100.py prepare-rpm` aus.
Dieser installiert die geprüfte offizielle Python-3-Portierung dieser
einzelnen Datei. Herkunft, Lizenz, Original-/Ziel-Blob-IDs und die 16
Upstream-Tests stehen in
[compat/fs_config_generator](../compat/fs_config_generator/README.md).
Die Android-ID-Header bleiben unverändert. Abweichende lokale Änderungen
werden nicht überschrieben. Das lokale Audit heißt `.n100-rpm-inputs.json`.

Im vorhandenen **Platform SDK** fortsetzen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" hal
)
```

Tooling und Target nicht erneut anlegen. Der später gemeldete erfolgreiche
HAL-RPM-Abschluss ist im folgenden Fortsetzungsabschnitt dokumentiert.

### RPM-Mountpoint-Aufruf auf den Geräte-Override umstellen

Nach der Python-3-Korrektur baute der erste HAL-RPM-Lauf die lokalen Werkzeuge
und erzeugte sieben Mount-Units. Anschließend rief das gepinnte
`droid-hal-device.inc` fest `hybris/hybris-boot/fixup-mountpoints` auf.
Dieser Aufruf berücksichtigt den Geschwister-Override
`hybris/fixup-mountpoints` nicht, den der Android-HAL-Build bereits verwendet.

Der HAL-RPM-Wrapper führt deshalb nun zusätzlich
`n100.py prepare-rpm-mounts` aus. Er prüft den gespeicherten Gerätebericht
erneut und erzeugt zusätzlich `hybris/fixup-rpm-mountpoints`. Dieser versteht
auch die vom Mountgenerator ausgegebenen `/dev/disk/by-partlabel/`-Pfade.
Der Boot-Override bleibt unverändert. Genau die Aufrufstelle im gepinnten
RPM-Include wird auf den neuen RPM-Override umgestellt. Der ursprüngliche Git-Blob muss
`7717982261ad321df173e489145a2f58126691c6` entsprechen; ein bereits
unverändert installierter Patch ist ebenfalls zulässig. Andere lokale
Änderungen werden nicht überschrieben. Das Audit heißt
`.n100-rpm-mounts.json`.

Zum Fortsetzen denselben Aktualisierungs-/HAL-RPM-Block wie oben verwenden.
Das Geräte-Spec und sein vorbereiteter Snapshot müssen nicht neu angelegt
werden; weder ein vollständiger Sync noch ein erneuter Android-HAL-Build
ist erforderlich.

Der Fixup ersetzt nur die im Bericht belegten physischen Aliase. Die fstab
verwendet für `modem`, `dsp` und `bluetooth` weiterhin Androids
`slotselect`-Semantik. Es wird keine feste Auswahl aus dem damals aktiven
Slot abgeleitet. Eine korrekte A/B-Auswahl zur Laufzeit bleibt zusammen mit
dynamischen HAL-Mounts und der udev-Konvertierung in
[BRINGUP.md](BRINGUP.md) offen. Ein erfolgreicher RPM-Build ist kein
Nachweis dieser Laufzeitfunktionen.

### Ungepackte Android-Root-Symlinks im HAL-RPM

Der nächste Build erreichte nach der Mountpoint-Korrektur die RPM-Dateiprüfung.
Der fatale Fehler waren genau vier mitkopierte Android-Root-Symlinks:

| Pfad | Gemeldetes Linkziel |
| --- | --- |
| `/bugreports` | `/data/user_de/0/com.android.shell/files/bugreports` |
| `/cache` | `/data/cache` |
| `/d` | `/sys/kernel/debug` |
| `/sdcard` | `/storage/self/primary` |

Das Geräte-Spec nutzt jetzt den vorhandenen Upstream-Hook
`custom_install_cmds`, um ausschließlich diese vier bekannten Links aus dem
RPM-Buildroot zu entfernen. Der Helfer prüft zuerst alle Linktypen und Ziele.
Bei abweichenden Dateien oder Zielen bricht er ab, ohne zuvor Links zu löschen.
Er folgt keinen Linkzielen und verändert weder Android-Buildausgaben noch
Telefondateien. Eine erneute Ausführung auf dem bereinigten Buildroot ist erlaubt.
Die RPM-Prüfung auf ungepackte Dateien bleibt eingeschaltet.

`n100.py prepare-rpm-spec` aktualisiert den bestehenden, eigenen RPM-Snapshot
vor dem Build. Er akzeptiert nur den exakt geprüften alten Spec-Stand
`b6184db55338fa680556ad2a4db65a30b33d15d5` mit passendem Snapshot-Marker
oder den bereits unverändert installierten neuen Stand. Lokale Änderungen
werden nicht überschrieben. Der `rpm/dhd`-Checkout und sein Mountpoint-Fixup
bleiben erhalten. Neu vorbereitete Bäume enthalten den Hook bereits.

Die absoluten Symlinks für die APEX-Linker und die udev-Regel wurden im Log
als Warnungen gemeldet; sie bleiben erhalten. Ihre Laufzeitfunktion ist
weiter Teil der Boot-/APEX-Integration. Mit dem üblichen Aktualisierungsblock
und `build-packages.sh hal` fortsetzen; die Snapshot-Anpassung läuft automatisch.

### Nach erfolgreichem HAL-RPM: Gerätekonfiguration bauen

Der Buildhost meldete am 25. Oktober 2026 mit Port-Commit `2ced030` den
erfolgreichen Abschluss von `droid-hal-billie2`. Der
[Nachweis](../validation/hal-rpm-build-user-20261025.json) beruht auf der
Terminalausgabe; RPM-Dateien und ihre Prüfsummen wurden nicht hochgeladen.
`hal_rpm_built` ist deshalb jetzt `true`. `rpm_built` bezeichnet den gesamten
Adaptationspaketsatz und bleibt bis zum Abschluss der weiteren Stufen `false`.

Im vorhandenen **Platform SDK** als Nächstes nur die Gerätekonfiguration bauen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" configs
)
```

Dieser Schritt baut die Community-Anpassung, sofern im Target benötigt, sowie
die `droid-config-billie2`-Pakete und bereitet die Kickstarts vor.
Die Pakete liegen unter `$ANDROID_ROOT/droid-local-repo/billie2`; das
Konfigurationslog liegt unter `$ANDROID_ROOT/hybris/droid-configs.log`.
Danach folgen `middleware`, `media`, `version` und zuletzt `rootfs`.
Ein erneuter Android-HAL- oder HAL-RPM-Build ist für diesen Übergang nicht nötig.
Geräteboot und Image-Kompatibilität sind weiterhin ungeprüft.

### Nach erfolgreicher Gerätekonfiguration: Middleware bauen

Mit Port-Commit `52a2dc6` wurden zwölf Gerätekonfigurations-RPMs geschrieben
und `Jolla-@RELEASE@-billie2-@ARCH@.ks` erzeugt. Die Terminalausgabe bestätigt
auch den erfolgreichen Community-Adaptation-Build und die Installation von
`droid-config-billie2-bluez5` im SDK-Target. Das hochgeladene
`droid-configs.log` endet mit erfolgreicher Bereinigung; der
[Buildnachweis](../validation/configs-rpm-build-user-20261025.json) hält
Log-Hash, Paketnamen und verbleibende Warnungen fest.

Die fehlenden geräteeigenen Keymap-/Attachment-Overrides brachen den Build
nicht ab: Upstream-Standarddateien wurden zuvor installiert. Doppelte
Dateilisteneinträge und absolute Symlinks sind als Warnungen protokolliert.
Der Paketbau bestätigt ihre Funktion auf dem Telefon noch nicht.

Im selben **Platform SDK** fortsetzen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" middleware
)
```

Bei `Build …? [Y/n/all]` mit `a` bestätigen, um alle Middleware-Pakete
dieses Schritts zu bauen. Der Upstream-Helfer nutzt vorhandene Quellen unter
`external` und lädt fehlende Middleware-Quellen nach. Deren tatsächliche
Revisionen und Build-Kompatibilität sind beim ersten Lauf noch zu erfassen.
Logs liegen für vorhandene externe Quellen unter
`$ANDROID_ROOT/external/PAKETNAME.log`, sonst unter
`$ANDROID_ROOT/hybris/mw/PAKETNAME.log`. Bei einem Abbruch das zuletzt
genannte Paketlog zusammen mit der Terminalausgabe prüfen.
Nach erfolgreicher Middleware folgen `media`, `version` und `rootfs`.

### Nach erfolgreicher Middleware: Medienpakete bauen

Mit Port-Commit `b78de82` meldete der Buildhost den erfolgreichen Build von
`libhybris`, `pulseaudio-modules-droid`, `pulseaudio-modules-droid-hidl`,
`mce-plugin-libhybris` und `qt5-qpa-hwcomposer-plugin`.
Der [Buildnachweis](../validation/middleware-rpm-build-user-20261025.json)
beruht auf der Terminalausgabe; Paketlogs, RPMs und tatsächlich verwendete
Middleware-Revisionen wurden nicht übermittelt. Der Build belegt noch keine
Grafik- oder Audiofunktion auf dem Gerät.

Vor dem Medien-RPM müssen die Android-Medienbibliotheken gebaut sein.
Der frühere HAL-Helfer rief nur `mka hybris-hal` auf. Mit diesem Stand konnte
der RPM-Packer `libdroidmedia.so` noch nicht finden und brach mit
`Please build droidmedia as per HADK instructions` ab.
Die offizielle [HADK-Buildfolge](https://docs.sailfishos.org/Develop/HADK/)
baut dafür zusätzlich das eigenständige Ziel `droidmedia`.

`build-hal.sh` baut jetzt standardmäßig `hybris-hal droidmedia`.
Für einen bereits erfolgreich gebauten HAL reicht der neue Modus `media`.
Er wählt denselben Gerätebaum und baut ausschließlich `droidmedia`.
Die Prüfung vor dem RPM-Packer verlangt alle vier benötigten Dateien mit
AArch64-ELF64-Headern: `libdroidmedia.so`, `libminisf.so`,
`minimediaservice` und `minisfservice`. Dies ist eine Strukturprüfung, kein
Funktionsnachweis. Abweichende oder teilweise fehlende Ausgaben stoppen den
RPM-Schritt, bevor der ältere Packer Dateien kopiert.

Im vorhandenen **Platform SDK** zuerst aktualisieren und das Ubuntu-Chroot betreten:

```bash
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

Erst am neuen Prompt **im Ubuntu-Chroot** ausführen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:$PATH"
bash "$N100_PORT/tools/build-hal.sh" media
)
```

Das Android-Buildlog liegt unter
`$ANDROID_ROOT/n100-build-logs/droidmedia.log`. Bei einem Fehler im
Ubuntu-Chroot bleiben und diesen Buildfehler zuerst beheben.
Nur nach erfolgreichem Abschluss das Ubuntu-Chroot mit genau einmal `exit`
verlassen und **zurück im Platform SDK** fortsetzen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" media
)
```

Ein neuer Sync, erneutes Anwenden der Hybris-Patches oder Wiederholen der
bereits erfolgreichen HAL-/Config-/Middleware-RPMs ist für diesen Schritt
nicht erforderlich.
Der anschließende Android-Build mit Port-Commit `118e31e` wurde auf dem
Buildhost erfolgreich abgeschlossen (2:17 Minuten). Die Strukturprüfung
bestätigte AArch64-ELF64-Header für alle vier Dateien;
[gemeldeter Buildnachweis](../validation/android-droidmedia-build-user-20261025.json).
Der Android-Schritt ist damit erledigt. Aus diesem Ubuntu-Chroot mit einmal
`exit` ins Platform SDK zurückkehren und den oben gezeigten
`build-packages.sh media`-Aufruf ausführen. Der anschließende erfolgreiche Medien-RPM-Build ist im nächsten Abschnitt dokumentiert.

Die aktuellen Paketgruppen verlangen `gstreamer1.0-droid`, daher bereitet der
Upstream-Helfer `droidmedia-localbuild` aus `external/droidmedia` vor und baut
dessen RPM. Das Log liegt unter
`$ANDROID_ROOT/hybris/mw/droidmedia-localbuild.log`.
Da `pulseaudio-modules-droid-hidl` gewählt ist, überspringt er anschließend
`audioflingerglue` und `pulseaudio-modules-droid-glue`; diese Meldung ist
für diese Paketwahl vorgesehen. Nach erfolgreichem Medien-Build folgen
`version` und zuletzt `rootfs`.

### Nach erfolgreichem Medien-RPM: Versionspaket bauen

Der Buildhost meldete den erfolgreichen Bau von `droidmedia-localbuild`
mit `rpm/droidmedia.spec` aus dem Archiv `droidmedia-0.20260902.0`.
Die vier Android-Binärdateien und die Init-Dateien wurden in das Quellarchiv
aufgenommen. Das Überspringen der beiden Audio-Glue-Pakete entspricht der
gewählten HIDL-Konfiguration.
[Gemeldeter Buildnachweis](../validation/media-rpm-build-user-20261025.json).

Im vorhandenen **Platform SDK** das Versionspaket bauen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" version
)
```

Das Paket heißt `droid-hal-version-billie2`; das Buildlog liegt unter
`$ANDROID_ROOT/hybris/droid-hal-version-billie2.log`.
Nach dessen erfolgreichem Abschluss folgt `build-packages.sh rootfs`.
Der RPM-Gesamtstatus bleibt bis zum Abschluss des Versionspakets offen.
Geräteboot und Hardwarefunktionen sind weiterhin ungeprüft.

### Dateikonflikt beim Versionspaket: ofono-Konfiguration ersetzen

Der erste Versionspaket-Build löste seine Abhängigkeiten auf und plante
19 Installationen. Danach brach die Transaktion wegen genau eines
Dateikonflikts ab: `droid-config-billie2` und das bereits im SDK-Target
installierte `ofono-configs-binder-1.1.25-1.12.2.jolla` lieferten beide
`/etc/ofono/binder.conf`. Die abschließende Liste fehlender BuildRequires
war eine Folge der abgebrochenen Installation.

Die Datei kommt aus dem gepinnten gemeinsamen `droid-hal-configs`-Sparsebaum.
Das Gerätespec deklariert jetzt `Provides: ofono-configs` und
`Obsoletes: ofono-configs-binder`. Dadurch ersetzt die Gerätekonfiguration
das generische Konfigurationspaket über die normale RPM-Paketauflösung.
Der oFono-Daemon und das Binder-Plugin werden dadurch nicht ersetzt.
Die Dateikonflikt- und BuildRequires-Prüfungen bleiben aktiviert.

`n100.py prepare-config-spec` aktualisiert beim nächsten `configs`-Aufruf
ausschließlich das eigene Spec im vorhandenen Snapshot. Es akzeptiert den
geprüften Vorgänger-Blob `8ea1bd5ce17e05abac0cbb689f1946010d56ee6b` oder
den bereits passenden Stand und prüft alle übrigen Dateien und Symlinks
gegen den Snapshot-Marker. Lokale Änderungen stoppen den Vorgang.
Gemeinsame Upstream-Quellen und Sparse-Dateien bleiben erhalten.

Im **Platform SDK** Konfiguration neu bauen und danach das Versionspaket
erneut versuchen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" configs
bash "$N100_PORT/tools/build-packages.sh" version
)
```

Der Konfigurationsschritt entfernt über seinen vorhandenen Upstream-Helfer
alte Konfigurations-RPMs aus dem lokalen Paketverzeichnis und schreibt die
neuen Pakete. Der Versions-Build lässt mb2 die Abhängigkeiten erneut im
SDK-Target installieren. Die tatsächliche Target-Transaktion und der
Versionsabschluss stehen nach dieser Korrektur noch aus.
Kein neuer Android-HAL-, Middleware- oder Medien-Build ist erforderlich.

### Übrige Versions-Abhängigkeiten nach abgebrochenem Anbieterwechsel

Das zweite Versionslog bestätigt den erfolgreichen Konfigurations-Neubau,
die Installation von `droid-config-billie2-1-202610251521` und die
Dateikonfliktprüfung ohne Fehler. Der erste mb2-Installationsversuch hatte
jedoch zuvor den Anbieterwechsel von `ofono-configs-binder` (`meego`) zur
Gerätekonfiguration (`oneplus`) mit `c` abgebrochen. Der anschließende
Distributionsabgleich installierte nur sechs Pakete; acht BuildRequires
blieben offen. Die vorherigen Logs belegen verfügbare Anbieter für diese
Abhängigkeiten. Das ist kein Anlass, sie aus dem Versionsspec zu entfernen.

Im **Platform SDK** die fehlenden Fähigkeiten gezielt im bestehenden
`oneplus-billie2-aarch64`-Target installieren und danach den Build wiederholen:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11

sdk-assistant maintain oneplus-billie2-aarch64 \
  zypper --plus-repo "$ANDROID_ROOT/droid-local-repo/billie2" \
  install --allow-vendor-change --allow-unsigned-rpm \
  droid-config-preinit-plugins droid-config-pulseaudio-settings \
  droid-config-sailfish droid-hal-kernel hybris-libsensorfw-qt5 \
  pulseaudio-modules-droid qt5-qpa-hwcomposer-plugin qtscenegraph-adaptation

bash "$N100_PORT/tools/build-packages.sh" version
)
```

`--allow-vendor-change` gilt hier nur für diesen Installationsaufruf im
SDK-Target; es ändert keine globale Solver-Konfiguration. Die
[Zypper-Dokumentation](https://doc.opensuse.org/documentation/tumbleweed/zypper/)
beschreibt diese Option für den Anbieterwechsel installierter Pakete.
`--allow-unsigned-rpm` lässt die selbst gebauten lokalen RPMs zu.
Die Paketübersicht mit `y` bestätigen. Die Paket- und Dateikonfliktprüfung
bleibt aktiviert. Wegen `set -e` startet das Versionspaket erst, wenn die
Installation erfolgreich abgeschlossen wurde.

Das lokale Verzeichnis wird nur für diese Transaktion als zusätzliche Quelle
verwendet. Die acht Namen sind die fehlenden RPM-Fähigkeiten des gepinnten
Versions-Includes; etwa `hybris-libsensorfw-qt5` kann durch
`hybris-libsensorfw-qt5-binder` erfüllt werden. Die anschließende vollständige Installation und der erfolgreiche Versionsabschluss
sind im nächsten Abschnitt dokumentiert.

### Alle RPM-Stufen abgeschlossen: Sailfish-Rootfs bauen

Die gezielte Target-Installation spielte 14 Pakete ohne Dateikonflikt ein.
Anschließend meldete `droid-hal-version-billie2` einen erfolgreichen Build.
HAL-, Config-, Middleware-, Media- und Versionsstufe sind damit abgeschlossen;
`rpm_built` steht jetzt auf `true`.
[Gemeldeter Buildnachweis](../validation/version-rpm-build-user-20261025.json).
Die Signaturhinweise und die nicht ausführbaren systemd-Serviceaktionen
haben diese SDK-Installation und den Versionsbuild nicht verhindert.

Im gleichen **Platform SDK** jetzt das gerätespezifische Rootfs bauen:

```bash
(
set -eo pipefail
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
mkdir -p "$ANDROID_ROOT/n100-build-logs"
bash "$N100_PORT/tools/build-packages.sh" rootfs 2>&1 \
  | tee "$ANDROID_ROOT/n100-build-logs/rootfs.log"
)
```

Der Wrapper verlangt `mic` und `createrepo_c`. Der gepinnte Upstream-Helfer
erstellt das lokale RPM-Repository und ruft `sudo mic` mit dem vorbereiteten
billie2-Kickstart und `RELEASE=5.1.0.11` auf.
Ausgabe und Fehlermeldungen landen zusätzlich in
`$ANDROID_ROOT/n100-build-logs/rootfs.log`; `pipefail` erhält den Build-Fehlercode.
Bei einem Abbruch dieses Log prüfen und die Paketauflösung beheben.
Der erste echte Rootfs-Build und die Vollständigkeit seiner Paketauflösung
sind noch nicht bestätigt. Ein erfolgreicher RPM-Satz allein ist kein
Nachweis, dass das komplette Rootfs bereits gebaut werden kann.

### Rootfs installiert, Recovery-ZIP-Verpackung fehlgeschlagen

Der erste Rootfs-Lauf installierte alle 756 Pakete, führte die Post-Skripte
aus und schrieb `sfe-billie2-5.1.0.11.tar.bz2`. Erst danach scheiterte der
Upstream-`%pack`-Schritt an `mv update-binary`: Die Recovery-Anhänge
`update-binary`, `hybris-updater-script` und `hybris-updater-unpack.sh`
waren nicht enthalten. Die Meldungen über auf den ersten Boot verschobene
Oneshots sind im Log explizit als solche markiert.
[Auswertung](../validation/rootfs-packaging.json).

Für den hier gewählten Weg über ein separates `userdata.img` wird ein
Rootfs-Archiv benötigt. Die zwei geräteeigenen Kickstart-Overrides
`kickstart/pack_package-droid-updater` und `kickstart/attachment_hybris`
verwenden die bereits vorgesehenen Upstream-Hooks. Der neue Pack-Schritt
belässt das Archiv im MIC-Ausgabeordner, liest den vollständigen bzip2-Tar-
Stream zur Integritätsprüfung und schreibt eine Datei `.tar.bz2.sha256`.
Er erzeugt kein Recovery-ZIP. Als Anhang bleibt nur das gebaute Bootimage.
Paketinstallation, Post-Skripte und Konfiguration werden weiterhin ausgeführt.

`prepare-config-spec` aktualisiert auch den bereits vorhandenen Snapshot
mit dem ofono-Fix. Der geprüfte Spec-Blob `343bd7aadd5eae6a028fe94cc1fdd5d08d971058`
und der ältere Vorgänger `8ea1bd5ce17e05abac0cbb689f1946010d56ee6b` sind erlaubt.
Abweichende Sparse-Dateien, lokale Änderungen, unbekannte Marker oder
bereits vorhandene fremde Override-Dateien stoppen die Aktualisierung.

Im **Platform SDK** die Konfiguration neu bauen, damit ihr Kickstart die
Overrides enthält, und danach den Rootfs-Build wiederholen:

```bash
(
set -eo pipefail
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" configs
export EXTRA_NAME="-rootfs-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$ANDROID_ROOT/n100-build-logs"
bash "$N100_PORT/tools/build-packages.sh" rootfs 2>&1 \
  | tee "$ANDROID_ROOT/n100-build-logs/rootfs-retry$EXTRA_NAME.log"
)
```

Der UTC-Zeitstempel in `EXTRA_NAME` erzeugt einen neuen MIC-Ausgabeordner und
ein neues Archiv. Der bisherige Versuch bleibt erhalten. Die Paketdownloads
können aus dem vorhandenen MIC-Cache wiederverwendet werden.
Das abschließende `Rootfs archive ready:` nennt den tatsächlichen Archivpfad.
Die `.sha256`-Datei bestätigt die Archivintegrität; die anschließende
Image-Erstellung prüft zusätzlich Geräteidentität und AArch64-Header.

Das Versionspaket erzeugt `/etc/hw-release` als Link auf
`../usr/lib/hw-release`. Die Image-Prüfung kann diese und andere begrenzte
Metadaten-Linkketten nun innerhalb des Archivs lesen. Sie folgt dabei keinen
Pfaden auf dem Buildhost. Archivpfad-, Zyklus- und ELF-Prüfungen bleiben erhalten.
Rootfs-Verpackung, Image-Erstellung und Geräteboot gelten bis zu ihren
tatsächlichen Abschlüssen weiterhin als unbestätigt.

Das Ergebnis des Rootfs-Schritts ist ein `sfe-billie2-*.tar.bz2`. Die
Paketauflösung und weitere Middleware-Anpassungen sind noch nicht erprobt.
Das Rootfs muss bereits die billie2-Anpassung und die AArch64-Laufzeit enthalten;
ein generisches Sailfish-Archiv genügt nicht.

## 5. Experimentelle Image-Dateien erzeugen

Der neue MIC-Lauf meldete am 25. Oktober 2026 um 15:58:53 UTC `Finished`
und listete das Rootfs-Archiv, seine `.sha256`-Datei, das angehängte Bootimage
und die Paket-/URL-Liste. [Buildnachweis](../validation/rootfs-build-user-20261025.json).
Die Archive und Hashwerte wurden noch nicht zur unabhängigen Prüfung übermittelt.

Das **Platform SDK mit einmal `exit` verlassen**, sodass wieder die
normale CachyOS-Shell aktiv ist. Anschließend auf dem **CachyOS-Host**:

```bash
(
set -eo pipefail
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
sudo pacman -S --needed e2fsprogs
rootfs_dir="$ANDROID_ROOT/SailfishOScommunity-release-5.1.0.11-billie2-rootfs-20261025T155521Z"
archive="sfe-billie2-5.1.0.11-rootfs-20261025T155521Z.tar.bz2"
(cd "$rootfs_dir" && sha256sum -c "$archive.sha256")
stamp=$(date -u +%Y%m%dT%H%M%SZ)
image_dir="$HOME/n100-build/images/billie2-$stamp"
sudo python3 "$N100_PORT/tools/make-userdata-image.py" \
  --rootfs "$rootfs_dir/$archive" \
  --boot "$rootfs_dir/hybris-boot.img" \
  --output-dir "$image_dir" --size-gib 8 2>&1 \
  | tee "$HOME/n100-build/image-build-$stamp.log"
printf 'Image-Verzeichnis: %s\n' "$image_dir"
)
```

Die Prüfsumme wird im Archivordner geprüft. Das Bootimage stammt aus
demselben MIC-Ausgabeordner wie das Rootfs. Ein neuer UTC-Zeitstempel sorgt
für ein neues Image-Verzeichnis. Ausgabe und Fehler werden zusätzlich in
`~/n100-build/image-build-ZEITSTEMPEL.log` gespeichert. Bei erfolgreichem
Abschluss das erzeugte `image-manifest.json` für die nächste Prüfung verwenden.

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
behauptet. Das erste echte Sailfish-Userdata-Image wurde auf dem Buildhost erstellt und
mit `e2fsck` geprüft. Der Image-Build wurde erfolgreich abgeschlossen;
[Manifest und Nachweis](../validation/image-build-user-20261025.json).

### Erster echter Image-Build abgeschlossen

Der Host meldete den erfolgreichen Abschluss unter
`~/n100-build/images/billie2-20261025T160318Z`.
Die Rootfs-Prüfsumme war `OK`, und e2fsck lief durch alle fünf Prüfdurchgänge.
Das hochgeladene Manifest ist
[unverändert gespeichert](../validation/image-manifest-user-20261025.json).
Die Image-Binärdateien wurden nicht hochgeladen oder unabhängig nachgehasht.

| Datei | Größe | SHA-256 |
| --- | ---: | --- |
| `userdata.img` | 8589934592 Byte | `3c7bc615c566ccd3634dd829bdea41a2df32aa9419f360f7969a189aff6e3e3d` |
| `hybris-boot.img` | 45064192 Byte | `82c44b8f8a04133b9d4e259b20f1dcfb0a07007938485c9f990beeb5fbc79fd5` |

Die Größen und Header sind im Manifest konsistent. Der nächste Schritt
ist die gezielte Bootloader-Abfrage in
[BRINGUP.md](BRINGUP.md#read-bootloader-variables-before-any-boot-experiment).
Ein erfolgreicher Dateisystembau bestätigt noch keinen Geräteboot oder
die Bereitstellung der Android-HAL-Partitionen.

## 6. Vor dem ersten Aufspielen noch erforderlich

Das Erzeugen der Dateien ist kein Nachweis ihrer Gerätekompatibilität.
Die [Fastboot-Abfrage](../validation/fastboot-preflight-user-20261025.json)
bestätigt inzwischen 96 MiB pro Boot-Slot, 24 MiB pro DTBO-Slot,
48.062.869.504 Byte für userdata sowie den beobachteten aktiven Slot B.
Bootimage und Userdata-Image passen hinsichtlich ihrer Dateigröße.
Vor Flash-Befehlen müssen weiterhin Stock-Bootheader und DTBO, AVB,
Wiederherstellung, eine passende Übertragungsstrategie sowie die Bereitstellung
der Android-HAL-Partitionen geklärt sein. Der Bootloader ist laut bisherigem
Bericht gesperrt; sein Entsperren löscht Nutzerdaten. Ein geprüftes Backup und
ein passender Wiederherstellungsweg werden dafür benötigt.

Die fehlende dynamische Mount-/HAL-Integration ist in [BRINGUP.md](BRINGUP.md)
aufgeführt. `userdata.img` löst ausschließlich die Bereitstellung des Linux-
Rootfs. Es stellt keine System-, Vendor- oder ODM-Partitionen bereit. Deshalb
bleibt `flashable_release` auch bei erfolgreicher Dateierstellung `false`, bis
die vollständige Kette auf dem Gerät validiert ist.
