# N100: fehlende GLESv3-Bibliothek für den Composer bauen

Die [V2-Vendor-Probe](../validation/vendor-v2-device-user-20261026.json) führt die
ELF-Erkennung erfolgreich aus. Alle drei System-Abhängigkeitslisten sowie der
Stock-Allocator und beide ausgewählten Sensordienste enden mit Status 0.
Der Display-Composer bricht wegen fehlendem `libGLESv3.so` ab. Die Audio-Datei
wurde als andere ABI/Format übersprungen und bleibt ungeprüft. Cleanup ist 0.
Kein Dienst-Hauptprozess oder vollständiger Hardwaretest wurde gestartet.

Die echte Android-Implementierung wird gezielt gebaut. Das Android-13-Buildmodul
`libGLESv3` verwendet `GLES2/gl2.cpp` und hängt von EGL ab. Der lokale Quellstand,
Dateihash und Git-HEAD werden protokolliert; die echte `cc_library_shared`-
Implementierung muss vorhanden sein. Als Primärvergleich dient die
[AOSP-Android-13-Moduldefinition](https://android.googlesource.com/platform/frameworks/native/+/refs/tags/android-13.0.0_r43/opengl/libs/Android.bp).
Es wird weder eine Dummy-Datei erzeugt noch ein Stock-Android-11-GLES-Wrapper
in den gebauten Android-13-Systemteil kopiert.

## SDK-Umgebungen

Falls bereits Ubuntu mit `MERSDKUBU=1` läuft, direkt zum Build-Abschnitt.
Andernfalls in einem neuen **CachyOS-Terminal**:

```bash
source "$HOME/n100-build/build.env"
"$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot"
```

Dann **im Sailfish Platform SDK**:

```bash
source "$HOME/n100-build/build.env"
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

`cat /etc/os-release` muss jetzt Ubuntu zeigen; `printf '%s\n' "$MERSDKUBU"`
muss `1` ausgeben. Der Prompt allein unterscheidet die Umgebungen nicht.

## In Ubuntu bauen und prüfen

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
git -C "$N100_PORT" pull --ff-only
bash "$N100_PORT/tools/build-graphics-runtime.sh"
)
```

Am Ende die ausgegebene **`report.json` bereitstellen**. Bei einem Buildfehler
die letzte Fehlermeldung bzw. `build.log` aus dem ausgegebenen
`graphics-runtime-*`-Verzeichnis bereitstellen. Falls die nachgelagerte Prüfung
fehlschlägt, kann auch ein Bericht mit `passed=false` vorliegen.
Der Helfer benötigt `readelf` aus binutils in Ubuntu.

## Prüfung und Ergebnisdateien

- Die bestehende native No-DEX-Anpassung wird mit dem bekannten idempotenten
  Helfer vorbereitet. `mka libGLESv3` fordert die reale Bibliothek und deren
  Build-Abhängigkeiten an. Kein Kernel-, Rootfs-, RPM- oder Voll-ART-Build ist als
  Ziel angefordert. Künftige normale HAL-/Runtime-Builds enthalten das Ziel ebenfalls.
- `system/lib64/libGLESv3.so` muss regulär, ELF64/AArch64, ET_DYN und ohne eigenen
  Interpreter sein. SONAME muss `libGLESv3.so` sein, und EGL muss als tatsächliche
  DT_NEEDED-Abhängigkeit erscheinen. Wichtige GLES-Exporte einschließlich
  `glGetStringi`, `glBindVertexArray` und `glDispatchCompute` müssen definiert sein.
- Die rekursive Bibliothekskette wird aus den realen installierten Ausgaben
  gelesen. Absolute Android-Symlinks werden innerhalb des Produktbaums aufgelöst;
  Hostpfade, falsche ABIs, unbekannte Abhängigkeiten und Symlink-Zyklen werden abgelehnt.
- Das Runtime-APEX muss weiter dem am Gerät geprüften Build entsprechen. In der
  Kette vorkommende Bionic-Bibliotheken müssen die getesteten Hashes behalten.
- `report.json` enthält ELF-/DT_NEEDED-Daten und Hashes, neue Bibliotheken sowie
  geänderte Bibliotheken gegenüber dem getesteten HAL-RPM. `graphics-runtime.tar.gz`
  enthält nur diese neuen/geänderten regulären Dateien mit Prüfsummenliste.
  Die noch fehlende Geräteprüfung wird ausdrücklich nicht als bestanden markiert.
- Das Archiv wird zunächst nur auf dem PC gespeichert. Nach der Berichtsauswertung
  folgt eine passend gepinnte RAM-Probe; noch keine Installation auf dem Telefon.

Die bisherigen Runtime-/Vendor-Bundles behalten ihre alten Pins. Die Prüfung
belegt keinen erfolgreichen Grafikaufruf und keine Display-Funktion.
Binder-Geräteknoten, Property-Initialisierung, NNAPI, Audio und vollständiger
Sailfish-/systemd-/HAL-Start bleiben eigene Aufgaben.
