import importlib.util
import struct
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hal_layout', ROOT / 'tools/inspect-android-output.py')
layout = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(layout)


def executable(kind=2, interpreter=None, wide=True):
    header_size, stride = (64, 56) if wide else (52, 32)
    data = bytearray(header_size + (stride + len(interpreter) + 1 if interpreter else 0))
    data[:7] = b'\x7fELF' + bytes([2 if wide else 1, 1, 1])
    struct.pack_into('<HH', data, 16, kind, 183 if wide else 40)
    if interpreter:
        struct.pack_into('<Q' if wide else '<I', data, 32 if wide else 28, header_size)
        struct.pack_into('<HH', data, 54 if wide else 42, stride, 1)
        struct.pack_into('<I', data, header_size, 3)
        struct.pack_into('<Q' if wide else '<I', data, header_size + (8 if wide else 4), header_size + stride)
        struct.pack_into('<Q' if wide else '<I', data, header_size + (32 if wide else 16), len(interpreter) + 1)
        data[header_size + stride:] = interpreter.encode() + b'\0'
    return data


class HalLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_static_executable_and_pie_interpreters(self):
        for wide in (False, True):
            path = self.root / 'binary'
            path.write_bytes(executable(wide=wide))
            self.assertTrue(layout.elf_details(path)['static_executable'])
            for kind in (2, 3):
                path.write_bytes(executable(kind, '/apex/com.android.runtime/bin/linker64', wide))
                record = layout.elf_details(path)
                self.assertEqual(record['elf_interpreter'], '/apex/com.android.runtime/bin/linker64')
                self.assertFalse(record['static_executable'])

    def test_invalid_program_table_and_interpreter_are_diagnostic(self):
        path = self.root / 'binary'
        data = executable(3, '/some/linker')
        struct.pack_into('<Q', data, 32, len(data) + 100)
        path.write_bytes(data)
        self.assertIn('elf_error', layout.elf_details(path))
        data = executable(3, '/some/linker')
        struct.pack_into('<Q', data, 64 + 32, 5000)
        path.write_bytes(data)
        self.assertIn('elf_error', layout.elf_details(path))

    def test_partition_and_absolute_android_links_are_preserved(self):
        product = self.root / 'android/out/target/product/billie2'
        (product / 'system/bin').mkdir(parents=True)
        (product / 'system_ext/bin').mkdir(parents=True)
        (product / 'system_ext/bin/hwservicemanager').write_bytes(executable(3, '/system/bin/linker64'))
        (product / 'system/bin/hwservicemanager').symlink_to('/system_ext/bin/hwservicemanager')
        (product / 'system/bin/getprop').symlink_to('toolbox')
        (product / 'system/bin/linkerconfig').write_bytes(executable())
        report = layout.inspect(self.root / 'android')
        records = {str(Path(p['path']).relative_to(product)): p for p in report['hal_layout']}
        self.assertEqual(records['system/bin/getprop']['android_target'], '/system/bin/toolbox')
        self.assertEqual(records['system/bin/hwservicemanager']['android_target'], '/system_ext/bin/hwservicemanager')
        self.assertTrue(records['system/bin/linkerconfig']['static_executable'])
        self.assertEqual(records['system_ext/bin/hwservicemanager']['elf_type'], 3)

    def test_symlinked_outside_partition_is_not_inspected(self):
        product = self.root / 'android/out/target/product/billie2'
        product.mkdir(parents=True)
        outside = self.root / 'outside'
        (outside / 'bin').mkdir(parents=True)
        (outside / 'bin/hwservicemanager').symlink_to('/sensitive-link-target')
        (product / 'system_ext').symlink_to(outside)
        report = layout.inspect(self.root / 'android')
        record = next(p for p in report['hal_layout'] if p['path'].endswith('/system_ext/bin/hwservicemanager'))
        self.assertIn('inspection_skipped', record)
        self.assertNotIn('symlink', record)


if __name__ == '__main__':
    unittest.main()
