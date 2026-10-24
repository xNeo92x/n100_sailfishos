#!/usr/bin/env python3
"""Host-side Nord N100 port tools. Python standard library; no flashing commands."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]


class PortError(Exception):
    stdout = ""
    stderr = ""
    returncode = None


def run(argv, cwd=None, capture=False, timeout=None):
    try:
        result = subprocess.run([str(x) for x in argv], cwd=cwd, check=True,
                                text=True, capture_output=capture, timeout=timeout)
        return result.stdout.strip() if capture else None
    except subprocess.CalledProcessError as exc:
        error = PortError(f"Command failed: {argv[0]} (exit {exc.returncode})")
        error.stdout = exc.stdout or ""
        error.stderr = exc.stderr or ""
        error.returncode = exc.returncode
        raise error from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise PortError(f"Command failed: {argv[0]}: {type(exc).__name__}") from exc


def read_lock():
    return json.loads((ROOT / "sources.lock.json").read_text())


def lock_digest():
    return hashlib.sha256((ROOT / "sources.lock.json").read_bytes()).hexdigest()


def diagnostic_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("stdout"), str):
        return value["stdout"]
    return ""


def block_aliases(text):
    """Keep physical by-name aliases separate from logical mapper aliases."""
    physical, mapper = {}, {}
    destination = None
    for line in text.splitlines():
        if line.strip() in ("/dev/block/by-name:", "/dev/block/bootdevice/by-name:"):
            destination = physical
            continue
        if line.strip() == "/dev/block/mapper:":
            destination = mapper
            continue
        match = re.search(r"\s([^\s/]+) -> (/dev/block/\S+)\s*$", line)
        if destination is not None and match and line.startswith("l"):
            name, target = match.groups()
            if name in destination and destination[name] != target:
                raise PortError(f"Inconsistent block alias evidence for {name}")
            destination[name] = target
    return physical, mapper


def vintf_inventory(xml):
    """Inventory declarations only; not a VINTF compatibility/runtime checker."""
    if not xml.strip():
        return None
    if len(xml) > 4 * 1024 * 1024 or re.search(r"<!\s*(DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise PortError("Unsupported VINTF XML input")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise PortError("Invalid VINTF XML") from exc
    if root.tag != "manifest":
        raise PortError("Expected a VINTF manifest element")
    hals=[]
    for hal in root.findall("hal"):
        versions={x.text.strip() for x in hal.findall("version") if x.text}
        instances={(interface.findtext("name", "").strip(), instance.text.strip())
                   for interface in hal.findall("interface") for instance in interface.findall("instance")
                   if instance.text}
        for fqname in hal.findall("fqname"):
            match=re.fullmatch(r"@(\d+\.\d+)::([^/]+)/(.+)", (fqname.text or "").strip())
            if match:
                versions.add(match[1])
                instances.add((match[2],match[3]))
        transport=hal.find("transport")
        hals.append({"name":hal.findtext("name", "").strip(), "format":hal.get("format", "hidl"),
                     "transport":(transport.text or "").strip() if transport is not None else "",
                     "transport_arch":transport.get("arch", "") if transport is not None else "",
                     "versions":sorted(versions),
                     "instances":[{"interface":i,"instance":n} for i,n in sorted(instances)]})
    return {"type":root.get("type"), "target_level":root.get("target-level"),
            "sepolicy_compatibility_version":root.findtext("sepolicy/version"),
            "hals":sorted(hals,key=lambda h:(h["name"],h["format"])),
            "coverage":"this XML only; separate fragments and runtime services not validated"}


def discovered_config_paths(text):
    """Accept only configuration filenames below the specific stock directories."""
    paths=set()
    for line in text.splitlines():
        path=line.strip()
        if any(part in (".","..") for part in path.split("/")):
            continue
        if re.fullmatch(r"/vendor/etc/fstab[A-Za-z0-9_.-]*",path):
            paths.add(path)
        elif re.fullmatch(r"/(?:vendor|odm|system_ext)/etc/vintf/(?:manifest/)?[A-Za-z0-9_@.+-]+\.xml",path):
            paths.add(path)
    return sorted(paths)


def analyze_report(report, lock=None):
    """Evidence summary only: a generation match never implies a bootable port."""
    if not isinstance(report, dict) or not isinstance(report.get("properties"), dict):
        raise PortError("Invalid device report: properties object is required")
    props = report["properties"]
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in props.items()):
        raise PortError("Invalid property values")
    diagnostics = report.get("diagnostics", {})
    if not isinstance(diagnostics, dict):
        raise PortError("Invalid diagnostics object")
    lock = lock or read_lock()
    mounts = []
    for line in diagnostic_text(diagnostics.get("mounts")).splitlines():
        fields = line.split()
        if len(fields) >= 6:
            mounts.append({"source": fields[0], "target": fields[1], "type": fields[2],
                           "options": fields[3].split(",")})
    aliases, mapper = block_aliases(diagnostic_text(diagnostics.get("partitions")))
    logical = sorted(name for name in mapper if re.fullmatch(r"(?:system|system_ext|vendor|product|odm)_[ab]", name))
    sdk = props.get("ro.build.version.sdk", "")
    release = props.get("ro.build.version.release", "")
    vendor_sdk = props.get("ro.vendor.build.version.sdk", "")
    vendor_fingerprint = props.get("ro.vendor.build.fingerprint", "")
    vendor_release = re.search(r":(\d+)(?:\.[^/]*)?/", vendor_fingerprint)
    identity = identify(props)
    compatibility = "unconfirmed"
    if identity == "wrong-device-nord-n10" or identity != "n100-candidate":
        compatibility = "wrong-or-unconfirmed-device"
    elif sdk and sdk != str(lock["stock_android_sdk"]):
        compatibility = "stock-android-generation-mismatch"
    elif vendor_sdk and vendor_sdk != str(lock["stock_android_sdk"]):
        compatibility = "vendor-android-generation-mismatch"
    elif vendor_release and vendor_release[1] != str(lock["stock_android_version"]):
        compatibility = "vendor-fingerprint-generation-mismatch"
    elif (release.split(".")[0] == str(lock["stock_android_version"]) and sdk == str(lock["stock_android_sdk"])
          and (vendor_sdk == str(lock["stock_android_sdk"]) or vendor_release)):
        compatibility = "candidate-generation-match"
    locked_value = props.get("ro.boot.flash.locked", "")
    locked = {"1": True, "0": False}.get(locked_value)
    encrypted = {"encrypted": True, "unencrypted": False}.get(props.get("ro.crypto.state"))
    blockers = ["images-not-built-or-boot-tested", "regional-proprietary-blobs-unverified"]
    if compatibility != "candidate-generation-match":
        blockers.append("source-profile-not-compatible-or-unconfirmed")
    if locked is not False:
        blockers.append("bootloader-locked" if locked else "bootloader-state-unconfirmed")
    if encrypted:
        blockers.append("encrypted-data-rootfs-access-unimplemented")
    elif encrypted is None:
        blockers.append("data-encryption-state-unconfirmed")
    if logical or props.get("ro.boot.dynamic_partitions") == "true":
        blockers.append("logical-partition-early-mounts-unimplemented")
    fstab_keys = [key for key in diagnostics if key == "fstab" or key.startswith("fstab_")]
    fstab_collected = any(re.search(r"^\s*[^#\s]\S*\s+/\S*\s+\w+\s+", diagnostic_text(diagnostics[key]), re.MULTILINE)
                          for key in fstab_keys)
    try:
        inventory = vintf_inventory(diagnostic_text(diagnostics.get("vintf_vendor")))
    except PortError as exc:
        inventory = {"status":"invalid-or-incomplete","reason":str(exc)}
    return {
        "schema": 1, "profile": lock["profile"], "source_compatibility": compatibility,
        "deployable": False, "model": props.get("ro.product.model", ""),
        "device": props.get("ro.product.device", ""), "platform": props.get("ro.board.platform", ""),
        "stock_android": release, "stock_sdk": sdk, "vendor_sdk": vendor_sdk or None,
        "vndk_version":props.get("ro.vndk.version") or None,
        "firmware_display":props.get("ro.build.display.id") or None,
        "security_patch":props.get("ro.build.version.security_patch") or None,
        "selinux_compatibility_version":diagnostic_text(diagnostics.get("selinux_vendor_version")).strip() or None,
        "fingerprint": props.get("ro.build.fingerprint", ""), "vendor_fingerprint": vendor_fingerprint,
        "kernel": diagnostic_text(diagnostics.get("kernel")), "active_slot": props.get("ro.boot.slot_suffix", ""),
        "bootloader_locked": locked, "verified_boot": props.get("ro.boot.verifiedbootstate", ""),
        "encrypted": encrypted, "encryption_type": props.get("ro.crypto.type", ""),
        "logical_partitions_observed": logical,
        "mounts_observed": [m for m in mounts if m["target"] in
                            ("/", "/system", "/system_ext", "/vendor", "/product", "/odm", "/data", "/metadata")],
        "physical_aliases_observed": {k: v for k, v in sorted(aliases.items()) if k in
                                     ("boot_a", "boot_b", "dtbo_a", "dtbo_b", "recovery_a", "recovery_b",
                                      "super", "metadata", "userdata")},
        "fstab_collected": fstab_collected,
        "fstab_paths_discovered":discovered_config_paths(diagnostic_text(diagnostics.get("fstab_paths"))),
        "vendor_vintf":inventory,
        "blockers": blockers,
        "note": "Observed dm numbers and slot are session-specific, not boot configuration."
    }


def analyze(args):
    assessment = analyze_report(json.loads(Path(args.report).read_text()))
    content = json.dumps(assessment, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        write_new(Path(args.output), content)
    print(content, end="")


def require_profile(android):
    marker = android / ".n100-source-profile.json"
    if not marker.is_file():
        raise PortError("Missing source profile marker; initialize a separate tree with the current report")
    profile = json.loads(marker.read_text())
    if profile.get("lock_sha256") != lock_digest():
        raise PortError("This tree uses a different source profile. Do not reuse an Android 10 tree")


def write_new(path, content):
    """Idempotent, refuse to overwrite edits or follow destination symlinks."""
    if path.is_symlink():
        raise PortError(f"Refusing symlink destination: {path}")
    if path.exists():
        if path.read_text() == content:
            return
        raise PortError(f"File differs; inspect manually: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def write_report(path, content, binary=False):
    """Save each collection separately, including when the requested name exists."""
    path = Path(os.path.abspath(Path(path).expanduser()))
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    candidate = path
    attempt = 0
    while True:
        try:
            # Exclusive creation also refuses symlinks and a concurrent writer.
            with candidate.open("xb" if binary else "x", **({} if binary else {"encoding":"utf-8"})) as stream:
                stream.write(content)
            return candidate
        except FileExistsError:
            attempt += 1
            suffix = "" if attempt == 1 else f"-{attempt}"
            candidate = path.with_name(f"{path.stem}-{stamp}{suffix}{path.suffix}")


def parse_config(text):
    values = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line)
        if match:
            values[match[1]] = match[2]
        match = re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line)
        if match:
            values[match[1]] = "n"
    return values


def merge_config(base, fragment):
    """Merge defconfig text only. The kernel's Kconfig still resolves dependencies."""
    overrides = parse_config(fragment)
    result = []
    for line in base.splitlines():
        found = re.match(r"(?:# )?(CONFIG_[A-Za-z0-9_]+)(?:=| is not set)", line)
        if not found or found[1] not in overrides:
            result.append(line)
    result.append("# Sailfish OS additions; generated by tools/n100.py prepare")
    for key, value in overrides.items():
        result.append(f"# {key} is not set" if value == "n" else f"{key}={value}")
    return "\n".join(result) + "\n"


