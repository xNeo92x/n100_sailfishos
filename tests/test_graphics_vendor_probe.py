import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('graphics_vendor_probe', ROOT / 'tools/prepare-graphics-vendor-probe.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)
SPEC = importlib.util.spec_from_file_location('graphics_vendor_fixture', ROOT / 'tests/test_vendor_probe.py')
v2tests = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(v2tests)

COMPOSER = probe.COMPOSER


def sha(data):
    return hashlib.sha256(data).hexdigest()


def delta(library=b'\x7fELF\x02\x01\x01' + bytes(11) + b'\xb7\x00' + b'x' * 64, extra=None, listing=None):
    listing = listing if listing is not None else '{}  lib64/libGLESv3.so\n'.format(sha(library))
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as archive:
        for name, data in [('n100-graphics-files.sha256', listing.encode()), ('lib64/libGLESv3.so', library)] + (extra or []):
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    pins = {'archive_bytes': len(data), 'archive_sha256': sha(data), 'library_bytes': len(library),
            'library_sha256': sha(library), 'checksum_line': '{}  lib64/libGLESv3.so'.format(sha(library)),
            'report_sha256': probe.GRAPHICS_REPORT_SHA}
    return data, pins


class GraphicsVendorProbeTests(unittest.TestCase):
    def setUp(self):
        self.v2 = v2tests.VendorProbeTests('test_reader_selftest_literal_is_exactly_an_aarch64_header')
        self.v2.setUp(); self.addCleanup(self.v2.doCleanups)
        self.root = self.v2.root

    def test_real_graphics_report_pins_only_the_new_aarch64_library(self):
        pins = probe.graphics()
        self.assertEqual(pins['library_sha256'], 'e1efbb5b54f0bac9d770c3c01dbfc39cee9d377fba7c87b7306e7fc2466df958')
        self.assertEqual(pins['archive_sha256'], '49d034582595e570ef41d2ec8f175b80891a0c382da9fccdb795f27859e3a169')
        report = json.loads(probe.GRAPHICS_REPORT.read_text())
        self.assertFalse(report['device_tested']); self.assertFalse(report['rpm_rebuilt'])

    def test_real_v3_script_is_the_device_tested_v2_script_plus_marked_additions(self):
        report, manifest = probe.vp.reference(); pins = probe.graphics()
        v2 = probe.vp.render(report, manifest)
        self.assertEqual(probe.sha(v2.encode()), probe.V2_SCRIPT_SHA)
        script = probe.render(report, manifest, pins)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        removed = set(v2.splitlines()) - set(script.splitlines())
        self.assertEqual(removed, {"# MIT. Render with prepare-vendor-probe.py; temporary RAM view only.",
                                   "    *) echo 'Usage: vendor-probe.sh --plan | run' >&2; exit 2 ;;",
                                   '        rm -rf "$work/hal"', "echo 'N100_VENDOR_PROBE_VERSION 2'"})
        # The overlay happens before /system is bind-mounted and never touches persistent storage.
        self.assertLess(script.index('N100_VENDOR_GRAPHICS_OVERLAY_READY'), script.index('mount --bind "$work/hal" "$P/system"'))
        self.assertNotIn('$ROOT/usr/libexec/droid-hybris/system/lib64', script)
        self.assertIn('dm create "$vendor_map" --readonly', script)
        result = subprocess.run(['sh', '-c', script, 'probe', '--plan'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)

    def overlay(self, data, pins, preexisting=False):
        report, manifest = probe.vp.reference()
        script = probe.render(report, manifest, pins)
        block = script.split("echo '=== Download, verify and overlay", 1)[1].split('N100_VENDOR_GRAPHICS_OVERLAY_READY', 1)[0]
        work = self.root / ('work-' + str(len(list(self.root.glob('work-*')))))
        (work / 'hal/lib64').mkdir(parents=True)
        if preexisting: (work / 'hal/lib64/libGLESv3.so').write_bytes(b'old')
        source = self.root / 'served.tar.gz'; source.write_bytes(data)
        stub = self.root / 'bin'; stub.mkdir(exist_ok=True)
        (stub / 'wget').write_text('#!/bin/sh\n[ "$1" = -O ] && cp "$N100_SERVED" "$2"\n'); (stub / 'wget').chmod(0o755)
        program = 'set -eu\ndie() { echo "STOP: $*" >&2; exit 1; }\nwork=$1\n'
        program += '\n'.join(line for line in script.splitlines() if line.startswith(('GFX_', 'GLES_'))) + '\n'
        program += "echo '" + block + "N100_VENDOR_GRAPHICS_OVERLAY_READY $actual\"\n"
        env = dict(os.environ, PATH=str(stub) + ':' + os.environ['PATH'], N100_SERVED=str(source))
        return subprocess.run(['sh', '-c', program, 'overlay', str(work)], capture_output=True, text=True, env=env), work

    def test_overlay_adds_exactly_the_pinned_library_to_the_ram_hal_copy(self):
        data, pins = delta()
        result, work = self.overlay(data, pins)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('N100_VENDOR_GRAPHICS_OVERLAY_READY ' + pins['library_sha256'], result.stdout)
        self.assertEqual(sha((work / 'hal/lib64/libGLESv3.so').read_bytes()), pins['library_sha256'])
        self.assertEqual(sorted(p.name for p in (work / 'hal/lib64').iterdir()), ['libGLESv3.so'])

    def test_overlay_refuses_changed_extra_or_preexisting_content(self):
        data, pins = delta()
        result, _ = self.overlay(data, pins, preexisting=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('already in HAL tree', result.stderr)
        changed = dict(pins, archive_sha256='0' * 64)
        result, _ = self.overlay(data, changed); self.assertIn('hash mismatch', result.stderr)
        extra, extra_pins = delta(extra=[('lib64/libEGL.so', b'replacement')])
        result, work = self.overlay(extra, extra_pins)
        self.assertNotEqual(result.returncode, 0); self.assertIn('Unexpected graphics delta entry', result.stderr)
        self.assertFalse((work / 'hal/lib64/libGLESv3.so').exists())
        wrong, wrong_pins = delta(listing='{}  lib64/libGLESv3.so\n'.format('1' * 64))
        result, _ = self.overlay(wrong, dict(wrong_pins, checksum_line='x'))
        self.assertNotEqual(result.returncode, 0)

    def test_host_delta_verification_rejects_other_members_and_non_aarch64(self):
        path = self.root / 'delta.tar.gz'
        data, pins = delta(); path.write_bytes(data); probe.verify_delta(path, pins)
        data, pins = delta(extra=[('lib64/libEGL.so', b'x')]); path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'members'): probe.verify_delta(path, pins)
        data, pins = delta(library=b'\x7fELF\x01' + bytes(64)); path.write_bytes(data)
        with self.assertRaisesRegex(ValueError, 'AArch64'): probe.verify_delta(path, pins)

    def log(self, resolved=True, code=0):
        base = self.v2.log(code).replace('/vendor/bin/hw/composer-service', COMPOSER)
        base = base.replace('N100_VENDOR_PROBE_VERSION 2', 'N100_VENDOR_PROBE_VERSION 3\nN100_VENDOR_GRAPHICS_DELTA ' +
                            self.pins['archive_sha256'] + '\nN100_VENDOR_GRAPHICS_OVERLAY_READY ' + self.pins['library_sha256'])
        line = ('\tlibGLESv3.so => /system/lib64/libGLESv3.so (0x7f001000)' if resolved else
                'CANNOT LINK EXECUTABLE: library "libGLESv3.so" not found')
        return base.replace('N100_VENDOR_DEPENDENCY_EXIT ' + COMPOSER, line + '\nN100_VENDOR_DEPENDENCY_EXIT ' + COMPOSER)

    def test_classifier_requires_v3_markers_and_composer_resolution_from_system_lib64(self):
        self.pins = delta()[1]; report, manifest = self.v2.report, self.v2.manifest
        good = probe.classify(self.log(), report, manifest, self.pins)
        self.assertTrue(good['selected_vendor_dependency_probe_passed'], good)
        self.assertTrue(good['composer_resolved_overlaid_libglesv3'])
        for log in [self.log(resolved=False, code=1), self.log().replace('VERSION 3', 'VERSION 2'),
                    self.log().replace('N100_VENDOR_GRAPHICS_OVERLAY_READY', 'old'),
                    self.log().replace('/system/lib64/libGLESv3.so', '/vendor/lib64/libGLESv3.so')]:
            with self.subTest(log=log[-300:]):
                self.assertFalse(probe.classify(log, report, manifest, self.pins)['selected_vendor_dependency_probe_passed'])

    def test_real_v2_failure_log_is_not_a_v3_pass(self):
        old = json.loads((ROOT / 'validation/vendor-v2-device-report-user-20261026.json').read_text())
        report, manifest = probe.vp.reference()
        result = probe.classify(old['device_probe_log'], report, manifest, probe.graphics())
        self.assertFalse(result['selected_vendor_dependency_probe_passed'])
        self.assertTrue(result['system_service_dependency_probe_passed'])
        self.assertFalse(result['composer_resolved_overlaid_libglesv3'])


    def test_documented_phone_block_pins_the_real_v3_script(self):
        report, manifest = probe.vp.reference()
        digest = probe.sha(probe.render(report, manifest, probe.graphics()).encode())
        doc = (ROOT / 'docs/GRAPHICS_VENDOR_PROBE.md').read_text()
        self.assertIn("echo '" + digest + "  /tmp/n100-vendor-probe-v3.sh' | sha256sum -c -", doc)
        archived = json.loads((ROOT / 'validation/vendor-probe-v3-manifest-user-20261111.json').read_text())
        self.assertEqual(archived, probe.bundle_manifest(report, manifest, probe.graphics()))
        helper = (ROOT / 'tools/run-vendor-v3-probe.sh').read_text()
        subprocess.run(['bash', '-n', str(ROOT / 'tools/run-vendor-v3-probe.sh')], check=True)
        self.assertIn('vendor-probe-v3.txt', helper); self.assertNotIn('fastboot flash', helper)


if __name__ == '__main__': unittest.main()
