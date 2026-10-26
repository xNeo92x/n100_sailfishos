#!/usr/bin/env python3
"""Host-side Nord N100 port tools. Python standard library; no flashing commands."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DHD_MOUNT_FIXUP_BLOB = "7717982261ad321df173e489145a2f58126691c6"


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
            value=(fqname.text or "").strip()
            match=re.fullmatch(r"@(\d+\.\d+)::([^/]+)/(.+)", value)
            if match:
                versions.add(match[1])
                instances.add((match[2],match[3]))
            elif hal.get("format")=="aidl":
                match=re.fullmatch(r"([^/]+)/(.+)",value)
                if match:
                    instances.add((match[1],match[2]))
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


def parse_android_fstab(text):
    """Read five-column Android fstab records without selecting a boot variant."""
    entries=[]
    for number,line in enumerate(text.splitlines(),1):
        try:
            fields=shlex.split(line,comments=True)
        except ValueError as exc:
            raise PortError(f"Invalid fstab quoting at line {number}") from exc
        if not fields:
            continue
        if len(fields)!=5:
            raise PortError(f"Expected five fstab fields at line {number}")
        entries.append({"source":fields[0],"target":fields[1],"filesystem":fields[2],
                        "mount_options":fields[3].split(","),"fs_mgr_flags":fields[4].split(",")})
    return entries


def selinux_policy_header(data):
    """Inspect the kernel policydb header, not CIL compatibility text."""
    if len(data)<32:
        raise PortError("Truncated SELinux policy header")
    magic,length=struct.unpack_from("<II",data)
    if magic!=0xF97CFF8C or length!=8 or data[8:16]!=b"SE Linux":
        raise PortError("Unsupported SELinux policy format")
    return {"identifier":"SE Linux","binary_policy_version":struct.unpack_from("<I",data,16)[0],
            "scope":"header only; policy rules and runtime compatibility not verified"}


def read_config_bundle(path):
    """Validate bounded ZIP inputs against the collector inventory; never extract."""
    try:
        with zipfile.ZipFile(path) as archive:
            members=archive.infolist()
            names=[m.filename for m in members]
            if len(names)!=len(set(names)) or len(names)>258:
                raise PortError("Duplicate or excessive ZIP members")
            if sum(m.file_size for m in members)>34*1024*1024 or any(m.file_size>8*1024*1024 for m in members):
                raise PortError("Configuration bundle exceeds size limits")
            if any((m.external_attr>>16)&0o170000==0o120000 for m in members):
                raise PortError("Symlink ZIP members are not supported")
            if "report.json" not in names or "inventory.json" not in names:
                raise PortError("Missing report or inventory in configuration bundle")
            inventory=json.loads(archive.read("inventory.json"))
            report=json.loads(archive.read("report.json"))
            records=inventory.get("files") if isinstance(inventory,dict) else None
            if not isinstance(records,list) or len(records)>256:
                raise PortError("Invalid configuration inventory")
            if not isinstance(report,dict) or not isinstance(report.get("properties"),dict):
                raise PortError("Invalid device report in bundle")
            files={}
            allowed_members={"report.json","inventory.json"}
            seen=set()
            for record in records:
                if not isinstance(record,dict):
                    raise PortError("Invalid configuration record")
                remote=record.get("remote_path","")
                if not isinstance(remote,str) or (remote not in POLICY_CONFIGS and remote not in discovered_config_paths(remote)):
                    raise PortError("Unexpected configuration path in inventory")
                if remote in seen:
                    raise PortError("Duplicate configuration path")
                seen.add(remote)
                if record.get("status")=="unavailable":
                    continue
                if record.get("status")!="collected":
                    raise PortError("Unknown inventory status")
                member="files"+remote
                if member not in names:
                    raise PortError(f"Missing collected configuration: {remote}")
                data=archive.read(member)
                if len(data)!=record.get("bytes") or hashlib.sha256(data).hexdigest()!=record.get("sha256"):
                    raise PortError(f"Configuration hash or size mismatch: {remote}")
                files[remote]=data
                allowed_members.add(member)
            if set(names)!=allowed_members:
                raise PortError("Unexpected or unlisted ZIP members")
            return report,inventory,files
    except (zipfile.BadZipFile,KeyError,json.JSONDecodeError) as exc:
        raise PortError("Invalid configuration bundle") from exc


def inspect_config_bundle(path):
    report,inventory,files=read_config_bundle(path)
    assessment=analyze_report(report)
    fstabs={}
    manifests={}
    for name,data in sorted(files.items()):
        if name in discovered_config_paths(name) and name.startswith("/vendor/etc/fstab"):
            fstabs[name]=parse_android_fstab(data.decode("utf-8"))
        elif "/vintf/" in name and name.endswith(".xml") and not name.endswith("compatibility_matrix.xml"):
            manifests[name]=vintf_inventory(data.decode("utf-8"))
    policy=files.get("/vendor/etc/selinux/precompiled_sepolicy")
    return {"schema":1,"profile":assessment["profile"],"model":assessment["model"],
            "source_compatibility":assessment["source_compatibility"],"deployable":False,
            "inventory_records":len(inventory["files"]),"verified_files":len(files),
            "file_sha256":{name:hashlib.sha256(data).hexdigest() for name,data in sorted(files.items())},
            "fstab_variants":fstabs,"active_fstab_variant":None,
            "fstab_selection_note":"Files are alternatives. Neither name nor directory listing proves which init selects.",
            "vintf_documents":manifests,
            "vintf_selection_note":"ODM manifest_2 and manifest_8 are alternate SKUs, not simultaneously active declarations.",
            "selinux_policy":selinux_policy_header(policy) if policy else None,
            "selinux_compatibility_version":files.get("/vendor/etc/selinux/plat_sepolicy_vers.txt",b"").decode().strip() or None,
            "runtime_note":"Stock Android 11 policy and contexts must not replace policy built for an Android 13 HAL without validation."}


def inspect_configs(args):
    result=inspect_config_bundle(Path(args.bundle))
    content=json.dumps(result,indent=2,ensure_ascii=False)+"\n"
    if args.output:
        out=write_report(args.output,content)
        print(f"Configuration assessment saved to {out}")
    else:
        print(content,end="")


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


def build_dependency_manifest():
    content = (ROOT / "manifests/build-dependencies.xml").read_text()
    root = ET.fromstring(content)
    projects = [element.attrib for element in root.findall("project")]
    expected_paths = {"packages/apps/SimpleSettingsConfig", "vendor/apn"}
    if (root.tag != "manifest" or len(projects) != 2
            or {p.get("path") for p in projects} != expected_paths
            or any(not re.fullmatch(r"[0-9a-f]{40}", p.get("revision", "")) for p in projects)):
        raise PortError("Invalid pinned build-dependency manifest")
    return content, projects


def install_build_dependency_manifest(android):
    content, projects = build_dependency_manifest()
    write_new(android / ".repo/local_manifests/n100-build-dependencies.xml", content)
    return projects


def verify_build_dependencies(android):
    content, projects = build_dependency_manifest()
    installed = android / ".repo/local_manifests/n100-build-dependencies.xml"
    if installed.is_symlink() or not installed.is_file() or installed.read_text() != content:
        raise PortError("Build dependencies missing or changed; run n100.py sync-build-deps first")
    for project in projects:
        require_checkout(android / project["path"], project["revision"])


def sync_build_dependencies(args):
    android = android_path(args.android_root)
    require_profile(android)
    projects = install_build_dependency_manifest(android)
    # Do not rebase local edits, replace unrelated directories or sync patched HAL projects.
    for project in projects:
        path = android / project["path"]
        if path.is_symlink():
            raise PortError(f"Refusing build-dependency directory symlink: {path}")
        if path.exists():
            require_checkout(path, project["revision"])
            if run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=path, capture=True):
                raise PortError(f"Build dependency has local changes: {path}")
    run(["repo", "sync", "-c", "-j1", "--no-clone-bundle", "--fail-fast"]
        + [p["path"] for p in projects], cwd=android)
    verify_build_dependencies(android)
    print("Pinned SimpleSettingsConfig and APN build dependencies are ready.")


def verify_tree(args):
    android = android_path(args.android_root)
    require_profile(android)
    verify_build_dependencies(android)


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
    install_build_dependency_manifest(android)
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


def boot_mount_mapping(report):
    assessment = analyze_report(report)
    if assessment["source_compatibility"] != "candidate-generation-match":
        raise PortError("Boot mapping requires the intended stock Android 11 N100 report")
    physical, _ = block_aliases(diagnostic_text(report.get("diagnostics", {}).get("partitions", "")))
    mapping = {}
    for name, target in physical.items():
        if re.fullmatch(r"[A-Za-z0-9_.-]+", name) and re.fullmatch(
                r"/dev/block/(?:sd[a-z]+[1-9][0-9]*|mmcblk[0-9]+p[1-9][0-9]*)", target):
            mapping[name] = "/dev/" + target.rsplit("/", 1)[1]
    if "userdata" not in mapping:
        raise PortError("Report has no physical userdata mapping; dm-* nodes are not boot inputs")
    return dict(sorted(mapping.items()))


def mount_fixup_script(mapping, prefixes=("/dev/block/bootdevice/by-name/", "/dev/block/by-name/")):
    # Only physical aliases are rewritten. Logical super partitions need separate runtime setup.
    lines = [
        "#!/bin/sh", "# Generated from the intended phone's physical by-name evidence.",
        "# No device writes, slot selection, decryption or logical-partition mapping.",
        "set -eu",
        'if [ "$#" -lt 2 ] || [ "$1" != billie2 ]; then',
        '    echo "Usage: fixup-mountpoints billie2 FILE..." >&2', "    exit 1", "fi", "shift",
        'for file in "$@"; do',
        '    if [ ! -f "$file" ] || [ -L "$file" ]; then',
        '        echo "Expected a regular generated mountpoint input: $file" >&2',
        "        exit 1", "    fi", "done",
        'echo "Fixing physical mount-point aliases for billie2"',
        "sed -i \\",
    ]
    for name in sorted(mapping, key=lambda value: (-len(value), value)):
        escaped = name.replace(".", r"\.")
        for prefix in prefixes:
            expression = "s#" + prefix + escaped + r"\([^[:alnum:]_.-]\|$\)#" + mapping[name] + r"\1#g"
            lines.append("    -e " + shlex.quote(expression) + " \\")
    lines.append('    "$@"')
    return "\n".join(lines) + "\n"


def prepare_boot_inputs(args):
    android = android_path(args.android_root)
    require_profile(android)
    lock = read_lock()
    boot = next(project for project in lock["extensions"] if project["path"] == "hybris/hybris-boot")
    require_checkout(android / boot["path"], boot["revision"])
    report_data = Path(args.device_report).read_bytes()
    mapping = boot_mount_mapping(json.loads(report_data))
    content = mount_fixup_script(mapping)
    path = android / "hybris/fixup-mountpoints"
    # The pinned hybris-boot Android.mk prefers this sibling override over its own script.
    write_new(path, content)
    path.chmod(0o755)
    write_new(android / ".n100-boot-inputs.json", json.dumps({
        "schema": 1, "lock_sha256": lock_digest(),
        "report_sha256": hashlib.sha256(report_data).hexdigest(),
        "physical_mount_mapping": mapping,
        "fixup_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "logical_partitions_implemented": False,
        "android_data_decryption_implemented": False,
        "boot_tested": False,
    }, indent=2) + "\n")
    print(f"Boot mount fixup ready: physical userdata -> {mapping['userdata']}")
    print("Existing Android encryption and logical HAL mounts remain separate bring-up work.")


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


def git_blob_identity(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()


def prepare_rpm_inputs(args):
    android = android_path(args.android_root)
    require_profile(android)
    path = android / "build/make/tools/fs_config/fs_config_generator.py"
    if path.is_symlink() or not path.is_file():
        raise PortError("Expected the regular Android fs_config_generator.py source file")
    replacement = (ROOT / "compat/fs_config_generator/fs_config_generator.py").read_bytes()
    upstream_blob = "098fde664f612626bc6342aa46808d467145281c"
    python3_blob = "44480b8aa4df0a85f9a82d772a1d7f4b3f763ffc"
    if git_blob_identity(replacement) != python3_blob:
        raise PortError("Bundled Python 3 fs_config generator differs from the reviewed upstream file")
    actual = git_blob_identity(path.read_bytes())
    if actual not in (upstream_blob, python3_blob):
        raise PortError("Android fs_config generator has unreviewed changes; inspect manually")
    if actual == upstream_blob:
        mode = path.stat().st_mode & 0o777
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".n100-fs-config-", dir=str(path.parent),
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(replacement)
            temporary.chmod(mode)
            os.replace(str(temporary), str(path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    write_new(android / ".n100-rpm-inputs.json", json.dumps({
        "schema": 1,
        "fs_config_generator": {
            "path": str(path.relative_to(android)),
            "original_blob": upstream_blob,
            "replacement_blob": python3_blob,
            "source_repository": "LineageOS/android_build",
            "source_commit": "216978e7c4bc20df2464e88f72f98bd302b419fd",
            "purpose": "Python 3 aidarray generation in Sailfish SDK tooling",
        },
    }, indent=2) + "\n")
    print("RPM fs_config generator is ready for Python 3.")


def install_rpm_mount_fixup(android):
    lock = read_lock()
    dependency = next(p for p in lock["packaging"] if p["path"] == "dhd")
    require_checkout(android / dependency["parent"] / dependency["path"], dependency["revision"])
    override = android / "hybris/fixup-rpm-mountpoints"
    if override.is_symlink() or not override.is_file() or not os.access(str(override), os.X_OK):
        raise PortError("Missing executable report-derived hybris/fixup-rpm-mountpoints")
    path = android / "rpm/dhd/droid-hal-device.inc"
    if path.is_symlink() or not path.is_file():
        raise PortError("Expected the regular droid-hal-device.inc source file")
    original_blob = DHD_MOUNT_FIXUP_BLOB
    before = "hybris/hybris-boot/fixup-mountpoints %{device} tmp/units/*"
    after = "hybris/fixup-rpm-mountpoints %{device} tmp/units/*"
    data = path.read_bytes()
    content = data.decode("utf-8")
    if git_blob_identity(data) == original_blob and content.count(before) == 1:
        replacement = content.replace(before, after)
        mode = path.stat().st_mode & 0o777
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".n100-dhd-mounts-", dir=str(path.parent),
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(replacement.encode("utf-8"))
            temporary.chmod(mode)
            os.replace(str(temporary), str(path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    elif (content.count(after) != 1 or
          git_blob_identity(content.replace(after, before).encode("utf-8")) != original_blob):
        raise PortError("droid-hal-device.inc has unreviewed changes; inspect manually")
    write_new(android / ".n100-rpm-mounts.json", json.dumps({
        "schema": 1, "dhd_revision": dependency["revision"],
        "original_include_blob": original_blob,
        "installed_include_blob": git_blob_identity(path.read_bytes()),
        "mount_fixup": "hybris/fixup-rpm-mountpoints",
        "slotselect_runtime_implemented": False,
        "boot_tested": False,
    }, indent=2) + "\n")
    print("RPM mount generation now uses the report-derived billie2 fixup.")
    print("A/B firmware slot selection remains runtime bring-up work.")


def prepare_rpm_mounts(args):
    # Keep the boot override unchanged; makefstab additionally emits by-partlabel paths.
    prepare_boot_inputs(args)
    android = android_path(args.android_root)
    mapping = boot_mount_mapping(json.loads(Path(args.device_report).read_bytes()))
    content = mount_fixup_script(mapping, prefixes=(
        "/dev/block/bootdevice/by-name/", "/dev/block/by-name/", "/dev/disk/by-partlabel/"))
    path = android / "hybris/fixup-rpm-mountpoints"
    write_new(path, content)
    path.chmod(0o755)
    install_rpm_mount_fixup(android)


def prepare_rpm_spec(args):
    android = android_path(args.android_root)
    require_profile(android)
    dependency = next(p for p in read_lock()["packaging"] if p["parent"] == "rpm")
    destination = android / "rpm"
    require_checkout(destination / dependency["path"], dependency["revision"])
    names = ("droid-hal-billie2.spec", "helpers/remove-android-root-aliases.sh",
             "helpers/install-android-runtime.sh")
    source = ROOT / "adaptation/rpm"
    if {str(p.relative_to(source)) for p in source.rglob("*") if p.is_file() or p.is_symlink()} != set(names):
        raise PortError("Unexpected RPM adaptation source files; inspect the snapshot migration")
    data = {}
    for name in names:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise PortError(f"Expected a regular RPM adaptation source: {path}")
        data[name] = path.read_bytes()
    wanted = {"dependency": dependency, "files": {
        name: hashlib.sha256(content).hexdigest() for name, content in data.items()}}
    marker = destination / ".n100-prepared.json"
    if marker.is_symlink() or not marker.is_file():
        raise PortError("Missing RPM adaptation snapshot marker; prepare sources first")
    installed = json.loads(marker.read_text())
    if not isinstance(installed, dict) or installed.get("dependency") != dependency or not isinstance(installed.get("files"), dict):
        raise PortError("Unexpected RPM snapshot dependency metadata")
    for name, digest in installed["files"].items():
        if name not in names:
            raise PortError("Unexpected files in RPM snapshot metadata")
        path = destination / name
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PortError(f"Installed RPM snapshot has local edits: {path}")
    if installed == wanted:
        print("RPM spec, staged-root cleanup and runtime payload helper are ready.")
        return
    predecessors = (
        {names[0]: "b6184db55338fa680556ad2a4db65a30b33d15d5"},
        {names[0]: "f80ea41b7a5f0c8b68c60673df2a9faa0cf1d040",
         names[1]: "554019209560cca2338abdd03d64a5bb07af15f3"},
    )
    current = {name: git_blob_identity((destination / name).read_bytes()) for name in installed["files"]}
    if current not in predecessors:
        raise PortError("RPM snapshot is not the reviewed predecessor; inspect manually")
    helpers = destination / "helpers"
    additions = [destination / name for name in names if name not in installed["files"]]
    if helpers.is_symlink() or any(path.exists() or path.is_symlink() for path in additions):
        raise PortError("Refusing to overwrite an existing RPM cleanup helper")
    # All old state has been checked; update only owned files and their snapshot marker.
    for name, content in data.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = (source / name).stat().st_mode & 0o777
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".n100-rpm-spec-", dir=str(path.parent),
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(content)
            temporary.chmod(mode)
            os.replace(str(temporary), str(path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    marker.write_text(json.dumps(wanted, indent=2) + "\n")
    print("RPM spec upgraded with root-alias cleanup and explicit Android runtime payloads.")


def prepare_config_spec(args):
    """Upgrade reviewed config inputs; preserve existing sparse and upstream files."""
    android = android_path(args.android_root)
    require_profile(android)
    dependency = next(p for p in read_lock()["packaging"] if p["parent"] == "hybris/droid-configs")
    destination = android / dependency["parent"]
    require_checkout(destination / dependency["path"], dependency["revision"])
    source = ROOT / "adaptation/droid-configs"
    spec_name = "rpm/droid-config-billie2.spec"
    additions = {"kickstart/pack_package-droid-updater", "kickstart/attachment_hybris"}

    def digest(path):
        if path.is_symlink():
            return "symlink:" + os.readlink(path)
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None

    wanted = {"dependency": dependency, "files": {
        str(path.relative_to(source)): digest(path) for path in source.rglob("*")
        if path.is_file() or path.is_symlink()}}
    marker = destination / ".n100-prepared.json"
    if marker.is_symlink() or not marker.is_file():
        raise PortError("Missing config snapshot marker; prepare sources first")
    installed = json.loads(marker.read_text())
    if (not isinstance(installed, dict) or installed.get("dependency") != dependency or
            not isinstance(installed.get("files"), dict) or
            set(installed["files"]) not in (set(wanted["files"]), set(wanted["files"]) - additions)):
        raise PortError("Unexpected config snapshot metadata")
    for name, expected in installed["files"].items():
        path = destination / name
        if any(parent.is_symlink() for parent in path.parents if parent != destination.parent):
            raise PortError(f"Config snapshot has a symlink parent: {path}")
        if digest(path) != expected:
            raise PortError(f"Installed config snapshot has local edits: {path}")
    if installed == wanted:
        print("Config inputs are ready: ofono replacement and rootfs archive packaging.")
        return
    spec = destination / spec_name
    source_spec = source / spec_name
    if (spec.is_symlink() or source_spec.is_symlink() or
            git_blob_identity(spec.read_bytes()) not in (
                "8ea1bd5ce17e05abac0cbb689f1946010d56ee6b",  # before ofono replacement
                "343bd7aadd5eae6a028fe94cc1fdd5d08d971058") or
            any(expected != wanted["files"][name] for name, expected in installed["files"].items()
                if name != spec_name)):
        raise PortError("Config snapshot is not the reviewed predecessor; inspect manually")
    missing = set(wanted["files"]) - set(installed["files"])
    for name in missing:
        path = destination / name
        if path.exists() or path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
            raise PortError(f"Refusing an existing config packaging path: {path}")
        if (source / name).is_symlink():
            raise PortError(f"Expected regular config packaging source: {name}")
    # Validate every input before changing any file. Never copy the upstream submodule.
    for name in sorted(missing | {spec_name}):
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix=".n100-config-", dir=str(path.parent),
                                             delete=False) as stream:
                temporary = Path(stream.name)
                stream.write((source / name).read_bytes())
            temporary.chmod((source / name).stat().st_mode & 0o777)
            os.replace(str(temporary), str(path))
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    marker.write_text(json.dumps(wanted, indent=2) + "\n")
    print("Config inputs upgraded: ofono replacement and rootfs archive packaging.")


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
    p = commands.add_parser("sync-build-deps", help="Add only the pinned missing Lineage build dependencies")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=sync_build_dependencies)
    p = commands.add_parser("prepare-boot", help="Generate physical mount fixups from the saved device report")
    p.add_argument("--android-root", required=True)
    p.add_argument("--device-report", required=True)
    p.set_defaults(func=prepare_boot_inputs)
    p = commands.add_parser("verify-tree", help="Reject stale source profiles and missing build dependencies")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=verify_tree)
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
    p = commands.add_parser("inspect-configs", help="Verify and inspect a configuration ZIP offline")
    p.add_argument("bundle")
    p.add_argument("--output")
    p.set_defaults(func=inspect_configs)
    p = commands.add_parser("prepare-rpm", help="Install the reviewed Python 3 fs_config generator backport")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=prepare_rpm_inputs)
    p = commands.add_parser("prepare-rpm-mounts", help="Use report-derived mount fixups during HAL RPM packaging")
    p.add_argument("--android-root", required=True)
    p.add_argument("--device-report", required=True)
    p.set_defaults(func=prepare_rpm_mounts)
    p = commands.add_parser("prepare-rpm-spec", help="Upgrade the owned RPM snapshot with root cleanup and runtime payload staging")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=prepare_rpm_spec)
    p = commands.add_parser("prepare-config-spec", help="Upgrade reviewed config spec and rootfs packaging inputs")
    p.add_argument("--android-root", required=True)
    p.set_defaults(func=prepare_config_spec)
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