def require_checkout(path, revision):
    if not (path / ".git").exists():
        raise PortError(f"Missing git checkout: {path}; sync sources first")
    actual = run(["git", "rev-parse", "HEAD"], cwd=path, capture=True)
    if actual != revision:
        raise PortError(f"Revision mismatch at {path}: {actual}; expected {revision}")


def android_path(value):
    path = Path(value).expanduser().resolve()
    if path == ROOT or path in ROOT.parents or ROOT in path.parents:
        raise PortError("Use a separate Android build tree outside this repository")
    if path == Path("/"):
        raise PortError("Android root cannot be /")
    return path


def init_sources(args):
    assessment = analyze_report(json.loads(Path(args.device_report).read_text()))
    if assessment["source_compatibility"] != "candidate-generation-match":
        raise PortError("Report/profile mismatch: " + assessment["source_compatibility"])
    android = android_path(args.android_root)
    if not shutil.which("repo"):
        raise PortError("Install the Android repo tool inside HABUILD_SDK first")
    android.mkdir(parents=True, exist_ok=True)
    if not (android / ".repo").exists() and any(android.iterdir()):
        raise PortError("Use an empty directory for a new source tree")
    if shutil.disk_usage(android).free < 200 * 1024**3:
        raise PortError("Reserve at least 200 GiB free for this Android HAL build")
    lock = read_lock()
    if (android / ".repo").exists():
        require_profile(android)
        existing = android / ".repo/local_manifests/billie2.xml"
        if not existing.exists() or existing.read_bytes() != (ROOT / "manifests/billie2.xml").read_bytes():
            raise PortError("Existing source tree has a different local manifest")
    run(["repo", "init", "-u", lock["android"]["url"], "-b",
         lock["android"]["revision"]], cwd=android)
    write_new(android / ".repo/local_manifests/billie2.xml",
              (ROOT / "manifests/billie2.xml").read_text())
    write_new(android / ".n100-source-profile.json", json.dumps({
        "profile": lock["profile"], "lock_sha256": lock_digest()}, indent=2) + "\n")
    if args.sync:
        run(["repo", "sync", "-c", f"-j{args.jobs}", "--fail-fast"], cwd=android)
        resolved = run(["repo", "manifest", "-r"], cwd=android, capture=True)
        write_new(android / "n100-resolved-manifest.xml", resolved + "\n")
    else:
        print(f"Manifest installed. Next: cd {android} && repo sync -c -j{args.jobs} --fail-fast")


