#!/usr/bin/env python3
"""Read the public OS Updater catalog; never download firmware or access a phone."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.error import URLError
from urllib.request import Request, urlopen


API = "https://oxygenupdater.com/api/v2.10/"
EXPECTED_OTA = "OnePlusN100Oxygen_14.E.35_GLO_0350_2310092319"
SOURCE_REVISION = "50327ea3fe9ffdcd4c4134b022793ad3f0d33cf2"
# The public catalog rejects the generic custom UA with HTTP 403. Match the
# app protocol in utils/Network.kt and the version in app/build.gradle.kts.
APP_USER_AGENT = "Oxygen_updater_7.1.0"


def get_json(endpoint):
    request = Request(API + endpoint, headers={
        "Accept": "application/json", "User-Agent": APP_USER_AGENT})
    with urlopen(request, timeout=30) as response:
        payload = response.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("Catalog response exceeds 1 MiB")
    return json.loads(payload)


def select_unique(rows, pattern, description):
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Unexpected catalog format for " + description)
    matches = [row for row in rows if isinstance(row.get("name"), str)
               and re.fullmatch(pattern, row["name"], flags=re.IGNORECASE)]
    if len(matches) != 1:
        raise ValueError("Expected exactly one " + description + "; found " + str(len(matches)))
    result = matches[0]
    if type(result.get("id")) is not int or result["id"] <= 0:
        raise ValueError("Invalid catalog ID for " + description)
    return result


def collect(fetch=get_json):
    device = select_unique(fetch("devices/all"), r"OnePlus Nord N100\s*\(EU\)",
                           "OnePlus Nord N100 (EU)")
    method = select_unique(fetch("updateMethods/" + str(device["id"]) + "?language=en"),
                           r"Stable\s*\(full\)", "stable full update method")
    update = fetch("mostRecentUpdateData/{}/{}".format(device["id"], method["id"]))
    if not isinstance(update, dict):
        raise ValueError("Unexpected update data format")
    # The API model accepts both legacy snake_case and current camelCase names.
    ota = update.get("otaVersionNumber", update.get("ota_version_number"))
    match = ota == EXPECTED_OTA
    return {
        "schema": 1,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "source": API,
        "api_source_revision": SOURCE_REVISION,
        "expected_installed_ota": EXPECTED_OTA,
        "device": device,
        "method": method,
        "update": update,
        "catalog_ota_matches_installed": match,
        "firmware_downloaded": False,
        "package_metadata_verified": False,
        "stock_images_verified": False,
        "device_accessed": False,
        "assessment": ("catalog-match-only-package-verification-required" if match
                       else "catalog-does-not-establish-an-exact-installed-build-match"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = collect()
        # Exclusive creation preserves existing reports, including symlinks.
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
    except (OSError, URLError, ValueError) as error:
        print("Catalog query failed: " + str(error), file=sys.stderr)
        return 1
    print("Report: " + str(args.output.absolute()))
    print("Catalog OTA matches installed: " + str(result["catalog_ota_matches_installed"]))
    print("No firmware downloaded. Share the report for package selection and verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

