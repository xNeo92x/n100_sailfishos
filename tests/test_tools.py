import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n100", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class ConfigTests(unittest.TestCase):
    def test_overrides_disabled_and_enabled_without_duplicates(self):
        merged = n100.merge_config("# stock\n# CONFIG_VT is not set\nCONFIG_NET=y\nCONFIG_DUMMY=y\n",
                                   "CONFIG_VT=y\n# CONFIG_DUMMY is not set\nCONFIG_DEVTMPFS=y\n")
        self.assertEqual(n100.parse_config(merged), {
            "CONFIG_VT": "y", "CONFIG_NET": "y", "CONFIG_DUMMY": "n", "CONFIG_DEVTMPFS": "y"})
        self.assertEqual(merged.count("CONFIG_VT="), 1)
        self.assertIn("# stock", merged)

    def test_preserves_string_and_numeric_values(self):
        merged = n100.merge_config('CONFIG_CMDLINE="stock"\nCONFIG_HZ=100\n', 'CONFIG_HZ=300\n')
        self.assertEqual(n100.parse_config(merged)["CONFIG_CMDLINE"], '"stock"')
        self.assertEqual(n100.parse_config(merged)["CONFIG_HZ"], "300")


class FileTests(unittest.TestCase):
    def test_idempotent_but_refuses_local_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "new/file.txt"
            n100.write_new(dest, "original")
            n100.write_new(dest, "original")
            with self.assertRaises(n100.PortError):
                n100.write_new(dest, "replacement")
            self.assertEqual(dest.read_text(), "original")

    def test_never_follows_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("keep")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            with self.assertRaises(n100.PortError):
                n100.write_new(link, "keep")


class DeviceTests(unittest.TestCase):
    def test_n10_is_not_n100(self):
        self.assertEqual(n100.identify({"ro.product.device": "billie"}), "wrong-device-nord-n10")

    def test_vendor_codename_and_stock_alias(self):
        self.assertEqual(n100.identify({"ro.product.vendor.device": "billie2"}), "n100-candidate")
        self.assertEqual(n100.identify({"ro.product.device": "OnePlusN100"}), "n100-candidate")

    def test_model_only_is_not_verified(self):
        self.assertEqual(n100.identify({"ro.product.model": "BE2013"}), "model-only-unconfirmed")
        self.assertEqual(n100.identify({}), "unconfirmed")

    def test_probe_does_not_query_private_identifiers_or_write_to_device(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv == ["adb", "devices"]:
                return "List of devices attached\nexample\tdevice"
            if argv[-1] in ("ro.product.device", "ro.product.vendor.device"):
                return "billie2"
            return "fixture"
        with tempfile.TemporaryDirectory() as tmp, patch.object(n100, "run", fake_run):
            output = Path(tmp) / "report.json"
            n100.probe(type("Args", (), {"serial": None, "output": str(output)})())
            report = json.loads(output.read_text())
            self.assertEqual(report["identity"], "n100-candidate")
        self.assertNotIn("ro.serialno", str(calls))
        self.assertFalse(any(word in call for call in calls for word in ("reboot", "flash", "setprop", "su -c")))

    def test_probe_rejects_missing_or_multiple_devices(self):
        for devices in ("List of devices attached", "List of devices attached\na\tdevice\nb\tdevice"):
            with patch.object(n100, "run", return_value=devices), self.assertRaises(n100.PortError):
                n100.probe(type("Args", (), {"serial": None, "output": "/unused"})())


class BootTests(unittest.TestCase):
    def image(self, path, version=2, page=4096):
        header = bytearray(4096)
        header[:8] = b"ANDROID!"
        struct.pack_into("<I", header, 8, 1000)
        struct.pack_into("<I", header, 16, 1000)
        struct.pack_into("<II", header, 36, page, version)
        struct.pack_into("<I", header, 1644, 1660)
        struct.pack_into("<I", header, 1648, 1000)
        path.write_bytes(header + bytes(3 * 4096))

    def test_valid_v2_is_only_structurally_assessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "boot.img"
            self.image(image)
            result = n100.inspect_boot(image)
            self.assertEqual(result["header_version"], 2)
            self.assertEqual(result["assessment"], "structural-check-only-not-boot-validation")

    def test_truncated_image_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "boot.img"
            self.image(image)
            image.write_bytes(image.read_bytes()[:4096])
            with self.assertRaises(n100.PortError):
                n100.inspect_boot(image)

    def test_wrong_header_layout_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "boot.img"
            for version, page in ((3, 4096), (2, 2048)):
                self.image(image, version, page)
                with self.assertRaises(n100.PortError):
                    n100.inspect_boot(image)


class ManifestTests(unittest.TestCase):
    def test_manifest_matches_lock_without_path_collisions(self):
        lock = n100.read_lock()
        manifest = ET.parse(ROOT / "manifests/billie2.xml").getroot()
        for project in lock["projects"]:
            element = next(x for x in manifest.findall("project") if x.attrib["path"] == project["path"])
            self.assertEqual(element.attrib["revision"], project["revision"])
            self.assertEqual(element.attrib["name"], project["name"])
        paths = [x.attrib["path"] for x in manifest.findall("project")]
        self.assertEqual(len(paths), len(set(paths)))
        for project in lock["projects"] + lock["extensions"] + lock["packaging"]:
            self.assertRegex(project["revision"], r"^[0-9a-f]{40}$")


class SnapshotTests(unittest.TestCase):
    def test_real_git_package_setup_is_repeatable_and_preserves_edits(self):
        # Exercise gitlink creation and the archive-visible package snapshot,
        # using a local upstream repository instead of network or an SDK.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            upstream = tmp / "upstream"
            upstream.mkdir()
            subprocess.run(["git", "init", "-q", str(upstream)], check=True)
            (upstream / "macros.inc").write_text("fixture macro\n")
            subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
            subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Test", "-c",
                            "user.email=test@localhost", "commit", "-qm", "fixture"], check=True)
            revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
            source = tmp / "source"
            source.mkdir()
            (source / "device.spec").write_text("package fixture\n")
            (source / "policy").symlink_to("/vendor/etc/selinux/precompiled_sepolicy")
            dependency = {"parent": "rpm", "path": "dhd", "name": "local/fixture", "revision": revision}
            real_run = n100.run
            def local_run(argv, **kwargs):
                argv = [str(x) for x in argv]
                argv = [str(upstream) if x == "https://github.com/local/fixture.git" else x for x in argv]
                if argv[0] == "git":
                    argv[1:1] = ["-c", "protocol.file.allow=always"]
                return real_run(argv, **kwargs)
            dest = tmp / "installed"
            with patch.object(n100, "run", local_run):
                n100.init_package(dest, source, dependency)
                n100.init_package(dest, source, dependency)
                files = real_run(["git", "ls-files", "--stage"], cwd=dest, capture=True)
                self.assertIn("160000 " + revision, files)
                self.assertIn("120000", files)
                (dest / "device.spec").write_text("user edits\n")
                with self.assertRaises(n100.PortError):
                    n100.init_package(dest, source, dependency)
                self.assertEqual((dest / "device.spec").read_text(), "user edits\n")


if __name__ == "__main__":
    unittest.main()