def init_package(dest, source, dependency):
    marker = dest / ".n100-prepared.json"
    wanted = {"dependency": dependency, "files": {}}
    for path in source.rglob("*"):
        if path.is_file() and not path.is_symlink():
            wanted["files"][str(path.relative_to(source))] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif path.is_symlink():
            wanted["files"][str(path.relative_to(source))] = "symlink:" + os.readlink(path)
    if marker.exists():
        if json.loads(marker.read_text()) != wanted:
            raise PortError(f"Adaptation changed; inspect and update snapshot manually: {dest}")
        require_checkout(dest / dependency["path"], dependency["revision"])
        for name, digest in wanted["files"].items():
            path = dest / name
            actual = ("symlink:" + os.readlink(path)) if path.is_symlink() else (
                hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None)
            if actual != digest:
                raise PortError(f"Installed adaptation was edited: {path}")
        return
    if dest.exists():
        raise PortError(f"Refusing to replace existing package directory: {dest}")
    shutil.copytree(source, dest, symlinks=True)
    run(["git", "init", "-q"], cwd=dest)
    sub = dest / dependency["path"]
    run(["git", "clone", "--no-checkout", "https://github.com/" + dependency["name"] + ".git", sub])
    run(["git", "checkout", "--detach", dependency["revision"]], cwd=sub)
    run(["git", "submodule", "add", "https://github.com/" + dependency["name"] + ".git",
         dependency["path"]], cwd=dest)
    marker.write_text(json.dumps(wanted, indent=2) + "\n")
    run(["git", "add", "."], cwd=dest)
    run(["git", "-c", "user.name=N100 port setup", "-c", "user.email=n100@localhost",
         "commit", "-qm", "Prepare locked N100 adaptation snapshot"], cwd=dest)


