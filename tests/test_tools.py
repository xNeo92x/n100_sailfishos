import importlib.util
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
import zipfile
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("n100", ROOT / "tools/n100.py")
n100 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(n100)


class ConfigTests(unittest.TestCase):
    def test_overrides_disabled_and_enabled_without_duplicates(self):
        merged = n100.merge_config("# stock\n# CONFIG_VT is not set\nCONFIG_NET=y\nCONFIG_DUMMY=y\n",
                                   "CONFIG_VT=y\n# CONFIG_DUMMY is not set\nCONFIG_DEVTMPFS=y\n")
        self.assertEqual(n100.parse_config(merged), {
            "CONFIG_VT": "y", "CONFIG_NET": "y", "CONFIG_DUMMY": "n", "CONFIG_DEVTMPFS": "y"})
        self.assertEqual(merged.count("CONFIG_VT="), 1)
        self.assertIn("# stock", merged)

    def test_preserves_string_and_numeric_values(self):
        merged = n100.merge_config('CONFIG_CMDLINE="stock"\nCONFIG_HZ=100\n', 'CONFIG_HZ=300\n')
        self.assertEqual(n100.parse_config(merged)["CONFIG_CMDLINE"], '"stock"')
        self.assertEqual(n100.parse_config(merged)["CONFIG_HZ"], "300")


class FileTests(unittest.TestCase):
    def test_report_creates_requested_file_and_returns_absolute_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"new/report.json"
            actual=n100.write_report(path,'{"report":1}\n')
            self.assertEqual(actual,path)
            self.assertTrue(actual.is_absolute())
            self.assertEqual(json.loads(actual.read_text()),{"report":1})

    def test_existing_report_is_preserved_and_new_collection_gets_another_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"n100-device-report-v2.json"
            path.write_text('original report\n')
            actual=n100.write_report(path,'{"report":2}\n')
            self.assertNotEqual(actual,path)
            self.assertEqual(actual.parent,path.parent)
            self.assertTrue(actual.name.startswith("n100-device-report-v2-"))
            self.assertEqual(path.read_text(),'original report\n')
            self.assertEqual(json.loads(actual.read_text()),{"report":2})

    def test_report_collision_even_with_same_timestamp_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(n100,"datetime") as clock:
            clock.now.return_value.strftime.return_value="fixed-timestamp"
            path=Path(tmp)/"report.json"
            path.write_text("original")
            outputs=[n100.write_report(path,str(i)) for i in range(3)]
            self.assertEqual(len(set(outputs)),3)
            self.assertEqual([p.read_text() for p in outputs],["0","1","2"])
            self.assertEqual(path.read_text(),"original")

    def test_report_does_not_follow_existing_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"target"
            target.write_text("keep")
            link=Path(tmp)/"report.json"
            link.symlink_to(target)
            actual=n100.write_report(link,"new report")
            self.assertNotEqual(actual,link)
            self.assertTrue(link.is_symlink())
            self.assertEqual(target.read_text(),"keep")
            self.assertEqual(actual.read_text(),"new report")

    def test_idempotent_but_refuses_local_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "new/file.txt"
            n100.write_new(dest, "original")
            n100.write_new(dest, "original")
            with self.assertRaises(n100.PortError):
                n100.write_new(dest, "replacement")
            self.assertEqual(dest.read_text(), "original")

    def test_never_follows_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("keep")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            with self.assertRaises(n100.PortError):
                n100.write_new(link, "keep")


