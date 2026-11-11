#!/usr/bin/env python3
"""Compare a rebuilt droid-hal-billie2 RPM with the device-tested one.

The only accepted difference is the addition of the built libGLESv3.so (64-bit
and 32-bit). Every other payload entry must keep type, mode, size, content hash
and symlink target. Runs on the CachyOS host (needs rpm2cpio, zstd).
"""
import argparse
import gzip
import hashlib
import lzma
import json
from pathlib import Path
import re
import stat
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_SHA = 'b6714b06b16f38e25d0298e4efe4e39b63b1b92128678e49c00b49b7fd5a3bc6'
REFERENCE_NAME = 'droid-hal-billie2-0.0.6-202610262022.aarch64.rpm'
GRAPHICS_REPORT = ROOT / 'validation/graphics-runtime-build-user-20261111.json'
GRAPHICS_REPORT_SHA = '41cde56a39704d60e1ddd30e9f40878558dcfab2de9c568464818bb62a01424b'
PREFIX = './usr/libexec/droid-hybris/system/'
ADDED = {PREFIX + 'lib64/libGLESv3.so', PREFIX + 'lib/libGLESv3.so'}
LIMIT = 512 * 1024 * 1024
RPM_NAME = re.compile(r'droid-hal-billie2-(\d[\w.]*)-(\d{12})\.aarch64\.rpm')


def require(condition, message):
    if not condition: raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''): digest.update(block)
    return digest.hexdigest()


def rpm_name(path):
    with path.open('rb') as stream: lead = stream.read(96)
    require(len(lead) == 96 and lead[:4] == b'\xed\xab\xee\xdb', 'Not an RPM file: ' + str(path))
    return lead[10:76].split(b'\0', 1)[0].decode()


def parse_cpio(data):
    """Parse a newc cpio archive into {path: entry}; hardlinked data is resolved."""
    entries, links, offset = {}, {}, 0
    while True:
        require(data[offset:offset + 6] in (b'070701', b'070702'), 'Unexpected cpio header')
        fields = [int(data[offset + 6 + 8 * i: offset + 14 + 8 * i], 16) for i in range(13)]
        ino, mode, _uid, _gid, nlink, _mtime, size = fields[:7]
        namesize = fields[11]
        start = offset + 110
        name = data[start:start + namesize - 1].decode()
        start = (start + namesize + 3) & ~3
        body = data[start:start + size]
        require(len(body) == size, 'Truncated cpio member: ' + name)
        offset = (start + size + 3) & ~3
        if name == 'TRAILER!!!': break
        require(name not in entries, 'Duplicate cpio member: ' + name)
        kind = stat.S_IFMT(mode)
        entry = {'mode': oct(stat.S_IMODE(mode))}
        if kind == stat.S_IFREG:
            entry.update(type='file', bytes=size, sha256=sha(body))
            if nlink > 1:
                links.setdefault(ino, []).append(name)
                if size: links[ino].append(entry)
        elif kind == stat.S_IFDIR: entry['type'] = 'dir'
        elif kind == stat.S_IFLNK: entry.update(type='symlink', target=body.decode())
        else: entry['type'] = 'other-' + oct(kind)
        entries[name] = entry
    for group in links.values():
        content = [item for item in group if isinstance(item, dict)]
        require(len(content) == 1, 'Hardlink group without exactly one data member')
        for name in (item for item in group if isinstance(item, str)): entries[name] = dict(content[0])
    return entries


def payload(path):
    require(0 < path.stat().st_size <= LIMIT, 'Unexpected RPM size')
    data = subprocess.run(['rpm2cpio', str(path)], check=True, capture_output=True).stdout
    # Some rpm2cpio variants emit the still-compressed payload.
    if data[:4] == b'\x28\xb5\x2f\xfd':
        data = subprocess.run(['zstd', '-dc'], input=data, check=True, capture_output=True).stdout
    elif data[:6] == b'\xfd7zXZ\x00':
        data = lzma.decompress(data)
    elif data[:2] == b'\x1f\x8b':
        data = gzip.decompress(data)
    require(0 < len(data) <= 4 * LIMIT, 'Unexpected payload size')
    return data


def find_new_rpm(repo):
    candidates = [p for p in repo.glob('droid-hal-billie2-*.aarch64.rpm') if RPM_NAME.fullmatch(p.name)]
    candidates = [p for p in candidates if p.name != REFERENCE_NAME]
    found = ', '.join(sorted(p.name for p in candidates)) or 'none'
    require(len(candidates) == 1, 'Expected exactly one new main droid-hal-billie2 RPM, found: ' + found)
    return candidates[0]


def compare(old, new, lib64_sha):
    added = sorted(set(new) - set(old)); removed = sorted(set(old) - set(new))
    changed = sorted(name for name in set(old) & set(new) if old[name] != new[name])
    problems = []
    if removed: problems.append('removed entries: ' + ', '.join(removed[:20]))
    if changed: problems.append('changed entries: ' + ', '.join(changed[:20]))
    if set(added) != ADDED: problems.append('added entries differ: ' + ', '.join(added[:20]))
    for name in ADDED & set(new):
        if new[name]['type'] != 'file': problems.append(name + ' is not a regular file')
    if PREFIX + 'lib64/libGLESv3.so' in new and new[PREFIX + 'lib64/libGLESv3.so'].get('sha256') != lib64_sha:
        problems.append('lib64/libGLESv3.so differs from the device-tested build')
    return added, removed, changed, problems


def check(reference, new_rpm, output_dir):
    graphics = json.loads(GRAPHICS_REPORT.read_bytes())
    require(sha(GRAPHICS_REPORT.read_bytes()) == GRAPHICS_REPORT_SHA, 'Reviewed graphics report changed')
    lib64_sha = graphics['files']['libGLESv3.so']['sha256']
    require(file_sha(reference) == REFERENCE_SHA, 'Reference RPM is not the device-tested package')
    require(rpm_name(new_rpm).startswith('droid-hal-billie2-'), 'New RPM is not droid-hal-billie2')
    require(not output_dir.exists(), 'Output directory exists; choose a new one')
    old, new = parse_cpio(payload(reference)), parse_cpio(payload(new_rpm))
    added, removed, changed, problems = compare(old, new, lib64_sha)
    output_dir.mkdir(parents=True)
    report = {'schema': 1, 'kind': 'gles-hal-rpm-comparison', 'passed': not problems,
              'reference_rpm': {'name': reference.name, 'sha256': REFERENCE_SHA},
              'new_rpm': {'name': new_rpm.name, 'bytes': new_rpm.stat().st_size, 'sha256': file_sha(new_rpm),
                          'lead_name': rpm_name(new_rpm)},
              'payload_entries': len(new), 'added': {n: new[n] for n in added}, 'removed': removed,
              'changed': {n: {'old': old[n], 'new': new[n]} for n in changed}, 'problems': problems,
              'graphics_report_sha256': GRAPHICS_REPORT_SHA, 'device_tested': False, 'flashable_release': False}
    (output_dir / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--repo', type=Path, help='droid-local-repo/billie2 directory (finds the new RPM)')
    parser.add_argument('--rpm', type=Path, help='explicit new RPM')
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        new_rpm = args.rpm or find_new_rpm(args.repo)
        report = check(args.reference, new_rpm, args.output_dir)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'GLES HAL RPM check failed: {}\n'.format(exc))
    print('Report: ' + str(args.output_dir / 'report.json'))
    for problem in report['problems']: print('PROBLEM: ' + problem)
    print('GLES HAL RPM check passed: ' + str(report['passed']))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__': main()
