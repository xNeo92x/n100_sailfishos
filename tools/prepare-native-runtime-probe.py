#!/usr/bin/env python3
"""Prepare and collect a RAM probe for the reviewed rebuilt no-DEX HAL RPM."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'validation/native-runtime-rpm-manifest-user-20261026.json'
REFERENCE_SHA = '035025fe9b3fa586cb09aa20644db9f90e668d36f1770302e346ab37bc636960'
SPEC = importlib.util.spec_from_file_location('native_rpm_inputs', ROOT / 'tools/prepare-native-runtime-rpm.py')
inputs = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(inputs)
require = inputs.rpm.require
sha = inputs.rpm.sha
PROFILE = 'N100_NATIVE_RPM_PROFILE ' + REFERENCE_SHA


def reviewed():
    raw = REFERENCE.read_bytes()
    require(sha(raw) == REFERENCE_SHA, 'Reviewed RPM manifest changed')
    manifest = json.loads(raw)
    build, build_sha = inputs.reference()
    require(manifest['kind'] == 'native-no-dex-rpm-inputs' and manifest['passed'] and
            manifest['source_build_report_sha256'] == build_sha, 'Unexpected RPM input provenance')
    for key, source in [('source_apex', 'apex'), ('payload', 'payload'),
                        ('runtime_manifest', 'manifest'), ('verified_core_reference', 'core_files')]:
        require(manifest[key] == build[source], 'RPM manifest differs from native build: ' + key)
    for name, pin in inputs.expected_files(build).items():
        require(manifest['hal_tree']['regular_file_hashes'].get(name) == pin['sha256'], 'HAL file pin differs: ' + name)
    require(manifest['flat_bionic_matches_new_build'] and not manifest['device_tested'], 'Unexpected RPM audit scope')
    return manifest


def render(manifest):
    checks = '\n'.join('{} {} {}'.format(n, p['bytes'], p['sha256'])
                       for n, p in manifest['verified_core_reference'].items())
    script = (ROOT / 'tools/runtime-rpm-probe.sh.in').read_text()
    for token, value in {'@PAYLOAD_SHA@': manifest['payload']['sha256'],
                         '@PAYLOAD_BYTES@': manifest['payload']['bytes'], '@FILE_CHECKS@': checks,
                         '@HAL_SHA@': manifest['hal_archive_sha256'],
                         '@HAL_BYTES@': manifest['hal_archive_bytes']}.items():
        script = script.replace(token, str(value))
    script = script.replace("echo 'N100 temporary Android Runtime RPM probe V1'",
                            "echo '" + PROFILE + "'\necho 'N100 native no-DEX Runtime RPM probe'")
    require('@PAYLOAD_' not in script and '@FILE_CHECKS@' not in script and '@HAL_' not in script,
            'Unresolved script tokens')
    require(PROFILE in script, 'Probe profile banner missing')
    return script


def verify_file(path, size, digest):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size == size,
            'Bundle file missing/changed: ' + str(path))
    require(sha(path.read_bytes()) == digest, 'Bundle hash mismatch: ' + str(path))


def verify_data(directory, manifest):
    verify_file(directory / 'apex_payload.img', manifest['payload']['bytes'], manifest['payload']['sha256'])
    verify_file(directory / 'hal-system.tar.gz', manifest['hal_archive_bytes'], manifest['hal_archive_sha256'])


def bundle_manifest(manifest, script):
    return {'schema': 1, 'kind': 'temporary-native-no-dex-rpm-probe',
            'reviewed_input_manifest_sha256': REFERENCE_SHA,
            'source_rpm': manifest['source_rpm'], 'payload': manifest['payload'],
            'hal_archive_bytes': manifest['hal_archive_bytes'],
            'hal_archive_sha256': manifest['hal_archive_sha256'],
            'script_sha256': sha(script.encode()), 'device_tested': False,
            'persistent_installation_performed': False, 'systemd_started': False,
            'android_hal_started': False, 'flashable_release': False}


def locate(base):
    matches = []
    for directory in sorted(base.glob('native-runtime-rpm-*')):
        path = directory / 'manifest.json'
        if path.is_file() and not path.is_symlink() and path.stat().st_size == REFERENCE.stat().st_size:
            if sha(path.read_bytes()) == REFERENCE_SHA: matches.append(directory)
    require(len(matches) == 1, 'Expected exactly one reviewed native-runtime-rpm-* bundle; use --input-dir explicitly')
    return matches[0]


def prepare(source, output):
    manifest = reviewed()
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new probe directory')
    verify_file(source / 'manifest.json', REFERENCE.stat().st_size, REFERENCE_SHA)
    verify_data(source, manifest)
    script = render(manifest)
    subprocess.run(['sh', '-n'], input=script, text=True, check=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.native-probe-', dir=str(output.parent)))
    try:
        for name in ('apex_payload.img', 'hal-system.tar.gz'):
            shutil.copyfile(source / name, staging / name)
        verify_data(staging, manifest)
        (staging / 'runtime-probe.sh').write_text(script)
        (staging / 'runtime-probe.sh').chmod(0o755)
        bundle = bundle_manifest(manifest, script)
        (staging / 'manifest.json').write_text(json.dumps(bundle, indent=2) + '\n')
        staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Prepared new native HAL RAM probe: ' + str(output))
    print('runtime-probe.sh SHA-256: ' + sha(script.encode()))
    return bundle


def classify(log, manifest):
    required = [PROFILE, 'Verified payload SHA-256: ' + manifest['payload']['sha256'],
                'Verified every regular file in the selected HAL RPM system tree.',
                'N100_LINKER_HELP_EXIT=0', 'N100_LINKERCONFIG_HELP_EXIT=0',
                'N100_RUNTIME_PROBE_RESULT exit=0 cleanup_failed=0']
    required += ['Verified runtime core: ' + name for name in manifest['verified_core_reference']]
    required += ['N100_DEPENDENCY_EXIT /system/bin/' + name + ' 0'
                 for name in ('servicemanager', 'hwservicemanager', 'logd')]
    lines = log.splitlines()
    missing = [line for line in required if lines.count(line) != 1]
    prefixes = ('N100_NATIVE_RPM_PROFILE ', 'N100_LINKER_HELP_EXIT=',
                'N100_LINKERCONFIG_HELP_EXIT=', 'N100_DEPENDENCY_EXIT ', 'N100_RUNTIME_PROBE_RESULT ')
    unexpected = [line for line in lines if line.startswith(prefixes) and line not in required]
    return {'dependency_probe_passed': not missing and not unexpected,
            'missing_or_duplicate_markers': missing, 'unexpected_status_markers': unexpected,
            'scope': 'Only linker help and three dependency lists; no service main or systemd start.'}


def collect(bundle_dir, log_path, output):
    manifest = reviewed()
    verify_data(bundle_dir, manifest)
    script = render(manifest).encode()
    verify_file(bundle_dir / 'runtime-probe.sh', len(script), sha(script))
    bundle = json.loads((bundle_dir / 'manifest.json').read_text())
    require(bundle == bundle_manifest(manifest, script.decode()), 'Probe manifest changed')
    require(log_path.is_file() and 0 < log_path.stat().st_size <= 1024 * 1024, 'Expected a bounded device log')
    log = log_path.read_text(errors='replace')
    result = {'schema': 1, 'kind': 'native-no-dex-rpm-device-probe-report', 'bundle': bundle,
              'bundle_integrity_rechecked': True, 'device_probe_log': log,
              'assessment': classify(log, manifest), 'rootfs_boot_tested': False,
              'android_services_started': False, 'nnapi_requirement_resolved': False,
              'flashable_release': False}
    with output.open('x') as stream: stream.write(json.dumps(result, indent=2) + '\n')
    print('Report: ' + str(output))
    print('Dependency probe passed: ' + str(result['assessment']['dependency_probe_passed']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--input-dir', type=Path)
    prep.add_argument('--output-dir', type=Path, required=True)
    report = commands.add_parser('report')
    report.add_argument('--bundle-dir', type=Path, required=True)
    report.add_argument('--probe-log', type=Path, required=True)
    report.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'prepare':
            source = args.input_dir if args.input_dir is not None else locate(Path.home() / 'n100-build')
            prepare(source, args.output_dir.absolute())
        else: collect(args.bundle_dir, args.probe_log, args.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Native RAM probe preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__': main()
