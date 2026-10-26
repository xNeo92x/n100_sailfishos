import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('rpm_probe', ROOT / 'tools/prepare-runtime-rpm-probe.py')
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def member(name, data=b'', mode=stat.S_IFREG | 0o644, inode=1, links=1, crc=False):
    encoded = name.encode() + b'\0'
    fields = [inode, mode, 0, 0, links, 0, len(data), 0, 0, 0, 0, len(encoded), sum(data) if crc else 0]
    header = (b'070702' if crc else b'070701') + ''.join('{:08x}'.format(n) for n in fields).encode()
    result = header + encoded
    result += b'\0' * (-len(result) % 4)
    result += data
    return result + b'\0' * (-len(data) % 4)


def archive(*items):
    return b''.join(items) + member('TRAILER!!!')


class RuntimeRpmProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_hardlink_data_can_follow_selected_member_and_32bit_is_excluded(self):
        raw = archive(member('./' + probe.PREFIX + 'bin/a', inode=7, links=2),
                      member('./outside/a', b'content', inode=7, links=2),
                      member('./' + probe.PREFIX + 'lib/a.so', b'32bit'),
                      member('./' + probe.PREFIX + 'etc/empty', inode=8, links=2))
        records = probe.cpio_tree(raw)
        self.assertEqual(records['bin/a']['data'], b'content')
        self.assertEqual(records['etc/empty']['data'], b'')
        self.assertNotIn('lib/a.so', records)

    def test_unsafe_paths_duplicates_parent_links_and_bad_crc_are_rejected(self):
        cases = [archive(member('../escape', b'x')),
                 archive(member(probe.PREFIX + 'bin/../escape', b'x')),
                 archive(member(probe.PREFIX + 'bin//a', b'x')),
                 archive(member(probe.PREFIX + 'bin/a'), member(probe.PREFIX + 'bin/a')),
                 archive(member(probe.PREFIX + 'bin', b'/outside', stat.S_IFLNK | 0o777),
                         member(probe.PREFIX + 'bin/a', b'x')),
                 archive(member(probe.PREFIX + 'node', b'', stat.S_IFCHR | 0o600))]
        damaged = bytearray(archive(member(probe.PREFIX + 'bin/a', b'abc', crc=True)))
        at = damaged.index(b'abc'); damaged[at] = ord('x'); cases.append(bytes(damaged))
        for raw in cases:
            with self.subTest(length=len(raw)), self.assertRaises(ValueError):
                probe.cpio_tree(raw)
        with self.assertRaises(ValueError):
            probe.cpio_tree(archive(member(probe.PREFIX + 'bin/a'))[:-5])

    def fixture(self):
        values = {'bin/hwservicemanager': b'fixture-hw', 'bin/logd': b'fixture-log',
                  'bin/linkerconfig': b'fixture-static', 'apex/com.android.runtime.apex': b'fixture-apex',
                  'lib64/fixture.so': b'fixture-library'}
        raw = archive(*(member('./' + probe.PREFIX + n, d, stat.S_IFREG | 0o755, inode=i + 1)
                        for i, (n, d) in enumerate(values.items())),
                      member('./' + probe.PREFIX + 'bin/linker64', b'/apex/com.android.runtime/bin/linker64', stat.S_IFLNK | 0o777, inode=99))
        pins = {n: {'bytes': len(d), 'sha256': probe.sha(d)} for n, d in values.items()}
        return raw, pins

    def test_archive_preserves_files_and_places_links_after_regular_entries(self):
        raw, pins = self.fixture()
        records = probe.cpio_tree(raw)
        target = self.root / 'hal.tar.gz'
        with patch.object(probe, 'expected_files', return_value=pins):
            result = probe.write_hal_archive(records, target)
        with tarfile.open(target) as archive_file:
            entries = archive_file.getmembers()
            link_index = next(i for i, p in enumerate(entries) if p.issym())
            self.assertTrue(all(not p.isfile() for p in entries[link_index:]))
            self.assertEqual(archive_file.getmember('bin/linker64').linkname, '/apex/com.android.runtime/bin/linker64')
            self.assertEqual(archive_file.extractfile('bin/logd').read(), b'fixture-log')
            checks = archive_file.extractfile('n100-files.sha256').read().decode()
            self.assertIn(probe.sha(b'fixture-library') + '  lib64/fixture.so', checks)
        self.assertEqual(result['regular_file_hashes']['bin/logd'], pins['bin/logd']['sha256'])
        pins['bin/logd']['sha256'] = '0' * 64
        with patch.object(probe, 'expected_files', return_value=pins), self.assertRaisesRegex(ValueError, 'differs'):
            probe.write_hal_archive(records, self.root / 'bad.tar.gz')
        self.assertFalse((self.root / 'bad.tar.gz').exists())

    def test_rpm_selection_rejects_ambiguity_and_wrong_architecture(self):
        folder = self.root / 'droid-local-repo/billie2'; folder.mkdir(parents=True)
        first = folder / 'droid-hal-billie2-0.0.6-1.aarch64.rpm'; first.write_bytes(b'fixture')
        (folder / 'droid-hal-billie2-kernel-0.0.6-1.aarch64.rpm').write_bytes(b'ignored')
        answer = subprocess.CompletedProcess([], 0, 'droid-hal-billie2\naarch64\n0.0.6-1\n')
        with patch.object(probe.subprocess, 'run', return_value=answer):
            path, info = probe.select_rpm(self.root)
            self.assertEqual(path, first); self.assertEqual(info['sha256'], probe.sha(b'fixture'))
        with patch.object(probe.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'droid-hal-billie2\nx86_64\n1\n')):
            with self.assertRaisesRegex(ValueError, 'architecture'):
                probe.select_rpm(self.root)
        (folder / 'droid-hal-billie2-0.0.6-2.aarch64.rpm').write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            probe.select_rpm(self.root)

    def test_prepare_uses_rpm_payload_and_publishes_exclusive_integrity_bundle(self):
        raw, pins = self.fixture()
        fake_rpm = self.root / 'main.rpm'; fake_rpm.write_bytes(b'fake RPM')
        output = self.root / 'bundle'
        payload = b'fixture-previously-pinned-payload'
        actual_run = subprocess.run
        def command(args, **kwargs):
            if args[0] == 'rpm2cpio':
                kwargs['stdout'].write(raw)
                return subprocess.CompletedProcess(args, 0)
            return actual_run(args, **kwargs)
        def check_apex(path):
            self.assertEqual(path.read_bytes(), b'fixture-apex')
            return payload
        with patch.object(probe, 'select_rpm', return_value=(fake_rpm, {'name': 'droid-hal-billie2', 'sha256': probe.sha(fake_rpm.read_bytes())})), \
             patch.object(probe, 'expected_files', return_value=pins), \
             patch.object(probe, 'PAYLOAD_SHA', probe.sha(payload)), \
             patch.object(probe.runtime, 'PAYLOAD_BYTES', len(payload)), \
             patch.object(probe.runtime, 'read_payload', side_effect=check_apex), \
             patch.object(probe.subprocess, 'run', side_effect=command):
            probe.prepare(self.root, output)
            before = (output / 'manifest.json').read_bytes()
            manifest = json.loads(before)
            self.assertEqual(manifest['hal_archive_sha256'], probe.sha((output / 'hal-system.tar.gz').read_bytes()))
            self.assertEqual(manifest['script_sha256'], probe.sha((output / 'runtime-probe.sh').read_bytes()))
            self.assertFalse(manifest['rpm_scripts_executed'])
            self.assertNotIn(b'@HAL_SHA@', (output / 'runtime-probe.sh').read_bytes())
            log = self.root / 'device.txt'; log.write_text('N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0\n')
            report = self.root / 'report.json'
            args = ['probe', 'report', '--bundle-dir', str(output), '--probe-log', str(log), '--output', str(report)]
            with patch.object(sys, 'argv', args):
                probe.main()
            self.assertEqual(json.loads(report.read_text())['bundle']['hal_archive_sha256'], manifest['hal_archive_sha256'])
            (output / 'hal-system.tar.gz').write_bytes(b'changed')
            args[-1] = str(self.root / 'bad-report.json')
            with patch.object(sys, 'argv', args), self.assertRaises(SystemExit) as failed:
                probe.main()
            self.assertEqual(failed.exception.code, 1)
            self.assertFalse((self.root / 'bad-report.json').exists())
            with self.assertRaisesRegex(ValueError, 'Output exists'):
                probe.prepare(self.root, output)
            self.assertEqual((output / 'manifest.json').read_bytes(), before)

    def test_plan_and_nonroot_guard(self):
        template = (ROOT / 'tools/runtime-rpm-probe.sh.in').read_text()
        for token, value in {'@PAYLOAD_SHA@': 'a'*64, '@PAYLOAD_BYTES@': '4096', '@HAL_SHA@': 'b'*64,
                             '@HAL_BYTES@': '1024', '@FILE_CHECKS@': 'bin/linker64 64 ' + 'c'*64}.items():
            template = template.replace(token, value)
        script = self.root / 'probe.sh'; script.write_text(template)
        subprocess.run(['sh', '-n', str(script)], check=True)
        self.assertEqual(subprocess.run(['sh', str(script), '--plan'], capture_output=True).returncode, 0)
        ident = self.root / 'id'; ident.write_text('#!/bin/sh\necho 1000\n'); ident.chmod(0o755)
        result = subprocess.run(['/bin/sh', str(script), 'run'], env={'PATH': str(self.root)}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn('initramfs root shell', result.stderr)


if __name__ == '__main__':
    unittest.main()
