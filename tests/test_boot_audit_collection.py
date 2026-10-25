import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zipfile


SPEC = importlib.util.spec_from_file_location(
    "boot_audit_collector", Path(__file__).resolve().parents[1] / "tools/collect-boot-audit.py")
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


def boot_image():
    kernel = b'K' * 120
    ramdisk = gzip.compress(b'070701-initramfs-fixture')
    dtb = b'\xd0\x0d\xfe\xed' + b'DTB-fixture'
    image = bytearray(4 * 4096)
    image[:8] = b'ANDROID!'
    for offset, value in ((8, len(kernel)), (16, len(ramdisk)), (36, 4096),
                          (40, 2), (1644, 1660), (1648, len(dtb))):
        struct.pack_into('<I', image, offset, value)
    image[64:70] = b'first '
    image[608:614] = b'second'
    for offset, data in ((4096, kernel), (8192, ramdisk), (12288, dtb)):
        image[offset:offset + len(data)] = data
    return bytes(image), ramdisk, dtb, kernel[:64]


def fixture(root, built_dtbo=True):
    stock = root / 'stock-BE2013-fixture'
    stock.mkdir()
    image, ramdisk, dtb, kernel_header = boot_image()
    records = []
    for name in ('boot', 'dtbo', 'recovery', 'vbmeta', 'vbmeta_system'):
        data = image if name == 'boot' else (name.encode() * 1024)[:4096]
        (stock / (name + '.img')).write_bytes(data)
        records.append({'name': name, 'file': name + '.img', 'bytes': len(data),
                        'sha256': hashlib.sha256(data).hexdigest(),
                        'operation_hashes_verified': True, 'partition_hash_verified': True})
    reference = {'device': 'OnePlus Nord N100 BE2013 EEA', 'archive_bytes': 2564194119,
                 'archive_sha256': 'a' * 64, 'ota_metadata_sha256': 'b' * 64,
                 'images': records}
    (stock / 'stock-images.json').write_text(json.dumps(reference))
    hybris = root / 'hybris-boot.img'
    hybris.write_bytes(image)
    hybris_ref = {'files': [{'name': 'hybris-boot.img', 'bytes': len(image),
                             'sha256': hashlib.sha256(image).hexdigest()}]}
    android = root / 'android'
    product = android / 'out/target/product/billie2'
    (product / 'obj/KERNEL_OBJ').mkdir(parents=True)
    (product / 'obj/KERNEL_OBJ/.config').write_text('CONFIG_USB_GADGET=y\n')
    if built_dtbo:
        (product / 'dtbo.img').write_bytes(b'built-overlay-fixture')
    return stock, hybris, android, reference, hybris_ref, (ramdisk, dtb, kernel_header)


