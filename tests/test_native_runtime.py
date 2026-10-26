import importlib.util
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_prepare_test', ROOT / 'tools/prepare-native-unwind.py')
prepare = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(prepare)
SPEC = importlib.util.spec_from_file_location('native_check_test', ROOT / 'tools/check-native-runtime.py')
checker = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(checker)


def fixture(android):
    header = android / prepare.TLS; header.parent.mkdir(parents=True)
    header.write_text('#define TLS_SLOT_ART_THREAD_SELF\n#define TLS_SLOT_THREAD_ID 99\n')
    for path, kind, name, old, new in prepare.EDITS:
        source = android / path; source.parent.mkdir(parents=True)
        source.write_text('// local comment retained\n' + kind + ' {\n    name: "' + name + '",\n' + old +
                          '\n            sentinel: true,\n        },\n    },\n}\n' +
                          'cc_library {\n    name: "unrelated",\n    cflags: ["-DDEXFILE_SUPPORT"],\n}\n')
    return header


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 native runtime ')
        self.addCleanup(self.temp.cleanup)
        self.android = Path(self.temp.name) / 'android'
        self.header = fixture(self.android)

    def test_scoped_android_exclusions_are_idempotent_and_preserve_tls_and_other_modules(self):
        before = self.header.read_bytes()
        first = prepare.prepare(self.android)
        self.assertEqual(first['protected_tls_header']['sha256'], prepare.info(self.header)['sha256'])
        for path, _, _, _, new in prepare.EDITS:
            content = (self.android / path).read_text()
            self.assertIn(new, content)
            self.assertIn('// local comment retained', content)
            self.assertIn('name: "unrelated",\n    cflags: ["-DDEXFILE_SUPPORT"]', content)
        saved = [(self.android / x[0]).read_bytes() for x in prepare.EDITS]
        second = prepare.prepare(self.android)
        self.assertTrue(all(x['state'] == 'already-applied' for x in second['edits']))
        self.assertEqual(saved, [(self.android / x[0]).read_bytes() for x in prepare.EDITS])
        self.assertEqual(self.header.read_bytes(), before)

    def test_unknown_second_module_is_refused_before_first_source_is_written(self):
        source = self.android / prepare.EDITS[1][0]
        source.write_text(source.read_text().replace('        recovery: {', '        android: {'))
        before = [(self.android / x[0]).read_bytes() for x in prepare.EDITS]
        with self.assertRaisesRegex(ValueError, 'Unexpected Android variant'):
            prepare.prepare(self.android)
        self.assertEqual(before, [(self.android / x[0]).read_bytes() for x in prepare.EDITS])
        self.assertFalse((self.android / '.n100-native-unwind.json').exists())

    def test_interrupted_two_file_update_can_resume(self):
        path, kind, name, old, new = prepare.EDITS[0]
        source = self.android / path
        source.write_text(prepare.transform(source.read_text(), kind, name, old, new)[0])
        report = prepare.prepare(self.android)
        self.assertEqual([x['state'] for x in report['edits']], ['already-applied', 'applied'])
        self.assertFalse(report['tls_header_modified'])

    @unittest.skipUnless(shutil.which('debugfs') and shutil.which('mke2fs'), 'e2fsprogs required')
    def test_new_real_apex_is_inspected_and_retained_dex_requirement_is_refused(self):
        prepare.prepare(self.android)
        root = self.android.parent / 'payload'; root.mkdir()
        name = b'com.android.runtime'
        manifest = b'\x0a' + bytes([len(name)]) + name + b'\x10\x01'
        (root / 'apex_manifest.pb').write_bytes(manifest)
        for path, (size, sha, elf_class, machine) in checker.reference.PINS.items():
            dest = root / path; dest.parent.mkdir(parents=True, exist_ok=True)
            data = bytearray(1024); data[:7] = b'\x7fELF' + bytes([elf_class, 1, 1]); struct.pack_into('<HH', data, 16, 3, machine)
            dest.write_bytes(data)
        image = self.android.parent / 'payload.img'
        with image.open('wb') as stream: stream.truncate(8 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-b', '4096', '-O', 'none,extent,filetype', '-d', str(root), str(image)],
                       check=True, capture_output=True)
        package = self.android / 'out/target/product/billie2/system/apex/com.android.runtime.apex'; package.parent.mkdir(parents=True)
        def write(pb):
            with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('apex_manifest.pb', pb); archive.write(image, 'apex_payload.img')
        write(manifest)
        actual_run = subprocess.run
        def run(args, **kwargs):
            if args[0] == 'git': return subprocess.CompletedProcess(args, 0, 'a' * 40 + '\n', '')
            return actual_run(args, **kwargs)
        with patch.object(checker.subprocess, 'run', side_effect=run):
            report = checker.inspect(self.android)
        self.assertTrue(report['passed'])
        self.assertFalse(report['previously_tested_core_hashes_unchanged'])
        self.assertFalse(report['device_tested']); self.assertFalse(report['ready_for_installation'])
        self.assertEqual(len(report['core_files']), 5)
        self.assertFalse(report['nnapi_requirement_resolved'])
        write(manifest + b'\x42\x0dlibdexfile.so')
        with self.assertRaisesRegex(ValueError, 'still requires libdexfile'):
            checker.inspect(self.android)

    def test_old_full_art_entrypoint_stops_before_build(self):
        result = subprocess.run(['bash', str(ROOT / 'tools/build-linker-providers.sh')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('build-native-runtime.sh', result.stderr)
        self.assertIn('ART', result.stderr)


if __name__ == '__main__': unittest.main()
