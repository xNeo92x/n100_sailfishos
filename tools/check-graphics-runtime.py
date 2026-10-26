#!/usr/bin/env python3
"""Inspect the real libGLESv3 build and stage changed AArch64 dependencies for later review."""
import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('graphics_native_reference', ROOT / 'tools/prepare-native-runtime-probe.py')
native = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(native)
SPEC = importlib.util.spec_from_file_location('graphics_elf_layout', ROOT / 'tools/inspect-android-output.py')
elf = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(elf)
require = native.require
sha = native.sha
VENDOR_REPORT = ROOT / 'validation/vendor-v2-device-report-user-20261026.json'
VENDOR_SHA = '2824601b4c79fab7715d7978c5f3c1b3fd38eba8527de440b2143b646d66ff19'
LIBRARY = 'libGLESv3.so'
BIONIC = {'libc.so', 'libm.so', 'libdl.so', 'libdl_android.so'}
EXPORTS = {'glGetString', 'glGetStringi', 'glBindVertexArray', 'glDrawArraysInstanced', 'glDispatchCompute'}
LIMIT = 32 * 1024 * 1024


def pin(path, limit=LIMIT):
    require(path.is_file() and not path.is_symlink() and 0 < path.stat().st_size <= limit, 'Invalid regular build input: ' + str(path))
    data = path.read_bytes()
    return {'bytes': len(data), 'sha256': sha(data)}


def source_info(android):
    path = android / 'frameworks/native/opengl/libs/Android.bp'
    info = pin(path); text = path.read_text()
    blocks = re.findall(r'^cc_library_shared\s*\{\s*name:\s*"libGLESv3",(.*?)^\}', text, re.M | re.S)
    require(len(blocks) == 1 and re.search(r'srcs:\s*\[\s*"GLES2/gl2.cpp"\s*,?\s*\]', blocks[0]) and
            '"gles_libs_defaults"' in blocks[0], 'Expected real libGLESv3 implementation module, not an NDK stub')
    head = subprocess.run(['git', '-C', str(android / 'frameworks/native'), 'rev-parse', 'HEAD'],
                          capture_output=True, text=True, check=True, timeout=15).stdout.strip()
    return dict(info, path=str(path), revision=head, module='libGLESv3', implementation_source='GLES2/gl2.cpp')


def reviewed_hal():
    raw = VENDOR_REPORT.read_bytes()
    require(sha(raw) == VENDOR_SHA, 'Reviewed V2 vendor report changed')
    device = json.loads(raw)
    composer = '/vendor/bin/hw/vendor.qti.hardware.display.composer-service'
    require(device['bundle_integrity_rechecked'] and device['bundle']['probe_version'] == 2 and
            device['assessment']['system_service_dependency_probe_passed'] and
            device['assessment']['vendor_dependency_exit_codes'][composer] == 1 and
            'libGLESv3.so' in device['device_probe_log'], 'Expected the reviewed composer dependency blocker')
    manifest = native.reviewed()
    require(device['bundle']['source_rpm'] == manifest['source_rpm'], 'Vendor probe used another HAL RPM')
    return manifest


def resolve_library(product, name):
    require(re.fullmatch(r'[A-Za-z0-9_.+@-]+\.so', name), 'Invalid DT_NEEDED name')
    logical = '/system/lib64/' + name
    path = product / logical.lstrip('/')
    if not path.exists() and not path.is_symlink() and name in BIONIC:
        logical = '/apex/com.android.runtime/lib64/bionic/' + name
    seen = set()
    for _ in range(10):
        require(logical not in seen and logical.startswith(('/system/lib64/', '/apex/com.android.runtime/lib64/bionic/')),
                'Library symlink escapes the reviewed system/runtime roots: ' + name)
        seen.add(logical); path = product / logical.lstrip('/')
        require(elf.within(path.parent, product), 'Library parent leaves the product tree: ' + name)
        if path.is_symlink():
            target = os.readlink(path)
            logical = posixpath.normpath(target if target.startswith('/') else posixpath.dirname(logical) + '/' + target)
            continue
        pin(path)
        return path, logical
    raise ValueError('Too many library symlinks: ' + name)


