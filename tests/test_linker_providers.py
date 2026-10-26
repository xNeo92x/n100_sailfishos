import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('linker_providers', ROOT / 'tools/check-linker-providers.py')
providers = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(providers)


def pb_string(field, value):
    data = value.encode()
    return bytes([field * 8 + 2, len(data)]) + data


class LinkerProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 providers ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def package(self, name, library):
        manifest = pb_string(1, name) + b'\x10\x01' + pb_string(7, library)
        tree = self.root / (name + '-source'); (tree / 'lib64').mkdir(parents=True)
        (tree / 'apex_manifest.pb').write_bytes(manifest)
        elf = bytearray(8192); elf[:7] = b'\x7fELF\x02\x01\x01'; struct.pack_into('<HH', elf, 16, 3, 183)
        (tree / 'lib64' / library).write_bytes(elf)
        image = self.root / (name + '.img')
        with image.open('wb') as stream: stream.truncate(8 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-b', '4096', '-O', 'none,extent,filetype', '-d', str(tree), str(image)],
                       check=True, capture_output=True)
        package = self.root / (name + '.apex')
        with zipfile.ZipFile(package, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('apex_manifest.pb', manifest); archive.write(image, 'apex_payload.img')
        return package

    @unittest.skipUnless(shutil.which('mke2fs') and shutil.which('debugfs'), 'e2fsprogs required')
    def test_four_real_ext4_payloads_and_capex_preserve_native_exports(self):
        android = self.root / 'android'; folder = android / 'out/target/product/billie2/system/apex'; folder.mkdir(parents=True)
        for index, (name, library) in enumerate(providers.PROVIDERS.items()):
            package = self.package(name, library)
            if index == 0:
                with zipfile.ZipFile(folder / (name + '.capex'), 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(package, 'original_apex')
            else:
                shutil.copyfile(package, folder / package.name)
        actual_run = subprocess.run
        def run(args, **kwargs):
            if args[0] == 'git': return subprocess.CompletedProcess(args, 0, 'a' * 40 + '\n', '')
            return actual_run(args, **kwargs)
        output = self.root / 'inspection'
        with patch.object(providers.subprocess, 'run', side_effect=run):
            result = providers.inspect(android, output)
        self.assertTrue(result['passed']); self.assertFalse(result['phone_modified'])
        for name, library in providers.PROVIDERS.items():
            item = result['apexes'][name]
            self.assertIn(library, item['manifest']['provide_native_libs'])
            self.assertEqual(item['aarch64_sample']['elf_machine'], 183)
            self.assertEqual(item['aarch64_sample']['payload_path'], 'lib64/' + library)
            self.assertTrue((output / 'packages' / (name + '.apex')).is_file())
        self.assertEqual(len(result['source_revisions']), 4)
        # Feed this real provider report into the staging path used by the next preview.
        report = {'apexes': {}}
        preview = providers.preview
        preview.add_provider_inputs(report, output / 'report.json', self.root / 'next-root', self.root / 'next-payloads', shutil.which('debugfs'))
        self.assertEqual(set(report['apexes']), set(providers.PROVIDERS))
        self.assertEqual(report['provider_inputs']['apexes']['com.android.art']['package']['sha256'], result['apexes']['com.android.art']['package']['sha256'])
        # A package swapped after inspection must be rejected before staging.
        package = output / 'packages/com.android.art.apex'; package.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'differs from reviewed'):
            preview.add_provider_inputs({'apexes': {}}, output / 'report.json', self.root / 'bad-root', self.root / 'bad-payloads', shutil.which('debugfs'))

    def test_capex_duplicate_or_wrong_identity_is_refused(self):
        name = 'com.android.art'
        plain = io.BytesIO()
        with zipfile.ZipFile(plain, 'w') as archive:
            archive.writestr('apex_manifest.pb', pb_string(1, 'com.android.wrong') + b'\x10\x01')
            archive.writestr('apex_payload.img', b'x' * 2048)
        capex = self.root / 'fixture.capex'
        with zipfile.ZipFile(capex, 'w') as archive: archive.writestr('original_apex', plain.getvalue())
        with self.assertRaisesRegex(ValueError, 'Unexpected APEX manifest'):
            providers.normalize_package(capex, self.root / 'wrong.apex', name)

    def test_build_guard_and_failed_mka_stop_before_inspection(self):
        actual = ROOT / 'tools/build-native-runtime.sh'
        result = subprocess.run(['bash', str(actual)], env=dict(os.environ, MERSDKUBU=''), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('Ubuntu', result.stderr)
        if not shutil.which('debugfs'): return
        repo = self.root / 'port'; (repo / 'tools').mkdir(parents=True); (repo / 'config').mkdir()
        shutil.copyfile(actual, repo / 'tools/build-native-runtime.sh')
        (repo / 'config/port.env').write_text('')
        (repo / 'tools/n100.py').write_text('print("verified fixture")\n')
        (repo / 'tools/prepare-native-unwind.py').write_text('print("prepared fixture")\n')
        android = self.root / 'android'; (android / 'build').mkdir(parents=True)
        (android / 'build/envsetup.sh').write_text('breakfast() { return 0; }\nmka() { echo build-failure >&2; return 37; }\n')
        work = self.root / 'work'
        result = subprocess.run(['bash', str(repo / 'tools/build-native-runtime.sh')],
            env=dict(os.environ, MERSDKUBU='1', ANDROID_ROOT=str(android), N100_NATIVE_RUNTIME_WORK=str(work)), capture_output=True, text=True)
        self.assertEqual(result.returncode, 37)
        self.assertIn('build-failure', (work / 'build.log').read_text())
        self.assertFalse((work / 'provider-inputs').exists())


if __name__ == '__main__':
    unittest.main()