class DeviceTests(unittest.TestCase):
    def test_n10_is_not_n100(self):
        self.assertEqual(n100.identify({"ro.product.device": "billie"}), "wrong-device-nord-n10")

    def test_vendor_codename_and_stock_alias(self):
        self.assertEqual(n100.identify({"ro.product.vendor.device": "billie2"}), "n100-candidate")
        self.assertEqual(n100.identify({"ro.product.device": "OnePlusN100"}), "n100-candidate")

    def test_model_only_is_not_verified(self):
        self.assertEqual(n100.identify({"ro.product.model": "BE2013"}), "model-only-unconfirmed")
        self.assertEqual(n100.identify({}), "unconfirmed")

    def test_probe_does_not_query_private_identifiers_or_write_to_device(self):
        calls = []
        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv == ["adb", "devices"]:
                return "List of devices attached\nexample\tdevice"
            if argv[-1] in ("ro.product.device", "ro.product.vendor.device"):
                return "billie2"
            return "fixture"
        with tempfile.TemporaryDirectory() as tmp, patch.object(n100, "run", fake_run):
            output = Path(tmp) / "report.json"
            n100.probe(type("Args", (), {"serial": None, "output": str(output)})())
            report = json.loads(output.read_text())
            self.assertEqual(report["identity"], "n100-candidate")
        self.assertNotIn("ro.serialno", str(calls))
        self.assertFalse(any(word in call for call in calls for word in ("reboot", "flash", "setprop", "su -c")))

    def test_probe_rejects_missing_or_multiple_devices(self):
        for devices in ("List of devices attached", "List of devices attached\na\tdevice\nb\tdevice"):
            with patch.object(n100, "run", return_value=devices), self.assertRaises(n100.PortError):
                n100.probe(type("Args", (), {"serial": None, "output": "/unused"})())


class ReportTests(unittest.TestCase):
    def fixture(self):
        return json.loads((ROOT / "tests/fixtures/be2013-stock11.json").read_text())

    def test_stock11_is_firmware_baseline_for_android13_hal_candidate(self):
        result = n100.analyze_report(self.fixture())
        self.assertEqual(n100.read_lock()["android_build_version"], 13)
        self.assertEqual(result["source_compatibility"], "candidate-generation-match")
        self.assertEqual(result["platform"], "bengal")
        self.assertEqual(result["active_slot"], "_b")
        self.assertTrue(result["bootloader_locked"])
        self.assertFalse(result["deployable"])
        self.assertIn("bootloader-locked", result["blockers"])
        self.assertIn("encrypted-data-rootfs-access-unimplemented", result["blockers"])
        self.assertIn("system_ext_b", result["logical_partitions_observed"])
        self.assertEqual(result["physical_aliases_observed"]["boot_b"], "/dev/block/sde32")
        self.assertEqual(result["physical_aliases_observed"]["userdata"], "/dev/block/sda16")
        self.assertFalse(result["fstab_collected"])

    def test_report_cannot_self_assert_device_identity(self):
        report = self.fixture()
        report["identity"] = "n100-candidate"
        report["properties"]["ro.product.device"] = "billie"
        self.assertEqual(n100.analyze_report(report)["source_compatibility"], "wrong-or-unconfirmed-device")

    def test_wrong_android_or_vendor_generation_is_rejected(self):
        for key,value in (("ro.build.version.sdk","29"), ("ro.vendor.build.version.sdk","29"),
                          ("ro.vendor.build.fingerprint","OnePlus/N100/N100:10/fixture/fixture:user/release-keys")):
            report = self.fixture()
            report["properties"][key] = value
            result = n100.analyze_report(report)
            self.assertNotEqual(result["source_compatibility"], "candidate-generation-match")
            self.assertIn("source-profile-not-compatible-or-unconfirmed", result["blockers"])

    def test_missing_data_does_not_imply_compatibility(self):
        report = {"properties": {"ro.product.device":"OnePlusN100"}}
        self.assertEqual(n100.analyze_report(report)["source_compatibility"], "unconfirmed")

    def test_partial_fstab_output_is_usable_even_when_another_path_fails(self):
        report = self.fixture()
        report["diagnostics"]["fstab_vendor"] = {
            "stdout": "system_ext /system_ext ext4 ro wait,logical,slotselect\n",
            "stderr": "cat: other file missing", "returncode": 1}
        self.assertTrue(n100.analyze_report(report)["fstab_collected"])

    def test_mapper_userdata_never_replaces_physical_userdata(self):
        physical, mapper = n100.block_aliases(
            "/dev/block/by-name:\nlrwxrwxrwx 1 root root 0 now userdata -> /dev/block/sda16\n"
            "/dev/block/mapper:\nlrwxrwxrwx 1 root root 0 now userdata -> /dev/block/dm-12\n")
        self.assertEqual(physical["userdata"], "/dev/block/sda16")
        self.assertEqual(mapper["userdata"], "/dev/block/dm-12")

    def test_failed_capture_preserves_both_streams_without_serial_in_error(self):
        completed = subprocess.CalledProcessError(1, ["adb","-s","PRIVATE_SERIAL","shell","fixture"],
                                                output="usable data\n", stderr="missing path\n")
        with patch.object(subprocess,"run",side_effect=completed):
            with self.assertRaises(n100.PortError) as caught:
                n100.run(["adb","-s","PRIVATE_SERIAL","shell","fixture"],capture=True)
        self.assertEqual(caught.exception.stdout,"usable data\n")
        self.assertEqual(caught.exception.stderr,"missing path\n")
        self.assertNotIn("PRIVATE_SERIAL",str(caught.exception))

    def test_probe_serializes_readable_partial_output_on_error(self):
        def fake_run(argv, **kwargs):
            if argv == ["adb", "devices"]:
                return "List of devices attached\nexample\tdevice"
            if argv[-1] == "cat /vendor/etc/fstab.qcom":
                exc=n100.PortError("exit 1")
                exc.stdout="system /system ext4 ro wait,logical\n"
                exc.stderr="one missing path\n"
                exc.returncode=1
                raise exc
            return "OnePlusN100" if argv[-1] == "ro.product.device" else ""
        with tempfile.TemporaryDirectory() as tmp, patch.object(n100,"run",fake_run):
            output=Path(tmp)/"report.json"
            n100.probe(type("Args",(),{"serial":None,"output":str(output)})())
            result=json.loads(output.read_text())["diagnostics"]["fstab_vendor"]
            self.assertEqual(result["stdout"],"system /system ext4 ro wait,logical\n")
            self.assertEqual(result["stderr"],"one missing path\n")
            self.assertEqual(result["returncode"],1)

    def test_incompatible_report_stops_before_source_downloads_or_directory_creation(self):
        report=self.fixture()
        report["properties"]["ro.build.version.sdk"]="29"
        with tempfile.TemporaryDirectory() as tmp, patch.object(n100,"run") as runner:
            path=Path(tmp)/"report.json"
            path.write_text(json.dumps(report))
            android=Path(tmp)/"android"
            args=type("Args",(),{"device_report":str(path),"android_root":str(android)})()
            with self.assertRaises(n100.PortError):
                n100.init_sources(args)
            runner.assert_not_called()
            self.assertFalse(android.exists())

    def test_old_source_tree_marker_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            android=Path(tmp)
            (android/".n100-source-profile.json").write_text(json.dumps({"lock_sha256":"old-profile"}))
            with self.assertRaises(n100.PortError):
                n100.require_profile(android)

    def test_board_overlay_preserves_upstream_and_hardware_specific_flags(self):
        original="TARGET_KERNEL_ADDITIONAL_FLAGS := OEM_TARGET_PRODUCT=$(PRODUCT_DEVICE)\n"
        result=n100.modified_board(original,n100.read_lock())
        self.assertTrue(result.startswith(original))
        self.assertIn("vendor/sailfish-billie2_defconfig",result)
        self.assertIn("selinux=1 androidboot.selinux=permissive",result)


