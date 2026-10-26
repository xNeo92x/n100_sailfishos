import hashlib
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
HELPER = ROOT / "adaptation/rpm/helpers/remove-android-root-aliases.sh"
spec = importlib.util.spec_from_file_location("n100_rpm_root", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)

ALIASES = {
    "bugreports": "/data/user_de/0/com.android.shell/files/bugreports",
    "cache": "/data/cache", "d": "/sys/kernel/debug", "sdcard": "/storage/self/primary",
}


class StagedRootCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "installroot"
        (self.root / "usr/libexec/droid-hybris/system").mkdir(parents=True)
        (self.root / "sbin").mkdir()
        (self.root / "sbin/droid-hal-init").write_text("staged fixture")
        for name, target in ALIASES.items():
            (self.root / name).symlink_to(target)

    def run_helper(self, root=None):
        return subprocess.run(["bash", str(HELPER), str(root or self.root)],
                              capture_output=True, text=True)

    def test_removes_only_known_links_and_is_idempotent(self):
        apex = self.root / "usr/libexec/droid-hybris/system/bin/linker64"
        apex.parent.mkdir()
        apex.symlink_to("/apex/com.android.runtime/bin/linker64")
        udev = self.root / "etc/udev/rules.d/60-persistent-v4l.rules"
        udev.parent.mkdir(parents=True)
        udev.symlink_to("/dev/null")
        target = self.root / "data/cache/keep"
        target.parent.mkdir(parents=True)
        target.write_text("keep")
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertEqual(self.run_helper().returncode, 0)
        self.assertTrue(all(not os.path.lexists(self.root / name) for name in ALIASES))
        self.assertEqual(os.readlink(apex), "/apex/com.android.runtime/bin/linker64")
        self.assertEqual(os.readlink(udev), "/dev/null")
        self.assertEqual(target.read_text(), "keep")

    def test_unexpected_target_fails_before_removing_any_links(self):
        link = self.root / "sdcard"
        link.unlink()
        link.symlink_to("/custom/storage")
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected Android root alias", result.stderr)
        self.assertTrue(all(os.path.lexists(self.root / name) for name in ALIASES))

    def test_regular_file_is_preserved_and_no_earlier_links_are_removed(self):
        path = self.root / "cache"
        path.unlink()
        path.write_text("local data")
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertEqual(path.read_text(), "local data")
        self.assertTrue((self.root / "bugreports").is_symlink())

    def test_refuses_root_and_non_staging_directory(self):
        self.assertNotEqual(self.run_helper("/").returncode, 0)
        self.assertNotEqual(self.run_helper(self.temporary.name).returncode, 0)
        self.assertTrue(all((self.root / name).is_symlink() for name in ALIASES))


class RpmSnapshotUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.android = Path(self.temporary.name) / "android"
        self.rpm = self.android / "rpm"
        (self.rpm / "dhd").mkdir(parents=True)
        (self.rpm / "dhd/keep").write_text("existing pinned dependency and local fixups")
        (self.android / ".n100-source-profile.json").write_text(
            json.dumps({"lock_sha256": n100.lock_digest()}))
        self.spec = self.rpm / "droid-hal-billie2.spec"
        self.old = (ROOT / "tests/fixtures/droid-hal-billie2-before-root-cleanup.spec").read_bytes()
        self.spec.write_bytes(self.old)
        self.dependency = next(p for p in n100.read_lock()["packaging"] if p["parent"] == "rpm")
        self.marker = self.rpm / ".n100-prepared.json"
        self.marker.write_text(json.dumps({"dependency": self.dependency, "files": {
            self.spec.name: hashlib.sha256(self.old).hexdigest()}}))
        self.args = SimpleNamespace(android_root=str(self.android))
        checkout = patch.object(n100, "require_checkout")
        self.checkout = checkout.start()
        self.addCleanup(checkout.stop)

    def test_upgrade_is_idempotent_and_matches_normal_snapshot_verification(self):
        self.assertEqual(n100.git_blob_identity(self.old),
                         "b6184db55338fa680556ad2a4db65a30b33d15d5")
        n100.prepare_rpm_spec(self.args)
        n100.prepare_rpm_spec(self.args)
        n100.init_package(self.rpm, ROOT / "adaptation/rpm", self.dependency)
        self.assertIn("%define custom_install_cmds bash rpm/helpers/", self.spec.read_text())
        self.assertEqual((self.rpm / "helpers/remove-android-root-aliases.sh").read_bytes(),
                         HELPER.read_bytes())
        self.assertEqual((self.rpm / "dhd/keep").read_text(),
                         "existing pinned dependency and local fixups")

    def test_local_spec_changes_are_not_overwritten(self):
        self.spec.write_bytes(self.old + b"# local edit\n")
        with self.assertRaisesRegex(n100.PortError, "local edits"):
            n100.prepare_rpm_spec(self.args)
        self.assertTrue(self.spec.read_bytes().endswith(b"# local edit\n"))
        self.assertFalse((self.rpm / "helpers").exists())

    def test_upgrade_from_the_deployed_root_cleanup_snapshot(self):
        previous = (ROOT / 'tests/fixtures/droid-hal-billie2-before-runtime.spec').read_bytes()
        helper = self.rpm / 'helpers/remove-android-root-aliases.sh'
        helper.parent.mkdir()
        helper.write_bytes(HELPER.read_bytes())
        self.spec.write_bytes(previous)
        self.marker.write_text(json.dumps({'dependency': self.dependency, 'files': {
            self.spec.name: hashlib.sha256(previous).hexdigest(),
            'helpers/remove-android-root-aliases.sh': hashlib.sha256(HELPER.read_bytes()).hexdigest()}}))
        n100.prepare_rpm_spec(self.args)
        n100.prepare_rpm_spec(self.args)
        self.assertEqual((self.rpm / 'helpers/install-android-runtime.sh').read_bytes(),
                         (ROOT / 'adaptation/rpm/helpers/install-android-runtime.sh').read_bytes())
        n100.init_package(self.rpm, ROOT / 'adaptation/rpm', self.dependency)

    def test_unknown_predecessor_is_rejected_even_with_matching_marker_digest(self):
        self.spec.write_bytes(b"# other spec\n")
        self.marker.write_text(json.dumps({"dependency": self.dependency, "files": {
            self.spec.name: hashlib.sha256(self.spec.read_bytes()).hexdigest()}}))
        with self.assertRaisesRegex(n100.PortError, "reviewed predecessor"):
            n100.prepare_rpm_spec(self.args)
        self.assertEqual(self.spec.read_bytes(), b"# other spec\n")

    def test_existing_helper_or_parent_symlink_is_preserved(self):
        helpers = self.rpm / "helpers"
        helpers.mkdir()
        helper = helpers / HELPER.name
        helper.write_text("custom helper")
        with self.assertRaisesRegex(n100.PortError, "overwrite"):
            n100.prepare_rpm_spec(self.args)
        self.assertEqual(helper.read_text(), "custom helper")
        helper.unlink()
        helpers.rmdir()
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        helpers.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(n100.PortError, "overwrite"):
            n100.prepare_rpm_spec(self.args)
        self.assertEqual(self.spec.read_bytes(), self.old)
