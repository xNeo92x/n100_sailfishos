#!/usr/bin/env python3
"""Prepare the exact reviewed BE2013 debug/DTBO pair and stock rollback file.

No device writes. The check subcommand only reads bootloader variables.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile


DTBO_SIZE = 25165824
BOOT_SHA = '6dccc17b73f572a42400274ca8ce50595d9e4a8127732e4e9d53d29d1727a6ac'
DTBO_SHA = 'c14f4bce02f0254a5eb381940767cb00bda9c740d5ad2cefa271f69bd859d46e'
STOCK_SHA = '5425e6e33ef63a06da926df47a42190ff263a3362bb8f1315a68fe92a443938e'
RESTORE_SHA = '2d663aaa05568bff83c7e96abe72eef85d4e9e14c5c9e70da8b7274278c23706'
BOOT_NAME = 'n100-debug-boot-20261025T191724Z.img'
USERDATA_BYTES = 8589934592
USERDATA_SHA = '3c7bc615c566ccd3634dd829bdea41a2df32aa9419f360f7969a189aff6e3e3d'
USERDATA_PARTITION_BYTES = 48062869504
SPARSE_LIMIT = 512 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_userdata(path):
    if not path.is_file() or path.stat().st_size != USERDATA_BYTES:
        raise ValueError('Expected the reviewed 8-GiB regular userdata image')
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    if result.hexdigest() != USERDATA_SHA:
        raise ValueError('Userdata SHA-256 mismatch; do not flash this file')


def checked_file(path, size, sha):
    if path.stat().st_size != size:
        raise ValueError('Unexpected size: {}'.format(path))
    data = path.read_bytes()
    if digest(data) != sha:
        raise ValueError('SHA-256 mismatch: {}'.format(path))
    return data


def resize_footer(data, size):
    """Raw-image equivalent of avbtool resize_image; preserve data/VBMeta/footer."""
    if len(data) < 4096 or len(data) % 4096 or size % 4096:
        raise ValueError('Invalid raw AVB image size')
    magic, major, minor, original, offset, length, reserved = struct.unpack(
        '>4sIIQQQ28s', data[-64:])
    if (magic, major, minor, reserved) != (b'AVBf', 1, 0, b'\0' * 28):
        raise ValueError('Unexpected AVB footer')
    if not 0 < original <= offset or length < 256 or offset + length > len(data) - 4096:
        raise ValueError('Invalid VBMeta bounds')
    if data[offset:offset + 4] != b'AVB0':
        raise ValueError('Missing VBMeta header')
    end = (offset + length + 4095) // 4096 * 4096
    if size < end + 4096:
        raise ValueError('Target too small')
    return data[:end] + b'\0' * (size - end - 64) + data[-64:]


def stock_restore(path):
    data = resize_footer(checked_file(path, 8388608, STOCK_SHA), DTBO_SIZE)
    if digest(data) != RESTORE_SHA:
        raise ValueError('Unexpected resized stock DTBO hash')
    return data


def prepare(android_root, stock_dir, output):
    if output.exists():
        raise ValueError('Output exists; use check or choose a new directory')
    product = android_root / 'out/target/product/billie2'
    # Validate every input before creating any output directory.
    boot = checked_file(product / BOOT_NAME, 45064192, BOOT_SHA)
    dtbo = checked_file(product / 'dtbo.img', DTBO_SIZE, DTBO_SHA)
    rollback = stock_restore(stock_dir / 'dtbo.img')
    outputs = {'debug-boot.img': boot, 'dtbo-test.img': dtbo,
               'dtbo-stock-24MiB.img': rollback}
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.n100-test-', dir=str(output.parent)))
    try:
        records = {}
        for name, data in outputs.items():
            (staging / name).write_bytes(data)
            records[name] = {'bytes': len(data), 'sha256': digest(data)}
        manifest = {'schema': 1, 'device': 'BE2013', 'slot': 'b',
                    'files': records, 'stock_source_sha256': STOCK_SHA,
                    'stock_dir': str(stock_dir.resolve()),
                    'restore_procedure_device_tested': False,
                    'boot_tested': False, 'flashable_release': False}
        (staging / 'test-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    print('Prepared: {}'.format(output))
    print('No device accessed. Stock data/VBMeta unchanged; AVB footer relocated to 24 MiB.')


def fastboot(*args):
    result = subprocess.run(['fastboot'] + list(args), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, timeout=15, check=True)
    return result.stdout + result.stderr


def check(bundle, stock_dir, userdata=None):
    # Recompute against constants and the independently pinned stock source;
    # do not trust a manifest-provided hash or source path.
    checked_file(bundle / 'debug-boot.img', 45064192, BOOT_SHA)
    checked_file(bundle / 'dtbo-test.img', DTBO_SIZE, DTBO_SHA)
    rollback = stock_restore(stock_dir / 'dtbo.img')
    checked_file(bundle / 'dtbo-stock-24MiB.img', DTBO_SIZE, digest(rollback))
    if userdata is not None:
        checked_userdata(userdata)
    devices = [line.split() for line in fastboot('devices').splitlines() if line.strip()]
    if len(devices) != 1 or len(devices[0]) != 2 or devices[0][1] != 'fastboot':
        raise ValueError('Connect exactly one bootloader fastboot device')
    serial = devices[0][0]
    expected = {'product': 'bengal', 'unlocked': 'yes', 'current-slot': 'b',
                'is-userspace': 'no', 'partition-size:dtbo_b': DTBO_SIZE,
                'partition-size:boot_b': 100663296}
    if userdata is not None:
        expected['partition-size:userdata'] = USERDATA_PARTITION_BYTES
    observed = {}
    for key in list(expected) + ['max-download-size']:
        text = fastboot('-s', serial, 'getvar', key)
        matches = re.findall(r'^(?:\(bootloader\)\s*)?' + re.escape(key) +
                             r':\s*(\S+)\s*$', text, re.M)
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous fastboot variable: ' + key)
        value = matches[0]
        if key.startswith('partition-size:') or key == 'max-download-size':
            value = int(value, 16 if value.lower().startswith('0x') else 10)
        if key in expected and value != expected[key]:
            raise ValueError('Unexpected {}: {}'.format(key, value))
        observed[key] = value
    if observed['max-download-size'] < 45064192:
        raise ValueError('Maximum download too small')
    if userdata is not None and observed['max-download-size'] < SPARSE_LIMIT:
        raise ValueError('Maximum download below the planned 512-MiB sparse limit')
    print(json.dumps(observed, indent=2))
    print('PASS: reviewed files, stock rollback, unlocked bootloader fastboot, active slot B.')
    if userdata is not None:
        print('PASS: exact userdata image/hash, physical capacity and 512-MiB download limit.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    prep = sub.add_parser('prepare')
    prep.add_argument('--android-root', type=Path, required=True)
    prep.add_argument('--stock-dir', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    verify = sub.add_parser('check')
    verify.add_argument('--bundle', type=Path, required=True)
    verify.add_argument('--stock-dir', type=Path, required=True)
    verify.add_argument('--userdata', type=Path,
                        help='Also stream-check the exact 8-GiB image and userdata capacity')
    args = parser.parse_args()
    try:
        if args.mode == 'prepare':
            prepare(args.android_root, args.stock_dir, args.output)
        else:
            check(args.bundle, args.stock_dir, args.userdata)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'First boot preparation/check failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
