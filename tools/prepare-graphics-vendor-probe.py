#!/usr/bin/env python3
"""Prepare vendor probe V3: the device-tested V2 view plus the hashed libGLESv3 delta."""
import argparse
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('graphics_vendor_v2', ROOT / 'tools/prepare-vendor-probe.py')
vp = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(vp)
require = vp.require
sha = vp.sha

GRAPHICS_REPORT = ROOT / 'validation/graphics-runtime-build-user-20261111.json'
GRAPHICS_REPORT_SHA = '41cde56a39704d60e1ddd30e9f40878558dcfab2de9c568464818bb62a01424b'
V2_DEVICE_REPORT_SHA = '2824601b4c79fab7715d7978c5f3c1b3fd38eba8527de440b2143b646d66ff19'
V2_SCRIPT_SHA = '1aff4719a18e0d6294aceb2d683fb505f5c4c1b0a2cbb267b7553ecfb8a37df6'
DELTA = 'graphics-runtime.tar.gz'
SCRIPT = 'vendor-probe-v3.sh'
LIBRARY = 'lib64/libGLESv3.so'
RESOLVED = re.compile(r'^\s*libGLESv3\.so => /system/lib64/libGLESv3\.so \(0x[0-9a-f]+\)\s*$')
COMPOSER = '/vendor/bin/hw/vendor.qti.hardware.display.composer-service'


def graphics():
    raw = GRAPHICS_REPORT.read_bytes()
    require(sha(raw) == GRAPHICS_REPORT_SHA, 'Reviewed graphics runtime report changed')
    report = json.loads(raw)
    require(report['kind'] == 'android-graphics-runtime-build' and report['passed'] is True and
            report['dependency_closure_complete'] is True and report['runtime_apex_matches_tested_build'] is True and
            report['bionic_matches_tested_build'] is True and
            report['source_vendor_report_sha256'] == V2_DEVICE_REPORT_SHA and
            sha((ROOT / 'validation/vendor-v2-device-report-user-20261026.json').read_bytes()) == V2_DEVICE_REPORT_SHA,
            'Graphics runtime build did not pass or is not bound to the V2 device report')
    require(report['new_library_names'] == ['libGLESv3.so'] and report['changed_library_names'] == [] and
            report['staged_paths'] == [LIBRARY], 'Unexpected graphics delta scope')
    lib = report['files']['libGLESv3.so']
    return {'archive_bytes': report['archive']['bytes'], 'archive_sha256': report['archive']['sha256'],
            'library_bytes': lib['bytes'], 'library_sha256': lib['sha256'],
            'checksum_line': '{}  {}'.format(lib['sha256'], LIBRARY), 'report_sha256': GRAPHICS_REPORT_SHA}


def verify_delta(path, pins):
    data = path.read_bytes()
    require(len(data) == pins['archive_bytes'] and sha(data) == pins['archive_sha256'], 'Graphics delta archive changed')
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = {m.name: m for m in archive.getmembers()}
        require(set(members) == {'n100-graphics-files.sha256', LIBRARY}, 'Unexpected graphics delta members')
        require(all(m.isreg() for m in members.values()), 'Graphics delta contains non-regular entries')
        listing = archive.extractfile('n100-graphics-files.sha256').read().decode()
        library = archive.extractfile(LIBRARY).read()
    require(listing == pins['checksum_line'] + '\n', 'Graphics checksum listing differs')
    require(len(library) == pins['library_bytes'] and sha(library) == pins['library_sha256'], 'libGLESv3 differs')
    require(library[:5] == b'\x7fELF\x02' and int.from_bytes(library[18:20], 'little') == 183, 'libGLESv3 is not AArch64')


def replace_once(text, old, new):
    require(text.count(old) == 1, 'V2 script anchor not unique: ' + old.splitlines()[0])
    return text.replace(old, new)


