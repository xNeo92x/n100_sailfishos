#!/usr/bin/env python3
"""Repair the reviewed WebView manifest link after the upstream Makefile rename."""
import argparse
import hashlib
import os
from pathlib import Path
import tempfile

# LineageOS/android_external_chromium-webview_patches:
# commit 091983e4d4a41a61eebd3ca3c01d0bcc128ffb6b renamed Android.mk
# to os_pickup.mk without changing its contents. Git blob ID of both files:
MAKEFILE_BLOB = "26ca6b19efe40f19362473d217596156075032b2"
OLD_TARGET = "patches/Android.mk"
NEW_TARGET = "patches/os_pickup.mk"


def repair(android_root):
    directory = Path(android_root) / "external/chromium-webview"
    link = directory / "Android.mk"
    if not link.is_symlink():
        raise ValueError("Expected WebView Android.mk manifest symlink; inspect manually")
    target = os.readlink(link)
    if target == OLD_TARGET and link.is_file():
        return False
    if target not in (OLD_TARGET, NEW_TARGET):
        raise ValueError("Unexpected WebView Android.mk link target; inspect manually")
    source = directory / NEW_TARGET
    if source.is_symlink() or not source.is_file():
        raise ValueError("Reviewed WebView os_pickup.mk is missing or is a symlink")
    data = source.read_bytes()
    # This is a Git blob identity check, not a file's plain SHA-1 digest.
    digest = hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()
    if digest != MAKEFILE_BLOB:
        raise ValueError("WebView os_pickup.mk differs from the reviewed Makefile")
    if target == NEW_TARGET:
        return False
    with tempfile.TemporaryDirectory(prefix=".n100-webview-", dir=str(directory)) as temporary:
        replacement = Path(temporary) / "Android.mk"
        replacement.symlink_to(NEW_TARGET)
        os.replace(str(replacement), str(link))
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-root", required=True)
    args = parser.parse_args()
    try:
        changed = repair(args.android_root)
    except (OSError, ValueError) as error:
        parser.exit(1, "WebView input check failed: " + str(error) + "\n")
    print("WebView manifest link repaired: Android.mk -> " + NEW_TARGET
          if changed else "WebView manifest link OK")


if __name__ == "__main__":
    main()
