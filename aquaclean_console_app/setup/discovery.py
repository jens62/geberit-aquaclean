"""Shared BLE and mDNS discovery helpers for Geberit AquaClean setup.

Public API:
    async_discover_esphome(timeout, hass)  — find ESPHome proxies via mDNS
    async_scan_ble_via_esphome(...)        — scan BLE devices via ESPHome proxy
    async_scan_ble_local(timeout)          — scan BLE devices via local adapter

Internal helpers (importable by connection-test.py):
    mac_int_to_str, mac_str_to_int
    parse_local_name, parse_service_uuids_128, parse_service_uuid_16, parse_geberit_adv_info
    is_geberit_device
    _discover_esphome_mdns, _discover_esphome_mdns_macos, _discover_esphome_mdns_ha
"""
from __future__ import annotations

import asyncio
import logging
import sys
from typing import Optional

_LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEBERIT_BLE_NAME_PREFIX = "HB"

# Mera Comfort / AcSela — 128-bit and 16-bit (0x3EA0)
GEBERIT_SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
GEBERIT_SERVICE_UUID_BYTES = bytes.fromhex("00003ea0b35c2da0414cf3909d423433")
GEBERIT_SERVICE_UUID_16 = bytes([0xA0, 0x3E])  # little-endian of 0x3EA0

# Alba / Ble20 — 128-bit and 16-bit (0xFD48)
ALBA_SERVICE_UUID = "0000fd48-0000-1000-8000-00805f9b34fb"
ALBA_SERVICE_UUID_16 = bytes([0x48, 0xFD])     # little-endian of 0xFD48

# Manufacturer data fingerprint — company ID 0x0602 = Geberit International AG (Alba/Ble20)
GEBERIT_COMPANY_ID = bytes([0x02, 0x06])        # little-endian of 0x0602
# AquacleanOld devices (Mera Comfort, Sela, Tuma, Cama) use company ID 0x0100
GEBERIT_COMPANY_ID_OLD = bytes([0x00, 0x01])    # little-endian of 0x0100

ESPHOME_DEFAULT_PORT = 6053

# ---------------------------------------------------------------------------
# MAC helpers
# ---------------------------------------------------------------------------
def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))


def mac_str_to_int(mac: str) -> int:
    parts = mac.replace("-", ":").split(":")
    result = 0
    for p in parts:
        result = (result << 8) | int(p, 16)
    return result


# ---------------------------------------------------------------------------
# BLE advertisement parsing helpers
# ---------------------------------------------------------------------------
def parse_local_name(data: bytes) -> str:
    """Extract device name from raw BLE advertisement AD structures."""
    i = 0
    name = ""
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        value = data[i + 2 : i + 1 + length]
        if ad_type == 0x09:
            return value.decode("utf-8", errors="replace")
        elif ad_type == 0x08:
            name = value.decode("utf-8", errors="replace")
        i += 1 + length
    return name


def parse_service_uuids_128(data: bytes) -> bool:
    """Return True if the Geberit 128-bit service UUID is in raw BLE advertisement AD structures."""
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type in (0x06, 0x07):
            payload = data[i + 2 : i + 1 + length]
            for start in range(0, len(payload) - 15, 16):
                if payload[start : start + 16] == GEBERIT_SERVICE_UUID_BYTES:
                    return True
        i += 1 + length
    return False


def parse_service_uuid_16(data: bytes) -> bool:
    """Return True if a Geberit 16-bit service UUID (Mera=0x3EA0 or Alba=0xFD48) is in raw BLE advertisement."""
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type in (0x02, 0x03):
            payload = data[i + 2 : i + 1 + length]
            for start in range(0, len(payload) - 1, 2):
                uuid_bytes = payload[start : start + 2]
                if uuid_bytes in (GEBERIT_SERVICE_UUID_16, ALBA_SERVICE_UUID_16):
                    return True
        i += 1 + length
    return False


def parse_manufacturer_data(data: bytes) -> bool:
    """Return True if Geberit manufacturer data (company ID 0x0602) is in raw BLE advertisement."""
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type == 0xFF and length >= 3:  # 0xFF = Manufacturer Specific Data
            if data[i + 2 : i + 4] == GEBERIT_COMPANY_ID:
                return True
        i += 1 + length
    return False


