#!/usr/bin/env python3
"""Prepare runtime service probes: the device-tested V2 view with the HAL tree of the new
SELinux/GLES RPM, plus a bounded binderfs + servicemanager/hwservicemanager start in RAM.

V4 (device-run 2026-11-11): servicemanager aborted because selinux_status_open() fails
without selinuxfs. V5 additionally mounts selinuxfs in the view without loading a policy."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


vp = load('runtime_service_v2', 'tools/prepare-vendor-probe.py')
rpmtool = load('runtime_service_rpm', 'tools/prepare-runtime-rpm-probe.py')
compare = load('runtime_service_compare', 'tools/check-gles-hal-rpm.py')
require = vp.require
sha = vp.sha

RPM_REPORT = ROOT / 'validation/selinux-hal-rpm-user-20261111.json'
RPM_REPORT_SHA = '397f215f0a6e193a64da0bd65cdd99dba8b8cb6cd992fa40da62d365fb1ae000'
V2_SCRIPT_SHA = '1aff4719a18e0d6294aceb2d683fb505f5c4c1b0a2cbb267b7553ecfb8a37df6'
VERSION = 5


def script_name(version):
    return 'runtime-probe-v{}.sh'.format(version)


NEW_FILES = ('lib64/libGLESv3.so', 'etc/selinux/plat_service_contexts',
             'etc/selinux/plat_hwservice_contexts', 'etc/selinux/plat_property_contexts')
SERVICES = ('servicemanager', 'hwservicemanager')
BANNER = '=== Android runtime service start probe (RAM only) ==='
END = 'N100_RUNTIME_SECTION_END'


def rpm_pins():
    raw = RPM_REPORT.read_bytes()
    require(sha(raw) == RPM_REPORT_SHA, 'Reviewed SELinux HAL RPM report changed')
    report = json.loads(raw)
    require(report['passed'] is True and report['selinux_contexts_expected'] is True and not report['problems'],
            'SELinux HAL RPM comparison did not pass')
    added = {name[len(compare.PREFIX):]: entry for name, entry in report['added'].items()}
    require(all(name in added and added[name]['type'] == 'file' for name in NEW_FILES), 'Unexpected RPM additions')
    return report['new_rpm'], {name: {'bytes': added[name]['bytes'], 'sha256': added[name]['sha256']} for name in NEW_FILES}


def build_hal(rpm_path, v2_archive, output):
    """Write the AArch64 HAL tree of the new RPM; it must equal the V2 tree plus the four new files."""
    package, pins = rpm_pins()
    require(rpm_path.stat().st_size == package['bytes'] and compare.file_sha(rpm_path) == package['sha256'],
            'New HAL RPM differs from the compared package')
    records = rpmtool.cpio_tree(compare.payload(rpm_path))
    tree = rpmtool.write_hal_archive(records, output, expected=pins)
    with tarfile.open(v2_archive) as old, tarfile.open(output) as new:
        old_list = old.extractfile('n100-files.sha256').read().decode().splitlines()
        new_list = new.extractfile('n100-files.sha256').read().decode().splitlines()
        old_links = {m.name: m.linkname for m in old.getmembers() if m.issym()}
        new_links = {m.name: m.linkname for m in new.getmembers() if m.issym()}
    expected = sorted(old_list + ['{}  {}'.format(pins[n]['sha256'], n) for n in NEW_FILES], key=lambda l: l.split('  ', 1)[1])
    require(new_list == expected, 'New HAL tree is not the V2 tree plus the four reviewed files')
    require(new_links == old_links, 'HAL symlinks differ from the device-tested tree')
    return {'bytes': output.stat().st_size, 'sha256': compare.file_sha(output),
            'regular_files': len(tree['regular_file_hashes']), 'added_files': pins, 'source_rpm': package}


def replace_once(text, old, new):
    require(text.count(old) == 1, 'V2 script anchor not unique: ' + old.splitlines()[0])
    return text.replace(old, new)


RUNTIME = r'''
echo '=== Android runtime service start probe (RAM only) ==='
echo '--- Context files in the temporary view ---'
ls -l "$P/system/etc/selinux" "$P/vendor/etc/selinux" 2>&1 || :
for leaf in binder hwbinder vndbinder binderfs; do
    [ ! -e "/dev/$leaf" ] && [ ! -L "/dev/$leaf" ] || die "Unexpected existing /dev/$leaf"
done
grep -qw binder /proc/filesystems || die 'binderfs filesystem type not available'
mkdir /dev/binderfs
binderfs_dir=/dev/binderfs
mounts="$P/dev/binderfs $mounts"
if mount -t binder -o stats=global binder "$P/dev/binderfs" 2>"$work/binderfs.err"; then
    echo 'N100_RUNTIME_BINDERFS_MOUNTED stats=global'
else
    cat "$work/binderfs.err"
    mount -t binder binder "$P/dev/binderfs"
    echo 'N100_RUNTIME_BINDERFS_MOUNTED stats=none'
fi
ls -l "$P/dev/binderfs"
for leaf in binder hwbinder vndbinder; do
    [ -c "$P/dev/binderfs/$leaf" ] || die "binderfs device missing: $leaf"
    ln -s "binderfs/$leaf" "/dev/$leaf"
    dev_links="/dev/$leaf $dev_links"
done
echo 'N100_RUNTIME_BINDER_NODES_READY'
@SELINUXFS@dmesg | grep -i binder | tail -n 10 || :
for name in servicemanager hwservicemanager; do
    chroot "$P" /bin/sh -c 'unset LD_LIBRARY_PATH LD_PRELOAD LD_CONFIG_FILE; exec "$1"' n100-svc "/system/bin/$name" \
        > "$work/$name.out" 2>&1 &
    pid=$!
    started_pids="$pid $started_pids"
    eval "pid_$name=$pid"
    echo "N100_RUNTIME_STARTED $name"
    sleep 2
done
for check in 3s 6s; do
    sleep 3
    for name in servicemanager hwservicemanager; do
        eval "pid=\$pid_$name"
        if n100_alive "$pid"; then state=1; else state=0; fi
        echo "N100_RUNTIME_ALIVE $name $check $state"
        if [ "$check" = 3s ] && [ "$state" = 1 ]; then
            fds=$(ls -l "/proc/$pid/fd" 2>/dev/null | grep -c binder || :)
            echo "N100_RUNTIME_BINDER_FDS $name ${fds:-0}"
        fi
    done
done
if [ -r "$P/dev/binderfs/binder_logs/state" ]; then
    echo '--- binder_logs/state (first 150 lines) ---'
    head -n 150 "$P/dev/binderfs/binder_logs/state" || :
fi
for name in servicemanager hwservicemanager; do
    eval "pid=\$pid_$name"
    if n100_alive "$pid"; then
        kill -TERM "$pid" 2>/dev/null || :
        n=0
        while n100_alive "$pid" && [ "$n" -lt 5 ]; do sleep 1; n=$((n + 1)); done
        if n100_alive "$pid"; then kill -KILL "$pid" 2>/dev/null || :; sleep 1; fi
    fi
    if n100_alive "$pid"; then
        echo "N100_RUNTIME_STOP_FAILED $name"
    else
        if wait "$pid"; then code=0; else code=$?; fi
        echo "N100_RUNTIME_EXIT $name $code"
    fi
    echo "--- $name output ---"
    head -c 8192 "$work/$name.out" || :
done
dmesg | grep -iE 'binder|servicemanager' | tail -n 20 || :
echo 'N100_RUNTIME_SECTION_END'
'''

# V5: servicemanager CHECKs selinux_status_open(), which needs a mounted selinuxfs. Without a
# loaded policy the kernel maps every context to "kernel" and allows every access.
SELINUXFS = r'''[ -d "$P/sys/fs/selinux" ] || die 'Kernel offers no /sys/fs/selinux mount point'
! mounted "$P/sys/fs/selinux" || die 'selinuxfs is already mounted in the view'
if grep -q ' /sys/fs/selinux ' /proc/mounts; then echo 'N100_RUNTIME_HOST_SELINUXFS_PRESENT'; fi
mounts="$P/sys/fs/selinux $mounts"
mount -t selinuxfs selinuxfs "$P/sys/fs/selinux"
[ ! -e "$P/sys/fs/selinux/class/binder" ] || die 'An SELinux policy is already loaded; this probe assumes none'
echo "N100_RUNTIME_SELINUXFS_MOUNTED enforce=$(cat "$P/sys/fs/selinux/enforce" 2>/dev/null || echo unknown) deny_unknown=$(cat "$P/sys/fs/selinux/deny_unknown" 2>/dev/null || echo unknown)"
'''

CLEANUP = r'''    failed=0
    # Stop services started by this probe before any view is unmounted.
    for pid in $started_pids; do
        if n100_alive "$pid"; then kill -TERM "$pid" 2>/dev/null; fi
    done
    for pid in $started_pids; do
        n=0
        while n100_alive "$pid" && [ "$n" -lt 5 ]; do sleep 1; n=$((n + 1)); done
        if n100_alive "$pid"; then kill -KILL "$pid" 2>/dev/null; sleep 1; fi
        if n100_alive "$pid"; then failed=1; echo "N100_RUNTIME_CLEANUP_STUCK $pid"; else wait "$pid" 2>/dev/null; fi
    done
'''

LINKS = r'''    for link in $dev_links; do
        if [ -L "$link" ] && ! rm -f "$link"; then failed=1; fi
    done
    if [ -n "$binderfs_dir" ] && [ -d "$binderfs_dir" ]; then
        if mounted "$P/dev/binderfs" || ! rmdir "$binderfs_dir"; then failed=1; fi
    fi
    if [ "$failed" = 0 ]; then
'''


def render(report, manifest, hal, version=VERSION):
    script = vp.render(report, manifest)
    require(sha(script.encode()) == V2_SCRIPT_SHA, 'Device-tested V2 script changed')
    script = replace_once(script, "# MIT. Render with prepare-vendor-probe.py; temporary RAM view only.\n",
        "# MIT. Rendered by prepare-runtime-service-probe.py from the device-tested V2 probe.\n"
        "# V{}: HAL tree of the SELinux/GLES RPM; starts servicemanager/hwservicemanager in RAM only.\n".format(version))
    script = replace_once(script, "HAL_SHA='{}'\nHAL_BYTES={}\n".format(manifest['hal_archive_sha256'], manifest['hal_archive_bytes']),
                          "HAL_SHA='{}'\nHAL_BYTES={}\n".format(hal['sha256'], hal['bytes']))
    script = replace_once(script, "Usage: vendor-probe.sh --plan", "Usage: {} --plan".format(script_name(version)))
    script = replace_once(script, 'mounted() { grep -F " $1 " /proc/mounts >/dev/null; }\n',
        'mounted() { grep -F " $1 " /proc/mounts >/dev/null; }\n'
        "n100_alive() { [ -n \"$1\" ] && [ -r \"/proc/$1/status\" ] && ! grep -q '^State:[[:space:]]*Z' \"/proc/$1/status\"; }\n")
    script = replace_once(script, "vendor_mounts=\nvendor_map=\n",
                          "vendor_mounts=\nvendor_map=\nstarted_pids=\ndev_links=\nbinderfs_dir=\n")
    script = replace_once(script, "    failed=0\n    # DM removal still needs", CLEANUP + "    # DM removal still needs")
    script = replace_once(script, '    if [ "$failed" = 0 ]; then\n        rm -rf "$work/hal"\n', LINKS + '        rm -rf "$work/hal"\n')
    script = replace_once(script, "echo 'N100_VENDOR_PROBE_VERSION 2'\n", "echo 'N100_VENDOR_PROBE_VERSION {}'\n".format(version))
    script = replace_once(script, "        echo \"N100_VENDOR_SKIPPED other-ABI-or-format $path\"\n",
        "        echo \"N100_VENDOR_SKIPPED other-ABI-or-format $path\"\n"
        "        echo \"N100_VENDOR_SKIPPED_IDENTITY $path $identity\"\n")
    script = replace_once(script,
        "echo 'Only linker help, linkerconfig help and dependency listing were requested; no HAL services started.'\n",
        RUNTIME.lstrip('\n').replace('@SELINUXFS@', SELINUXFS if version >= 5 else '') +
        "echo 'Only servicemanager and hwservicemanager were started, as root, for a few seconds; no vendor HAL started.'\n")
    require(not re.search(r'@[A-Z_]+@', script), 'Unresolved template token')
    return script


def bundle_manifest(report, manifest, hal, version=VERSION):
    bundle = vp.bundle_manifest(report, manifest)
    bundle.update(kind='temporary-runtime-service-start-probe', probe_version=version, script=script_name(version),
                  source_vendor_v2_script_sha256=V2_SCRIPT_SHA, hal_archive_bytes=hal['bytes'],
                  hal_archive_sha256=hal['sha256'], hal_tree=hal, script_sha256=sha(render(report, manifest, hal, version).encode()),
                  source_rpm_report_sha256=RPM_REPORT_SHA,
                  scope='V2 view with the new HAL tree; binderfs' + (' and selinuxfs (no policy)' if version >= 5 else '') +
                        ' in RAM; servicemanager and hwservicemanager '
                        'started as root for about ten seconds; no vendor HAL, logd, init or systemd')
    return bundle


def verify_bundle(directory, report, manifest):
    saved = json.loads((directory / 'manifest.json').read_text())
    hal, version = saved['hal_tree'], saved['probe_version']
    bundle = bundle_manifest(report, manifest, hal, version)
    require(saved == bundle, 'V4 probe manifest changed')
    vp.ns.native.verify_file(directory / 'apex_payload.img', manifest['payload']['bytes'], manifest['payload']['sha256'])
    pin = bundle['vndk_payload']; vp.ns.native.verify_file(directory / 'vndk_payload.img', pin['bytes'], pin['sha256'])
    for name, pin in bundle['configuration_files'].items():
        vp.ns.native.verify_file(directory / 'linkerconfig' / name, pin['bytes'], pin['sha256'])
    vp.ns.native.verify_file(directory / 'hal-system.tar.gz', hal['bytes'], hal['sha256'])
    script = render(report, manifest, hal, version).encode()
    vp.ns.native.verify_file(directory / script_name(version), len(script), sha(script))
    return bundle


def prepare(v2_bundle, rpm_path, output, version=VERSION):
    report, manifest = vp.reference()
    require(not output.exists() and not output.is_symlink(), 'Output exists; choose a new V4 probe directory')
    vp.verify_bundle(v2_bundle, report, manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.runtime-probe-v4-', dir=str(output.parent)))
    try:
        hal = build_hal(rpm_path, v2_bundle / 'hal-system.tar.gz', staging / 'hal-system.tar.gz')
        for name in ['apex_payload.img', 'vndk_payload.img'] + ['linkerconfig/' + n for n in report['generated']['files']]:
            target = staging / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(v2_bundle / name, target)
        script = render(report, manifest, hal, version)
        subprocess.run(['sh', '-n'], input=script, text=True, check=True)
        name = script_name(version)
        (staging / name).write_text(script); (staging / name).chmod(0o755)
        bundle = bundle_manifest(report, manifest, hal, version)
        (staging / 'manifest.json').write_text(json.dumps(bundle, indent=2) + '\n')
        verify_bundle(staging, report, manifest)
        staging.rename(output)
    finally:
        if staging.exists(): shutil.rmtree(staging)
    print('Prepared runtime service probe V{}: {}'.format(version, output))
    print(bundle['script'] + ' SHA-256: ' + bundle['script_sha256'])
    return bundle


def runtime_section(log):
    if log.count(BANNER) != 1 or log.count(END + '\n') != 1: return None
    return log.split(BANNER, 1)[1].split(END, 1)[0]


def classify(log, report, manifest, version=VERSION):
    lines = log.splitlines()
    section = runtime_section(log)
    # The repeated system and vendor dependency parts are judged by the reviewed V2 classifier.
    if section is not None:
        before, after = log.split(BANNER, 1)[0], log.split(END, 1)[1]
        base_log = before + after
    else:
        base_log = log
    base_log = base_log.replace('N100_VENDOR_PROBE_VERSION {}'.format(version), 'N100_VENDOR_PROBE_VERSION 2')
    base_log = '\n'.join(l for l in base_log.splitlines()
                         if not l.startswith(('N100_VENDOR_SKIPPED_IDENTITY ', 'N100_RUNTIME_CLEANUP_STUCK'))) + '\n'
    base = vp.classify(base_log, report, manifest)
    runtime = {'section_complete': section is not None, 'binderfs_mounted': None, 'binder_nodes_ready': False,
               'selinuxfs_mounted': None, 'services': {}}
    if section is not None:
        rlines = section.splitlines()
        mounted = [l for l in rlines if l.startswith('N100_RUNTIME_BINDERFS_MOUNTED ')]
        runtime['binderfs_mounted'] = mounted[0].split(' ', 1)[1] if len(mounted) == 1 else None
        runtime['binder_nodes_ready'] = rlines.count('N100_RUNTIME_BINDER_NODES_READY') == 1
        selinuxfs = [l for l in rlines if l.startswith('N100_RUNTIME_SELINUXFS_MOUNTED ')]
        runtime['selinuxfs_mounted'] = selinuxfs[0].split(' ', 1)[1] if len(selinuxfs) == 1 else None
        for name in SERVICES:
            info = {'started': rlines.count('N100_RUNTIME_STARTED ' + name) == 1}
            for check in ('3s', '6s'):
                found = [l for l in rlines if l.startswith('N100_RUNTIME_ALIVE {} {} '.format(name, check))]
                info['alive_' + check] = found[0].endswith(' 1') if len(found) == 1 else None
            fds = [l for l in rlines if l.startswith('N100_RUNTIME_BINDER_FDS {} '.format(name))]
            info['binder_fds'] = int(fds[0].rsplit(' ', 1)[1]) if len(fds) == 1 and fds[0].rsplit(' ', 1)[1].isdigit() else None
            exits = [l for l in rlines if l.startswith('N100_RUNTIME_EXIT {} '.format(name))]
            info['exit_code'] = int(exits[0].rsplit(' ', 1)[1]) if len(exits) == 1 else None
            info['stop_failed'] = ('N100_RUNTIME_STOP_FAILED ' + name) in rlines
            info['stayed_up'] = bool(info['started'] and info['alive_3s'] and info['alive_6s'] and
                                     (info['binder_fds'] or 0) > 0 and not info['stop_failed'])
            runtime['services'][name] = info
    stuck = [l for l in lines if l.startswith('N100_RUNTIME_CLEANUP_STUCK')]
    cleanup_ok = any(re.fullmatch(r'N100_VENDOR_PROBE_RESULT exit=[0-9]{1,3} cleanup_failed=0', l) for l in lines) and not stuck
    service_passed = (runtime['binderfs_mounted'] is not None and runtime['binder_nodes_ready'] and cleanup_ok and
                      (version < 5 or runtime['selinuxfs_mounted'] is not None) and
                      lines.count('N100_VENDOR_PROBE_VERSION {}'.format(version)) == 1 and
                      all(runtime['services'].get(n, {}).get('stayed_up') for n in SERVICES))
    return {'runtime_service_start_passed': service_passed, 'cleanup_ok': cleanup_ok, 'runtime': runtime,
            'probe_version': version, 'version_marker_present': lines.count('N100_VENDOR_PROBE_VERSION {}'.format(version)) == 1,
            'system_service_dependency_probe_passed': base['system_service_dependency_probe_passed'],
            'selected_vendor_dependency_probe_passed': base['selected_vendor_dependency_probe_passed'],
            'vendor_dependency_exit_codes': base['vendor_dependency_exit_codes'],
            'skipped_program_identities': [l for l in lines if l.startswith('N100_VENDOR_SKIPPED_IDENTITY ')],
            'base_assessment': base,
            'scope': 'Two Android service managers started as root in a RAM view for ~10 s; no property service, '
                     'logd, vendor HAL, droid-hal-init or systemd. Staying up is not proof of full function.'}


def collect(bundle_dir, log_path, output):
    report, manifest = vp.reference(); bundle = verify_bundle(bundle_dir, report, manifest)
    require(log_path.is_file() and 0 < log_path.stat().st_size <= 2 * 1024 * 1024, 'Expected a bounded probe log')
    log = log_path.read_text(errors='replace')
    result = {'schema': 1, 'kind': 'runtime-service-start-device-probe-report', 'bundle': bundle,
              'bundle_integrity_rechecked': True, 'device_probe_log': log,
              'assessment': classify(log, report, manifest, bundle['probe_version']),
              'rootfs_boot_tested': False, 'flashable_release': False}
    with output.open('x') as stream: stream.write(json.dumps(result, indent=2) + '\n')
    a = result['assessment']
    print('Report: ' + str(output))
    print('Service managers stayed up: ' + str(a['runtime_service_start_passed']))
    for name, info in a['runtime']['services'].items():
        print('  {}: started={} alive_3s={} alive_6s={} binder_fds={} exit={}'.format(
            name, info['started'], info['alive_3s'], info['alive_6s'], info['binder_fds'], info['exit_code']))
    print('Dependency lists (system/vendor): {} / {}'.format(a['system_service_dependency_probe_passed'],
                                                            a['selected_vendor_dependency_probe_passed']))
    print('Cleanup OK: ' + str(a['cleanup_ok']))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--v2-bundle', type=Path, required=True)
    prep.add_argument('--rpm', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    prep.add_argument('--version', type=int, choices=(4, 5), default=VERSION)
    capture = commands.add_parser('report')
    capture.add_argument('--bundle-dir', type=Path, required=True)
    capture.add_argument('--probe-log', type=Path, required=True)
    capture.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'prepare': prepare(args.v2_bundle, args.rpm, args.output_dir.absolute(), args.version)
        else: collect(args.bundle_dir, args.probe_log, args.output)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError) as exc:
        parser.exit(1, 'Runtime service probe preparation/report failed: {}\n'.format(exc))


if __name__ == '__main__': main()
