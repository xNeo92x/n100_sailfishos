# N100: fehlende native Linker-Provider bauen

**Dieser Voll-ART-Bau ist nach dem TLS-Fehler gestoppt.** Aktueller nächster
Schritt: [native Runtime ohne DEX-Unwinding](NATIVE_RUNTIME.md). Die folgende
Schrittfolge dokumentiert den fehlgeschlagenen Ansatz und soll nicht erneut
ausgeführt werden.

Die erste [Host-Vorschau](../validation/linker-host-preview-user-20261026.json)
hat eine reguläre Konfiguration mit fünf Sektionen erzeugt. Die Hashes und Größen
der drei gespeicherten Ausgabedateien sind nachgerechnet. Der normale Generator
hat nichts ins Fehlerlog geschrieben.

**Die strenge Auflösung ist trotzdem fehlgeschlagen.** Im unverändert archivierten
[Rohbericht](../validation/linker-host-preview-report-user-20261026.json) steht
`strict_diagnostic.passed=true`, weil der erste Helfer nur den Exitcode berücksichtigte.
Der Generator hat Exitcode 0 geliefert und gleichzeitig fünf Fehler protokolliert:

| Bibliothek | Betroffene Sektionen |
| --- | --- |
| `libneuralnetworks.so` | system, vendor, unrestricted |
| `libdexfile.so` | isolated, com.android.runtime |

AOSP `BuildSection` protokolliert einen fehlgeschlagenen `Resolve`-Aufruf mit
`LOG(ERROR)` und gibt die Sektion anschließend zurück. Ein Exitcode 0 belegt hier
keine vollständige Auflösung. Der korrigierte Helfer wertet Exitcode, Fehlerzeilen
und konkrete fehlende Anforderungen gemeinsam aus. Eine fehlerfreie strenge
Ausgabe wird zusätzlich strukturell geprüft. Der Rohbericht wird nicht nachträglich
umgeschrieben; die [Auswertung](../validation/linker-host-preview-user-20261026.json)
enthält das korrigierte Ergebnis.

## Nächster Lauf: in Ubuntu auf dem PC

Nach dem letzten Build bist du bereits im Ubuntu HABUILD_SDK. Dort:

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
git -C "$N100_PORT" pull --ff-only
bash "$N100_PORT/tools/build-linker-providers.sh"
)
```

Der Helfer legt ein neues Verzeichnis `~/n100-build/linker-providers-ZEITSTEMPEL`
an. Er fordert diese vier APEX-Module an:

- `com.android.art`: exportiert `libdexfile.so` für die native Runtime-Abhängigkeit;
- `com.android.neuralnetworks`: exportiert `libneuralnetworks.so`;
- `com.android.i18n`: ICU-Bibliotheken für ART;
- `com.android.os.statsd`: unter anderem `libstatssocket.so` für NNAPI.

Die exakten nativen Exporte und weiteren Anforderungen werden aus den gebauten
Manifesten gelesen. Der ART-Build kann je nach Android-Konfiguration ausgewählte
Prebuilts verwenden; der aufgelöste Manifeststand wird deshalb mitgespeichert.
Dies ist kein Nachweis, dass jede Bibliothek in diesem Lauf aus C++ kompiliert wurde.
Ein erstmaliger Paketbau kann deutlich länger dauern als die acht Sekunden des
vorherigen Eingaben-Builds.

Danach liest der Prüfer jedes Payload mit `debugfs` ohne Mount oder Schreibzugriff,
prüft Paket-/Payload-Hashes und Manifest-Identität und liest die jeweilige native
Bibliothek direkt aus `lib64` im Paket. Geprüft werden AArch64, ELF64 und ET_DYN.
Es werden keine externen Link-Stubs als Ersatz kopiert. APEX und komprimierte
CAPEX mit `original_apex` werden unterstützt; widersprüchliche installierte
Varianten werden abgelehnt.

Anschließend erzeugt der Helfer automatisch eine neue Vorschau mit diesen vier
zusätzlichen Providern. Bereits geprüfte Runtime-, VNDK- und RPM-Eingaben bleiben
an ihre bisherigen Hashes gebunden. Native APEX-Exporte werden nicht zusätzlich
als System-Provider deklariert. Der Bericht enthält auch die genauen Provider-
Manifeste, Stichproben und den neuen strengen Auflösungsversuch.

**Die am Ende ausgegebene Datei `.../preview/report.json` bereitstellen.**
Bei einem früheren Fehler die Terminalmeldung und, falls vorhanden,
`.../provider-inputs/report.json` bzw. `.../build.log` bereitstellen. Es muss jetzt
kein älterer Test wiederholt und nichts am Telefon ausgeführt werden.

## Grenzen

Die vier Provider-Pakete sind für die native Bibliotheksauflösung vorgesehen.
Auf dem Telefon werden keine Dienst-Hauptfunktionen, ART, statsd oder systemd
gestartet und keine Pakete installiert. Ob die ergänzten Module alle
Anforderungen schließen, entscheidet der neue Bericht; weitere fehlende
Bibliotheken werden nicht unterdrückt oder durch leere Ersatzdateien ersetzt.

Auch ein fehlerfreier Host-Generator bestätigt noch keine vollständige ELF-
Abhängigkeitsauflösung oder Hardwarefunktion auf dem N100. Stock-Vendor-/ODM-
Metadaten, dauerhafte APEX-/Device-Mapper-Einbindung, der Übergang zu systemd und
die Hardwaretests bleiben offen. `ready_for_installation` bleibt deshalb false.

## Primärquellen

- [AOSP Resolve-Fehlerbehandlung](https://android.googlesource.com/platform/system/linkerconfig/+/45d040fd8ba22330c0ca61d2b803bd4f6cea6b7b/contents/section/sectionbuilder.cc)
- [ART-APEX und ICU-Anforderung](https://android.googlesource.com/platform/art/+/android-13.0.0_r75/build/apex/Android.bp)
- [libdexfile und ihre APEX-API](https://android.googlesource.com/platform/art/+/android-13.0.0_r75/libdexfile/Android.bp)
- [NNAPI-APEX](https://android.googlesource.com/platform/packages/modules/NeuralNetworks/+/android-13.0.0_r75/apex/Android.bp)
- [NNAPI-Statistikabhängigkeit](https://android.googlesource.com/platform/packages/modules/NeuralNetworks/+/android-13.0.0_r75/runtime/Android.bp)
- [ICU-APEX](https://android.googlesource.com/platform/packages/modules/RuntimeI18n/+/android-13.0.0_r75/apex/Android.bp)
- [StatsD-APEX](https://android.googlesource.com/platform/packages/modules/StatsD/+/android-13.0.0_r75/apex/Android.bp)
