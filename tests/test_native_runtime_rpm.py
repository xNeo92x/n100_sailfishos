import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_rpm', ROOT / 'tools/prepare-native-runtime-rpm.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)
SPEC = importlib.util.spec_from_file_location('cpio_test_fixture', ROOT / 'tests/test_runtime_rpm_probe.py')
fixture = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(fixture)


class NativeRpmTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 native rpm '); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        payload = bytearray(4096); payload[1080:1082] = b'\x53\xef'; self.payload = bytes(payload)
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, 'w') as target: target.writestr('apex_payload.img', self.payload)
        self.files = {'apex/com.android.runtime.apex': archive.getvalue(), 'bin/hwservicemanager': b'hw',
                      'bin/logd': b'log', 'bin/linkerconfig': b'static tool',
                      'lib64/libc.so': b'new reviewed libc', 'lib64/libdl.so': b'reviewed libdl', 'lib64/libm.so': b'reviewed libm'}
        pins = {n: {'bytes': len(v), 'sha256': probe.rpm.sha(v)} for n, v in self.files.items()}
        self.reference = {'passed': True, 'kind': 'native-runtime-no-dex-build',
            'hal_build_outputs': {'passed': True, 'files': {
                **{'system/' + n: p for n, p in pins.items() if not n.startswith('lib64/') and n != 'bin/linkerconfig'},
                'apex/com.android.runtime/bin/linkerconfig': pins['bin/linkerconfig']}},
            'core_files': {'lib64/bionic/' + Path(n).name: p for n, p in pins.items() if n.startswith('lib64/')},
            'apex': pins['apex/com.android.runtime.apex'],
            'payload': {'bytes': len(self.payload), 'sha256': probe.rpm.sha(self.payload)},
            'manifest': {'require_native_libs': ['liblog.so']}, 'previously_tested_core_hashes_unchanged': False}
        self.package = self.root / 'main.rpm'; self.package.write_bytes(b'RPM fixture')

    def prepare(self, files, output):
        raw = fixture.archive(*(fixture.member(probe.rpm.PREFIX + n, v, stat.S_IFREG | 0o755, inode=i+1)
                                for i, (n, v) in enumerate(files.items())))
        package_info = {'bytes': self.package.stat().st_size, 'sha256': probe.rpm.sha(self.package.read_bytes()), 'name': 'droid-hal-billie2', 'arch': 'aarch64'}
        actual_run = subprocess.run
        def command(args, **kwargs):
            if args[0] == 'rpm2cpio':
                kwargs['stdout'].write(raw); return subprocess.CompletedProcess(args, 0)
            return actual_run(args, **kwargs)
        with patch.object(probe, 'reference', return_value=(self.reference, 'a'*64)), \
             patch.object(probe.rpm, 'select_rpm', return_value=(self.package, package_info)), \
             patch.object(probe.subprocess, 'run', side_effect=command):
            return probe.prepare(self.root, output)

    def test_rebuilt_rpm_matches_new_payload_and_flat_libc_with_exclusive_bundle(self):
        output = self.root / 'bundle'
        result = self.prepare(self.files, output)
        self.assertTrue(result['passed']); self.assertTrue(result['flat_bionic_matches_new_build'])
        self.assertEqual(result['hal_tree']['regular_file_hashes']['lib64/libc.so'], probe.rpm.sha(self.files['lib64/libc.so']))
        self.assertEqual((output / 'apex_payload.img').read_bytes(), self.payload)
        self.assertFalse((output / 'runtime-probe.sh').exists())
        self.assertFalse(result['device_script_generated']); self.assertFalse(result['device_tested'])
        self.assertEqual(result['hal_archive_sha256'], probe.rpm.sha((output / 'hal-system.tar.gz').read_bytes()))
        saved = (output / 'manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError, 'Output exists'):
            self.prepare(self.files, output)
        self.assertEqual((output / 'manifest.json').read_bytes(), saved)

    def test_old_apex_or_stale_flat_libc_cannot_publish_a_bundle(self):
        for name, value in [('apex/com.android.runtime.apex', b'old apex'), ('lib64/libc.so', b'old libc')]:
            files = dict(self.files); files[name] = value; target = self.root / ('bad-' + Path(name).name)
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.prepare(files, target)
            self.assertFalse(target.exists())
            self.assertFalse(list(self.root.glob('.native-rpm-*')))

    def test_payload_hash_mismatch_is_not_treated_as_new_build(self):
        self.reference['payload']['sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'payload differs'):
            self.prepare(self.files, self.root / 'bad-payload')
        self.assertFalse((self.root / 'bad-payload').exists())

    def test_reviewed_reference_uses_new_core_without_changing_old_probe_defaults(self):
        report, digest = probe.reference()
        self.assertEqual(digest, probe.rpm.sha(probe.REFERENCE.read_bytes()))
        expected = probe.expected_files(report)
        self.assertEqual(expected['lib64/libc.so']['sha256'], '65aa28664008ff41d455708a9c63e9c620184d0370e5cba7a982f18f9897a722')
        self.assertNotEqual(expected['apex/com.android.runtime.apex']['sha256'], probe.rpm.runtime.APEX_SHA)
        self.assertEqual(probe.rpm.runtime.APEX_SHA, '293e90ce25eec01a38b723731eea03d378750e7ee0287df93305d43073e8bdf0')
        self.assertNotIn('libdexfile.so', report['manifest']['require_native_libs'])
        for name in ('bin/hwservicemanager', 'bin/logd', 'bin/linkerconfig'):
            self.assertIn(name, expected)


if __name__ == '__main__': unittest.main()
