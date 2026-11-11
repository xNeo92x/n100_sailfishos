import importlib.util
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('service_tool', ROOT / 'tools/check-service-tool.py')
tool = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(tool)


class ServiceToolTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.binary = self.root / 'service'; self.binary.write_bytes(b'fixture')
        self.hal = self.root / 'hal.tar.gz'
        listing = ''.join('{}  lib64/{}\n'.format('0' * 64, n) for n in ('libbinder.so', 'libutils.so', 'libcutils.so'))
        with tarfile.open(self.hal, 'w:gz') as archive:
            info = tarfile.TarInfo('n100-files.sha256'); info.size = len(listing)
            archive.addfile(info, io.BytesIO(listing.encode()))

    def run_check(self, **elf):
        info = {'class64': True, 'aarch64': True, 'type_dyn': True, 'interpreter': '/system/bin/linker64',
                'needed': ['libcutils.so', 'libutils.so', 'libbinder.so', 'libc++.so', 'libc.so']}
        info.update(elf)
        with patch.object(tool, 'elf', return_value=info):
            return tool.check(self.binary, self.hal, self.root / ('out' + str(len(list(self.root.glob('out*'))))))

    def test_staged_only_when_all_libraries_are_in_the_hal_tree(self):
        report = self.run_check()
        self.assertEqual(report['problems'], ['libraries missing from the HAL tree: libc++.so'])
        report = self.run_check(needed=['libcutils.so', 'libutils.so', 'libbinder.so', 'libc.so', 'libm.so'])
        self.assertTrue(report['passed']); self.assertFalse(report['packaged_in_rpm'])
        self.assertTrue((self.root / 'out1/service').is_file())

    def test_wrong_abi_or_interpreter_is_rejected(self):
        ok = ['libbinder.so', 'libc.so']
        self.assertFalse(self.run_check(aarch64=False, needed=ok)['passed'])
        self.assertFalse(self.run_check(interpreter='/lib/ld-linux-aarch64.so.1', needed=ok)['passed'])
        self.assertFalse(self.run_check(needed=['libc.so'])['passed'])
        self.assertFalse((self.root / 'out2/service').exists())

    def test_launcher_builds_only_the_service_target(self):
        script = ROOT / 'tools/run-service-tool-build.sh'
        subprocess.run(['bash', '-n', str(script)], check=True)
        text = script.read_text()
        self.assertIn('mka service 2>&1', text); self.assertNotIn('fastboot', text)
        self.assertNotIn('build-packages.sh', text)


if __name__ == '__main__': unittest.main()