def prepare(args):
    android = android_path(args.android_root)
    require_profile(android)
    lock = read_lock()
    for project in lock["projects"] + lock["extensions"]:
        require_checkout(android / project["path"], project["revision"])
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=android / "external/libhybris")
    device = android / "device/oneplus/billie2"
    # These exact files were inspected at the pinned upstream commit.
    checks = lock["device_changes"]["inspected_blobs"]
    for name, expected in checks.items():
        actual = run(["git", "hash-object", name], cwd=device, capture=True)
        # BoardConfig gains the controlled selinux flag below, but may not have other edits.
        if actual != expected:
            original = run(["git", "show", "HEAD:" + name], cwd=device, capture=True) + "\n"
            intended = modified_board(original, lock)
            if name != "BoardConfig.mk" or (device / name).read_text() != intended:
                raise PortError(f"Unexpected upstream edits: {device / name}")
    kernel = android / "kernel/oneplus/sm4250"
    base = kernel / "arch/arm64/configs" / lock["device_changes"]["kernel_base_config"]
    if run(["git", "diff", "--", str(base.relative_to(kernel))], cwd=kernel, capture=True):
        raise PortError("Stock kernel defconfig has local edits")
    generated = merge_config(base.read_text(), (ROOT / "kernel/sailfish.config").read_text())
    write_new(kernel / "arch/arm64/configs" / lock["device_changes"]["kernel_generated_config"], generated)
    board = device / "BoardConfig.mk"
    original = run(["git", "show", "HEAD:BoardConfig.mk"], cwd=device, capture=True) + "\n"
    intended = modified_board(original, lock)
    board.write_text(intended)
    sources = [ROOT / "adaptation/rpm", ROOT / "adaptation/droid-configs",
               ROOT / "adaptation/droid-hal-version-billie2"]
    for source, dependency in zip(sources, lock["packaging"]):
        init_package(android / dependency["parent"], source, dependency)
    print("Adaptation snapshots prepared. Apply hybris patches before building.")