def render(report, manifest, pins):
    script = vp.render(report, manifest)
    require(sha(script.encode()) == V2_SCRIPT_SHA, 'Device-tested V2 script changed')
    script = replace_once(script, "# MIT. Render with prepare-vendor-probe.py; temporary RAM view only.\n",
        "# MIT. Rendered by prepare-graphics-vendor-probe.py from the device-tested V2 probe.\n"
        "# V3 adds only the hashed libGLESv3 delta to the temporary RAM copy of the HAL tree.\n")
    script = replace_once(script, "VNDK_BYTES={}\n".format(report['apexes']['com.android.vndk.v30']['payload']['bytes']),
        "VNDK_BYTES={}\nGFX_SHA='{}'\nGFX_BYTES={}\nGLES_SHA='{}'\nGLES_BYTES={}\n".format(
            report['apexes']['com.android.vndk.v30']['payload']['bytes'], pins['archive_sha256'],
            pins['archive_bytes'], pins['library_sha256'], pins['library_bytes']))
    script = replace_once(script, "Usage: vendor-probe.sh --plan", "Usage: vendor-probe-v3.sh --plan")
    script = replace_once(script, "    if [ \"$failed\" = 0 ]; then\n        rm -rf \"$work/hal\"\n",
        "    if [ \"$failed\" = 0 ]; then\n        rm -rf \"$work/hal\" \"$work/graphics\"\n"
        "        rm -f \"$work/graphics-runtime.tar.gz\"\n")
    script = replace_once(script, "echo 'N100_VENDOR_PROBE_VERSION 2'\n",
        "echo 'N100_VENDOR_PROBE_VERSION 3'\necho \"N100_VENDOR_GRAPHICS_DELTA $GFX_SHA\"\n")
    script = replace_once(script, "echo 'Verified every regular file in the selected HAL RPM system tree.'\n",
        "echo 'Verified every regular file in the selected HAL RPM system tree.'\n" + r'''
echo '=== Download, verify and overlay the libGLESv3 delta (RAM copy only) ==='
wget -O "$work/graphics-runtime.tar.gz" http://192.168.2.14:8081/graphics-runtime.tar.gz
[ "$(wc -c < "$work/graphics-runtime.tar.gz")" -eq "$GFX_BYTES" ] || die 'Graphics delta size mismatch'
actual=$(sha256sum "$work/graphics-runtime.tar.gz"); actual=${actual%% *}
[ "$actual" = "$GFX_SHA" ] || die 'Graphics delta hash mismatch'
mkdir "$work/graphics"
tar -xzf "$work/graphics-runtime.tar.gz" -C "$work/graphics"
for entry in "$work/graphics"/* "$work/graphics"/.[!.]* "$work/graphics"/..?* \
             "$work/graphics/lib64"/* "$work/graphics/lib64"/.[!.]* "$work/graphics/lib64"/..?*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    case "${entry#"$work/graphics/"}" in
        lib64|lib64/libGLESv3.so|n100-graphics-files.sha256) ;;
        *) die "Unexpected graphics delta entry: ${entry#"$work/graphics/"}" ;;
    esac
done
[ -d "$work/graphics/lib64" ] && [ ! -L "$work/graphics/lib64" ] || die 'Graphics lib64 is not a directory'
for entry in "$work/graphics/n100-graphics-files.sha256" "$work/graphics/lib64/libGLESv3.so"; do
    [ -f "$entry" ] && [ ! -L "$entry" ] || die 'Graphics delta file is not regular'
done
[ "$(cat "$work/graphics/n100-graphics-files.sha256")" = "$GLES_SHA  lib64/libGLESv3.so" ] || die 'Graphics checksum list differs'
if ! (cd "$work/graphics" && sha256sum -c n100-graphics-files.sha256) > "$work/graphics-check.log" 2>&1; then
    cat "$work/graphics-check.log"
    die 'Graphics delta file hash verification failed'
fi
[ -d "$work/hal/lib64" ] && [ ! -L "$work/hal/lib64" ] || die 'HAL lib64 directory missing'
[ ! -e "$work/hal/lib64/libGLESv3.so" ] && [ ! -L "$work/hal/lib64/libGLESv3.so" ] || die 'libGLESv3.so already in HAL tree'
cp "$work/graphics/lib64/libGLESv3.so" "$work/hal/lib64/libGLESv3.so"
chmod 0644 "$work/hal/lib64/libGLESv3.so"
[ "$(wc -c < "$work/hal/lib64/libGLESv3.so")" -eq "$GLES_BYTES" ] || die 'Overlaid libGLESv3 size mismatch'
actual=$(sha256sum "$work/hal/lib64/libGLESv3.so"); actual=${actual%% *}
[ "$actual" = "$GLES_SHA" ] || die 'Overlaid libGLESv3 hash mismatch'
echo "N100_VENDOR_GRAPHICS_OVERLAY_READY $actual"
''')
    script = replace_once(script, '        echo "N100_VENDOR_SKIPPED other-ABI-or-format $path"\n',
        '        echo "N100_VENDOR_SKIPPED other-ABI-or-format $path"\n'
        '        echo "N100_VENDOR_SKIPPED_IDENTITY $path $identity"\n')
    script = replace_once(script, "echo 'Persistent Sailfish /apex and userdata contents remain unchanged.'\n",
        "echo 'Persistent Sailfish /apex and userdata contents remain unchanged.'\n"
        "echo 'libGLESv3.so was added only to the RAM copy of the HAL tree.'\n")
    require(not re.search(r'@[A-Z_]+@', script), 'Unresolved template token')
    return script


