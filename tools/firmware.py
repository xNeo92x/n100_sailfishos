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


def adb_error_kind(stdout, stderr):
    message = (stdout + "\n" + stderr).lower()
    if "permission denied" in message or "operation not permitted" in message:
        return "permission_denied"
    if "no such file" in message or "does not exist" in message or "cannot stat" in message:
        return "not_found"
    if (any(term in message for term in ("device offline", "device not found", "no devices", "disconnected", "error: closed"))
            or re.search(r"device\s+'[^']+'\s+not found", message)):
        return "transport"
    return "unknown"


def collect(work, serial=None, retry_failed=False):
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
        if old.get("error_kind") in ("permission_denied", "not_found") and not retry_failed:
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
                error = n100.PortError("ADB pull unavailable (exit " + str(result.returncode) + ")")
                error.stdout = getattr(result, "stdout", "") or ""
                error.stderr = getattr(result, "stderr", "") or ""
                error.returncode = result.returncode
                raise error
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
            stdout = getattr(exc, "stdout", "") or ""
            stderr = getattr(exc, "stderr", "") or ""
            def clean(value):
                if isinstance(value, bytes):
                    value = value.decode("utf-8", errors="replace")
                return value.replace(serial, "<device>")[-16384:]
            stdout, stderr = clean(stdout), clean(stderr)
            timeout = isinstance(exc, subprocess.TimeoutExpired)
            inventory["files"][name] = {**declaration, "status": "unavailable",
                "error": "ADB pull timed out" if timeout else str(exc),
                "stdout": stdout, "stderr": stderr, "returncode": getattr(exc, "returncode", None),
                "error_kind": "timeout" if timeout else adb_error_kind(stdout, stderr)}
        save_inventory(inventory_path, inventory)
        if index % 20 == 0 or index == len(wanted):
            count = sum(r["status"] == "collected" for r in inventory["files"].values())
            print(f"{index}/{len(wanted)} checked; {count} collected", flush=True)
    result = verify(work)
    result["failure_kinds"] = {}
    for record in inventory["files"].values():
        if record.get("status") == "unavailable":
            kind = record.get("error_kind", "unknown")
            result["failure_kinds"][kind] = result["failure_kinds"].get(kind, 0) + 1
    report_path = n100.write_report(output / "verification.json", json.dumps(result, indent=2) + "\n")
    print(f"Collection: {len(wanted) - len(result['missing'])}/{len(wanted)} files; "
          f"{len(result['upstream_pin_mismatches'])} raw-reference hash differences")
    print("Failure kinds:", json.dumps(result["failure_kinds"]))
    print("Details:", report_path)
    if not result["complete"]:
        raise n100.PortError("Incomplete dump. Inspect the saved failure details. For persistent failures, "
                             "use vendor-source; --retry-failed explicitly retries permission/path failures")
    if not result["ready_for_extraction"]:
        raise n100.PortError("Dump is complete but differs from raw reference pins, including donor-device blobs. "
                             "Use the separately verified vendor-source build input")
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
            "note": "Raw reference pins include donor-device blobs (lemonadep WiFi Display); "
                    "differences are not necessarily regional or corruption. Do not apply unreviewed ELF fixups"}


def vendor_pin():
    pin = json.loads((n100.ROOT / "config/vendor-source.json").read_text())
    device = next(p for p in n100.read_lock()["projects"] if p["path"] == "device/oneplus/billie2")
    if pin["device_revision"] != device["revision"]:
        raise n100.PortError("Vendor source has not been assessed against the current device revision")
    return pin


def verify_vendor(tree, declarations, pin):
    """Validate a pre-extracted dataset, separately from the raw stock dump."""
    n100.require_checkout(tree, pin["revision"])
    try:
        n100.run(["git", "diff", "--quiet", "HEAD", "--"], cwd=tree)
    except n100.PortError as exc:
        raise n100.PortError("Vendor checkout has tracked changes; inspect it before continuing") from exc
    if n100.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=tree, capture=True):
        raise n100.PortError("Unexpected untracked files in vendor checkout; inspect before continuing")
    tracked = n100.run(["git", "ls-files", "-z"], cwd=tree, capture=True).split("\0")
    tracked = sorted(name for name in tracked if name)
    if len(tracked) != pin["tracked_files"] or len(declarations) != pin["required_blob_files"]:
        raise n100.PortError("Unexpected vendor file-list size")
    build_files = {"Android.bp", "Android.mk", "BoardConfigVendor.mk", "billie2-vendor.mk"}
    expected = build_files | {"proprietary/" + d["destination"] for d in declarations.values()}
    if set(tracked) != expected:
        raise n100.PortError("Vendor checkout does not exactly cover the device's build inputs")
    catalog = {}
    for name in tracked:
        file = tree / name
        current = file
        while current != tree.parent:
            if current.is_symlink():
                raise n100.PortError("Symlink in vendor source: " + name)
            current = current.parent
        if not file.is_file():
            raise n100.PortError("Missing vendor source file: " + name)
        with file.open("rb") as stream:
            if stream.read(80).startswith(b"version https://git-lfs.github.com/spec/v1"):
                raise n100.PortError("Vendor file is an unresolved Git LFS pointer: " + name)
        catalog[name] = {"bytes": file.stat().st_size, "sha256": digest(file)}
    actual = hashlib.sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if actual != pin["catalog_sha256"]:
        raise n100.PortError("Vendor dataset SHA-256 inventory differs from the reviewed source")
    matches, differences = [], []
    for entry in declarations.values():
        if not entry["upstream_sha1"]:
            continue
        name = entry["destination"]
        sha1 = digest(tree / "proprietary" / name, "sha1")
        if sha1 in entry["upstream_sha1"]:
            matches.append(name)
        else:
            assessed = pin["packaged_reference_differences"].get(name)
            if (not assessed or assessed["sha1"] != sha1 or
                    assessed["input_reference_sha1"] != entry["upstream_sha1"] or
                    assessed["sha256"] != catalog["proprietary/" + name]["sha256"]):
                raise n100.PortError("Unassessed packaged vendor/reference difference: " + name)
            differences.append(name)
    if set(differences) != set(pin["packaged_reference_differences"]):
        raise n100.PortError("Vendor reference-difference inventory changed")
    return {"profile": pin["profile"], "repository": pin["repository"], "revision": pin["revision"],
            "required_blob_files": len(declarations), "tracked_files_verified": len(tracked),
            "catalog_sha256": actual, "raw_reference_pins_matched": len(matches),
            "declared_packaged_reference_differences": differences, "build_inputs_complete": True,
            "stock_eea_runtime_compatibility_verified": False, "hal_built": False, "boot_tested": False}