def modified_board(original, lock):
    return (original + "\n# Sailfish bring-up; preserve the inspected upstream board configuration.\n"
            "BOARD_KERNEL_CMDLINE += selinux=1 androidboot.selinux=permissive\n"
            "TARGET_KERNEL_CONFIG := " + lock["device_changes"]["kernel_generated_config"] + "\n")


PROPERTIES = [
    "ro.product.device", "ro.product.vendor.device", "ro.product.model",
    "ro.product.manufacturer", "ro.board.platform", "ro.hardware",
    "ro.build.version.release", "ro.build.version.sdk", "ro.build.fingerprint",
    "ro.vendor.build.fingerprint", "ro.boot.slot_suffix", "ro.boot.dynamic_partitions",
    "ro.boot.verifiedbootstate", "ro.boot.flash.locked", "ro.crypto.state", "ro.crypto.type",
    "ro.vendor.build.version.sdk", "ro.vndk.version", "ro.treble.enabled",
    "ro.build.display.id", "ro.build.version.incremental", "ro.build.version.security_patch",
    "ro.vendor.build.security_patch", "ro.boot.hardware.sku",
]


def identify(properties):
    names = [properties.get(x, "").strip().lower() for x in
             ("ro.product.device", "ro.product.vendor.device")]
    model = properties.get("ro.product.model", "").strip().upper()
    # An expected model is supporting evidence, never a flashing authorization.
    if "billie" in names:
        return "wrong-device-nord-n10"
    if any(x in ("billie2", "oneplusn100") for x in names):
        return "n100-candidate"
    if model in ("BE2011", "BE2012", "BE2013", "BE2015"):
        return "model-only-unconfirmed"
    return "unconfirmed"


def collect_report(serial=None):
    adb = ["adb"] + (["-s", serial] if serial else [])
    if not serial:
        lines = run(["adb", "devices"], capture=True, timeout=20).splitlines()[1:]
        if len([x for x in lines if x.strip()]) != 1 or not lines[0].endswith("\tdevice"):
            raise PortError("Connect exactly one authorized Android device, or use --serial")
    props = {key: run(adb + ["shell", "getprop", key], capture=True, timeout=20)
             for key in PROPERTIES}
    report = {"schema": 1, "collected_at": datetime.now(timezone.utc).isoformat(),
              "identity": identify(props), "properties": props, "diagnostics": {}}
    commands = {
        "kernel": "uname -a", "mounts": "cat /proc/mounts", "filesystems": "cat /proc/filesystems",
        "partitions": "ls -l /dev/block/by-name /dev/block/bootdevice/by-name /dev/block/mapper",
        "fstab_vendor": "cat /vendor/etc/fstab.qcom",
        "fstab_default": "cat /vendor/etc/fstab.default",
        "fstab_emmc": "cat /vendor/etc/fstab.emmc",
        "fstab_nodata": "cat /vendor/etc/fstab_nodata.qcom",
        "fstab_odm": "cat /odm/etc/fstab.qcom",
        "fstab_root": "cat /fstab.qcom",
        "fstab_first_stage": "cat /first_stage_ramdisk/fstab.qcom",
        "fstab_paths": "ls /vendor/etc/fstab* /odm/etc/fstab* /first_stage_ramdisk/fstab*",
        "selinux_files": "ls -l /vendor/etc/selinux /system/etc/selinux",
        "selinux_vendor_version": "cat /vendor/etc/selinux/plat_sepolicy_vers.txt",
        "vintf_vendor": "cat /vendor/etc/vintf/manifest.xml",
        "vintf_paths": "ls /vendor/etc/vintf/manifest /odm/etc/vintf /system_ext/etc/vintf",
        "partition_sizes": "cat /proc/partitions",
        "mapper_names": "cat /sys/block/dm-*/dm/name",
        "inputs": "cat /proc/bus/input/devices",
    }
    # Fixed shell strings only; no user input enters the remote shell.
    for key, command in commands.items():
        try:
            report["diagnostics"][key] = run(adb + ["shell", command], capture=True, timeout=20)
        except PortError as exc:
            report["diagnostics"][key] = {"unavailable": str(exc), "stdout": exc.stdout,
                                          "stderr": exc.stderr, "returncode": exc.returncode}
    return report


