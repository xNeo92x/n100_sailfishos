import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_device_probe', ROOT / 'tools/prepare-native-runtime-probe.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)


class NativeDeviceProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 native device '); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = copy.deepcopy(probe.reviewed())
        self.source = self.root / 'native-runtime-rpm-fixture'; self.source.mkdir()
        for name, content in [('apex_payload.img', b'fixture payload'), ('hal-system.tar.gz', b'fixture archive')]:
            (self.source / name).write_bytes(content)
            if name == 'apex_payload.img':
                self.manifest['payload'] = {'bytes': len(content), 'sha256': probe.sha(content)}
            else:
                self.manifest['hal_archive_bytes'] = len(content)
                self.manifest['hal_archive_sha256'] = probe.sha(content)
        self.ref = self.root / 'reviewed.json'
        raw = json.dumps(self.manifest).encode(); self.ref.write_bytes(raw)
        (self.source / 'manifest.json').write_bytes(raw)
        self.output = self.root / 'probe'

    def context(self):
        return patch.multiple(probe, REFERENCE=self.ref, REFERENCE_SHA=probe.sha(self.ref.read_bytes()))

    def prepare(self):
        with self.context(), patch.object(probe, 'reviewed', return_value=self.manifest):
            return probe.prepare(self.source, self.output)

    def good_log(self):
        lines = [probe.PROFILE, 'Verified payload SHA-256: ' + self.manifest['payload']['sha256'],
                 'Verified every regular file in the selected HAL RPM system tree.',
                 'N100_LINKER_HELP_EXIT=0', 'N100_LINKERCONFIG_HELP_EXIT=0']
        lines += ['Verified runtime core: ' + n for n in self.manifest['verified_core_reference']]
        lines += ['N100_DEPENDENCY_EXIT /system/bin/' + n + ' 0'
                  for n in ('servicemanager', 'hwservicemanager', 'logd')]
        lines += ['N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0']
        return '\n'.join(lines) + '\n'

    def test_exact_reviewed_real_manifest_and_changed_files(self):
        data = probe.reviewed()
        self.assertEqual(len(data['hal_tree']['regular_file_hashes']), 260)
        self.assertEqual(data['source_rpm']['sha256'], 'b6714b06b16f38e25d0298e4efe4e39b63b1b92128678e49c00b49b7fd5a3bc6')
        self.assertEqual(data['hal_tree']['regular_file_hashes']['lib64/libc.so'],
                         '65aa28664008ff41d455708a9c63e9c620184d0370e5cba7a982f18f9897a722')
        script = probe.render(data)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        self.assertIn(probe.PROFILE, script)
        self.assertIn('65aa28664008ff41d455708a9c63e9c620184d0370e5cba7a982f18f9897a722', script)
        self.assertNotIn('d109ef03d686bd6b549a602f9ed5d774af2fdccc312f178d2f42b188787a6d3a', script)
        self.assertIn('ro,noload', script); self.assertIn('cleanup_failed=$failed', script)

    def test_bundle_is_exclusive_and_collect_checks_exact_files_and_scope(self):
        bundle = self.prepare()
        self.assertFalse(bundle['device_tested'])
        self.assertFalse(bundle['flashable_release'])
        with self.assertRaisesRegex(ValueError, 'Output exists'): self.prepare()
        log = self.root / 'log.txt'; log.write_text(self.good_log())
        result_path = self.root / 'result.json'
        with self.context(), patch.object(probe, 'reviewed', return_value=self.manifest):
            result = probe.collect(self.output, log, result_path)
            self.assertTrue(result['assessment']['dependency_probe_passed'])
            self.assertFalse(result['rootfs_boot_tested'])
            self.assertFalse(result['android_services_started'])
            with self.assertRaises(FileExistsError): probe.collect(self.output, log, result_path)
            for name in ('runtime-probe.sh', 'hal-system.tar.gz', 'apex_payload.img'):
                path = self.output / name; saved = path.read_bytes(); path.write_bytes(saved + b'changed')
                with self.subTest(name=name), self.assertRaises(ValueError):
                    probe.collect(self.output, log, self.root / 'tampered.json')
                self.assertFalse((self.root / 'tampered.json').exists()); path.write_bytes(saved)
            path = self.output / 'manifest.json'; changed = json.loads(path.read_text()); changed['device_tested'] = True
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, 'manifest changed'):
                probe.collect(self.output, log, self.root / 'false-scope.json')

    def test_bad_input_and_ambiguous_discovery_are_refused(self):
        with self.context():
            self.assertEqual(probe.locate(self.root), self.source)
            duplicate = self.root / 'native-runtime-rpm-duplicate'; duplicate.mkdir()
            (duplicate / 'manifest.json').write_bytes(self.ref.read_bytes())
            with self.assertRaisesRegex(ValueError, 'exactly one'): probe.locate(self.root)
        (self.source / 'apex_payload.img').write_bytes(b'old payload')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.output.exists())
        (self.source / 'manifest.json').write_text('{}')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(list(self.root.glob('.native-probe-*')))

    def test_partial_old_failed_or_duplicate_logs_never_pass(self):
        good = self.good_log()
        bad = [good.replace(probe.PROFILE, 'old profile'),
               good.replace('N100_DEPENDENCY_EXIT /system/bin/logd 0', 'N100_DEPENDENCY_EXIT /system/bin/logd 1'),
               good.replace('cleanup_failed=0', 'cleanup_failed=1'),
               good + 'N100_LINKER_HELP_EXIT=0\n',
               good + 'N100_DEPENDENCY_EXIT /system/bin/logd 124\n',
               'N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0\n']
        for log in bad:
            with self.subTest(log=log):
                self.assertFalse(probe.classify(log, self.manifest)['dependency_probe_passed'])


if __name__ == '__main__': unittest.main()
