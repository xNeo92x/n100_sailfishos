import copy
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
spec = importlib.util.spec_from_file_location("n100_boot_inputs", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class BootInputTests(unittest.TestCase):
    def report(self):
        return json.loads((ROOT / "tests/fixtures/be2013-stock11-v2.json").read_text())

    def test_physical_userdata_does_not_use_decrypted_mapper(self):
        mapping = n100.boot_mount_mapping(self.report())
        self.assertEqual(mapping["userdata"], "/dev/sda16")
        self.assertEqual(mapping["boot_a"], "/dev/sde11")
        self.assertEqual(mapping["boot_b"], "/dev/sde32")
        self.assertNotIn("boot", mapping)
        self.assertFalse(any("dm-" in value for value in mapping.values()))
        self.assertNotIn("system_a", mapping)

    def test_missing_or_nonphysical_userdata_is_rejected(self):
        for replacement in ("", "/dev/block/dm-12"):
            report = self.report()
            report["diagnostics"]["partitions"] = (
                "/dev/block/by-name:\n"
                + ("lrwxrwxrwx 1 root root 0 fixture userdata -> " + replacement + "\n"
                   if replacement else "")
                + "/dev/block/mapper:\n"
                + "lrwxrwxrwx 1 root root 0 fixture userdata -> /dev/block/dm-12\n")
            with self.assertRaisesRegex(n100.PortError, "physical userdata"):
                n100.boot_mount_mapping(report)

    def test_incompatible_report_or_conflicting_aliases_is_rejected(self):
        report = self.report()
        report["properties"]["ro.build.version.sdk"] = "29"
        with self.assertRaises(n100.PortError):
            n100.boot_mount_mapping(report)
        report = self.report()
        report["diagnostics"]["partitions"] = (
            "/dev/block/by-name:\n"
            "lrwxrwxrwx 1 root root 0 fixture userdata -> /dev/block/sda16\n"
            "lrwxrwxrwx 1 root root 0 fixture userdata -> /dev/block/sda17\n")
        with self.assertRaisesRegex(n100.PortError, "Inconsistent"):
            n100.boot_mount_mapping(report)

    def test_generated_script_rewrites_exact_physical_tokens_in_multiple_files(self):
        mapping = n100.boot_mount_mapping(self.report())
        script = n100.mount_fixup_script(mapping)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixup = root / "fixup-mountpoints"
            fixup.write_text(script)
            subprocess.run(["sh", "-n", str(fixup)], check=True)
            first = root / "init with spaces"
            first.write_text(
                "DATA_PARTITION=/dev/block/bootdevice/by-name/userdata\n"
                'BOOT="/dev/block/by-name/boot_b"\n'
                "UNCHANGED=/dev/block/bootdevice/by-name/userdata_extra\n"
                "LOGICAL=/dev/block/mapper/system_b\n"
                "UNKNOWN=/dev/block/by-name/boot\n")
            second = root / "fstab"
            second.write_text("/dev/block/by-name/userdata /data ext4 rw wait\n")
            subprocess.run(["sh", str(fixup), "billie2", str(first), str(second)], check=True,
                           capture_output=True, text=True)
            self.assertEqual(first.read_text(),
                             "DATA_PARTITION=/dev/sda16\n"
                             'BOOT="/dev/sde32"\n'
                             "UNCHANGED=/dev/block/bootdevice/by-name/userdata_extra\n"
                             "LOGICAL=/dev/block/mapper/system_b\n"
                             "UNKNOWN=/dev/block/by-name/boot\n")
            self.assertEqual(second.read_text(), "/dev/sda16 /data ext4 rw wait\n")

    def test_script_rejects_wrong_device_and_symlink_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixup = root / "fixup"
            fixup.write_text(n100.mount_fixup_script(n100.boot_mount_mapping(self.report())))
            data = root / "input"
            original = "/dev/block/by-name/userdata\n"
            data.write_text(original)
            link = root / "link"
            link.symlink_to(data)
            for args in (["other", str(data)], ["billie2", str(link)], []):
                result = subprocess.run(["sh", str(fixup)] + args, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(data.read_text(), original)

    def test_preparation_records_evidence_and_preserves_local_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            android = root / "android"
            android.mkdir()
            (android / ".n100-source-profile.json").write_text(
                json.dumps({"lock_sha256": n100.lock_digest()}))
            report = root / "report.json"
            report.write_text(json.dumps(self.report()))
            args = SimpleNamespace(android_root=str(android), device_report=str(report))
            with patch.object(n100, "require_checkout") as checkout:
                n100.prepare_boot_inputs(args)
                n100.prepare_boot_inputs(args)
            self.assertEqual(checkout.call_args.args[1],
                             "6547fd1c3aeb9c987271a01b298d2ae5d39be231")
            fixup = android / "hybris/fixup-mountpoints"
            self.assertEqual(fixup.stat().st_mode & 0o777, 0o755)
            audit = json.loads((android / ".n100-boot-inputs.json").read_text())
            self.assertEqual(audit["physical_mount_mapping"]["userdata"], "/dev/sda16")
            self.assertEqual(audit["report_sha256"], hashlib.sha256(report.read_bytes()).hexdigest())
            self.assertFalse(audit["logical_partitions_implemented"])
            self.assertFalse(audit["android_data_decryption_implemented"])
            fixup.write_text("# local edits\n")
            with patch.object(n100, "require_checkout"), self.assertRaisesRegex(n100.PortError, "differs"):
                n100.prepare_boot_inputs(args)
            self.assertEqual(fixup.read_text(), "# local edits\n")


class CcachePreflightTests(unittest.TestCase):
    def test_configured_missing_cache_fails_but_disabled_cache_does_not(self):
        source = (ROOT / "tools/build-hal.sh").read_text()
        start = source.index('# Match build/make/core/ccache.mk:')
        end = source.index("breakfast billie2", start)
        check = source[start:end]
        for enabled, expected in (("1", 1), ("false", 0), ("", 0)):
            environment = dict(os.environ, USE_CCACHE=enabled, CCACHE_EXEC="/missing/n100/ccache")
            result = subprocess.run(["bash", "-c", check], env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, expected)
            if expected:
                self.assertIn("sudo apt-get install ccache", result.stderr)
