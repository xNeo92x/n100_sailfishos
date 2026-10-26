# N100: VNDK-30 und Eingaben für reguläre Linker-Namespaces

Der [RPM-Gerätetest](../validation/hal-rpm-device-user-20261026.json) ist erfolgreich:
Alle Abhängigkeiten von servicemanager, hwservicemanager und logd werden mit den
Dateien aus dem neuen RPM aufgelöst. Die Dienst-Hauptfunktionen und systemd
wurden noch nicht gestartet. Die Tests verwenden explizite Bibliothekspfade;
`/linkerconfig/ld.config.txt` wird noch nicht erzeugt.

## Warum jetzt VNDK 30?

Der gespeicherte BE2013-Gerätebericht meldet `ro.vndk.version=30` und
`ro.treble.enabled=true`. Der Android-13-Generator lädt seine VNDK-Listen aus
`/apex/com.android.vndk.v30/etc/`. Das Runtime-APEX allein stellt diese
Snapshot-Bibliotheken nicht bereit. AOSP enthält das passende Modul
`com.android.vndk.v30`, das in system_ext installiert wird. Seine Quellen
`packages/modules/vndk` und `prebuilts/vndk/v30` sind bereits im verwendeten
Manifest enthalten.

Wir bauen dieses Paket und die Linux-Host-Werkzeuge für die spätere Erzeugung
der Namespaces. Hier werden noch keine Konfiguration generiert, keine RPMs
neu gebaut und keine Dateien auf dem Telefon installiert.

## Umfang des Buildhelfers

`build-linker-inputs.sh` läuft ausschließlich im Ubuntu HABUILD_SDK. Er wählt
billie2 und fordert `com.android.vndk.v30`, das Basis-Protobuf
`system_linker_config` sowie die Dateiziele der beiden Linux-Host-Werkzeuge
`linkerconfig` und `conv_linker_config` an. Kernel und Boot-Images werden nicht
als Ziele angefordert. Der aufgelöste Manifeststand wird separat protokolliert.

Der Prüfer:

- prüft Name und Version im APEX-Protobuf, ZIP-CRC und ext4-Payload;
- liest alle vier VNDK-30-Bibliothekslisten mit debugfs ohne `-w`;
- prüft ein vorhandenes 64-Bit-Bibliotheksbeispiel auf AArch64-ELF;
- erfasst Größen und SHA-256 von Paket, Payload, Listen, Bibliothek und Werkzeugen;
- prüft den x86_64-Host-Linker sowie die benötigten `--help`-Optionen der Host-Werkzeuge;
- erfasst das Basis-Protobuf und die Git-Revisionen der drei betreffenden Quellprojekte.

`system_linker_config` ist ein Basis-Protobuf. Die fertige Datei mit den tatsächlich
bereitgestellten Bibliotheken benötigt zusätzlich den `systemprovide`-Schritt.
APEX-Aktivierungsmetadaten und die gerätespezifischen Namespaces werden danach
mit den vollständigen Eingaben erstellt. Der Bericht behauptet deren Erzeugung
noch nicht und ersetzt keinen späteren Vendor-/HAL-Gerätetest.

## 1. In der Platform-SDK-Shell Ubuntu betreten

Im Terminal mit dem laufenden HTTP-Server zuerst Strg+C drücken. Dieses Terminal
kehrt in die Platform-SDK-Shell zurück. Dann:

```bash
source "$HOME/n100-build/build.env"
git -C "$N100_PORT" pull --ff-only
/usr/bin/ubu-chroot -r "$PLATFORM_SDK_ROOT/sdks/ubuntu"
```

Bei unklarer Shell vor dem Build `/etc/os-release` prüfen: jetzt muss Ubuntu
angezeigt werden und `MERSDKUBU=1` gesetzt sein.

## 2. In Ubuntu bauen

```bash
(
set -e
source "$HOME/n100-build/build.env"
export PATH="$HOME/.local/bin:/usr/sbin:/sbin:$PATH"
if ! command -v debugfs >/dev/null; then
    sudo apt-get install --no-install-recommends e2fsprogs
fi
bash "$N100_PORT/tools/build-linker-inputs.sh"
)
```

Nach Erfolg diese Datei bereitstellen:

```text
/home/eugen/n100-build/android/n100-build-logs/linker-inputs.json
```

Bei Buildfehlern die Terminalmeldung oder `n100-build-logs/linker-inputs.log`
bereitstellen. Bei einem Prüffehler wird ein Bericht mit `passed=false` geschrieben,
damit kein älterer Erfolgsbericht stehen bleibt. Das Telefon bleibt in seiner
bisherigen schreibgeschützten Debug-Sitzung; es sind jetzt keine Telnet-Kommandos nötig.

## Quellen und Grenzen

- [VNDK-Versionierung und APEX](https://source.android.com/docs/core/architecture/vndk).
- [Android-13-Modul com.android.vndk.v30](https://android.googlesource.com/platform/packages/modules/vndk/+/android-13.0.0_r75/apex/Android.bp).
- [Versionsabhängige Eingaben des Generators](https://android.googlesource.com/platform/system/linkerconfig/+/android-13.0.0_r75/generator/variableloader.cc).
- [Host-Optionen des Android-13-Generators](https://android.googlesource.com/platform/system/linkerconfig/+/android-13.0.0_r75/main.cc).

Die Host-Tests verwenden ein echtes ext4-Fixture mit APEX-ZIP, Bibliothekslisten
und AArch64-Header sowie simulierte Host-Werkzeug-Hilfsausgaben. Sie prüfen auch
Buildziele, falsche Umgebung, Fehlerrückgabe und das Ersetzen eines veralteten
Berichts. Der echte VNDK-/Host-Werkzeug-Build muss auf dem Nutzer-PC erfolgen.

## Ergebnis des Builds

Der [Nutzerbericht vom 26.10.2026](../validation/linker-inputs-build-user-20261026.json)
bestätigt diese Eingaben. Weiter mit der [Host-Vorschau](LINKER_HOST_PREVIEW.md).
