#!/usr/bin/env python3
"""Extract five pinned BE2013 stock images locally; never access a phone."""
import argparse
import bz2
from datetime import datetime, timezone
import hashlib
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import sys
import zipfile


PARTITIONS = ("boot", "dtbo", "recovery", "vbmeta", "vbmeta_system")
PROFILE = Path(__file__).resolve().parents[1] / "validation/stock-firmware-source-20261025.json"
MAX_PARTITION = 128 * 1024 * 1024
MAX_OPERATION = 8 * 1024 * 1024
MISSING = object()


def sha256(stream):
    digest = hashlib.sha256()
    while True:
        block = stream.read(1024 * 1024)
        if not block:
            return digest.hexdigest()
        digest.update(block)


def read_exact(stream, length):
    data = stream.read(length)
    if len(data) != length:
        raise ValueError("Truncated archive or payload")
    return data


def varint(data, offset):
    value = 0
    for shift in range(0, 70, 7):
        if offset >= len(data):
            raise ValueError("Truncated protobuf varint")
        byte = data[offset]
        offset += 1
        if shift == 63 and byte > 1:
            raise ValueError("Protobuf varint exceeds uint64")
        value |= (byte & 127) << shift
        if byte < 128:
            return value, offset
    raise ValueError("Invalid protobuf varint")


def fields(data):
    """Read protobuf wire fields; the pinned AOSP schema supplies field numbers."""
    result = {}
    offset = 0
    while offset < len(data):
        key, offset = varint(data, offset)
        tag, wire = key >> 3, key & 7
        if not 0 < tag < 2 ** 29:
            raise ValueError("Invalid protobuf field number")
        if wire == 0:
            value, offset = varint(data, offset)
        elif wire in (1, 2, 5):
            if wire == 2:
                length, offset = varint(data, offset)
            else:
                length = 8 if wire == 1 else 4
            if length > len(data) - offset:
                raise ValueError("Truncated protobuf field")
            value = data[offset:offset + length]
            offset += length
        else:
            raise ValueError("Unsupported protobuf wire type")
        result.setdefault(tag, []).append((wire, value))
    return result


def one(message, tag, wire, default=MISSING):
    values = message.get(tag)
    if values is None and default is not MISSING:
        return default
    if not values or len(values) != 1 or values[0][0] != wire:
        raise ValueError("Missing, duplicate or mistyped protobuf field: " + str(tag))
    return values[0][1]


def messages(message, tag):
    values = message.get(tag, [])
    if any(wire != 2 for wire, value in values):
        raise ValueError("Mistyped nested protobuf message")
    return [fields(value) for wire, value in values]


def partition_plan(partition, reference, block_size, data_size):
    if 6 in partition:
        raise ValueError("Stock extraction cannot use an old partition image")
    info = fields(one(partition, 7, 2))
    size, digest = one(info, 1, 0), one(info, 2, 2)
    if not 0 < size <= MAX_PARTITION or size % block_size or len(digest) != 32:
        raise ValueError("Invalid stock partition size or hash")
    if (size, digest.hex()) != (reference["size"], reference["sha256"]):
        raise ValueError("Partition does not match the pinned stock profile")
    operations = messages(partition, 8)
    if not 0 < len(operations) <= 4096:
        raise ValueError("Invalid operation count")
    plan, intervals = [], []
    for operation in operations:
        kind = one(operation, 1, 0)
        if kind not in (0, 1, 8) or any(tag in operation for tag in (4, 5, 9)):
            raise ValueError("Only source-independent REPLACE/BZ/XZ operations are supported")
        offset, length = one(operation, 2, 0, 0), one(operation, 3, 0)
        blob_hash = one(operation, 8, 2)
        if not 0 < length <= MAX_OPERATION or offset + length > data_size or len(blob_hash) != 32:
            raise ValueError("Operation data exceeds payload bounds or lacks a hash")
        extents = []
        for extent in messages(operation, 6):
            start, blocks = one(extent, 1, 0), one(extent, 2, 0)
            begin, count = start * block_size, blocks * block_size
            if blocks == 0 or begin + count > size:
                raise ValueError("Destination extent exceeds partition bounds")
            extents.append((begin, count))
            intervals.append((begin, begin + count))
        decoded_size = sum(count for begin, count in extents)
        if not 0 < decoded_size <= MAX_OPERATION:
            raise ValueError("Invalid decompressed operation size")
        if one(operation, 7, 0, decoded_size) != decoded_size:
            raise ValueError("Destination length disagrees with its extents")
        plan.append((kind, offset, length, blob_hash, extents, decoded_size))
    cursor = 0
    for begin, end in sorted(intervals):
        if begin != cursor:
            raise ValueError("Destination extents overlap or leave a gap")
        cursor = end
    if cursor != size:
        raise ValueError("Destination extents do not cover the stock image")
    return size, digest.hex(), plan


def decompress(kind, blob, expected):
    if kind == 0:
        decoded = blob
    else:
        decoder = (bz2.BZ2Decompressor() if kind == 1 else
                   lzma.LZMADecompressor(format=lzma.FORMAT_XZ, memlimit=256 * 1024 * 1024))
        decoded = decoder.decompress(blob, max_length=expected + 1)
        if not decoder.eof or decoder.unused_data:
            raise ValueError("Truncated, concatenated or oversized compressed operation")
    if len(decoded) != expected:
        raise ValueError("Decompressed operation has the wrong size")
    return decoded