def bundle_manifest(report, manifest, pins):
    bundle = vp.bundle_manifest(report, manifest)
    bundle.update(kind='temporary-stock-vendor-graphics-dependency-probe', probe_version=3,
                  source_vendor_v2_script_sha256=V2_SCRIPT_SHA, script=SCRIPT,
                  script_sha256=sha(render(report, manifest, pins).encode()),
                  graphics_delta={'file': DELTA, 'bytes': pins['archive_bytes'], 'sha256': pins['archive_sha256'],
                                  'library': LIBRARY, 'library_bytes': pins['library_bytes'],
                                  'library_sha256': pins['library_sha256'],
                                  'source_report_sha256': pins['report_sha256']},
                  scope='V2 selected AArch64 vendor dependency lists with libGLESv3.so added to the RAM HAL copy; no daemon main')
    return bundle


def verify_bundle(directory, report, manifest, pins):
    bundle = bundle_manifest(report, manifest, pins)
    vp.ns.native.verify_data(directory, manifest)
    pin = bundle['vndk_payload']; vp.ns.native.verify_file(directory / 'vndk_payload.img', pin['bytes'], pin['sha256'])
    for name, pin in bundle['configuration_files'].items():
        vp.ns.native.verify_file(directory / 'linkerconfig' / name, pin['bytes'], pin['sha256'])
    verify_delta(directory / DELTA, pins)
    script = render(report, manifest, pins).encode()
    vp.ns.native.verify_file(directory / SCRIPT, len(script), sha(script))
    require(json.loads((directory / 'manifest.json').read_text()) == bundle, 'V3 probe manifest changed')
    return bundle


def prepare(v2_bundle, delta, output):
    report, manifest = vp.reference(); pins = graphics()
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new V3 probe directory')
    vp.verify_bundle(v2_bundle, report, manifest)
    verify_delta(delta, pins)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.vendor-probe-v3-', dir=str(output.parent)))
    try:
        files = ['apex_payload.img', 'vndk_payload.img', 'hal-system.tar.gz']
        files += ['linkerconfig/' + name for name in report['generated']['files']]
        for name in files:
            target = staging / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(v2_bundle / name, target)
        shutil.copyfile(delta, staging / DELTA)
        script = render(report, manifest, pins)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        (staging / SCRIPT).write_text(script); (staging / SCRIPT).chmod(0o755)
        bundle = bundle_manifest(report, manifest, pins)
        (staging / 'manifest.json').write_text(json.dumps(bundle, indent=2) + '\n')
        verify_bundle(staging, report, manifest, pins)
        staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Prepared vendor probe V3: ' + str(output))
    print(SCRIPT + ' SHA-256: ' + bundle['script_sha256'])
    return bundle


