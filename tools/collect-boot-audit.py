#!/usr/bin/env python3
"""Collect bounded local boot inputs for offline analysis; never access a phone."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
STOCK_REFERENCE = ROOT / "validation/stock-images-user-20261025.json"
HYBRIS_REFERENCE = ROOT / "validation/image-manifest-user-20261025.json"
MAX_BOOT = 128 * 1024 * 1024


def small_file(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError("Missing, symlinked or oversized input: " + str(path))
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Input grew beyond its size limit: " + str(path))
    return data


def verify_image(path, reference):
    if (path.is_symlink() or not path.is_file()
            or path.stat().st_size != reference["bytes"]
            or not 0 < reference["bytes"] <= MAX_BOOT):
        raise ValueError("Image size/type differs from its recorded input: " + str(path))
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        remaining = reference["bytes"]
        while remaining:
            data = stream.read(min(remaining, 1024 * 1024))
            if not data:
                raise ValueError("Truncated image: " + str(path))
            digest.update(data)
            remaining -= len(data)
        if stream.read(1):
            raise ValueError("Image grew during collection: " + str(path))
    if digest.hexdigest() != reference["sha256"]:
        raise ValueError("Image SHA-256 differs from its recorded input: " + str(path))


def stock_record(path, reference):
    record = json.loads(small_file(path, 65536))
    for key in ("device", "archive_bytes", "archive_sha256", "ota_metadata_sha256"):
        if record.get(key) != reference[key]:
            raise ValueError("Stock report identity differs: " + str(path))
    images = record.get("images")
    if (not isinstance(images, list) or any(not isinstance(item, dict) for item in images)
            or any(not isinstance(item.get("name"), str) for item in images)):
        raise ValueError("Invalid stock image inventory")
    expected = {item["name"]: item for item in reference["images"]}
    if len(images) != len(expected) or {item.get("name") for item in images} != set(expected):
        raise ValueError("Missing or duplicate stock image record")
    for item in images:
        for key in ("file", "bytes", "sha256", "operation_hashes_verified", "partition_hash_verified"):
            if item.get(key) != expected[item["name"]][key]:
                raise ValueError("Stock image record differs from the verified firmware")
    return record


def find_stock(work, explicit, reference):
    if explicit is not None:
        stock_record(explicit / "stock-images.json", reference)
        return explicit
    candidates = []
    for report in sorted(work.glob("stock-BE2013-*/stock-images.json")):
        if report.parent.is_symlink():
            continue
        try:
            stock_record(report, reference)
        except (OSError, ValueError, KeyError):
            continue
        candidates.append(report.parent)
    if len(candidates) != 1:
        raise ValueError("Expected one matching stock directory; found {}. Pass --stock-dir explicitly.".format(len(candidates)))
    return candidates[0]


def boot_sections(path):
    """Android boot header v2 layout from AOSP bootimg.h; copy bounded sections."""
    size = path.stat().st_size
    if not 4096 <= size <= MAX_BOOT:
        raise ValueError("Invalid boot image size")
    with path.open("rb") as stream:
        header = stream.read(4096)
        if len(header) != 4096 or header[:8] != b"ANDROID!":
            raise ValueError("Not an Android boot image")
        kernel, ramdisk, second = (struct.unpack_from("<I", header, offset)[0]
                                  for offset in (8, 16, 24))
        page, version = struct.unpack_from("<II", header, 36)
        recovery_dtbo = struct.unpack_from("<I", header, 1632)[0]
        dtb = struct.unpack_from("<I", header, 1648)[0]
        if version != 2 or page != 4096 or second or recovery_dtbo:
            raise ValueError("Expected boot v2 / 4096 pages without second-stage/recovery-DTBO sections")
        if not 0 < kernel <= MAX_BOOT or not 0 < ramdisk <= 8 * 1024 * 1024 or not 0 < dtb <= 4 * 1024 * 1024:
            raise ValueError("Invalid kernel, ramdisk or DTB section size")
        aligned = lambda length: (length + page - 1) // page * page
        ramdisk_offset = page + aligned(kernel)
        dtb_offset = ramdisk_offset + aligned(ramdisk)
        if dtb_offset + aligned(dtb) > size:
            raise ValueError("Truncated boot image sections")
        stream.seek(page)
        kernel_header = stream.read(min(kernel, 64))
        stream.seek(ramdisk_offset)
        ramdisk_data = stream.read(ramdisk)
        stream.seek(dtb_offset)
        dtb_data = stream.read(dtb)
        if len(ramdisk_data) != ramdisk or len(dtb_data) != dtb:
            raise ValueError("Boot image changed during collection")
    cmdline = (header[64:576].split(b"\0", 1)[0]
               + header[608:1632].split(b"\0", 1)[0]).decode("ascii", errors="replace")
    compression = ("gzip" if ramdisk_data.startswith(b"\x1f\x8b") else
                   "lz4" if ramdisk_data[:4] in (b"\x02\x21\x4c\x18", b"\x04\x22\x4d\x18") else
                   "cpio-newc" if ramdisk_data.startswith((b"070701", b"070702")) else "unknown")
    summary = {"header_version": version, "page_size": page, "bytes": size,
               "kernel_bytes": kernel, "ramdisk_bytes": ramdisk, "dtb_bytes": dtb,
               "cmdline": cmdline, "ramdisk_compression": compression,
               "dtb_has_fdt_magic": dtb_data.startswith(b"\xd0\x0d\xfe\xed"),
               "scope": "section extraction and structural inspection only"}
    return summary, ramdisk_data, dtb_data, kernel_header


def collect(hybris, stock, android, output, stock_reference, hybris_reference):
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; choose a new ZIP filename")
    record = stock_record(stock / "stock-images.json", stock_reference)
    # Fixed expected filenames are used; report-provided paths are never followed.
    for item in stock_reference["images"]:
        verify_image(stock / (item["name"] + ".img"), item)
    hybris_pin = next(item for item in hybris_reference["files"] if item["name"] == "hybris-boot.img")
    verify_image(hybris, hybris_pin)
    built_header, ramdisk, built_dtb, kernel_header = boot_sections(hybris)
    stock_header, unused_ramdisk, stock_dtb, unused_kernel = boot_sections(stock / "boot.img")
    product = android / "out/target/product/billie2"
    config = small_file(product / "obj/KERNEL_OBJ/.config", 2 * 1024 * 1024)
    members = {
        "hybris/ramdisk.bin": ramdisk,
        "hybris/dtb.img": built_dtb,
        "hybris/kernel-header.bin": kernel_header,
        "stock/dtb.img": stock_dtb,
        "stock/dtbo.img": small_file(stock / "dtbo.img", 32 * 1024 * 1024),
        "kernel.config": config,
    }
    built_dtbo = product / "dtbo.img"
    if built_dtbo.exists() or built_dtbo.is_symlink():
        members["hybris/dtbo.img"] = small_file(built_dtbo, 32 * 1024 * 1024)
    inventory = [{"file": name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                 for name, data in sorted(members.items())]
    audit = {
        "schema": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
        "device": "OnePlus Nord N100 BE2013 EEA", "stock_directory": str(stock.absolute()),
        "hybris_boot_path": str(hybris.absolute()), "hybris_boot_sha256": hybris_pin["sha256"],
        "stock_archive_sha256": record["archive_sha256"],
        "stock_images": record["images"], "stock_boot": stock_header, "hybris_boot": built_header,
        "members": inventory, "built_dtbo_available": "hybris/dtbo.img" in members,
        "kernel_config_provenance": "Current Android output .config; not proven to be embedded in this kernel",
        "device_accessed": False, "boot_tested": False, "restore_procedure_validated": False,
        "dtbo_application_tested": False, "ramdisk_contents_reviewed": False,
    }
    members["audit.json"] = (json.dumps(audit, indent=2) + "\n").encode()
    # All inputs are verified before exclusively creating the output ZIP.
    with output.open("xb") as stream:
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for name, data in sorted(members.items()):
                archive.writestr(name, data)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--stock-dir", type=Path)
    parser.add_argument("--android-root", type=Path, required=True)
    parser.add_argument("--hybris-boot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        stock_reference = json.loads(STOCK_REFERENCE.read_text())
        hybris_reference = json.loads(HYBRIS_REFERENCE.read_text())
        stock = find_stock(args.work_dir, args.stock_dir, stock_reference)
        collect(args.hybris_boot, stock, args.android_root, args.output, stock_reference, hybris_reference)
    except (OSError, ValueError, KeyError, StopIteration, struct.error, zipfile.BadZipFile) as error:
        print("Boot audit collection failed: " + str(error), file=sys.stderr)
        return 1
    print("Stock directory: " + str(stock.absolute()))
    print("Boot audit ZIP: " + str(args.output.absolute()))
    print("Upload this ZIP for DTB/DTBO and initramfs analysis. No phone access was performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
