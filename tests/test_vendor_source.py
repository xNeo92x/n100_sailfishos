import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import firmware
import n100


class VendorSourceTests(unittest.TestCase):
    def fixture(self, tree, lfs=False):
        tree.mkdir()
        good = b"pinned raw input"
        packaged = b"separate packaged input"
        text = ("vendor/lib/good.so|" + hashlib.sha1(good).hexdigest() + "\n"
                "system_ext/lib/wfd.so|" + "0" * 40 + "\n")
        declarations = firmware.parse_list(text)
        data = {"Android.bp": b"// fixture\n", "Android.mk": b"# fixture\n",
                "billie2-vendor.mk": b"# fixture\n", "BoardConfigVendor.mk": b"# fixture\n",
                "proprietary/vendor/lib/good.so": good,
                "proprietary/system_ext/lib/wfd.so": packaged}
        if lfs:
            data["proprietary/vendor/lib/good.so"] = b"version https://git-lfs.github.com/spec/v1\noid sha256:123\n"
        catalog = {}
        for name, content in data.items():
            file = tree / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_bytes(content)
            catalog[name] = {"bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for args in (("init", "-q"), ("add", "."), ("-c", "user.name=Test", "-c", "user.email=test@localhost", "commit", "-qm", "fixture")):
            subprocess.run(["git", *args], cwd=tree, check=True)
        revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=tree, text=True).strip()
        pin = {"revision": revision, "tracked_files": len(data), "required_blob_files": 2,
               "catalog_sha256": hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
               "profile": "test-pre-extracted", "repository": "test/vendor", "source_list_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "packaged_reference_differences": {"system_ext/lib/wfd.so": {
                   "sha1": hashlib.sha1(packaged).hexdigest(), "sha256": hashlib.sha256(packaged).hexdigest(),
                   "input_reference_sha1": ["0" * 40]}}}
        return pin, text, declarations

    def test_separate_packaged_source_checks_both_catalog_and_declared_differences(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "vendor"
            pin, _, declarations = self.fixture(tree)
            result = firmware.verify_vendor(tree, declarations, pin)
            self.assertTrue(result["build_inputs_complete"])
            self.assertFalse(result["stock_eea_runtime_compatibility_verified"])
            self.assertEqual(result["raw_reference_pins_matched"], 1)
            self.assertEqual(result["declared_packaged_reference_differences"], ["system_ext/lib/wfd.so"])
            pin["catalog_sha256"] = "f" * 64
            with self.assertRaisesRegex(n100.PortError, "dataset SHA-256"):
                firmware.verify_vendor(tree, declarations, pin)

    def test_unassessed_difference_and_local_edits_are_not_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "vendor"
            pin, _, declarations = self.fixture(tree)
            pin["packaged_reference_differences"] = {}
            with self.assertRaisesRegex(n100.PortError, "Unassessed"):
                firmware.verify_vendor(tree, declarations, pin)
            (tree / "proprietary/vendor/lib/good.so").write_bytes(b"changed")
            with self.assertRaisesRegex(n100.PortError, "tracked changes"):
                firmware.verify_vendor(tree, declarations, pin)

    def test_lfs_pointer_is_not_a_usable_vendor_blob(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "vendor"
            pin, _, declarations = self.fixture(tree, lfs=True)
            with self.assertRaisesRegex(n100.PortError, "unresolved Git LFS"):
                firmware.verify_vendor(tree, declarations, pin)

    def test_untracked_build_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tree = Path(tmp) / "vendor"
            pin, _, declarations = self.fixture(tree)
            extra = tree / "extra/Android.bp"
            extra.parent.mkdir()
            extra.write_text("// unexpected build input\n")
            with self.assertRaisesRegex(n100.PortError, "untracked files"):
                firmware.verify_vendor(tree, declarations, pin)

    def test_verified_cache_needs_no_adb_and_preserves_stock_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            tree = work / "vendor-source"
            pin, text, declarations = self.fixture(tree)
            dump = work / "firmware/files/vendor/stock-only.txt"
            dump.parent.mkdir(parents=True)
            dump.write_text("preserve stock input")
            with patch.object(firmware, "vendor_pin", return_value=pin), \
                 patch.object(firmware, "source_tree", return_value=({}, text, declarations)), \
                 patch.object(n100, "collect_report", side_effect=AssertionError("ADB must not be used")):
                firmware.fetch_vendor(work)
            self.assertEqual(dump.read_text(), "preserve stock input")
            self.assertTrue((work / "vendor-source-audit.json").is_file())


class AdbFailureTests(unittest.TestCase):
    def test_error_reason_preserved_serial_redacted_and_permanent_failure_not_retried(self):
        report = json.loads((ROOT / "tests/fixtures/be2013-stock11-v2.json").read_text())
        text = "vendor/lib/private.so\n"
        declarations = firmware.parse_list(text)
        failure = SimpleNamespace(returncode=1, stdout="", stderr="Permission denied on serial-test-device")
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(firmware, "source_tree", return_value=({"revision": "a" * 40}, text, declarations)), \
             patch.object(n100, "collect_report", return_value=report), \
             patch.object(firmware.shutil, "which", return_value="adb"), \
             patch.object(firmware.subprocess, "run", return_value=failure) as pull:
            work = Path(tmp)
            for retry in (False, False, True):
                with self.assertRaisesRegex(n100.PortError, "Incomplete dump"):
                    firmware.collect(work, "serial-test-device", retry_failed=retry)
            self.assertEqual(pull.call_count, 2)
            inventory = json.loads((work / "firmware/inventory.json").read_text())
            record = inventory["files"]["vendor/lib/private.so"]
            self.assertEqual(record["stderr"], "Permission denied on <device>")
            self.assertEqual(record["returncode"], 1)
            self.assertEqual(record["error_kind"], "permission_denied")

    def test_failure_classification_does_not_guess_a_cause(self):
        self.assertEqual(firmware.adb_error_kind("", "No such file or directory"), "not_found")
        self.assertEqual(firmware.adb_error_kind("", "device 'example' not found"), "transport")
        self.assertEqual(firmware.adb_error_kind("", "unrecognized failure"), "unknown")


if __name__ == "__main__":
    unittest.main()
