import importlib.util
import os
from pathlib import Path
import shutil
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runtime_service_probe', ROOT / 'tools/prepare-runtime-service-probe.py')
probe = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(probe)
SPEC = importlib.util.spec_from_file_location('runtime_service_fixture', ROOT / 'tests/test_vendor_probe.py')
v2tests = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(v2tests)

HAL = {'sha256': 'f' * 64, 'bytes': 1234}


class RuntimeServiceProbeTests(unittest.TestCase):
    def setUp(self):
        self.v2 = v2tests.VendorProbeTests('test_reader_selftest_literal_is_exactly_an_aarch64_header')
        self.v2.setUp(); self.addCleanup(self.v2.doCleanups)
        self.root = self.v2.root
        self.fake = Path(tempfile.mkdtemp(prefix='n100sim')); self.addCleanup(shutil.rmtree, self.fake, True)

    def test_real_v4_script_is_v2_plus_marked_runtime_section(self):
        report, manifest = probe.vp.reference()
        v2 = probe.vp.render(report, manifest)
        script = probe.render(report, manifest, HAL)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        removed = set(v2.splitlines()) - set(script.splitlines())
        self.assertEqual(removed, {
            "# MIT. Render with prepare-vendor-probe.py; temporary RAM view only.",
            "HAL_SHA='{}'".format(manifest['hal_archive_sha256']), 'HAL_BYTES={}'.format(manifest['hal_archive_bytes']),
            "    *) echo 'Usage: vendor-probe.sh --plan | run' >&2; exit 2 ;;", "echo 'N100_VENDOR_PROBE_VERSION 2'",
            "echo 'Only linker help, linkerconfig help and dependency listing were requested; no HAL services started.'"})
        # Services are stopped before views are unmounted; links and the directory go after.
        self.assertLess(script.index('for pid in $started_pids'), script.index('for point in $vendor_mounts'))
        self.assertLess(script.index('for point in $mounts'), script.index('for link in $dev_links'))
        self.assertLess(script.index(probe.BANNER), script.index('Only servicemanager and hwservicemanager'))
        self.assertGreater(script.index(probe.BANNER), script.index('N100_VENDOR_PROGRAM_COUNT'))
        self.assertNotIn('vendor/bin/hw/" &', script)
        self.assertNotIn('mount -o remount', script)

    def simulate(self, crash=False):
        """Run the runtime section with stubs and a fake /dev; never touches the host /dev."""
        fake = self.fake; P = fake / 'P'
        for path in (fake / 'dev', P / 'dev', P / 'system/etc/selinux', P / 'vendor/etc/selinux', fake / 'bin'):
            path.mkdir(parents=True, exist_ok=True)
        (fake / 'filesystems').write_text('nodev\tbinder\n')
        (fake / 'binder').write_text('')
        stubs = {
            'mount': 'last=; for a; do last=$a; done; mkdir -p "$last"; for n in binder hwbinder vndbinder; do : > "$last/$n"; done',
            'dmesg': 'echo "[1.0] binder: fixture"',
            'chroot': 'name=${6##*/}; if [ "$name" = hwservicemanager ] && [ "$N100_CRASH" = 1 ]; then echo crashed >&2; exit 134; fi\n'
                      'exec 3<"$N100_FAKE/binder"; exec sleep 30',
        }
        for name, body in stubs.items():
            (fake / 'bin' / name).write_text('#!/bin/sh\n' + body + '\n'); (fake / 'bin' / name).chmod(0o755)
        # The fake root has no spaces, so unquoted substitutions stay single words.
        section = re.sub(r'(?<!\$P)/dev/', '$N100_FAKE/dev/', probe.RUNTIME).replace('/proc/filesystems', '$N100_FAKE/filesystems')
        section = section.replace('[ -c "$P/dev/binderfs/$leaf" ]', '[ -e "$P/dev/binderfs/$leaf" ]')
        head = ('set -eu\ndie() { echo "STOP: $*" >&2; exit 1; }\n'
                'mounted() { return 1; }\n'
                "n100_alive() { [ -n \"$1\" ] && [ -r \"/proc/$1/status\" ] && ! grep -q '^State:[[:space:]]*Z' \"/proc/$1/status\"; }\n"
                'work=$N100_FAKE; P=$N100_FAKE/P; mounts=; started_pids=; dev_links=; binderfs_dir=\n')
        tail = '\necho "PIDS $started_pids"\necho "LINKS $dev_links"\n'
        env = dict(os.environ, PATH=str(fake / 'bin') + ':' + os.environ['PATH'], N100_FAKE=str(fake),
                   N100_CRASH='1' if crash else '0')
        result = subprocess.run(['sh', '-c', head + section + tail], capture_output=True, text=True, env=env, timeout=60)
        return result, fake

    def test_simulated_runtime_section_reports_running_and_crashed_services(self):
        result, fake = self.simulate(crash=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        self.assertIn('N100_RUNTIME_BINDERFS_MOUNTED stats=global', out)
        self.assertIn('N100_RUNTIME_BINDER_NODES_READY', out)
        self.assertIn('N100_RUNTIME_ALIVE servicemanager 6s 1', out)
        self.assertIn('N100_RUNTIME_ALIVE hwservicemanager 3s 0', out)
        self.assertIn('N100_RUNTIME_EXIT hwservicemanager 134', out)
        self.assertRegex(out, r'N100_RUNTIME_BINDER_FDS servicemanager [1-9]')
        self.assertIn('N100_RUNTIME_EXIT servicemanager 143', out)
        self.assertTrue((fake / 'dev/binder').is_symlink())
        self.assertTrue(out.rstrip().splitlines()[-3:][0].startswith('N100_RUNTIME_SECTION_END'))
        section = out.split(probe.BANNER, 1)[1]
        log = self.v2.log().replace('N100_VENDOR_PROBE_VERSION 2', 'N100_VENDOR_PROBE_VERSION 4').replace(
            'N100_VENDOR_PROBE_RESULT', probe.BANNER + section.split('PIDS ')[0] + 'N100_VENDOR_PROBE_RESULT')
        result = probe.classify(log, self.v2.report, self.v2.manifest)
        self.assertTrue(result['runtime']['section_complete'])
        self.assertTrue(result['runtime']['services']['servicemanager']['stayed_up'])
        self.assertFalse(result['runtime']['services']['hwservicemanager']['stayed_up'])
        self.assertEqual(result['runtime']['services']['hwservicemanager']['exit_code'], 134)
        self.assertFalse(result['runtime_service_start_passed'])
        self.assertTrue(result['system_service_dependency_probe_passed'])
        self.assertTrue(result['selected_vendor_dependency_probe_passed'])

    def test_simulated_runtime_section_passes_when_both_stay_up(self):
        result, _ = self.simulate()
        self.assertEqual(result.returncode, 0, result.stderr)
        section = result.stdout.split(probe.BANNER, 1)[1].split('PIDS ')[0]
        log = self.v2.log().replace('N100_VENDOR_PROBE_VERSION 2', 'N100_VENDOR_PROBE_VERSION 4').replace(
            'N100_VENDOR_PROBE_RESULT', probe.BANNER + section + 'N100_VENDOR_PROBE_RESULT')
        good = probe.classify(log, self.v2.report, self.v2.manifest)
        self.assertTrue(good['runtime_service_start_passed'], good['runtime'])
        for bad in [log.replace('cleanup_failed=0', 'cleanup_failed=1'),
                    log.replace('N100_RUNTIME_SECTION_END', ''),
                    log.replace('N100_RUNTIME_ALIVE hwservicemanager 6s 1', 'N100_RUNTIME_ALIVE hwservicemanager 6s 0'),
                    log + 'N100_RUNTIME_CLEANUP_STUCK 123\n']:
            self.assertFalse(probe.classify(bad, self.v2.report, self.v2.manifest)['runtime_service_start_passed'])

    def test_existing_binder_nodes_stop_the_probe(self):
        fake = self.fake; (fake / 'dev').mkdir(parents=True)
        (fake / 'dev/binder').write_text('')
        result, _ = self.simulate()
        self.assertNotEqual(result.returncode, 0); self.assertIn('Unexpected existing', result.stderr)

    @unittest.skipUnless(shutil.which('rpm2cpio') and shutil.which('zstd'), 'needs rpm tools')
    def test_real_hal_tree_is_v2_tree_plus_four_reviewed_files(self):
        rpm = Path(os.path.expanduser('~/n100-build/rpm-archive/droid-hal-billie2-0.0.6-202611111828.aarch64.rpm'))
        v2 = Path(os.path.expanduser('~/n100-build/vendor-probe-v2-20261026/hal-system.tar.gz'))
        if not (rpm.is_file() and v2.is_file()): self.skipTest('local build artefacts not present')
        hal = probe.build_hal(rpm, v2, self.root / 'hal.tar.gz')
        self.assertEqual(set(hal['added_files']), set(probe.NEW_FILES))

    def test_documented_phone_block_pins_the_archived_v4_script(self):
        import json
        archived = json.loads((ROOT / 'validation/runtime-probe-v4-manifest-user-20261111.json').read_text())
        report, manifest = probe.vp.reference()
        self.assertEqual(archived, probe.bundle_manifest(report, manifest, archived['hal_tree']))
        doc = (ROOT / 'docs/RUNTIME_SERVICE_PROBE.md').read_text()
        self.assertIn("echo '" + archived['script_sha256'] + "  /tmp/n100-runtime-probe-v4.sh' | sha256sum -c -", doc)
        subprocess.run(['bash', '-n', str(ROOT / 'tools/run-runtime-v4-probe.sh')], check=True)
        helper = (ROOT / 'tools/run-runtime-v4-probe.sh').read_text()
        self.assertIn('runtime-probe-v4.txt', helper); self.assertNotIn('fastboot flash', helper)


if __name__ == '__main__': unittest.main()
