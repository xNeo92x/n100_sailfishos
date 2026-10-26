#!/usr/bin/env python3
"""Inspect runtime APEX and HAL executable layout without executing build files."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import posixpath
import struct
import zipfile


def elf_details(path):
    """Read bounded ELF program headers, distinguishing static and dynamic files."""
    with path.open('rb') as stream:
        header = stream.read(64)
        if header[:4] != b'\x7fELF':
            return {}
        if len(header) < 52 or header[4] not in (1, 2) or header[5] != 1:
            return {'elf_error': 'Unsupported or truncated ELF header'}
        wide = header[4] == 2
        if wide and len(header) < 64:
            return {'elf_error': 'Truncated ELF64 header'}
        result = {'elf_class': header[4], 'elf_machine': struct.unpack_from('<H', header, 18)[0],
                  'elf_type': struct.unpack_from('<H', header, 16)[0]}
        offset = struct.unpack_from('<Q' if wide else '<I', header, 32 if wide else 28)[0]
        stride, count = struct.unpack_from('<HH', header, 54 if wide else 42)
        size = path.stat().st_size
        if count > 1024 or (count and (stride < (56 if wide else 32) or stride > 4096 or offset + count * stride > size)):
            return dict(result, elf_error='Invalid program header table')
        interpreters = []
        for index in range(count):
            stream.seek(offset + index * stride)
            program = stream.read(56 if wide else 32)
            if struct.unpack_from('<I', program)[0] != 3:  # PT_INTERP
                continue
            start = struct.unpack_from('<Q' if wide else '<I', program, 8 if wide else 4)[0]
            length = struct.unpack_from('<Q' if wide else '<I', program, 32 if wide else 16)[0]
            if not 1 < length <= 4096 or start + length > size:
                return dict(result, elf_error='Invalid interpreter bounds')
            stream.seek(start)
            value = stream.read(length)
            if not value.endswith(b'\0') or b'\0' in value[:-1]:
                return dict(result, elf_error='Invalid interpreter string')
            interpreters.append(value[:-1].decode('utf-8', errors='replace'))
        if len(interpreters) > 1:
            return dict(result, elf_error='Multiple ELF interpreters')
        result['elf_interpreter'] = interpreters[0] if interpreters else None
        result['static_executable'] = result['elf_type'] == 2 and not interpreters
        return result


def within(path, boundary):
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False
    return resolved == boundary or boundary in resolved.parents


def entry(path, boundary):
    result = {'path': str(path)}
    if path.absolute() != boundary and not within(path.parent, boundary):
        return dict(result, inspection_skipped='Path resolves outside product output')
    if path.is_symlink():
        target = os.readlink(path)
        # Describe absolute links in Android's product namespace, never the host's /.
        relative = str(path.absolute().relative_to(boundary).parent)
        logical = posixpath.normpath(target if target.startswith('/') else '/' + relative + '/' + target)
        return dict(result, symlink=target, android_target=logical)
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
    result.update(elf_details(path))
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
    product = product.resolve()
    boundary = product
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
    layout = []
    for partition in ['system', 'system_ext', 'vendor', 'product', 'root']:
        layout.append(entry(product / partition / 'bin', boundary))
        for name in ['getprop', 'toolbox', 'toybox', 'hwservicemanager', 'servicemanager', 'linkerconfig', 'apexd']:
            layout.append(entry(product / partition / 'bin' / name, boundary))
        for name in ['hwservicemanager.rc', 'servicemanager.rc', 'apexd.rc']:
            layout.append(entry(product / partition / 'etc/init' / name, boundary))
    return {'schema': 1, 'kind': 'android-runtime-output-inventory',
            'product': str(product), 'candidates': candidates,
            'hal_layout': layout,
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
