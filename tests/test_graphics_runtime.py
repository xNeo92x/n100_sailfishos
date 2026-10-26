import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('graphics_runtime', ROOT / 'tools/check-graphics-runtime.py')
graphics = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(graphics)


@unittest.skipUnless(shutil.which('cc') and shutil.which('readelf'), 'host compiler and binutils required')
class GraphicsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='n100 graphics '); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.android = self.root / 'android'
        self.product = self.android / 'out/target/product/billie2'
        self.libs = self.product / 'system/lib64'; self.libs.mkdir(parents=True)
        self.hostlibs = self.root / 'host-link-inputs'; self.hostlibs.mkdir()
        self.compile('libEGL.so', 'int eglFixture(void) { return 0; }')
        self.compile_root()
        self.baseline = copy.deepcopy(graphics.reviewed_hal())
        self.baseline['hal_tree']['regular_file_hashes']['lib64/libEGL.so'] = graphics.pin(self.libs / 'libEGL.so')['sha256']
        runtime = self.product / 'system/apex/com.android.runtime.apex'; runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b'Runtime transport fixture')
        self.baseline['source_apex'] = graphics.pin(runtime)
        self.output = self.root / 'build-report'; self.output.mkdir()

    def compile(self, name, code, needed=True):
        source = self.root / (name + '.c'); source.write_text(code)
        command = ['cc', '-shared', '-fPIC', '-nostdlib', str(source), '-Wl,-soname,' + name,
                   '-o', str(self.libs / name)]
        if name == graphics.LIBRARY and needed:
            command += ['-Wl,--no-as-needed', '-L' + str(self.hostlibs), '-lEGL']
        subprocess.run(command, check=True, capture_output=True)
        shutil.copyfile(self.libs / name, self.hostlibs / name)
        # Transport fixture: real ELF dynamic tables and symbols, declared ARM64 header.
        # No fixture library is executed; this does not validate ARM64 machine code.
        path = self.libs / name; data = bytearray(path.read_bytes()); struct.pack_into('<H', data, 18, 183); path.write_bytes(data)

    def compile_root(self, needed=True, missing=None):
        names = graphics.EXPORTS - ({missing} if missing else set())
        code = '\n'.join('int ' + name + '(void) { return 0; }' for name in names)
        self.compile(graphics.LIBRARY, code, needed)

    def inspect(self):
        with patch.object(graphics, 'reviewed_hal', return_value=self.baseline), \
             patch.object(graphics, 'source_info', return_value={'module': 'libGLESv3', 'revision': 'fixture'}):
            return graphics.inspect(self.android, self.output)

    def test_complete_real_dynamic_metadata_exports_and_safe_delta_archive(self):
        report = self.inspect()
        self.assertTrue(report['dependency_closure_complete']); self.assertFalse(report['device_tested'])
        self.assertEqual(report['new_library_names'], ['libGLESv3.so'])
        self.assertEqual(report['changed_library_names'], [])
        self.assertEqual(report['files']['libGLESv3.so']['needed'], ['libEGL.so'])
        self.assertEqual(set(report['required_exports_verified']), graphics.EXPORTS)
        with tarfile.open(self.output / 'graphics-runtime.tar.gz') as archive:
            self.assertEqual(archive.getnames(), ['n100-graphics-files.sha256', 'lib64/libGLESv3.so'])
            data = archive.extractfile('lib64/libGLESv3.so').read()
            self.assertEqual(graphics.sha(data), report['files']['libGLESv3.so']['sha256'])
        with self.assertRaisesRegex(ValueError, 'exists'): self.inspect()

    def test_changed_egl_is_included_in_delta_instead_of_silently_using_old_body(self):
        self.baseline['hal_tree']['regular_file_hashes']['lib64/libEGL.so'] = '0'*64
        report = self.inspect()
        self.assertEqual(report['changed_library_names'], ['libEGL.so'])
        self.assertEqual(report['staged_paths'], ['lib64/libEGL.so', 'lib64/libGLESv3.so'])

    def test_wrong_abi_missing_dependency_stub_and_missing_export_refuse_successful_publication(self):
        root = self.libs / graphics.LIBRARY; original = root.read_bytes()
        wrong = bytearray(original); struct.pack_into('<H', wrong, 18, 62); root.write_bytes(wrong)
        with self.assertRaisesRegex(ValueError, 'AArch64'): self.inspect()
        root.write_bytes(original)
        dependency = self.libs / 'libEGL.so'; saved = dependency.read_bytes(); dependency.unlink()
        with self.assertRaisesRegex(ValueError, 'Invalid regular'): self.inspect()
        dependency.write_bytes(saved)
        self.compile_root(needed=False)
        with self.assertRaisesRegex(ValueError, 'real EGL-dependent'): self.inspect()
        self.compile_root(missing='glDispatchCompute')
        with self.assertRaisesRegex(ValueError, 'exports missing'): self.inspect()
        self.assertFalse((self.output / 'report.json').exists())
        self.assertFalse((self.output / 'graphics-runtime.tar.gz').exists())

    def test_android_absolute_symlink_resolution_is_product_local_and_escape_or_cycle_refused(self):
        flat = self.product / 'apex/com.android.runtime/lib64/bionic/libc.so'; flat.parent.mkdir(parents=True)
        flat.write_bytes((self.libs / 'libEGL.so').read_bytes())
        link = self.libs / 'libc.so'; link.symlink_to('/apex/com.android.runtime/lib64/bionic/libc.so')
        path, logical = graphics.resolve_library(self.product, 'libc.so')
        self.assertEqual(path, flat); self.assertEqual(logical, '/apex/com.android.runtime/lib64/bionic/libc.so')
        link.unlink(); link.symlink_to('/outside/private.so')
        with self.assertRaisesRegex(ValueError, 'escapes'): graphics.resolve_library(self.product, 'libc.so')
        link.unlink(); link.symlink_to('libc.so')
        with self.assertRaisesRegex(ValueError, 'escapes'): graphics.resolve_library(self.product, 'libc.so')

    def test_source_inspection_accepts_real_module_shape_and_rejects_ndk_only(self):
        source = self.android / 'frameworks/native/opengl/libs/Android.bp'; source.parent.mkdir(parents=True)
        source.write_text('cc_library_shared {\n name: "libGLESv3",\n defaults: ["gles_libs_defaults"],\n srcs: ["GLES2/gl2.cpp"],\n}\n')
        with patch.object(graphics.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, stdout='fixture-head\n')):
            result = graphics.source_info(self.android)
        self.assertEqual(result['module'], 'libGLESv3'); self.assertEqual(result['revision'], 'fixture-head')
        source.write_text('ndk_library {\n name: "libGLESv3",\n}\n')
        with self.assertRaisesRegex(ValueError, 'implementation'): graphics.source_info(self.android)

    def test_changed_runtime_and_existing_dangling_report_link_are_refused(self):
        runtime = self.product / 'system/apex/com.android.runtime.apex'; saved = runtime.read_bytes()
        runtime.write_bytes(b'changed Runtime')
        with self.assertRaisesRegex(ValueError, 'Runtime APEX changed'): self.inspect()
        runtime.write_bytes(saved)
        report = self.output / 'report.json'; report.symlink_to(self.root / 'nonexistent-private-target')
        with self.assertRaisesRegex(ValueError, 'exists'): self.inspect()
        self.assertTrue(report.is_symlink())
        self.assertFalse((self.root / 'nonexistent-private-target').exists())


class GraphicsBuildContextTests(unittest.TestCase):
    def test_build_entrypoint_rejects_host_before_android_commands(self):
        env = dict(os.environ); env.pop('MERSDKUBU', None)
        result = subprocess.run(['bash', str(ROOT / 'tools/build-graphics-runtime.sh')], env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0); self.assertIn('Ubuntu HABUILD_SDK', result.stderr)


if __name__ == '__main__': unittest.main()
