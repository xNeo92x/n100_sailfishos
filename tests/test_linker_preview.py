import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('linker_preview', ROOT / 'tools/generate-linker-preview.py')
preview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview)


def manifest(name):
    data = name.encode()
    return b'\x0a' + bytes([len(data)]) + data + b'\x10\x01'


def golden_config():
    # AOSP Configuration::WriteConfig repeats dir.X =; namespace lists use +=.
    return '''dir.system = /system/bin/
dir.system = /system/xbin/
dir.vendor = /odm/bin/
dir.vendor = /vendor/bin/
[system]
namespace.default.search.paths = /system/${LIB}
namespace.default.search.paths += /system_ext/${LIB}
namespace.vndk.search.paths = /vendor/${LIB}/vndk-sp
namespace.vndk.search.paths += /apex/com.android.vndk.v30/${LIB}
[vendor]
namespace.vndk.search.paths = /apex/com.android.vndk.v30/${LIB}
[unrestricted]
namespace.default.isolated = false
'''


class LinkerPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 preview ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, members):
        archive = self.root / 'hal.tar.gz'
        expected, links = {}, {}
        with tarfile.open(archive, 'w:gz') as target:
            for name, content, kind in members:
                item = tarfile.TarInfo(name)
                if kind == 'file':
                    item.size = len(content); item.mode = 0o644
                    target.addfile(item, io.BytesIO(content)); expected[name] = preview.digest(content)
                elif kind == 'link':
                    item.type = tarfile.SYMTYPE; item.linkname = content
                    target.addfile(item); links[name] = content
                elif kind == 'hardlink':
                    item.type = tarfile.LNKTYPE; item.linkname = content; target.addfile(item)
        expected.pop('n100-files.sha256', None)
        return archive, {'hal_archive_bytes': archive.stat().st_size,
                         'hal_archive_sha256': preview.digest(archive.read_bytes()),
                         'hal_tree': {'regular_file_hashes': expected, 'symlinks': links,
                                      'unpacked_regular_bytes': sum(len(c) for n, c, k in members if k == 'file' and n != 'n100-files.sha256')}}

    def test_archive_verifies_every_hash_and_preserves_android_links_without_following(self):
        archive, bundle = self.archive([
            ('n100-files.sha256', b'fixture', 'file'),
            ('lib64/libfixture.so', b'fixture ELF', 'file'),
            ('bin/linker64', '/apex/com.android.runtime/bin/linker64', 'link')])
        target = self.root / 'hal'
        result = preview.unpack_hal(archive, target, bundle)
        self.assertEqual(result['regular_files'], 1)
        self.assertEqual((target / 'lib64/libfixture.so').read_bytes(), b'fixture ELF')
        self.assertEqual((target / 'bin/linker64').readlink().as_posix(), '/apex/com.android.runtime/bin/linker64')
        bundle['hal_tree']['regular_file_hashes']['lib64/libfixture.so'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'content hash'):
            preview.unpack_hal(archive, self.root / 'bad-content', bundle)

    def test_archive_rejects_traversal_link_parents_hardlinks_and_unreviewed_members(self):
        for bad in [
            [('../outside', b'x', 'file')],
            [('bin', '/tmp', 'link'), ('bin/outside', b'x', 'file')],
            [('hard', 'other', 'hardlink')],
            [('same', b'x', 'file'), ('same', b'y', 'file')],
        ]:
            with self.subTest(bad=bad):
                archive, bundle = self.archive([('n100-files.sha256', b'fixture', 'file')] + bad)
                with self.assertRaises(ValueError):
                    preview.unpack_hal(archive, self.root / ('target' + str(len(list(self.root.iterdir())))), bundle)
        self.assertFalse((self.root / 'outside').exists())
        archive, bundle = self.archive([('n100-files.sha256', b'fixture', 'file'), ('extra', b'x', 'file')])
        bundle['hal_tree']['regular_file_hashes'] = {}
        with self.assertRaisesRegex(ValueError, 'inventory'):
            preview.unpack_hal(archive, self.root / 'extra', bundle)

    def test_native_output_parser_repeated_directory_assignments_and_append(self):
        parsed = preview.read_config(golden_config())
        self.assertEqual(parsed['']['dir.vendor'], '/odm/bin/:/vendor/bin/')
        self.assertEqual(parsed['system']['namespace.default.search.paths'], '/system/${LIB}:/system_ext/${LIB}')
        for bad in ('[system]\nx = one\nx = two', 'x += one', '[system]\n[system]', 'invalid'):
            with self.assertRaises(ValueError):
                preview.read_config(bad)
        target = self.root / 'generated'; target.mkdir()
        (target / 'ld.config.txt').write_text(golden_config())
        apex = target / 'com.android.runtime'; apex.mkdir()
        (apex / 'ld.config.txt').write_text('dir.runtime = /apex/com.android.runtime/bin/\n[runtime]\nnamespace.default.isolated = true\n')
        self.assertIn('vendor', preview.inspect_generated(target, self.root / 'root')['sections'])
        (target / 'ld.config.txt').write_text(golden_config().replace('.v30/', '.v33/'))
        with self.assertRaisesRegex(ValueError, 'VNDK-30'):
            preview.inspect_generated(target, self.root / 'root')
        (target / 'ld.config.txt').write_text(golden_config() + '# ' + str(self.root / 'root'))
        with self.assertRaisesRegex(ValueError, 'host path'):
            preview.inspect_generated(target, self.root / 'root')

    @unittest.skipUnless(shutil.which('debugfs') and shutil.which('mke2fs'), 'e2fsprogs required')
    def test_real_ext4_apex_metadata_matches_packaged_manifest_and_list_pins(self):
        name = 'com.android.vndk.v30'
        tree = self.root / 'source'; (tree / 'etc').mkdir(parents=True); (tree / 'lib64').mkdir()
        pb = manifest(name) + b'\x3a\x07libc.so' + b'\x42\x09liblog.so'
        (tree / 'apex_manifest.pb').write_bytes(pb)
        (tree / 'etc/linker.config.pb').write_bytes(b'fixture protobuf')
        pins = {}
        for label in preview.inputs.LISTS:
            path = tree / ('etc/' + label + '.libraries.30.txt'); path.write_text('libfixture.so\n')
            pins[label] = preview.inputs.file_info(path)
        image = self.root / 'input.img'
        with image.open('wb') as stream: stream.truncate(8 * 1024 * 1024)
        subprocess.run(['mke2fs', '-q', '-t', 'ext4', '-b', '4096', '-O', 'none,extent,filetype', '-d', str(tree), str(image)],
                       check=True, capture_output=True)
        package = self.root / 'fixture.apex'
        with zipfile.ZipFile(package, 'w') as target:
            target.writestr('apex_manifest.pb', pb); target.writestr('apex_payload.img', image.read_bytes())
        payloads = self.root / 'payloads'; payloads.mkdir()
        actual = preview.stage_apex(package, preview.inputs.file_info(package), preview.inputs.file_info(image), name,
                                    self.root / 'root', payloads, shutil.which('debugfs'), pins)
        self.assertEqual(actual['name'], name)
        self.assertEqual(actual['provide_native_libs'], ['libc.so'])
        self.assertEqual(actual['require_native_libs'], ['liblog.so'])
        self.assertEqual(actual['directory_presence'], ['lib64'])
        self.assertEqual((self.root / 'root/apex' / name / 'etc/vndkcore.libraries.30.txt').read_text(), 'libfixture.so\n')
        self.assertEqual(list((self.root / 'root/apex' / name / 'lib64').iterdir()), [])
        self.assertEqual(preview.inputs.manifest_info(manifest('com.android.runtime'), 'com.android.runtime')['version'], 1)
        # A rehashed ZIP containing a different manifest must still fail against the payload.
        with zipfile.ZipFile(package, 'w') as target:
            target.writestr('apex_manifest.pb', pb + b'\x28\x01'); target.writestr('apex_payload.img', image.read_bytes())
        other = self.root / 'other-payloads'; other.mkdir()
        with self.assertRaisesRegex(ValueError, 'manifests differ'):
            preview.stage_apex(package, preview.inputs.file_info(package), preview.inputs.file_info(image), name,
                               self.root / 'other-root', other, shutil.which('debugfs'))

    def test_failed_host_tool_is_not_masked_and_core_dumps_disabled(self):
        log = self.root / 'tool.log'
        with self.assertRaisesRegex(ValueError, 'Host tool failed'):
            preview.run_tool(['/bin/sh', '-c', 'echo diagnostic; exit 23'], log)
        self.assertEqual(log.read_text(), 'diagnostic\n')
        result = preview.run_tool(['/bin/sh', '-c', 'ulimit -c; exit 23'], log, must_succeed=False)
        self.assertEqual(result['exit'], 23)
        self.assertEqual(result['output'].strip(), '0')

    def test_generation_keeps_normal_preview_separate_from_strict_failure(self):
        for classified in (True, False):
            with self.subTest(classified=classified):
                case = self.root / str(classified); case.mkdir()
                repo = case / 'repo'; (repo / 'validation').mkdir(parents=True)
                profile = repo / 'adaptation/linkerconfig/system.json'; profile.parent.mkdir(parents=True)
                profile.write_text('{"requireLibs": ["libc.so", "libdl.so", "libm.so"]}')
                android = case / 'android'; product = android / 'out/target/product/billie2'
                base = product / 'obj/ETC/system_linker_config_intermediates/system_linker_config'
                base.parent.mkdir(parents=True); base.write_bytes(b'original base')
                vndk = product / 'system_ext/apex/com.android.vndk.v30.apex'; vndk.parent.mkdir(parents=True); vndk.touch()
                host = android / 'out/host/linux-x86/bin'; host.mkdir(parents=True)
                for name in ('linkerconfig', 'conv_linker_config'):
                    (host / name).write_bytes(b'fixture host tool')
                reviewed = {'passed': True, 'host_tools': {n: preview.inputs.file_info(host / n) for n in ('linkerconfig', 'conv_linker_config')},
                            'base_system_config_candidates': [preview.inputs.file_info(base)], 'vndk_apex': {'payload': {}, 'library_lists': {}},
                            'source_revisions': {'system/linkerconfig': 'a' * 40}}
                reference = repo / 'validation/linker-inputs-user-20261026.json'; reference.write_text(json.dumps(reviewed))
                input_report = case / 'input.json'; input_report.write_bytes(reference.read_bytes())
                archive, bundle = self.archive([
                    ('n100-files.sha256', b'fixture', 'file'),
                    ('apex/com.android.runtime.apex', b'fixture package', 'file'),
                    ('lib64/libfixture.so', b'fixture ELF', 'file'),
                    ('lib64/libc.so', b'flat Runtime libc', 'file'),
                    ('etc/sanitizer.libraries.txt', b'fixture list', 'file')])
                bundle.update(payload_bytes=1, payload_sha256='0' * 64, source_rpm={'sha256': 'b' * 64})
                (repo / 'validation/hal-rpm-device-report-user-20261026.json').write_text(json.dumps({'bundle': bundle}))
                bundle_dir = case / 'bundle'; bundle_dir.mkdir()
                (bundle_dir / 'manifest.json').write_text(json.dumps(bundle)); shutil.copyfile(archive, bundle_dir / 'hal-system.tar.gz')
                def apex(package, pin, payload_pin, name, root, payloads, debugfs, list_pins=None):
                    folder = root / 'apex' / name; folder.mkdir(parents=True)
                    return {'name': name, 'version': 1, 'provide_native_libs': ['libc.so'] if name == 'com.android.runtime' else []}
                def tool(command, log, must_succeed=True):
                    args = [str(x) for x in command]; content = ''; exit_code = 0
                    if 'proto' in args:
                        Path(args[args.index('--output') + 1]).write_bytes(b'profile pb')
                    elif 'systemprovide' in args:
                        self.assertEqual(args[args.index('--value') + 1], 'libfixture.so')
                        Path(args[args.index('--output') + 1]).write_bytes(b'provided pb')
                    elif 'print' in args:
                        content = 'requireLibs: "libc.so"\nrequireLibs: "libdl.so"\nrequireLibs: "libm.so"\nprovideLibs: "libfixture.so"\n'
                    elif '--strict' in args:
                        exit_code = -6
                        content = 'not found: libneuralnetworks.so is required by vndk in [system]' if classified else 'unexpected tool failure'
                    else:
                        folder = Path(args[args.index('--target') + 1]); (folder / 'ld.config.txt').write_text(golden_config())
                        (folder / 'com.android.runtime').mkdir()
                        (folder / 'com.android.runtime/ld.config.txt').write_text('[runtime]\nnamespace.default.search.paths = /apex/com.android.runtime/${LIB}\n')
                    log.write_text(content)
                    return {'command': args, 'exit': exit_code, 'log': str(log), 'output': content}
                output = case / 'output'
                with patch.object(preview, 'ROOT', repo), patch.object(preview, 'stage_apex', side_effect=apex), \
                        patch.object(preview, 'run_tool', side_effect=tool), patch.object(preview.shutil, 'which', return_value='/usr/sbin/debugfs'):
                    if classified:
                        result = preview.generate(android, bundle_dir, input_report, output)
                        self.assertTrue(result['namespace_preview_generated'])
                        self.assertFalse(result['strict_diagnostic']['passed'])
                        self.assertEqual(result['strict_diagnostic']['first_missing_requirements'][0]['library'], 'libneuralnetworks.so')
                        self.assertFalse(result['ready_for_installation'])
                        self.assertEqual(result['runtime_providers_excluded_from_system_provides'], ['libc.so'])
                    else:
                        with self.assertRaisesRegex(ValueError, 'unclassified'):
                            preview.generate(android, bundle_dir, input_report, output)
                        failed = json.loads((output / 'report.json').read_text())
                        self.assertFalse(failed['passed']); self.assertFalse(failed['namespace_preview_generated'])
                self.assertEqual(base.read_bytes(), b'original base')
                self.assertFalse((product / 'system/etc/linker.config.pb').exists())

    def test_strict_zero_exit_with_real_error_log_is_not_success(self):
        raw = json.loads((ROOT / 'validation/linker-host-preview-report-user-20261026.json').read_text())
        result = preview.strict_diagnostic(raw['strict_diagnostic'])
        self.assertEqual(result['exit'], 0)
        self.assertFalse(result['passed'])
        self.assertEqual(len(result['error_lines']), 5)
        self.assertEqual({x['library'] for x in result['first_missing_requirements']}, {'libdexfile.so', 'libneuralnetworks.so'})
        for text in ('linkerconfig E 10-26 19:05:50 1 1 source.cc:60] unexpected error',
                     'ERROR invalid input', 'Failed to parse metadata'):
            with self.assertRaisesRegex(ValueError, 'unclassified'):
                preview.strict_diagnostic({'exit': 0, 'output': text})
        self.assertTrue(preview.strict_diagnostic({'exit': 0, 'output': ''})['passed'])
        self.assertTrue(preview.error_lines('linkerconfig F 10-26 19:05:50 fatal'))

    def test_reviewed_upload_lists_and_immutable_input_pins(self):
        report = json.loads((ROOT / 'validation/linker-inputs-user-20261026.json').read_text())
        self.assertTrue(report['passed']); self.assertEqual(report['vndk_version'], '30')
        for item in report['vndk_apex']['library_lists'].values():
            data = item['text'].encode()
            self.assertEqual((len(data), hashlib.sha256(data).hexdigest()), (item['bytes'], item['sha256']))
            self.assertEqual(data.decode().splitlines(), item['entries'])
        sample = self.root / 'input'; sample.write_text('original')
        pin = preview.inputs.file_info(sample); sample.write_text('changed')
        with self.assertRaisesRegex(ValueError, 'differs from reviewed'):
            preview.checked_file(sample, pin)
        original = self.root / 'original'; original.write_text('original'); sample.unlink(); sample.symlink_to(original)
        with self.assertRaisesRegex(ValueError, 'regular input'):
            preview.checked_file(sample, pin)


if __name__ == '__main__':
    unittest.main()
