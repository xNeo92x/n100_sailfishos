import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'tools/probe-android-mounts.sh'
SPEC = importlib.util.spec_from_file_location('android_output', ROOT / 'tools/inspect-android-output.py')
output = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(output)


class MountProbeTests(unittest.TestCase):
    def test_plan_matches_audited_slot_b_extents_and_metadata_digest(self):
        audit = json.loads((ROOT / 'validation/super-metadata-report-user-20261025.json').read_text())
        plan = subprocess.run(['sh', str(SCRIPT), '--plan'], check=True, capture_output=True, text=True).stdout
        self.assertIn(audit['metadata_region_sha256'], plan)
        rows = [line.split() for line in plan.splitlines() if not line.startswith('#')]
        self.assertEqual(len(rows), 4)
        expected = {p['effective_name']: p for p in audit['metadata']['partitions'] if p['effective_name'].endswith('_b')}
        ranges = []
        for name, sectors, offset in rows:
            part = expected[name]
            self.assertTrue(part['readonly_attribute'])
            self.assertEqual(len(part['extents']), 1)
            extent = part['extents'][0]
            self.assertEqual(extent['type'], 'linear')
            self.assertEqual(extent['block_device_index'], 0)
            self.assertEqual((int(sectors), int(offset)), (extent['sectors'], extent['physical_sector']))
            self.assertEqual(int(sectors) * 512, part['bytes'])
            self.assertLessEqual((int(offset) + int(sectors)) * 512, 10737418240)
            ranges.append((int(offset), int(offset) + int(sectors)))
        ranges.sort()
        self.assertTrue(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])))
        self.assertEqual(audit['metadata_region_bytes'], 792 * 512)

    def test_plan_and_bad_argument_do_not_call_device_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); log = root / 'called'
            for name in ['id', 'cat', 'dd', 'mount', 'chroot', 'mktemp', 'sha256sum']:
                path = root / name
                path.write_text('#!/bin/sh\necho called >> "$CALL_LOG"\nexit 99\n')
                path.chmod(0o755)
            env = dict(os.environ, PATH=str(root), CALL_LOG=str(log))
            self.assertEqual(subprocess.run(['/bin/sh', str(SCRIPT), '--plan'], env=env, capture_output=True).returncode, 0)
            self.assertEqual(subprocess.run(['/bin/sh', str(SCRIPT), 'bad'], env=env, capture_output=True).returncode, 2)
            self.assertFalse(log.exists())

    def test_posix_shell_syntax(self):
        subprocess.run(['sh', '-n', str(SCRIPT)], check=True)

    def test_run_rejects_nonroot_before_device_queries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ident = root / 'id'; ident.write_text('#!/bin/sh\nprintf "1000\\n"\n'); ident.chmod(0o755)
            result = subprocess.run(['/bin/sh', str(SCRIPT), 'run'], env=dict(os.environ, PATH=str(root)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('initramfs root shell', result.stderr)

    def test_run_rejects_foreign_kernel_before_block_access(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, body in [('id', 'printf "0\\n"'), ('grep', 'exit 1')]:
                path = root / name; path.write_text('#!/bin/sh\n' + body + '\n'); path.chmod(0o755)
            result = subprocess.run(['/bin/sh', str(SCRIPT), 'run'], env=dict(os.environ, PATH=str(root)), capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Early-debug flag missing', result.stderr)


class AndroidOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.android = self.root / 'android'
        self.product = self.android / 'out/target/product/billie2'
        self.product.mkdir(parents=True)

    def test_flattened_and_packaged_runtime_are_reported_without_extraction(self):
        apex = self.product / 'apex/com.android.runtime'
        (apex / 'bin').mkdir(parents=True)
        elf = bytearray(64); elf[:7] = b'\x7fELF\x02\x01\x01'
        struct.pack_into('<H', elf, 18, 183)
        (apex / 'bin/linker64').write_bytes(elf)
        packed = self.product / 'system/apex/com.android.runtime.apex'
        packed.parent.mkdir(parents=True)
        with zipfile.ZipFile(packed, 'w') as archive:
            archive.writestr('apex_payload.img', b'fixture payload')
        before = sorted(str(p) for p in self.root.rglob('*'))
        report = output.inspect(self.android)
        self.assertTrue(any(p.get('elf_machine') == 183 for p in report['candidates']))
        self.assertTrue(any(p.get('zip_entries') for p in report['candidates']))
        self.assertFalse(report['apex_extracted'])
        self.assertEqual(before, sorted(str(p) for p in self.root.rglob('*')))

    def test_escape_symlinks_are_not_read(self):
        outside = self.root / 'outside'
        (outside / 'apex').mkdir(parents=True)
        (outside / 'apex/com.android.runtime.apex').write_bytes(b'private fixture')
        (self.product / 'system').symlink_to(outside, target_is_directory=True)
        report = output.inspect(self.android)
        self.assertTrue(any(p.get('inspection_skipped') == 'Path resolves outside product output' for p in report['candidates']))
        self.assertFalse(any(p.get('sha256') for p in report['candidates']))

    def test_large_runtime_file_skips_hash_and_zip_read(self):
        path = self.product / 'large.apex'
        with path.open('wb') as stream:
            stream.truncate(512 * 1024 * 1024 + 1)
        entry = output.entry(path, self.product.resolve())
        self.assertIn('inspection_skipped', entry)
        self.assertNotIn('sha256', entry)

    def test_cli_attaches_log_and_preserves_existing_report(self):
        log, report = self.root / 'probe.txt', self.root / 'report.json'
        log.write_text('N100_PROBE_RESULT exit=0 cleanup_failed=0\n')
        args = [sys.executable, str(ROOT / 'tools/inspect-android-output.py'), '--android-root', str(self.android), '--probe-log', str(log), '--output', str(report)]
        subprocess.run(args, check=True, capture_output=True)
        original = report.read_bytes()
        self.assertEqual(json.loads(original)['device_probe_log'], log.read_text())
        self.assertNotEqual(subprocess.run(args, capture_output=True).returncode, 0)
        self.assertEqual(original, report.read_bytes())


if __name__ == '__main__':
    unittest.main()
