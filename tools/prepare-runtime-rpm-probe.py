#!/usr/bin/env python3
"""Prepare a RAM-only probe from the exact locally built HAL RPM; run no RPM scripts."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import shutil
import stat
import subprocess
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('runtime_base', ROOT / 'tools/prepare-runtime-probe.py')
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
PREFIX = 'usr/libexec/droid-hybris/system/'
LIMIT = 512 * 1024 * 1024
TREE_LIMIT = 384 * 1024 * 1024
PAYLOAD_SHA = '52b434e4197f95bbc741b5657f1583186b40c2bc2b9448c6f978865c106974b9'


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def cpio_tree(raw):
    """Parse bounded newc/CRC CPIO, expanding regular hardlinks without extraction."""
    require(len(raw) <= LIMIT, 'CPIO archive exceeds limit')
    selected, groups = {}, {}
    offset = 0
    for _ in range(20000):
        header = raw[offset:offset + 110]
        require(len(header) == 110 and header[:6] in (b'070701', b'070702'), 'Invalid CPIO header')
        values = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
        inode, mode, _, _, links, _, size, major, minor, _, _, namesize, checksum = values
        offset += 110
        require(1 <= namesize <= 4096 and offset + namesize <= len(raw), 'Invalid CPIO name size')
        name_bytes = raw[offset:offset + namesize]
        require(name_bytes.endswith(b'\0'), 'Unterminated CPIO name')
        name = name_bytes.rstrip(b'\0').decode('utf-8')
        require('\0' not in name, 'Embedded NUL in CPIO name')
        offset = (offset + namesize + 3) // 4 * 4
        require(size <= 128 * 1024 * 1024 and offset + size <= len(raw), 'Invalid CPIO file size')
        content = memoryview(raw)[offset:offset + size]
        offset = (offset + size + 3) // 4 * 4
        if header[:6] == b'070702':
            require(sum(content) & 0xffffffff == checksum, 'Invalid CPIO checksum')
        if name == 'TRAILER!!!':
            require(size == 0 and not any(raw[offset:]), 'Unexpected trailing CPIO data')
            break
        name = name[2:] if name.startswith('./') else name
        name = name[1:] if name.startswith('/') else name
        require('..' not in PurePosixPath(name).parts and not PurePosixPath(name).is_absolute(), 'Unsafe CPIO path')
        key = major, minor, inode
        if stat.S_ISREG(mode) and links > 1:
            if size:
                require(not groups.get(key) or groups[key] == content, 'Conflicting CPIO hardlink contents')
                groups[key] = content
            else:
                groups.setdefault(key, b'')
        if not name.startswith(PREFIX):
            continue
        relative = name[len(PREFIX):].rstrip('/')
        if not relative or relative == 'lib' or relative.startswith('lib/'):
            continue  # AArch64 diagnostic only; leave the phone's 32-bit files untouched.
        require(re.fullmatch(r'[A-Za-z0-9_./@+:=-]+', relative) is not None, 'Unsupported HAL filename')
        require(relative == str(PurePosixPath(relative)), 'Noncanonical HAL path')
        require(relative not in selected, 'Duplicate HAL path')
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode), 'Unexpected special HAL file')
        require(not stat.S_ISDIR(mode) or size == 0, 'Directory has CPIO data')
        if stat.S_ISLNK(mode):
            require(0 < size <= 4096 and links == 1, 'Invalid HAL symlink')
        selected[relative] = {'mode': mode, 'data': content, 'key': key, 'links': links}
    else:
        raise ValueError('Too many CPIO entries')
    require(name == 'TRAILER!!!', 'Missing CPIO trailer')
    for name, record in selected.items():
        if stat.S_ISREG(record['mode']) and record['links'] > 1:
            require(record['key'] in groups, 'Unresolved HAL hardlink')
            record['data'] = groups[record['key']]
        for parent in PurePosixPath(name).parents:
            previous = selected.get(str(parent))
            require(previous is None or stat.S_ISDIR(previous['mode']), 'HAL parent is not a directory')
    require(sum(len(r['data']) for r in selected.values()) <= TREE_LIMIT, 'HAL tree exceeds RAM probe limit')
    return selected


def expected_files():
    report = json.loads((ROOT / 'validation/runtime-service-build-report-user-20261026.json').read_text())
    expected = {k[len('system/'):]: v for k, v in report['files'].items() if k.startswith('system/')}
    expected['bin/linkerconfig'] = report['files']['apex/com.android.runtime/bin/linkerconfig']
    return expected


def write_hal_archive(records, output, expected=None):
    if expected is None:
        expected = expected_files()
    for name, pin in expected.items():
        record = records.get(name)
        require(record is not None and stat.S_ISREG(record['mode']), 'Missing packaged regular file: ' + name)
        require((len(record['data']), sha(record['data'])) == (pin['bytes'], pin['sha256']), 'Packaged file differs from reviewed build: ' + name)
    hashes, links = {}, {}
    for name, record in records.items():
        if stat.S_ISREG(record['mode']):
            hashes[name] = sha(record['data'])
        elif stat.S_ISLNK(record['mode']):
            target = bytes(record['data']).decode('utf-8')
            require(not any(ord(c) < 32 for c in target), 'Control character in HAL link')
            links[name] = target
    require('n100-files.sha256' not in records, 'Reserved HAL path')
    # Place all directories/files before symlinks; never write through a link.
    order = sorted(records, key=lambda n: (2 if stat.S_ISLNK(records[n]['mode']) else 0 if stat.S_ISDIR(records[n]['mode']) else 1, n))
    with tarfile.open(output, 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
        checks = ''.join('{}  {}\n'.format(h, n) for n, h in sorted(hashes.items())).encode()
        info = tarfile.TarInfo('n100-files.sha256'); info.size = len(checks); info.mode = 0o644
        archive.addfile(info, io.BytesIO(checks))
        for name in order:
            record = records[name]
            info = tarfile.TarInfo(name)
            info.uid = info.gid = 0
            info.mode = 0o755 if record['mode'] & 0o111 else 0o644
            if stat.S_ISDIR(record['mode']):
                info.type = tarfile.DIRTYPE; info.mode = 0o755
                archive.addfile(info)
            elif stat.S_ISLNK(record['mode']):
                info.type = tarfile.SYMTYPE; info.linkname = links[name]
                archive.addfile(info)
            else:
                info.size = len(record['data'])
                archive.addfile(info, io.BytesIO(record['data']))
    return {'regular_file_hashes': hashes, 'symlinks': links,
            'unpacked_regular_bytes': sum(len(r['data']) for r in records.values() if stat.S_ISREG(r['mode']))}


def select_rpm(android_root, explicit=None):
    repo = android_root / 'droid-local-repo/billie2'
    paths = [explicit] if explicit else sorted(repo.glob('droid-hal-billie2-[0-9]*.aarch64.rpm'))
    require(len(paths) == 1, 'Expected exactly one main HAL aarch64 RPM in {}; use --rpm for an explicit file'.format(repo))
    path = paths[0]
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= LIMIT, 'Invalid RPM input file')
    path = path.resolve()
    query = subprocess.run(['rpm', '-qp', '--qf', '%{NAME}\n%{ARCH}\n%{VERSION}-%{RELEASE}\n', str(path)],
                           capture_output=True, text=True, check=True, timeout=30).stdout.splitlines()
    require(len(query) == 3 and query[:2] == ['droid-hal-billie2', 'aarch64'], 'Unexpected RPM name or architecture')
    return path, {'name': query[0], 'arch': query[1], 'version_release': query[2],
                  'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes()), 'path': str(path)}


def limit_child():
    _, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
    bound = LIMIT if hard == resource.RLIM_INFINITY else min(LIMIT, hard)
    resource.setrlimit(resource.RLIMIT_FSIZE, (bound, bound))


def prepare(android_root, output, rpm=None):
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new bundle directory')
    package, package_info = select_rpm(android_root, rpm)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.runtime-rpm-', dir=str(output.parent)))
    try:
        cpio = staging / 'payload.cpio'
        with cpio.open('wb') as stream:
            subprocess.run(['rpm2cpio', str(package)], stdout=stream, stderr=subprocess.PIPE,
                           check=True, timeout=180, preexec_fn=limit_child)
        require(sha(package.read_bytes()) == package_info['sha256'], 'RPM changed during preparation')
        require(cpio.stat().st_size <= LIMIT, 'RPM CPIO exceeds bound')
        records = cpio_tree(cpio.read_bytes()); cpio.unlink()
        tree_info = write_hal_archive(records, staging / 'hal-system.tar.gz')
        apex = staging / 'runtime.apex'
        apex.write_bytes(records['apex/com.android.runtime.apex']['data'])
        payload = runtime.read_payload(apex); apex.unlink()
        require(sha(payload) == PAYLOAD_SHA, 'Runtime payload differs from device-tested payload')
        (staging / 'apex_payload.img').write_bytes(payload)
        checks = '\n'.join('{} {} {}'.format(n, p[0], p[1]) for n, p in runtime.PINS.items())
        hal = (staging / 'hal-system.tar.gz').read_bytes()
        script = (ROOT / 'tools/runtime-rpm-probe.sh.in').read_text()
        for token, value in {'@PAYLOAD_SHA@': PAYLOAD_SHA, '@PAYLOAD_BYTES@': len(payload),
                             '@FILE_CHECKS@': checks, '@HAL_SHA@': sha(hal), '@HAL_BYTES@': len(hal)}.items():
            script = script.replace(token, str(value))
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        (staging / 'runtime-probe.sh').write_text(script)
        (staging / 'runtime-probe.sh').chmod(0o755)
        manifest = {'schema': 1, 'kind': 'temporary-hal-rpm-probe', 'probe_version': 3,
                    'source_rpm': package_info, 'source_apex_sha256': runtime.APEX_SHA,
                    'payload_bytes': len(payload), 'payload_sha256': PAYLOAD_SHA,
                    'script_sha256': sha(script.encode()), 'hal_archive_sha256': sha(hal),
                    'hal_archive_bytes': len(hal), 'hal_tree': tree_info,
                    'core_reference': 'previously device-verified fixed runtime payload and core hashes',
                    'rpm_scripts_executed': False, 'rpm_signatures_verified': False, 'device_tested': False,
                    'persistent_installation_performed': False, 'systemd_started': False,
                    'android_hal_started': False, 'flashable_release': False}
        (staging / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        del records
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print('Prepared exact HAL RPM probe bundle: {}'.format(output))
    print('HAL RAM bytes: {}; download bytes: {}'.format(tree_info['unpacked_regular_bytes'], len(hal)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--android-root', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    prep.add_argument('--rpm', type=Path)
    report = commands.add_parser('report')
    report.add_argument('--bundle-dir', type=Path, required=True)
    report.add_argument('--probe-log', type=Path, required=True)
    report.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'prepare':
            prepare(args.android_root, args.output_dir, args.rpm)
        else:
            manifest = json.loads((args.bundle_dir / 'manifest.json').read_text())
            archive = args.bundle_dir / 'hal-system.tar.gz'
            require(archive.stat().st_size == manifest['hal_archive_bytes'] and sha(archive.read_bytes()) == manifest['hal_archive_sha256'], 'HAL bundle archive changed')
            runtime.collect(args.bundle_dir, args.probe_log, args.output)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'HAL RPM probe preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
