import hashlib
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("webview_fix", ROOT / "tools/fix-webview-link.py")
fix = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fix)


class WebViewLinkTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.android = Path(self.temporary.name)
        self.directory = self.android / "external/chromium-webview"
        (self.directory / "patches").mkdir(parents=True)
        self.link = self.directory / "Android.mk"
        self.source = self.directory / fix.NEW_TARGET
        data = b"# reviewed fixture\n"
        self.source.write_bytes(data)
        digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        patcher = patch.object(fix, "MAKEFILE_BLOB", digest)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_repairs_broken_manifest_link_and_is_idempotent(self):
        self.link.symlink_to(fix.OLD_TARGET)
        self.assertFalse(self.link.exists())
        self.assertTrue(fix.repair(self.android))
        self.assertEqual(os.readlink(self.link), fix.NEW_TARGET)
        self.assertEqual(self.link.read_bytes(), self.source.read_bytes())
        self.assertFalse(fix.repair(self.android))
        self.assertEqual(list(self.directory.glob(".n100-webview-*")), [])

    def test_preserves_working_legacy_layout(self):
        (self.directory / fix.OLD_TARGET).write_text("# existing legacy file\n")
        self.link.symlink_to(fix.OLD_TARGET)
        self.assertFalse(fix.repair(self.android))
        self.assertEqual(os.readlink(self.link), fix.OLD_TARGET)

    def test_refuses_changed_upstream_file_without_changing_link(self):
        self.link.symlink_to(fix.OLD_TARGET)
        self.source.write_text("# unreviewed change\n")
        with self.assertRaisesRegex(ValueError, "differs"):
            fix.repair(self.android)
        self.assertEqual(os.readlink(self.link), fix.OLD_TARGET)

    def test_refuses_regular_file_or_unexpected_link(self):
        self.link.write_text("# local changes\n")
        with self.assertRaises(ValueError):
            fix.repair(self.android)
        self.assertEqual(self.link.read_text(), "# local changes\n")
        self.link.unlink()
        self.link.symlink_to("patches/custom.mk")
        with self.assertRaises(ValueError):
            fix.repair(self.android)
        self.assertEqual(os.readlink(self.link), "patches/custom.mk")

    def test_refuses_missing_source_or_source_symlink(self):
        self.link.symlink_to(fix.OLD_TARGET)
        self.source.unlink()
        with self.assertRaises(ValueError):
            fix.repair(self.android)
        target = self.directory / "other.mk"
        target.write_text("# reviewed fixture\n")
        self.source.symlink_to(target)
        with self.assertRaises(ValueError):
            fix.repair(self.android)
        self.assertEqual(os.readlink(self.link), fix.OLD_TARGET)
