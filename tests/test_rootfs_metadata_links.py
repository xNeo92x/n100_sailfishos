# SPDX-License-Identifier: MIT
import importlib.util
import io
from pathlib import Path
import struct
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import n100
SPEC = importlib.util.spec_from_file_location("userdata_metadata_links", ROOT / "tools/make-userdata-image.py")
image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image)


class RootfsMetadataLinksTests(unittest.TestCase):
    def make_rootfs(self, path, hw_link="../usr/lib/hw-release", extra=None):
        binary = bytearray(64)
        binary[:6] = b"\x7fELF\x02\x01"
        struct.pack_into("<H", binary, 18, 183)
        files = {"usr/lib/os-release": b"ID=sailfishos\n", "usr/lib/hw-release": b"MER_HA_DEVICE=billie2\n",
                 "usr/lib/systemd/systemd": binary, "sbin/droid-hal-init": binary}
        with tarfile.open(path, "w:bz2") as archive:
            for name, data in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
            for name, target in {"etc/os-release": "/usr/lib/os-release", "etc/hw-release": hw_link,
                                 **(extra or {})}.items():
                member = tarfile.TarInfo(name)
                member.type = tarfile.SYMTYPE
                member.linkname = target
                archive.addfile(member)

    def test_version_package_relative_hw_release_and_absolute_os_release_links(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rootfs.tar.bz2"
            self.make_rootfs(path)
            self.assertEqual(image.inspect_rootfs(path)["members"], 6)

    def test_link_chain_is_resolved_inside_the_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rootfs.tar.bz2"
            self.make_rootfs(path, "../metadata/hardware", {"metadata/hardware": "/usr/lib/hw-release"})
            self.assertEqual(image.inspect_rootfs(path)["members"], 7)

    def test_cycles_escape_and_dangling_links_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rootfs.tar.bz2"
            for target in ("hw-release", "../../outside", "/not-in-this-archive"):
                with self.subTest(target=target):
                    self.make_rootfs(path, target)
                    with self.assertRaises(n100.PortError):
                        image.inspect_rootfs(path)


if __name__ == "__main__":
    unittest.main()
