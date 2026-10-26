#!/usr/bin/env python3
"""Prepare a pinned runtime APEX probe bundle, or combine its device report."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zipfile


APEX_BYTES = 8187904
APEX_SHA = '293e90ce25eec01a38b723731eea03d378750e7ee0287df93305d43073e8bdf0'
PAYLOAD_BYTES = 8110080
PINS = {
    'bin/linker': (1311204, 'b4cc0d3534db2a18ec0828f394657a0aad773137c0127c3ebbe142a65ef16ae2', 1, 40),
    'bin/linker64': (1790304, '7edbd7684bba7a53fc5d666ed23fe3f7927adc11ca060d4b079e7f73918f56cc', 2, 183),
    'lib64/bionic/libc.so': (1020008, 'd109ef03d686bd6b549a602f9ed5d774af2fdccc312f178d2f42b188787a6d3a', 2, 183),
    'lib64/bionic/libdl.so': (13728, '316c59f9624362bd59f1fb2be6c15bfd32e8d5f47aac3356075811c4bf667e03', 2, 183),
    'lib64/bionic/libm.so': (225176, 'd5348e97648de62112f1625a70e87a93771ba641d1ee0c3f61f47e0858dec1e8', 2, 183),
}
ROOT = Path(__file__).resolve().parents[1]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def read_payload(source):
    require(source.is_file() and source.stat().st_size == APEX_BYTES, 'Unexpected runtime APEX size')
    data = source.read_bytes()
    require(digest(data) == APEX_SHA, 'Runtime APEX SHA-256 changed; inspect the new build before using it')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        require(archive.namelist().count('apex_payload.img') == 1, 'Expected exactly one apex_payload.img')
        info = archive.getinfo('apex_payload.img')
        require(info.file_size == PAYLOAD_BYTES, 'Unexpected APEX payload length')
        payload = archive.read(info)
    require(payload[1080:1082] == b'\x53\xef', 'Payload is not the expected raw ext4 filesystem')
    return payload


def verify_core(image, directory):
    tool = shutil.which('debugfs')
    require(tool, 'debugfs is required; install the host e2fsprogs package')
    result = {}
    for index, (name, (reference_size, reference_sha, elf_class, machine)) in enumerate(PINS.items()):
        target = directory / ('core-{}'.format(index))
        # debugfs has its own command parser. Use only fixed source names and
        # quote the host-generated destination; never invoke a shell.
        quoted = str(target).replace('\\', '\\\\').replace('"', '\\"')
        require(not any(c in quoted for c in '\n\r\0'), 'Invalid staging path')
        proc = subprocess.run([tool, '-R', 'dump /{} "{}"'.format(name, quoted), str(image)],
                              capture_output=True, text=True, timeout=30)
        require(proc.returncode == 0 and target.is_file(), 'debugfs could not read ' + name)
        require((64 if elf_class == 2 else 52) <= target.stat().st_size <= PAYLOAD_BYTES,
                'Invalid extracted ELF size: ' + name)
        data = target.read_bytes()
        require(data[:4] == b'\x7fELF' and data[4] == elf_class and data[5] == 1 and
                struct.unpack_from('<H', data, 18)[0] == machine and
                struct.unpack_from('<H', data, 16)[0] in (2, 3), 'Wrong ELF ABI: ' + name)
        result[name] = {'bytes': len(data), 'sha256': digest(data), 'elf_class': elf_class,
                        'elf_machine': machine,
                        'matches_flattened_reference': (len(data), digest(data)) == (reference_size, reference_sha),
                        'flattened_reference_bytes': reference_size,
                        'flattened_reference_sha256': reference_sha}
        target.unlink()
    return result


def prepare(android_root, output):
    require(not output.exists() and not output.is_symlink(), 'Output directory exists; use a new directory')
    source = android_root / 'out/target/product/billie2/system/apex/com.android.runtime.apex'
    payload = read_payload(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.runtime-probe-', dir=str(output.parent)))
    try:
        image = staging / 'apex_payload.img'
        image.write_bytes(payload)
        verified = verify_core(image, staging)
        checks = '\n'.join('{} {} {}'.format(name, values['bytes'], values['sha256']) for name, values in verified.items())
        script = (ROOT / 'tools/runtime-probe.sh.in').read_text()
        script = script.replace('@PAYLOAD_SHA@', digest(payload)).replace('@PAYLOAD_BYTES@', str(len(payload))).replace('@FILE_CHECKS@', checks)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        (staging / 'runtime-probe.sh').write_text(script)
        (staging / 'runtime-probe.sh').chmod(0o755)
        manifest = {'schema': 1, 'kind': 'temporary-runtime-apex-probe',
                    'probe_version': 2,
                    'source_apex_sha256': APEX_SHA, 'payload_bytes': len(payload),
                    'payload_sha256': digest(payload), 'script_sha256': digest(script.encode()),
                    'verified_core_files': verified, 'oem_signature_verified': False,
                    'device_tested': False, 'persistent_installation_performed': False,
                    'systemd_started': False, 'android_hal_started': False, 'flashable_release': False}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print('Verified pinned APEX package, payload and {} core ELF files.'.format(len(PINS)))
    print('Bundle: {}'.format(output))


def collect(bundle, log, output, android_root=None):
    require(not output.exists(), 'Report exists; use a new filename')
    manifest = json.loads((bundle / 'manifest.json').read_text())
    require(manifest['source_apex_sha256'] == APEX_SHA, 'Unexpected source APEX pin')
    for name, size, expected in [('apex_payload.img', PAYLOAD_BYTES, manifest['payload_sha256']),
                                  ('runtime-probe.sh', None, manifest['script_sha256'])]:
        path = bundle / name
        require(path.is_file() and (size is None or path.stat().st_size == size), 'Bundle file missing/changed: ' + name)
        require(digest(path.read_bytes()) == expected, 'Bundle integrity mismatch: ' + name)
    require(log.is_file() and log.stat().st_size <= 1024 * 1024, 'Expected a bounded device log')
    result = {'schema': 1, 'kind': 'runtime-apex-device-probe-report', 'bundle': manifest,
              'device_probe_log': log.read_text(errors='replace'), 'bundle_integrity_rechecked': True,
              'scope': 'Device probe result is unclassified terminal output; no full OS/HAL success inferred.'}
    if android_root is not None:
        spec = importlib.util.spec_from_file_location('n100_android_inventory', ROOT / 'tools/inspect-android-output.py')
        inspector = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inspector)
        result['android_output_inventory'] = inspector.inspect(android_root)
    with output.open('x') as stream:
        stream.write(json.dumps(result, indent=2) + '\n')
    print('Report: {}'.format(output))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--android-root', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    report = commands.add_parser('report')
    report.add_argument('--bundle-dir', type=Path, required=True)
    report.add_argument('--probe-log', type=Path, required=True)
    report.add_argument('--output', type=Path, required=True)
    report.add_argument('--android-root', type=Path, help='Attach current HAL file, symlink and ELF-interpreter inventory')
    args = parser.parse_args()
    try:
        if args.mode == 'prepare':
            prepare(args.android_root, args.output_dir)
        else:
            collect(args.bundle_dir, args.probe_log, args.output, args.android_root)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Runtime probe preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
