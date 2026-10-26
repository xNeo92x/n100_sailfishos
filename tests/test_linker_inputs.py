import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('linker_inputs', ROOT / 'tools/check-linker-inputs.py')
inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inputs)


def manifest(name='com.android.vndk.v30'):
    data = name.encode()
    return b'\x0a' + bytes([len(data)]) + data + b'\x10\x01'


class LinkerInputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 linker inputs ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_manifest_name_version_and_bounds(self):
        self.assertEqual(inputs.manifest_info(manifest()), {'name': 'com.android.vndk.v30', 'version': 1})
        for data in (manifest('com.android.vndk.v33'), b'\x0a\xff', b'\x0f', manifest() + manifest(), b'x' * 65537):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                inputs.manifest_info(data)

    @unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'), 'e2fsprogs required')
    def test_real_ext4_vndk_package_lists_and_host_report(self):
        tree = self.root / 'tree'; (tree / 'etc').mkdir(parents=True); (tree / 'lib64').mkdir()
        for label in inputs.LISTS:
            (tree / 'etc' / (label + '.libraries.30.txt')).write_text('libfixture.so\n' if label != 'vndkprivate' else '')
        elf = bytearray(64); elf[:7] = b'\x7fELF\x02\x01\x01'; struct.pack_into('<HH', elf, 16, 3, 183)
        (tree / 'lib64/libfixture.so').write_bytes(elf)
        image = self.root / 'payload.img'
        with image.open('wb') as stream: stream.truncate(16 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-b', '4096', '-O', 'none,extent,filetype', '-d', str(tree), str(image)], check=True, capture_output=True)
        android = self.root / 'android'; product = android / 'out/target/product/billie2'
        package = product / 'system_ext/apex/com.android.vndk.v30.apex'; package.parent.mkdir(parents=True)
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('apex_manifest.pb', manifest())
            archive.writestr('apex_payload.img', image.read_bytes())
        info = inputs.inspect_apex(package, shutil.which('debugfs'))
        self.assertEqual(info['manifest']['name'], 'com.android.vndk.v30')
        self.assertEqual(info['library_lists']['vndkprivate']['entries'], [])
        self.assertEqual(info['aarch64_library_sample']['elf_machine'], 183)
        self.assertEqual(info['payload']['bytes'], image.stat().st_size)
        base = product / 'obj/ETC/system_linker_config_intermediates/system_linker_config'
        base.parent.mkdir(parents=True); base.write_bytes(b'fixture protobuf')
        host_bin = android / 'out/host/linux-x86/bin'; host_bin.mkdir(parents=True)
        host_elf = bytearray(elf); struct.pack_into('<H', host_elf, 18, 62)
        for name, data in [('linkerconfig', host_elf), ('conv_linker_config', b'#!/usr/bin/python3\n')]:
            p = host_bin / name; p.write_bytes(data); p.chmod(0o755)
        actual_run = subprocess.run
        def command(args, **kwargs):
            if str(args[0]).endswith('/linkerconfig'):
                return subprocess.CompletedProcess(args, 0, 'Usage --root --vndk --target', '')
            if str(args[0]).endswith('/conv_linker_config'):
                return subprocess.CompletedProcess(args, 0, 'Usage proto systemprovide', '')
            if args[0] == 'git':
                return subprocess.CompletedProcess(args, 0, 'a' * 40 + '\n', '')
            return actual_run(args, **kwargs)
        with patch.object(inputs.subprocess, 'run', side_effect=command):
            report = inputs.inspect(android, Path('out/host/linux-x86/bin'))
        self.assertTrue(report['passed'])
        self.assertFalse(report['namespace_configuration_generated'])
        self.assertEqual(report['host_tools']['linkerconfig']['elf_machine'], 62)
        self.assertEqual(len(report['source_revisions']), 3)
        self.assertEqual(report['base_system_config_candidates'][0]['bytes'], len(b'fixture protobuf'))
        # A wrong-name package is refused before any filesystem dumping.
        with zipfile.ZipFile(package, 'w') as archive:
            archive.writestr('apex_manifest.pb', manifest('com.android.vndk.v33'))
            archive.writestr('apex_payload.img', image.read_bytes())
        with self.assertRaisesRegex(ValueError, 'VNDK-30'):
            inputs.inspect_apex(package, shutil.which('debugfs'))

    def test_failed_inspection_replaces_stale_success_report(self):
        report = self.root / 'report.json'; report.write_text('{"passed": true}')
        result = subprocess.run([sys.executable, str(ROOT / 'tools/check-linker-inputs.py'),
                                 '--android-root', str(self.root), '--host-bin', str(self.root / 'tools'),
                                 '--output', str(report)], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(json.loads(report.read_text())['passed'])

    def test_build_guard_stops_outside_ubuntu(self):
        result = subprocess.run(['bash', str(ROOT / 'tools/build-linker-inputs.sh')],
                                env=dict(os.environ, MERSDKUBU=''), capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('Ubuntu HABUILD_SDK', result.stderr)

    @unittest.skipUnless(shutil.which('debugfs'), 'debugfs required')
    def test_build_requests_vndk_and_exact_host_variants_and_preserves_failure(self):
        port = self.root / 'port'; (port / 'tools').mkdir(parents=True); (port / 'config').mkdir()
        shutil.copy2(ROOT / 'tools/build-linker-inputs.sh', port / 'tools/build-linker-inputs.sh')
        (port / 'config/port.env').write_text('')
        for name in ('n100.py', 'check-linker-inputs.py'):
            (port / 'tools' / name).write_text('# Fixture: checks tested separately.\n')
        android = self.root / 'android'; (android / 'packages/modules/vndk/apex').mkdir(parents=True)
        (android / 'packages/modules/vndk/apex/Android.bp').write_text('fixture')
        (android / 'prebuilts/vndk/v30').mkdir(parents=True); (android / 'build').mkdir()
        (android / 'build/envsetup.sh').write_text('''breakfast() { echo "$*" > selected; }
get_build_var() { echo out/host/linux-x86/bin; }
mka() { echo "$*" > requested; echo build-output; return "${FIXTURE_EXIT:-0}"; }
repo() { echo fixture-resolved-manifest; }
''')
        args = ['bash', str(port / 'tools/build-linker-inputs.sh')]
        env = dict(os.environ, MERSDKUBU='1', ANDROID_ROOT=str(android))
        result = subprocess.run(args, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((android / 'requested').read_text().split(),
                         ['com.android.vndk.v30', 'system_linker_config',
                          'out/host/linux-x86/bin/linkerconfig', 'out/host/linux-x86/bin/conv_linker_config'])
        self.assertIn('build-output', (android / 'n100-build-logs/linker-inputs.log').read_text())
        self.assertEqual((android / 'selected').read_text().strip(), 'billie2')
        result = subprocess.run(args, env=dict(env, FIXTURE_EXIT='47'), capture_output=True, text=True)
        self.assertEqual(result.returncode, 47)


if __name__ == '__main__':
    unittest.main()
