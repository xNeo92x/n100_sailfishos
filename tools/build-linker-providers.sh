#!/usr/bin/env bash
set -e
printf '%s\n' 'The full ART provider build is blocked by the hybris TLS layout.' \
    'Use tools/build-native-runtime.sh in Ubuntu; see docs/NATIVE_RUNTIME.md.' >&2
exit 1
