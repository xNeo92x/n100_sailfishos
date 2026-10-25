#!/usr/bin/env python3
"""Prepare and verify an N100 early USB debug boot; no phone access or flashing."""
import argparse
import ctypes
import ctypes.util
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import struct
import tempfile
import zlib
import zipfile

import n100


ROOT = Path(__file__).resolve().parents[1]
INIT_BLOB = "79e39d9a02ce2205a71ec440faa012d63382aaa3"
LEGACY_DEFCONFIG_SHA256 = "ec1623f53b2990076f53018d72e17874e2f771e15b3ccd5a4ba3f17ba0aa4661"
EARLY_MARKER = b"N100_EARLY_DEBUG_V1"
LIMIT = 64 * 1024 * 1024


def patched_init(original):
    begin = original.index("usb_setup_configfs() {")
    end = original.index("# This sets up the USB with whatever USB_FUNCTIONS are set to via android_usb", begin)
    usb = (ROOT / "adaptation/initramfs/usb-configfs.sh").read_text().rstrip() + "\n\n"
    text = original[:begin] + usb + original[end:]
    begin = text.index("# This determines which USB setup method is going to be used")
    end = text.index("# This lets us communicate errors to host", begin)
    text = text[:begin] + text[end:]
    begin = text.index("\t# During debug we export mmc too")
    end = text.index("\n\trun_debug_session", begin)
    text = text[:begin] + "\tUSB_FUNCTIONS=rndis\n" + text[end:]
    anchor = "    mount_stowaways\n\n    # No target debug"
    if text.count(anchor) != 1:
        raise n100.PortError("Unexpected initramfs storage/debug sequence")
    early = (ROOT / "adaptation/initramfs/early-debug.sh").read_text().rstrip() + "\n\n"
    return text.replace(anchor, early + anchor)


def prepare(android):
    android = n100.android_path(android)
    n100.require_profile(android)
    lock = n100.read_lock()
    for path in ("kernel/oneplus/sm4250", "hybris/hybris-boot"):
        pin = next(p for p in lock["projects"] + lock["extensions"] if p["path"] == path)
        n100.require_checkout(android / path, pin["revision"])
    kernel = android / "kernel/oneplus/sm4250"
    base_name = "arch/arm64/configs/" + lock["device_changes"]["kernel_base_config"]
    if n100.run(["git", "diff", "--", base_name], cwd=kernel, capture=True):
        raise n100.PortError("Pinned base kernel defconfig has local edits")
    desired_config = n100.merge_config((kernel / base_name).read_text(),
                                        (ROOT / "kernel/sailfish.config").read_text())
    boot = android / "hybris/hybris-boot"
    original = n100.run(["git", "show", "HEAD:init-script"], cwd=boot, capture=True) + "\n"
    if n100.git_blob_identity(original.encode()) != INIT_BLOB:
        raise n100.PortError("Unexpected pinned hybris init-script")
    desired_init = patched_init(original)
    generated = kernel / "arch/arm64/configs" / lock["device_changes"]["kernel_generated_config"]
    plans = [(generated, desired_config.encode(), {LEGACY_DEFCONFIG_SHA256}),
             (boot / "init-script", desired_init.encode(), {hashlib.sha256(original.encode()).hexdigest()})]
    marker = {"schema": 1, "kernel_config_sha256": hashlib.sha256(desired_config.encode()).hexdigest(),
              "init_script_sha256": hashlib.sha256(desired_init.encode()).hexdigest(),
              "early_debug_cmdline": "n100_debug=1", "device_accessed": False, "boot_tested": False}
    plans.append((android / ".n100-debug-inputs.json", (json.dumps(marker, indent=2) + "\n").encode(), set()))
    checked = []
    # Check every source before changing any file. Unknown local edits are preserved.
    for path, wanted, legacy in plans:
        if path.is_symlink() or (path.exists() and (not path.is_file() or path.stat().st_size > 2 * 1024 * 1024)):
            raise n100.PortError("Unsafe debug input destination: " + str(path))
        current = path.read_bytes() if path.exists() else None
        if current is not None and current != wanted and hashlib.sha256(current).hexdigest() not in legacy:
            raise n100.PortError("Debug input has unknown edits; preserved: " + str(path))
        checked.append((path, wanted, current))
    for path, wanted, current in checked:
        if current == wanted:
            continue
        if (path.read_bytes() if path.exists() else None) != current:
            raise n100.PortError("Debug input changed during preparation: " + str(path))
        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".n100-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(wanted)
        try:
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print("LZ4 kernel configuration and early RNDIS-only debug init prepared.")


