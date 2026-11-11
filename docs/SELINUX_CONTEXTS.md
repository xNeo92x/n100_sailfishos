# N100: SELinux-Kontextdateien für die Android-Grunddienste

## Befund

Das HAL-RPM enthält die echte `libselinux.so` (versionierte Symbole), aber keine
Dateien unter `/system/etc/selinux`. Im Android-13-Quelltext gilt:

- `servicemanager` registriert sich selbst als `manager`
  (`frameworks/native/cmds/servicemanager/main.cpp:130`). Dazu sucht `Access::canAdd`
  einen Eintrag über `getSehandle()`; ohne lesbare `service_contexts` endet das in
  `CHECK(gSehandle != nullptr)` und damit im Abbruch (`Access.cpp`).
- `hwservicemanager` benötigt entsprechend `plat_hwservice_contexts`, Androids
  Property-Dienst `plat_property_contexts`.
- Die Zugriffsprüfung selbst ist unkritisch: Ohne eingehängtes selinuxfs liefert
  `selinux_check_access` 0 (`checkAccess.c`). Der hybris-Init lädt keine Android-Policy
  (Patch „(hybris) Disable SELinux“, SELinux übernimmt systemd).

Die Vendor-Gegenstücke (`vendor_*_contexts`) bringt die Stock-Vendor-Partition mit.

## Änderung

- `tools/build-hal.sh` baut zusätzlich `plat_service_contexts`,
  `plat_hwservice_contexts` und `plat_property_contexts`.
- `adaptation/rpm/helpers/install-android-runtime.sh` übernimmt die drei Dateien nach
  `/usr/libexec/droid-hybris/system/etc/selinux/` ins RPM; `tools/check-hal-runtime.py`
  verlangt sie und prüft Format und Schlüsseleinträge.
- `tools/check-gles-hal-rpm.py --with-selinux-contexts` erlaubt gegenüber dem am Gerät
  getesteten RPM genau: zwei `libGLESv3.so`, das Verzeichnis `etc/selinux` und die drei Dateien.

## Ein Befehl auf CachyOS

```bash
bash ~/n100_sailfishos/tools/run-selinux-rpm-build.sh
```

Das Skript baut die Kontextdateien im Ubuntu HABUILD SDK (`build-hal.sh runtime`,
bereits gebaute Ziele bleiben unverändert), baut im Platform SDK das HAL-RPM und
vergleicht es zurück auf CachyOS Eintrag für Eintrag. Telefon nicht nötig.
Bericht: `~/n100-build/selinux-hal-rpm-<UTC>/report.json`.
