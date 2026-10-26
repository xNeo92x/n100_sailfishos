# N100: Linker-Konfiguration auf dem PC erzeugen

Der [Nutzerbericht](../validation/linker-inputs-user-20261026.json) bestätigt das
VNDK-30-APEX (114.733.056 Bytes), alle vier Bibliothekslisten, ein AArch64-Beispiel,
die Linux-Host-Werkzeuge und das Basis-Protobuf. Die fertige
`system/etc/linker.config.pb` und die Namespaces wurden dabei noch nicht erzeugt.

Der nächste Schritt erzeugt eine **isolierte AArch64-HAL-Vorschau auf dem PC**.
Es gibt noch keine Installation oder neue Telefonanweisung. Die Vorschau ist
kein vollständiges Modell des Stock-Vendors und keine Freigabe für einen Systemstart.

## Ausführen: im Ubuntu HABUILD_SDK

Du bist nach `build-linker-inputs.sh` bereits in Ubuntu. Die beiden Befehlsblöcke
laufen auf dem PC, nicht in Telnet. Falls das Terminal geschlossen wurde, zuerst
auf CachyOS das Platform SDK betreten und darin Ubuntu:

```bash
# Auf CachyOS:
source "$HOME/n100-build/build.env"
"$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot"
```

```bash
# Im Platform SDK:
source "$HOME/n100-build/build.env"
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

Dann in Ubuntu:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
git -C "$N100_PORT" pull --ff-only

n100_preview="$HOME/n100-build/linker-preview-$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$N100_PORT/tools/generate-linker-preview.py" \
  --android-root "$ANDROID_ROOT" \
  --hal-bundle "$HOME/n100-build/runtime-rpm-probe-20261026" \
  --input-report "$ANDROID_ROOT/n100-build-logs/linker-inputs.json" \
  --output-dir "$n100_preview"
)
```

Am Ende steht der vollständige Pfad zu `report.json`. Diese Datei bereitstellen.
Bei einem Fehler die Meldung und, falls angelegt, ebenfalls `report.json`
bereitstellen. Ein bereits bestehendes Ausgabe-Verzeichnis wird abgelehnt;
der Zeitstempel erlaubt einen neuen Versuch ohne Überschreiben.

## Welche Daten verwendet werden

- Das exakt am Telefon getestete `hal-system.tar.gz` aus dem bestehenden RPM-Probe-Bundle.
  Archiv-SHA, Manifest, 260 Dateihashes und Symlinks müssen dem erfolgreichen
  [RPM-Gerätebericht](../validation/hal-rpm-device-report-user-20261026.json) entsprechen.
  Es werden keine RPM-Skripte ausgeführt; `rpm` wird in Ubuntu nicht benötigt.
- Das Runtime-APEX aus diesem Archiv sowie das jetzt gebaute VNDK-30-APEX.
  Paket- und Payload-Hashes werden mit den gespeicherten Berichten verglichen.
  `debugfs` liest Manifest, optionale APEX-Linker-Metadaten und VNDK-Listen ohne
  Schreibzugriff oder Mount. Die Payloads bleiben im Ausgabe-Verzeichnis erhalten.
- Die exakten geprüften Host-Werkzeuge. Der eingegebene Bericht muss der gespeicherten
  Datei bytegleich entsprechen. Unbekannte neu gebaute Eingaben werden nicht stillschweigend akzeptiert.

Die APEX-Ansicht enthält nur tatsächlich gelesene Metadaten und Marker für im
Payload vorhandene `bin`-/`lib`-/`lib64`-Verzeichnisse. Sie enthält keine
extrahierten APEX-Bibliotheken und ist kein ausführbares Chroot.
Das separat geschriebene `apex-info-list.xml` beschreibt das Host-Modell und ist
kein Nachweis einer Aktivierung durch apexd auf dem Telefon.
`vendor`, `odm`, `product` und `system_ext` sind hier leere Verzeichnisse.
Die öffentliche App-Bibliotheksliste ist für diese Vorschau ohne ART explizit leer.

## HAL-Profil und tatsächliche Systembibliotheken

Das allgemeine Android-Basis-Protobuf gehört zu einer vollständigen
Android-Installation. Seine mit `conv_linker_config print` gelesenen Anforderungen
werden im Bericht zum Vergleich gespeichert. Es wird nicht verändert oder als
fertige HAL-Konfiguration ausgegeben.

