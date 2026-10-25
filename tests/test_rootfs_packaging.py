# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "adaptation/droid-configs/kickstart/pack_package-droid-updater"
SPEC = importlib.util.spec_from_file_location("n100_rootfs_pack", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(n100)
ADDITIONS = {"kickstart/pack_package-droid-updater", "kickstart/attachment_hybris"}


class RootfsPackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.archive = self.output / "sfe-billie2-5.1.0.11.tar.bz2"

    def create_archive(self):
        with tarfile.open(self.archive, "w:bz2") as archive:
            entry = tarfile.TarInfo("etc/os-release")
            data = b"ID=sailfishos\n"
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))

    def run_pack(self):
        return subprocess.run(["bash", str(PACK)], env=dict(os.environ, IMG_OUT_DIR=str(self.output)),
                              capture_output=True, text=True)

    def test_complete_archive_is_retained_and_hashed_without_recovery_files(self):
        self.create_archive()
        before = self.archive.read_bytes()
        result = self.run_pack()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertIn(hashlib.sha256(before).hexdigest(),
                      self.archive.with_suffix(".bz2.sha256").read_text())
        self.assertFalse((self.output / "updater").exists())
        self.assertFalse(list(self.output.glob("*.zip")))
        self.assertEqual(self.run_pack().returncode, 0)

    def test_corrupt_archive_fails_and_has_no_success_checksum(self):
        self.archive.write_bytes(b"not a bzip2 tar archive")
        self.assertNotEqual(self.run_pack().returncode, 0)
        self.assertFalse(self.archive.with_suffix(".bz2.sha256").exists())

    def test_missing_or_ambiguous_archives_are_rejected(self):
        self.assertNotEqual(self.run_pack().returncode, 0)
        self.create_archive()
        shutil.copyfile(self.archive, self.output / "sfe-billie2-other.tar.bz2")
        self.assertNotEqual(self.run_pack().returncode, 0)

    def test_checksum_symlink_is_preserved_and_rejected(self):
        self.create_archive()
        target = self.output / "preserve"
        target.write_text("keep")
        self.archive.with_suffix(".bz2.sha256").symlink_to(target)
        self.assertNotEqual(self.run_pack().returncode, 0)
        self.assertEqual(target.read_text(), "keep")


class ExistingConfigPackagingUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.android = Path(self.temp.name) / "android"
        self.destination = self.android / "hybris/droid-configs"
        self.source = ROOT / "adaptation/droid-configs"
        shutil.copytree(self.source, self.destination, symlinks=True)
        self.dependency = next(p for p in n100.read_lock()["packaging"]
                               if p["parent"] == "hybris/droid-configs")
        (self.destination / self.dependency["path"]).mkdir()
        self.marker = self.destination / ".n100-prepared.json"
        files = {}
        for path in self.source.rglob("*"):
            if path.is_file() or path.is_symlink():
                name = str(path.relative_to(self.source))
                if name in ADDITIONS:
                    (self.destination / name).unlink()
                else:
                    files[name] = ("symlink:" + os.readlink(path) if path.is_symlink()
                                   else hashlib.sha256(path.read_bytes()).hexdigest())
        self.marker.write_text(json.dumps({"dependency": self.dependency, "files": files}))
        (self.android / ".n100-source-profile.json").write_text(
            json.dumps({"lock_sha256": n100.lock_digest()}))
        self.args = SimpleNamespace(android_root=str(self.android))
        checkout = patch.object(n100, "require_checkout")
        checkout.start()
        self.addCleanup(checkout.stop)

    def test_real_pre_packaging_snapshot_upgrades_and_remains_idempotent(self):
        old = (self.destination / "rpm/droid-config-billie2.spec").read_bytes()
        self.assertEqual(n100.git_blob_identity(old), "343bd7aadd5eae6a028fe94cc1fdd5d08d971058")
        n100.prepare_config_spec(self.args)
        n100.prepare_config_spec(self.args)
        n100.init_package(self.destination, self.source, self.dependency)
        self.assertEqual((self.destination / "rpm/droid-config-billie2.spec").read_bytes(), old)
        for name in ADDITIONS:
            self.assertEqual((self.destination / name).read_bytes(), (self.source / name).read_bytes())

    def test_untracked_packaging_file_is_not_overwritten(self):
        path = self.destination / "kickstart/attachment_hybris"
        path.write_text("custom local attachment\n")
        with self.assertRaisesRegex(n100.PortError, "existing config packaging path"):
            n100.prepare_config_spec(self.args)
        self.assertEqual(path.read_text(), "custom local attachment\n")
        self.assertFalse((self.destination / "kickstart/pack_package-droid-updater").exists())

    def test_partial_unknown_snapshot_marker_is_rejected(self):
        marker = json.loads(self.marker.read_text())
        marker["files"]["kickstart/attachment_hybris"] = hashlib.sha256(b"custom").hexdigest()
        self.marker.write_text(json.dumps(marker))
        with self.assertRaisesRegex(n100.PortError, "snapshot metadata"):
            n100.prepare_config_spec(self.args)
