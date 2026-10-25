#!/usr/bin/env python3
"""Check Android media outputs before the legacy RPM packer copies them."""
# SPDX-License-Identifier: MIT
import argparse
from pathlib import Path
import sys


OUTPUTS = (
    "system/lib64/libdroidmedia.so",
    "system/lib64/libminisf.so",
    "system/bin/minimediaservice",
    "system/bin/minisfservice",
)


def check_outputs(android_root):
    product = Path(android_root) / "out/target/product/billie2"
    errors = []
    for relative in OUTPUTS:
        path = product / relative
        try:
            with path.open("rb") as stream:
                header = stream.read(64)
        except OSError as exc:
            errors.append("{}: {}".format(relative, exc.strerror))
            continue
        if (len(header) < 64 or header[:7] != b"\x7fELF\x02\x01\x01"
                or int.from_bytes(header[18:20], "little") != 183
                or int.from_bytes(header[16:18], "little") not in (2, 3)):
            errors.append("{}: expected an AArch64 ELF64 executable or shared library".format(relative))
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-root", required=True, type=Path)
    args = parser.parse_args()
    errors = check_outputs(args.android_root)
    if errors:
        print("Android droidmedia outputs are missing or invalid:", file=sys.stderr)
        for error in errors:
            print("  " + error, file=sys.stderr)
        print('In Ubuntu HABUILD_SDK run: bash "$N100_PORT/tools/build-hal.sh" media', file=sys.stderr)
        print("Then return to Platform SDK and retry build-packages.sh media.", file=sys.stderr)
        return 1
    print("All four Android droidmedia outputs have AArch64 ELF64 headers (structural check only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
