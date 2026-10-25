#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Remove only obsolete Android root aliases from a staged droid-hal RPM payload.
set -euo pipefail
: "${1:?Pass the RPM buildroot}"
buildroot=$(readlink -f -- "$1")
if [[ "$buildroot" == / || ! -d "$buildroot/usr/libexec/droid-hybris/system" ||
      ! -f "$buildroot/sbin/droid-hal-init" ]]; then
    echo "Expected a staged droid-hal RPM buildroot" >&2
    exit 1
fi

links=()
for name in bugreports cache d sdcard; do
    case "$name" in
        bugreports) expected=/data/user_de/0/com.android.shell/files/bugreports ;;
        cache) expected=/data/cache ;;
        d) expected=/sys/kernel/debug ;;
        sdcard) expected=/storage/self/primary ;;
    esac
    path="$buildroot/$name"
    if [[ -L "$path" ]]; then
        actual=$(readlink -- "$path")
        if [[ "$actual" != "$expected" ]]; then
            echo "Unexpected Android root alias: $name -> $actual" >&2
            exit 1
        fi
        links+=("$path")
    elif [[ -e "$path" ]]; then
        echo "Refusing to remove a non-symlink Android root entry: $name" >&2
        exit 1
    fi
done

# Validate every alias before removing any, and never follow targets or recurse.
for path in "${links[@]}"; do
    rm -- "$path"
done