def classify(log, report, manifest, pins):
    lines = log.splitlines()
    delta = 'N100_VENDOR_GRAPHICS_DELTA ' + pins['archive_sha256']
    overlay = 'N100_VENDOR_GRAPHICS_OVERLAY_READY ' + pins['library_sha256']
    missing = [line for line in ('N100_VENDOR_PROBE_VERSION 3', delta, overlay) if lines.count(line) != 1]
    identities = [line for line in lines if line.startswith('N100_VENDOR_SKIPPED_IDENTITY ')]
    graphics_lines = {'N100_VENDOR_PROBE_VERSION 3', delta, overlay}
    # Normalize to the V2 marker set and reuse the reviewed V2 classifier unchanged.
    normalized = '\n'.join('N100_VENDOR_PROBE_VERSION 2' if line == 'N100_VENDOR_PROBE_VERSION 3' else line
                           for line in lines if (line not in graphics_lines or line == 'N100_VENDOR_PROBE_VERSION 3')
                           and not line.startswith('N100_VENDOR_SKIPPED_IDENTITY ')) + '\n'
    base = vp.classify(normalized, report, manifest)
    composer = log.split('N100_VENDOR_SELECTED ' + COMPOSER + ' ', 1)
    section = composer[1].split('N100_VENDOR_DEPENDENCY_EXIT ' + COMPOSER + ' ', 1)[0] if len(composer) == 2 else ''
    resolved = any(RESOLVED.match(line) for line in section.splitlines())
    gles_missing = [line for line in lines if 'libGLESv3.so' in line and 'not found' in line]
    passed = base['selected_vendor_dependency_probe_passed'] and not missing and resolved and not gles_missing
    base.update(selected_vendor_dependency_probe_passed=passed, graphics_markers_missing_or_duplicate=missing,
                composer_resolved_overlaid_libglesv3=resolved, libglesv3_not_found_lines=gles_missing,
                skipped_program_identities=identities,
                scope='V3: selected AArch64 vendor dependency lists with RAM-only libGLESv3; daemon main, '
                      '32-bit services, NNAPI and full vendor compatibility unvalidated.')
    return base


def collect(bundle_dir, log_path, output):
    report, manifest = vp.reference(); pins = graphics()
    bundle = verify_bundle(bundle_dir, report, manifest, pins)
    require(log_path.is_file() and 0 < log_path.stat().st_size <= 2 * 1024 * 1024, 'Expected a bounded probe log')
    log = log_path.read_text(errors='replace')
    result = {'schema': 1, 'kind': 'stock-vendor-graphics-dependency-device-probe-report', 'bundle': bundle,
              'bundle_integrity_rechecked': True, 'device_probe_log': log,
              'assessment': classify(log, report, manifest, pins), 'nnapi_requirement_resolved': False,
              'rootfs_boot_tested': False, 'android_services_started': False, 'flashable_release': False}
    with output.open('x') as stream: stream.write(json.dumps(result, indent=2) + '\n')
    print('Report: ' + str(output))
    print('Selected vendor dependency probe V3 passed: ' + str(result['assessment']['selected_vendor_dependency_probe_passed']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--v2-bundle', type=Path, required=True)
    prep.add_argument('--graphics-archive', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    capture = commands.add_parser('report')
    capture.add_argument('--bundle-dir', type=Path, required=True)
    capture.add_argument('--probe-log', type=Path, required=True)
    capture.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'prepare': prepare(args.v2_bundle, args.graphics_archive, args.output_dir.absolute())
        else: collect(args.bundle_dir, args.probe_log, args.output)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Vendor probe V3 preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__': main()
