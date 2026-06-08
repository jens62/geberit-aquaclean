#!/usr/bin/env python3
"""
geberit-firmware-download.py — Download Geberit AquaClean firmware from the cloud
===================================================================================

Fetches the latest active firmware for a given device from Geberit's production
firmware API and downloads it to the current directory.

Does NOT require a running bridge or a BLE connection.

Usage
-----
Auto-detect series from the device's reported firmware version string:

    python tools/geberit-firmware-download.py --firmware "RS28.0 TS199"

Specify series and variants directly:

    python tools/geberit-firmware-download.py --series 248 --variants 1,2,3

List all available series numbers in the cloud:

    python tools/geberit-firmware-download.py --list

Save to a specific output path:

    python tools/geberit-firmware-download.py --firmware "RS28.0 TS199" --output /tmp/

Known series
------------
  248  Mera Comfort (variants 1,2,3) / AcSela (variant 6)

For other devices pass --firmware with the version string shown by the bridge
(GET /info/firmware or the HACS firmware_version sensor) and the script
detects the series automatically.
"""

import argparse
import asyncio
import os
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from aquaclean_console_app.FirmwareUpdateService import (
    fetch_all_firmware_packages,
    _find_series_and_variant,
    _parse_rs_ts,
)


def _find_download_package(series: int, variants: list, packages: list) -> dict | None:
    """Return the best active package dict for the given series/variants."""
    device_variants = set(variants)
    best_pkg = None
    best_ver = None
    for pkg in packages:
        if not pkg.get("isActive"):
            continue
        fp = pkg.get("firmwarePackage", {})
        if fp.get("series") != series:
            continue
        if device_variants and not device_variants.intersection(fp.get("variants", [])):
            continue
        for node in fp.get("nodeFirmwares", []):
            if node.get("nodeId") not in ("0x01", 1):
                continue
            rv = node.get("rsTsVersion", "")
            if "." not in rv:
                continue
            try:
                rs, ts = int(rv.split(".")[0]), int(rv.split(".")[1])
                if best_ver is None or (rs, ts) > best_ver:
                    best_ver = (rs, ts)
                    best_pkg = pkg
            except ValueError:
                continue
    return best_pkg


async def cmd_list(packages: list) -> None:
    seen = {}
    for pkg in packages:
        if not pkg.get("isActive"):
            continue
        fp = pkg.get("firmwarePackage", {})
        series = fp.get("series")
        variants = fp.get("variants", [])
        date = (fp.get("packageCreatedDate") or "")[:10]
        for node in fp.get("nodeFirmwares", []):
            if node.get("nodeId") not in ("0x01", 1):
                continue
            rv = node.get("rsTsVersion", "")
            if "." not in rv:
                continue
            try:
                rs, ts = int(rv.split(".")[0]), int(rv.split(".")[1])
                key = series
                if key not in seen or (rs, ts) > seen[key][0]:
                    seen[key] = ((rs, ts), variants, date)
            except ValueError:
                continue

    print(f"{'Series':>8}  {'Variants':<16}  {'Latest version':<18}  {'Date'}")
    print("-" * 62)
    for series in sorted(seen):
        ver_tuple, variants, date = seen[series]
        ver_str = f"RS{ver_tuple[0]:02d}.0 TS{ver_tuple[1]}"
        variants_str = ",".join(str(v) for v in sorted(variants))
        print(f"{series:>8}  {variants_str:<16}  {ver_str:<18}  {date}")


async def cmd_download(series: int, variants: list, output_dir: str, packages: list) -> None:
    pkg = _find_download_package(series, variants, packages)
    if pkg is None:
        print(f"ERROR: no active firmware found for series={series} variants={variants}", file=sys.stderr)
        sys.exit(1)

    fp = pkg["firmwarePackage"]
    download_url = pkg["downloadUrl"]
    auth_header = pkg["downloadHeaders"]["Authorization"]
    date = (fp.get("packageCreatedDate") or "")[:10]

    # Version from main node
    cloud_version = None
    for node in fp.get("nodeFirmwares", []):
        if node.get("nodeId") in ("0x01", 1):
            rv = node.get("rsTsVersion", "")
            if "." in rv:
                rs, ts = int(rv.split(".")[0]), int(rv.split(".")[1])
                cloud_version = f"RS{rs:02d}.0 TS{ts}"
            break

    # Derive filename from URL path
    url_path = download_url.rstrip("/")
    pkg_id = url_path.split("/")[-2] if url_path.endswith("/download") else url_path.split("/")[-1]
    filename = f"{pkg_id}.bin"
    output_path = os.path.join(output_dir, filename)

    print(f"Series:   {series}  (variants {variants})")
    print(f"Version:  {cloud_version}")
    print(f"Date:     {date}")
    print(f"Output:   {output_path}")
    print()

    proc = await asyncio.create_subprocess_exec(
        "curl", "--progress-bar", "-L", "--output", output_path,
        "-H", f"Authorization: {auth_header}",
        download_url,
    )
    await proc.wait()
    if proc.returncode != 0:
        print(f"ERROR: curl exited with code {proc.returncode}", file=sys.stderr)
        sys.exit(1)

    size = os.path.getsize(output_path)
    print(f"\nDownloaded {size:,} bytes → {output_path}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Geberit AquaClean firmware from the cloud.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/geberit-firmware-download.py --firmware 'RS28.0 TS199'\n"
            "  python tools/geberit-firmware-download.py --series 248 --variants 1,2,3\n"
            "  python tools/geberit-firmware-download.py --list"
        ),
    )
    parser.add_argument("--firmware", metavar="VERSION",
                        help="Device firmware string (e.g. 'RS28.0 TS199'); auto-detects series")
    parser.add_argument("--series", type=int, metavar="N",
                        help="Series number (e.g. 248 for Mera Comfort)")
    parser.add_argument("--variants", metavar="1,2,3",
                        help="Comma-separated variant list (used with --series; default: all)")
    parser.add_argument("--output", metavar="DIR", default=".",
                        help="Directory to save the firmware file (default: current directory)")
    parser.add_argument("--list", action="store_true",
                        help="List all available series and their latest firmware versions")
    args = parser.parse_args()

    if not args.list and not args.firmware and not args.series:
        parser.error("one of --firmware, --series, or --list is required")

    print("Fetching firmware catalogue from Geberit cloud…")
    packages = await fetch_all_firmware_packages()
    print(f"  {len(packages)} packages retrieved.\n")

    if args.list:
        await cmd_list(packages)
        return

    if args.firmware:
        device_tuple = _parse_rs_ts(args.firmware)
        if device_tuple is None:
            print(f"ERROR: cannot parse firmware string {args.firmware!r}", file=sys.stderr)
            sys.exit(1)
        rs_ts_str = f"{device_tuple[0]}.{device_tuple[1]}"
        match = _find_series_and_variant(rs_ts_str, packages)
        if match is None:
            print(f"ERROR: firmware {args.firmware!r} not found in cloud catalogue", file=sys.stderr)
            sys.exit(1)
        series, variants, _ = match
        print(f"Auto-detected series={series} variants={variants} from {args.firmware!r}\n")
    else:
        series = args.series
        variants = [int(v.strip()) for v in args.variants.split(",")] if args.variants else []

    os.makedirs(args.output, exist_ok=True)
    await cmd_download(series, variants, args.output, packages)


if __name__ == "__main__":
    asyncio.run(main())
