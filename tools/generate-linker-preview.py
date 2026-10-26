#!/usr/bin/env python3
"""Generate an isolated AArch64 HAL linker preview on the PC; never install it."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import resource
import shutil
import subprocess
import tarfile
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('namespace_inputs', ROOT / 'tools/check-linker-inputs.py')
inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inputs)
LIMIT = 384 * 1024 * 1024


def require(ok, message):
    if not ok:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def checked_file(path, pin):
    require(not path.is_symlink() and path.is_file(), 'Expected regular input: ' + str(path))
    info = inputs.file_info(path)
    require((info['bytes'], info['sha256']) == (pin['bytes'], pin['sha256']),
            'Input differs from reviewed report: ' + str(path))
    return info


def unpack_hal(archive, output, bundle):
    checked_file(archive, {'bytes': bundle['hal_archive_bytes'], 'sha256': bundle['hal_archive_sha256']})
    tree = bundle['hal_tree']
    expected = tree['regular_file_hashes']
    links = tree['symlinks']
    output.mkdir(parents=True)
    with tarfile.open(archive, 'r:gz') as source:
        members = source.getmembers()
        require(len(members) <= 5000, 'Too many HAL archive entries')
        entries = {}
        for member in members:
            name = member.name
            require(name not in ('', '.') and name == str(PurePosixPath(name)) and not name.startswith('/') and
                    '..' not in PurePosixPath(name).parts and
                    re.fullmatch(r'[A-Za-z0-9_./@+:=-]+', name), 'Unsafe HAL archive path')
            require(name not in entries, 'Duplicate HAL archive path')
            require(member.isdir() or member.isfile() or member.issym(), 'Unsupported HAL archive type')
            entries[name] = member
        require(sum(m.size for m in members if m.isfile()) <= LIMIT, 'HAL archive exceeds bound')
        regular = {n for n, m in entries.items() if m.isfile()}
        require(regular == set(expected) | {'n100-files.sha256'}, 'HAL regular file inventory differs')
        require({n: m.linkname for n, m in entries.items() if m.issym()} == links, 'HAL symlinks differ')
        for name in entries:
            for parent in PurePosixPath(name).parents:
                require(str(parent) not in entries or entries[str(parent)].isdir(), 'HAL archive parent is not a directory')
        # Write all files before creating any links, without tar.extractall or host link traversal.
        for name, member in sorted(entries.items(), key=lambda item: (item[1].issym(), item[0])):
            dest = output / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                dest.mkdir(exist_ok=True)
            elif member.isfile():
                with source.extractfile(member) as stream, dest.open('xb') as target:
                    shutil.copyfileobj(stream, target, 1024 * 1024)
                if name in expected:
                    require(inputs.file_info(dest)['sha256'] == expected[name], 'HAL content hash mismatch: ' + name)
                dest.chmod(member.mode & 0o777)
            else:
                require(not any(ord(c) < 32 for c in member.linkname), 'Control character in HAL symlink')
                dest.symlink_to(member.linkname)
    return {'regular_files': len(expected), 'unpacked_regular_bytes': tree['unpacked_regular_bytes']}


def image_stat(debugfs, image, name):
    result = subprocess.run([debugfs, '-R', 'stat /' + name, str(image)],
                            capture_output=True, text=True, check=True, timeout=30)
    if 'Type: directory' in result.stdout:
        return 'directory'
    if 'Type: regular' in result.stdout:
        return 'regular'
    if 'File not found' in result.stderr:
        return None
    raise ValueError('Cannot inspect APEX payload path: ' + name + '\n' + result.stdout + result.stderr)


def stage_apex(package, pin, payload_pin, name, root, payloads, debugfs, list_pins=None):
    checked_file(package, pin)
    image = payloads / (name + '.img')
    dest = root / 'apex' / name
    dest.mkdir(parents=True)
    with zipfile.ZipFile(package) as source:
        for key in ('apex_payload.img', 'apex_manifest.pb'):
            require(source.namelist().count(key) == 1, 'Missing or duplicate APEX ZIP member')
        require(source.getinfo('apex_manifest.pb').file_size <= 65536, 'Oversized APEX manifest')
        manifest = source.read('apex_manifest.pb')
        identity = inputs.manifest_info(manifest, expected_name=name, include_libraries=True)
        require(source.getinfo('apex_payload.img').file_size == payload_pin['bytes'], 'APEX payload size differs')
        with source.open('apex_payload.img') as stream, image.open('xb') as target:
            shutil.copyfileobj(stream, target, 1024 * 1024)
    checked_file(image, payload_pin)
    with image.open('rb') as stream:
        stream.seek(1080)
        require(stream.read(2) == b'\x53\xef', 'Expected raw ext4 APEX payload')
    # Extract only metadata read by linkerconfig; do not simulate complete APEX contents.
    inputs.dump_file(debugfs, image, 'apex_manifest.pb', dest / 'apex_manifest.pb')
    require((dest / 'apex_manifest.pb').read_bytes() == manifest, 'ZIP and payload APEX manifests differ')
    directories = []
    for directory in ('bin', 'lib', 'lib64'):
        kind = image_stat(debugfs, image, directory)
        if kind == 'directory':
            (dest / directory).mkdir(); directories.append(directory)
        else:
            require(kind is None, 'Unexpected APEX directory type')
    metadata = {'apex_manifest.pb': inputs.file_info(dest / 'apex_manifest.pb')}
    config = 'etc/linker.config.pb'
    if image_stat(debugfs, image, config) == 'regular':
        (dest / 'etc').mkdir(exist_ok=True)
        inputs.dump_file(debugfs, image, config, dest / config)
        require((dest / config).stat().st_size <= 65536, 'Oversized APEX linker config')
        metadata[config] = inputs.file_info(dest / config)
    if list_pins is not None:
        (dest / 'etc').mkdir(exist_ok=True)
        for label, item in list_pins.items():
            relative = 'etc/{}.libraries.30.txt'.format(label)
            inputs.dump_file(debugfs, image, relative, dest / relative)
            metadata[relative] = checked_file(dest / relative, item)
    checked_file(package, pin)
    return dict(identity, payload=inputs.file_info(image), metadata=metadata,
                directory_presence=directories, view='metadata-only; library/binary contents are not extracted')


def no_core_dump():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run_tool(command, log, must_succeed=True):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('ro.', 'persist.'))}
    result = subprocess.run([str(v) for v in command], capture_output=True, text=True,
                            timeout=120, env=env, preexec_fn=no_core_dump)
    text = result.stdout + result.stderr
    log.write_text(text)
    if must_succeed:
        require(result.returncode == 0, 'Host tool failed; see ' + str(log) + '\n' + text[-8000:])
    return {'command': [str(v) for v in command], 'exit': result.returncode,
            'log': str(log), 'output': text[:65536], 'output_truncated': len(text) > 65536}


def error_lines(text):
    """AOSP may log resolution errors and still return zero; inspect both channels."""
    pattern = r'(?i)(?:^\S+\s+[EF]\s+\d\d-\d\d\b|\bERROR\b|\bFATAL\b|not found:|duplicate:|Failed to|Unable to|undefined var|^error:)'
    return [line for line in text.splitlines() if re.search(pattern, line)]


def strict_diagnostic(result):
    diagnostic = dict(result)
    missing = re.findall(r'not found: (\S+) is required by (\S+) in \[([^]]+)\]', result['output'])
    errors = error_lines(result['output'])
    require(not result.get('output_truncated'), 'Strict diagnostic output was truncated')
    require(result['exit'] == 0 or missing, 'Strict generator failed for an unclassified reason; see logs/strict-generator.txt')
    require(not any('not found:' not in line for line in errors), 'Strict generator logged an unclassified error; see logs/strict-generator.txt')
    diagnostic['passed'] = result['exit'] == 0 and not missing and not errors
    diagnostic['error_lines'] = errors
    diagnostic['first_missing_requirements'] = [dict(library=a, namespace=b, section=c) for a, b, c in missing]
    diagnostic['exhaustive_missing_inventory'] = False
    return diagnostic


def read_config(text):
    """Parse linker key/value assignments, preserving append operations and mappings."""
    sections = {'': {}}
    current = sections['']
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('[') and line.endswith(']'):
            name = line[1:-1]
            require(name not in sections, 'Duplicate linker section')
            current = sections.setdefault(name, {})
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.]+)\s*(\+?=)\s*(.*)', line)
        require(match, 'Unrecognized linker configuration line: ' + line)
        key, op, value = match.groups()
        if op == '=':
            if current is sections[''] and key.startswith('dir.') and key in current:
                current[key] += ':' + value
            else:
                require(key not in current, 'Duplicate linker key: ' + key)
                current[key] = value
        else:
            require(key in current, 'Append without initial linker key: ' + key)
            current[key] += ':' + value
    return sections


def inspect_generated(folder, root):
    main = folder / 'ld.config.txt'
    require(main.is_file() and 0 < main.stat().st_size < 2 * 1024 * 1024, 'Missing or oversized ld.config.txt')
    text = main.read_text()
    config = read_config(text)
    require(all(name in config for name in ('system', 'vendor', 'unrestricted')), 'Treble linker sections missing')
    require('/system/bin/' in config[''].get('dir.system', ''), 'System executable mapping missing')
    require('/vendor/bin/' in config[''].get('dir.vendor', ''), 'Vendor executable mapping missing')
    for section in ('system', 'vendor'):
        require('/apex/com.android.vndk.v30/${LIB}' in config[section].get('namespace.vndk.search.paths', ''),
                'VNDK-30 namespace path missing in ' + section)
    require('/system/${LIB}' in config['system'].get('namespace.default.search.paths', ''), 'System search path missing')
    files = {}
    for path in sorted(folder.rglob('*')):
        if not path.is_file():
            continue
        require(not path.is_symlink() and path.stat().st_size < 2 * 1024 * 1024, 'Unexpected generated file')
        content = path.read_text()
        require(str(root) not in content and not re.search(r'/apex/com\.android\.vndk\.v(?!30\b)', content),
                'Generated config contains a host path or wrong VNDK version')
        files[str(path.relative_to(folder))] = dict(inputs.file_info(path), text=content)
    require('com.android.runtime/ld.config.txt' in files, 'Runtime APEX executable config missing')
    return {'sections': sorted(k for k in config if k), 'files': files,
            'host_path_leak': False, 'vndk_version': '30'}


def add_provider_inputs(report, provider_report, root, payloads, debugfs):
    payloads.mkdir(parents=True, exist_ok=True)
    providers = json.loads(provider_report.read_text())
    expected = {'com.android.art': 'libdexfile.so', 'com.android.neuralnetworks': 'libneuralnetworks.so',
                'com.android.i18n': 'libandroidicu.so', 'com.android.os.statsd': 'libstatssocket.so'}
    require(providers.get('kind') == 'native-linker-provider-inputs' and providers.get('passed') is True and
            set(providers.get('apexes', {})) == set(expected), 'Invalid or incomplete provider input report')
    for name, library in expected.items():
        entry = providers['apexes'][name]
        require(library in entry['manifest']['provide_native_libs'], 'Provider native export missing: ' + library)
        package = provider_report.parent / 'packages' / (name + '.apex')
        report['apexes'][name] = stage_apex(package, entry['package'], entry['payload'], name, root, payloads, debugfs)
        require(report['apexes'][name]['provide_native_libs'] == entry['manifest']['provide_native_libs'] and
                report['apexes'][name]['require_native_libs'] == entry['manifest']['require_native_libs'],
                'Provider native manifest differs from inspected input')
    report['provider_inputs'] = providers
    report['provider_report'] = inputs.file_info(provider_report)
    report['scope'] = 'AArch64 HAL RPM with Runtime/VNDK and ART/NNAPI/ICU/StatsD provider metadata; no stock vendor/product metadata'


def generate(android, bundle_dir, input_report, output, provider_report=None):
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new preview directory')
    reviewed = json.loads((ROOT / 'validation/linker-inputs-user-20261026.json').read_text())
    checked_file(input_report, inputs.file_info(ROOT / 'validation/linker-inputs-user-20261026.json'))
    require(reviewed['passed'] is True, 'Reviewed input report did not pass')
    bundle = json.loads((ROOT / 'validation/hal-rpm-device-report-user-20261026.json').read_text())['bundle']
    local_bundle = json.loads((bundle_dir / 'manifest.json').read_text())
    require(local_bundle == bundle, 'HAL bundle manifest differs from successful device test')
    product = android / 'out/target/product/billie2'
    host = android / 'out/host/linux-x86/bin'
    tools = {n: host / n for n in ('linkerconfig', 'conv_linker_config')}
    for name, path in tools.items():
        checked_file(path, reviewed['host_tools'][name])
    base = product / 'obj/ETC/system_linker_config_intermediates/system_linker_config'
    checked_file(base, reviewed['base_system_config_candidates'][0])
    debugfs = shutil.which('debugfs')
    require(debugfs, 'debugfs required; install e2fsprogs inside Ubuntu')
    output.mkdir(parents=True)
    report = {'schema': 1, 'kind': 'hal-linker-host-preview', 'passed': False,
              'scope': 'AArch64 HAL RPM plus Runtime/VNDK metadata; no stock vendor or product metadata',
              'phone_modified': False, 'rpm_rebuilt': False, 'full_os_boot_verified': False,
              'ready_for_installation': False, 'device_namespace_tested': False}
    report_path = output / 'report.json'
    try:
        root = output / 'root'
        logs = output / 'logs'; logs.mkdir()
        payloads = output / 'payloads'; payloads.mkdir()
        report['hal'] = unpack_hal(bundle_dir / 'hal-system.tar.gz', root / 'system', bundle)
        for directory in ('vendor', 'odm', 'product', 'system_ext'):
            (root / directory).mkdir()
        runtime_pin = bundle['hal_tree']['regular_file_hashes']['apex/com.android.runtime.apex']
        runtime_package = root / 'system/apex/com.android.runtime.apex'
        report['apexes'] = {}
        report['apexes']['com.android.runtime'] = stage_apex(
            runtime_package, {'bytes': runtime_package.stat().st_size, 'sha256': runtime_pin},
            {'bytes': bundle['payload_bytes'], 'sha256': bundle['payload_sha256']},
            'com.android.runtime', root, payloads, debugfs)
        vndk = product / 'system_ext/apex/com.android.vndk.v30.apex'
        report['apexes']['com.android.vndk.v30'] = stage_apex(
            vndk, reviewed['vndk_apex'], reviewed['vndk_apex']['payload'],
            'com.android.vndk.v30', root, payloads, debugfs, reviewed['vndk_apex']['library_lists'])
        if provider_report is not None:
            add_provider_inputs(report, provider_report, root, payloads, debugfs)
        info_list = ET.Element('apex-info-list')
        for name, info in report['apexes'].items():
            module = ('/system_ext/apex/' if name == 'com.android.vndk.v30' else '/system/apex/') + name + '.apex'
            ET.SubElement(info_list, 'apex-info', moduleName=name, modulePath=module,
                          preinstalledModulePath=module, versionCode=str(info['version']), versionName='',
                          isFactory='true', isActive='true', lastUpdateMillis='0', provideSharedApexLibs='false')
        ET.ElementTree(info_list).write(root / 'apex/apex-info-list.xml', encoding='utf-8', xml_declaration=True)
        # This native HAL model does not expose application-public library APIs.
        public = root / 'system/etc/public.libraries.txt'
        require(not public.exists() and not public.is_symlink(), 'Unexpected RPM public library list; review it first')
        public.write_text('')
        report['synthetic_inputs'] = {
            'apex_info_list': 'host model of the recorded payloads; not an apexd activation record',
            'empty_partitions': ['vendor', 'odm', 'product', 'system_ext'],
            'public_library_list': 'empty application-public list for native HAL preview; no stock list was substituted'}
        converter = tools['conv_linker_config']
        report['upstream_base'] = run_tool([converter, 'print', '--source', base], logs / 'upstream-system-config.txt')
        profile = ROOT / 'adaptation/linkerconfig/system.json'
        report['profile'] = dict(inputs.file_info(profile), content=json.loads(profile.read_text()))
        profile_base = output / 'hal-base.pb'
        commands = [run_tool([converter, 'proto', '--source', profile, '--output', profile_base], logs / 'proto.txt')]
        runtime_provides = set(report['apexes']['com.android.runtime']['provide_native_libs'])
        apex_provides = set().union(*(set(a['provide_native_libs']) for a in report['apexes'].values()))
        libs = sorted(p.name for p in (root / 'system/lib64').iterdir()
                      if p.name.endswith('.so') and p.is_file() and not p.is_symlink()
                      and p.name not in apex_provides)
        report['runtime_providers_excluded_from_system_provides'] = sorted(runtime_provides)
        report['apex_providers_excluded_from_system_provides'] = sorted(apex_provides)
        require(libs, 'No regular AArch64 system libraries')
        config_pb = root / 'system/etc/linker.config.pb'
        require(not config_pb.exists() and not config_pb.is_symlink(), 'RPM already has linker config; review before replacing')
        commands.append(run_tool([converter, 'systemprovide', '--source', profile_base,
                                  '--output', config_pb, '--value', ' '.join(libs), '--system', root / 'system'],
                                 logs / 'systemprovide.txt'))
        printed = run_tool([converter, 'print', '--source', config_pb], logs / 'hal-system-config.txt')
        provided = re.findall(r'^provideLibs:\s*"([^"]+)"', printed['output'], re.M)
        required = re.findall(r'^requireLibs:\s*"([^"]+)"', printed['output'], re.M)
        require(sorted(provided) == libs, 'Generated system provides differ from actual RPM library inventory')
        require(sorted(required) == sorted(report['profile']['content']['requireLibs']), 'HAL profile requirements differ')
        report['system_config'] = dict(inputs.file_info(config_pb), text=printed['output'],
                                       provide_libraries=libs, commands=commands)
        target = output / 'generated'; target.mkdir()
        command = [tools['linkerconfig'], '--root', root, '--vndk', '30', '--target', target]
        report['generator'] = run_tool(command, logs / 'generator.txt')
        require(not report['generator'].get('output_truncated') and not error_lines(report['generator']['output']),
                'Generator logged an input error; see logs/generator.txt')
        report['generated'] = inspect_generated(target, root)
        strict_target = output / 'strict-generated'; strict_target.mkdir()
        strict = run_tool([tools['linkerconfig'], '--strict', '--root', root, '--vndk', '30', '--target', strict_target],
                          logs / 'strict-generator.txt', must_succeed=False)
        strict = strict_diagnostic(strict)
        if strict['passed']:
            strict['generated'] = inspect_generated(strict_target, root)
        report['strict_diagnostic'] = strict
        report['input_report'] = inputs.file_info(input_report)
        report['source_rpm'] = bundle['source_rpm']
        report['source_revisions'] = reviewed['source_revisions']
        report['passed'] = True
        report['namespace_preview_generated'] = True
        report['stock_vendor_metadata_included'] = False
        report['limitations'] = [
            'Host generation only; all linker execution on device still untested with this configuration.',
            'Only AArch64 RPM files are modeled; 32-bit support is not validated.',
            'APEX directories contain metadata and directory markers, not extracted libraries.',
            'Stock vendor/odm/product metadata and automatic APEX/DM integration remain separate work.',
            'A normal-mode preview can exist even when the separate strict diagnostic fails.']
        # Recheck all executable and report sources after running them.
        for name, path in tools.items():
            checked_file(path, reviewed['host_tools'][name])
        checked_file(input_report, inputs.file_info(ROOT / 'validation/linker-inputs-user-20261026.json'))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        report.update(passed=False, namespace_preview_generated=False, errors=[str(exc)])
        report_path.write_text(json.dumps(report, indent=2) + '\n')
        raise
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--android-root', type=Path, required=True)
    parser.add_argument('--hal-bundle', type=Path, required=True)
    parser.add_argument('--input-report', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--provider-report', type=Path)
    args = parser.parse_args()
    try:
        report = generate(args.android_root.resolve(), args.hal_bundle.resolve(), args.input_report.resolve(), args.output_dir.absolute(),
                          args.provider_report.resolve() if args.provider_report else None)
        print('Host linker preview generated; strict_passed={}. No installation performed.'.format(report['strict_diagnostic']['passed']))
        print('Share ' + str(args.output_dir.absolute() / 'report.json'))
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as exc:
        parser.exit(1, 'Linker preview failed: {}\n'.format(exc))


if __name__ == '__main__':
    main()
