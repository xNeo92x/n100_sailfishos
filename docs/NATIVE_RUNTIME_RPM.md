# N100: neues natives Runtime-RPM prüfen

Der [native Buildbericht](../validation/native-runtime-build-report-user-20261026.json)
bestätigt ein Runtime-APEX ohne `libdexfile.so` als Anforderung. Im Manifest bleibt
`liblog.so`. Die beiden Android.bp-Anpassungen und der dabei unveränderte
TLS-Header sind protokolliert. Das Vorbereitungs-Audit im Bericht enthält noch
`runtime_apex_built=false`, weil es vor dem Build erstellt wurde; das eigentliche
Build-/Payload-Prüfergebnis steht auf der obersten Ebene und ist erfolgreich.

Unter den fünf überprüften Kerndateien sind beide Linker, libdl und libm mit den
bisherigen Referenzen identisch. **libc.so hat einen neuen SHA-256-Hash.**
Damit gilt der bisherige erfolgreiche Gerätetest nicht automatisch für dieses
Payload. Die neue Paketprüfung akzeptiert die neue libc nur in einem ausdrücklich
an den gespeicherten Buildbericht gebundenen Eingaben-Bundle.

## 1. Ubuntu verlassen

Nach `build-native-runtime.sh` befindest du dich in Ubuntu. Dort einmal:

```bash
exit
```

Du kehrst in das Sailfish Platform SDK zurück. Zur Kontrolle:

```bash
cat /etc/os-release
```

Jetzt muss `NAME="Sailfish OS"` stehen. Der Prompt allein unterscheidet die
Umgebungen nicht. Falls die SDK-Shell geschlossen wurde, auf CachyOS erneut
`$PLATFORM_SDK_ROOT/sdks/sfossdk/sdk-chroot` ausführen, nachdem `build.env` geladen wurde.

## 2. Im Platform SDK das HAL-RPM bauen und prüfen

```bash
(
set -e
source "$HOME/n100-build/build.env"
export RELEASE=5.1.0.11
git -C "$N100_PORT" pull --ff-only

bash "$N100_PORT/tools/build-packages.sh" hal

n100_native_bundle="$HOME/n100-build/native-runtime-rpm-$(date -u +%Y%m%dT%H%M%SZ)"
python3 "$N100_PORT/tools/prepare-native-runtime-rpm.py" \
  --android-root "$ANDROID_ROOT" \
  --output-dir "$n100_native_bundle"
)
```

**Die am Ende genannte `manifest.json` bereitstellen.** Bei einem Build- oder
Prüffehler die letzte Terminalmeldung bzw. `droid-hal-billie2.log` bereitstellen.
Es ist kein erneuter Android-/Kernel-Build nötig. `rpm` und `rpm2cpio` werden aus
der Platform-SDK-Umgebung verwendet.

## Inhalt der Prüfung

- `rpm -qp` prüft die Identität `droid-hal-billie2` und Architektur `aarch64`.
  `rpm2cpio` liest begrenzt den Paketinhalt. RPM-Skripte werden nicht ausgeführt;
  Signaturen werden nicht als verifiziert ausgewiesen.
- Die Dateien aus dem neuen Dienst-Ausgabenbericht müssen bytegleich im RPM
  vorhanden sein: Dienste, RC-Dateien, Bibliothekslisten, statisches linkerconfig
  und das neue Runtime-APEX.
- Zusätzlich werden die drei von DHD nach `system/lib64` kopierten
  Bionic-Bibliotheken mit den neuen Payload-Referenzen verglichen. Eine alte
  `libc.so` neben dem neuen APEX wird abgelehnt.
- Der vollständige APEX-Hash und der per ZIP-CRC gelesene Payload-Hash müssen dem
  überprüften nativen Build entsprechen. Dafür ist in diesem Schritt kein
  erneutes debugfs-Auslesen im Platform SDK erforderlich.
- Der bestehende CPIO-Prüfer übernimmt Pfad-, Typ-, Größen-, CRC- und Hardlink-
  Kontrollen. Der AArch64-HAL-Baum wird als `hal-system.tar.gz` mit Dateihashes
  gespeichert; dazu kommt `apex_payload.img`. Bereits bestehende Ausgabeverzeichnisse
  werden abgelehnt. Die alten Bundle-Verzeichnisse und ihre alten Pins bleiben gültig
  für ihre historischen Tests und werden nicht umgeschrieben.

DHD entfernt ältere Haupt-RPMs beim Neubau. Falls dennoch mehrere Kandidaten im
lokalen Repo liegen, stoppt der Helfer. Mit `--rpm /voller/Pfad.rpm` lässt sich
ein konkretes Hauptpaket angeben; dessen Inhalt muss dieselben neuen Pins erfüllen.

Dieses Bundle enthält **noch kein Telefon-Testskript**. Nach Auswertung der
Paketkennung und Hashes wird die passende neue RAM-Probe vorbereitet. Es werden
hier weder ein HTTP-Server gestartet noch Dateien auf dem Telefon installiert.
Die neue libc ist weiterhin als nicht auf dem Gerät getestet markiert.

## Ergebnis des Nutzerlaufs

Das inzwischen [ausgewertete Manifest](../validation/native-runtime-rpm-user-20261026.json)
bestätigt den erfolgreichen RPM-Neubau und die neuen Payload-/Bionic-Pins.
Weiter mit der [RAM-Geräteprüfung für genau dieses neue Paket](NATIVE_RUNTIME_DEVICE_PROBE.md).

`libneuralnetworks.so` und die reguläre Namespace-/Vendor-Integration bleiben
separat offen. Aus erfolgreicher Paketprüfung folgt kein vollständiger
Sailfish-/HAL-Start oder installierbares Release.
