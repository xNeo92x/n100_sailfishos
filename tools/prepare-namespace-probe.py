#!/usr/bin/env python3
"""Prepare a limited RAM dependency probe using reviewed generated linker namespaces."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'validation/native-linker-preview-report-user-20261026.json'
REFERENCE_SHA = 'ef75ded3e79478d00f0196c89e548c20f57ff5e73ea524b7337cbcac7fe62820'
SPEC = importlib.util.spec_from_file_location('namespace_preview', ROOT / 'tools/generate-linker-preview.py')
preview = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(preview)
SPEC = importlib.util.spec_from_file_location('namespace_native_probe', ROOT / 'tools/prepare-native-runtime-probe.py')
native = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(native)
sha = native.sha
require = native.require
CONFIG_FILES = {'ld.config.txt', 'com.android.runtime/ld.config.txt', 'apex.libraries.config.txt'}


def reference():
    raw = REFERENCE.read_bytes()
    require(sha(raw) == REFERENCE_SHA, 'Reviewed native namespace preview changed')
    report = json.loads(raw); manifest = native.reviewed()
    require(report['passed'] and report['kind'] == 'hal-linker-host-preview' and
            report['source_rpm'] == manifest['source_rpm'], 'Unexpected native host preview')
    require(report['native_runtime_profile']['runtime_manifest'] == manifest['runtime_manifest'],
            'Runtime profile differs from reviewed native build')
    strict = preview.strict_diagnostic(report['strict_diagnostic'])
    require(not strict['passed'] and len(strict['first_missing_requirements']) == 3 and
            {x['library'] for x in strict['first_missing_requirements']} == {'libneuralnetworks.so'},
            'Strict diagnostic differs from the reviewed limited NNAPI blocker')
    audit = report['hal_elf_dependencies']
    require(audit['elf_files'] == len(audit['files']) == 245 and
            set(audit['service_flat_closures']) == {'bin/' + n for n in ('servicemanager', 'hwservicemanager', 'logd')},
            'Unexpected HAL ELF inventory')
    for name, item in audit['files'].items():
        require(manifest['hal_tree']['regular_file_hashes'].get(name) == item['sha256'] and
                not {'libdexfile.so', 'libneuralnetworks.so'}.intersection(item['needed']),
                'HAL has changed or needs an unresolved provider: ' + name)
    require(all(not x['not_found_in_regular_flat_hal'] for x in audit['service_flat_closures'].values()),
            'Service flat closure is incomplete')
    require(set(report['generated']['files']) == CONFIG_FILES, 'Unexpected linker configuration inventory')
    for name, pin in report['generated']['files'].items():
        data = pin['text'].encode()
        require((len(data), sha(data)) == (pin['bytes'], pin['sha256']), 'Config text hash mismatch: ' + name)
    inputs = json.loads((ROOT / 'validation/linker-inputs-user-20261026.json').read_text())
    vndk = report['apexes']['com.android.vndk.v30']['payload']
    require(all(vndk[k] == inputs['vndk_apex']['payload'][k] for k in ('bytes', 'sha256')), 'VNDK payload differs')
    runtime = report['apexes']['com.android.runtime']['payload']
    require(all(runtime[k] == manifest['payload'][k] for k in ('bytes', 'sha256')), 'Runtime payload differs')
    return report, manifest


def render(report, manifest):
    script = (ROOT / 'tools/native-namespace-probe.sh.in').read_text()
    core = '\n'.join('{} {} {}'.format(n, p['bytes'], p['sha256'])
                     for n, p in manifest['verified_core_reference'].items())
    config = '\n'.join('{} {} {}'.format(n, p['bytes'], p['sha256'])
                       for n, p in sorted(report['generated']['files'].items()))
    vndk = report['apexes']['com.android.vndk.v30']['payload']
    for key, value in {'@PROFILE_SHA@': REFERENCE_SHA, '@PAYLOAD_SHA@': manifest['payload']['sha256'],
                       '@PAYLOAD_BYTES@': manifest['payload']['bytes'], '@HAL_SHA@': manifest['hal_archive_sha256'],
                       '@HAL_BYTES@': manifest['hal_archive_bytes'], '@VNDK_SHA@': vndk['sha256'],
                       '@VNDK_BYTES@': vndk['bytes'], '@FILE_CHECKS@': core, '@CONFIG_CHECKS@': config}.items():
        script = script.replace(key, str(value))
    require(not re.search(r'@[A-Z_]+@', script), 'Unresolved namespace probe template token')
    return script


def bundle_manifest(report, manifest):
    return {'schema': 1, 'kind': 'temporary-generated-namespace-probe',
            'source_preview_sha256': REFERENCE_SHA, 'source_rpm': manifest['source_rpm'],
            'payload': manifest['payload'],
            'vndk_payload': {k: report['apexes']['com.android.vndk.v30']['payload'][k] for k in ('bytes', 'sha256')},
            'hal_archive_bytes': manifest['hal_archive_bytes'], 'hal_archive_sha256': manifest['hal_archive_sha256'],
            'configuration_files': {n: {k: p[k] for k in ('bytes', 'sha256')}
                                    for n, p in report['generated']['files'].items()},
            'script_sha256': sha(render(report, manifest).encode()),
            'source_strict_diagnostic_passed': False, 'nnapi_requirement_resolved': False,
            'device_tested': False, 'persistent_installation_performed': False,
            'systemd_started': False, 'android_hal_started': False, 'flashable_release': False}


def verify_bundle(directory, report, manifest):
    native.verify_data(directory, manifest)
    bundle = bundle_manifest(report, manifest)
    p = bundle['vndk_payload']; native.verify_file(directory / 'vndk_payload.img', p['bytes'], p['sha256'])
    for name, p in bundle['configuration_files'].items():
        native.verify_file(directory / 'linkerconfig' / name, p['bytes'], p['sha256'])
    script = render(report, manifest).encode()
    native.verify_file(directory / 'namespace-probe.sh', len(script), sha(script))
    require(json.loads((directory / 'manifest.json').read_text()) == bundle, 'Namespace probe manifest changed')
    return bundle


def prepare(preview_dir, hal_dir, output):
    report, manifest = reference()
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new namespace probe directory')
    native.verify_file(preview_dir / 'report.json', REFERENCE.stat().st_size, REFERENCE_SHA)
    preview.load_hal_bundle(hal_dir, native_rpm=True)
    payloads = preview_dir / 'payloads'
    for key in ('com.android.runtime', 'com.android.vndk.v30'):
        preview.checked_file(payloads / (key + '.img'), report['apexes'][key]['payload'])
    for name, pin in report['generated']['files'].items():
        preview.checked_file(preview_dir / 'generated' / name, pin)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.namespace-probe-', dir=str(output.parent)))
    try:
        for name in ('apex_payload.img', 'hal-system.tar.gz'):
            shutil.copyfile(hal_dir / name, staging / name)
        shutil.copyfile(payloads / 'com.android.vndk.v30.img', staging / 'vndk_payload.img')
        for name, item in report['generated']['files'].items():
            path = staging / 'linkerconfig' / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(item['text'].encode())
        preview.inspect_generated(staging / 'linkerconfig', preview_dir / 'root')
        script = render(report, manifest)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        (staging / 'namespace-probe.sh').write_text(script); (staging / 'namespace-probe.sh').chmod(0o755)
        bundle = bundle_manifest(report, manifest)
        (staging / 'manifest.json').write_text(json.dumps(bundle, indent=2) + '\n')
        verify_bundle(staging, report, manifest)
        staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Prepared limited generated-namespace RAM probe: ' + str(output))
    print('namespace-probe.sh SHA-256: ' + bundle['script_sha256'])
    return bundle


def classify(log, report, manifest):
    required = ['N100_NAMESPACE_PROFILE ' + REFERENCE_SHA,
                'N100_NAMESPACE_MODE generated-config-no-ld-library-path', 'N100_NAMESPACE_CONFIG_READY',
                'Verified payload SHA-256: ' + manifest['payload']['sha256'],
                'Verified VNDK payload SHA-256: ' + report['apexes']['com.android.vndk.v30']['payload']['sha256'],
                'Verified every regular file in the selected HAL RPM system tree.',
                'N100_LINKER_HELP_EXIT=0', 'N100_LINKERCONFIG_HELP_EXIT=0',
                'N100_NAMESPACE_PROBE_RESULT exit=0 cleanup_failed=0']
    required += ['Verified runtime core: ' + n for n in manifest['verified_core_reference']]
    required += ['Verified linker config: ' + n for n in report['generated']['files']]
    required += ['N100_NAMESPACE_DEPENDENCY_EXIT /system/bin/' + n + ' 0'
                 for n in ('servicemanager', 'hwservicemanager', 'logd')]
    lines = log.splitlines()
    missing = [line for line in required if lines.count(line) != 1]
    prefixes = ('N100_NAMESPACE_', 'N100_LINKER_HELP_EXIT=', 'N100_LINKERCONFIG_HELP_EXIT=')
    unexpected = [line for line in lines if line.startswith(prefixes) and line not in required]
    pattern = r'(?i)(failed to find generated linker configuration|linker:\s*(?:warning|error)|warning:\s*linker|cannot link executable|not accessible for the namespace|unknown namespace|^error:|^STOP:)'
    errors = [line for line in lines if re.search(pattern, line)]
    return {'generated_config_dependency_probe_passed': not missing and not unexpected and not errors,
            'missing_or_duplicate_markers': missing, 'unexpected_status_markers': unexpected,
            'loader_error_or_warning_lines': errors,
            'scope': 'Three AArch64 system service dependency lists with generated config and no LD_LIBRARY_PATH; no service main, vendor or systemd test.'}


def collect(bundle_dir, log_path, output):
    report, manifest = reference(); bundle = verify_bundle(bundle_dir, report, manifest)
    require(log_path.is_file() and 0 < log_path.stat().st_size <= 1024 * 1024, 'Expected a bounded namespace device log')
    log = log_path.read_text(errors='replace')
    result = {'schema': 1, 'kind': 'generated-namespace-device-probe-report', 'bundle': bundle,
              'bundle_integrity_rechecked': True, 'device_probe_log': log,
              'assessment': classify(log, report, manifest), 'source_strict_diagnostic_passed': False,
              'nnapi_requirement_resolved': False, 'vendor_namespace_tested': False,
              'rootfs_boot_tested': False, 'android_services_started': False, 'flashable_release': False}
    with output.open('x') as stream: stream.write(json.dumps(result, indent=2) + '\n')
    print('Report: ' + str(output))
    print('Generated-config dependency probe passed: ' + str(result['assessment']['generated_config_dependency_probe_passed']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--preview-dir', type=Path, required=True)
    prep.add_argument('--hal-bundle', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    capture = commands.add_parser('report')
    capture.add_argument('--bundle-dir', type=Path, required=True)
    capture.add_argument('--probe-log', type=Path, required=True)
    capture.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'prepare': prepare(args.preview_dir, args.hal_bundle, args.output_dir.absolute())
        else: collect(args.bundle_dir, args.probe_log, args.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Namespace RAM probe preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__': main()
