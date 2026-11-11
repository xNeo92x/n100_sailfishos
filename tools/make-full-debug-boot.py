#!/usr/bin/env python3
"""Derive a full-boot debug image from the device-tested early debug image.

The early image (n100_debug=1) always stops before userdata is mounted. This variant
replaces only that command-line token with hybris' own `bootmode=debug`: init then mounts
the Sailfish stowaway, offers the Telnet debug shell (port 23) and, after `continue`,
switch_roots into /init-debug, which halts again before systemd when
/target/init_enter_debug2 exists (Telnet port 2323). Kernel, ramdisk and DTB stay
byte-identical; only the primary 512-byte cmdline field may differ. Runs on the host.
"""
import argparse
import hashlib
import json
from pathlib import Path

SOURCE_SHA = '6dccc17b73f572a42400274ca8ce50595d9e4a8127732e4e9d53d29d1727a6ac'
OLD, NEW = 'n100_debug=1', 'bootmode=debug'
FIELD = slice(64, 576)


def require(condition, message):
    if not condition: raise ValueError(message)


def derive(data):
    require(hashlib.sha256(data).hexdigest() == SOURCE_SHA, 'Input is not the device-tested debug-boot.img')
    require(data[:8] == b'ANDROID!', 'Not an Android boot image')
    require(not data[608:1632].split(b'\0', 1)[0], 'Unexpected extra command line')
    cmdline = data[FIELD].split(b'\0', 1)[0].decode('ascii')
    tokens = cmdline.split()
    require(tokens.count(OLD) == 1 and not any(t.startswith('bootmode=') for t in tokens), 'Unexpected command line')
    encoded = ' '.join(NEW if t == OLD else t for t in tokens).encode('ascii')
    require(len(encoded) < 512, 'Command line too long')
    out = bytearray(data)
    out[FIELD] = encoded.ljust(512, b'\0')
    changed = [i for i in range(len(data)) if data[i] != out[i]]
    require(changed and FIELD.start <= changed[0] and changed[-1] < FIELD.stop, 'Change outside the cmdline field')
    return bytes(out), cmdline, encoded.decode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        require(not args.output.exists(), 'Output exists')
        data, old, new = derive(args.source.read_bytes())
        with args.output.open('xb') as stream: stream.write(data)
        report = {'schema': 1, 'kind': 'full-boot-debug-image', 'source_sha256': SOURCE_SHA,
                  'image_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                  'old_cmdline': old, 'cmdline': new, 'changed_region': 'header cmdline bytes 64-575 only',
                  'mounts_userdata': True, 'debug_stops': ['initramfs Telnet :23 before switch_root',
                  'real rootfs /init-debug Telnet :2323 if /target/init_enter_debug2 exists'],
                  'requires_matching_dtbo': True, 'device_tested': False, 'flashable_release': False}
        report_path = args.output.with_name(args.output.name + '.json')
        with report_path.open('x') as stream: stream.write(json.dumps(report, indent=2) + '\n')
    except (OSError, ValueError) as exc:
        parser.exit(1, 'full debug boot derivation failed: {}\n'.format(exc))
    print('Full-boot debug image: ' + str(args.output))
    print('SHA-256: ' + report['image_sha256'])
    print('cmdline: ' + new)


if __name__ == '__main__': main()
