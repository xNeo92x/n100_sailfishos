# N100: fehlende Android-Dienste bauen und paketieren

Der [V2-Gerätebericht](../validation/runtime-apex-v2-report-user-20261026.json)
bestätigt den Android-Linker und den direkten statischen `linkerconfig --help`.
Alle Abhängigkeiten von `servicemanager` werden aufgelöst; Cleanup ist erfolgreich.
Das native Sailfish-`/usr/bin/getprop` ist vorhanden. Ein fehlendes Android-getprop
ist deshalb kein Grund, das gesamte Android-System zu bauen.

`hwservicemanager` samt Init-Datei fehlt dagegen auch in den untersuchten
Build-Ausgaben für system, system_ext, vendor und product. `logd` fehlt in der
system/bin-Liste. Der gepinnte [hybris-boot-Stand](https://github.com/mer-hybris/hybris-boot/blob/6547fd1c3aeb9c987271a01b298d2ae5d39be231/Android.mk)
fordert diese Dienste im `hybris-hal`-Ziel nicht ausdrücklich an.

## Build auf dem Nutzer-PC bestätigt

Der [Buildbericht vom 26.10.2026](../validation/runtime-service-build-report-user-20261026.json) bestätigt
AArch64-Dateien für hwservicemanager und logd, beide Init-Dateien, das unveränderte
Runtime-APEX und den statischen linkerconfig-Fallback. `passed=true`, keine
Prüffehler. `system/etc/linker.config.pb` fehlt weiterhin, wie für diesen
Buildabschnitt getrennt ausgewiesen. Der Bericht belegt noch keinen RPM-Neubau
oder gestartete Dienste. Weiter mit **Schritt 4** unten.

## Änderung

`build-hal.sh runtime` baut gezielt `hwservicemanager logd linkerconfig`. Der normale Gesamtbuild enthält dieselben zusätzlichen
Ziele. Der Runtime-Modus fordert keinen neuen Kernel oder Boot-Imagebau an.
Android kann dabei benötigte Bibliotheken und andere Abhängigkeiten nachbauen.

`check-hal-runtime.py` prüft die AArch64-ELF-Dateien, ihre Interpreter,
die statische linkerconfig-Datei, passende Init-Deklarationen und vorhandene
APEX-/Linker-Konfigurationsinputs. Der JSON-Bericht erfasst Größen und Hashes.
Das ist eine Buildprüfung, noch keine nachgewiesene Dienst-Ausführung.

Die HAL-RPM-Stufe stoppt vor dem Packer, wenn diese Inputs fehlen. Der neue
Installationshelfer ergänzt ausschließlich den RPM-Buildroot um die beiden
Dienste, ihre RC-Dateien, das Runtime-APEX und die beiden Bibliothekslisten in system/etc.
DHD kopiert bereits den system/bin- und lib64-Baum sowie den statischen
linkerconfig-Fallback aus dem flachen Runtime-APEX. Die RPM-Dateiliste wird
um die neuen APEX-/etc-Pfade ergänzt. Die RC-Dateien erfasst das bestehende Glob.

Die Migration akzeptiert nur die zwei geprüften Vorgänger-Snapshots und den
aktuellen Stand. Lokale Änderungen, fremde Helfer und Symlink-Eltern werden
nicht überschrieben. Die vorhandene Mount-Fixup-Anpassung in `rpm/dhd` bleibt
erhalten. APEX-Aktivierung, persistente Mounts und Systemstart sind weitere
Bring-up-Arbeit; der Paketbau aktiviert auf dem Telefon nichts.

Die fertige `system/etc/linker.config.pb` entsteht in Android 13 durch eine
eigene Make-Regel mit `conv_linker_config systemprovide` und den tatsächlich
bereitgestellten Bibliotheken. Der Bericht erfasst deren Vorhandensein separat.
Diese Generierung und die APEX-/Vendor-Namespace-Integration bleiben als
nächster Arbeitsabschnitt offen.

## 1. CachyOS: Platform SDK betreten

Der HTTP-Server auf Port 8081 kann mit Strg+C beendet werden. Telnet und das
Telefon im Debug-Modus offen lassen. In einer Host-Bash:

```bash
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
"$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot"
```

## 2. Im Platform SDK: Ubuntu-Buildumgebung betreten

```bash
source "$HOME/n100-build/build.env"
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

Bei den bekannten Mount-/DBus-Meldungen entscheidet `/etc/os-release`, ob der
Eintritt tatsächlich erfolgt ist. Den Android-Build nur in Ubuntu mit
`MERSDKUBU=1` starten; der Helfer prüft diese Umgebung.

## 3. In Ubuntu: Runtime-Dienste bauen

```bash
(
set -e
source "$HOME/n100-build/build.env"
bash "$N100_PORT/tools/build-hal.sh" runtime
)
```

Bei Erfolg entsteht `$ANDROID_ROOT/n100-build-logs/runtime-outputs.json`.
Die bisherige `/data`-Mountung auf dem Telefon bleibt unverändert.

## 4. Zurück im Platform SDK: HAL-RPM neu bauen

Ubuntu einmal mit `exit` verlassen. Anschließend im Platform SDK:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
bash "$N100_PORT/tools/build-packages.sh" hal
)
```

Den JSON-Buildbericht und die Abschlussausgabe des RPM-Baus bereitstellen.
Bei einem Fehler die Fehlermeldung bzw. `n100-build-logs/runtime.log` oder
`droid-hal-billie2.log` bereitstellen. Es wird jetzt weder userdata neu geflasht
noch das alte Rootfs-Image erneut installiert.

## RPM-Abbruch beim linkerconfig-Fallback

Der [RPM-Log vom 26.10.2026](../validation/hal-runtime-rpm-failure-user-20261026.log)
zeigt einen Abbruch in `%install`, bevor der neue Installationshelfer ausgeführt
wird: Der system/bin-Baum wurde bereits kopiert, anschließend scheitert die
statische linkerconfig-Fallback-Kopie. Das passt zu einem bereits vorhandenen
APEX-Symlink am Ziel. Dessen konkretes Linkziel wurde im Geräte-/Hostlog nicht
separat gesammelt; der Fehlermechanismus ist mit einem solchen Ziel reproduziert.

Die exakt geprüfte DHD-Kopierregel erhält `cp -a --remove-destination`, damit die
Zieldatei selbst ersetzt wird. Der Test prüft leere und vorhandene Symlinkziele
und erhält die außerhalb des Buildroots liegenden Zieldateien unverändert.
Der bestehende Mount-Fixup wird beibehalten. Das komplette Include wird nach
Normalisierung beider Anpassungen gegen seinen gepinnten Git-Blob geprüft;
der bisherige Audit-Marker wird nur bei exakt bekanntem Inhalt aktualisiert.

Nach `git pull --ff-only` im Platform SDK genügt die Wiederholung von Schritt 4.
Kein manuelles Löschen oder Anlegen von Buildroot-Verzeichnissen ist erforderlich.
Die vollständige Auswertung steht in
[rpm-linkerconfig-copy-fix.json](../validation/rpm-linkerconfig-copy-fix.json).

## Quellen und Grenzen

- [Android 13 hwservicemanager mit Init-Datei](https://android.googlesource.com/platform/system/hwservicemanager/+/android-13.0.0_r75/Android.bp).
- [Android 13 logd mit Init-Datei](https://android.googlesource.com/platform/system/logging/+/android-13.0.0_r75/logd/Android.bp).
- [Gepinnte DHD-Paketierung](https://github.com/mer-hybris/droid-hal-device/blob/775f6c05123ca7e58422df1fcfd51943eb56920f/droid-hal-device.inc).

Der neue Build und das neue RPM müssen auf dem Build-PC ausgeführt werden.
Host-Tests mit ELF-/Dateisystem-Fixtures prüfen ABI-Fehler, fehlende Inputs,
RC-Deklarationen, Schutz vor Symlinks, RPM-Dateilisten und Snapshot-Migration.
Sie ersetzen weder den echten Android-Compiler noch RPM- oder Gerätetests.

## HAL-RPM-Neubau bestätigt

Der Nutzer meldet den HAL-RPM-Neubau nach der linkerconfig-Kopierkorrektur als
erfolgreich; [Nachweis](../validation/runtime-service-rpm-build-user-20261026.json).
Weiter mit dem [RAM-Gerätetest aus dem neuen RPM](HAL_RPM_DEVICE_PROBE.md).
