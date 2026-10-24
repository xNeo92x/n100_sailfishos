import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import firmware
import n100

spec = importlib.util.spec_from_file_location("make_image", ROOT / "tools/make-userdata-image.py")
image = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image)


class FirmwareTests(unittest.TestCase):
    def test_source_remaps_and_both_hashes_preserved(self):
        records = firmware.parse_list("# stock\n-vendor/odm/etc/audio:odm/etc/audio\n"
                                      "vendor/lib64/camera.so|" + "a" * 40 + "|" + "b" * 40)
        self.assertEqual(records["vendor/odm/etc/audio"]["destination"], "odm/etc/audio")
        self.assertEqual(records["vendor/lib64/camera.so"]["upstream_sha1"], ["a" * 40, "b" * 40])

    def test_private_traversal_and_shell_paths_rejected(self):
        for line in ("/vendor/lib/a.so", "vendor/../data/private", "data/user/secret", "vendor/a;BANG=1:../x",
                     "vendor/a$(id)", "vendor/a:odm/x:system/b", "vendor/a|not-a-hash"):
            with self.subTest(line=line), self.assertRaises(n100.PortError):
                firmware.parse_list(line)

    def test_resume_retries_only_unavailable_and_detects_tampering(self):
        text = "vendor/lib64/a.so\nvendor/lib64/b.so\n"
        wanted = firmware.parse_list(text)
        report = json.loads((ROOT / "tests/fixtures/be2013-stock11-v2.json").read_text())
        calls = []
        failed = [True]
        def pull(argv, **kwargs):
            self.assertEqual(argv[:4], ["adb", "-s", "test-transport", "pull"])
            self.assertNotIn("shell", argv)
            calls.append(argv[4])
            if argv[4].endswith("b.so") and failed[0]:
                return SimpleNamespace(returncode=1)
            Path(argv[5]).write_bytes(b"test blob")
            return SimpleNamespace(returncode=0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(firmware, "source_tree", return_value=({"revision": "a" * 40}, text, wanted)), \
             patch.object(n100, "collect_report", return_value=report), patch.object(shutil, "which", return_value="adb"), \
             patch.object(subprocess, "run", side_effect=pull):
            work = Path(tmp)
            with self.assertRaisesRegex(n100.PortError, "Incomplete dump"):
                firmware.collect(work, "test-transport")
            failed[0] = False
            firmware.collect(work, "test-transport")
            self.assertEqual(calls, ["/vendor/lib64/a.so", "/vendor/lib64/b.so", "/vendor/lib64/b.so"])
            inventory = (work / "firmware/inventory.json").read_text()
            self.assertNotIn("test-transport", inventory)
            (work / "firmware/files/vendor/lib64/a.so").write_bytes(b"modified")
            self.assertEqual(firmware.verify(work)["changed"], ["vendor/lib64/a.so"])

    def test_global_blob_pin_mismatch_requires_review(self):
        text = "vendor/lib/x.so|" + "a" * 40
        wanted = firmware.parse_list(text)
        with tempfile.TemporaryDirectory() as tmp, patch.object(firmware, "source_tree", return_value=({"revision": "a" * 40}, text, wanted)):
            work = Path(tmp)
            file = work / "firmware/files/vendor/lib/x.so"
            file.parent.mkdir(parents=True)
            file.write_bytes(b"EEA variant")
            inventory = {"device_revision": "a" * 40, "list_sha256": firmware.hashlib.sha256(text.encode()).hexdigest(),
                         "files": {"vendor/lib/x.so": {"status": "collected", "bytes": file.stat().st_size, "sha256": firmware.digest(file)}}}
            (work / "firmware/inventory.json").write_text(json.dumps(inventory))
            result = firmware.verify(work)
            self.assertTrue(result["complete"])
            self.assertFalse(result["ready_for_extraction"])
            self.assertEqual(result["upstream_pin_mismatches"], ["vendor/lib/x.so"])

    def test_no_symlink_in_local_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "vendor").symlink_to("/etc")
            with self.assertRaises(n100.PortError):
                firmware.checked_file(root, "vendor/passwd")


class ImageTests(unittest.TestCase):
    def rootfs(self, path, extra=None, model="billie2", machine=183):
        binary = bytearray(64)
        binary[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", binary, 18, machine)
        files = {"etc/os-release": b"ID=sailfishos\n", "etc/hw-release": f"MER_HA_DEVICE={model}\n".encode(),
                 "usr/lib/systemd/systemd": binary, "sbin/droid-hal-init": binary}
        with tarfile.open(path, "w:bz2") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            for member in extra or []:
                archive.addfile(member)

    def test_rootfs_identity_and_aarch64_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rootfs.tar.bz2"
            self.rootfs(path)
            self.assertEqual(image.inspect_rootfs(path)["members"], 4)
            for kwargs in ({"model": "billie"}, {"machine": 62}):
                self.rootfs(path, **kwargs)
                with self.assertRaises(n100.PortError):
                    image.inspect_rootfs(path)

    def test_archive_cannot_escape_staging_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rootfs.tar.bz2"
            for bad in ("../outside", "/etc/outside", "etc/../../outside"):
                self.rootfs(path, [tarfile.TarInfo(bad)])
                with self.assertRaises(n100.PortError):
                    image.inspect_rootfs(path)
            link = tarfile.TarInfo("escape")
            link.type = tarfile.SYMTYPE
            link.linkname = "/tmp/outside"
            self.rootfs(path, [link, tarfile.TarInfo("escape/file")])
            with self.assertRaises(n100.PortError):
                image.inspect_rootfs(path)
            hardlink = tarfile.TarInfo("hardlink")
            hardlink.type = tarfile.LNKTYPE
            hardlink.linkname = "escape/file"
            self.rootfs(path, [link, hardlink])
            with self.assertRaises(n100.PortError):
                image.inspect_rootfs(path)

    def test_absolute_runtime_symlinks_can_be_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rootfs.tar.bz2"
            link = tarfile.TarInfo("sbin/init")
            link.type = tarfile.SYMTYPE
            link.linkname = "/usr/lib/systemd/systemd"
            self.rootfs(path, [link])
            self.assertEqual(image.inspect_rootfs(path)["members"], 5)

    @unittest.skipUnless(all(shutil.which(x) for x in ("mke2fs", "e2fsck", "debugfs")), "e2fsprogs needed")
    def test_real_ext4_file_contains_stowaway_root_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tree = root / "tree"
            target = tree / image.ROOT_PATH
            target.mkdir(parents=True)
            (target / "marker").write_text("filesystem population check\n")
            output = root / "userdata.img"
            image.make_ext4(tree, output, 64 * 1024**2)
            text = subprocess.check_output(["debugfs", "-R", f"cat /{image.ROOT_PATH}/marker", str(output)], stderr=subprocess.DEVNULL, text=True)
            self.assertEqual(text, "filesystem population check\n")
            with self.assertRaises(n100.PortError):
                image.make_ext4(tree, output, 64 * 1024**2)


if __name__ == "__main__":
    unittest.main()
