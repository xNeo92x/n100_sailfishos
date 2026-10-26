# SPDX-License-Identifier: MIT
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class MediaBuildFlowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.port = self.base / "port"
        self.android = self.base / "android"
        for directory in ("tools", "config"):
            (self.port / directory).mkdir(parents=True)
        for name in ("build-hal.sh", "build-packages.sh", "check-droidmedia.py"):
            shutil.copy2(ROOT / "tools" / name, self.port / "tools" / name)
        (self.port / "config/port.env").write_text("")
        for name in ("n100.py", "fix-webview-link.py", "boot-debug.py", "check-hal-runtime.py"):
            (self.port / "tools" / name).write_text("# Fixture: upstream checks are outside this orchestration test.\n")
        files = (
            "rpm/droid-hal-billie2.spec",
            "kernel/oneplus/sm4250/arch/arm64/configs/vendor/sailfish-billie2_defconfig",
            "vendor/oneplus/billie2/billie2-vendor.mk",
            "vendor/oneplus/billie2/BoardConfigVendor.mk",
            ".n100-hybris-patches-applied",
        )
        for relative in files:
            path = self.android / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n")
        (self.android / "build").mkdir()
        (self.android / "build/envsetup.sh").write_text(
            'breakfast() { echo "$*" > selected-product; }\n'
            'mka() { echo "$*" > requested-targets; echo fixture-build; '
            'return "${FIXTURE_BUILD_EXIT:-0}"; }\n'
            'repo() { echo fixture-manifest; }\n'
            'perl() { return 0; }\n'
        )
        header = bytearray(64)
        header[:7] = b"\x7fELF\x02\x01\x01"
        header[16:20] = b"\x03\x00\xb7\x00"
        for relative in ("system/lib64/libdroidmedia.so", "system/lib64/libminisf.so",
                         "system/bin/minimediaservice", "system/bin/minisfservice"):
            path = self.android / "out/target/product/billie2" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(header)

    def run_build(self, mode=None, **overrides):
        env = dict(os.environ, ANDROID_ROOT=str(self.android), N100_DEVICE_REPORT="fixture",
                   MERSDKUBU="1", CCACHE_EXEC="", USE_CCACHE="")
        env.update(overrides)
        args = ["bash", str(self.port / "tools/build-hal.sh")]
        if mode is not None:
            args.append(mode)
        return subprocess.run(args, env=env, capture_output=True, text=True)

    def test_media_only_selects_the_existing_product_and_keeps_its_own_log(self):
        result = self.run_build("media")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.android / "requested-targets").read_text().strip(), "droidmedia")
        self.assertEqual((self.android / "selected-product").read_text().strip(), "billie2")
        self.assertIn("fixture-build", (self.android / "n100-build-logs/droidmedia.log").read_text())

    def test_default_build_includes_media(self):
        result = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.android / "requested-targets").read_text().strip(), "hybris-hal droidmedia hwservicemanager logd linkerconfig libGLESv3")

    def test_runtime_build_requests_services_without_boot_or_media_targets(self):
        result = self.run_build("runtime")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.android / "requested-targets").read_text().strip(), "hwservicemanager logd linkerconfig libGLESv3")
        self.assertIn("fixture-build", (self.android / "n100-build-logs/runtime.log").read_text())
        self.assertFalse((self.android / "n100-build-logs/kernel-config-check.log").exists())

    def test_runtime_build_refuses_the_platform_sdk(self):
        result = self.run_build("runtime", MERSDKUBU="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.android / "requested-targets").exists())

    def test_boot_only_build_selects_hybris_boot_and_keeps_its_own_log(self):
        result = self.run_build("boot")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.android / "requested-targets").read_text().strip(), "hybris-boot")
        self.assertIn("fixture-build", (self.android / "n100-build-logs/boot-debug.log").read_text())

    def test_boot_build_refuses_the_platform_sdk(self):
        result = self.run_build("boot", MERSDKUBU="")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.android / "requested-targets").exists())

    def test_compiler_failure_is_not_hidden_by_tee(self):
        result = self.run_build("media", FIXTURE_BUILD_EXIT="47")
        self.assertEqual(result.returncode, 47, result.stderr)
        self.assertNotIn("build finished", result.stdout)

    def test_media_build_refuses_the_platform_sdk(self):
        result = self.run_build("media", MERSDKUBU="")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Ubuntu HABUILD_SDK", result.stderr)
        self.assertFalse((self.android / "requested-targets").exists())

    def test_rpm_stage_stops_before_packing_incomplete_android_outputs(self):
        commands = self.base / "bin"
        commands.mkdir()
        for name in ("mb2", "sdk-assistant"):
            path = commands / name
            path.write_text("#!/bin/sh\nexit 0\n")
            path.chmod(0o755)
        upstream = self.android / "rpm/dhd/helpers/build_packages.sh"
        upstream.parent.mkdir(parents=True)
        upstream.write_text("#!/bin/sh\ntouch upstream-was-called\n")
        (self.android / "out/target/product/billie2/hybris-boot.img").write_bytes(b"fixture")
        (self.android / "out/target/product/billie2/system/lib64/libminisf.so").unlink()
        env = dict(os.environ, ANDROID_ROOT=str(self.android), RELEASE="5.1.0.11",
                   PATH=str(commands) + os.pathsep + os.environ["PATH"])
        result = subprocess.run(["bash", str(self.port / "tools/build-packages.sh"), "media"],
                                env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("libminisf.so", result.stderr)
        self.assertFalse((self.android / "upstream-was-called").exists())

    def test_hal_rpm_stops_before_packer_when_services_are_missing(self):
        for name in ('check-hal-runtime.py', 'inspect-android-output.py'):
            shutil.copy2(ROOT / 'tools' / name, self.port / 'tools' / name)
        commands = self.base / 'bin'
        commands.mkdir()
        for name in ('mb2', 'sdk-assistant'):
            path = commands / name
            path.write_text('#!/bin/sh\nexit 0\n')
            path.chmod(0o755)
        upstream = self.android / 'rpm/dhd/helpers/build_packages.sh'
        upstream.parent.mkdir(parents=True)
        upstream.write_text('#!/bin/sh\ntouch upstream-was-called\n')
        (self.android / 'out/target/product/billie2/hybris-boot.img').write_bytes(b'fixture')
        result = subprocess.run(['bash', str(self.port / 'tools/build-packages.sh'), 'hal'],
                                env=dict(os.environ, ANDROID_ROOT=str(self.android), RELEASE='5.1.0.11',
                                         PATH=str(commands) + os.pathsep + os.environ['PATH']),
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('system/bin/hwservicemanager', result.stdout)
        self.assertFalse((self.android / 'upstream-was-called').exists())


if __name__ == "__main__":
    unittest.main()