class ConfigurationTests(unittest.TestCase):
    def test_v2_identifies_real_fstab_paths_and_vendor_baseline(self):
        report=json.loads((ROOT/"tests/fixtures/be2013-stock11-v2.json").read_text())
        result=n100.analyze_report(report)
        self.assertEqual(result["vendor_sdk"],"30")
        self.assertEqual(result["vndk_version"],"30")
        self.assertEqual(result["firmware_display"],"BE2013_7_231009")
        self.assertEqual(result["selinux_compatibility_version"],"30.0")
        self.assertEqual(result["fstab_paths_discovered"],["/vendor/etc/fstab.default",
                         "/vendor/etc/fstab.emmc","/vendor/etc/fstab_nodata.qcom"])
        self.assertFalse(result["fstab_collected"])
        self.assertEqual(len(result["vendor_vintf"]["hals"]),63)
        audio=next(h for h in result["vendor_vintf"]["hals"] if h["name"]=="android.hardware.audio")
        self.assertEqual(audio["versions"],["6.0"])
        self.assertEqual(audio["transport"],"hwbinder")
        self.assertFalse(result["deployable"])

    def test_equivalent_vintf_fqname_and_interface_forms_are_deduplicated(self):
        prefix='<manifest type="device" target-level="4"><hal format="hidl"><name>test.audio</name><transport>hwbinder</transport>'
        version='<version>6.0</version><interface><name>IFactory</name><instance>default</instance></interface>'
        fqname='<fqname>@6.0::IFactory/default</fqname>'
        end='</hal></manifest>'
        a=n100.vintf_inventory(prefix+version+end)
        b=n100.vintf_inventory(prefix+version+fqname+end)
        c=n100.vintf_inventory(prefix+fqname+end)
        self.assertEqual(a,b)
        self.assertEqual(b,c)

    def test_malformed_or_dtd_manifest_is_not_used(self):
        for xml in ('<manifest>','<!DOCTYPE manifest><manifest/>','<not-a-manifest/>'):
            with self.assertRaises(n100.PortError):
                n100.vintf_inventory(xml)
        report={"properties":{},"diagnostics":{"vintf_vendor":"<manifest>"}}
        self.assertEqual(n100.analyze_report(report)["vendor_vintf"]["status"],"invalid-or-incomplete")

    def test_discovery_accepts_only_safe_configuration_files(self):
        accepted=['/vendor/etc/fstab.default','/vendor/etc/vintf/manifest/android.hardware.audio@6.0.xml',
                  '/odm/etc/vintf/manifest_2.xml','/system_ext/etc/vintf/manifest.xml']
        rejected=['/data/user/0/private.xml','/vendor/etc/../secret.xml',
                  '/vendor/etc/vintf/manifest/foo.xml;touch-pwned','/vendor/etc/fstab.foo bar',
                  'ls: /odm/etc/vintf: Permission denied','/vendor/etc/vintf/manifest/../../data.xml']
        self.assertEqual(n100.discovered_config_paths('\n'.join(accepted+rejected)),sorted(accepted))

    def test_bundle_copies_configs_preserves_failures_and_never_writes_to_phone(self):
        calls=[]
        report={"schema":1,"properties":{"ro.product.device":"OnePlusN100"},"diagnostics":{}}
        def fake_run(argv,**kwargs):
            calls.append(argv)
            if argv[-1]==n100.CONFIG_DISCOVERY_COMMAND:
                return '/vendor/etc/fstab.default\n/vendor/etc/vintf/manifest.xml\n/data/private.xml\n'
            self.assertEqual(argv[:2],["adb","pull"])
            remote,local=argv[2:]
            if remote=="/vendor/etc/fstab.default":
                Path(local).write_text('system /system ext4 ro wait,logical\n')
            elif remote=="/vendor/etc/vintf/manifest.xml":
                Path(local).write_text('<manifest type="device"/>')
            else:
                error=n100.PortError("permission denied")
                error.returncode=1
                raise error
        with tempfile.TemporaryDirectory() as tmp, patch.object(n100,"collect_report",return_value=report), patch.object(n100,"run",fake_run):
            path=Path(tmp)/"n100-configs.zip"
            path.write_text("old bundle")
            output=n100.collect_configs(type("Args",(),{"serial":None,"output":str(path)})())
            self.assertNotEqual(path,output)
            self.assertEqual(path.read_text(),"old bundle")
            with zipfile.ZipFile(output) as bundle:
                self.assertIsNone(bundle.testzip())
                self.assertEqual(bundle.read('files/vendor/etc/fstab.default'),b'system /system ext4 ro wait,logical\n')
                inventory=json.loads(bundle.read('inventory.json'))
                self.assertEqual(sum(f["status"]=="collected" for f in inventory["files"]),2)
                self.assertTrue(any(f["status"]=="unavailable" for f in inventory["files"]))
                self.assertFalse(inventory["device_writes"])
                self.assertNotIn('files/data/private.xml',bundle.namelist())
        self.assertTrue(all(call[:2]==["adb","pull"] or call[-1]==n100.CONFIG_DISCOVERY_COMMAND for call in calls))


