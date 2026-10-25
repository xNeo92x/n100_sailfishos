import hashlib
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
SPEC = importlib.util.spec_from_file_location('runtime_bundle', ROOT / 'tools/prepare-runtime-probe.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def elf(elf_class=2, machine=183):
    data = bytearray(64)
    data[:7] = b'\x7fELF' + bytes([elf_class, 1, 1])
    struct.pack_into('<H', data, 16, 3)
    struct.pack_into('<H', data, 18, machine)
    return bytes(data)


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 runtime test ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def package(self, payload):
        source = self.root / 'android/out/target/product/billie2/system/apex/com.android.runtime.apex'
        source.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(source, 'w') as archive:
            archive.writestr('apex_payload.img', payload)
        return source

    def test_wrong_source_stops_before_output_creation(self):
        self.package(b'not the reviewed APEX')
        output = self.root / 'bundle'
        with self.assertRaises(ValueError):
            runtime.prepare(self.root / 'android', output)
        self.assertFalse(output.exists())

    def test_payload_magic_and_existing_output_guard(self):
        source = self.package(b'bad' * 1024)
        with patch.object(runtime, 'APEX_BYTES', source.stat().st_size), patch.object(runtime, 'APEX_SHA', runtime.digest(source.read_bytes())), patch.object(runtime, 'PAYLOAD_BYTES', 3072):
            with self.assertRaisesRegex(ValueError, 'raw ext4'):
                runtime.read_payload(source)
        self.root.joinpath('bundle').mkdir()
        with self.assertRaisesRegex(ValueError, 'exists'):
            runtime.prepare(self.root / 'android', self.root / 'bundle')

    def test_template_syntax_and_nonroot_guard(self):
        template = (ROOT / 'tools/runtime-probe.sh.in').read_text()
        script = template.replace('@PAYLOAD_SHA@', 'a' * 64).replace('@PAYLOAD_BYTES@', '4096').replace('@FILE_CHECKS@', 'bin/linker64 64 ' + 'b' * 64)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        path = self.root / 'probe.sh'; path.write_text(script)
        ident = self.root / 'id'; ident.write_text('#!/bin/sh\necho 1000\n'); ident.chmod(0o755)
        result = subprocess.run(['/bin/sh', str(path), 'run'], env={'PATH': str(self.root)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('initramfs root shell', result.stderr)
        plan = subprocess.run(['/bin/sh', str(path), '--plan'], capture_output=True, text=True, check=True)
        self.assertIn('a' * 64, plan.stdout)

    def test_production_pins_match_uploaded_inventory(self):
        report = json.loads((ROOT / 'validation/android-bringup-report-user-20261025.json').read_text())
        for name, (size, sha, elf_class, machine) in runtime.PINS.items():
            record = next(p for p in report['candidates'] if p['path'].endswith('/apex/com.android.runtime/' + name))
            self.assertEqual((size, sha, elf_class, machine), (record['bytes'], record['sha256'], record['elf_class'], record['elf_machine']))
        apex = next(p for p in report['candidates'] if p['path'].endswith('/system/apex/com.android.runtime.apex'))
        self.assertEqual((runtime.APEX_BYTES, runtime.APEX_SHA), (apex['bytes'], apex['sha256']))

    @unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'), 'e2fsprogs required')
    def test_real_ext4_apex_bundle_and_report_integrity(self):
        tree = self.root / 'tree'; (tree / 'bin').mkdir(parents=True)
        core = elf()
        (tree / 'bin/linker64').write_bytes(core)
        image = self.root / 'payload.img'
        with image.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-b', '4096', '-O', 'none,extent,filetype', '-d', str(tree), str(image)], check=True, capture_output=True)
        payload = image.read_bytes(); source = self.package(payload)
        pins = {'bin/linker64': (len(core), runtime.digest(core), 2, 183)}
        with patch.object(runtime, 'APEX_BYTES', source.stat().st_size), patch.object(runtime, 'APEX_SHA', runtime.digest(source.read_bytes())), patch.object(runtime, 'PAYLOAD_BYTES', len(payload)), patch.object(runtime, 'PINS', pins):
            bundle = self.root / 'bundle'
            runtime.prepare(self.root / 'android', bundle)
            self.assertEqual((bundle / 'apex_payload.img').read_bytes(), payload)
            self.assertFalse(any(bundle.glob('core-*')))
            log = self.root / 'probe.txt'; log.write_text('N100_LINKER_HELP_EXIT=0\n')
            report = self.root / 'report.json'
            runtime.collect(bundle, log, report)
            self.assertTrue(json.loads(report.read_text())['bundle_integrity_rechecked'])
            with self.assertRaisesRegex(ValueError, 'exists'):
                runtime.collect(bundle, log, report)
            (bundle / 'runtime-probe.sh').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                runtime.collect(bundle, log, self.root / 'other.json')
            # Same byte/hash pin but wrong expected ABI must still be refused.
            bad = {'bin/linker64': (len(core), runtime.digest(core), 2, 62)}
            with patch.object(runtime, 'PINS', bad):
                with self.assertRaisesRegex(ValueError, 'ELF ABI'):
                    runtime.prepare(self.root / 'android', self.root / 'bad-abi')
            self.assertFalse((self.root / 'bad-abi').exists())
            # Container and flattened install variants may differ. The pinned
            # APEX container is the source of truth; retain the comparison.
            with patch.object(runtime, 'PINS', {'bin/linker64': (64, '0' * 64, 2, 183)}):
                other = self.root / 'different-flat-reference'
                runtime.prepare(self.root / 'android', other)
                manifest = json.loads((other / 'manifest.json').read_text())
                self.assertFalse(manifest['verified_core_files']['bin/linker64']['matches_flattened_reference'])
                self.assertEqual(manifest['verified_core_files']['bin/linker64']['sha256'], runtime.digest(core))


if __name__ == '__main__':
    unittest.main()