def vendor_marker(pin):
    return {"profile": pin["profile"], "revision": pin["revision"],
            "pin_config_sha256": digest(n100.ROOT / "config/vendor-source.json")}


def fetch_vendor(work, android=None):
    if android is not None:
        n100.require_profile(android)
    pin = vendor_pin()
    work.mkdir(parents=True, exist_ok=True)
    _, text, declarations = source_tree(work)
    if hashlib.sha256(text.encode()).hexdigest() != pin["source_list_sha256"]:
        raise n100.PortError("Vendor source was assessed against a different proprietary file list")
    tree = work / "vendor-source"
    if not tree.exists():
        n100.run(["git", "clone", "--depth", "1", "--no-checkout", "--branch", pin["branch"],
                  "https://github.com/" + pin["repository"] + ".git", tree])
        if n100.run(["git", "rev-parse", "HEAD"], cwd=tree, capture=True) != pin["revision"]:
            n100.run(["git", "fetch", "--depth", "1", "origin", pin["revision"]], cwd=tree)
        n100.run(["git", "checkout", "--detach", pin["revision"]], cwd=tree)
    result = verify_vendor(tree, declarations, pin)
    audit = n100.write_report(work / "vendor-source-audit.json", json.dumps(result, indent=2) + "\n")
    print(f"Vendor source: {result['required_blob_files']} blobs verified; "
          f"{len(result['declared_packaged_reference_differences'])} declared raw-reference differences")
    print("Audit:", audit)
    if android is not None:
        n100.require_checkout(android / "device/oneplus/billie2", pin["device_revision"])
        destination = android / "vendor/oneplus/billie2"
        if destination.exists() or destination.is_symlink():
            verify_vendor(destination, declarations, pin)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            n100.run(["git", "worktree", "add", "--detach", destination, pin["revision"]], cwd=tree)
        n100.write_new(android / ".n100-vendor-source.json", json.dumps(vendor_marker(pin), indent=2) + "\n")
        print("Pre-extracted vendor worktree installed at", destination)
    print("Build inputs only; EEA firmware compatibility and device boot remain unvalidated.")
    return result


def verify_installed_vendor(android):
    n100.require_profile(android)
    pin = vendor_pin()
    marker = android / ".n100-vendor-source.json"
    if json.loads(marker.read_text()) != vendor_marker(pin):
        raise n100.PortError("Installed vendor-source marker differs from current reviewed inputs")
    device = android / "device/oneplus/billie2"
    n100.require_checkout(device, pin["device_revision"])
    text = n100.run(["git", "show", "HEAD:proprietary-files.txt"], cwd=device, capture=True)
    if hashlib.sha256(text.encode()).hexdigest() != pin["source_list_sha256"]:
        raise n100.PortError("Installed device proprietary list differs from the vendor assessment")
    return verify_vendor(android / "vendor/oneplus/billie2", parse_list(text), pin)


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
    p.add_argument("--retry-failed", action="store_true")
    sub.add_parser("verify")
    p = sub.add_parser("vendor-source", help="Fetch the reviewed complete pre-extracted vendor candidate; no ADB")
    p.add_argument("--android-root")
    p = sub.add_parser("verify-vendor", help="Verify an installed pre-extracted vendor worktree")
    p.add_argument("--android-root", required=True)
    p = sub.add_parser("import")
    p.add_argument("--android-root", required=True)
    args = parser.parse_args()
    try:
        work = n100.android_path(args.work_dir)
        if args.command == "collect":
            collect(work, args.serial, args.retry_failed)
        elif args.command == "vendor-source":
            fetch_vendor(work, n100.android_path(args.android_root) if args.android_root else None)
        elif args.command == "verify-vendor":
            print(json.dumps(verify_installed_vendor(n100.android_path(args.android_root)), indent=2))
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