Das gerätespezifische [HAL-Profil](../adaptation/linkerconfig/system.json) benötigt
`libc.so`, `libdl.so` und `libm.so`, ohne eine Android-App-Laufzeit vorauszusetzen.
`conv_linker_config proto` übersetzt dieses Profil.
`conv_linker_config systemprovide` ergänzt die regulären `.so`-Dateien, die im
geprüften AArch64-RPM direkt unter `system/lib64` vorliegen. Native Bibliotheken,
die das Runtime-APEX laut seinem tatsächlichen Manifest bereitstellt, werden aus
dieser Provider-Liste ausgeschlossen, damit keine doppelten Provider entstehen.
Die zusätzliche Prüfung der ausgedruckten Protobuf-Felder verhindert eine
abweichende Provider-Liste. Das ist eine Vorschau und noch keine verbindliche
LLNDK-Exportliste für eine dauerhafte Installation.

Danach läuft das originale Host-`linkerconfig` mit `--root`, `--vndk 30` und
`--target`. Geprüft werden System-/Vendor-Sektionen, ausführbare Pfadzuordnungen,
die VNDK-30-Suchpfade, die Runtime-APEX-Konfiguration und versehentlich übernommene
Hostpfade. Die erzeugten Konfigurationen stehen samt Text und Hashes im Bericht.
Die vorhandenen Android-Build-Ausgaben und das RPM-Bundle werden nicht verändert.

## Normale Erzeugung und strenge Diagnose getrennt lesen

Zusätzlich läuft der gleiche Generator in einem eigenen Verzeichnis mit
`--strict`. **`passed=true` im Bericht bedeutet nur, dass die normale
Host-Vorschau erzeugt und strukturell geprüft wurde.** Das Ergebnis der strengen
Prüfung steht separat unter `strict_diagnostic.passed`. Seit der Korrektur nach dem
ersten Nutzerlauf zählen dabei auch Fehlerzeilen trotz Exitcode 0; das alte
`true` im Rohbericht ist falsch und in der Auswertung ausdrücklich korrigiert.

AOSP fordert auch aus optionalen Namespaces Bibliotheken an, die im kleinen
HAL-Build fehlen können; beispielsweise fordert der VNDK-Namespace explizit
`libneuralnetworks.so`. Ein solcher Fehler darf nicht als laufender Dienst oder
vollständige Vendor-Kompatibilität gewertet werden. Ein identifizierter
`not found: ... is required by ...`-Fehler bleibt mitsamt Exitcode und erster
fehlender Anforderung sichtbar. Die Diagnose ist nicht vollständig, weil jede
Sektion beim ersten Auflösungsfehler aufhören kann. Der Gesamtprozess muss dabei
keinen Fehlerstatus zurückgeben. Protokollierte Fehler verhindern dennoch ein
positives Ergebnis der strengen Diagnose. Andere Generatorfehler
führen zu `passed=false` und einem Fehlerstatus des Helfers. Core-Dumps der
Diagnose sind deaktiviert.

Auch bei erfolgreicher strenger Prüfung bleiben `ready_for_installation=false`
und `device_namespace_tested=false`. Vor dem nächsten Gerätetest sind die
konkreten erzeugten Dateien, die Stock-Vendor-Eingaben und die temporäre
APEX-Einbindung zu prüfen. Die bisher erfolgreiche `--list`-Prüfung mit explizitem
Bibliothekspfad ist noch kein Gerätetest dieser Namespaces.

## Primärquellen

- [AOSP: Host-Parameter und Erzeugungsablauf](https://android.googlesource.com/platform/system/linkerconfig/+/45d040fd8ba22330c0ca61d2b803bd4f6cea6b7b/main.cc)
- [AOSP: APEX-Manifeste und Aktivierungsmetadaten](https://android.googlesource.com/platform/system/linkerconfig/+/45d040fd8ba22330c0ca61d2b803bd4f6cea6b7b/modules/apex.cc)
- [AOSP: strenge Auflösung der Anforderungen](https://android.googlesource.com/platform/system/linkerconfig/+/45d040fd8ba22330c0ca61d2b803bd4f6cea6b7b/modules/section.cc)
- [AOSP: zusätzliche VNDK-Anforderung](https://android.googlesource.com/platform/system/linkerconfig/+/45d040fd8ba22330c0ca61d2b803bd4f6cea6b7b/contents/namespace/vndk.cc)
- [AOSP: proto/print/systemprovide](https://android.googlesource.com/platform/build/soong/+/android-13.0.0_r75/scripts/conv_linker_config.py)

## Ergebnis des ersten Nutzerlaufs

Die normale Erzeugung ist bestätigt. Die strenge Auflösung hat trotz Exitcode 0
fünf Fehler zu zwei Bibliotheken protokolliert. Weiter mit dem
[Bau der nativen Provider und automatischer neuer Vorschau](LINKER_PROVIDERS.md).
