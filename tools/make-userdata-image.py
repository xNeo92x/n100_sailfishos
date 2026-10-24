#!/usr/bin/env python3
"""Create an experimental ext4 userdata file from a built billie2 Sailfish rootfs.

This writes regular build files only. It does not flash a device or establish
compatibility with stock partitions, verified boot or the Android HAL.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile

import n100

ROOT_PATH = ".stowaways/sailfishos"
FEATURES = "none,has_journal,ext_attr,dir_index,filetype,extent,sparse_super,large_file,huge_file,flex_bg,dir_nlink,extra_isize,metadata_csum"


def archive_name(name):
    while name.startswith("./"):
        name = name[2:]
    name = name.rstrip("/")
    if name in ("", "."):
        return ""
    if name.startswith("/") or any(p in ("", ".", "..") for p in name.split("/")):
        raise n100.PortError("Unsafe archive path: " + name)
    return name


def inspect_rootfs(path):
    with tarfile.open(path, "r:*") as archive:
        members = {}
        total = 0
        for member in archive:
            name = archive_name(member.name)
            if not name:
                if not member.isdir():
                    raise n100.PortError("Invalid archive root")
                continue
            if name in members:
                raise n100.PortError("Duplicate rootfs archive member: " + name)
            if member.islnk():
                archive_name(member.linkname)
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()
                    or member.ischr() or member.isblk() or member.isfifo()):
                raise n100.PortError("Unsupported rootfs member: " + name)
            total += member.size
            if total > 16 * 1024**3 or len(members) >= 250000:
                raise n100.PortError("Rootfs archive exceeds build bounds")
            members[name] = member
        for name in members:
            for parent in PurePosixPath(name).parents:
                entry = members.get(str(parent))
                if entry and not entry.isdir():
                    raise n100.PortError("Archive writes through a link/non-directory: " + str(parent))
            entry = members[name]
            if entry.islnk():
                target = members.get(archive_name(entry.linkname))
                if target is None or not target.isfile():
                    raise n100.PortError("Hardlink must refer directly to a regular archive member")

        def read(name, limit=65536):
            member = members.get(name)
            if member is None or not member.isfile():
                raise n100.PortError("Required built rootfs file missing: " + name)
            with archive.extractfile(member) as stream:
                return stream.read(limit)

        release = read("etc/os-release").decode()
        hardware = read("etc/hw-release").decode()
        if not re.search(r'^ID=["\']?sailfishos["\']?$', release, re.MULTILINE):
            raise n100.PortError("Rootfs is not identified as Sailfish OS")
        if not re.search(r'^MER_HA_DEVICE=["\']?billie2["\']?$', hardware, re.MULTILINE):
            raise n100.PortError("Rootfs does not identify the billie2 adaptation")
        binaries = []
        for choices in (("usr/lib/systemd/systemd", "lib/systemd/systemd"), ("sbin/droid-hal-init",)):
            name = next((p for p in choices if p in members and members[p].isfile()), choices[0])
            data = read(name, 64)
            if len(data) < 20 or data[:6] != b"\x7fELF\x02\x01" or struct.unpack_from("<H", data, 18)[0] != 183:
                raise n100.PortError("Required runtime binary is not ELF64 AArch64: " + name)
            binaries.append(name)
        return {"unpacked_file_bytes": total, "members": len(members), "runtime_binaries": binaries,
                "scope": "identity, archive paths and binary headers; not a runtime validation"}


def make_ext4(tree, image, size):
    if image.exists() or image.is_symlink():
        raise n100.PortError("Image output already exists")
    # Open exclusively: neither a block device nor an existing file can be used.
    with image.open("xb") as stream:
        stream.truncate(size)
    n100.run(["mke2fs", "-q", "-t", "ext4", "-b", "4096", "-I", "256", "-m", "0",
              "-L", "sailfish-data", "-O", FEATURES,
              "-E", "lazy_itable_init=0,lazy_journal_init=0", "-d", tree, image, str(size // 4096)])
    n100.run(["e2fsck", "-f", "-n", image])


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def build(args):
    rootfs = Path(args.rootfs).expanduser().resolve(strict=True)
    boot = Path(args.boot).expanduser().resolve(strict=True)
    if not rootfs.is_file() or not boot.is_file():
        raise n100.PortError("Rootfs and boot inputs must be regular files")
    inspection = inspect_rootfs(rootfs)
    boot_info = n100.inspect_boot(boot)
    if os.geteuid() != 0:
        raise n100.PortError("Run image creation with sudo to preserve rootfs owners and capabilities")
    for tool in ("tar", "mke2fs", "e2fsck"):
        if not shutil.which(tool):
            raise n100.PortError("Missing image build tool: " + tool)
    output = Path(args.output_dir).expanduser().resolve()
    if output == n100.ROOT or n100.ROOT in output.parents:
        raise n100.PortError("Keep binary image outputs outside the source repository")
    if output.exists():
        raise n100.PortError("Use a new output directory; existing outputs are never overwritten")
    size = args.size_gib * 1024**3
    if size < inspection["unpacked_file_bytes"] + 2 * 1024**3:
        raise n100.PortError("Allow at least 2 GiB above the unpacked rootfs size")
    output.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(output.parent).free < size + inspection["unpacked_file_bytes"] + 1024**3:
        raise n100.PortError("Insufficient build space for image and unpacked rootfs")
    output.mkdir()
    with tempfile.TemporaryDirectory(prefix=".n100-image-", dir=output) as temporary:
        tree = Path(temporary)
        target = tree / ROOT_PATH
        target.mkdir(parents=True)
        n100.run(["tar", "--extract", "--file", rootfs, "--directory", target,
                  "--numeric-owner", "--same-owner", "--same-permissions", "--xattrs",
                  "--xattrs-include=*", "--acls"])
        # First boot stops in the normal hybris debug session; no automatic GUI claim.
        marker = target / "init_enter_debug"
        if marker.is_symlink():
            raise n100.PortError("Debug marker must not be a symlink")
        marker.touch()
        make_ext4(tree, output / "userdata.img", size)
    shutil.copyfile(boot, output / "hybris-boot.img")
    files = [output / "userdata.img", output / "hybris-boot.img"]
    manifest = {"schema": 1, "device": "billie2", "model": "BE2013", "kind": "experimental-userdata-ext4",
                "root_path": "/data/" + ROOT_PATH, "rootfs_input_sha256": sha256(rootfs),
                "rootfs_inspection": inspection, "boot_inspection": boot_info,
                "first_boot_debug": True, "boot_tested": False, "flashable_release": False,
                "replaces_android_userdata": True,
                "deployment_blockers": ["Confirm actual userdata partition capacity and stock boot/DTBO compatibility",
                    "Validate Android HAL payload, dynamic partitions, AVB and recovery/restore procedure",
                    "Complete first boot and hardware validation on the handset"],
                "files": [{"name": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files]}
    (output / "image-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "SHA256SUMS").write_text("".join(f"{r['sha256']}  {r['name']}\n" for r in manifest["files"]))
    print("Experimental files created in", output)
    print("Not validated for device installation. See image-manifest.json deployment blockers.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rootfs", required=True)
    parser.add_argument("--boot", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--size-gib", type=n100.positive, default=8)
    args = parser.parse_args()
    try:
        build(args)
    except (n100.PortError, OSError, ValueError, tarfile.TarError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
