"""
Geberit cloud firmware update checker.

Fetches all firmware packages from Geberit's production API (no series parameter
needed), identifies the device's product series from the BLE firmware version
string, and determines whether an update is available.

Series detection algorithm
--------------------------
1. Parse the BLE firmware string, e.g. "RS28.0 TS199" → rsTsVersion "28.199".
2. GET /api/firmwares (no filter) returns ~170 packages covering all series.
3. Find the package whose nodeFirmwares contains rsTsVersion "28.199" → series 248.
4. From the same response, find isActive=True packages for series 248 → latest.
5. Compare (device RS, TS) < (cloud RS, TS) → update available.

This avoids hardcoding the series number and works for any AquaClean variant.

Collision handling
------------------
A handful of very old rsTsVersion values (e.g. "4.39") appear in more than one
series.  For those, we prefer the series where the version is NOT the current
active release — a real device running "4.39" against a series whose active
firmware is "30.206" is clearly on old firmware for that series.  If ambiguity
remains, we log a warning and pick the lowest series number.  In practice,
devices running firmware RS14+ are unambiguous.

SSL note
--------
Geberit's firmware API uses an internal CA that Python's urllib does not trust.
We call curl via asyncio subprocess instead; curl uses the system certificate
store which trusts the CA on macOS and modern Linux distributions.
"""

import asyncio
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_API_URL = "https://prod.firmwarev1.services.geberit.com/api/firmwares"
_AUTH    = "Basic aG9tZWFwcDozVkd5VWNWM25RMzNZc2dR"
_UA      = ("FwsLib/11.1.46 (Date 251106; FirmwarePackageDto.DtoTypeVersion 1.2; "
            "Service 1.3; Client Geberit Home 2.13.2)")


async def fetch_all_firmware_packages() -> list:
    """Fetch all firmware packages from Geberit cloud (all product series)."""
    proc = await asyncio.create_subprocess_exec(
        "curl", "--silent", "--compressed", _API_URL,
        "-H", f"Authorization: {_AUTH}",
        "-H", f"User-Agent: {_UA}",
        "--max-time", "30",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"curl failed (rc={proc.returncode}): {stderr.decode(errors='replace')[:200]}"
        )
    return json.loads(stdout.decode())


def _parse_rs_ts(firmware_main: str) -> Optional[tuple]:
    """
    Parse 'RS28.0 TS199' → (28, 199).
    Returns None if the format does not match.
    """
    try:
        parts = firmware_main.strip().split()
        rs_str = next(p for p in parts if p.startswith("RS"))
        ts_str = next(p for p in parts if p.startswith("TS"))
        rs = int(rs_str[2:].split(".")[0])
        ts = int(ts_str[2:])
        return (rs, ts)
    except (StopIteration, ValueError, IndexError):
        return None


def _find_series(rs_ts_str: str, packages: list) -> Optional[int]:
    """
    Find the product series for a given rsTsVersion string (e.g. "28.199").

    If the version appears in more than one series (rare, only affects very old
    firmware), prefers the series where it is NOT the active release.
    Falls back to the lowest series number if still ambiguous.
    """
    matches = []  # list of (series, is_active)
    for pkg in packages:
        fp = pkg.get("firmwarePackage", {})
        series = fp.get("series")
        is_active = pkg.get("isActive", False)
        for node in fp.get("nodeFirmwares", []):
            if node.get("rsTsVersion") == rs_ts_str:
                matches.append((series, is_active))
                break

    if not matches:
        return None

    unique_series = {s for s, _ in matches}
    if len(unique_series) == 1:
        return unique_series.pop()

    # Prefer series where the version is historical (not active)
    inactive_series = {s for s, active in matches if not active}
    if len(inactive_series) == 1:
        return inactive_series.pop()

    logger.warning(
        "Firmware version %s matches multiple series %s — picking lowest",
        rs_ts_str, sorted(unique_series),
    )
    return min(unique_series)


def _get_latest_for_series(series: int, packages: list) -> Optional[tuple]:
    """
    Return the highest (RS, TS) among all isActive=True packages for a series.
    Only considers the main node (nodeId 0x01) to match how the device reports
    its version via GetFirmwareVersionList (component ID 1).
    """
    best: Optional[tuple] = None
    for pkg in packages:
        if not pkg.get("isActive"):
            continue
        fp = pkg.get("firmwarePackage", {})
        if fp.get("series") != series:
            continue
        for node in fp.get("nodeFirmwares", []):
            # main node only
            if node.get("nodeId") not in ("0x01", 1):
                continue
            rv = node.get("rsTsVersion", "")
            if "." not in rv:
                continue
            try:
                rs, ts = int(rv.split(".")[0]), int(rv.split(".")[1])
                if best is None or (rs, ts) > best:
                    best = (rs, ts)
            except ValueError:
                continue
    return best


async def check_firmware_update(firmware_main: str) -> dict:
    """
    Check whether a firmware update is available for the device.

    Args:
        firmware_main: BLE firmware string from GetFirmwareVersionList,
                       e.g. "RS28.0 TS199"

    Returns a dict:
        {
            "update_available": bool,
            "device_version":   "RS28.0 TS199",
            "cloud_version":    "RS30.0 TS206" | None,
            "series":           248 | None,
            "error":            None | str,
        }
    """
    result: dict = {
        "update_available": False,
        "device_version": firmware_main,
        "cloud_version": None,
        "series": None,
        "error": None,
    }

    device_tuple = _parse_rs_ts(firmware_main)
    if not device_tuple:
        result["error"] = f"Cannot parse firmware version: {firmware_main!r}"
        return result

    try:
        packages = await fetch_all_firmware_packages()
    except Exception as exc:
        result["error"] = str(exc)
        logger.warning("Firmware update check failed: %s", exc)
        return result

    rs_ts_str = f"{device_tuple[0]}.{device_tuple[1]}"
    series = _find_series(rs_ts_str, packages)
    if series is None:
        result["error"] = f"Device firmware {rs_ts_str} not found in Geberit cloud"
        logger.warning("Firmware update check: %s", result["error"])
        return result

    result["series"] = series

    latest = _get_latest_for_series(series, packages)
    if latest is None:
        result["error"] = f"No active firmware found for series {series}"
        logger.warning("Firmware update check: %s", result["error"])
        return result

    cloud_rs, cloud_ts = latest
    result["cloud_version"] = f"RS{cloud_rs}.0 TS{cloud_ts}"
    result["update_available"] = latest > device_tuple

    logger.info(
        "Firmware check: device=%s cloud=%s series=%s update_available=%s",
        firmware_main, result["cloud_version"], series, result["update_available"],
    )
    return result
