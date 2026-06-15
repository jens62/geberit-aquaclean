"""Shared BLE and mDNS discovery helpers for Geberit AquaClean setup.

Public API:
    async_discover_esphome(timeout)     — find ESPHome proxies via mDNS
    async_scan_ble_via_esphome(...)     — scan BLE devices via ESPHome proxy
    async_scan_ble_local(timeout)       — scan BLE devices via local adapter

Internal helpers (importable by connection-test.py):
    mac_int_to_str, mac_str_to_int
    parse_local_name, parse_service_uuids_128, parse_service_uuid_16
    is_geberit_device
    _discover_esphome_mdns, _discover_esphome_mdns_macos
"""
from __future__ import annotations

import asyncio
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GEBERIT_BLE_NAME_PREFIX = "HB"
GEBERIT_SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
GEBERIT_SERVICE_UUID_BYTES = bytes.fromhex("00003ea0b35c2da0414cf3909d423433")
GEBERIT_SERVICE_UUID_16 = bytes([0xA0, 0x3E])
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
    """Return True if the Geberit 16-bit service UUID (0x3EA0) is in raw BLE advertisement."""
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type in (0x02, 0x03):
            payload = data[i + 2 : i + 1 + length]
            for start in range(0, len(payload) - 1, 2):
                if payload[start : start + 2] == GEBERIT_SERVICE_UUID_16:
                    return True
        i += 1 + length
    return False


def is_geberit_device(
    name: str,
    adv_data: bytes = b"",
    service_uuids: Optional[list] = None,
) -> bool:
    """Return True if name or UUID identifies a Geberit AquaClean device."""
    by_name = name.startswith(GEBERIT_BLE_NAME_PREFIX) or "geberit" in name.lower()
    if service_uuids is not None:
        uuids_lower = [str(u).lower() for u in service_uuids]
        by_uuid = (
            GEBERIT_SERVICE_UUID in uuids_lower
            or any("3ea0" in u for u in uuids_lower)
        )
    else:
        by_uuid = parse_service_uuid_16(adv_data) or parse_service_uuids_128(adv_data)
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
    """Return ESPHome devices found via mDNS (_esphomelib._tcp.local.).

    On macOS uses dns-sd subprocess (avoids mDNSResponder port-5353 conflict).
    On Linux/Windows uses AsyncZeroconf + AsyncServiceBrowser so discovery
    runs entirely on the asyncio event loop — no background threads, no race
    between asyncio.sleep() and zc.close().
    """
    if sys.platform == "darwin":
        return await _discover_esphome_mdns_macos(timeout)

    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf
    except ImportError:
        return []

    import socket as _socket
    found: list[dict] = []

    async def _on_service_state_change(zeroconf, service_type, name, state_change):
        if state_change is not ServiceStateChange.Added:
            return
        try:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            if info.addresses:
                found.append({
                    "name": name.replace(f".{service_type}", "").rstrip("."),
                    "ip": _socket.inet_ntoa(info.addresses[0]),
                    "port": info.port,
                    "host": (info.server or "").rstrip("."),
                })
        except Exception:
            pass

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(
            azc.zeroconf, "_esphomelib._tcp.local.", handlers=[_on_service_state_change]
        )
        await asyncio.sleep(timeout)
        await browser.async_cancel()
    finally:
        await azc.async_close()
    return found


async def _discover_esphome_mdns_ha(hass, timeout: float) -> list[dict]:
    """HA-context discovery: use HA's shared Zeroconf instance.

    Avoids creating a competing mDNS stack alongside HA's own.
    Falls back to _discover_esphome_mdns() if HA zeroconf is unavailable.
    """
    try:
        from homeassistant.components.zeroconf import async_get_instance
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo
    except ImportError:
        return await _discover_esphome_mdns(timeout)

    import socket as _socket
    found: list[dict] = []

    async def _on_service_state_change(zeroconf, service_type, name, state_change):
        if state_change is not ServiceStateChange.Added:
            return
        try:
            info = AsyncServiceInfo(service_type, name)
            await info.async_request(zeroconf, 3000)
            if info.addresses:
                found.append({
                    "name": name.replace(f".{service_type}", "").rstrip("."),
                    "ip": _socket.inet_ntoa(info.addresses[0]),
                    "port": info.port,
                    "host": (info.server or "").rstrip("."),
                })
        except Exception:
            pass

    try:
        zc = await async_get_instance(hass)
    except Exception:
        return await _discover_esphome_mdns(timeout)

    browser = AsyncServiceBrowser(
        zc, "_esphomelib._tcp.local.", handlers=[_on_service_state_change]
    )
    try:
        await asyncio.sleep(timeout)
    finally:
        await browser.async_cancel()
        # Do NOT close zc — it is HA's shared instance.
    return found


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def async_discover_esphome(timeout: float = 8.0, hass=None) -> list[dict]:
    """Discover ESPHome proxies on the local network via mDNS.

    Pass hass when calling from within Home Assistant so HA's shared
    Zeroconf instance is used instead of a competing standalone one.

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
    try:
        from aioesphomeapi import APIClient
    except ImportError:
        return []

    client = APIClient(address=host, port=port, password="", noise_psk=noise_psk)
    try:
        await asyncio.wait_for(client.connect(login=True), timeout=10.0)
    except Exception:
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

    return [
        d for d in seen.values()
        if is_geberit_device(d["adv_name"], adv_data=d["adv_bytes"])
    ]


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
        seen[device.address.upper()] = {
            "mac": device.address.upper(),
            "rssi": rssi,
            "adv_name": name,
            "service_uuids": list(advertisement_data.service_uuids or []),
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
