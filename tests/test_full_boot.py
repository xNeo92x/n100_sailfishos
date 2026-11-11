import hashlib
import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('full_debug_boot', ROOT / 'tools/make-full-debug-boot.py')
tool = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(tool)


def image(cmdline):
    data = bytearray(4096 * 3)
    data[:8] = b'ANDROID!'
    data[64:64 + len(cmdline)] = cmdline.encode()
    data[5000:5010] = b'kernelbits'
    return bytes(data)


class FullBootTests(unittest.TestCase):
    def derive(self, data):
        with patch.object(tool, 'SOURCE_SHA', hashlib.sha256(data).hexdigest()):
            return tool.derive(data)

    def test_only_the_debug_token_changes(self):
        data = image('console=x selinux=1 n100_debug=1')
        out, old, new = self.derive(data)
        self.assertEqual(new, 'console=x selinux=1 bootmode=debug')
        self.assertEqual(out[576:], data[576:]); self.assertEqual(out[:64], data[:64])

    def test_unexpected_inputs_are_refused(self):
        for cmdline in ('console=x', 'n100_debug=1 bootmode=normal', 'n100_debug=1 n100_debug=1'):
            with self.subTest(cmdline=cmdline), self.assertRaises(ValueError):
                self.derive(image(cmdline))
        with self.assertRaisesRegex(ValueError, 'device-tested'):
            tool.derive(image('n100_debug=1'))

    def test_scripts_and_documented_pins(self):
        diag = ROOT / 'tools/fullboot-diag.sh'
        subprocess.run(['sh', '-n', str(diag)], check=True)
        doc = (ROOT / 'docs/FULL_BOOT_TEST.md').read_text()
        self.assertIn("echo '" + hashlib.sha256(diag.read_bytes()).hexdigest() + "  /tmp/fullboot-diag.sh'", doc)
        text = diag.read_text()
        for forbidden in ('rm -', 'mkfs', 'dd if', 'flash', 'mount -o remount'):
            self.assertNotIn(forbidden, text)
        launcher = ROOT / 'tools/run-full-boot-test.sh'
        subprocess.run(['bash', '-n', str(launcher)], check=True)
        text = launcher.read_text()
        self.assertEqual(text.count('fastboot -S 512M flash userdata'), 1)
        self.assertLess(text.index('[[ "$answer" == JA ]]'), text.index('fastboot -S 512M flash userdata'))
        self.assertNotIn('flash boot', text); self.assertNotIn('flash dtbo', text)
        self.assertIn('ba6eecd996dd7d729cc25601738fba820592c6bc7612b0734379cf92833f18df', text)
        self.assertIn('74d16a51cb4a79c6f67ae7c84ff3cb17722cc6b0cfafee2d543fe2bb8fce84fd', text)


if __name__ == '__main__': unittest.main()
