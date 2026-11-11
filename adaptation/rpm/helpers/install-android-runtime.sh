#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Extend only a staged HAL RPM payload; this does not activate APEXes or services.
set -euo pipefail
: "${1:?Pass ANDROID_ROOT}" "${2:?Pass the RPM buildroot}"
product=$(readlink -f -- "$1/out/target/product/billie2")
buildroot=$(readlink -f -- "$2")
base="$buildroot/usr/libexec/droid-hybris/system"
[[ "$buildroot" != / && -d "$base" && -f "$buildroot/sbin/droid-hal-init" && -f tmp/droid-hal.files ]] || {
    echo 'Expected a staged HAL RPM and its generated file list' >&2; exit 1;
}
inputs=(system/bin/hwservicemanager system/bin/logd system/etc/init/hwservicemanager.rc system/etc/init/logd.rc
        system/apex/com.android.runtime.apex
        system/etc/sanitizer.libraries.txt system/etc/vndkcorevariant.libraries.txt
        system/etc/selinux/plat_service_contexts system/etc/selinux/plat_hwservice_contexts
        system/etc/selinux/plat_property_contexts)
for relative in "${inputs[@]}"; do
    [[ -f "$product/$relative" && ! -L "$product/$relative" ]] || {
        echo "Missing runtime build input: $relative" >&2; exit 1;
    }
    resolved=$(readlink -f -- "$product/$relative")
    [[ "$resolved" == "$product/"* ]] || { echo "Runtime input leaves product directory: $relative" >&2; exit 1; }
    destination="$base/${relative#system/}"
    # Never follow an unexpected parent or destination link in the staged tree.
    check="$destination"
    while [[ "$check" != "$buildroot" ]]; do
        [[ ! -L "$check" ]] || { echo "Unexpected staged symlink: $check" >&2; exit 1; }
        check=${check%/*}
    done
done
for relative in "${inputs[@]}"; do
    destination="$base/${relative#system/}"
    mode=0644
    [[ "$relative" != system/bin/* ]] || mode=0755
    install -D -m "$mode" -- "$product/$relative" "$destination"
done
# Device probe V6: servicemanager answers clients only with the hybris SELinux stubs preloaded
# (real libselinux denies the unknown service_manager class without an Android policy).
stubs=/system/lib64/libselinux_stubs.so
[[ -f "$base/lib64/libselinux_stubs.so" && ! -L "$base/lib64/libselinux_stubs.so" ]] || {
    echo 'Staged HAL tree lacks lib64/libselinux_stubs.so' >&2; exit 1;
}
for name in servicemanager hwservicemanager; do
    rc="$base/etc/init/$name.rc"
    [[ -f "$rc" && ! -L "$rc" ]] || { echo "Missing staged init declaration: $name.rc" >&2; exit 1; }
    [[ $(grep -c "^service $name /system/bin/$name\$" "$rc") == 1 ]] || {
        echo "Unexpected service declaration in $name.rc" >&2; exit 1;
    }
    if grep -q 'LD_PRELOAD' "$rc"; then
        grep -qx "    setenv LD_PRELOAD $stubs" "$rc" || { echo "Unexpected LD_PRELOAD in $name.rc" >&2; exit 1; }
        continue
    fi
    awk -v svc="service $name /system/bin/$name" -v line="    setenv LD_PRELOAD $stubs" \
        '{ print } $0 == svc { print line }' "$rc" > "$rc.n100" && mv -- "$rc.n100" "$rc"
    chmod 0644 "$rc"
done
# bin/ and etc/init/*.rc are already owned by DHD; add only new file-list paths.
for line in '%dir /usr/libexec/droid-hybris/system/apex' \
    '/usr/libexec/droid-hybris/system/apex/com.android.runtime.apex' \
    '/usr/libexec/droid-hybris/system/etc/sanitizer.libraries.txt' \
    '/usr/libexec/droid-hybris/system/etc/vndkcorevariant.libraries.txt' \
    '%dir /usr/libexec/droid-hybris/system/etc/selinux' \
    '/usr/libexec/droid-hybris/system/etc/selinux/plat_service_contexts' \
    '/usr/libexec/droid-hybris/system/etc/selinux/plat_hwservice_contexts' \
    '/usr/libexec/droid-hybris/system/etc/selinux/plat_property_contexts'; do
    grep -Fxq -- "$line" tmp/droid-hal.files || printf '%s\n' "$line" >> tmp/droid-hal.files
done
echo 'Staged runtime APEX, HAL services, init declarations, linkerconfig inputs, platform SELinux contexts and the SELinux stub preload for RPM packaging.'