def payload_location(stream, archive, info):
    if info.compress_type != zipfile.ZIP_STORED or info.compress_size != info.file_size:
        raise ValueError("This extractor requires an uncompressed payload.bin ZIP member")
    stream.seek(info.header_offset)
    header = struct.unpack("<4s5H3I2H", read_exact(stream, 30))
    if header[0] != b"PK\x03\x04" or header[2] & 1 or header[3] != 0:
        raise ValueError("Invalid or encrypted payload local ZIP header")
    name = read_exact(stream, header[9])
    read_exact(stream, header[10])
    if name != b"payload.bin":
        raise ValueError("Payload local/central ZIP names disagree")
    start = stream.tell()
    if start + info.file_size > archive:
        raise ValueError("Payload ZIP member exceeds archive bounds")
    return start


def extract(archive_path, output, profile):
    if output.exists() or output.is_symlink():
        raise ValueError("Output already exists; use a new directory")
    if not archive_path.is_file():
        raise ValueError("Archive must be a regular file")
    expected = profile["package"]
    with archive_path.open("rb") as stream:
        source_stat = os.fstat(stream.fileno())
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_size != expected["catalog_bytes"]:
            raise ValueError("Archive is not a regular file of the expected size")
        archive_hash = sha256(stream)
        if archive_hash != expected["full_zip_sha256"]:
            raise ValueError("Archive SHA-256 does not match the verified BE2013 package")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            names = archive.namelist()
            for name in ("META-INF/com/android/metadata", "payload.bin"):
                if names.count(name) != 1:
                    raise ValueError("Missing or duplicate ZIP member: " + name)
            metadata_info = archive.getinfo("META-INF/com/android/metadata")
            if metadata_info.file_size > 16384:
                raise ValueError("OTA metadata exceeds size limit")
            metadata = archive.read(metadata_info)
            if hashlib.sha256(metadata).hexdigest() != profile["ota_metadata"]["sha256"]:
                raise ValueError("OTA target metadata does not match the installed EEA build")
            info = archive.getinfo("payload.bin")
            payload_start = payload_location(stream, source_stat.st_size, info)
            stream.seek(payload_start)
            header = read_exact(stream, 24)
            magic, major, manifest_size, signature_size = struct.unpack(">4sQQI", header)
            if magic != b"CrAU" or major != 2 or manifest_size > 8 * 1024 * 1024 or signature_size > 65536:
                raise ValueError("Unsupported payload header")
            metadata_size = 24 + manifest_size + signature_size
            if metadata_size > info.file_size:
                raise ValueError("Payload metadata exceeds ZIP member")
            rest = read_exact(stream, manifest_size + signature_size)
            if hashlib.sha256(header + rest).hexdigest() != profile["payload"]["metadata_sha256"]:
                raise ValueError("Payload metadata hash does not match the pinned profile")
            manifest = fields(rest[:manifest_size])
            block_size = one(manifest, 3, 0, 4096)
            if block_size != 4096 or one(manifest, 12, 0, 0) != 0 or one(manifest, 16, 0, 0) != 0:
                raise ValueError("A full non-partial payload with 4096-byte blocks is required")
            partitions = {}
            for partition in messages(manifest, 13):
                name = one(partition, 1, 2).decode("ascii")
                if name in partitions:
                    raise ValueError("Duplicate payload partition")
                partitions[name] = partition
            references = {p["name"]: p for p in profile["payload"]["partitions"]}
            plans = {}
            for name in PARTITIONS:
                if name not in partitions or name not in references:
                    raise ValueError("Required stock partition missing: " + name)
                plans[name] = partition_plan(partitions[name], references[name], block_size,
                                             info.file_size - metadata_size)
            # No output is created until archive identity and all plans are checked.
            output.mkdir(mode=0o700)
            images = []
            for name in PARTITIONS:
                size, expected_hash, plan = plans[name]
                destination = output / (name + ".img")
                with destination.open("xb") as image:
                    image.truncate(size)
                    for kind, offset, length, blob_hash, extents, decoded_size in plan:
                        stream.seek(payload_start + metadata_size + offset)
                        blob = read_exact(stream, length)
                        if hashlib.sha256(blob).digest() != blob_hash:
                            raise ValueError("Compressed operation hash mismatch: " + name)
                        decoded = decompress(kind, blob, decoded_size)
                        cursor = 0
                        for begin, count in extents:
                            image.seek(begin)
                            image.write(decoded[cursor:cursor + count])
                            cursor += count
                with destination.open("rb") as image:
                    digest = sha256(image)
                if digest != expected_hash:
                    raise ValueError("Reconstructed stock image hash mismatch: " + name)
                images.append({"name": name, "file": name + ".img", "bytes": size,
                               "sha256": digest, "operation_hashes_verified": True,
                               "partition_hash_verified": True})
                print("Verified " + name + ".img: " + digest)
    result = {
        "schema": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
        "device": "OnePlus Nord N100 BE2013 EEA", "archive": str(archive_path.absolute()),
        "archive_bytes": source_stat.st_size, "archive_sha256": archive_hash,
        "ota_metadata_sha256": hashlib.sha256(metadata).hexdigest(),
        "images": images, "device_accessed": False, "oem_signature_verified": False,
        "restore_procedure_validated": False, "boot_tested": False,
    }
    with (output / "stock-images.json").open("x", encoding="utf-8") as report:
        json.dump(result, report, indent=2)
        report.write("\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        profile = json.loads(PROFILE.read_text())
        extract(args.archive, args.output, profile)
    except (OSError, ValueError, KeyError, struct.error, zipfile.BadZipFile, lzma.LZMAError) as error:
        print("Stock extraction failed: " + str(error), file=sys.stderr)
        print("If output was created, keep it as incomplete; use a new directory on retry.", file=sys.stderr)
        return 1
    print("Report: " + str((args.output / "stock-images.json").absolute()))
    print("Stock files extracted locally. No phone access or flash commands were performed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
