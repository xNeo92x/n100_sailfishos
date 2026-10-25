import importlib.util
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n100_build_deps", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class BuildDependencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.android = Path(self.temporary.name) / "android"
        (self.android / ".repo/local_manifests").mkdir(parents=True)
        self.args = SimpleNamespace(android_root=str(self.android))
        self.profile = patch.object(n100, "require_profile")
        self.profile.start()
        self.addCleanup(self.profile.stop)

    def test_sync_targets_only_additions_and_preserves_existing_profile_and_manifest(self):
        marker = self.android / ".n100-source-profile.json"
        marker.write_text("existing profile")
        base = self.android / ".repo/local_manifests/billie2.xml"
        base.write_text("existing manifest")
        with patch.object(n100, "run") as run, patch.object(n100, "require_checkout") as checkout:
            n100.sync_build_dependencies(self.args)
        self.assertEqual(run.call_args_list[0].args[0],
                         ["repo", "sync", "-c", "-j1", "--no-clone-bundle", "--fail-fast",
                          "packages/apps/SimpleSettingsConfig", "vendor/apn"])
        self.assertEqual(len(run.call_args_list), 1)
        self.assertEqual({call.args[0] for call in checkout.call_args_list},
                         {self.android / "packages/apps/SimpleSettingsConfig", self.android / "vendor/apn"})
        self.assertEqual(marker.read_text(), "existing profile")
        self.assertEqual(base.read_text(), "existing manifest")

    def test_modified_manifest_stops_before_any_download(self):
        installed = self.android / ".repo/local_manifests/n100-build-dependencies.xml"
        installed.write_text("local changes")
        with patch.object(n100, "run") as run, self.assertRaises(n100.PortError):
            n100.sync_build_dependencies(self.args)
        run.assert_not_called()
        self.assertEqual(installed.read_text(), "local changes")

    def test_dirty_dependency_stops_before_sync(self):
        path = self.android / "vendor/apn"
        path.mkdir(parents=True)
        with patch.object(n100, "require_checkout"), patch.object(n100, "run", return_value=" M Android.bp") as run:
            with self.assertRaisesRegex(n100.PortError, "local changes"):
                n100.sync_build_dependencies(self.args)
        self.assertTrue(all(call.args[0][0] == "git" for call in run.call_args_list))

    def test_wrong_revision_stops_before_sync(self):
        (self.android / "vendor/apn").mkdir(parents=True)
        with patch.object(n100, "require_checkout", side_effect=n100.PortError("Revision mismatch")), patch.object(n100, "run") as run:
            with self.assertRaisesRegex(n100.PortError, "Revision mismatch"):
                n100.sync_build_dependencies(self.args)
        run.assert_not_called()

    def test_dependency_directory_symlink_is_rejected(self):
        (self.android / "vendor").mkdir()
        target = Path(self.temporary.name) / "other"
        target.mkdir()
        (self.android / "vendor/apn").symlink_to(target, target_is_directory=True)
        with patch.object(n100, "run") as run, self.assertRaisesRegex(n100.PortError, "symlink"):
            n100.sync_build_dependencies(self.args)
        run.assert_not_called()

    def test_verification_requires_installed_manifest_then_both_pins(self):
        with self.assertRaisesRegex(n100.PortError, "sync-build-deps"):
            n100.verify_tree(self.args)
        projects = n100.install_build_dependency_manifest(self.android)
        with patch.object(n100, "require_checkout") as checkout:
            n100.verify_tree(self.args)
        self.assertEqual([call.args[1] for call in checkout.call_args_list],
                         [p["revision"] for p in projects])

    def test_revision_mismatch_prevents_success_after_sync(self):
        with patch.object(n100, "run"), patch.object(n100, "require_checkout", side_effect=n100.PortError("Revision mismatch")):
            with self.assertRaisesRegex(n100.PortError, "Revision mismatch"):
                n100.sync_build_dependencies(self.args)
