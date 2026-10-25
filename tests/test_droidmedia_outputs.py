# SPDX-License-Identifier: MIT
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("check_droidmedia", ROOT / "tools/check-droidmedia.py")
MEDIA = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MEDIA)


class DroidmediaOutputsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.android = Path(self.temp.name)
        self.product = self.android / "out/target/product/billie2"

    def write_outputs(self):
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        header[16:20] = b"\x03\x00\xb7\x00"
        for relative in MEDIA.OUTPUTS:
            path = self.product / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(header)

    def test_complete_aarch64_inputs(self):
        self.write_outputs()
        self.assertEqual(MEDIA.check_outputs(self.android), [])

    def test_reports_all_missing_inputs(self):
        self.assertEqual(len(MEDIA.check_outputs(self.android)), 4)

    def test_partial_build_is_rejected_even_when_main_library_exists(self):
        self.write_outputs()
        (self.product / "system/lib64/libminisf.so").unlink()
        errors = MEDIA.check_outputs(self.android)
        self.assertEqual(len(errors), 1)
        self.assertIn("libminisf.so", errors[0])

    def test_rejects_wrong_architecture_and_empty_outputs(self):
        self.write_outputs()
        path = self.product / "system/bin/minimediaservice"
        header = bytearray(path.read_bytes())
        header[18:20] = b"\x3e\x00"  # x86_64
        path.write_bytes(header)
        (self.product / "system/bin/minisfservice").write_bytes(b"")
        self.assertEqual(len(MEDIA.check_outputs(self.android)), 2)

    def test_cli_failure_points_to_the_android_environment(self):
        result = subprocess.run([sys.executable, str(ROOT / "tools/check-droidmedia.py"),
                                 "--android-root", str(self.android)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Ubuntu HABUILD_SDK", result.stderr)
        self.assertIn('build-hal.sh" media', result.stderr)


if __name__ == "__main__":
    unittest.main()
