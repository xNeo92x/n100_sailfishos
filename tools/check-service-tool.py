#!/usr/bin/env python3
"""Check the built AOSP `service` client and stage it for the runtime probe.

The binary must be an AArch64 PIE using the Android linker, and every DT_NEEDED
library must exist in the device-tested HAL tree (V5 bundle) or be Bionic. Only
the binary is staged; it is a diagnostic client and is not packaged in an RPM.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

BIONIC = {'libc.so', 'libm.so', 'libdl.so'}


def require(condition, message):
    if not condition: raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def elf(path):
    header = subprocess.run(['readelf', '-hW', str(path)], capture_output=True, text=True, check=True).stdout
    program = subprocess.run(['readelf', '-lW', str(path)], capture_output=True, text=True, check=True).stdout
    dynamic = subprocess.run(['readelf', '-dW', str(path)], capture_output=True, text=True, check=True).stdout
    interp = re.search(r'Requesting program interpreter: ([^\]]+)\]', program)
    return {'class64': 'ELF64' in header, 'aarch64': 'AArch64' in header, 'type_dyn': 'DYN' in header,
            'interpreter': interp.group(1) if interp else None,
            'needed': re.findall(r'\(NEEDED\)\s+Shared library: \[([^\]]+)\]', dynamic)}


def hal_libraries(archive):
    with tarfile.open(archive) as tree:
        listing = tree.extractfile('n100-files.sha256').read().decode().splitlines()
        links = {m.name for m in tree.getmembers() if m.issym()}
    files = {line.split('  ', 1)[1] for line in listing}
    return {Path(p).name for p in files | links if p.startswith('lib64/') and p.count('/') == 1}


def check(binary, hal_archive, output):
    require(binary.is_file() and not binary.is_symlink(), 'Built service binary missing: ' + str(binary))
    require(not output.exists(), 'Output directory exists; choose a new one')
    info = elf(binary)
    available = hal_libraries(hal_archive)
    missing = [n for n in info['needed'] if n not in available and n not in BIONIC]
    problems = []
    if not (info['class64'] and info['aarch64'] and info['type_dyn']): problems.append('not an AArch64 PIE')
    if info['interpreter'] not in ('/system/bin/linker64', '/apex/com.android.runtime/bin/linker64'):
        problems.append('unexpected interpreter: {}'.format(info['interpreter']))
    if 'libbinder.so' not in info['needed']: problems.append('libbinder.so not linked')
    if missing: problems.append('libraries missing from the HAL tree: ' + ', '.join(missing))
    output.mkdir(parents=True)
    report = {'schema': 1, 'kind': 'service-client-tool-build', 'passed': not problems, 'problems': problems,
              'binary': {'bytes': binary.stat().st_size, 'sha256': sha(binary), 'source_path': str(binary)},
              'elf': info, 'hal_archive_sha256': sha(hal_archive), 'packaged_in_rpm': False, 'device_tested': False}
    if not problems:
        shutil.copyfile(binary, output / 'service'); (output / 'service').chmod(0o755)
    (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--hal-archive', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    try:
        report = check(args.binary, args.hal_archive, args.output_dir)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as exc:
        parser.exit(1, 'service tool check failed: {}\n'.format(exc))
    for problem in report['problems']: print('PROBLEM: ' + problem)
    print('Report: ' + str(args.output_dir / 'report.json'))
    print('service tool check passed: ' + str(report['passed']))
    raise SystemExit(0 if report['passed'] else 1)


if __name__ == '__main__': main()
