#!/usr/bin/env python3
"""Check a rebuilt no-DEX HAL RPM and prepare data for a later reviewed RAM probe."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_rpm_base', ROOT / 'tools/prepare-runtime-rpm-probe.py')
rpm = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(rpm)
REFERENCE = ROOT / 'validation/native-runtime-build-report-user-20261026.json'


def reference():
    data = REFERENCE.read_bytes(); report = json.loads(data)
    rpm.require(report['passed'] and report['kind'] == 'native-runtime-no-dex-build' and
                report['hal_build_outputs']['passed'], 'Reviewed native build did not pass')
    rpm.require('libdexfile.so' not in report['manifest']['require_native_libs'], 'Reviewed native build still requires DEX')
    return report, hashlib.sha256(data).hexdigest()


def expected_files(report):
    files = report['hal_build_outputs']['files']
    expected = {n[len('system/'):]: p for n, p in files.items() if n.startswith('system/')}
    linker = next((files[n] for n in ('system/bin/linkerconfig', 'apex/com.android.runtime/bin/linkerconfig') if n in files), None)
    rpm.require(linker is not None, 'Reviewed linkerconfig output missing')
    expected['bin/linkerconfig'] = linker
    # DHD copies these into its flat HAL library tree as well as packaging the APEX.
    for path, pin in report['core_files'].items():
        if path.startswith('lib64/bionic/'):
            expected['lib64/' + Path(path).name] = pin
    return expected


def read_payload(records, report):
    entry = records.get('apex/com.android.runtime.apex')
    rpm.require(entry is not None and stat.S_ISREG(entry['mode']), 'Packaged Runtime APEX missing')
    data = bytes(entry['data']); pin = report['apex']
    rpm.require((len(data), rpm.sha(data)) == (pin['bytes'], pin['sha256']), 'RPM contains an old or unreviewed Runtime APEX')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        rpm.require(archive.namelist().count('apex_payload.img') == 1, 'Ambiguous Runtime payload')
        expected = report['payload']
        rpm.require(archive.getinfo('apex_payload.img').file_size == expected['bytes'] <= rpm.LIMIT, 'Runtime payload size differs')
        payload = archive.read('apex_payload.img')  # ZIP CRC is checked by the reader.
    rpm.require(rpm.sha(payload) == expected['sha256'] and payload[1080:1082] == b'\x53\xef',
                'RPM Runtime payload differs from reviewed no-DEX build')
    return payload


def prepare(android, output, explicit=None):
    rpm.require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new bundle directory')
    report, report_sha = reference()
    package, package_info = rpm.select_rpm(android, explicit)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.native-rpm-', dir=str(output.parent)))
    try:
        cpio = staging / 'payload.cpio'
        with cpio.open('wb') as stream:
            subprocess.run(['rpm2cpio', str(package)], stdout=stream, stderr=subprocess.PIPE,
                           check=True, timeout=180, preexec_fn=rpm.limit_child)
        rpm.require(rpm.sha(package.read_bytes()) == package_info['sha256'], 'RPM changed during inspection')
        rpm.require(cpio.stat().st_size <= rpm.LIMIT, 'RPM CPIO exceeds bound')
        records = rpm.cpio_tree(cpio.read_bytes()); cpio.unlink()
        payload = read_payload(records, report)
        tree = rpm.write_hal_archive(records, staging / 'hal-system.tar.gz', expected=expected_files(report))
        (staging / 'apex_payload.img').write_bytes(payload)
        archive = staging / 'hal-system.tar.gz'
        manifest = {'schema': 1, 'kind': 'native-no-dex-rpm-inputs', 'passed': True,
                    'source_rpm': package_info, 'source_build_report_sha256': report_sha,
                    'source_apex': report['apex'], 'payload': report['payload'], 'runtime_manifest': report['manifest'],
                    'verified_core_reference': report['core_files'],
                    'hal_archive_bytes': archive.stat().st_size, 'hal_archive_sha256': rpm.sha(archive.read_bytes()),
                    'hal_tree': tree, 'flat_bionic_matches_new_build': True,
                    'previously_tested_core_hashes_unchanged': report['previously_tested_core_hashes_unchanged'],
                    'rpm_scripts_executed': False, 'rpm_signatures_verified': False,
                    'device_script_generated': False, 'device_tested': False,
                    'nnapi_requirement_resolved': False, 'phone_modified': False, 'ready_for_installation': False}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Native HAL RPM and flat Bionic checked against the reviewed build.')
    print('Share ' + str(output / 'manifest.json'))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--rpm', type=Path)
    args = parser.parse_args()
    try:
        prepare(args.android_root.resolve(), args.output_dir.absolute(), args.rpm)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'Native HAL RPM preparation failed: {}\n'.format(exc))


if __name__ == '__main__': main()