def probe(args):
    report=collect_report(args.serial)
    out = write_report(args.output, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"{report['identity']}: report saved to {out}")
    print("Review before sharing; no IMEI/serial queried.")


CONFIG_DISCOVERY_COMMAND = ("ls -1d /vendor/etc/fstab* /vendor/etc/vintf/*.xml "
                            "/vendor/etc/vintf/manifest/*.xml /odm/etc/vintf/*.xml "
                            "/system_ext/etc/vintf/*.xml")
POLICY_CONFIGS = (
    "/vendor/etc/selinux/plat_sepolicy_vers.txt", "/vendor/etc/selinux/precompiled_sepolicy",
    "/vendor/etc/selinux/vendor_file_contexts", "/system/etc/selinux/plat_file_contexts",
)


def collect_configs(args):
    """Copy stock configuration files into a local ZIP, without root or device writes."""
    report=collect_report(args.serial)
    if identify(report["properties"]) != "n100-candidate":
        raise PortError("Configuration collection requires an identified Nord N100")
    adb=["adb"]+(["-s",args.serial] if args.serial else [])
    try:
        listing={"stdout":run(adb+["shell",CONFIG_DISCOVERY_COMMAND],capture=True,timeout=20),"returncode":0}
    except PortError as exc:
        listing={"stdout":exc.stdout,"stderr":exc.stderr,"returncode":exc.returncode}
    paths=sorted(set(discovered_config_paths(listing["stdout"])) | set(POLICY_CONFIGS))
    if len(paths)>256:
        raise PortError("Unexpectedly many configuration paths")
    inventory={"schema":1,"purpose":"stock configuration source inputs; not a firmware backup",
               "discovery":listing,"files":[],"device_writes":False}
    archive=io.BytesIO()
    total=0
    with tempfile.TemporaryDirectory(prefix="n100-configs-") as work, zipfile.ZipFile(
            archive,"w",compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("report.json",json.dumps(report,indent=2,ensure_ascii=False)+"\n")
        for remote in paths:
            local=Path(work)/remote.lstrip("/")
            local.parent.mkdir(parents=True,exist_ok=True)
            entry={"remote_path":remote}
            try:
                run(adb+["pull",remote,str(local)],capture=True,timeout=60)
                if local.is_symlink() or not local.is_file():
                    raise PortError("Expected a regular configuration file")
                size=local.stat().st_size
                if size>8*1024*1024 or total+size>32*1024*1024:
                    raise PortError("Configuration size budget exceeded")
                data=local.read_bytes()
                bundle.writestr("files/"+remote.lstrip("/"),data)
                total+=size
                entry.update({"status":"collected","bytes":size,"sha256":hashlib.sha256(data).hexdigest()})
            except (PortError,OSError) as exc:
                entry.update({"status":"unavailable","reason":str(exc)})
                if isinstance(exc,PortError):
                    entry.update({"stdout":exc.stdout,"stderr":exc.stderr,"returncode":exc.returncode})
            inventory["files"].append(entry)
        bundle.writestr("inventory.json",json.dumps(inventory,indent=2,ensure_ascii=False)+"\n")
    out=write_report(args.output,archive.getvalue(),binary=True)
    count=sum(f["status"]=="collected" for f in inventory["files"])
    print(f"Configuration bundle saved to {out} ({count}/{len(paths)} files collected)")
    print("Contains device configuration and a report, not user data or a complete firmware backup.")
    return out


def check_kernel(args):
    values = parse_config(Path(args.config).read_text())
    expected = parse_config((ROOT / "kernel/sailfish.config").read_text())
    failures = []
    for key, value in expected.items():
        actual = values.get(key, "n")
        if actual != value:
            failures.append(f"{key}: expected {value}, got {actual}")
    if failures:
        raise PortError("Final kernel config does not satisfy fragment:\n" + "\n".join(failures))
    print("Fragment requirements met. Also run the pinned hybris/mer-kernel-check tool.")


def inspect_boot(path):
    with path.open("rb") as stream:
        header = stream.read(4096)
    if len(header) < 1660 or header[:8] != b"ANDROID!":
        raise PortError("Not an Android v0-v2 boot image")
    kernel_size = struct.unpack_from("<I", header, 8)[0]
    ramdisk_size = struct.unpack_from("<I", header, 16)[0]
    second_size = struct.unpack_from("<I", header, 24)[0]
    page_size, version = struct.unpack_from("<II", header, 36)
    if version != 2 or page_size != 4096:
        raise PortError(f"Expected header v2 / page size 4096; got v{version} / {page_size}")
    dtb_size = struct.unpack_from("<I", header, 1648)[0]
    recovery_dtbo_size = struct.unpack_from("<I", header, 1632)[0]
    if not all((kernel_size, ramdisk_size, dtb_size)):
        raise PortError("Kernel, ramdisk and DTB must all be present")
    aligned = lambda size: (size + page_size - 1) // page_size * page_size
    required = page_size + sum(aligned(x) for x in
                               (kernel_size, ramdisk_size, second_size, recovery_dtbo_size, dtb_size))
    size = path.stat().st_size
    if size < required or size > 100663296:
        raise PortError(f"Truncated or oversized image: {size} bytes, minimum {required}")
    cmdline = (header[64:576] + header[608:1632]).split(b"\0", 1)[0].decode("ascii", errors="replace")
    return {"header_version": version, "page_size": page_size, "bytes": size,
            "kernel_bytes": kernel_size, "ramdisk_bytes": ramdisk_size, "dtb_bytes": dtb_size,
            "cmdline": cmdline, "assessment": "structural-check-only-not-boot-validation"}


def boot(args):
    print(json.dumps(inspect_boot(Path(args.image)), indent=2))


def positive(value):
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("init", help="Initialize pinned Android manifest in HABUILD_SDK")
    p.add_argument("--android-root", required=True)
    p.add_argument("--device-report", required=True, help="Read-only report from the intended stock Android device")
    p.add_argument("--sync", action="store_true")
    p.add_argument("--jobs", type=positive, default=4)
    p.set_defaults(func=init_sources)
    p = commands.add_parser("prepare", help="Install device changes and locked RPM source snapshots")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=prepare)
    p = commands.add_parser("verify-tree", help="Reject stale source profiles before a HAL build")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=lambda args: require_profile(android_path(args.android_root)))
    p = commands.add_parser("analyze-report", help="Assess a saved report offline; no ADB or device writes")
    p.add_argument("report")
    p.add_argument("--output")
    p.set_defaults(func=analyze)
    p = commands.add_parser("probe", help="Read Android device information; never writes to device")
    p.add_argument("--serial")
    p.add_argument("--output", required=True)
    p.set_defaults(func=probe)
    p = commands.add_parser("collect-configs", help="Copy actual fstab/VINTF/policy files into a local ZIP; no root")
    p.add_argument("--serial")
    p.add_argument("--output", required=True)
    p.set_defaults(func=collect_configs)
    p = commands.add_parser("check-kernel", help="Check the resolved kernel .config")
    p.add_argument("config")
    p.set_defaults(func=check_kernel)
    p = commands.add_parser("inspect-boot", help="Check Android boot image structure; does not boot it")
    p.add_argument("image")
    p.set_defaults(func=boot)
    args = parser.parse_args()
    try:
        args.func(args)
    except (PortError, OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
