#!/usr/bin/env python3
"""Inspect VNDK-30 APEX and Linux host linker tools; never install device files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import zipfile


MAX_APEX = 512 * 1024 * 1024
LISTS = ('llndk', 'vndksp', 'vndkcore', 'vndkprivate')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def file_info(path):
    require(path.is_file(), 'Missing build input: ' + str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return {'path': str(path.resolve()), 'bytes': path.stat().st_size, 'sha256': h.hexdigest()}


def manifest_info(data):
    require(len(data) <= 65536, 'Oversized APEX manifest')
    offset, result = 0, {}
    def varint():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            require(offset < len(data), 'Truncated protobuf integer')
            byte = data[offset]; offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
        raise ValueError('Oversized protobuf integer')
    while offset < len(data):
        tag = varint(); field, wire = tag >> 3, tag & 7
        require(field > 0, 'Invalid protobuf field')
        if wire == 0:
            value = varint()
        elif wire == 2:
            length = varint()
            require(offset + length <= len(data), 'Truncated protobuf field')
            value = data[offset:offset + length]; offset += length
        elif wire in (1, 5):
            length = 8 if wire == 1 else 4
            require(offset + length <= len(data), 'Truncated fixed protobuf field')
            offset += length
            continue
        else:
            raise ValueError('Unsupported APEX protobuf wire type')
        if field == 1 and wire == 2:
            require('name' not in result, 'Duplicate APEX name')
            result['name'] = value.decode('utf-8')
        elif field == 2 and wire == 0:
            require('version' not in result, 'Duplicate APEX version')
            result['version'] = value
    require(result.get('name') == 'com.android.vndk.v30' and result.get('version', 0) > 0,
            'Expected the VNDK-30 APEX manifest')
    return result


def dump_file(debugfs, image, name, output):
    quoted = str(output).replace('\\', '\\\\').replace('"', '\\"')
    require(not any(c in quoted for c in '\n\r\0'), 'Invalid staging directory')
    result = subprocess.run([debugfs, '-R', 'dump /{} "{}"'.format(name, quoted), str(image)],
                            capture_output=True, text=True, timeout=30)
    require(result.returncode == 0 and output.is_file(), 'Cannot read VNDK payload file: ' + name)


def inspect_apex(package, debugfs):
    require(package.is_file() and 0 < package.stat().st_size <= MAX_APEX, 'VNDK APEX size exceeds bound')
    info = file_info(package)
    with tempfile.TemporaryDirectory(prefix='n100-vndk30-') as temp:
        root = Path(temp); image = root / 'payload.img'
        with zipfile.ZipFile(package) as archive:
            for name in ('apex_payload.img', 'apex_manifest.pb'):
                require(archive.namelist().count(name) == 1, 'Missing or duplicate ' + name)
            require(archive.getinfo('apex_manifest.pb').file_size <= 65536, 'Oversized APEX manifest')
            info['manifest'] = manifest_info(archive.read('apex_manifest.pb'))
            size = archive.getinfo('apex_payload.img').file_size
            require(2048 <= size <= MAX_APEX, 'Invalid VNDK payload size')
            h = hashlib.sha256()
            with archive.open('apex_payload.img') as source, image.open('wb') as dest:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    dest.write(chunk); h.update(chunk)
        require(image.stat().st_size == size, 'Incomplete VNDK payload')
        with image.open('rb') as stream:
            stream.seek(1080); require(stream.read(2) == b'\x53\xef', 'Expected raw ext4 VNDK payload')
        info['payload'] = {'bytes': size, 'sha256': h.hexdigest(), 'zip_crc_checked': True}
        info['library_lists'] = {}
        for label in LISTS:
            name = 'etc/{}.libraries.30.txt'.format(label)
            output = root / (label + '.txt')
            dump_file(debugfs, image, name, output)
            require(output.stat().st_size <= 65536, 'VNDK library list exceeds bound')
            data = output.read_bytes(); text = data.decode('utf-8')
            lines = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith('#')]
            info['library_lists'][label] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                                            'entries': lines, 'text': text}
        candidates = info['library_lists']['vndkcore']['entries'] + info['library_lists']['vndksp']['entries']
        found = None
        for name in candidates[:12]:
            if not re.fullmatch(r'[A-Za-z0-9_.+@-]+\.so', name):
                continue
            output = root / 'sample.so'
            if output.exists(): output.unlink()
            try:
                dump_file(debugfs, image, 'lib64/' + name, output)
            except ValueError:
                continue
            with output.open('rb') as stream: header = stream.read(64)
            if len(header) >= 64 and header[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<HH', header, 16) == (3, 183):
                found = dict(file_info(output), payload_path='lib64/' + name, elf_class=2, elf_machine=183, elf_type=3)
                found.pop('path'); break
        require(found is not None, 'No sampled AArch64 VNDK library found')
        info['aarch64_library_sample'] = found
    require(file_info(package)['sha256'] == info['sha256'], 'VNDK APEX changed during inspection')
    return info


def inspect(android_root, host_bin):
    android = android_root.resolve(); product = android / 'out/target/product/billie2'
    host_bin = host_bin if host_bin.is_absolute() else android / host_bin
    debugfs = shutil.which('debugfs')
    require(debugfs, 'debugfs missing in Ubuntu; install e2fsprogs')
    paths = [product / p for p in ('system_ext/apex/com.android.vndk.v30.apex', 'system/apex/com.android.vndk.v30.apex')]
    apex = next((p for p in paths if p.is_file() and not p.is_symlink()), None)
    require(apex is not None, 'VNDK-30 APEX not built in system_ext/apex or system/apex')
    result = {'schema': 1, 'kind': 'android-linker-namespace-inputs', 'vndk_version': '30',
              'vndk_apex': inspect_apex(apex, debugfs), 'host_tools': {}}
    for name in ('linkerconfig', 'conv_linker_config'):
        path = host_bin / name
        info = file_info(path)
        require(path.stat().st_mode & 0o111, 'Host tool is not executable: ' + str(path))
        if name == 'linkerconfig':
            with path.open('rb') as stream: header = stream.read(64)
            require(len(header) >= 64 and header[:6] == b'\x7fELF\x02\x01' and struct.unpack_from('<H', header, 18)[0] == 62,
                    'Expected the Linux x86_64 host linkerconfig binary')
            info.update(elf_class=2, elf_machine=62)
        help_result = subprocess.run([str(path), '--help'], capture_output=True, text=True, timeout=15)
        info['help_exit'] = help_result.returncode
        info['help'] = (help_result.stdout + help_result.stderr)[:16384]
        require(help_result.returncode == 0, 'Host tool help failed: ' + name + '\n' + info['help'])
        if name == 'linkerconfig':
            require(all(flag in info['help'] for flag in ('--root', '--vndk', '--target')), 'Host linkerconfig options missing')
        else:
            require('systemprovide' in info['help'] and 'proto' in info['help'], 'Host protobuf conversion commands missing')
        result['host_tools'][name] = info
    bases = [product / 'obj/ETC/system_linker_config_intermediates/system_linker_config']
    folder = android / 'out/soong/.intermediates/system/core/rootdir/system_linker_config'
    bases += sorted(folder.glob('*/linker.config.pb')) + sorted(folder.glob('*/gen/linker.config.pb'))
    available = [file_info(p) for p in bases if p.is_file()]
    require(available and all(p['bytes'] > 0 for p in available), 'Base system_linker_config protobuf not built')
    result['base_system_config_candidates'] = available
    installed = product / 'system/etc/linker.config.pb'
    result['installed_system_config_pb'] = file_info(installed) if installed.is_file() else None
    result['source_revisions'] = {}
    for relative in ('packages/modules/vndk', 'prebuilts/vndk/v30', 'system/linkerconfig'):
        git = subprocess.run(['git', '-C', str(android / relative), 'rev-parse', 'HEAD'],
                             capture_output=True, text=True, check=True, timeout=10)
        result['source_revisions'][relative] = git.stdout.strip()
    result.update(passed=True, errors=[], final_system_config_pb_generated=False, namespace_configuration_generated=False,
                  phone_modified=False, rpm_rebuilt=False, full_os_boot_verified=False)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--host-bin', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.android_root, args.host_bin)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
        print('Linker input report: {}'.format(args.output.resolve()))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        failure = {'schema': 1, 'kind': 'android-linker-namespace-inputs', 'passed': False,
                   'errors': [str(exc)], 'phone_modified': False, 'namespace_configuration_generated': False}
        try:
            args.output.write_text(json.dumps(failure, indent=2) + '\n')
        except OSError:
            pass
        parser.exit(1, 'Linker input inspection failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
