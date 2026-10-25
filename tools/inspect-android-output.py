#!/usr/bin/env python3
"""Inspect built runtime APEX candidates; never extract, install or execute them."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import zipfile


def within(path, boundary):
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == boundary or boundary in resolved.parents


def entry(path, boundary):
    result = {'path': str(path)}
    if path.is_symlink():
        return dict(result, symlink=os.readlink(path))
    if not within(path, boundary):
        return dict(result, inspection_skipped='Path resolves outside product output')
    if not path.exists():
        return dict(result, exists=False)
    if path.is_dir():
        return dict(result, directory=True, entries=sorted(p.name for p in path.iterdir())[:256])
    if not path.is_file():
        return dict(result, regular_file=False)
    result['bytes'] = path.stat().st_size
    if result['bytes'] > 512 * 1024 * 1024:
        return dict(result, inspection_skipped='File exceeds 512 MiB bound')
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        header = stream.read(64)
        hasher.update(header)
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    result['sha256'] = hasher.hexdigest()
    if header[:4] == b'\x7fELF' and len(header) >= 20:
        result['elf_class'] = header[4]
        if header[5] == 1:
            result['elf_machine'] = struct.unpack_from('<H', header, 18)[0]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > 1024:
                raise ValueError('Too many runtime APEX ZIP entries')
            result['zip_entries'] = [{'name': p.filename, 'bytes': p.file_size,
                                       'compressed_bytes': p.compress_size} for p in infos]
    return result


def inspect(android_root):
    product = android_root / 'out/target/product/billie2'
    if not product.is_dir():
        raise ValueError('billie2 Android product output not found')
    boundary = product.resolve()
    candidates = []
    for relative in ['apex', 'system/apex', 'system_ext/apex', 'product/apex', 'root/apex']:
        folder = product / relative
        candidates.append(entry(folder, boundary))
        if within(folder, boundary) and folder.is_dir() and not folder.is_symlink():
            for candidate in sorted(folder.glob('com.android.runtime*')):
                candidates.append(entry(candidate, boundary))
                if within(candidate, boundary) and candidate.is_dir() and not candidate.is_symlink():
                    for child in ['bin/linker', 'bin/linker64', 'lib64/bionic/libc.so',
                                  'lib64/bionic/libdl.so', 'lib64/bionic/libm.so']:
                        candidates.append(entry(candidate / child, boundary))
    for relative in ['system/bin/linker', 'system/bin/linker64']:
        candidates.append(entry(product / relative, boundary))
    return {'schema': 1, 'kind': 'android-runtime-output-inventory',
            'product': str(product), 'candidates': candidates,
            'device_accessed': False, 'apex_extracted': False,
            'apex_installed': False, 'hal_started': False, 'flashable_release': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--probe-log', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.probe_log.is_file() or args.probe_log.stat().st_size > 1024 * 1024:
            raise ValueError('Expected a regular probe log of at most 1 MiB')
        result = inspect(args.android_root)
        result['device_probe_log'] = args.probe_log.read_text(errors='replace')
        with args.output.open('x') as stream:
            stream.write(json.dumps(result, indent=2) + '\n')
        print('Report: {}'.format(args.output))
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'Android output inspection failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
