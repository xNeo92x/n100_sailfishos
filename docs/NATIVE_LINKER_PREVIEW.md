# N100: Linker-Konfiguration mit dem neuen nativen RPM erzeugen

Die [neue Geräteprobe](../validation/native-runtime-device-user-20261026.json)
ist erfolgreich: neues Runtime-Payload und alle fünf Kerndateien geprüft,
alle 260 regulären HAL-Dateien verifiziert, Hilfe und Abhängigkeitslisten von
servicemanager, hwservicemanager und logd mit Status 0, Cleanup ebenfalls 0.
Damit ist die neue libc für diesen begrenzten Probeumfang auf dem Gerät geprüft.
Die Dienste wurden nicht gestartet. `/linkerconfig/ld.config.txt` fehlt weiterhin.

Jetzt wird die Konfiguration auf dem PC mit genau diesem neuen Paket erzeugt.
Die frühere Vorschau war an das alte Paket gebunden. Der neue explizite Schalter
`--native-rpm` prüft zusätzlich den vollständigen neuen Gerätebericht und dessen
Profil. Alte und neue Bundles werden nicht verwechselt; historische Pins bleiben
unverändert. Der Datenbestand des neuen RAM-Probe-Bundles liegt bereits auf dem PC.

## SDK-Umgebungen betreten

Falls bereits Ubuntu mit `MERSDKUBU=1` läuft, direkt zum nächsten Abschnitt.
Ansonsten auf **CachyOS** zuerst:

```bash
source "$HOME/n100-build/build.env"
"$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot"
```

Dann **im Sailfish Platform SDK**:

```bash
source "$HOME/n100-build/build.env"
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

Der Prompt allein unterscheidet die Umgebungen nicht. In Ubuntu zeigt
`cat /etc/os-release` Ubuntu und `printf '%s\n' "$MERSDKUBU"` den Wert `1`.

## In Ubuntu ausführen

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
git -C "$N100_PORT" pull --ff-only

n100_preview="$HOME/n100-build/native-linker-preview-$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$N100_PORT/tools/generate-linker-preview.py" \
  --native-rpm \
  --android-root "$ANDROID_ROOT" \
  --hal-bundle "$HOME/n100-build/native-runtime-probe-20261026" \
  --input-report "$ANDROID_ROOT/n100-build-logs/linker-inputs.json" \
  --output-dir "$n100_preview"
)
```

Die ausgegebene **`report.json` bereitstellen**; bei einem Fehler die Meldung und,
falls angelegt, ebenfalls den Bericht. Ein Neubau ist dafür nicht erforderlich.
Die geprüften Host-Werkzeuge, VNDK-30 und das Basis-Protobuf werden wiederverwendet.
`debugfs` gehört zu e2fsprogs, `readelf` zu binutils in Ubuntu.

## Was der neue Bericht klärt

- Das neue Runtime-APEX muss bytegleich dem auf dem Telefon geprüften RPM
  entsprechen. Das aus ZIP und Payload gelesene Manifest muss dem nativen
  Buildbericht entsprechen; es wird nicht verändert.
- `readelf --dynamic --wide` liest die tatsächlichen `DT_NEEDED`-Einträge aller
  regulären ELF-Dateien aus dem verifizierten HAL-Baum. Die Android-Dateien
  werden nicht ausgeführt. Der Bericht enthält direkte Verbraucher von
  `libdexfile.so` und `libneuralnetworks.so` sowie die rekursiven Abhängigkeiten
  der drei Dienste innerhalb der regulären flachen AArch64-HAL-Bibliotheken.
- Nicht vorhandene flache Abhängigkeiten werden als solche aufgelistet. Dies ist
  kein Ersatz für Linker-Namespace-Auflösung. `dlopen`, interne APEX-ELFs und der
  Stock-Vendor werden in diesem Inventar ausdrücklich nicht untersucht.
- Normale Konfiguration und separate strenge Diagnose werden neu erzeugt.
  Fehler im Generatorprotokoll zählen auch bei Exitcode 0. Das bekannte
  NNAPI-Problem kann weiterhin als fehlende Anforderung erscheinen und wird
  nicht durch einen erfundenen Provider oder gelöschte Fehlermeldungen verdeckt.

Die Vorschau umfasst weiterhin Runtime-/VNDK-Metadaten und das native HAL-Profil;
die Stock-Vendor-/Produktverzeichnisse bleiben leer. Sie ist kein ausführbares
Chroot und wird nicht auf dem Telefon installiert. Auch ein positiver strenger
Befund wäre noch kein vollständiger Sailfish- oder Hardwaretest. Siehe auch
[Details und Primärquellen des Generators](LINKER_HOST_PREVIEW.md).