def unpack_ramdisk(data):
    if data.startswith(b"\x1f\x8b"):
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = decoder.decompress(data, LIMIT + 1)
        if len(output) > LIMIT or not decoder.eof or decoder.unused_data:
            raise n100.PortError("Invalid or oversized gzip initramfs")
        return output, "CONFIG_RD_GZIP"
    if data[:4] != bytes.fromhex("02214c18"):
        raise n100.PortError("Expected gzip or legacy LZ4 initramfs")
    name = ctypes.util.find_library("lz4")
    if not name:
        raise n100.PortError("Legacy LZ4 verification needs liblz4.so.1 (Ubuntu package liblz4-1)")
    library = ctypes.CDLL(name)
    library.LZ4_decompress_safe.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    library.LZ4_decompress_safe.restype = ctypes.c_int
    output, offset = bytearray(), 4
    while offset < len(data):
        if offset + 4 > len(data):
            raise n100.PortError("Truncated LZ4 block size")
        size = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if not 0 < size <= 9 * 1024 * 1024 or offset + size > len(data):
            raise n100.PortError("Invalid LZ4 block bounds")
        block = data[offset:offset + size]
        offset += size
        destination = ctypes.create_string_buffer(8 * 1024 * 1024)
        count = library.LZ4_decompress_safe(block, destination, size, len(destination))
        if count < 0 or len(output) + count > LIMIT:
            raise n100.PortError("Invalid or oversized LZ4 initramfs")
        output.extend(destination.raw[:count])
    return bytes(output), "CONFIG_RD_LZ4"


def cpio_members(data):
    members, offset = {}, 0
    while offset + 110 <= len(data):
        header = data[offset:offset + 110]
        if header[:6] != b"070701":
            raise n100.PortError("Expected newc initramfs without CRC extension")
        try:
            values = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        except ValueError as error:
            raise n100.PortError("Invalid cpio header") from error
        offset += 110
        size, namesize = values[6], values[11]
        if not 0 < namesize <= 4096 or offset + namesize > len(data) or data[offset + namesize - 1] != 0:
            raise n100.PortError("Invalid cpio name")
        name = data[offset:offset + namesize - 1].decode()
        offset = (offset + namesize + 3) // 4 * 4
        if offset + size > len(data):
            raise n100.PortError("Truncated cpio member")
        content = data[offset:offset + size]
        offset = (offset + size + 3) // 4 * 4
        if name == "TRAILER!!!":
            return members
        if name.startswith("./"):
            name = name[2:]
        if PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or name in members:
            raise n100.PortError("Unsafe or duplicate cpio member")
        members[name] = (values[1], content)
        if len(members) > 4096:
            raise n100.PortError("Too many initramfs members")
    raise n100.PortError("Missing cpio trailer")


def verify_ramdisk(data, config):
    raw, required = unpack_ramdisk(data)
    values = n100.parse_config(config)
    for key in ("CONFIG_BLK_DEV_INITRD", required, "CONFIG_DEVTMPFS", "CONFIG_DEVTMPFS_MOUNT",
                "CONFIG_CONFIGFS_FS", "CONFIG_USB_GADGET", "CONFIG_USB_CONFIGFS", "CONFIG_USB_CONFIGFS_RNDIS"):
        if values.get(key) != "y":
            raise n100.PortError("Debug kernel requires " + key + "=y")
    members = cpio_members(raw)
    mode, init = members.get("init", (0, b""))
    if not stat.S_ISREG(mode) or not mode & 0o111 or not init.startswith(b"#!/bin/sh\n") or EARLY_MARKER not in init:
        raise n100.PortError("Actual ramdisk lacks the executable N100 early-debug init")
    usb = (ROOT / "adaptation/initramfs/usb-configfs.sh").read_bytes().strip()
    early = (ROOT / "adaptation/initramfs/early-debug.sh").read_bytes().strip()
    if usb not in init or early not in init:
        raise n100.PortError("Actual ramdisk does not contain the expected debug guards")
    storage_call = b"    mount_stowaways\n\n    # No target debug"
    if init.count(early) != 1 or init.count(storage_call) != 1 or init.index(early) > init.index(storage_call):
        raise n100.PortError("Early debug guard must precede the userdata mount call")
    mode, busybox = members.get("bin/busybox", (0, b""))
    if not stat.S_ISREG(mode) or not mode & 0o111 or len(busybox) < 64 or busybox[:7] != b"\x7fELF\x02\x01\x01":
        raise n100.PortError("Initramfs BusyBox is not executable ELF64 little-endian")
    if struct.unpack_from("<H", busybox, 18)[0] != 183:
        raise n100.PortError("Initramfs BusyBox is not AArch64")
    phoff, phsize, phcount = struct.unpack_from("<Q", busybox, 32)[0], *struct.unpack_from("<HH", busybox, 54)
    if not 0 < phcount <= 1024 or phsize < 56 or phoff < 64 or phoff + phsize * phcount > len(busybox):
        raise n100.PortError("Invalid BusyBox program headers")
    if not any(struct.unpack_from("<I", busybox, phoff + i * phsize)[0] == 1 for i in range(phcount)):
        raise n100.PortError("BusyBox has no loadable ELF segment")
    if any(struct.unpack_from("<I", busybox, phoff + i * phsize)[0] == 3 for i in range(phcount)):
        raise n100.PortError("Initramfs BusyBox depends on an ELF interpreter")
    if members.get("bin/sh", (0, b"")) != (0o120777, b"busybox"):
        raise n100.PortError("Unexpected initramfs /bin/sh link")
    return {"compression_config": required, "unpacked_bytes": len(raw), "members": len(members),
            "init_sha256": hashlib.sha256(init).hexdigest(), "busybox_sha256": hashlib.sha256(busybox).hexdigest(),
            "early_debug_guard_verified": True, "busybox_aarch64_without_interpreter": True}


