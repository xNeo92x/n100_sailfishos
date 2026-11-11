# N100: HAL-RPM mit libGLESv3 neu bauen

Nach der [bestandenen Vendor-Probe V3](GRAPHICS_VENDOR_PROBE.md) soll das HAL-Paket
`droid-hal-billie2` die gebaute `libGLESv3.so` dauerhaft enthalten. Ein Vorab-Vergleich
des Android-Ausgabebaums mit dem am Gerät getesteten RPM
(`b6714b06…a3bc6`) zeigt: Gegenüber dem Paket kommen nur `system/lib64/libGLESv3.so` und
`system/lib/libGLESv3.so` hinzu; alle übrigen Paketdateien sind unverändert.
(`libdroidmedia.so`, `libminisf.so` und die Mini-Dienste gehören in das Media-RPM.)

## Ein Befehl auf CachyOS

```bash
bash ~/n100_sailfishos/tools/run-hal-rpm-build.sh
```

1. Das getestete RPM wird nach `~/n100-build/rpm-archive/` gesichert, weil DHD ältere
   Hauptpakete beim Neubau löscht. Der SHA-256 wird geprüft.
2. Im Platform SDK läuft `tools/build-packages.sh hal` mit `RELEASE=5.1.0.11`.
   Android wird dabei nicht neu kompiliert, es wird nur paketiert.
3. Zurück auf CachyOS vergleicht `tools/check-gles-hal-rpm.py` beide Pakete Eintrag für
   Eintrag (Typ, Rechte, Größe, SHA-256, Symlink-Ziel). Bestanden nur, wenn genau die
   zwei `libGLESv3.so` hinzukommen und die 64-Bit-Datei der am Gerät getesteten entspricht.
   Bericht: `~/n100-build/gles-hal-rpm-<UTC>/report.json`, Gesamtprotokoll:
   `~/n100-build/hal-rpm-console-<UTC>.log`.

Das Telefon wird nicht benötigt und nicht verändert. Das neue RPM ist danach weder am
Gerät getestet noch in einem Userdata-Image enthalten.

## Ergebnis (11.11.2026)

[Bestanden](../validation/gles-hal-rpm-user-20261111.json):
`droid-hal-billie2-0.0.6-202611111819.aarch64.rpm` (SHA-256 `ecef6f4c…50ca`) enthält
gegenüber dem getesteten Paket genau die zwei neuen Dateien; die 64-Bit-Bibliothek ist
die am Gerät geprüfte (`e1efbb5b…f958`). Beide Pakete liegen in `~/n100-build/rpm-archive/`.
