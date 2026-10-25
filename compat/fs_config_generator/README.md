# Android fs_config generator Python 3 backport

The two Python files in this directory are unmodified upstream files from
[LineageOS/android_build, commit 216978e7c4bc20df2464e88f72f98bd302b419fd](https://github.com/LineageOS/android_build/commit/216978e7c4bc20df2464e88f72f98bd302b419fd),
under `tools/fs_config/`. They retain the upstream Android Apache-2.0 license;
see LICENSE and the upstream directory's Android.bp license declaration.

| File | Git blob ID |
| --- | --- |
| fs_config_generator.py | 44480b8aa4df0a85f9a82d772a1d7f4b3f763ffc |
| test_fs_config_generator.py | cbf46a14a4c2fd1e9ed6610aa30317ce07b03a4c |

The generator is the official Python 3 conversion, including the preceding
AID/range parser corrections from upstream commit
`f08ab3d098448085616264312eb911936e39f14f`. The RPM helper invokes its
`aidarray` command to generate Android filesystem identity data.

The selected Android 13 build tree still ships the Python 2 file with blob
`098fde664f612626bc6342aa46808d467145281c`. Sailfish SDK 5.1.0.11's tooling
reported that `python2` was unavailable. The port's `n100.py prepare-rpm`
installs this reviewed backport at the original Android-tree path only when
that exact original file, or the already installed backport, is present.
It preserves the executable permissions, refuses unrelated local edits, and
records provenance in `.n100-rpm-inputs.json`. It does not replace the rest of
the Android build tree or define a global `python2` command.

Run the 16 upstream tests with:

```bash
python3 -m unittest discover -s compat/fs_config_generator -p 'test_*.py' -v
```

This directory is Apache-2.0, not the MIT license used for newly written
port tools. The files are copied verbatim to preserve the reviewed blob IDs.
