#!/usr/bin/env bash
# Run inside PLATFORM_SDK with a configured oneplus-billie2-aarch64 target.
set -eo pipefail
port_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
source "$port_dir/config/port.env"
: "${ANDROID_ROOT:?Set ANDROID_ROOT to the prepared Android tree}"
: "${RELEASE:?Set RELEASE to the exact Sailfish release installed in your build target}"
command -v mb2 >/dev/null
command -v sdk-assistant >/dev/null
cd "$ANDROID_ROOT"
test -s out/target/product/billie2/hybris-boot.img
test -f rpm/droid-hal-billie2.spec
case "${1:-}" in
    hal) exec bash rpm/dhd/helpers/build_packages.sh -d ;;
    configs) exec bash rpm/dhd/helpers/build_packages.sh -c ;;
    middleware) exec bash rpm/dhd/helpers/build_packages.sh -m ;;
    media) exec bash rpm/dhd/helpers/build_packages.sh -g ;;
    version) exec bash rpm/dhd/helpers/build_packages.sh -v ;;
    rootfs)
        command -v mic >/dev/null
        command -v createrepo_c >/dev/null
        exec bash rpm/dhd/helpers/build_packages.sh -i ;;
    *) echo 'Usage: build-packages.sh {hal|configs|middleware|media|version|rootfs}' >&2; exit 2 ;;
esac
