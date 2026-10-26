#!/usr/bin/env python3
"""Inspect native library provider APEXes on the PC; do not run their code."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('provider_preview', ROOT / 'tools/generate-linker-preview.py')
preview = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(preview)
SPEC = importlib.util.spec_from_file_location('provider_elf', ROOT / 'tools/inspect-android-output.py')
elf = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(elf)
MAX_PACKAGE = 512 * 1024 * 1024
PROVIDERS = {
    'com.android.art': 'libdexfile.so',
    'com.android.neuralnetworks': 'libneuralnetworks.so',
    'com.android.i18n': 'libandroidicu.so',
    'com.android.os.statsd': 'libstatssocket.so',
}


def package_identity(path, name):
    preview.require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_PACKAGE,
                    'Missing, linked or oversized provider package: ' + str(path))
    with zipfile.ZipFile(path) as source:
        preview.require(source.namelist().count('apex_manifest.pb') == 1, 'Missing/duplicate provider manifest')
        preview.require(source.getinfo('apex_manifest.pb').file_size <= 65536, 'Oversized provider manifest')
        manifest = preview.inputs.manifest_info(source.read('apex_manifest.pb'), name, include_libraries=True)
        preview.require(source.namelist().count('apex_payload.img') == 1, 'Provider package has no unique payload')
        size = source.getinfo('apex_payload.img').file_size
        preview.require(2048 <= size <= MAX_PACKAGE, 'Provider payload exceeds bound')
    return manifest


def normalize_package(source, destination, name):
    """Normalize an installed APEX or CAPEX, reading ZIP entries with CRC checks."""
    preview.require(source.is_file() and not source.is_symlink() and source.stat().st_size <= MAX_PACKAGE,
                    'Invalid installed provider package')
    original = preview.inputs.file_info(source)
    with zipfile.ZipFile(source) as archive:
        if 'original_apex' in archive.namelist():
            preview.require(archive.namelist().count('original_apex') == 1 and
                            0 < archive.getinfo('original_apex').file_size <= MAX_PACKAGE, 'Invalid CAPEX original_apex')
            with archive.open('original_apex') as stream, destination.open('xb') as target:
                shutil.copyfileobj(stream, target, 1024 * 1024)
        else:
            with source.open('rb') as stream, destination.open('xb') as target:
                shutil.copyfileobj(stream, target, 1024 * 1024)
    preview.checked_file(source, original)
    package_identity(destination, name)
    return original


def inspect(android, output):
    preview.require(not output.exists() and not output.is_symlink(), 'Provider output exists; choose a new directory')
    debugfs = shutil.which('debugfs'); preview.require(debugfs, 'debugfs missing; install e2fsprogs in Ubuntu')
    output.mkdir(parents=True)
    packages = output / 'packages'; packages.mkdir()
    payloads = output / 'payloads'; payloads.mkdir()
    result = {'schema': 1, 'kind': 'native-linker-provider-inputs', 'passed': False,
              'apexes': {}, 'phone_modified': False, 'ready_for_installation': False,
              'payload_libraries_executed': False, 'source_revisions': {}}
    try:
        for name, library in PROVIDERS.items():
            folder = android / 'out/target/product/billie2/system/apex'
            filenames = [name + '.apex', name + '.capex']
            if name == 'com.android.art':
                filenames += [name + '.release.apex', name + '.release.capex']
            candidates = [folder / p for p in filenames if (folder / p).exists()]
            preview.require(candidates, 'Provider APEX not installed by build: ' + name)
            package = packages / (name + '.apex')
            source_info = normalize_package(candidates[0], package, name)
            pin = preview.inputs.file_info(package)
            # Refuse conflicting installed encodings/variants rather than choosing stale content.
            for index, candidate in enumerate(candidates[1:]):
                other = packages / (name + '.alternative-' + str(index) + '.apex')
                normalize_package(candidate, other, name)
                preview.checked_file(other, pin); other.unlink()
            manifest = package_identity(package, name)
            preview.require(library in manifest['provide_native_libs'], 'APEX does not export expected native library: ' + library)
            image = payloads / (name + '.img')
            with zipfile.ZipFile(package) as archive, archive.open('apex_payload.img') as stream, image.open('xb') as target:
                shutil.copyfileobj(stream, target, 1024 * 1024)
            payload_pin = preview.inputs.file_info(image)
            # Reuse the same bounded, read-only metadata checks as the generation helper.
            image.unlink()
            metadata = preview.stage_apex(package, pin, payload_pin, name, output / 'root', payloads, debugfs)
            sample = output / (name + '-sample.so')
            preview.inputs.dump_file(debugfs, image, 'lib64/' + library, sample)
            info = dict(preview.inputs.file_info(sample), **elf.elf_details(sample))
            preview.require(info.get('elf_class') == 2 and info.get('elf_machine') == 183 and
                            info.get('elf_type') == 3 and not info.get('elf_error') and info['bytes'] > 4096,
                            'Expected an AArch64 shared implementation from the provider payload: ' + library)
            info['payload_path'] = 'lib64/' + library
            result['apexes'][name] = {'package': pin, 'installed_source': source_info,
                                     'payload': metadata['payload'], 'manifest': manifest,
                                     'metadata': metadata['metadata'], 'aarch64_sample': info}
        for project in ('art', 'packages/modules/NeuralNetworks', 'packages/modules/RuntimeI18n', 'packages/modules/StatsD'):
            result['source_revisions'][project] = subprocess.run(
                ['git', '-C', str(android / project), 'rev-parse', 'HEAD'],
                capture_output=True, text=True, check=True, timeout=15).stdout.strip()
        result['passed'] = True
        result['errors'] = []
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        result['errors'] = [str(exc)]
        (output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
        raise
    (output / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        inspect(args.android_root.resolve(), args.output_dir.absolute())
        print('Provider inputs checked: ' + str(args.output_dir.absolute() / 'report.json'))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'Provider input check failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