def dtbo_content_identity(data):
    if len(data) < 32:
        raise n100.PortError("Truncated DTBO table")
    magic, total, header, entry_size, count, entries_offset, page, version = struct.unpack_from(">8I", data)
    if (magic != 0xd7b7ab1e or version != 0 or header != 32 or entry_size != 32
            or not 0 < count <= 32 or entries_offset < header
            or entries_offset + count * entry_size > total or total > len(data)):
        raise n100.PortError("Invalid DTBO table bounds or format")
    entries = []
    for index in range(count):
        entry = struct.unpack_from(">8I", data, entries_offset + index * entry_size)
        size, offset = entry[:2]
        if not size or offset < entries_offset + count * entry_size or offset + size > total:
            raise n100.PortError("DTBO overlay exceeds table bounds")
        overlay = data[offset:offset + size]
        if not overlay.startswith(bytes.fromhex("d00dfeed")):
            raise n100.PortError("DTBO entry lacks FDT magic")
        entries.append({"bytes": size, "offset": offset, "id": entry[2], "revision": entry[3],
                        "custom": list(entry[4:]), "sha256": hashlib.sha256(overlay).hexdigest()})
    has_footer = len(data) >= 64 and data[-64:-60] == b"AVBf"
    footer = None
    if has_footer:
        unused, major, minor, original, vbmeta_offset, vbmeta_size, unused_padding = struct.unpack(
            ">4sIIQQQ28s", data[-64:])
        if (major != 1 or total > original or original > vbmeta_offset
                or vbmeta_size < 256 or vbmeta_offset + vbmeta_size > len(data) - 64
                or data[vbmeta_offset:vbmeta_offset + 4] != b"AVB0"):
            raise n100.PortError("Invalid AVB footer bounds")
        footer = {"major": major, "minor": minor, "original_image_bytes": original,
                  "vbmeta_offset": vbmeta_offset, "vbmeta_bytes": vbmeta_size}
    return {"image_bytes": len(data), "image_sha256": hashlib.sha256(data).hexdigest(),
            "table_bytes": total, "table_sha256": hashlib.sha256(data[:total]).hexdigest(),
            "entries": entries, "has_avb_footer": has_footer, "avb_footer": footer,
            "avb_signature_verified": False}


def review_changed_pair(output, dtb_data, dtbo_data, identity, details):
    bundle = output.with_suffix(output.suffix + ".review.zip")
    report = {"reason": "Device-tree content differs from the applied/ reviewed pair",
              "actual_dtb_sha256": hashlib.sha256(dtb_data).hexdigest(),
              "dtbo": identity, "differences": details, "device_accessed": False, "boot_tested": False}
    with bundle.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("review.json", json.dumps(report, indent=2) + "\n")
            archive.writestr("dtb.img", dtb_data)
            archive.writestr("dtbo.img", dtbo_data[:identity["table_bytes"]])
    raise n100.PortError("Built DTB/DTBO content changed; no debug image created. Upload " + str(bundle.absolute()))


