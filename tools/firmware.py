#!/usr/bin/env python3
"""Collect and verify the pinned billie2 vendor inputs; never write to the phone."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys

import n100


def digest(path, algorithm="sha256"):
    result = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def safe_name(name):
    path = PurePosixPath(name)
    if (not re.fullmatch(r"[A-Za-z0-9_@.+/-]+", name) or path.is_absolute()
            or any(p in ("", ".", "..") for p in name.split("/"))
            or path.parts[0] not in ("vendor", "odm", "system", "system_ext", "product")):
        raise n100.PortError("Unsafe or unsupported proprietary file path: " + name)
    return name


def parse_list(text):
    records = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = (line[1:] if line.startswith("-") else line).split("|")
        # The pinned list has no module-option suffixes. Refuse new syntax
        # rather than silently ignoring it when a source list changes.
        paths = fields[0].split(":")
        if len(paths) > 2:
            raise n100.PortError(f"Unsupported blob mapping on line {number}")
        source = safe_name(paths[0])
        destination = safe_name(paths[-1])
        pins = fields[1:]
        if len(pins) > 2 or any(not re.fullmatch(r"[0-9a-f]{40}", value) for value in pins):
            raise n100.PortError(f"Unsupported blob hash on line {number}")
        record = {"source": source, "destination": destination, "upstream_sha1": pins}
        if source in records and records[source] != record:
            raise n100.PortError("Conflicting blob declarations: " + source)
        records[source] = record
    if not records:
        raise n100.PortError("Empty proprietary file list")
    return records


def source_tree(work):
    pin = next(p for p in n100.read_lock()["projects"] if p["path"] == "device/oneplus/billie2")
    tree = work / "device-source"
    if not tree.exists():
        n100.run(["git", "clone", "--depth", "1", "--no-checkout", "--branch", pin["branch"],
                  "https://github.com/" + pin["name"] + ".git", tree])
        if n100.run(["git", "rev-parse", "HEAD"], cwd=tree, capture=True) != pin["revision"]:
            n100.run(["git", "fetch", "--depth", "1", "origin", pin["revision"]], cwd=tree)
        n100.run(["git", "checkout", "--detach", pin["revision"]], cwd=tree)
    n100.require_checkout(tree, pin["revision"])
    # Read the committed list, even if a checkout file has been locally edited.
    text = n100.run(["git", "show", pin["revision"] + ":proprietary-files.txt"], cwd=tree, capture=True)
    return pin, text, parse_list(text)


def checked_file(root, name):
    if root.is_symlink():
        raise n100.PortError("Firmware file directory must not be a symlink")
    current = root
    for part in PurePosixPath(safe_name(name)).parts:
        current = current / part
        if current.is_symlink():
            raise n100.PortError("Symlink in local firmware dump: " + str(current))
    return current


def save_inventory(path, inventory):
    temporary = path.with_suffix(".json.tmp")
    if temporary.is_symlink():
        raise n100.PortError("Refusing an inventory symlink")
    temporary.write_text(json.dumps(inventory, indent=2) + "\n")
    temporary.replace(path)


def collect(work, serial=None):
    if not shutil.which("adb"):
        raise n100.PortError("Install android-tools first: sudo pacman -S --needed android-tools")
    if not serial:
        devices = n100.run(["adb", "devices"], capture=True).splitlines()[1:]
        devices = [line.split() for line in devices if line.strip()]
        if len(devices) != 1 or len(devices[0]) != 2 or devices[0][1] != "device":
            raise n100.PortError("Connect exactly one authorized device, or pass --serial")
        serial = devices[0][0]
    report = n100.collect_report(serial)
    assessment = n100.analyze_report(report)
    if assessment["source_compatibility"] != "candidate-generation-match" or assessment["model"] != "BE2013":
        raise n100.PortError("This collector requires the confirmed BE2013 stock Android 11 device")
    fingerprint = report["properties"].get("ro.build.fingerprint")
    if not fingerprint:
        raise n100.PortError("Missing firmware fingerprint; cannot safely resume a dump")
    work.mkdir(parents=True, exist_ok=True)
    pin, text, wanted = source_tree(work)
    output = work / "firmware"
    inventory_path = output / "inventory.json"
    identity = {"schema": 1, "device_revision": pin["revision"], "fingerprint": fingerprint,
                "list_sha256": hashlib.sha256(text.encode()).hexdigest()}
    if output.is_symlink() or inventory_path.is_symlink():
        raise n100.PortError("Refusing a firmware output symlink")
    if inventory_path.exists():
        inventory = json.loads(inventory_path.read_text())
        if any(inventory.get(k) != v for k, v in identity.items()):
            raise n100.PortError("Firmware/source identity changed; use a separate work directory")
    else:
        if output.exists() and any(output.iterdir()):
            raise n100.PortError("Existing firmware directory has no matching inventory")
        output.mkdir(exist_ok=True)
        inventory = {**identity, "device_writes": False, "files": {}}
        n100.write_new(output / "device-report.json", json.dumps(report, indent=2) + "\n")
        save_inventory(inventory_path, inventory)
    # Pin the same transport used for the identity check. Never store its serial.
    base = ["adb", "-s", serial]
    for index, (name, declaration) in enumerate(wanted.items(), 1):
        destination = checked_file(output / "files", name)
        old = inventory["files"].get(name, {})
        if destination.exists():
            if old.get("sha256") != digest(destination) or old.get("bytes") != destination.stat().st_size:
                raise n100.PortError("Existing file differs from inventory: " + name)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".partial")
        if partial.exists() or partial.is_symlink():
            raise n100.PortError("Inspect/remove interrupted temporary file before retrying: " + str(partial))
        try:
            # File-specific pull follows stock source/destination remaps exactly.
            result = subprocess.run(base + ["pull", "/" + name, str(partial)],
                                    capture_output=True, text=True, timeout=180)
            if result.returncode or not partial.is_file():
                raise n100.PortError("ADB pull unavailable (exit " + str(result.returncode) + ")")
            if partial.stat().st_size > 512 * 1024**2:
                raise n100.PortError("Unexpected blob larger than 512 MiB")
            record = {**declaration, "status": "collected", "bytes": partial.stat().st_size,
                      "sha256": digest(partial), "sha1": digest(partial, "sha1")}
            pins = declaration["upstream_sha1"]
            record["upstream_pin_matches"] = record["sha1"] in pins if pins else None
            partial.replace(destination)
            inventory["files"][name] = record
        except (n100.PortError, subprocess.TimeoutExpired) as exc:
            partial.unlink(missing_ok=True)
            inventory["files"][name] = {**declaration, "status": "unavailable", "error": str(exc)}
        save_inventory(inventory_path, inventory)
        if index % 20 == 0 or index == len(wanted):
            count = sum(r["status"] == "collected" for r in inventory["files"].values())
            print(f"{index}/{len(wanted)} checked; {count} collected", flush=True)
    result = verify(work)
    print(json.dumps(result, indent=2))
    if not result["complete"]:
        raise n100.PortError("Incomplete dump. Repeat the same command to retry unavailable files")
    print("Firmware inputs saved locally. No upload of the proprietary files is required.")


def verify(work):
    pin, text, wanted = source_tree(work)
    output = work / "firmware"
    inventory = json.loads((output / "inventory.json").read_text())
    if (inventory.get("device_revision") != pin["revision"] or
            inventory.get("list_sha256") != hashlib.sha256(text.encode()).hexdigest()):
        raise n100.PortError("Dump was collected against a different source list")
    missing, changed, mismatches = [], [], []
    for name, declaration in wanted.items():
        file = checked_file(output / "files", name)
        record = inventory.get("files", {}).get(name, {})
        if not file.is_file() or record.get("status") != "collected":
            missing.append(name)
            continue
        if file.stat().st_size != record.get("bytes") or digest(file) != record.get("sha256"):
            changed.append(name)
        pins = declaration["upstream_sha1"]
        if pins and digest(file, "sha1") not in pins:
            mismatches.append(name)
    return {"required": len(wanted), "missing": missing, "changed": changed,
            "upstream_pin_mismatches": mismatches, "complete": not (missing or changed),
            "ready_for_extraction": not (missing or changed or mismatches),
            "note": "EEA vs global pinned-blob differences require review before ELF fixups"}


def import_dump(work, android):
    result = verify(work)
    if not result["ready_for_extraction"]:
        raise n100.PortError("Firmware inputs require review: " + json.dumps(result))
    n100.require_profile(android)
    pin = next(p for p in n100.read_lock()["projects"] if p["path"] == "device/oneplus/billie2")
    device = android / pin["path"]
    n100.require_checkout(device, pin["revision"])
    for name in ("extract-files.sh", "setup-makefiles.sh", "proprietary-files.txt"):
        if n100.run(["git", "diff", "HEAD", "--", name], cwd=device, capture=True):
            raise n100.PortError("Extraction script was edited: " + name)
    if (android / "vendor/oneplus/billie2").exists():
        raise n100.PortError("Vendor output already exists; refusing upstream cleanup/overwrite")
    n100.run(["bash", device / "extract-files.sh", work / "firmware/files"], cwd=android)
    for name in ("billie2-vendor.mk", "BoardConfigVendor.mk"):
        file = android / "vendor/oneplus/billie2" / name
        if not file.is_file() or not file.stat().st_size:
            raise n100.PortError("Extraction did not produce " + name)
    print("Vendor makefiles generated. Full HAL build is the next stage.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(Path.home() / "n100-build"))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("collect")
    p.add_argument("--serial")
    sub.add_parser("verify")
    p = sub.add_parser("import")
    p.add_argument("--android-root", required=True)
    args = parser.parse_args()
    try:
        work = n100.android_path(args.work_dir)
        if args.command == "collect":
            collect(work, args.serial)
        elif args.command == "verify":
            result = verify(work)
            print(json.dumps(result, indent=2))
            if not result["ready_for_extraction"]:
                parser.exit(1)
        else:
            import_dump(work, n100.android_path(args.android_root))
    except (n100.PortError, OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
