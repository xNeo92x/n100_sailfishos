import importlib.util
import json
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
                         "echo Creating hw-release\n")
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
            "hybris/hybris-boot/fixup-mountpoints", "hybris/fixup-rpm-mountpoints"))
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
