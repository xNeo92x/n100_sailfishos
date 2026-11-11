#!/bin/sh
# MIT. Read-only diagnostics for the first full Sailfish boot on the N100.
# Run as root in the Telnet shell of the booted rootfs (port 2323). Writes only to
# /tmp/n100-fullboot and serves that directory on 192.168.2.15:8080 for collection.
out=/tmp/n100-fullboot
mkdir -p "$out"
report="$out/diag.txt"
bb=/bin/busybox-static
section() {
    echo "=== $1 ==="
    shift
    "$bb" timeout 20 "$@" > "$out/section.tmp" 2>&1
    rc=$?
    "$bb" head -c 200000 "$out/section.tmp"
    echo "N100_FULLBOOT_EXIT $rc"
}
{
    echo 'N100_FULLBOOT_DIAG_VERSION 1'
    section uptime cat /proc/uptime
    section kernel uname -a
    section os-release cat /etc/os-release
    section cmdline cat /proc/cmdline
    section system-running systemctl is-system-running
    section failed-units systemctl --failed --no-pager --plain
    section droid-hal-init systemctl status droid-hal-init --no-pager -l
    section binderfs-unit systemctl status dev-binderfs.mount --no-pager -l
    section mounts "$bb" grep -E 'binder|selinux| /system | /vendor | /odm | /apex| /data ' /proc/mounts
    section binder-nodes "$bb" ls -l /dev/binder /dev/hwbinder /dev/vndbinder /dev/binderfs /dev/__properties__
    section android-processes "$bb" sh -c "ps -eo pid,user,stat,args | grep -E 'servicemanager|hwservicemanager|vndservicemanager|droid-hal-init|/vendor/bin|logd' | grep -v grep"
    section service-list /system/bin/service list
    section binder-state "$bb" head -n 200 /dev/binderfs/binder_logs/state
    section logcat /system/bin/logcat -d -b all -v brief
    section journal-droid journalctl -b --no-pager -u droid-hal-init
    section journal-errors journalctl -b --no-pager -p warning
    section dmesg-tail "$bb" sh -c 'dmesg | tail -n 400'
    echo 'N100_FULLBOOT_DIAG_DONE'
} > "$report" 2>&1
"$bb" httpd -p 192.168.2.15:8080 -h "$out" 2>/dev/null || :
echo "Bericht: $report  ($("$bb" wc -c < "$report") Bytes)"
echo 'N100_READY_FOR_COLLECT'