class BootAuditTests(unittest.TestCase):
    def test_complete_bundle_copies_only_selected_sections_and_checks_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock, hybris, android, reference, hybris_ref, sections = fixture(root)
            output = root / 'audit.zip'
            result = audit.collect(hybris, stock, android, output, reference, hybris_ref)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(set(archive.namelist()), {
                    'audit.json', 'kernel.config', 'hybris/ramdisk.bin', 'hybris/dtb.img',
                    'hybris/kernel-header.bin', 'hybris/dtbo.img', 'stock/dtb.img', 'stock/dtbo.img'})
                self.assertEqual(archive.read('hybris/ramdisk.bin'), sections[0])
                self.assertEqual(archive.read('hybris/dtb.img'), sections[1])
                self.assertEqual(archive.read('hybris/kernel-header.bin'), sections[2])
                self.assertEqual(result, json.loads(archive.read('audit.json')))
                for member in result['members']:
                    data = archive.read(member['file'])
                    self.assertEqual(len(data), member['bytes'])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), member['sha256'])
            self.assertEqual(result['hybris_boot']['cmdline'], 'first second')
            self.assertEqual(result['hybris_boot']['ramdisk_compression'], 'gzip')
            self.assertTrue(result['hybris_boot']['dtb_has_fdt_magic'])
            self.assertFalse(result['device_accessed'])
            self.assertFalse(result['dtbo_application_tested'])
            self.assertFalse(result['ramdisk_contents_reviewed'])

    def test_actual_image_corruption_rejected_before_zip_creation(self):
        for kind in ('stock', 'hybris'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                stock, hybris, android, reference, hybris_ref, unused = fixture(root)
                target = stock / 'dtbo.img' if kind == 'stock' else hybris
                data = bytearray(target.read_bytes())
                data[-1] ^= 1
                target.write_bytes(data)
                output = root / 'audit.zip'
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    audit.collect(hybris, stock, android, output, reference, hybris_ref)
                self.assertFalse(output.exists())

    def test_invalid_boot_layouts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = boot_image()[0]
            for offset, value in ((36, 2048), (40, 3), (24, 10), (1632, 10),
                                  (1648, 99999999), (16, 0)):
                data = bytearray(original)
                struct.pack_into('<I', data, offset, value)
                path = root / 'bad.img'
                path.write_bytes(data)
                with self.subTest(offset=offset), self.assertRaises(ValueError):
                    audit.boot_sections(path)
            path.write_bytes(original[:-4096])
            with self.assertRaisesRegex(ValueError, 'Truncated'):
                audit.boot_sections(path)

    def test_stock_discovery_requires_matching_report_and_refuses_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock, hybris, android, reference, hybris_ref, unused = fixture(root)
            wrong = root / 'stock-BE2013-other'
            wrong.mkdir()
            changed = copy.deepcopy(reference)
            changed['archive_sha256'] = 'c' * 64
            (wrong / 'stock-images.json').write_text(json.dumps(changed))
            self.assertEqual(audit.find_stock(root, None, reference), stock)
            (wrong / 'stock-images.json').write_text(json.dumps(reference))
            with self.assertRaisesRegex(ValueError, 'found 2'):
                audit.find_stock(root, None, reference)
            self.assertEqual(audit.find_stock(root, stock, reference), stock)

    def test_untrusted_report_filename_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock, hybris, android, reference, hybris_ref, unused = fixture(root)
            changed = copy.deepcopy(reference)
            changed['images'][0]['file'] = '../outside.img'
            (stock / 'stock-images.json').write_text(json.dumps(changed))
            with self.assertRaises(ValueError):
                audit.collect(hybris, stock, android, root / 'audit.zip', reference, hybris_ref)
            self.assertFalse((root / 'audit.zip').exists())

    def test_existing_output_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock, hybris, android, reference, hybris_ref, unused = fixture(root)
            output = root / 'audit.zip'
            output.write_bytes(b'keep')
            with self.assertRaises(ValueError):
                audit.collect(hybris, stock, android, output, reference, hybris_ref)
            self.assertEqual(output.read_bytes(), b'keep')

    def test_missing_config_and_symlinked_config_fail_without_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock, hybris, android, reference, hybris_ref, unused = fixture(root)
            config = android / 'out/target/product/billie2/obj/KERNEL_OBJ/.config'
            config.unlink()
            for symlink in (False, True):
                if symlink:
                    other = root / 'other'
                    other.write_bytes(b'private')
                    config.symlink_to(other)
                with self.subTest(symlink=symlink), self.assertRaises(ValueError):
                    audit.collect(hybris, stock, android, root / 'audit.zip', reference, hybris_ref)
                self.assertFalse((root / 'audit.zip').exists())

    def test_missing_built_dtbo_is_recorded_without_blocking_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stock, hybris, android, reference, hybris_ref, unused = fixture(root, built_dtbo=False)
            result = audit.collect(hybris, stock, android, root / 'audit.zip', reference, hybris_ref)
            self.assertFalse(result['built_dtbo_available'])


if __name__ == '__main__':
    unittest.main()