def dynamic_info(tool, path):
    result = subprocess.run([tool, '--dynamic', '--wide', str(path)], capture_output=True, text=True, check=True, timeout=20,
                            env={'LC_ALL': 'C'})
    require(len(result.stdout) <= 262144 and not result.stderr, 'Unexpected dynamic ELF metadata: ' + str(path))
    needed = re.findall(r'\(NEEDED\).*Shared library: \[([^]]+)\]', result.stdout)
    sonames = re.findall(r'\(SONAME\).*Library soname: \[([^]]+)\]', result.stdout)
    require(len(needed) == result.stdout.count('(NEEDED)') and len(sonames) <= 1, 'Unparsed dynamic ELF metadata')
    return needed, sonames[0] if sonames else None


def inspect(android, output):
    require(not any((output / name).exists() or (output / name).is_symlink()
                    for name in ('report.json', 'graphics-runtime.tar.gz')),
            'Graphics report/archive exists; choose a new work directory')
    baseline = reviewed_hal(); source = source_info(android)
    product = (android / 'out/target/product/billie2').resolve()
    runtime = product / 'system/apex/com.android.runtime.apex'
    require(elf.within(runtime.parent, product), 'Runtime parent leaves the product tree')
    runtime_pin = pin(runtime)
    require(all(runtime_pin[k] == baseline['source_apex'][k] for k in ('bytes', 'sha256')),
            'Runtime APEX changed; review it before mixing graphics inputs')
    root = product / 'system/lib64' / LIBRARY
    require(elf.within(root.parent, product), 'Graphics library parent leaves the product tree')
    pin(root)  # The root must be an installed implementation, never a symlink or intermediate SDK stub.
    tool = shutil.which('readelf'); require(tool, 'readelf is required; install binutils inside Ubuntu')
    files, pending, total = {}, [LIBRARY], 0
    while pending:
        name = pending.pop()
        if name in files: continue
        require(len(files) < 128, 'Graphics dependency graph exceeds bound')
        path, logical = resolve_library(product, name)
        info = pin(path); total += info['bytes']; require(total <= 256 * 1024 * 1024, 'Graphics closure exceeds bound')
        abi = elf.elf_details(path)
        require(abi.get('elf_class') == 2 and abi.get('elf_machine') == 183 and abi.get('elf_type') == 3 and
                not abi.get('elf_error') and not abi.get('elf_interpreter'), 'Expected AArch64 shared library: ' + name)
        needed, soname = dynamic_info(tool, path)
        require(all(re.fullmatch(r'[A-Za-z0-9_.+@-]+\.so', n) for n in needed), 'Unsupported dependency name')
        require(pin(path) == info, 'Library changed during inspection: ' + name)
        old = baseline['hal_tree']['regular_file_hashes'].get('lib64/' + name)
        if name in BIONIC: require(info['sha256'] == old, 'Bionic differs from tested Runtime: ' + name)
        files[name] = dict(info, source_path=str(path), logical_source=logical, elf=abi, needed=needed, soname=soname,
                           matches_device_tested_hal=info['sha256'] == old,
                           previous_hal_sha256=old)
        pending += needed
    require(files[LIBRARY]['soname'] == LIBRARY and 'libEGL.so' in files[LIBRARY]['needed'],
            'libGLESv3 must be the real EGL-dependent implementation')
    symbols = subprocess.run([tool, '--dyn-syms', '--wide', str(root)], capture_output=True, text=True, check=True, timeout=20,
                             env={'LC_ALL': 'C'})
    require(len(symbols.stdout) <= 2 * 1024 * 1024 and not symbols.stderr, 'Unexpected graphics symbol table')
    exported = set(re.findall(r'^\s*\d+:\s+\S+\s+\d+\s+(?:FUNC|IFUNC)\s+(?:GLOBAL|WEAK)\s+DEFAULT\s+(?!UND\b)\S+\s+(\S+)', symbols.stdout, re.M))
    exported = {n.split('@')[0] for n in exported}
    require(EXPORTS <= exported, 'Required GLES implementation exports missing: ' + ', '.join(sorted(EXPORTS - exported)))
    additions = {n: v for n, v in files.items() if v['previous_hal_sha256'] is None}
    changed = {n: v for n, v in files.items() if v['previous_hal_sha256'] and not v['matches_device_tested_hal']}
    staged = dict(additions, **changed)
    require(LIBRARY in staged, 'Expected libGLESv3 missing from the reviewed HAL; inspect changed baseline')
    output.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.graphics-runtime-', dir=str(output)))
    try:
        archive_path = staging / 'graphics-runtime.tar.gz'
        with tarfile.open(archive_path, 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
            checks = ''.join('{}  lib64/{}\n'.format(item['sha256'], name) for name, item in sorted(staged.items())).encode()
            member = tarfile.TarInfo('n100-graphics-files.sha256'); member.size = len(checks); member.mode = 0o644
            archive.addfile(member, io.BytesIO(checks))
            for name, item in sorted(staged.items()):
                path = Path(item['source_path']); data = path.read_bytes()
                require((len(data), sha(data)) == (item['bytes'], item['sha256']), 'Graphics source changed')
                member = tarfile.TarInfo('lib64/' + name); member.size = item['bytes']; member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))
        for item in files.values():
            require(pin(Path(item['source_path'])) == {k: item[k] for k in ('bytes', 'sha256')}, 'Graphics closure changed before publication')
        require(pin(runtime) == runtime_pin, 'Runtime APEX changed during graphics inspection')
        report = {'schema': 1, 'kind': 'android-graphics-runtime-build', 'passed': True,
                  'source_vendor_report_sha256': VENDOR_SHA, 'source_rpm': baseline['source_rpm'], 'source': source,
                  'library': LIBRARY, 'files': files, 'required_exports_verified': sorted(EXPORTS),
                  'new_library_names': sorted(additions), 'changed_library_names': sorted(changed),
                  'staged_paths': ['lib64/' + n for n in sorted(staged)], 'archive': pin(archive_path, limit=256 * 1024 * 1024),
                  'runtime_apex_matches_tested_build': True, 'bionic_matches_tested_build': True,
                  'checked_bionic_libraries': sorted(BIONIC.intersection(files)),
                  'dependency_closure_complete': True, 'phone_modified': False, 'rpm_rebuilt': False,
                  'device_tested': False, 'nnapi_requirement_resolved': False, 'flashable_release': False}
        report['archive']['path'] = str(output / archive_path.name)
        (staging / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        for name in ('graphics-runtime.tar.gz', 'report.json'): (staging / name).rename(output / name)
    finally:
        shutil.rmtree(staging)
    print('Graphics runtime build and AArch64 dependency closure checked.')
    print('Share ' + str(output / 'report.json'))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path)
    parser.add_argument('--source-only', action='store_true')
    args = parser.parse_args()
    try:
        if args.source_only: print(json.dumps(source_info(args.android_root.resolve()), indent=2))
        else:
            if args.output_dir is None: parser.error('--output-dir is required')
            inspect(args.android_root.resolve(), args.output_dir.absolute())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as exc:
        if (args.output_dir and args.output_dir.is_dir() and not (args.output_dir / 'report.json').exists()
                and not (args.output_dir / 'report.json').is_symlink()):
            with (args.output_dir / 'report.json').open('x') as stream:
                json.dump({'schema': 1, 'kind': 'android-graphics-runtime-build', 'passed': False,
                           'errors': [str(exc)], 'device_tested': False, 'flashable_release': False}, stream, indent=2)
        parser.exit(1, 'Graphics runtime check failed: {}\n'.format(exc))


if __name__ == '__main__': main()