# Article number prefix → model name, from AcDeviceTypeHelper.GetDeviceType().
# Prefix is bytes 3–7 of the manufacturer payload formatted as "XXX.YY".
# Matching: exact first, then startswith fallback for 3-digit suffixes (e.g. "146.09" ⊂ "146.096").
_ARTICLE_PREFIX_MODEL: dict[str, str] = {
    "146.22": "Sela", "243.64": "Sela", "243.71": "Sela",
    "146.21": "Mera Comfort",
    "146.20": "Mera Classic",
    "146.19": "Mera Floorstanding", "146.24": "Mera Floorstanding",
    "146.07": "Tuma Classic", "146.09": "Tuma Classic",
    "243.36": "Tuma Classic", "243.46": "Tuma Classic", "243.47": "Tuma Classic",
    "146.27": "Tuma Comfort", "146.29": "Tuma Comfort", "146.98": "Tuma Comfort",
    "243.29": "Tuma Comfort", "243.48": "Tuma Comfort", "243.49": "Tuma Comfort",
    "243.67": "Tuma Comfort",
    "146.30": "Cama Testset",
    "146.34": "Cama",
}


def _lookup_model_from_prefix(prefix: str) -> str:
    if prefix in _ARTICLE_PREFIX_MODEL:
        return _ARTICLE_PREFIX_MODEL[prefix]
    for key, name in _ARTICLE_PREFIX_MODEL.items():
        if prefix.startswith(key) or key.startswith(prefix):
            return name
    return ""


def _extract_article_and_model(mfr_payload: bytes, is_alba: bool) -> tuple:
    """Return (article_number, device_type) from manufacturer payload bytes.

    Alba (company 0x0602): article at payload bytes 3–7, min 8 bytes.
    AquacleanOld (company 0x0100): article at payload bytes 1–5, min 6 bytes.
    """
    if is_alba:
        if len(mfr_payload) < 8:
            return None, "Alba"
        printable = [chr(c) for c in mfr_payload[3:8] if 0x20 <= c <= 0x7E]
        if len(printable) < 5:
            return None, "Alba"
        return "".join(printable).strip() or None, "Alba"
    else:
        if len(mfr_payload) < 6:
            return None, ""
        printable = [chr(c) for c in mfr_payload[1:6] if 0x20 <= c <= 0x7E]
        if len(printable) < 5:
            return None, ""
        prefix = "".join(printable[:3]) + "." + "".join(printable[3:5])
        return prefix, _lookup_model_from_prefix(prefix) or "AquaClean"


def parse_geberit_adv_info(data: bytes) -> dict:
    """Return article number and device type from a raw Geberit BLE advertisement.

    Two-pass over AD structures: collect service UUIDs + manufacturer payload,
    then resolve model name via article prefix lookup.  Safe on malformed or empty input.

    Returns dict with keys:
      article_number (str | None) — e.g. "AC250" (Alba) or "146.22" (Sela)
      device_type    (str)        — "Alba", "Sela", "Mera Comfort", "AquaClean", etc.
    """
    mfr_payload = b""
    is_alba = False
    is_aquaclean_old = False
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type == 0xFF and length >= 3:
            if data[i + 2 : i + 4] in (GEBERIT_COMPANY_ID, GEBERIT_COMPANY_ID_OLD):
                mfr_payload = data[i + 4 : i + 1 + length]
        elif ad_type in (0x02, 0x03):
            payload = data[i + 2 : i + 1 + length]
            for start in range(0, len(payload) - 1, 2):
                uuid_bytes = payload[start : start + 2]
                if uuid_bytes == ALBA_SERVICE_UUID_16:
                    is_alba = True
                elif uuid_bytes == GEBERIT_SERVICE_UUID_16:
                    is_aquaclean_old = True
        i += 1 + length
    if not (is_alba or is_aquaclean_old):
        return {"article_number": None, "device_type": ""}
    article_number, device_type = _extract_article_and_model(mfr_payload, is_alba)
    if is_alba and not device_type:
        device_type = "Alba"
    elif is_aquaclean_old and not device_type:
        device_type = "AquaClean"
    return {"article_number": article_number, "device_type": device_type}


def parse_geberit_adv_info_bleak(manufacturer_data: dict, service_uuids: list) -> dict:
    """Same output as parse_geberit_adv_info but from already-parsed bleak AdvertisementData fields."""
    uuids = service_uuids or []
    is_alba = ALBA_SERVICE_UUID in uuids
    is_aquaclean_old = GEBERIT_SERVICE_UUID in uuids
    if not (is_alba or is_aquaclean_old):
        return {"article_number": None, "device_type": ""}
    mfr_data = manufacturer_data or {}
    mfr_payload = mfr_data.get(0x0602) or mfr_data.get(0x0100) or b""
    article_number, device_type = _extract_article_and_model(mfr_payload, is_alba)
    if is_alba and not device_type:
        device_type = "Alba"
    elif is_aquaclean_old and not device_type:
        device_type = "AquaClean"
    return {"article_number": article_number, "device_type": device_type}


