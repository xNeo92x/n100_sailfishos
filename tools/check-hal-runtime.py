#!/usr/bin/env python3
"""Check explicitly built HAL services and their init files before RPM packaging."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('hal_runtime_elf', ROOT / 'tools/inspect-android-output.py')
inventory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inventory)
SERVICES = ('hwservicemanager', 'logd')
APEX = 'system/apex/com.android.runtime.apex'
EXTRA_ETC = ('sanitizer.libraries.txt', 'vndkcorevariant.libraries.txt')


def inspect(android_root):
    product = (Path(android_root) / 'out/target/product/billie2').resolve()
    files, errors = {}, []
    required = ['system/bin/' + n for n in SERVICES] + ['system/etc/init/' + n + '.rc' for n in SERVICES] + [APEX]
    # DHD can also copy the static tool from the flattened runtime APEX.
    linkerconfig = next((p for p in ('system/bin/linkerconfig', 'apex/com.android.runtime/bin/linkerconfig')
                         if (product / p).is_file() and not (product / p).is_symlink()), 'system/bin/linkerconfig')
    required += [linkerconfig]
    required += ['system/etc/' + n for n in EXTRA_ETC]
    for relative in required:
        path = product / relative
        if not inventory.within(path, product) or path.is_symlink() or not path.is_file():
            errors.append('Missing regular build output: ' + relative)
            continue
        data = path.read_bytes()
        if not data and '/etc/' not in relative:
            errors.append('Empty executable or APEX output: ' + relative)
        record = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if '/bin/' in relative:
            record.update(inventory.elf_details(path))
            if record.get('elf_class') != 2 or record.get('elf_machine') != 183 or record.get('elf_type') not in (2, 3) or record.get('elf_error'):
                errors.append('Expected a valid AArch64 executable: ' + relative)
            elif relative == linkerconfig:
                if not record.get('static_executable'):
                    errors.append('Expected the static linkerconfig executable: ' + relative)
            elif record.get('elf_interpreter') not in ('/system/bin/linker64', '/apex/com.android.runtime/bin/linker64'):
                errors.append('Unexpected Android service interpreter: ' + relative)
        elif relative.endswith('.rc'):
            service = path.stem
            text = data.decode('utf-8')
            expected = r'^service\s+' + re.escape(service) + r'\s+/(?:system/bin|usr/libexec/droid-hybris/system/bin)/' + re.escape(service) + r'(?:\s|$)'
            if not re.search(expected, text, re.M):
                errors.append('Missing expected service declaration: ' + relative)
        elif relative == APEX:
            try:
                with zipfile.ZipFile(path) as archive:
                    if archive.namelist().count('apex_payload.img') != 1 or archive.getinfo('apex_payload.img').file_size <= 0:
                        errors.append('Runtime APEX payload missing or ambiguous')
            except zipfile.BadZipFile:
                errors.append('Runtime APEX is not a ZIP container')
        files[relative] = record
    return {'schema': 1, 'kind': 'hal-runtime-build-outputs', 'product': str(product),
            'files': files, 'errors': errors, 'passed': not errors,
            'system_linker_config_pb_present': (product / 'system/etc/linker.config.pb').is_file(),
            'scope': 'Build output ABI/layout only; dependency closure, signatures and device runtime unvalidated.',
            'phone_modified': False, 'rpm_rebuilt': False, 'full_os_boot_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = inspect(args.android_root)
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + '\n')
            print('Runtime build report: {}'.format(args.output.resolve()))
        for error in result['errors']:
            print('ERROR: ' + error)
        if not result['passed']:
            print('In Ubuntu HABUILD_SDK run: bash "$N100_PORT/tools/build-hal.sh" runtime')
            return 1
        print('HAL runtime service ABI, init declarations and APEX inputs checked; device execution remains pending.')
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, 'HAL runtime check failed: {}\n'.format(exc))


if __name__ == '__main__':
    raise SystemExit(main())
