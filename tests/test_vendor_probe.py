import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('stock_vendor_probe', ROOT / 'tools/prepare-vendor-probe.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)
SPEC = importlib.util.spec_from_file_location('vendor_namespace_fixture', ROOT / 'tests/test_namespace_probe.py')
fixture = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(fixture)


class VendorProbeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.NamespaceProbeTests(); self.fixture.setUp(); self.addCleanup(self.fixture.doCleanups)
        self.fixture.prepare()
        self.report = self.fixture.report; self.manifest = self.fixture.manifest
        self.root = self.fixture.root; self.source = self.fixture.output; self.output = self.root / 'vendor-out'

    def prepare(self):
        with patch.object(probe, 'reference', return_value=(self.report, self.manifest)):
            return probe.prepare(self.source, self.output)

    def log(self, code=0):
        core = self.fixture.log().replace('N100_NAMESPACE_PROFILE ' + probe.ns.REFERENCE_SHA,
                                         'N100_VENDOR_PROFILE ' + probe.REFERENCE_SHA).replace(
            'N100_NAMESPACE_MODE generated-config-no-ld-library-path',
            'N100_VENDOR_MODE generated-config-stock-vendor-no-ld-library-path').replace(
            'N100_NAMESPACE_PROBE_RESULT exit=0 cleanup_failed=0\n', '')
        core += '\n'.join(['N100_VENDOR_METADATA_SHA256 ' + probe.META_SHA,
                          'N100_VENDOR_MAPPING_READONLY /dev/dm-0', 'N100_VENDOR_FINGERPRINT_VERIFIED',
                          'N100_VENDOR_VIEW_READY', 'N100_VENDOR_PREFLIGHT_RECORDED', probe.BANNER,
                          'N100_VENDOR_SELECTED /vendor/bin/hw/composer-service ' + 'a'*64,
                          'N100_VENDOR_DEPENDENCY_EXIT /vendor/bin/hw/composer-service ' + str(code),
                          'N100_VENDOR_PROGRAM_COUNT 1', 'N100_VENDOR_PROBE_RESULT exit=0 cleanup_failed=0']) + '\n'
        return core

    def test_real_system_namespace_evidence_and_snapshot_bound_readonly_template(self):
        report, manifest = probe.reference()
        old = json.loads(probe.REFERENCE.read_text())
        self.assertTrue(old['assessment']['generated_config_dependency_probe_passed'])
        self.assertEqual(old['bundle']['script_sha256'], '6175afdc0a5a3b4a2b6ed3053c1edf1bd628480ab88c6945b288878ba0f50b4f')
        script = probe.render(report, manifest)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        self.assertIn('0 {} linear 8:10 {}'.format(probe.VENDOR_B['sectors'], probe.VENDOR_B['physical_sector']), script)
        self.assertIn(probe.META_SHA, script)
        self.assertIn('dm create "$vendor_map" --readonly', script)
        self.assertIn('vendor_map=n100_vendor_probe_${work##*.}', script)
        self.assertIn('mount -t ext4 -o ro,noload "$node" "$work/vendor"', script)
        self.assertIn('"$P/odm"', script)
        self.assertIn('unset LD_LIBRARY_PATH LD_PRELOAD LD_CONFIG_FILE', script)
        self.assertNotIn('LD_LIBRARY_PATH=', script)
        self.assertLess(script.index('for point in $vendor_mounts'), script.index('dm remove "$vendor_map"'))
        self.assertLess(script.index('dm remove "$vendor_map"'), script.index('for point in $mounts'))
        self.assertNotIn('dm remove_all', script)
        self.assertNotIn('echo "continue"', script)
        result = subprocess.run(['sh', '-c', script, 'probe', '--plan'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

    def test_exact_bundle_transport_and_collect_keep_service_main_unstarted(self):
        bundle = self.prepare()
        self.assertTrue(bundle['source_system_namespace_probe_passed'])
        self.assertFalse(bundle['device_tested'])
        with self.assertRaisesRegex(ValueError, 'Output exists'): self.prepare()
        log = self.root / 'vendor.log'; log.write_text(self.log()); result_path = self.root / 'result.json'
        with patch.object(probe, 'reference', return_value=(self.report, self.manifest)):
            result = probe.collect(self.output, log, result_path)
            self.assertTrue(result['assessment']['selected_vendor_dependency_probe_passed'])
            self.assertFalse(result['android_services_started']); self.assertFalse(result['nnapi_requirement_resolved'])
            with self.assertRaises(FileExistsError): probe.collect(self.output, log, result_path)
        path = self.output / 'vndk_payload.img'; path.write_bytes(b'changed')
        with self.assertRaises(ValueError): probe.verify_bundle(self.output, self.report, self.manifest)

    def test_stale_source_archive_is_refused_without_publishing_output(self):
        path = self.source / 'hal-system.tar.gz'; path.write_bytes(b'old HAL')
        with self.assertRaises(ValueError): self.prepare()
        self.assertFalse(self.output.exists()); self.assertFalse(list(self.root.glob('.vendor-probe-*')))

    def test_missing_library_failure_stays_separate_from_successful_system_dependency_lists(self):
        good = self.log()
        self.assertTrue(probe.classify(good, self.report, self.manifest)['selected_vendor_dependency_probe_passed'])
        failure = self.log(1).replace('N100_VENDOR_PROGRAM_COUNT 1',
            'CANNOT LINK EXECUTABLE: library "missing.so" not found\nN100_VENDOR_PROGRAM_COUNT 1')
        result = probe.classify(failure, self.report, self.manifest)
        self.assertTrue(result['system_service_dependency_probe_passed'])
        self.assertFalse(result['selected_vendor_dependency_probe_passed'])
        self.assertEqual(result['vendor_dependency_exit_codes']['/vendor/bin/hw/composer-service'], 1)
        bad = [good.replace(probe.META_SHA, '0'*64), good.replace('cleanup_failed=0', 'cleanup_failed=1'),
               good.replace('N100_VENDOR_PROGRAM_COUNT 1', 'N100_VENDOR_PROGRAM_COUNT 0'),
               good.replace(probe.BANNER, ''), good.replace('N100_VENDOR_VIEW_READY', 'old-view'),
               good + 'N100_VENDOR_DEPENDENCY_EXIT /vendor/bin/hw/composer-service 0\n',
               good + 'N100_VENDOR_PROBE_RESULT exit=0 cleanup_failed=0\n',
               good + 'linker: Warning: failed to find generated linker configuration\n']
        for log in bad:
            with self.subTest(log=log):
                self.assertFalse(probe.classify(log, self.report, self.manifest)['selected_vendor_dependency_probe_passed'])


if __name__ == '__main__': unittest.main()
