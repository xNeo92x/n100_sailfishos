#!/usr/bin/env python3
"""Prepare a RAM-only stock vendor dependency probe after the system namespace test."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / 'validation/namespace-device-report-user-20261026.json'
REFERENCE_SHA = 'b32b453845a33ce80cac0ba3fe97e889de9ee1f52ee314b13e6e27058836be27'
SPEC = importlib.util.spec_from_file_location('vendor_namespace_base', ROOT / 'tools/prepare-namespace-probe.py')
ns = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(ns)
require = ns.require
sha = ns.sha
META_SHA = 'bb42777e434f73ae5e4a9871af64f792a61a832be3852adf7c3d2f8b7012dcc7'
VENDOR_B = {'name': 'vendor_b', 'sectors': 1856272, 'physical_sector': 18888896,
            'source_device': '8:10', 'metadata_bytes': 405504, 'metadata_sha256': META_SHA}
BANNER = '=== Selected stock AArch64 vendor service dependency lists ==='


def reference():
    raw = REFERENCE.read_bytes()
    require(sha(raw) == REFERENCE_SHA, 'Reviewed system namespace device report changed')
    device = json.loads(raw); report, manifest = ns.reference()
    require(device['kind'] == 'generated-namespace-device-probe-report' and
            device['bundle_integrity_rechecked'] is True and device['bundle'] == ns.bundle_manifest(report, manifest) and
            ns.classify(device['device_probe_log'], report, manifest)['generated_config_dependency_probe_passed'],
            'Reviewed system namespace device dependency probe did not pass')
    audit = json.loads((ROOT / 'validation/super-metadata-report-user-20261025.json').read_text())
    require(audit['metadata_region_sha256'] == META_SHA and audit['metadata_region_bytes'] == 405504,
            'Reviewed super snapshot differs')
    parts = [p for p in audit['metadata']['partitions'] if p['effective_name'] == 'vendor_b']
    require(len(parts) == 1 and parts[0]['readonly_attribute'] and len(parts[0]['extents']) == 1,
            'Unexpected vendor logical partition')
    extent = parts[0]['extents'][0]
    require(extent['type'] == 'linear' and extent['block_device_index'] == 0 and
            extent['sectors'] == VENDOR_B['sectors'] and extent['physical_sector'] == VENDOR_B['physical_sector'],
            'Reviewed vendor extent differs')
    return report, manifest


def render(report, manifest):
    script = (ROOT / 'tools/vendor-dependency-probe.sh.in').read_text()
    core = '\n'.join('{} {} {}'.format(n, p['bytes'], p['sha256'])
                     for n, p in manifest['verified_core_reference'].items())
    configs = '\n'.join('{} {} {}'.format(n, p['bytes'], p['sha256'])
                        for n, p in sorted(report['generated']['files'].items()))
    vndk = report['apexes']['com.android.vndk.v30']['payload']
    for token, value in {'@PROFILE_SHA@': REFERENCE_SHA, '@PAYLOAD_SHA@': manifest['payload']['sha256'],
                         '@PAYLOAD_BYTES@': manifest['payload']['bytes'], '@HAL_SHA@': manifest['hal_archive_sha256'],
                         '@HAL_BYTES@': manifest['hal_archive_bytes'], '@VNDK_SHA@': vndk['sha256'],
                         '@VNDK_BYTES@': vndk['bytes'], '@FILE_CHECKS@': core, '@CONFIG_CHECKS@': configs}.items():
        script = script.replace(token, str(value))
    require(not re.search(r'@[A-Z_]+@', script), 'Unresolved vendor probe template token')
    return script


def bundle_manifest(report, manifest):
    bundle = ns.bundle_manifest(report, manifest)
    bundle.update(kind='temporary-stock-vendor-dependency-probe',
                  source_namespace_device_report_sha256=REFERENCE_SHA, source_system_namespace_probe_passed=True,
                  script_sha256=sha(render(report, manifest).encode()), logical_vendor_snapshot=VENDOR_B,
                  scope='Selected regular AArch64 stock composer/allocator/audio/sensors service dependency lists; no daemon main')
    return bundle


def verify_bundle(directory, report, manifest):
    bundle = bundle_manifest(report, manifest)
    ns.native.verify_data(directory, manifest)
    pin = bundle['vndk_payload']; ns.native.verify_file(directory / 'vndk_payload.img', pin['bytes'], pin['sha256'])
    for name, pin in bundle['configuration_files'].items():
        ns.native.verify_file(directory / 'linkerconfig' / name, pin['bytes'], pin['sha256'])
    script = render(report, manifest).encode()
    ns.native.verify_file(directory / 'vendor-probe.sh', len(script), sha(script))
    require(json.loads((directory / 'manifest.json').read_text()) == bundle, 'Vendor probe manifest changed')
    return bundle


def prepare(source, output):
    report, manifest = reference()
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new vendor probe directory')
    ns.verify_bundle(source, report, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.vendor-probe-', dir=str(output.parent)))
    try:
        files = ['apex_payload.img', 'vndk_payload.img', 'hal-system.tar.gz']
        files += ['linkerconfig/' + name for name in report['generated']['files']]
        for name in files:
            target = staging / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, target)
        script = render(report, manifest)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        (staging / 'vendor-probe.sh').write_text(script); (staging / 'vendor-probe.sh').chmod(0o755)
        bundle = bundle_manifest(report, manifest)
        (staging / 'manifest.json').write_text(json.dumps(bundle, indent=2) + '\n')
        verify_bundle(staging, report, manifest)
        staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Prepared stock vendor RAM dependency probe: ' + str(output))
    print('vendor-probe.sh SHA-256: ' + bundle['script_sha256'])
    return bundle


def classify(log, report, manifest):
    lines = log.splitlines()
    markers = ['N100_VENDOR_PROFILE ' + REFERENCE_SHA,
               'N100_VENDOR_MODE generated-config-stock-vendor-no-ld-library-path',
               'N100_VENDOR_METADATA_SHA256 ' + META_SHA,
               'N100_VENDOR_FINGERPRINT_VERIFIED', 'N100_VENDOR_VIEW_READY', 'N100_VENDOR_PREFLIGHT_RECORDED',
               'N100_VENDOR_PROBE_RESULT exit=0 cleanup_failed=0']
    missing = [line for line in markers if lines.count(line) != 1]
    if lines.count(BANNER) != 1: missing.append('exactly one vendor dependency section')
    mapping = [line for line in lines if line.startswith('N100_VENDOR_MAPPING_READONLY ')]
    if len(mapping) != 1 or not re.fullmatch(r'N100_VENDOR_MAPPING_READONLY /dev/dm-[0-9]+', mapping[0]):
        missing.append('exactly one read-only vendor mapping marker')
    selected, exits, skipped, counts, malformed = {}, {}, [], [], []
    for line in lines:
        if line.startswith('N100_VENDOR_SELECTED '):
            match = re.fullmatch(r'N100_VENDOR_SELECTED (/vendor/bin/hw/[A-Za-z0-9_.@+-]+) ([0-9a-f]{64})', line)
            if not match or match[1] in selected: malformed.append(line)
            else: selected[match[1]] = match[2]
        elif line.startswith('N100_VENDOR_DEPENDENCY_EXIT '):
            match = re.fullmatch(r'N100_VENDOR_DEPENDENCY_EXIT (/vendor/bin/hw/[A-Za-z0-9_.@+-]+) ([0-9]{1,3})', line)
            if not match or match[1] in exits: malformed.append(line)
            else: exits[match[1]] = int(match[2])
        elif line.startswith('N100_VENDOR_PROGRAM_COUNT '): counts.append(line)
        elif line.startswith('N100_VENDOR_SKIPPED '): skipped.append(line)
        elif line.startswith('N100_VENDOR_') and line not in markers and line not in mapping: malformed.append(line)
    count_ok = counts == ['N100_VENDOR_PROGRAM_COUNT ' + str(len(selected))] and 1 <= len(selected) <= 24
    inventory_ok = count_ok and set(selected) == set(exits)
    normalized = log.replace(markers[0], 'N100_NAMESPACE_PROFILE ' + ns.REFERENCE_SHA).replace(
        markers[1], 'N100_NAMESPACE_MODE generated-config-no-ld-library-path').replace(
        'N100_VENDOR_PROBE_RESULT ', 'N100_NAMESPACE_PROBE_RESULT ')
    # Classify the repeated system test separately from the later vendor failures.
    core = normalized.split(BANNER)[0]
    if BANNER in normalized:
        core += '\n' + '\n'.join(line for line in normalized.splitlines() if line.startswith('N100_NAMESPACE_PROBE_RESULT '))
    base = ns.classify(core, report, manifest)
    warnings = ns.classify(normalized, report, manifest)['loader_error_or_warning_lines']
    passed = (base['generated_config_dependency_probe_passed'] and not missing and not malformed and
              inventory_ok and all(code == 0 for code in exits.values()) and not warnings)
    return {'selected_vendor_dependency_probe_passed': passed,
            'system_service_dependency_probe_passed': base['generated_config_dependency_probe_passed'],
            'selected_program_sha256': selected, 'vendor_dependency_exit_codes': exits, 'skipped_programs': skipped,
            'inventory_consistent': inventory_ok, 'missing_or_duplicate_markers': missing,
            'malformed_or_unexpected_markers': malformed, 'loader_error_or_warning_lines': warnings,
            'scope': 'Selected AArch64 vendor dependency lists only; 32-bit services, daemon main, NNAPI and full vendor compatibility unvalidated.'}


def collect(bundle_dir, log_path, output):
    report, manifest = reference(); bundle = verify_bundle(bundle_dir, report, manifest)
    require(log_path.is_file() and 0 < log_path.stat().st_size <= 2 * 1024 * 1024, 'Expected a bounded vendor probe log')
    log = log_path.read_text(errors='replace')
    result = {'schema': 1, 'kind': 'stock-vendor-dependency-device-probe-report', 'bundle': bundle,
              'bundle_integrity_rechecked': True, 'device_probe_log': log, 'assessment': classify(log, report, manifest),
              'nnapi_requirement_resolved': False, 'rootfs_boot_tested': False,
              'android_services_started': False, 'flashable_release': False}
    with output.open('x') as stream: stream.write(json.dumps(result, indent=2) + '\n')
    print('Report: ' + str(output))
    print('Selected vendor dependency probe passed: ' + str(result['assessment']['selected_vendor_dependency_probe_passed']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--namespace-bundle', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    capture = commands.add_parser('report')
    capture.add_argument('--bundle-dir', type=Path, required=True)
    capture.add_argument('--probe-log', type=Path, required=True)
    capture.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'prepare': prepare(args.namespace_bundle, args.output_dir.absolute())
        else: collect(args.bundle_dir, args.probe_log, args.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Stock vendor probe preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__': main()
