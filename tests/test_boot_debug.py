import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
SPEC = importlib.util.spec_from_file_location('n100_boot_debug', ROOT / 'tools/boot-debug.py')
debug = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(debug)

ORIGINAL = '''#!/bin/sh
usb_setup_configfs() {
    echo old-configfs
}
# This sets up the USB with whatever USB_FUNCTIONS are set to via android_usb
usb_setup_android_usb() { :; }
# This determines which USB setup method is going to be used
usb_setup() { echo old-backend; }
# This lets us communicate errors to host
usb_info() { :; }
    mount_stowaways

    # No target debug unless we debug here too
if true; then
\t# During debug we export mmc too
\techo old-storage-export
\tUSB_FUNCTIONS=rndis,mass_storage

\trun_debug_session example
fi
'''


def cpio(entries):
    result = bytearray()
    for name, mode, content in entries + [('TRAILER!!!', 0, b'')]:
        fields = [1, mode, 0, 0, 1, 0, len(content), 0, 0, 0, 0, len(name.encode()) + 1, 0]
        result.extend(b'070701' + ''.join('%08x' % value for value in fields).encode())
        result.extend(name.encode() + b'\0')
        result.extend(b'\0' * (-len(result) % 4))
        result.extend(content)
        result.extend(b'\0' * (-len(result) % 4))
    return bytes(result)


def legacy_lz4_literals(data):
    block = bytearray([min(len(data), 15) << 4])
    if len(data) >= 15:
        length = len(data) - 15
        while length >= 255:
            block.append(255)
            length -= 255
        block.append(length)
    block.extend(data)
    return bytes.fromhex('02214c18') + struct.pack('<I', len(block)) + block


def busybox(interpreter=False):
    data = bytearray(176)
    data[:7] = b'\x7fELF\x02\x01\x01'
    struct.pack_into('<HH', data, 16, 2, 183)
    struct.pack_into('<Q', data, 32, 64)
    struct.pack_into('<HH', data, 54, 56, 2)
    struct.pack_into('<I', data, 64, 1)
    struct.pack_into('<I', data, 120, 3 if interpreter else 4)
    return bytes(data)


def dtbo_image():
    overlay = bytes.fromhex('d00dfeed') + b'DTBO-fixture'
    total = 64 + len(overlay)
    data = bytearray(1024)
    struct.pack_into('>8I', data, 0, 0xd7b7ab1e, total, 32, 32, 1, 32, 4096, 0)
    struct.pack_into('>8I', data, 32, len(overlay), 64, 0, 0, 0, 0, 0, 0)
    data[64:total] = overlay
    data[256:260] = b'AVB0'
    struct.pack_into('>4sIIQQQ28s', data, len(data) - 64, b'AVBf', 1, 0, total, 256, 256, b'\0' * 28)
    reference = {'image_bytes': len(data), 'table_bytes': total,
                 'table_sha256': hashlib.sha256(data[:total]).hexdigest(),
                 'entries': [{'bytes': len(overlay), 'offset': 64, 'id': 0, 'revision': 0,
                              'custom': [0, 0, 0, 0], 'sha256': hashlib.sha256(overlay).hexdigest()}],
                 'has_avb_footer': True}
    return bytes(data), reference


CONFIG = '\n'.join(key + '=y' for key in (
    'CONFIG_BLK_DEV_INITRD', 'CONFIG_RD_GZIP', 'CONFIG_RD_LZ4', 'CONFIG_DEVTMPFS',
    'CONFIG_DEVTMPFS_MOUNT', 'CONFIG_CONFIGFS_FS', 'CONFIG_USB_GADGET',
    'CONFIG_USB_CONFIGFS', 'CONFIG_USB_CONFIGFS_RNDIS')) + '\n'


class DebugBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.port = self.root / 'port'
        shutil.copytree(ROOT / 'adaptation/initramfs', self.port / 'adaptation/initramfs')
        (self.port / 'kernel').mkdir()
        (self.port / 'kernel/sailfish.config').write_text('CONFIG_RD_GZIP=y\nCONFIG_RD_LZ4=y\n')
        (self.port / 'validation').mkdir()
        self.addCleanup(patch.stopall)
        patch.object(debug, 'ROOT', self.port).start()

    def ramdisk(self, compression='gzip', interpreter=False, init=None):
        text = debug.patched_init(ORIGINAL) if init is None else init
        data = cpio([('init', 0o100755, text.encode()),
                     ('bin/busybox', 0o100755, busybox(interpreter)),
                     ('bin/sh', 0o120777, b'busybox')])
        return gzip.compress(data) if compression == 'gzip' else legacy_lz4_literals(data)

    def image(self, ramdisk):
        align = lambda count: (count + 4095) // 4096 * 4096
        dtb = b'\xd0\x0d\xfe\xedfixture-dtb'
        data = bytearray(4096 + 4096 + align(len(ramdisk)) + 4096)
        data[:8] = b'ANDROID!'
        for offset, value in ((8, 64), (16, len(ramdisk)), (36, 4096), (40, 2), (1648, len(dtb))):
            struct.pack_into('<I', data, offset, value)
        cmdline = b'androidboot.hardware=qcom'
        data[64:64 + len(cmdline)] = cmdline
        data[4096:4160] = b'K' * 64
        data[8192:8192 + len(ramdisk)] = ramdisk
        start = 8192 + align(len(ramdisk))
        data[start:start + len(dtb)] = dtb
        image = self.root / 'hybris-boot.img'
        image.write_bytes(data)
        dtbo = self.root / 'dtbo.img'
        dtbo_data, content_reference = dtbo_image()
        dtbo.write_bytes(dtbo_data)
        (self.port / 'validation/boot-dtbo-content-20261025.json').write_text(json.dumps(content_reference))
        (self.port / 'validation/boot-audit-report-user-20261025.json').write_text(json.dumps({'members':[
            {'file':'hybris/dtb.img','sha256':hashlib.sha256(dtb).hexdigest()},
            {'file':'hybris/dtbo.img','sha256':hashlib.sha256(dtbo.read_bytes()).hexdigest()}]}))
        config = self.root / '.config'
        config.write_text(CONFIG)
        return image, config, dtbo

    def test_pack_gzip_and_legacy_lz4_preserves_source_and_all_payload_bytes(self):
        for compression in ('gzip', 'lz4'):
            with self.subTest(compression=compression):
                image, config, dtbo = self.image(self.ramdisk(compression))
                original = image.read_bytes()
                output = self.root / (compression + '-debug.img')
                with contextlib.redirect_stdout(io.StringIO()):
                    debug.pack(image, config, dtbo, output)
                built = output.read_bytes()
                self.assertEqual(image.read_bytes(), original)
                self.assertEqual(built[4096:], original[4096:])
                self.assertIn(b'n100_debug=1', built[64:576])
                report = json.loads(output.with_suffix('.img.json').read_text())
                self.assertTrue(report['ramdisk']['early_debug_guard_verified'])
                self.assertFalse(report['device_accessed'])
                self.assertFalse(report['boot_tested'])
                self.assertEqual(report['sha256'], hashlib.sha256(built).hexdigest())

    def test_missing_explicit_lz4_initramfs_option_stops_before_output(self):
        image, config, dtbo = self.image(self.ramdisk('lz4'))
        config.write_text(CONFIG.replace('CONFIG_RD_LZ4=y', '# CONFIG_RD_LZ4 is not set')
                          + 'CONFIG_DECOMPRESS_LZ4=y\n')
        with self.assertRaisesRegex(debug.n100.PortError, 'CONFIG_RD_LZ4'):
            debug.pack(image, config, dtbo, self.root / 'debug.img')
        self.assertFalse((self.root / 'debug.img').exists())

    def test_unpatched_or_late_debug_guard_and_dynamic_busybox_are_refused(self):
        modified = debug.patched_init(ORIGINAL)
        early = (self.port / 'adaptation/initramfs/early-debug.sh').read_text().rstrip() + '\n\n'
        late = modified.replace(early, '') + early
        for data in (self.ramdisk(init=ORIGINAL), self.ramdisk(init=late), self.ramdisk(interpreter=True)):
            with self.subTest(data_size=len(data)), self.assertRaises(debug.n100.PortError):
                debug.verify_ramdisk(data, CONFIG)

    def test_changed_dtbo_or_existing_output_are_preserved(self):
        image, config, dtbo = self.image(self.ramdisk())
        changed = bytearray(dtbo.read_bytes())
        changed[70] ^= 1
        dtbo.write_bytes(changed)
        output = self.root / 'debug.img'
        with self.assertRaisesRegex(debug.n100.PortError, 'DTB/DTBO content changed'):
            debug.pack(image, config, dtbo, output)
        self.assertFalse(output.exists())
        with zipfile.ZipFile(output.with_suffix('.img.review.zip')) as review:
            self.assertEqual(review.read('dtbo.img'), changed[:dtbo_image()[1]['table_bytes']])
        output.write_bytes(b'keep')
        with self.assertRaises(debug.n100.PortError):
            debug.pack(image, config, dtbo, output)
        self.assertEqual(output.read_bytes(), b'keep')

    def test_changed_avb_metadata_keeps_reviewed_content_and_records_actual_file_hash(self):
        image, config, dtbo = self.image(self.ramdisk())
        previous_hash = hashlib.sha256(dtbo.read_bytes()).hexdigest()
        changed = bytearray(dtbo.read_bytes())
        changed[300] = 123  # Metadata inside the AVB block, outside the DTBO table.
        dtbo.write_bytes(changed)
        output = self.root / 'debug.img'
        with contextlib.redirect_stdout(io.StringIO()):
            debug.pack(image, config, dtbo, output)
        result = json.loads(output.with_suffix('.img.json').read_text())['matching_dtbo']
        self.assertFalse(result['whole_image_matches_previous'])
        self.assertNotEqual(result['sha256'], previous_hash)
        self.assertEqual(result['sha256'], hashlib.sha256(changed).hexdigest())
        self.assertTrue(result['reviewed_content_matches'])
        self.assertFalse(result['avb_signature_verified'])

    def test_malformed_dtbo_table_or_footer_is_refused(self):
        original, unused = dtbo_image()
        cases = [b'bad']
        for offset, value in ((4, 5000), (32, 5000), (36, 9999)):
            data = bytearray(original)
            struct.pack_into('>I', data, offset, value)
            cases.append(bytes(data))
        data = bytearray(original)
        data[256:260] = b'BAD!'
        cases.append(bytes(data))
        for data in cases:
            with self.subTest(size=len(data)), self.assertRaises(debug.n100.PortError):
                debug.dtbo_content_identity(data)

    def test_corrupt_ramdisk_and_cpio_traversal_are_refused(self):
        for data in (bytes.fromhex('02214c18') + b'\xff\xff\xff\xff', b'not a ramdisk', gzip.compress(b'bad cpio')):
            with self.subTest(data=data), self.assertRaises(debug.n100.PortError):
                debug.verify_ramdisk(data, CONFIG)
        for name in ('../escape', '/absolute'):
            with self.assertRaises(debug.n100.PortError):
                debug.cpio_members(cpio([(name, 0o100644, b'bad')]))

    def test_debug_flag_never_falls_through_to_storage_even_after_injection_returns(self):
        early = self.port / 'adaptation/initramfs/early-debug.sh'
        script = ('get_opt() { echo 1; }; run_debug_session() { echo "debug:$USB_FUNCTIONS"; }; '
                  'sleep() { exit 0; }; . "$1"; echo STORAGE-MOUNTED')
        result = subprocess.run(['sh', '-c', script, 'fixture', str(early)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('debug:rndis', result.stdout)
        self.assertNotIn('STORAGE-MOUNTED', result.stdout)

    def prepare_fixture(self):
        android = self.root / 'android'
        kernel = android / 'kernel/oneplus/sm4250'
        configs = kernel / 'arch/arm64/configs/vendor'
        configs.mkdir(parents=True)
        base = 'CONFIG_RD_GZIP=y\n# CONFIG_RD_LZ4 is not set\n'
        (configs / 'base_defconfig').write_text(base)
        legacy = debug.n100.merge_config(base, 'CONFIG_RD_GZIP=y\n')
        generated = configs / 'sailfish_defconfig'
        generated.write_text(legacy)
        boot = android / 'hybris/hybris-boot'
        boot.mkdir(parents=True)
        init = boot / 'init-script'
        init.write_text(ORIGINAL)
        lock = {'projects':[{'path':'kernel/oneplus/sm4250','revision':'kernel'}],
                'extensions':[{'path':'hybris/hybris-boot','revision':'boot'}],
                'device_changes':{'kernel_base_config':'vendor/base_defconfig',
                                  'kernel_generated_config':'vendor/sailfish_defconfig'}}
        patch.object(debug.n100, 'require_profile').start()
        patch.object(debug.n100, 'require_checkout').start()
        patch.object(debug.n100, 'read_lock', return_value=lock).start()
        patch.object(debug.n100, 'run', side_effect=lambda argv, **kw: '' if argv[1] == 'diff' else ORIGINAL.rstrip()).start()
        patch.object(debug, 'INIT_BLOB', debug.n100.git_blob_identity(ORIGINAL.encode())).start()
        patch.object(debug, 'LEGACY_DEFCONFIG_SHA256', hashlib.sha256(legacy.encode()).hexdigest()).start()
        return android, generated, init

    def test_prepare_upgrades_known_inputs_and_is_idempotent(self):
        android, generated, init = self.prepare_fixture()
        with contextlib.redirect_stdout(io.StringIO()):
            debug.prepare(str(android))
            before = (generated.stat().st_mtime_ns, init.stat().st_mtime_ns)
            debug.prepare(str(android))
        self.assertEqual(before, (generated.stat().st_mtime_ns, init.stat().st_mtime_ns))
        self.assertIn('CONFIG_RD_LZ4=y', generated.read_text())
        self.assertIn('N100_EARLY_DEBUG_V1', init.read_text())
        self.assertNotIn('old-storage-export', init.read_text())

    def test_prepare_preserves_unknown_edits_before_mutating_any_input(self):
        android, generated, init = self.prepare_fixture()
        original_generated = generated.read_bytes()
        init.write_text(ORIGINAL + '# local edit\n')
        with self.assertRaisesRegex(debug.n100.PortError, 'unknown edits'):
            debug.prepare(str(android))
        self.assertEqual(generated.read_bytes(), original_generated)
        self.assertTrue(init.read_text().endswith('# local edit\n'))
        self.assertFalse((android / '.n100-debug-inputs.json').exists())


if __name__ == '__main__':
    unittest.main()
