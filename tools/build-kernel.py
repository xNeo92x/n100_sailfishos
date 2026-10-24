#!/usr/bin/env python3
"""Build an experimental kernel/DTB/modules; this does not build a bootable OS."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys

import n100


def checkout(path, repository, revision, branch):
    if path.exists():
        n100.require_checkout(path, revision)
        for args in (("diff", "--quiet"), ("diff", "--cached", "--quiet")):
            n100.run(["git", *args], cwd=path)
    else:
        n100.run(["git", "clone", "--depth", "1", "--no-checkout", "--branch", branch,
                  "https://github.com/" + repository + ".git", path])
        if n100.run(["git", "rev-parse", "HEAD"], cwd=path, capture=True) != revision:
            n100.run(["git", "fetch", "--depth", "1", "origin", revision], cwd=path)
        n100.run(["git", "checkout", "--detach", revision], cwd=path)


def logged(command, log, env, cwd=None):
    print(f"Running {command[0]}; log: {log}", flush=True)
    with log.open("w") as stream:
        result = subprocess.run([str(x) for x in command], cwd=cwd, env=env,
                                stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise n100.PortError(f"Build step failed ({result.returncode}); inspect {log}")


def build(args):
    for tool in ("git", "gcc", "g++", "make", "perl"):
        if not shutil.which(tool):
            raise n100.PortError(f"Missing host build prerequisite: {tool}")
    work = Path(args.work_dir).expanduser().resolve()
    if work == n100.ROOT or n100.ROOT in work.parents:
        raise n100.PortError("Use a work directory outside the port source repository")
    work.mkdir(parents=True, exist_ok=True)
    pins = json.loads((n100.ROOT / "config/kernel-build-sources.json").read_text())
    lock = n100.read_lock()
    kernel_pin = next(p for p in lock["projects"] if p["path"] == "kernel/oneplus/sm4250")
    checker_pin = next(p for p in lock["extensions"] if p["path"] == "hybris/mer-kernel-check")
    kernel = work / "kernel-check-sm4250"
    checkout(kernel, kernel_pin["name"], kernel_pin["revision"], kernel_pin["branch"])
    paths = {}
    for name, pin in pins.items():
        paths[name] = work / pin["directory"]
        checkout(paths[name], pin["repository"], pin["revision"], pin["branch"])
    checker = work / "mer-kernel-check"
    checkout(checker, "mer-hybris/mer-kernel-check", checker_pin["revision"], "master")
    output = work / "kernel-config-output"
    output.mkdir(exist_ok=True)
    tools = paths["host_tools"]
    n100.run(["git", "sparse-checkout", "set", "linux-x86", "common", "path/linux-x86"], cwd=tools)
    env = os.environ.copy()
    env["PATH"] = str(paths["clang"] / "bin") + os.pathsep + str(tools / "linux-x86/bin") + os.pathsep + env.get("PATH", "") + os.pathsep + str(tools / "path/linux-x86")
    env["BISON_PKGDATADIR"] = str(tools / "common/bison")
    env["M4"] = str(tools / "linux-x86/bin/m4")
    # Fixed kernel metadata avoids embedding the build machine's username/hostname.
    epoch = int(n100.run(["git", "show", "-s", "--format=%ct", "HEAD"], cwd=kernel, capture=True))
    env["SOURCE_DATE_EPOCH"] = str(epoch)
    env["KBUILD_BUILD_USER"] = "n100"
    env["KBUILD_BUILD_HOST"] = "builder"
    env["KBUILD_BUILD_VERSION"] = "1"
    env["KBUILD_BUILD_TIMESTAMP"] = datetime.fromtimestamp(epoch, timezone.utc).strftime("%a %b %d %H:%M:%S UTC %Y")
    env["LD_LIBRARY_PATH"] = str(paths["dtc"] / "libfdt") + (os.pathsep + env["LD_LIBRARY_PATH"] if env.get("LD_LIBRARY_PATH") else "")
    # Android's lexer includes dtc-parser.h; GNU make would generate a .tab.h.
    logged([tools / "linux-x86/bin/bison", "--defines=dtc-parser.h", "-o", "dtc-parser.tab.c", "dtc-parser.y"],
           output / "dtc-parser.log", env, paths["dtc"])
    logged(["make", f"-j{args.jobs}", "NO_PYTHON=1", "NO_YAML=1", "PKG_CONFIG=false",
            "CFLAGS=-O2 -g -Wno-error=sign-compare -Wno-error=cast-qual", "dtc", "fdtoverlay"],
           output / "android-dtc-build.log", env, paths["dtc"])
    changes = lock["device_changes"]
    base = kernel / "arch/arm64/configs" / changes["kernel_base_config"]
    fragment = n100.ROOT / "kernel/sailfish.config"
    generated = kernel / "arch/arm64/configs" / changes["kernel_generated_config"]
    n100.write_new(generated, n100.merge_config(base.read_text(), fragment.read_text()))
    make = ["make", "-C", kernel, "O=" + str(output), f"-j{args.jobs}", "ARCH=arm64",
            "OEM_TARGET_PRODUCT=billie2", "CC=clang", "LD=ld.lld", "HOSTCC=gcc", "HOSTCXX=g++",
            "LLVM=1", "LLVM_IAS=1", "CROSS_COMPILE=aarch64-linux-gnu-", "CROSS_COMPILE_COMPAT=arm-linux-gnueabi-",
            "AR=llvm-ar", "NM=llvm-nm", "OBJCOPY=llvm-objcopy", "OBJDUMP=llvm-objdump",
            "READELF=llvm-readelf", "OBJSIZE=llvm-size", "STRIP=llvm-strip",
            "DTC_EXT=" + str(paths["dtc"] / "dtc")]
    logged(make + [changes["kernel_generated_config"]], output / "configure.log", env)
    config = output / ".config"
    n100.check_kernel(argparse.Namespace(config=str(config)))
    logged(["perl", checker / "mer_verify_kernel_config", config], output / "mer-check.log", env)
    logged(make + ["Image", "dtbs", "modules"], output / "kernel-build.log", env)
    n100.check_kernel(argparse.Namespace(config=str(config)))
    logged(["perl", checker / "mer_verify_kernel_config", config], output / "mer-check.log", env)
    dts = output / "arch/arm64/boot/dts/vendor/billie2"
    merged = output / "billie2-merged-check.dtb"
    logged([paths["dtc"] / "fdtoverlay", "-i", dts / "bengal.dtb", "-o", merged,
            dts / "billie2-qrd-overlay.dtbo"], output / "overlay-apply.log", env)
    image = output / "arch/arm64/boot/Image"
    with image.open("rb") as stream:
        if stream.read(64)[56:60] != b"ARM\x64":
            raise n100.PortError("Built image lacks the ARM64 Image header")
    artifacts = [image, dts / "bengal.dtb", dts / "billie2-qrd-overlay.dtbo", merged] + sorted(output.rglob("*.ko"))
    for file in artifacts[1:4]:
        with file.open("rb") as stream:
            if struct.unpack(">I", stream.read(4))[0] != 0xD00DFEED:
                raise n100.PortError("Invalid device-tree header: " + str(file))
    warnings = (output / "mer-check.log").read_text().count("WARNING:")
    result = {"scope": "kernel, DTB, DTBO and modules built; not an OS/boot validation",
              "boot_tested": False, "flashable_sailfish_image": False,
              "kernel_revision": kernel_pin["revision"], "tools": pins,
              "checker_revision": checker_pin["revision"],
              "configuration_checks": {"local_fragment_passed": True, "upstream_exit": 0,
                                       "upstream_optional_warnings": warnings},
              "fragment_sha256": hashlib.sha256(fragment.read_bytes()).hexdigest(),
              "config_sha256": hashlib.sha256(config.read_bytes()).hexdigest(),
              "overlay_apply_exit": 0,
              "artifacts": [{"path": str(p.relative_to(output)), "bytes": p.stat().st_size,
                             "sha256": hashlib.sha256(p.read_bytes()).hexdigest()} for p in artifacts]}
    (output / "build-result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(f"Experimental kernel build completed: {output / 'build-result.json'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(Path.home() / ".cache/n100-kernel-build"))
    parser.add_argument("--jobs", type=n100.positive, default=min(os.cpu_count() or 1, 8))
    args = parser.parse_args()
    try:
        build(args)
    except (n100.PortError, OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
