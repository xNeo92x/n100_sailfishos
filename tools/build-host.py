#!/usr/bin/env python3
"""Check a CachyOS/Linux build host and print actionable, local build paths."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import shlex

import n100


def inspect(work):
    ancestor = work
    while not ancestor.exists():
        ancestor = ancestor.parent
    free = shutil.disk_usage(ancestor).free
    ram = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                ram = int(line.split()[1]) * 1024
    except OSError:
        pass
    missing = [name for name in ("git", "python3", "adb", "curl", "tar", "xz") if not shutil.which(name)]
    blockers = []
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        blockers.append("The official HADK host workflow requires Linux x86_64")
    if free < 200 * 1024**3:
        blockers.append("Reserve at least 200 GiB free on the build filesystem (250–300 GiB preferred)")
    if missing:
        blockers.append("Missing host tools: " + ", ".join(missing))
    filesystem = None
    if shutil.which("findmnt"):
        filesystem = json.loads(n100.run(["findmnt", "--json", "--target", ancestor,
                                         "--output", "FSTYPE,OPTIONS"], capture=True))["filesystems"][0]
        if filesystem["fstype"] in ("vfat", "exfat", "ntfs", "ntfs3", "cifs", "nfs", "nfs4"):
            blockers.append("Use a local Linux filesystem with Unix ownership, links and permissions")
        if "nosuid" in filesystem["options"].split(","):
            blockers.append("SDKs require a filesystem mounted without nosuid")
    return {"host": platform.system(), "architecture": platform.machine(),
            "work_directory": str(work), "free_gib": round(free / 1024**3, 1),
            "ram_gib": round(ram / 1024**3, 1) if ram else None, "cpus": os.cpu_count(),
            "filesystem": filesystem, "missing_tools": missing, "blockers": blockers,
            "host_prerequisites_passed": not blockers,
            "sdk_status": "not checked; enter each official SDK to validate its build tools",
            "android_root": str(work / "android"),
            "firmware_inputs": str(work / "firmware/files"),
            "advice": "16 GiB RAM or more recommended; choose make job count for available RAM"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", default=str(Path.home() / "n100-build"))
    parser.add_argument("--output")
    parser.add_argument("--initialize", action="store_true", help="Write build.env after host prerequisites pass")
    args = parser.parse_args()
    try:
        result = inspect(n100.android_path(args.work_dir))
        content = json.dumps(result, indent=2) + "\n"
        if args.output:
            print("Host report saved to", n100.write_report(args.output, content))
        print(content, end="")
        if result["blockers"]:
            parser.exit(1)
        if args.initialize:
            work = Path(result["work_directory"])
            work.mkdir(parents=True, exist_ok=True)
            environment = {"N100_PORT": str(n100.ROOT), "N100_FIRMWARE_WORK": str(work),
                           "ANDROID_ROOT": str(work / "android"),
                           "N100_DEVICE_REPORT": str(work / "firmware/device-report.json"),
                           "PLATFORM_SDK_ROOT": "/srv/sailfishos"}
            content = "# Generated N100 build paths. Source inside both HADK SDK shells.\n"
            content += "".join(f"export {key}={shlex.quote(value)}\n" for key, value in environment.items())
            content += 'source "$N100_PORT/config/port.env"\n'
            n100.write_new(work / "build.env", content)
            print("Build paths saved to", work / "build.env")
    except (n100.PortError, OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
