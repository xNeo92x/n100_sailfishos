# N100: native Runtime ohne DEX-Unwinding

Der vollständige ART-Paketbau ist beim Generator `asm_defines.s` für ARM64 und ARM
abgebrochen: `art/runtime/thread-current-inl.h` kann den Ausdruck
`__get_tls()[TLS_SLOT_ART_THREAD_SELF]` nicht kompilieren. Der gespeicherte
[Buildbericht](../validation/art-provider-build-failure-user-20261026.json)
enthält die Belege. Die Auswahl eines vollständigen ART-Pakets war für den
aktuellen nativen HAL-Schritt zu weitgehend.

Wir ändern keine Bionic-TLS-Slots, um ART zu kompilieren. Auch der AOSP-Prebuilt-
Ausweg ist für den verwendeten Android-13-Stand nicht als vorhandenes ART-APEX
belegt: `prebuilts/runtime/mainline` enthält hier kein ART-Paket; das Module-SDK
ersetzt kein nachgewiesenes ART-Implementierungspaket.

Stattdessen wird die tatsächliche DEX-Abhängigkeit aus der nativen Diagnostik
entfernt. Die Android-13-Quellen enthalten bereits native Unwinder-Konfigurationen
für Vendor/Recovery ohne `DexFile.cpp`, `libdexfile_support` und `libdexfile`.
Dieselben Ausschlüsse werden auf die **Android-Varianten** von `libunwindstack`
und `libdebuggerd` angewendet. Host-Varianten bleiben unverändert. Native
Stackdiagnostik bleibt im Build, die Symbolisierung von Android-DEX-Frames entfällt.
Das ist eine explizite HAL-Featureentscheidung, keine leere Ersatzbibliothek und
kein manuelles Entfernen von Anforderungen aus einem fertigen APEX-Manifest.

## Auf dem PC in Ubuntu ausführen

Du bist nach dem fehlgeschlagenen Build weiter im Ubuntu HABUILD_SDK:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
git -C "$N100_PORT" pull --ff-only
bash "$N100_PORT/tools/build-native-runtime.sh"
)
```

Der Helfer schreibt den Pfad zum neuen Arbeitsverzeichnis und am Ende den Pfad
zu `report.json`. **Diese `report.json` bereitstellen.** Bei einem Buildfehler die
letzte Terminalmeldung und `build.log` aus dem ausgegebenen Verzeichnis senden.
Der Build setzt auf vorhandenen Ausgaben auf; kein `clean` ist erforderlich.

Der frühere `build-linker-providers.sh` stoppt jetzt mit einem Verweis auf diesen
Ablauf. Der bekannte Voll-ART/TLS-Fehler soll nicht erneut als normaler nächster
Schritt angefordert werden.

## Was der Helfer ändert und prüft

1. Nur zwei Android.bp-Module erhalten `target.android`-Ausschlüsse:
   `system/unwinding/libunwindstack/Android.bp: libunwindstack` und
   `system/core/debuggerd/Android.bp: libdebuggerd`.
   Der Helfer prüft beide Modulkontexte vor dem ersten Schreiben. Bei unbekannten
   bestehenden Android-Varianten bricht er ab. Eine Wiederholung oder das
   Fortsetzen einer unterbrochenen Zweidatei-Anpassung ist möglich.
2. Größe und SHA-256 von `bionic/libc/platform/bionic/tls_defines.h` werden vor und
   nach der Vorbereitung verglichen und im Audit gespeichert. Das Buildskript
   setzt keinen TLS-Slot auf einen geratenen Index zurück.
3. Angefordert werden `libunwindstack`, `crash_dump`, `libc_malloc_debug`,
   `com.android.runtime`, `hwservicemanager`, `logd` und `linkerconfig`.
   Ein vollständiges ART-Paket, Java-Framework, Kernel oder Userdata-Image wird
   nicht als Ziel angefordert.
4. Der neue Runtime-Container wird mit ZIP-CRC und ext4-Metadaten geprüft.
   **Steht weiterhin `libdexfile.so` in `requireNativeLibs`, gilt der Lauf als
   fehlgeschlagen.** Die Anforderungen werden nicht nachträglich umgeschrieben.
5. Die fünf zuvor am Gerät geprüften Kern-Dateien (beide Linker und drei
   AArch64-Bionic-Bibliotheken) werden aus dem neuen Payload gelesen. Der Bericht
   enthält neue Hashes und den Vergleich mit den bisherigen Referenzen.
   Ein abweichender Hash wird sichtbar und gilt nicht automatisch als Gerätetest.
6. Der Dienst-Ausgabenbericht ist im Ergebnis enthalten, einschließlich der
   Paket-/Executable-Hashes. Der aufgelöste Repo-Manifeststand wird separat gespeichert.

Das Quellaudit liegt in `$ANDROID_ROOT/.n100-native-unwind.json`. Die gepinnten
bisherigen Probe-Bundles werden nicht stillschweigend auf neue Dateien umgestellt.

## Noch offene Schritte

Dieser Lauf baut und prüft ausschließlich lokale Android-Ausgaben. Das HAL-RPM,
das Rootfs und die Dateien auf dem Telefon sind dadurch noch nicht aktualisiert.
Das neue Runtime-APEX benötigt eine erneute Prüfung und eine passende temporäre
Geräteprobe, bevor es dauerhaft verwendet wird. Dafür wird zuerst dieser Bericht
mit den tatsächlichen Hashes ausgewertet.

`libneuralnetworks.so` bleibt ein separater offener Punkt der strengen
Linker-Diagnose; dieser Helfer behauptet dessen Auflösung nicht. Die vier
APEX-Provider aus dem bisherigen Plan gelten nicht als gebaut oder validiert.
Stock-Vendor-/ODM-Einbindung, vollständiger Systemstart und Hardwaretests bleiben offen.

## Quellen

- [AOSP Android 13: libunwindstack und bestehende Ausschlüsse](https://android.googlesource.com/platform/system/unwinding/+/android-13.0.0_r75/libunwindstack/Android.bp)
- [AOSP Android 13: libdebuggerd und Recovery-Ausschlüsse](https://android.googlesource.com/platform/system/core/+/android-13.0.0_r75/debuggerd/Android.bp)
- [AOSP Android 13: Runtime-APEX](https://android.googlesource.com/platform/bionic/+/android-13.0.0_r75/apex/Android.bp)
- [Hybris-Grundprinzip: angepasste Android-TLS-Zugriffe](https://github.com/libhybris/libhybris/blob/master/README.md)
