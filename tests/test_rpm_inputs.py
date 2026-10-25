import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n100_rpm_inputs", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class RpmInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.android = root / "android"
        self.path = self.android / "build/make/tools/fs_config/fs_config_generator.py"
        self.path.parent.mkdir(parents=True)
        self.data = (ROOT / "compat/fs_config_generator/fs_config_generator.py").read_bytes()
        self.path.write_bytes(self.data)
        (self.android / ".n100-source-profile.json").write_text(
            json.dumps({"lock_sha256": n100.lock_digest()}))
        self.args = SimpleNamespace(android_root=str(self.android))

    def test_backport_identity_and_repeat_preparation(self):
        self.assertEqual(n100.git_blob_identity(self.data),
                         "44480b8aa4df0a85f9a82d772a1d7f4b3f763ffc")
        n100.prepare_rpm_inputs(self.args)
        n100.prepare_rpm_inputs(self.args)
        self.assertEqual(self.path.read_bytes(), self.data)
        audit = json.loads((self.android / ".n100-rpm-inputs.json").read_text())
        self.assertEqual(audit["fs_config_generator"]["source_commit"],
                         "216978e7c4bc20df2464e88f72f98bd302b419fd")

    def test_unreviewed_source_changes_are_preserved(self):
        self.path.write_bytes(b"# local changes\n")
        with self.assertRaisesRegex(n100.PortError, "unreviewed"):
            n100.prepare_rpm_inputs(self.args)
        self.assertEqual(self.path.read_bytes(), b"# local changes\n")

    def test_symlink_source_is_refused(self):
        target = self.path.parent / "other.py"
        target.write_bytes(self.data)
        self.path.unlink()
        self.path.symlink_to(target)
        with self.assertRaisesRegex(n100.PortError, "regular"):
            n100.prepare_rpm_inputs(self.args)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(target.read_bytes(), self.data)

    def test_changed_bundled_backport_is_refused_before_installation(self):
        port = Path(self.temporary.name) / "port"
        source = port / "compat/fs_config_generator/fs_config_generator.py"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"# changed compatibility file\n")
        with patch.object(n100, "ROOT", port), patch.object(n100, "require_profile"):
            with self.assertRaisesRegex(n100.PortError, "Bundled"):
                n100.prepare_rpm_inputs(self.args)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_stale_profile_is_refused_before_source_changes(self):
        (self.android / ".n100-source-profile.json").write_text(
            json.dumps({"lock_sha256": "other"}))
        with self.assertRaises(n100.PortError):
            n100.prepare_rpm_inputs(self.args)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_aidarray_emits_expected_android_identities(self):
        header = Path(self.temporary.name) / "android_filesystem_config.h"
        header.write_text("#define AID_ROOT 0\n#define AID_SYSTEM 1000\n"
                          "#define AID_SHELL 2000\n#define AID_NOBODY 9999\n"
                          "#define AID_OEM_RESERVED_START 2900\n"
                          "#define AID_OEM_RESERVED_END 2999\n")
        result = subprocess.run(
            [sys.executable, str(ROOT / "compat/fs_config_generator/fs_config_generator.py"),
             "aidarray", str(header)], check=True, capture_output=True, text=True)
        for name, symbol in (("root", "AID_ROOT"), ("system", "AID_SYSTEM"),
                             ("shell", "AID_SHELL"), ("nobody", "AID_NOBODY")):
            self.assertIn('{ "' + name + '", ' + symbol + " },", result.stdout)
        self.assertNotIn("oem_reserved", result.stdout)
        self.assertIn("#define android_id_count", result.stdout)
