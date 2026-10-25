#!/bin/sh
# MIT. Read-only scratch probe for the reviewed BE2013 super snapshot only.
# No Android filesystem is mounted over the built Sailfish HAL payload.
set -eu

N100_TABLES='system_b 2961328 10486976
system_ext_b 2133024 13448384
product_b 3307080 15581632
vendor_b 1856272 18888896'
N100_META_SHA=bb42777e434f73ae5e4a9871af64f792a61a832be3852adf7c3d2f8b7012dcc7

case "${1:-}" in
    --plan) printf '# metadata_sha256=%s\n%s\n' "$N100_META_SHA" "$N100_TABLES"; exit 0 ;;
    run) [ "$#" -eq 1 ] || exit 2 ;;
    *) echo 'Usage: probe-android-mounts.sh --plan | run' >&2; exit 2 ;;
esac

die() { echo "STOP: $*" >&2; exit 1; }
mounted() { grep -F " $1 " /proc/mounts >/dev/null; }
ROOT=/data/.stowaways/sailfishos
[ "$(id -u)" = 0 ] || die 'Run in the existing N100 initramfs root shell'
grep -Eq '(^|[[:space:]])n100_debug=1([[:space:]]|$)' /proc/cmdline || die 'Early-debug flag missing'
slot=
for opt in $(cat /proc/cmdline); do
    case "$opt" in
        androidboot.slot_suffix=*) [ -z "$slot" ] || die 'Duplicate slot option'; slot=${opt#*=} ;;
    esac
done
[ "$slot" = _b ] || die 'This profile requires current slot B'
grep -qx 'PARTNAME=super' /sys/class/block/sda10/uevent || die 'sda10 is not super'
[ "$(cat /sys/class/block/sda10/dev)" = 8:10 ] || die 'Unexpected super device number'
[ "$(cat /sys/class/block/sda10/size)" = 20971520 ] || die 'Unexpected super size'
grep -Eq '^/dev/sda16 /data ext4 ro(,| )' /proc/mounts || die 'Expected read-only userdata mount'
[ -x "$ROOT/usr/sbin/dmsetup" ] || die 'Native Sailfish dmsetup missing'
for leaf in dev proc sys; do
    [ -d "$ROOT/$leaf" ] || die "Missing rootfs directory: $leaf"
    if mounted "$ROOT/$leaf"; then die "Existing bind mount: $leaf"; fi
done
for item in /sys/class/block/dm-*/dm/name; do
    [ -r "$item" ] || continue
    case "$(cat "$item")" in n100_ro_*) die 'Reserved probe mapping already exists' ;; esac
done

work=$(mktemp -d /tmp/n100-map-probe.XXXXXX)
binds=
maps=
mounts=
dm() { chroot "$ROOT" /usr/sbin/dmsetup --noudevsync "$@"; }
cleanup() {
    rc=$?
    trap - 0 HUP INT TERM
    set +e
    failed=0
    for point in $mounts; do
        if mounted "$point" && ! umount "$point"; then failed=1; fi
    done
    for map in $maps; do
        present=no
        for item in /sys/class/block/dm-*/dm/name; do
            [ -r "$item" ] || continue
            if [ "$(cat "$item")" = "$map" ]; then present=yes; fi
        done
        if [ "$present" = yes ] && ! dm remove "$map"; then failed=1; fi
    done
    for point in $binds; do
        if mounted "$point" && ! umount "$point"; then failed=1; fi
    done
    echo "N100_PROBE_RESULT exit=$rc cleanup_failed=$failed"
    echo "RAM work directory: $work"
    if [ "$failed" != 0 ] && [ "$rc" = 0 ]; then rc=1; fi
    exit "$rc"
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' HUP TERM

echo 'N100 read-only Android mapping probe V1'
echo "Kernel: $(uname -r); slot=$slot"
dd if=/dev/sda10 of="$work/metadata.bin" bs=512 count=792
[ "$(wc -c < "$work/metadata.bin")" -eq 405504 ] || die 'Incomplete metadata read'
actual=$(sha256sum "$work/metadata.bin")
actual=${actual%% *}
[ "$actual" = "$N100_META_SHA" ] || die 'Super metadata changed; collect a new report'
echo "Live metadata SHA-256 verified: $actual"
echo '=== Current Sailfish rootfs APEX contents and linker targets ==='
chroot "$ROOT" /bin/sh -c '
ls -la /apex
for p in /apex/com.android.runtime /apex/com.android.runtime/bin/linker /apex/com.android.runtime/bin/linker64 /usr/libexec/droid-hybris/system/apex; do
    if [ -e "$p" ]; then ls -ld "$p"; else echo "MISSING: $p"; fi
done
ls -l /usr/libexec/droid-hybris/system/bin/linker*
' || :

for leaf in dev proc sys; do
    binds="$ROOT/$leaf $binds"
    mount --bind "/$leaf" "$ROOT/$leaf"
done
dm --version
dm targets

while read -r part sectors offset; do
    map=n100_ro_$part
    echo "=== $part: 0 $sectors linear 8:10 $offset ==="
    # Track an attempted creation too, so a partial userspace error is visible.
    maps="$map $maps"
    dm create "$map" --readonly --table "0 $sectors linear 8:10 $offset"
    node=
    for block in /sys/class/block/dm-*; do
        [ -r "$block/dm/name" ] || continue
        [ "$(cat "$block/dm/name")" = "$map" ] || continue
        [ "$(cat "$block/ro")" = 1 ] || die "Mapping is not read-only: $map"
        node=/dev/${block##*/}
        break
    done
    [ -n "$node" ] && [ -b "$node" ] || die "No block node for $map"
    dm table "$map"
    echo "Verified kernel read-only flag: $node"
    point="$work/$part"
    mkdir "$point"
    mounts="$point $mounts"
    mount -t ext4 -o ro,noload "$node" "$point"
    grep -F " $point " /proc/mounts
    ls -la "$point"
    for prop in "$point/build.prop" "$point/system/build.prop" "$point/etc/build.prop"; do
        [ -f "$prop" ] || continue
        echo "--- $prop ---"
        grep -E '^ro\..*(fingerprint|version.sdk|version.release|security_patch)=' "$prop" || :
    done
    for apex in "$point/apex" "$point/system/apex"; do
        [ -d "$apex" ] || continue
        echo "--- APEX directory: $apex ---"
        ls -l "$apex"
    done
done <<N100_PROFILE
$N100_TABLES
N100_PROFILE

echo 'All four snapshot-bound Android filesystems mounted read-only.'
echo 'Sailfish Android-13 HAL files, /apex and userdata contents were not changed.'
# EXIT trap removes scratch mounts, mappings and our three bind mounts.
