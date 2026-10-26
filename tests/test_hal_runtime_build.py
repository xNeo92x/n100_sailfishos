import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

from test_hal_layout import executable


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('check_runtime_build', ROOT / 'tools/check-hal-runtime.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
HELPER = ROOT / 'adaptation/rpm/helpers/install-android-runtime.sh'


class HalRuntimeBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 runtime package ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.android = self.root / 'android'
        self.product = self.android / 'out/target/product/billie2'
        for name in runtime.SERVICES:
            binary = self.product / 'system/bin' / name
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(executable(3, '/system/bin/linker64'))
            rc = self.product / 'system/etc/init' / (name + '.rc')
            rc.parent.mkdir(parents=True, exist_ok=True)
            rc.write_text('service {} /system/bin/{}\n    class core\n'.format(name, name))
        (self.product / 'system/bin/linkerconfig').write_bytes(executable())
        for name in runtime.EXTRA_ETC:
            (self.product / 'system/etc' / name).write_bytes(b'fixture' if name.endswith('.pb') else b'')
        apex = self.product / runtime.APEX
        apex.parent.mkdir(parents=True)
        with zipfile.ZipFile(apex, 'w') as archive:
            archive.writestr('apex_payload.img', b'fixture-payload')
        self.buildroot = self.root / 'staged'
        (self.buildroot / 'sbin').mkdir(parents=True)
        (self.buildroot / 'sbin/droid-hal-init').write_text('staged marker')
        (self.buildroot / 'usr/libexec/droid-hybris/system').mkdir(parents=True)
        self.work = self.root / 'rpm-work'
        (self.work / 'tmp').mkdir(parents=True)
        (self.work / 'tmp/droid-hal.files').write_text('/usr/libexec/droid-hybris/system/etc/init/*.rc\n')

    def run_helper(self):
        return subprocess.run(['bash', str(HELPER), str(self.android), str(self.buildroot)],
                              cwd=self.work, capture_output=True, text=True)

    def test_valid_outputs_and_staged_payload_are_consistent_and_idempotent(self):
        report = runtime.inspect(self.android)
        self.assertTrue(report['passed'], report['errors'])
        self.assertTrue(report['files']['system/bin/linkerconfig']['static_executable'])
        for _ in range(2):
            result = self.run_helper()
            self.assertEqual(result.returncode, 0, result.stderr)
        base = self.buildroot / 'usr/libexec/droid-hybris/system'
        for name in runtime.SERVICES:
            self.assertEqual((base / 'bin' / name).read_bytes(), (self.product / 'system/bin' / name).read_bytes())
            self.assertTrue((base / 'etc/init' / (name + '.rc')).is_file())
            self.assertEqual((base / 'bin' / name).stat().st_mode & 0o777, 0o755)
        self.assertEqual((base / 'apex/com.android.runtime.apex').read_bytes(), (self.product / runtime.APEX).read_bytes())
        lines = (self.work / 'tmp/droid-hal.files').read_text().splitlines()
        self.assertEqual(len(lines), len(set(lines)))
        self.assertIn('/usr/libexec/droid-hybris/system/apex/com.android.runtime.apex', lines)
        self.assertFalse((self.buildroot / 'apex').exists())  # no activation here

    def test_missing_service_and_wrong_abi_stop_before_packaging(self):
        (self.product / 'system/bin/hwservicemanager').unlink()
        (self.product / 'system/bin/logd').write_bytes(executable(3, '/lib/ld-linux-aarch64.so.1'))
        report = runtime.inspect(self.android)
        self.assertFalse(report['passed'])
        self.assertTrue(any('hwservicemanager' in error for error in report['errors']))
        self.assertTrue(any('interpreter' in error for error in report['errors']))
        self.assertNotEqual(self.run_helper().returncode, 0)
        self.assertFalse((self.buildroot / 'usr/libexec/droid-hybris/system/bin').exists())

    def test_wrong_service_declaration_and_dynamic_linkerconfig_are_rejected(self):
        (self.product / 'system/etc/init/logd.rc').write_text('service other /system/bin/logd\n')
        (self.product / 'system/bin/linkerconfig').write_bytes(executable(3, '/system/bin/linker64'))
        report = runtime.inspect(self.android)
        self.assertTrue(any('declaration' in error for error in report['errors']))
        self.assertTrue(any('static linkerconfig' in error for error in report['errors']))

    def test_unexpected_staged_parent_link_is_preserved(self):
        outside = self.root / 'outside'; outside.mkdir()
        base = self.buildroot / 'usr/libexec/droid-hybris/system'
        (base / 'apex').symlink_to(outside, target_is_directory=True)
        result = self.run_helper()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((base / 'bin').exists())


if __name__ == '__main__':
    unittest.main()
