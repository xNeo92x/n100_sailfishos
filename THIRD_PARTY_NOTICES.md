# Third-party sources and licensing

Upstream projects are fetched separately, at revisions in `sources.lock.json`.
They retain their own notices and licenses. No proprietary vendor blobs or
Jolla-licensed binary products are included in this repository.

- `adaptation/rpm/droid-hal-billie2.spec` is based on the device spec template
  in mer-hybris/droid-hal-device (BSD). Its upstream package macros and helpers
  are used as an independently fetched Git submodule.
- `adaptation/droid-configs/` RPM and package-group files are based on templates
  in mer-hybris/droid-hal-configs (GPLv2). These derivative files are GPL-2.0.
- `adaptation/droid-hal-version-billie2/` is based on
  mer-hybris/droid-hal-version's template (GPLv2), and is GPL-2.0.
- The sparse SELinux layout follows the HADK FAQ / Android-10 adaptation
  convention. The two absolute symlinks refer to files expected on the user's
  own device and do not contain Android policy binaries.
- The kernel defconfig is generated locally from LineageOS's OnePlus SM4250
  GPL-2.0 kernel, derived from the manufacturer's sources.
  `kernel/sailfish.config` contains initial configuration selections;
  the resulting kernel/config remains subject to the kernel's licensing.
- `tests/fixtures/fstab.default`, `fstab.emmc` and `fstab_nodata.qcom` were
  collected from the supplied BE2013 stock configuration archive. They carry
  The Linux Foundation's BSD redistribution conditions, retained in full in
  each file; they are not covered by the MIT license for new project tools.
- The Android device tree is obtained separately from LineageOS
  and retains its Apache-2.0 and per-file notices. None of its source notices
  are removed by the setup tool.

Relevant upstream repositories and exact revisions are listed in
`docs/SOURCES.md` and `sources.lock.json`. Before redistributing a compiled
kernel or other GPL component, provide corresponding source, build config and
local changes as required by its license. A future release should carry all
applicable upstream license texts along with its build-source inventory.
