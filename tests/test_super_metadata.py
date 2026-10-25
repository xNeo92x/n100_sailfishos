import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('super_reader', ROOT / 'tools/inspect-super.py')
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)
MAXIMUM = 4096


def geometry(slots=2, block=4096):
    raw = bytearray(struct.pack('<II32sIII', 0x616c4467, 52, b'\0' * 32, MAXIMUM, slots, block))
    raw[8:40] = hashlib.sha256(raw).digest()
    return raw


def metadata(minor=0, bad_extent=False, overlap=False, bad_name=False, empty=False):
    partition_name = b'system_b\0BAD' if bad_name else b'system_b'
    parts = b''.join([struct.pack('<36s4I', b'system_a', 1, 0, 1, 0),
                      struct.pack('<36s4I', partition_name, 1, 1, 0 if empty else 1, 0),
                      struct.pack('<36s4I', b'vendor_b', 1, 2, 1, 0)])
    physical = reader.SUPER_BYTES // 512 - 4 if bad_extent else 4096
    extents = b''.join([struct.pack('<QIQI', 16, 0, 2048, 0),
                        struct.pack('<QIQI', 24, 0, physical, 0),
                        struct.pack('<QIQI', 8, 1, 0, 0)])
    groups = struct.pack('<36sIQ', b'default', 0, 0)
    devices = struct.pack('<QIIQ36sI', 2048, 1048576, 0, reader.SUPER_BYTES, b'super', 0)
    tables = parts + extents + groups + devices
    header = bytearray(256 if minor == 2 else 128)
    struct.pack_into('<IHHI', header, 0, 0x414c5030, 10, minor, len(header))
    struct.pack_into('<I', header, 44, len(tables))
    header[48:80] = hashlib.sha256(tables).digest()
    starts = [0, len(parts), len(parts) + len(extents), len(parts) + len(extents) + len(groups)]
    for i, (start, count, width) in enumerate(zip(starts, [3, 3, 1, 1], [52, 24, 48, 64])):
        struct.pack_into('<III', header, 80 + i * 12, 0 if overlap and i == 1 else start, count, width)
    if minor == 2:
        struct.pack_into('<I', header, 128, 1)
    header[12:44] = hashlib.sha256(header).digest()
    return bytes(header) + tables + b'\0' * (MAXIMUM - len(header) - len(tables))


def prefix(**kwargs):
    data = bytearray(65536)
    for offset in [4096, 8192]:
        data[offset:offset + 52] = geometry()
    for offset in [12288, 16384, 20480, 24576]:
        data[offset:offset + MAXIMUM] = metadata(**kwargs)
    return data


class SuperMetadataTests(unittest.TestCase):
    def test_slot_b_checksums_extents_and_no_device_actions(self):
        report = reader.inspect(prefix())
        self.assertEqual(report['active_partition_candidates'], ['system_b', 'vendor_b'])
        self.assertTrue(report['valid_metadata_copies_equal'])
        self.assertTrue(report['checksums_verified'])
        self.assertFalse(report['mappings_created'])
        self.assertFalse(report['mounts_performed'])
        self.assertFalse(report['device_accessed'])
        self.assertEqual(report['metadata']['partitions'][1]['bytes'], 24 * 512)
        self.assertEqual(report['metadata']['partitions'][2]['extents'][0]['type'], 'zero')

    def test_slot_a_and_empty_partition(self):
        self.assertEqual(reader.inspect(prefix(), 'a')['active_partition_candidates'], ['system_a'])
        self.assertEqual(reader.inspect(prefix(empty=True))['metadata']['partitions'][1]['bytes'], 0)

    def test_corrupt_primary_geometry_falls_back(self):
        data = prefix(); data[4104] ^= 1
        self.assertIn('primary', reader.inspect(data)['geometry_copy_errors'])

    def test_both_corrupt_geometries_rejected(self):
        data = prefix(); data[4104] ^= 1; data[8200] ^= 1
        with self.assertRaisesRegex(ValueError, 'No checksummed geometry'):
            reader.inspect(data)

    def test_conflicting_valid_geometries_rejected(self):
        data = prefix(); data[8192:8244] = geometry(block=8192)
        with self.assertRaisesRegex(ValueError, 'Conflicting valid geometries'):
            reader.inspect(data)

    def test_corrupt_primary_metadata_falls_back(self):
        data = prefix(); data[16384 + 12] ^= 1
        report = reader.inspect(data)
        self.assertEqual(report['chosen_copy'], 'backup')
        self.assertIsNone(report['valid_metadata_copies_equal'])

    def test_bad_table_checksums_rejected(self):
        data = prefix()
        for offset in [16384, 24576]:
            data[offset + 128] ^= 1
        with self.assertRaisesRegex(ValueError, 'Table checksum'):
            reader.inspect(data)

    def test_extent_bounds_table_overlap_and_name_validation(self):
        for kwargs in [{'bad_extent': True}, {'overlap': True}, {'bad_name': True}]:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    reader.inspect(prefix(**kwargs))

    def test_truncated_prefix_wrong_device_and_missing_slot(self):
        with self.assertRaisesRegex(ValueError, 'Prefix too short'):
            reader.inspect(prefix()[:26000])
        with self.assertRaises(ValueError):
            reader.inspect(prefix(), super_bytes=reader.SUPER_BYTES - 512)
        data = prefix()
        for offset in [4096, 8192]:
            data[offset:offset + 52] = geometry(slots=1)
        with self.assertRaisesRegex(ValueError, 'slot does not exist'):
            reader.inspect(data)

    def test_expanded_header_and_divergent_valid_copies_reported(self):
        data = prefix(minor=2)
        report = reader.inspect(data)
        self.assertEqual(report['metadata']['header_flags'], 1)
        data[24576:28672] = metadata()
        self.assertFalse(reader.inspect(data)['valid_metadata_copies_equal'])

    def test_cli_writes_only_json_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            source, output = Path(temp) / 'prefix.bin', Path(temp) / 'report.json'
            layout = Path(temp) / 'layout.txt'
            layout.write_text('dmsetup=/usr/sbin/dmsetup\n')
            source.write_bytes(prefix())
            args = [sys.executable, str(ROOT / 'tools/inspect-super.py'), str(source), '--slot', 'b', '--output', str(output), '--rootfs-layout', str(layout)]
            subprocess.run(args, check=True, capture_output=True)
            first = output.read_bytes()
            self.assertTrue(json.loads(first)['checksums_verified'])
            self.assertEqual(json.loads(first)['rootfs_layout'], layout.read_text())
            self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
            self.assertEqual(first, output.read_bytes())
            self.assertEqual(bytes(prefix()), source.read_bytes())


if __name__ == '__main__':
    unittest.main()
