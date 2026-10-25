import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('first_device', ROOT / 'tools/prepare-first-boot.py')
first = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(first)


def raw_image():
    image = bytearray(12288)
    image[:128] = bytes(range(128))
    image[4096:4352] = b'AVB0' + b'v' * 252
    image[-64:] = struct.pack('>4sIIQQQ28s', b'AVBf', 1, 0, 128, 4096, 256, b'\0' * 28)
    return bytes(image)


class FirstDeviceTests(unittest.TestCase):
    def test_relocated_footer_and_preserved_metadata(self):
        source = raw_image()
        result = first.resize_footer(source, 24576)
        self.assertEqual(len(result), 24576)
        self.assertEqual(result[:8192], source[:8192])
        self.assertEqual(result[-64:], source[-64:])
        self.assertEqual(result[8192:-64], b'\0' * (24576 - 8192 - 64))
        self.assertEqual(source, raw_image())

    def test_reject_missing_or_out_of_bounds_footer(self):
        for footer in [b'\0' * 64,
                       struct.pack('>4sIIQQQ28s', b'AVBf', 1, 0, 128, 12000, 512, b'\0' * 28)]:
            with self.subTest(footer=footer[:4]):
                with self.assertRaises(ValueError):
                    first.resize_footer(raw_image()[:-64] + footer, 24576)

    def test_reject_small_target_and_non_block_size(self):
        for size in [8192, 24001]:
            with self.assertRaises(ValueError):
                first.resize_footer(raw_image(), size)

    def test_reject_changed_source_without_creating_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            product = root / 'android/out/target/product/billie2'
            product.mkdir(parents=True)
            (product / first.BOOT_NAME).write_bytes(b'changed')
            output = root / 'bundle'
            with self.assertRaises(ValueError):
                first.prepare(root / 'android', root / 'stock', output)
            self.assertFalse(output.exists())

    def test_reject_size_hash_changes_and_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'input'
            path.write_bytes(b'abc')
            with self.assertRaises(ValueError):
                first.checked_file(path, 3, '0' * 64)
            with self.assertRaises(ValueError):
                first.prepare(root, root, root)

    def run_guard(self, overrides=None, devices='private-id\tfastboot\n'):
        values = {'product': 'bengal', 'unlocked': 'yes', 'current-slot': 'b',
                  'is-userspace': 'no', 'partition-size:dtbo_b': '0x1800000',
                  'partition-size:boot_b': '0x6000000', 'max-download-size': '804280320'}
        values.update(overrides or {})
        calls = []

        def fake(*args):
            calls.append(args)
            if args == ('devices',):
                return devices
            self.assertEqual(args[:3], ('-s', 'private-id', 'getvar'))
            return '(bootloader) {}: {}\nFinished. Total time: 0.001s\n'.format(args[3], values[args[3]])

        with patch.object(first, 'checked_file'), patch.object(first, 'stock_restore', return_value=b'rollback'), patch.object(first, 'fastboot', side_effect=fake):
            first.check(Path('/bundle'), Path('/stock'))
        return calls

    def test_guard_reads_only_and_selects_one_device(self):
        calls = self.run_guard()
        self.assertEqual(len(calls), 8)
        self.assertTrue(all(c == ('devices',) or c[2] == 'getvar' for c in calls))

    def test_guard_rejects_wrong_slot_locked_fastbootd_sizes_and_download(self):
        for key, value in [('current-slot', 'a'), ('unlocked', 'no'),
                           ('is-userspace', 'yes'), ('product', 'other'),
                           ('partition-size:dtbo_b', '0x800000'),
                           ('partition-size:boot_b', '4096'), ('max-download-size', '1024')]:
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    self.run_guard({key: value})

    def test_guard_rejects_missing_or_multiple_devices(self):
        for devices in ['', 'private-id\tfastboot\nother\tfastboot\n']:
            with self.assertRaises(ValueError):
                self.run_guard(devices=devices)


if __name__ == '__main__':
    unittest.main()
