#!/usr/bin/env python3
"""Disable Android DEX unwinding for the native HAL without changing TLS slots."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

EDITS = (
    ('system/unwinding/libunwindstack/Android.bp', 'cc_library', 'libunwindstack',
     '    target: {\n        vendor: {',
     '    target: {\n        // N100 native HAL: no ART/DEX stack unwinding.\n        android: {\n'
     '            cflags: ["-UDEXFILE_SUPPORT"],\n'
     '            exclude_srcs: ["DexFile.cpp"],\n'
     '            exclude_static_libs: ["libdexfile_support"],\n'
     '            exclude_runtime_libs: ["libdexfile"],\n'
     '        },\n        vendor: {'),
    ('system/core/debuggerd/Android.bp', 'cc_library_static', 'libdebuggerd',
     '    target: {\n        recovery: {',
     '    target: {\n        // N100 native HAL: match the no-DEX Android unwinder.\n        android: {\n'
     '            exclude_static_libs: ["libdexfile_support"],\n'
     '            exclude_runtime_libs: ["libdexfile"],\n'
     '        },\n        recovery: {'),
)
TLS = 'bionic/libc/platform/bionic/tls_defines.h'


def require(ok, message):
    if not ok: raise ValueError(message)


def info(path):
    require(path.is_file() and not path.is_symlink(), 'Expected regular source: ' + str(path))
    data = path.read_bytes()
    return {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def transform(text, kind, name, old, new):
    # Android.bp module closing braces are at column zero; nested blocks are indented.
    pattern = r'(?ms)^' + re.escape(kind) + r' \{\n    name: "' + re.escape(name) + r'",.*?^\}'
    found = list(re.finditer(pattern, text))
    require(len(found) == 1, 'Expected one source module: ' + name)
    match = found[0]; module = match.group()
    if new in module:
        require(module.count(new) == 1 and old not in module, 'Ambiguous existing no-DEX adaptation')
        return text, 'already-applied'
    require(module.count(old) == 1 and not re.search(r'^        android: \{', module, re.M),
            'Unexpected Android variant edits in ' + name + '; source was not overwritten')
    changed = module.replace(old, new, 1)
    return text[:match.start()] + changed + text[match.end():], 'applied'


def prepare(android):
    android = android.resolve()
    header = android / TLS
    protected = info(header)
    plans = []
    for path, kind, name, old, new in EDITS:
        source = android / path
        require(android in source.resolve().parents, 'Source path escapes Android tree')
        before = info(source)
        text, state = transform(source.read_text(), kind, name, old, new)
        plans.append((source, text, state, before))
    # Validate every edit before writing either file. Retry is safe after interruption.
    results = []
    for source, text, state, before in plans:
        if state == 'applied':
            mode = stat.S_IMODE(source.stat().st_mode)
            with tempfile.NamedTemporaryFile(mode='w', dir=str(source.parent), prefix='.n100-unwind-', delete=False) as target:
                temporary = Path(target.name); target.write(text)
            try:
                temporary.chmod(mode); os.replace(temporary, source)
            finally:
                if temporary.exists(): temporary.unlink()
        results.append({'source': str(source.relative_to(android)), 'state': state,
                        'before': before, 'after': info(source)})
    require(info(header) == protected, 'TLS header unexpectedly changed')
    report = {'schema': 1, 'kind': 'native-android-no-dex-unwind', 'passed': True,
              'protected_tls_header': protected, 'tls_header_modified': False, 'edits': results,
              'art_tls_slot_definitions': re.findall(r'(?m)^#define\s+TLS_SLOT_ART_THREAD_SELF[^\n]*', header.read_text()),
              'scope': 'Android variants only; retain native unwinding, disable DEX symbolization/runtime libdexfile dependency',
              'runtime_apex_built': False, 'phone_modified': False}
    (android / '.n100-native-unwind.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', required=True, type=Path)
    args = parser.parse_args()
    try:
        prepare(args.android_root)
        print('Android native no-DEX unwinding prepared. Bionic TLS header unchanged.')
    except (OSError, ValueError) as exc:
        parser.exit(1, 'Native unwind preparation failed: {}\n'.format(exc))


if __name__ == '__main__': main()