def is_geberit_device(
    name: str,
    adv_data: bytes = b"",
    service_uuids: Optional[list] = None,
) -> bool:
    """Return True if name, UUID, or manufacturer data identifies a Geberit AquaClean device."""
    by_name = name.startswith(GEBERIT_BLE_NAME_PREFIX) or "geberit" in name.lower()
    if service_uuids is not None:
        uuids_lower = [str(u).lower() for u in service_uuids]
        by_uuid = (
            GEBERIT_SERVICE_UUID in uuids_lower     # Mera Comfort 128-bit
            or ALBA_SERVICE_UUID in uuids_lower      # Alba / Ble20 128-bit
            or any("3ea0" in u for u in uuids_lower) # Mera Comfort 16-bit
            or any("fd48" in u for u in uuids_lower) # Alba 16-bit
        )
    else:
        by_uuid = (
            parse_service_uuid_16(adv_data)      # 0x3EA0 (Mera) or 0xFD48 (Alba)
            or parse_service_uuids_128(adv_data) # Mera Comfort 128-bit UUID
            or parse_manufacturer_data(adv_data) # Geberit company ID 0x0602
        )
    return by_name or by_uuid


# ---------------------------------------------------------------------------
# mDNS / ESPHome discovery — internal helpers
# ---------------------------------------------------------------------------
async def _discover_esphome_mdns_macos(timeout: float) -> list[dict]:
    """macOS: use dns-sd subprocess — avoids mDNSResponder port-5353 conflict."""
    import re
    import socket as _socket

    browse = await asyncio.create_subprocess_exec(
        "dns-sd", "-B", "_esphomelib._tcp", "local",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    instances: list[str] = []
    try:
        async def _read_browse():
            assert browse.stdout
            while True:
                line = (await browse.stdout.readline()).decode(errors="replace")
                if not line:
                    break
                if "Add" in line and "_esphomelib._tcp" in line:
                    parts = line.split()
                    if len(parts) >= 7:
                        name = " ".join(parts[6:]).strip()
                        if name and name not in instances:
                            instances.append(name)
        await asyncio.wait_for(_read_browse(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            browse.kill()
        except Exception:
            pass
        await browse.wait()

    found: list[dict] = []
    seen_names: set[str] = set()
    for instance in instances:
        if instance in seen_names:
            continue
        seen_names.add(instance)
        try:
            lookup = await asyncio.create_subprocess_exec(
                "dns-sd", "-L", instance, "_esphomelib._tcp", "local",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            )
            output = b""
            try:
                async def _read_lookup():
                    assert lookup.stdout
                    nonlocal output
                    output = await lookup.stdout.read(2048)
                await asyncio.wait_for(_read_lookup(), timeout=3.0)
            except asyncio.TimeoutError:
                pass
            finally:
                try:
                    lookup.kill()
                except Exception:
                    pass
                await lookup.wait()

            text = output.decode(errors="replace")
            m = re.search(r"can be reached at (\S+?):(\d+)", text)
            if m:
                hostname = m.group(1).rstrip(".")
                port = int(m.group(2))
                try:
                    loop = asyncio.get_event_loop()
                    results = await loop.run_in_executor(
                        None, lambda: _socket.getaddrinfo(hostname, port, _socket.AF_INET)
                    )
                    ip = results[0][4][0]
                    found.append({"name": instance, "ip": ip, "port": port, "host": hostname})
                except Exception:
                    pass
        except Exception:
            pass
    return found


async def _discover_esphome_mdns(timeout: float = 8.0) -> list[dict]:
    """Return ESPHome devices found via mDNS — standalone (no HA) path.

    On macOS uses dns-sd subprocess (avoids mDNSResponder port-5353 conflict).
    On Linux/Windows uses AsyncZeroconf + AsyncServiceBrowser.

    Uses a sync handler + loop.create_task() because AsyncServiceBrowser fires
    handlers synchronously for cached entries during registration — an async def
    handler would produce a coroutine that is never awaited.
    """
    if sys.platform == "darwin":
        return await _discover_esphome_mdns_macos(timeout)

    _LOGGER.debug("ESPHome mDNS discovery (standalone, timeout=%.1fs)", timeout)

    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except ImportError:
        _LOGGER.debug("zeroconf not available — skipping mDNS discovery")
        return []

    import socket as _socket
    found: list[dict] = []
    loop = asyncio.get_running_loop()
    pending_tasks: list[asyncio.Task] = []

    async def _lookup(zeroconf, service_type, name):
        _LOGGER.debug("mDNS lookup start: %s", name)
        try:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            if info.addresses:
                entry = {
                    "name": name.replace(f".{service_type}", "").rstrip("."),
                    "ip": _socket.inet_ntoa(info.addresses[0]),
                    "port": info.port,
                    "host": (info.server or "").rstrip("."),
                }
                _LOGGER.debug("mDNS found: %s @ %s:%s", entry["name"], entry["ip"], entry["port"])
                found.append(entry)
            else:
                _LOGGER.debug("mDNS lookup: no addresses for %s", name)
        except Exception as exc:
            _LOGGER.debug("mDNS lookup error for %s: %s", name, exc)

    def _on_service_state_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            _LOGGER.debug("mDNS service Added: %s", name)
            pending_tasks.append(loop.create_task(_lookup(zeroconf, service_type, name)))
        elif state_change is ServiceStateChange.Removed:
            _LOGGER.debug("mDNS service Removed: %s", name)

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(
            azc.zeroconf, "_esphomelib._tcp.local.", handlers=[_on_service_state_change]
        )
        await asyncio.sleep(timeout)
        if pending_tasks:
            _LOGGER.debug("Awaiting %d pending lookup(s)", len(pending_tasks))
            await asyncio.gather(*pending_tasks, return_exceptions=True)
        await browser.async_cancel()
    finally:
        await azc.async_close()
    _LOGGER.debug("mDNS discovery done: %d proxies found", len(found))
    return found


async def _discover_esphome_mdns_ha(hass, timeout: float) -> list[dict]:
    """Return ESPHome devices found via mDNS — HA context path.

    Uses HA's shared Zeroconf instance (via async_get_instance) so HA does not
    complain about a competing Zeroconf stack.  HA's HaZeroconf calls handlers
    synchronously, so we use a sync handler and schedule async lookups with
    loop.create_task() to avoid 'coroutine was never awaited' warnings.
    """
    _LOGGER.debug("ESPHome mDNS discovery (HA, timeout=%.1fs)", timeout)

    try:
        from homeassistant.components.zeroconf import async_get_instance
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo
    except ImportError:
        _LOGGER.debug("homeassistant.components.zeroconf not available")
        return []

    import socket as _socket
    found: list[dict] = []
    loop = asyncio.get_running_loop()
    pending_tasks: list[asyncio.Task] = []

    async def _lookup(zeroconf, service_type, name):
        _LOGGER.debug("mDNS lookup start: %s", name)
        try:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            if info.addresses:
                entry = {
                    "name": name.replace(f".{service_type}", "").rstrip("."),
                    "ip": _socket.inet_ntoa(info.addresses[0]),
                    "port": info.port,
                    "host": (info.server or "").rstrip("."),
                }
                _LOGGER.debug("mDNS found: %s @ %s:%s", entry["name"], entry["ip"], entry["port"])
                found.append(entry)
            else:
                _LOGGER.debug("mDNS lookup: no addresses for %s", name)
        except Exception as exc:
            _LOGGER.debug("mDNS lookup error for %s: %s", name, exc)

    def _on_service_state_change(zeroconf, service_type, name, state_change):
        if state_change is ServiceStateChange.Added:
            _LOGGER.debug("mDNS service Added: %s", name)
            pending_tasks.append(loop.create_task(_lookup(zeroconf, service_type, name)))
        elif state_change is ServiceStateChange.Removed:
            _LOGGER.debug("mDNS service Removed: %s", name)

    zc = await async_get_instance(hass)
    _LOGGER.debug("Got HA shared Zeroconf: %s", type(zc).__name__)
    browser = AsyncServiceBrowser(zc, "_esphomelib._tcp.local.", handlers=[_on_service_state_change])
    _LOGGER.debug("AsyncServiceBrowser created, waiting %.1fs", timeout)
    try:
        await asyncio.sleep(timeout)
        if pending_tasks:
            _LOGGER.debug("Awaiting %d pending lookup(s)", len(pending_tasks))
            await asyncio.gather(*pending_tasks, return_exceptions=True)
    finally:
        await browser.async_cancel()
        # Do NOT close zc — it is HA's shared instance.
    _LOGGER.debug("mDNS discovery done: %d proxies found", len(found))
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def async_discover_esphome(timeout: float = 8.0, hass=None) -> list[dict]:
    """Discover ESPHome proxies on the local network via mDNS.

    When hass is provided (HA context), uses HA's shared Zeroconf instance to
    avoid conflicts with HA's own mDNS stack.

    Returns list of dicts with keys: name, host, port, ip.
    """
    if hass is not None:
        return await _discover_esphome_mdns_ha(hass, timeout)
    return await _discover_esphome_mdns(timeout)


async def async_scan_ble_via_esphome(
    host: str,
    port: int,
    noise_psk: Optional[str] = None,
    timeout: float = 10.0,
) -> list[dict]:
    """Scan for BLE devices via an ESPHome Bluetooth proxy.

    Returns list of dicts with keys: mac, rssi, adv_name, adv_bytes.
    Only Geberit devices are returned.
    """
    _LOGGER.debug("BLE scan via ESPHome %s:%s (timeout=%.1fs)", host, port, timeout)
    try:
        from aioesphomeapi import APIClient
    except ImportError:
        _LOGGER.debug("aioesphomeapi not available")
        return []

    client = APIClient(address=host, port=port, password="", noise_psk=noise_psk)
    try:
        await asyncio.wait_for(client.connect(login=True), timeout=10.0)
    except Exception as exc:
        _LOGGER.debug("ESPHome connect failed: %s", exc)
        return []

    seen: dict[str, dict] = {}

    def _on_adv(resp) -> None:
        for adv in resp.advertisements:
            mac_str = mac_int_to_str(adv.address)
            adv_bytes = bytes(adv.data)
            name = parse_local_name(adv_bytes)
            existing = seen.get(mac_str, {})
            kept_bytes = (
                adv_bytes if len(adv_bytes) >= len(existing.get("adv_bytes", b"")) else existing["adv_bytes"]
            )
            seen[mac_str] = {
                "mac": mac_str,
                "rssi": adv.rssi,
                "adv_name": name or existing.get("adv_name", ""),
                "adv_bytes": kept_bytes,
            }

    unsub = None
    try:
        unsub = client.subscribe_bluetooth_le_raw_advertisements(_on_adv)
        await asyncio.sleep(timeout)
    finally:
        if unsub:
            try:
                unsub()
            except Exception:
                pass
        try:
            await client.disconnect()
        except Exception:
            pass

    _LOGGER.debug("BLE scan done: %d unique devices seen", len(seen))
    geberit = []
    for d in seen.values():
        matched = is_geberit_device(d["adv_name"], adv_data=d["adv_bytes"])
        _LOGGER.debug(
            "  %s rssi=%+d name=%r adv_len=%d geberit=%s",
            d["mac"], d["rssi"], d["adv_name"], len(d["adv_bytes"]), matched,
        )
        if matched:
            geberit.append(d)
    _LOGGER.debug("BLE scan: %d Geberit device(s) identified", len(geberit))
    return geberit


async def async_scan_ble_local(timeout: float = 10.0) -> list[dict]:
    """Scan for BLE devices using the local Bluetooth adapter (bleak).

    Returns list of dicts with keys: mac, rssi, adv_name, service_uuids.
    Only Geberit devices are returned.
    """
    try:
        from bleak import BleakScanner
    except ImportError:
        return []

    seen: dict[str, dict] = {}

    def _on_detection(device, advertisement_data) -> None:
        name = advertisement_data.local_name or device.name or ""
        rssi = getattr(advertisement_data, "rssi", None) or getattr(device, "rssi", 0) or 0
        uuids = list(advertisement_data.service_uuids or [])
        adv_info = parse_geberit_adv_info_bleak(
            dict(advertisement_data.manufacturer_data or {}),
            uuids,
        )
        seen[device.address.upper()] = {
            "mac": device.address.upper(),
            "rssi": rssi,
            "adv_name": name,
            "service_uuids": uuids,
            "article_number": adv_info["article_number"],
            "device_type": adv_info["device_type"],
        }

    scanner = BleakScanner(detection_callback=_on_detection)
    try:
        await scanner.start()
        await asyncio.sleep(timeout)
    finally:
        try:
            await scanner.stop()
        except Exception:
            pass

    return [
        d for d in seen.values()
        if is_geberit_device(d["adv_name"], service_uuids=d["service_uuids"])
    ]