def pack(image, config_path, dtbo, output):
    report_path = output.with_suffix(output.suffix + ".json")
    if any(path.exists() or path.is_symlink() for path in (output, report_path)):
        raise n100.PortError("Debug output exists; use a new filename")
    if not image.is_file() or not dtbo.is_file() or not config_path.is_file():
        raise n100.PortError("Expected regular boot, DTBO and kernel configuration files")
    if dtbo.stat().st_size > 32 * 1024 * 1024 or config_path.stat().st_size > 2 * 1024 * 1024:
        raise n100.PortError("Oversized DTBO or kernel configuration")
    inspection = n100.inspect_boot(image)
    data = bytearray(image.read_bytes())
    source_hash = hashlib.sha256(data).hexdigest()
    config_data = config_path.read_bytes()
    if data[-64:-60] == b"AVBf":
        raise n100.PortError("Refusing to modify a boot image with an AVB footer")
    page = inspection["page_size"]
    align = lambda size: (size + page - 1) // page * page
    if struct.unpack_from("<I", data, 24)[0] or struct.unpack_from("<I", data, 1632)[0]:
        raise n100.PortError("Unexpected second-stage or recovery-DTBO section")
    ramdisk_offset = page + align(inspection["kernel_bytes"])
    ramdisk = bytes(data[ramdisk_offset:ramdisk_offset + inspection["ramdisk_bytes"]])
    ramdisk_check = verify_ramdisk(ramdisk, config_data.decode())
    dtb_offset = ramdisk_offset + align(inspection["ramdisk_bytes"])
    dtb_data = bytes(data[dtb_offset:dtb_offset + inspection["dtb_bytes"]])
    reference = json.loads((ROOT / "validation/boot-audit-report-user-20261025.json").read_text())
    expected = {entry["file"]: entry["sha256"] for entry in reference["members"]}
    dtbo_data = dtbo.read_bytes()
    identity = dtbo_content_identity(dtbo_data)
    reviewed = json.loads((ROOT / "validation/boot-dtbo-content-20261025.json").read_text())
    differences = []
    if hashlib.sha256(dtb_data).hexdigest() != expected["hybris/dtb.img"]:
        differences.append("Base DTB bytes changed")
    for key in ("image_bytes", "table_bytes", "table_sha256", "entries", "has_avb_footer"):
        if identity[key] != reviewed[key]:
            differences.append("DTBO " + key + " changed")
    if differences:
        review_changed_pair(output, dtb_data, dtbo_data, identity, differences)
    cmdline = inspection["cmdline"].split()
    if data[608:1632].split(b"\0", 1)[0]:
        raise n100.PortError("This N100 debug packer requires the command line to fit the primary field")
    if any(item.startswith("n100_debug=") for item in cmdline):
        raise n100.PortError("Input already has a debug-mode command line")
    encoded = (" ".join(cmdline + ["n100_debug=1"])).encode("ascii")
    if len(encoded) >= 512:
        raise n100.PortError("Debug command line is too long")
    data[64:576] = encoded[:511].ljust(512, b"\0")
    data[608:1632] = encoded[511:].ljust(1024, b"\0")
    with output.open("xb") as stream:
        stream.write(data)
    result = {"schema": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
              "kind": "early-usb-debug-only", "image": str(output.absolute()), "bytes": len(data),
              "sha256": hashlib.sha256(data).hexdigest(), "cmdline": encoded.decode(),
              "source_boot_sha256": source_hash,
              "kernel_config_sha256": hashlib.sha256(config_data).hexdigest(),
              "ramdisk": ramdisk_check, "dtb_sha256": hashlib.sha256(dtb_data).hexdigest(),
              "matching_dtbo": {"path": str(dtbo.absolute()), "bytes": len(dtbo_data), **identity,
                                "sha256": identity["image_sha256"],
                                "whole_image_matches_previous": identity["image_sha256"] == expected["hybris/dtbo.img"],
                                "reviewed_content_matches": True},
              "matching_dtbo_required": True, "automatic_userdata_mount": False,
              "restore_procedure_validated": False,
              "stock_dtbo_applies_to_built_dtb": False, "device_accessed": False,
              "boot_tested": False, "flashable_release": False,
              "scope": "Debug flag enters USB shell before mount_stowaways; no userdata mount or rootfs boot"}
    with report_path.open("x") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print("Debug boot image: " + str(output.absolute()))
    print("Build report: " + str(report_path.absolute()))
    print("Requires matching built DTBO and an unlocked bootloader; no phone commands performed.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("prepare")
    item.add_argument("--android-root", required=True)
    item = sub.add_parser("pack")
    for argument in ("image", "kernel-config", "dtbo", "output"):
        item.add_argument("--" + argument, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(args.android_root)
        else:
            pack(args.image, args.kernel_config, args.dtbo, args.output)
    except (n100.PortError, OSError, ValueError, KeyError, StopIteration, struct.error) as error:
        parser.exit(1, "error: " + str(error) + "\n")


if __name__ == "__main__":
    main()
