import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('gles_hal_rpm', ROOT / 'tools/check-gles-hal-rpm.py')
check = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(check)
P = check.PREFIX


def member(name, mode, body=b'', ino=1, nlink=1):
    fields = [ino, mode, 0, 0, nlink, 0, len(body), 0, 0, 0, 0, len(name) + 1, 0]
    head = b'070701' + b''.join(b'%08x' % value for value in fields) + name.encode() + b'\0'
    head += b'\0' * (-len(head) % 4)
    return head + body + b'\0' * (-len(body) % 4)


def archive(*members):
    return b''.join(members) + member('TRAILER!!!', 0)


BASE = [member(P + 'lib64', 0o40755, ino=2), member(P + 'lib64/libEGL.so', 0o100644, b'egl', ino=3),
        member(P + 'lib64/liblink.so', 0o120777, b'libEGL.so', ino=4)]
GLES = [member(P + 'lib64/libGLESv3.so', 0o100644, b'gles64', ino=5),
        member(P + 'lib/libGLESv3.so', 0o100644, b'gles32', ino=6)]


class GlesHalRpmTests(unittest.TestCase):
    def test_cpio_parser_reads_types_and_resolves_hardlinks(self):
        data = archive(*BASE, member(P + 'bin/a', 0o100755, b'', ino=9, nlink=2),
                       member(P + 'bin/b', 0o100755, b'shared', ino=9, nlink=2))
        entries = check.parse_cpio(data)
        self.assertEqual(entries[P + 'lib64/liblink.so'], {'mode': '0o777', 'type': 'symlink', 'target': 'libEGL.so'})
        self.assertEqual(entries[P + 'bin/a'], entries[P + 'bin/b'])
        self.assertEqual(entries[P + 'bin/a']['sha256'], check.sha(b'shared'))
        with self.assertRaises(ValueError): check.parse_cpio(b'garbage')

    def test_only_the_two_gles_additions_with_the_tested_64bit_hash_pass(self):
        old = check.parse_cpio(archive(*BASE)); new = check.parse_cpio(archive(*BASE, *GLES))
        self.assertEqual(check.compare(old, new, check.sha(b'gles64'))[3], [])
        self.assertTrue(check.compare(old, new, check.sha(b'other'))[3])
        changed = check.parse_cpio(archive(BASE[0], member(P + 'lib64/libEGL.so', 0o100644, b'EGL', ino=3), BASE[2], *GLES))
        self.assertIn('changed', ' '.join(check.compare(old, changed, check.sha(b'gles64'))[3]))
        removed = check.parse_cpio(archive(*BASE[:2], *GLES))
        self.assertIn('removed', ' '.join(check.compare(old, removed, check.sha(b'gles64'))[3]))
        extra = check.parse_cpio(archive(*BASE, *GLES, member(P + 'lib/libextra.so', 0o100644, b'x', ino=7)))
        self.assertIn('added', ' '.join(check.compare(old, extra, check.sha(b'gles64'))[3]))
        mode = check.parse_cpio(archive(BASE[0], member(P + 'lib64/libEGL.so', 0o100755, b'egl', ino=3), BASE[2], *GLES))
        self.assertTrue(check.compare(old, mode, check.sha(b'gles64'))[3])

    def test_selinux_mode_requires_the_three_context_files_and_their_directory(self):
        old = check.parse_cpio(archive(*BASE))
        contexts = [member(P + 'etc/selinux', 0o40755, ino=20)] + [
            member(name, 0o100644, b'manager u:object_r:x:s0\n', ino=21 + i) for i, name in enumerate(sorted(check.SELINUX))]
        new = check.parse_cpio(archive(*BASE, *GLES, *contexts))
        self.assertEqual(check.compare(old, new, check.sha(b'gles64'), selinux=True)[3], [])
        self.assertTrue(check.compare(old, new, check.sha(b'gles64'), selinux=False)[3])
        partial = check.parse_cpio(archive(*BASE, *GLES, *contexts[:-1]))
        self.assertTrue(check.compare(old, partial, check.sha(b'gles64'), selinux=True)[3])
        empty = check.parse_cpio(archive(*BASE, *GLES, *contexts[:-1], member(sorted(check.SELINUX)[-1], 0o100644, b'', ino=40)))
        self.assertTrue(check.compare(old, empty, check.sha(b'gles64'), selinux=True)[3])

    def test_new_rpm_selection_ignores_subpackages_and_the_reference(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup); base = Path(temp.name)
        for name in [check.REFERENCE_NAME, *check.IGNORED, 'droid-hal-billie2-devel-0.0.6-202611111900.aarch64.rpm',
                     'droid-hal-billie2-img-boot-0.0.6-202611111900.aarch64.rpm']:
            (base / name).write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'none'): check.find_new_rpm(base)
        (base / 'droid-hal-billie2-0.0.6-202611111900.aarch64.rpm').write_bytes(b'')
        self.assertEqual(check.find_new_rpm(base).name, 'droid-hal-billie2-0.0.6-202611111900.aarch64.rpm')
        (base / 'droid-hal-billie2-0.0.6-202611111901.aarch64.rpm').write_bytes(b'')
        with self.assertRaisesRegex(ValueError, 'exactly one'): check.find_new_rpm(base)

    def test_real_reference_rpm_when_available(self):
        path = Path(os.path.expanduser('~/n100-build/rpm-archive')) / check.REFERENCE_NAME
        if not path.is_file(): self.skipTest('device-tested RPM not on this machine')
        entries = check.parse_cpio(check.payload(path))
        self.assertNotIn(P + 'lib64/libGLESv3.so', entries)
        self.assertEqual(entries[P + 'lib64/libGLESv2.so']['type'], 'file')

    def test_launcher_syntax_and_scope(self):
        script = ROOT / 'tools/run-hal-rpm-build.sh'
        subprocess.run(['bash', '-n', str(script)], check=True)
        text = script.read_text()
        self.assertIn('build-packages.sh" hal', text); self.assertIn('check-gles-hal-rpm.py', text)
        self.assertNotIn('fastboot', text)
        selinux = ROOT / 'tools/run-selinux-rpm-build.sh'
        subprocess.run(['bash', '-n', str(selinux)], check=True)
        text = selinux.read_text()
        self.assertIn('build-hal.sh" runtime', text); self.assertIn('--with-selinux-contexts', text)
        self.assertNotIn('fastboot', text)


if __name__ == '__main__': unittest.main()
