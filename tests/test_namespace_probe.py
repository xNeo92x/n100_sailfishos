import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('generated_namespace_probe', ROOT / 'tools/prepare-namespace-probe.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)


class NamespaceProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 namespace transport '); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.report, self.manifest = map(copy.deepcopy, probe.reference())
        self.preview = self.root / 'preview'; self.preview.mkdir()
        self.hal = self.root / 'hal'; self.hal.mkdir()
        self.output = self.root / 'output'
        (self.preview / 'report.json').write_bytes(probe.REFERENCE.read_bytes())
        payloads = self.preview / 'payloads'; payloads.mkdir()
        for name, content in [('apex_payload.img', b'Runtime fixture'), ('hal-system.tar.gz', b'HAL fixture')]:
            (self.hal / name).write_bytes(content)
            if name == 'apex_payload.img': self.manifest['payload'] = {'bytes': len(content), 'sha256': probe.sha(content)}
            else:
                self.manifest['hal_archive_bytes'] = len(content); self.manifest['hal_archive_sha256'] = probe.sha(content)
        for name, content in [('com.android.runtime', b'Runtime fixture'), ('com.android.vndk.v30', b'VNDK fixture')]:
            (payloads / (name + '.img')).write_bytes(content)
            self.report['apexes'][name]['payload'] = {'bytes': len(content), 'sha256': probe.sha(content)}
        for name, item in self.report['generated']['files'].items():
            path = self.preview / 'generated' / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item['text'].encode())

    def prepare(self):
        with patch.object(probe, 'reference', return_value=(self.report, self.manifest)), \
             patch.object(probe.preview, 'load_hal_bundle', return_value=(self.manifest, {})):
            return probe.prepare(self.preview, self.hal, self.output)

    def log(self):
        lines = ['N100_NAMESPACE_PROFILE ' + probe.REFERENCE_SHA,
                 'N100_NAMESPACE_MODE generated-config-no-ld-library-path', 'N100_NAMESPACE_CONFIG_READY',
                 'Verified payload SHA-256: ' + self.manifest['payload']['sha256'],
                 'Verified VNDK payload SHA-256: ' + self.report['apexes']['com.android.vndk.v30']['payload']['sha256'],
                 'Verified every regular file in the selected HAL RPM system tree.',
                 'N100_LINKER_HELP_EXIT=0', 'N100_LINKERCONFIG_HELP_EXIT=0']
        lines += ['Verified runtime core: ' + n for n in self.manifest['verified_core_reference']]
        lines += ['Verified linker config: ' + n for n in self.report['generated']['files']]
        lines += ['N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/' + n + ' 0'
                  for n in ('servicemanager', 'hwservicemanager', 'logd')]
        lines += ['N100_NAMESPACE_PROBE_RESULT exit=0 cleanup_failed=0']
        return '\n'.join(lines) + '\n'

    def test_real_reference_preserves_failed_strict_diagnostic_and_script_has_no_library_override(self):
        report, manifest = probe.reference()
        self.assertFalse(report['strict_diagnostic']['passed'])
        self.assertEqual(report['hal_elf_dependencies']['elf_files'], 245)
        self.assertEqual(set(report['hal_elf_dependencies']['direct_consumers_of_missing_providers']['libneuralnetworks.so']), set())
        script = probe.render(report, manifest)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        self.assertNotIn('LD_LIBRARY_PATH=', script)
        self.assertIn('unset LD_LIBRARY_PATH LD_PRELOAD LD_CONFIG_FILE', script)
        self.assertIn('"$P/linkerconfig"', script)
        self.assertIn('"$P/apex/com.android.vndk.v30"', script)
        self.assertIn('for loop in $loops', script)
        self.assertIn('losetup -r "$loop" "$work/vndk.img"', script)
        self.assertIn('"$work/vndk.img"|"${work#/}/vndk.img"', script)
        self.assertIn('grep -E', script)
        self.assertNotIn('echo "continue"', script)

    def test_transport_collect_and_overwrite_guards_preserve_limited_scope(self):
        bundle = self.prepare()
        self.assertFalse(bundle['source_strict_diagnostic_passed'])
        self.assertFalse(bundle['device_tested']); self.assertFalse(bundle['nnapi_requirement_resolved'])
        with self.assertRaisesRegex(ValueError, 'Output exists'): self.prepare()
        log = self.root / 'probe.txt'; log.write_text(self.log()); output = self.root / 'result.json'
        with patch.object(probe, 'reference', return_value=(self.report, self.manifest)):
            result = probe.collect(self.output, log, output)
            self.assertTrue(result['assessment']['generated_config_dependency_probe_passed'])
            self.assertFalse(result['vendor_namespace_tested']); self.assertFalse(result['rootfs_boot_tested'])
            self.assertFalse(result['nnapi_requirement_resolved'])
            with self.assertRaises(FileExistsError): probe.collect(self.output, log, output)
        for name in ('apex_payload.img', 'vndk_payload.img', 'hal-system.tar.gz', 'namespace-probe.sh', 'linkerconfig/ld.config.txt'):
            file = self.output / name; original = file.read_bytes(); file.write_bytes(original + b'changed')
            with self.subTest(name=name), self.assertRaises(ValueError):
                probe.verify_bundle(self.output, self.report, self.manifest)
            file.write_bytes(original)
        path = self.output / 'manifest.json'; changed = json.loads(path.read_text()); changed['source_strict_diagnostic_passed'] = True
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            probe.verify_bundle(self.output, self.report, self.manifest)

    def test_changed_vndk_or_generated_file_cannot_publish_bundle(self):
        for path in (self.preview / 'payloads/com.android.vndk.v30.img', self.preview / 'generated/ld.config.txt'):
            original = path.read_bytes(); path.write_bytes(b'wrong bytes')
            with self.subTest(path=path), self.assertRaises(ValueError): self.prepare()
            self.assertFalse(self.output.exists()); self.assertFalse(list(self.root.glob('.namespace-probe-*')))
            path.write_bytes(original)

    def test_old_partial_duplicate_failed_and_fallback_logs_never_pass(self):
        good = self.log()
        self.assertTrue(probe.classify(good, self.report, self.manifest)['generated_config_dependency_probe_passed'])
        bad = [good.replace('N100_NAMESPACE_PROFILE ', 'OLD_PROFILE '),
               good.replace('N100_NAMESPACE_CONFIG_READY\n', ''), good.replace('cleanup_failed=0', 'cleanup_failed=1'),
               good.replace('N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/logd 0', 'N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/logd 1'),
               good + 'N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/logd 0\n',
               good + 'linker: Warning: failed to find generated linker configuration from "/linkerconfig/ld.config.txt"\n',
               good + 'CANNOT LINK EXECUTABLE: library not found\n',
               good + 'WARNING: linker: invalid config\n', 'N100_NAMESPACE_PROBE_RESULT exit=0 cleanup_failed=0\n']
        for log in bad:
            with self.subTest(log=log):
                self.assertFalse(probe.classify(log, self.report, self.manifest)['generated_config_dependency_probe_passed'])


if __name__ == '__main__': unittest.main()
