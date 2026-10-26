import importlib.util
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_linker_preview', ROOT / 'tools/generate-linker-preview.py')
preview = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(preview)


def elf(needed):
    """Tiny ELF64 AArch64 file with real PT_DYNAMIC/string table for host readelf."""
    strings = b'\0'; tags = []
    for name in needed:
        tags.append((1, len(strings))); strings += name.encode() + b'\0'
    tags += [(5, 0x300), (10, len(strings)), (0, 0)]
    data = bytearray(0x300 + len(strings))
    data[:64] = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0'*9,
                            3, 183, 1, 0, 64, 0, 0, 64, 56, 2, 0, 0, 0)
    data[64:120] = struct.pack('<IIQQQQQQ', 1, 4, 0, 0, 0, len(data), len(data), 4096)
    data[120:176] = struct.pack('<IIQQQQQQ', 2, 4, 0x100, 0x100, 0x100, len(tags)*16, len(tags)*16, 8)
    for index, tag in enumerate(tags): struct.pack_into('<qQ', data, 0x100 + 16*index, *tag)
    data[0x300:] = strings
    return bytes(data)


class NativeLinkerPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 native namespace '); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_explicit_new_profile_matches_real_device_evidence_and_preserves_old_default(self):
        device = json.loads((ROOT / 'validation/native-runtime-device-report-user-20261026.json').read_text())
        self.assertTrue(device['assessment']['dependency_probe_passed'])
        manifest = self.root / 'manifest.json'; manifest.write_text(json.dumps(device['bundle']))
        with patch.object(preview, 'checked_file') as check:
            bundle, profile = preview.load_hal_bundle(self.root, native_rpm=True)
            self.assertTrue(profile['device_dependency_probe_passed'])
            self.assertEqual(profile['runtime_manifest']['require_native_libs'], ['liblog.so'])
            self.assertEqual(bundle['payload_sha256'], '2077c05b09aa83f466336c9fc6a7340f16bf335c570f10b27ee1b57623c78da0')
            self.assertEqual(check.call_count, 2)
            self.assertEqual(check.call_args_list[0].args, (self.root / 'apex_payload.img', bundle['payload']))
            self.assertEqual(check.call_args_list[1].args[1]['sha256'], bundle['hal_archive_sha256'])
        with self.assertRaisesRegex(ValueError, 'differs from successful device test'):
            preview.load_hal_bundle(self.root)
        old = json.loads((ROOT / 'validation/hal-rpm-device-report-user-20261026.json').read_text())['bundle']
        manifest.write_text(json.dumps(old))
        historical, profile = preview.load_hal_bundle(self.root)
        self.assertIsNone(profile); self.assertEqual(historical, old)
        with self.assertRaisesRegex(ValueError, 'successful native device test'):
            preview.load_hal_bundle(self.root, native_rpm=True)
        with self.assertRaisesRegex(ValueError, 'mix'):
            preview.generate(self.root, self.root, self.root, self.root / 'new', self.root / 'providers', native_rpm=True)

    @unittest.skipUnless(shutil.which('readelf'), 'binutils readelf required')
    def test_actual_readelf_inventory_closure_missing_library_and_abi_guard(self):
        system = self.root / 'system'; (system / 'bin').mkdir(parents=True); (system / 'lib64').mkdir()
        files = {'bin/' + name: elf(['libbase.so']) for name in ('servicemanager', 'hwservicemanager', 'logd')}
        files.update({'lib64/libbase.so': elf(['libc.so']), 'lib64/libc.so': elf([]),
                      'lib64/libunwindstack.so': elf(['libneuralnetworks.so']), 'bin/script': b'#!/bin/sh\n'})
        pins = {}
        for name, content in files.items():
            (system / name).write_bytes(content); pins[name] = preview.digest(content)
        bundle = {'hal_tree': {'regular_file_hashes': pins}}
        result = preview.audit_elf_dependencies(system, bundle)
        self.assertEqual(result['elf_files'], 6)
        for closure in result['service_flat_closures'].values():
            self.assertEqual(len(closure['regular_flat_hal_files']), 3)
            self.assertEqual(closure['not_found_in_regular_flat_hal'], [])
        self.assertEqual(result['direct_consumers_of_missing_providers'],
                         {'libdexfile.so': [], 'libneuralnetworks.so': ['lib64/libunwindstack.so']})
        self.assertFalse(result['vendor_included']); self.assertFalse(result['dlopen_analyzed'])
        (system / 'lib64/libc.so').unlink(); del pins['lib64/libc.so']
        result = preview.audit_elf_dependencies(system, bundle)
        self.assertEqual(result['service_flat_closures']['bin/logd']['not_found_in_regular_flat_hal'], ['libc.so'])
        wrong = bytearray(files['lib64/libc.so']); struct.pack_into('<H', wrong, 18, 62)
        (system / 'lib64/libc.so').write_bytes(wrong); pins['lib64/libc.so'] = preview.digest(wrong)
        with self.assertRaisesRegex(ValueError, 'Wrong ABI'):
            preview.audit_elf_dependencies(system, bundle)
        pins['lib64/libc.so'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'changed during inspection'):
            preview.audit_elf_dependencies(system, bundle)


if __name__ == '__main__': unittest.main()
