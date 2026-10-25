import bz2
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import lzma
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "stock_extract", Path(__file__).resolve().parents[1] / "tools/extract-stock-boot.py")
stock = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stock)


def vi(value):
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def integer(tag, value):
    return vi(tag << 3) + vi(value)


def binary(tag, value):
    return vi((tag << 3) | 2) + vi(len(value)) + value


def fixture(folder, fault=None):
    """Build an actual ZIP/full-payload fixture with all five stock partitions."""
    payload_blobs = bytearray()
    partition_messages, references, wanted = [], [], {}
    for name in stock.PARTITIONS:
        blocks = [bytes([65 + len(wanted)]) * 4096]
        kinds = [8]
        if name == "boot":
            blocks = [b'A' * 4096, b'B' * 4096, b'C' * 4096]
            kinds = [0, 1, 8]
        original = b''.join(blocks)
        expected_hash = hashlib.sha256(original).digest()
        if fault == "partition_hash" and name == "boot":
            expected_hash = b'\xff' * 32
        operations = []
        for index, (block, kind) in enumerate(zip(blocks, kinds)):
            data = block + b'X' * 4096 if fault == "expansion" and name == "dtbo" else block
            blob = {0: lambda value: value, 1: bz2.compress, 8: lzma.compress}[kind](data)
            offset = len(payload_blobs)
            blob_hash = hashlib.sha256(blob).digest()
            if fault == "blob_hash" and name == "dtbo":
                blob_hash = b'\xff' * 32
            payload_blobs.extend(blob)
            start = index
            if fault == "overlap" and name == "boot" and index == 1:
                start = 0
            if fault == "gap" and name == "boot" and index == 1:
                start = 2
            if fault == "source_copy" and name == "boot":
                kind = 4
            extent = integer(1, start) + integer(2, 1)
            operation = (integer(1, kind) + integer(2, offset) + integer(3, len(blob))
                         + binary(6, extent) + binary(8, blob_hash))
            if fault == "source_extents" and name == "boot":
                operation += binary(4, extent)
            operations.append(binary(8, operation))
        info = integer(1, len(original)) + binary(2, expected_hash)
        partition = binary(1, name.encode()) + binary(7, info) + b''.join(operations)
        if fault == "old_partition" and name == "boot":
            partition += binary(6, info)
        partition_messages.append(binary(13, partition))
        references.append({"name": name, "size": len(original), "sha256": expected_hash.hex()})
        wanted[name] = original
    minor = 1 if fault == "incremental" else 0
    partial = 1 if fault == "partial" else 0
    manifest = integer(3, 4096) + integer(12, minor) + integer(16, partial) + b''.join(partition_messages)
    header = struct.pack('>4sQQI', b'CrAU', 2, len(manifest), 0)
    payload_metadata = header + manifest
    metadata = b'ota-id=test-EEA\npost-build-incremental=test\n'
    archive = folder / "stock.zip"
    with zipfile.ZipFile(archive, 'w') as zf:
        zf.writestr('META-INF/com/android/metadata', metadata)
        zf.writestr('payload.bin', payload_metadata + payload_blobs)
        # Unselected paths are never extracted, regardless of their ZIP names.
        zf.writestr('../outside.txt', 'must not be extracted')
    profile = {
        "package": {"catalog_bytes": archive.stat().st_size,
                    "full_zip_sha256": hashlib.sha256(archive.read_bytes()).hexdigest()},
        "ota_metadata": {"sha256": hashlib.sha256(metadata).hexdigest()},
        "payload": {"metadata_sha256": hashlib.sha256(payload_metadata).hexdigest(),
                    "partitions": references},
    }
    return archive, profile, wanted


class StockExtractionTests(unittest.TestCase):
    def test_full_zip_extracts_all_five_with_replace_bz_and_xz(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, profile, wanted = fixture(root)
            output = root / 'out'
            with contextlib.redirect_stdout(io.StringIO()):
                result = stock.extract(archive, output, profile)
            self.assertEqual({p.name for p in output.iterdir()},
                             {name + '.img' for name in wanted} | {'stock-images.json'})
            for name, original in wanted.items():
                self.assertEqual((output / (name + '.img')).read_bytes(), original)
            self.assertFalse((root / 'outside.txt').exists())
            self.assertEqual(result, json.loads((output / 'stock-images.json').read_text()))
            self.assertFalse(result['device_accessed'])
            self.assertFalse(result['restore_procedure_validated'])

    def test_identity_failures_create_no_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, profile, wanted = fixture(root)
            for section, key in [('package', 'catalog_bytes'), ('package', 'full_zip_sha256'),
                                 ('ota_metadata', 'sha256'), ('payload', 'metadata_sha256')]:
                changed = copy.deepcopy(profile)
                changed[section][key] = 1 if key == 'catalog_bytes' else '0' * 64
                with self.subTest(key=key), self.assertRaises(ValueError):
                    stock.extract(archive, root / 'out', changed)
                self.assertFalse((root / 'out').exists())

    def test_incremental_partial_source_operations_and_bad_extents_refused(self):
        for fault in ('incremental', 'partial', 'old_partition', 'source_copy',
                      'source_extents', 'overlap', 'gap'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, profile, wanted = fixture(root, fault)
                with self.assertRaises(ValueError):
                    stock.extract(archive, root / 'out', profile)
                self.assertFalse((root / 'out').exists())

    def test_hash_or_decompression_failure_never_produces_success_report(self):
        for fault in ('blob_hash', 'partition_hash', 'expansion'):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive, profile, wanted = fixture(root, fault)
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                    stock.extract(archive, root / 'out', profile)
                self.assertFalse((root / 'out/stock-images.json').exists())

    def test_existing_directory_and_symlink_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive, profile, wanted = fixture(root)
            existing = root / 'existing'
            existing.mkdir()
            sentinel = existing / 'keep.img'
            sentinel.write_bytes(b'keep')
            link = root / 'link'
            link.symlink_to(existing, target_is_directory=True)
            for output in (existing, link):
                with self.assertRaises(ValueError):
                    stock.extract(archive, output, profile)
            self.assertEqual(sentinel.read_bytes(), b'keep')

    def test_truncated_and_invalid_protobuf_refused(self):
        for data in (b'\x80', b'\x00', b'\x0a\x04ab', b'\x08' + b'\xff' * 10, b'\x0b'):
            with self.subTest(data=data), self.assertRaises(ValueError):
                stock.fields(data)

    def test_bounded_decompression_rejects_trailing_and_extra_data(self):
        for kind, compressor in ((1, bz2.compress), (8, lzma.compress)):
            for blob in (compressor(b'a' * 8192), compressor(b'a' * 4096) + b'trailing',
                         compressor(b'a' * 4096)[:-2]):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    stock.decompress(kind, blob, 4096)


if __name__ == '__main__':
    unittest.main()
