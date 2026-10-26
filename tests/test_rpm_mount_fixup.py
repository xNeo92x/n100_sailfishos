import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n100_rpm_mounts", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class RpmMountFixupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.android = Path(self.temporary.name) / "android"
        self.path = self.android / "rpm/dhd/droid-hal-device.inc"
        self.path.parent.mkdir(parents=True)
        self.original = ("# include fixture\n%build\necho Fixing up mount points\n"
                         "hybris/hybris-boot/fixup-mountpoints %{device} tmp/units/*\n"
                         "echo Creating hw-release\n" + n100.DHD_LINKERCONFIG_COPY + "\n")
        self.path.write_text(self.original)
        self.override = self.android / "hybris/fixup-rpm-mountpoints"
        self.override.parent.mkdir(parents=True)
        # Synthetic mappings for the generated-script integration test.
        self.override.write_text(n100.mount_fixup_script(
            {"userdata": "/dev/sdz16", "persist": "/dev/sdz2", "op2": "/dev/sdz9"},
            prefixes=("/dev/block/bootdevice/by-name/", "/dev/block/by-name/", "/dev/disk/by-partlabel/")))
        self.override.chmod(0o755)
        expected = patch.object(n100, "DHD_MOUNT_FIXUP_BLOB",
                                n100.git_blob_identity(self.original.encode()))
        expected.start()
        self.addCleanup(expected.stop)
        checkout = patch.object(n100, "require_checkout")
        self.checkout = checkout.start()
        self.addCleanup(checkout.stop)

    def test_uses_override_in_actual_mount_recipe_and_is_idempotent(self):
        n100.install_rpm_mount_fixup(self.android)
        first = self.path.read_bytes()
        n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(self.path.read_bytes(), first)
        self.assertEqual(first.decode(), self.original.replace(
            "hybris/hybris-boot/fixup-mountpoints", "hybris/fixup-rpm-mountpoints").replace(
                n100.DHD_LINKERCONFIG_COPY, n100.DHD_LINKERCONFIG_COPY_FIXED))
        self.checkout.assert_called_with(self.android / "rpm/dhd",
                                         "775f6c05123ca7e58422df1fcfd51943eb56920f")
        units = self.android / "tmp/units"
        units.mkdir(parents=True)
        persist = units / "mnt-vendor-persist.mount"
        persist.write_text("[Mount]\nWhat=/dev/disk/by-partlabel/persist\n")
        firmware = units / "vendor-dsp.mount"
        firmware.write_text("[Mount]\nWhat=/dev/disk/by-partlabel/dsp\n")
        command = next(line for line in first.decode().splitlines()
                       if line.startswith("hybris/fixup-rpm-mountpoints "))
        command = command.replace("%{device}", "billie2")
        subprocess.run(["bash", "-ec", command], cwd=self.android, check=True,
                       capture_output=True, text=True)
        self.assertIn("What=/dev/sdz2", persist.read_text())
        # An unsuffixed slotselect alias is never guessed from a collected slot.
        self.assertIn("What=/dev/disk/by-partlabel/dsp", firmware.read_text())
        audit = json.loads((self.android / ".n100-rpm-mounts.json").read_text())
        self.assertFalse(audit["slotselect_runtime_implemented"])
        self.assertFalse(audit["boot_tested"])

    def test_preserves_unreviewed_edits_before_or_after_installation(self):
        changed = self.original + "# local changes\n"
        self.path.write_text(changed)
        with self.assertRaisesRegex(n100.PortError, "unreviewed"):
            n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(self.path.read_text(), changed)
        self.path.write_text(self.original)
        n100.install_rpm_mount_fixup(self.android)
        changed = self.path.read_text() + "# local changes after patch\n"
        self.path.write_text(changed)
        with self.assertRaisesRegex(n100.PortError, "unreviewed"):
            n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(self.path.read_text(), changed)

    def test_upgrades_the_previous_mount_only_include_and_marker(self):
        old = self.original.replace('hybris/hybris-boot/fixup-mountpoints', 'hybris/fixup-rpm-mountpoints')
        self.path.write_text(old)
        dependency = next(p for p in n100.read_lock()['packaging'] if p['path'] == 'dhd')
        marker = self.android / '.n100-rpm-mounts.json'
        marker.write_text(json.dumps({
            'schema': 1, 'dhd_revision': dependency['revision'],
            'original_include_blob': n100.git_blob_identity(self.original.encode()),
            'installed_include_blob': n100.git_blob_identity(old.encode()),
            'mount_fixup': 'hybris/fixup-rpm-mountpoints',
            'slotselect_runtime_implemented': False, 'boot_tested': False}))
        n100.install_rpm_mount_fixup(self.android)
        first = self.path.read_bytes(), marker.read_bytes()
        n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(first, (self.path.read_bytes(), marker.read_bytes()))
        self.assertTrue(json.loads(marker.read_text())['linkerconfig_copy_replaces_destination'])
        self.assertIn('hybris/fixup-rpm-mountpoints', self.path.read_text())

    def test_rejects_unknown_audit_metadata_before_changing_include(self):
        marker = self.android / '.n100-rpm-mounts.json'
        marker.write_text('{"custom": true}\n')
        with self.assertRaisesRegex(n100.PortError, 'metadata has unreviewed'):
            n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(self.path.read_text(), self.original)
        self.assertEqual(marker.read_text(), '{"custom": true}\n')

    def test_copy_recipe_replaces_link_without_touching_its_target(self):
        source = self.android / 'out/target/product/billie2/apex/com.android.runtime/bin/linkerconfig'
        source.parent.mkdir(parents=True)
        source.write_bytes(b'checked static executable fixture')
        source.chmod(0o755)
        buildroot = self.android / 'installroot'
        destination = buildroot / 'usr/libexec/droid-hybris/system/bin/linkerconfig'
        destination.parent.mkdir(parents=True)
        outside = self.android / 'outside/keep'
        outside.parent.mkdir()
        recipe = n100.DHD_LINKERCONFIG_COPY.replace('%{android_root}', str(self.android)).replace('%{device}', 'billie2').replace('%{_libexecdir}', '/usr/libexec')
        env = dict(os.environ, RPM_BUILD_ROOT=str(buildroot))
        env.pop('POSIXLY_CORRECT', None)
        destination.symlink_to(outside)
        before = subprocess.run(['sh', '-ec', recipe], env=env, capture_output=True)
        self.assertNotEqual(before.returncode, 0)
        self.assertFalse(outside.exists())
        fixed = n100.DHD_LINKERCONFIG_COPY_FIXED.replace('%{android_root}', str(self.android)).replace('%{device}', 'billie2').replace('%{_libexecdir}', '/usr/libexec')
        for existing_target in (False, True):
            if destination.exists() or destination.is_symlink():
                destination.unlink()
            if existing_target:
                outside.write_bytes(b'preserve outside file')
            destination.symlink_to(outside)
            subprocess.run(['sh', '-ec', fixed], env=env, check=True, capture_output=True)
            self.assertFalse(destination.is_symlink())
            self.assertEqual(destination.read_bytes(), source.read_bytes())
            self.assertEqual(destination.stat().st_mode & 0o777, 0o755)
            self.assertEqual(outside.read_bytes() if existing_target else None,
                             b'preserve outside file' if existing_target else None)

    def test_requires_regular_executable_override(self):
        self.override.chmod(0o644)
        with self.assertRaisesRegex(n100.PortError, "executable"):
            n100.install_rpm_mount_fixup(self.android)
        self.override.chmod(0o755)
        other = self.override.with_name("other")
        self.override.rename(other)
        self.override.symlink_to(other)
        with self.assertRaisesRegex(n100.PortError, "executable"):
            n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(self.path.read_text(), self.original)

    def test_refuses_include_symlink_or_wrong_dependency_revision(self):
        other = self.path.with_name("other.inc")
        self.path.rename(other)
        self.path.symlink_to(other)
        with self.assertRaisesRegex(n100.PortError, "regular"):
            n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(other.read_text(), self.original)
        self.path.unlink()
        other.rename(self.path)
        self.checkout.side_effect = n100.PortError("wrong revision")
        with self.assertRaisesRegex(n100.PortError, "wrong revision"):
            n100.install_rpm_mount_fixup(self.android)
        self.assertEqual(self.path.read_text(), self.original)

    def test_revalidates_saved_report_before_patching_rpm_include(self):
        args = SimpleNamespace(android_root=str(self.android), device_report=str(Path(self.temporary.name) / "report.json"))
        Path(args.device_report).write_text("{}")
        self.override.unlink()
        with patch.object(n100, "prepare_boot_inputs") as prepare, patch.object(
                n100, "boot_mount_mapping", return_value={"userdata": "/dev/sdz16"}):
            n100.prepare_rpm_mounts(args)
        prepare.assert_called_once_with(args)
        self.assertIn("hybris/fixup-rpm-mountpoints %{device}", self.path.read_text())