class BootTests(unittest.TestCase):
    def image(self, path, version=2, page=4096):
        header = bytearray(4096)
        header[:8] = b"ANDROID!"
        struct.pack_into("<I", header, 8, 1000)
        struct.pack_into("<I", header, 16, 1000)
        struct.pack_into("<II", header, 36, page, version)
        struct.pack_into("<I", header, 1644, 1660)
        struct.pack_into("<I", header, 1648, 1000)
        path.write_bytes(header + bytes(3 * 4096))

    def test_valid_v2_is_only_structurally_assessed(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "boot.img"
            self.image(image)
            result = n100.inspect_boot(image)
            self.assertEqual(result["header_version"], 2)
            self.assertEqual(result["assessment"], "structural-check-only-not-boot-validation")

    def test_truncated_image_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "boot.img"
            self.image(image)
            image.write_bytes(image.read_bytes()[:4096])
            with self.assertRaises(n100.PortError):
                n100.inspect_boot(image)

    def test_wrong_header_layout_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "boot.img"
            for version, page in ((3, 4096), (2, 2048)):
                self.image(image, version, page)
                with self.assertRaises(n100.PortError):
                    n100.inspect_boot(image)


class ManifestTests(unittest.TestCase):
    def test_manifest_matches_lock_without_path_collisions(self):
        lock = n100.read_lock()
        manifest = ET.parse(ROOT / "manifests/billie2.xml").getroot()
        for project in lock["projects"]:
            element = next(x for x in manifest.findall("project") if x.attrib["path"] == project["path"])
            self.assertEqual(element.attrib["revision"], project["revision"])
            self.assertEqual(element.attrib["name"], project["name"])
        paths = [x.attrib["path"] for x in manifest.findall("project")]
        self.assertEqual(len(paths), len(set(paths)))
        for project in lock["projects"] + lock["extensions"] + lock["packaging"]:
            self.assertRegex(project["revision"], r"^[0-9a-f]{40}$")
        for project in lock["extensions"]:
            element=next(x for x in manifest.findall("extend-project") if x.attrib["path"]==project["path"])
            self.assertEqual(element.attrib["revision"],project["revision"])

    def test_recorded_kernel_configuration_check_applies_to_current_source_inputs(self):
        lock=n100.read_lock()
        audit=json.loads((ROOT/"validation/kernel-config.json").read_text())
        kernel=next(p for p in lock["projects"] if p["path"]=="kernel/oneplus/sm4250")
        checker=next(p for p in lock["extensions"] if p["path"]=="hybris/mer-kernel-check")
        self.assertEqual(audit["kernel_revision"],kernel["revision"])
        self.assertEqual(audit["checker_revision"],checker["revision"])
        self.assertEqual(audit["fragment_sha256"],hashlib.sha256((ROOT/"kernel/sailfish.config").read_bytes()).hexdigest())


class SnapshotTests(unittest.TestCase):
    def test_real_git_package_setup_is_repeatable_and_preserves_edits(self):
        # Exercise gitlink creation and the archive-visible package snapshot,
        # using a local upstream repository instead of network or an SDK.
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            upstream = tmp / "upstream"
            upstream.mkdir()
            subprocess.run(["git", "init", "-q", str(upstream)], check=True)
            (upstream / "macros.inc").write_text("fixture macro\n")
            subprocess.run(["git", "-C", str(upstream), "add", "."], check=True)
            subprocess.run(["git", "-C", str(upstream), "-c", "user.name=Test", "-c",
                            "user.email=test@localhost", "commit", "-qm", "fixture"], check=True)
            revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
            source = tmp / "source"
            source.mkdir()
            (source / "device.spec").write_text("package fixture\n")
            (source / "policy").symlink_to("/vendor/etc/selinux/precompiled_sepolicy")
            dependency = {"parent": "rpm", "path": "dhd", "name": "local/fixture", "revision": revision}
            real_run = n100.run
            def local_run(argv, **kwargs):
                argv = [str(x) for x in argv]
                argv = [str(upstream) if x == "https://github.com/local/fixture.git" else x for x in argv]
                if argv[0] == "git":
                    argv[1:1] = ["-c", "protocol.file.allow=always"]
                return real_run(argv, **kwargs)
            dest = tmp / "installed"
            with patch.object(n100, "run", local_run):
                n100.init_package(dest, source, dependency)
                n100.init_package(dest, source, dependency)
                files = real_run(["git", "ls-files", "--stage"], cwd=dest, capture=True)
                self.assertIn("160000 " + revision, files)
                self.assertIn("120000", files)
                (dest / "device.spec").write_text("user edits\n")
                with self.assertRaises(n100.PortError):
                    n100.init_package(dest, source, dependency)
                self.assertEqual((dest / "device.spec").read_text(), "user edits\n")


if __name__ == "__main__":
    unittest.main()
