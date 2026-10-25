# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("n100_config_ofono", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(n100)


class ConfigOfonoUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.android = Path(self.temp.name) / "android"
        self.destination = self.android / "hybris/droid-configs"
        self.source = ROOT / "adaptation/droid-configs"
        shutil.copytree(self.source, self.destination, symlinks=True)
        self.spec = self.destination / "rpm/droid-config-billie2.spec"
        self.old = (ROOT / "tests/fixtures/droid-config-billie2-before-ofono.spec").read_bytes()
        self.spec.write_bytes(self.old)
        self.dependency = next(p for p in n100.read_lock()["packaging"]
                               if p["parent"] == "hybris/droid-configs")
        (self.destination / self.dependency["path"]).mkdir()
        (self.destination / self.dependency["path"] / "keep").write_text("pinned source")
        (self.android / ".n100-source-profile.json").write_text(
            json.dumps({"lock_sha256": n100.lock_digest()}))
        self.marker = self.destination / ".n100-prepared.json"
        self.record_marker()
        self.args = SimpleNamespace(android_root=str(self.android))
        checkout = patch.object(n100, "require_checkout")
        checkout.start()
        self.addCleanup(checkout.stop)

    def record_marker(self):
        files = {}
        for path in self.source.rglob("*"):
            if path.is_file() or path.is_symlink():
                relative = path.relative_to(self.source)
                installed = self.destination / relative
                files[str(relative)] = ("symlink:" + os.readlink(installed) if installed.is_symlink()
                                        else hashlib.sha256(installed.read_bytes()).hexdigest())
        self.marker.write_text(json.dumps({"dependency": self.dependency, "files": files}))

    def test_upgrade_is_idempotent_and_normal_snapshot_validation_accepts_it(self):
        self.assertEqual(n100.git_blob_identity(self.old), "8ea1bd5ce17e05abac0cbb689f1946010d56ee6b")
        n100.prepare_config_spec(self.args)
        n100.prepare_config_spec(self.args)
        n100.init_package(self.destination, self.source, self.dependency)
        content = self.spec.read_text()
        self.assertIn("Provides: ofono-configs\nObsoletes: ofono-configs-binder\n", content)
        self.assertLess(content.index("Provides: ofono-configs"), content.index("%include"))
        self.assertEqual((self.destination / self.dependency["path"] / "keep").read_text(), "pinned source")
        policy = "sparse/etc/selinux/minimum/policy/policy.30"
        self.assertEqual(os.readlink(self.destination / policy), os.readlink(self.source / policy))

    def test_local_spec_edit_is_preserved(self):
        self.spec.write_bytes(self.old + b"# local edit\n")
        with self.assertRaisesRegex(n100.PortError, "local edits"):
            n100.prepare_config_spec(self.args)
        self.assertTrue(self.spec.read_bytes().endswith(b"# local edit\n"))

    def test_unknown_spec_is_rejected_even_with_matching_marker(self):
        self.spec.write_bytes(b"# unknown predecessor\n")
        self.record_marker()
        with self.assertRaisesRegex(n100.PortError, "reviewed predecessor"):
            n100.prepare_config_spec(self.args)
        self.assertEqual(self.spec.read_bytes(), b"# unknown predecessor\n")

    def test_other_snapshot_changes_stop_upgrade_before_writing_spec(self):
        config = self.destination / "sparse/etc/selinux/config"
        config.write_text("# local config\n")
        self.record_marker()
        with self.assertRaisesRegex(n100.PortError, "reviewed predecessor"):
            n100.prepare_config_spec(self.args)
        self.assertEqual(self.spec.read_bytes(), self.old)

    def test_symlink_parent_is_preserved_and_rejected(self):
        directory = self.destination / "rpm"
        outside = Path(self.temp.name) / "outside"
        directory.rename(outside)
        directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(n100.PortError, "symlink parent"):
            n100.prepare_config_spec(self.args)
        self.assertEqual((outside / self.spec.name).read_bytes(), self.old)

    def test_missing_marker_file_is_rejected(self):
        self.marker.unlink()
        with self.assertRaisesRegex(n100.PortError, "snapshot marker"):
            n100.prepare_config_spec(self.args)
        self.assertEqual(self.spec.read_bytes(), self.old)


if __name__ == "__main__":
    unittest.main()
