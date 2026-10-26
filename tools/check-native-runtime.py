#!/usr/bin/env python3
"""Inspect a newly built native Runtime APEX; never reuse old package trust implicitly."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_preview', ROOT / 'tools/generate-linker-preview.py')
preview = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(preview)
SPEC = importlib.util.spec_from_file_location('old_runtime', ROOT / 'tools/prepare-runtime-probe.py')
reference = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(reference)
SPEC = importlib.util.spec_from_file_location('native_unwind', ROOT / 'tools/prepare-native-unwind.py')
prepare = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(prepare)


def inspect(android, hal_report=None):
    audit = json.loads((android / '.n100-native-unwind.json').read_text())
    preview.require(audit['passed'] and audit['kind'] == 'native-android-no-dex-unwind', 'Native unwind adaptation missing')
    for path, kind, name, old, new in prepare.EDITS:
        source = android / path
        _, state = prepare.transform(source.read_text(), kind, name, old, new)
        preview.require(state == 'already-applied', 'Native unwind adaptation not applied')
        match = next(x for x in audit['edits'] if x['source'] == path)
        preview.checked_file(source, match['after'])
    preview.checked_file(android / prepare.TLS, audit['protected_tls_header'])
    package = android / 'out/target/product/billie2/system/apex/com.android.runtime.apex'
    preview.require(package.is_file() and package.stat().st_size <= 512 * 1024 * 1024, 'Missing or oversized Runtime APEX')
    package_pin = preview.inputs.file_info(package)
    with zipfile.ZipFile(package) as archive:
        preview.require(archive.namelist().count('apex_manifest.pb') == 1 and
                        archive.getinfo('apex_manifest.pb').file_size <= 65536, 'Invalid Runtime APEX manifest')
        manifest = preview.inputs.manifest_info(archive.read('apex_manifest.pb'), 'com.android.runtime', include_libraries=True)
        preview.require('libdexfile.so' not in manifest['require_native_libs'], 'Runtime still requires libdexfile.so; do not proceed')
        preview.require(archive.namelist().count('apex_payload.img') == 1 and
                        2048 <= archive.getinfo('apex_payload.img').file_size <= 512 * 1024 * 1024, 'Invalid Runtime payload size')
        debugfs = shutil.which('debugfs'); preview.require(debugfs, 'debugfs required')
        with tempfile.TemporaryDirectory(prefix='n100-native-runtime-') as temp:
            root = Path(temp); payload = root / 'source.img'
            with archive.open('apex_payload.img') as stream, payload.open('xb') as target:
                shutil.copyfileobj(stream, target, 1024 * 1024)
            payload_pin = preview.inputs.file_info(payload); payload.unlink()
            view = preview.stage_apex(package, package_pin, payload_pin, 'com.android.runtime', root / 'root', root, debugfs)
            image = Path(view['payload']['path'])
            core = reference.verify_core(image, root)
    preview.checked_file(package, package_pin)
    revisions = {}
    for project in ('bionic', 'system/core', 'system/unwinding'):
        revisions[project] = subprocess.run(['git', '-C', str(android / project), 'rev-parse', 'HEAD'],
            capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    for path, kind, name, old, new in prepare.EDITS:
        preview.checked_file(android / path, next(x['after'] for x in audit['edits'] if x['source'] == path))
    preview.checked_file(android / prepare.TLS, audit['protected_tls_header'])
    result = {'schema': 1, 'kind': 'native-runtime-no-dex-build', 'passed': True,
            'apex': package_pin, 'payload': {k: v for k, v in payload_pin.items() if k != 'path'},
            'manifest': manifest, 'metadata': {n: {k: v for k, v in x.items() if k != 'path'} for n, x in view['metadata'].items()},
            'core_files': core, 'previously_tested_core_hashes_unchanged': all(x['matches_flattened_reference'] for x in core.values()),
            'source_revisions': revisions, 'source_adaptation': audit,
            'dex_unwinding_disabled': True, 'native_unwinding_retained': True, 'art_package_requested': False,
            'nnapi_requirement_resolved': False, 'rpm_rebuilt': False, 'old_probe_bundles_automatically_updated': False,
            'device_tested': False, 'phone_modified': False, 'ready_for_installation': False,
            'full_os_boot_verified': False}
    if hal_report is not None:
        hal = json.loads(hal_report.read_text())
        preview.require(hal.get('kind') == 'hal-runtime-build-outputs' and hal.get('passed') is True,
                        'HAL build outputs did not pass')
        product = android / 'out/target/product/billie2'
        for name, record in hal['files'].items():
            path = product / name
            preview.require(product.resolve() in path.resolve().parents, 'HAL report path escapes product output')
            preview.checked_file(path, record)
        preview.require(hal['files']['system/apex/com.android.runtime.apex']['sha256'] == package_pin['sha256'],
                        'HAL report and inspected Runtime package differ')
        result['hal_build_outputs'] = hal
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--hal-report', required=True, type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.android_root.resolve(), args.hal_report)
    except (OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps({'passed': False, 'errors': [str(exc)]}, indent=2) + '\n')
        parser.exit(1, 'Native Runtime check failed: {}\n'.format(exc))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print('Native Runtime report: ' + str(args.output))


if __name__ == '__main__': main()
