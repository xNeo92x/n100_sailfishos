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
den nachgewiesenen Konfigurationsabbruch; ein vollständiger HAL-Build wurde
damit noch nicht nachgewiesen.

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
Die Änderung repariert die Eingabestruktur; ein vollständiger HAL-Build und
ein Geräteboot sind damit noch nicht bestätigt.

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
Das behebt die nachgewiesenen fehlenden Produktmodule; vollständiger HAL-Build,
Mobilfunkfunktion und Geräteboot müssen weiterhin validiert werden.

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
behoben oder als Laufzeitkompatibilitätsnachweis gewertet. Der vollständige
HAL-Build und ein Geräteboot sind weiter offen.

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
