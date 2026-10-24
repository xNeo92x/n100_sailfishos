import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n100", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class BundleTests(unittest.TestCase):
    def bundle(self, path, payloads, change=None, extra=None):
        records = [{"remote_path": name, "status": "collected", "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()} for name, data in payloads.items()]
        if change:
            change(records)
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("report.json", (ROOT / "tests/fixtures/be2013-stock11-v2.json").read_bytes())
            archive.writestr("inventory.json", json.dumps({"files": records}))
            for name, data in payloads.items():
                archive.writestr("files" + name, data)
            if extra:
                archive.writestr(*extra)

    def test_verified_bundle_keeps_fstab_variants_separate(self):
        payloads = {"/vendor/etc/" + name: (ROOT / "tests/fixtures" / name).read_bytes()
                    for name in ("fstab.default", "fstab.emmc", "fstab_nodata.qcom")}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "configs.zip"
            self.bundle(path, payloads)
            result = n100.inspect_config_bundle(path)
            self.assertEqual(result["verified_files"], 3)
            self.assertIsNone(result["active_fstab_variant"])
            self.assertFalse(result["deployable"])
            variants = result["fstab_variants"]
            data = {name: next(e for e in entries if e["target"] == "/data")
                    for name, entries in variants.items()}
            self.assertIn("fileencryption=ice", data["/vendor/etc/fstab.default"]["fs_mgr_flags"])
            self.assertIn("metadata_encryption=aes-256-xts:wrappedkey_v0",
                          data["/vendor/etc/fstab.emmc"]["fs_mgr_flags"])
            self.assertEqual(data["/vendor/etc/fstab_nodata.qcom"]["filesystem"], "tmpfs")
            self.assertEqual(list(Path(tmp).iterdir()), [path])  # never extracted

    def test_modified_size_or_hash_rejected(self):
        payloads = {"/vendor/etc/fstab.default": b"system /system ext4 ro wait,logical\n"}
        for key, wrong in (("bytes", 0), ("sha256", "0" * 64)):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "bad.zip"
                self.bundle(path, payloads, change=lambda records: records[0].update({key: wrong}))
                with self.assertRaisesRegex(n100.PortError, "hash or size mismatch"):
                    n100.read_config_bundle(path)

    def test_private_paths_and_unlisted_members_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            self.bundle(path, {"/data/private.xml": b"private"})
            with self.assertRaisesRegex(n100.PortError, "Unexpected configuration path"):
                n100.read_config_bundle(path)
            self.bundle(path, {}, extra=("../../outside.txt", b"no extraction"))
            with self.assertRaisesRegex(n100.PortError, "unlisted ZIP members"):
                n100.read_config_bundle(path)
            self.assertFalse((Path(tmp).parent / "outside.txt").exists())

    def test_duplicate_inventory_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            self.bundle(path, {"/vendor/etc/fstab.default": b""},
                        change=lambda records: records.append(dict(records[0])))
            with self.assertRaisesRegex(n100.PortError, "Duplicate configuration path"):
                n100.read_config_bundle(path)

    def test_symlinks_and_oversized_members_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            link = zipfile.ZipInfo("link")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            self.bundle(path, {}, extra=(link, b"/etc/passwd"))
            with self.assertRaisesRegex(n100.PortError, "Symlink ZIP"):
                n100.read_config_bundle(path)
            self.bundle(path, {}, extra=("oversized", b"x" * (8 * 1024 * 1024 + 1)))
            with self.assertRaisesRegex(n100.PortError, "size limits"):
                n100.read_config_bundle(path)

    def test_invalid_zip_is_controlled_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.zip"
            path.write_bytes(b"not a zip")
            with self.assertRaises(n100.PortError):
                n100.read_config_bundle(path)


class StockConfigParserTests(unittest.TestCase):
    def test_aidl_endpoint_without_explicit_version(self):
        xml = ('<manifest type="device"><hal format="aidl"><name>android.hardware.power</name>'
               '<fqname>IPower/default</fqname></hal></manifest>')
        hal = n100.vintf_inventory(xml)["hals"][0]
        self.assertEqual(hal["format"], "aidl")
        self.assertEqual(hal["versions"], [])
        self.assertEqual(hal["instances"], [{"interface": "IPower", "instance": "default"}])

    def test_policy_header_not_compatibility_string(self):
        header = struct.pack("<II8sIIII", 0xF97CFF8C, 8, b"SE Linux", 30, 0, 8, 8)
        self.assertEqual(n100.selinux_policy_header(header)["binary_policy_version"], 30)
        for bad in (b"30.0", b"x" * 32, header[:20]):
            with self.assertRaises(n100.PortError):
                n100.selinux_policy_header(bad)

    def test_fstab_comments_and_invalid_columns(self):
        entry = n100.parse_android_fstab("# comment\nsystem /system ext4 ro wait,logical # note\n")[0]
        self.assertEqual(entry["fs_mgr_flags"], ["wait", "logical"])
        for text in ("system /system ext4 ro", 'system /system ext4 ro "unterminated'):
            with self.assertRaises(n100.PortError):
                n100.parse_android_fstab(text)


if __name__ == "__main__":
    unittest.main()
