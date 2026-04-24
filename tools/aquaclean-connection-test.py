#!/usr/bin/env python3
"""
aquaclean-connection-test.py — Geberit AquaClean Connection Test
=================================================================

Diagnoses the connection between the bridge and a Geberit AquaClean toilet.
Supports three modes:

  Auto-discovery (default, no --host):
    Scans the network for ESPHome devices via mDNS and auto-selects the
    aquaclean-proxy. Then runs the full ESPHome + BLE check sequence.

  Explicit ESPHome host (--host):
    Skips mDNS and connects directly to the given host/IP.

  Local BLE (--local-ble):
    Skips ESPHome entirely. Scans for Geberit BLE devices using the local
    Bluetooth adapter (requires bleak: pip install bleak).

Usage
-----
  python tools/aquaclean-connection-test.py
  python tools/aquaclean-connection-test.py --mac AA:BB:CC:DD:EE:FF
  python tools/aquaclean-connection-test.py --host 192.168.0.160
  python tools/aquaclean-connection-test.py --host 192.168.0.160 --mac AA:BB:CC:DD:EE:FF
  python tools/aquaclean-connection-test.py --host aquaclean-proxy.local --noise-psk "base64=="
  python tools/aquaclean-connection-test.py --local-ble
  python tools/aquaclean-connection-test.py --local-ble --mac AA:BB:CC:DD:EE:FF
  python tools/aquaclean-connection-test.py --mac AA:BB:CC:DD:EE:FF --stream-logs
  python tools/aquaclean-connection-test.py --stream-logs --stream-duration 60 --log-level verbose
  python tools/aquaclean-connection-test.py --mac AA:BB:CC:DD:EE:FF --dynamic-uuids

Install
-------
  pip install aioesphomeapi          # required for ESPHome mode (default)
  pip install aioesphomeapi bleak    # required for --local-ble mode

See docs/connection-test.md for full installation and usage guide.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import struct
import sys
import time
from typing import Optional

# ---------------------------------------------------------------------------
# Dependency check — give a friendly message before import fails
# ---------------------------------------------------------------------------
try:
    from aioesphomeapi import APIClient
    from aioesphomeapi.core import (
        APIConnectionError,
        TimeoutAPIError,
    )
    # InvalidStateError was removed in newer aioesphomeapi versions
    try:
        from aioesphomeapi.core import InvalidStateError
    except ImportError:
        InvalidStateError = APIConnectionError  # type: ignore[misc,assignment]
    import aioesphomeapi
    import importlib.metadata as _imeta
    try:
        _AIOESPHOMEAPI_VERSION = _imeta.version("aioesphomeapi")
    except Exception:
        _AIOESPHOMEAPI_VERSION = "unknown"
except ImportError:
    print(
        "\nERROR: aioesphomeapi is not installed.\n"
        "\nInstall it with:\n"
        "  pip install aioesphomeapi\n"
        "\nFor Windows users, see docs/connection-test.md for detailed instructions.\n"
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
SCRIPT_VERSION = "2026-04-21 10:55"    # update this date+time whenever the script is changed

GEBERIT_BLE_NAME_PREFIX = "HB"   # Mera Comfort advertises as HB2304EU…; other models use "Geberit …"
GEBERIT_SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
# Same UUID in little-endian bytes (as it appears in raw BLE advertisement AD structures)
GEBERIT_SERVICE_UUID_BYTES = bytes.fromhex("00003ea0b35c2da0414cf3909d423433")
# 16-bit service UUID advertised by Geberit devices (e.g. AC PRO): 0x3EA0, little-endian
GEBERIT_SERVICE_UUID_16 = bytes([0xA0, 0x3E])
ESPHOME_API_PORT = 6053
TCP_TIMEOUT = 5.0
API_CONNECT_TIMEOUT = 10.0
SUBSCRIPTION_WARMUP = 3.0        # seconds to wait for first advertisement packet

# ---------------------------------------------------------------------------
# Terminal colours (disabled on Windows or when not a TTY)
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty() and sys.platform != "win32"

def _green(s: str) -> str:   return f"\033[32m{s}\033[0m" if _USE_COLOR else s
def _yellow(s: str) -> str:  return f"\033[33m{s}\033[0m" if _USE_COLOR else s
def _red(s: str) -> str:     return f"\033[31m{s}\033[0m" if _USE_COLOR else s
def _bold(s: str) -> str:    return f"\033[1m{s}\033[0m"  if _USE_COLOR else s
def _cyan(s: str) -> str:    return f"\033[36m{s}\033[0m" if _USE_COLOR else s

# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------
class _Result:
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    SKIP = "SKIP"

_results: list[tuple[str, str, str]] = []   # (step, status, detail)

def _report(step: str, status: str, detail: str = "", hint: str = "") -> None:
    _results.append((step, status, detail))
    color = {"PASS": _green, "WARN": _yellow, "FAIL": _red, "SKIP": _cyan}.get(status, str)
    tag = color(f"[{status}]")
    print(f"  {tag}  {step}")
    if detail:
        print(f"         {detail}")
    if hint:
        for line in hint.strip().splitlines():
            print(f"         {_yellow('→')} {line}")

def _section(title: str) -> None:
    print(f"\n{_bold(title)}")
    print("  " + "─" * (len(title) + 2))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))

def mac_str_to_int(mac: str) -> int:
    parts = mac.replace("-", ":").split(":")
    result = 0
    for p in parts:
        result = (result << 8) | int(p, 16)
    return result

def parse_local_name(data: bytes) -> str:
    """Extract BLE device name from raw advertisement AD structures."""
    i = 0
    name = ""
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        value = data[i + 2 : i + 1 + length]
        if ad_type == 0x09:      # Complete Local Name
            return value.decode("utf-8", errors="replace")
        elif ad_type == 0x08:   # Shortened Local Name — keep as fallback
            name = value.decode("utf-8", errors="replace")
        i += 1 + length
    return name


_AD_TYPE_NAMES = {
    0x01: "Flags",
    0x02: "16-bit UUIDs (incomplete)",
    0x03: "16-bit UUIDs (complete)",
    0x04: "32-bit UUIDs (incomplete)",
    0x05: "32-bit UUIDs (complete)",
    0x06: "128-bit UUIDs (incomplete)",
    0x07: "128-bit UUIDs (complete)",
    0x08: "Shortened Local Name",
    0x09: "Complete Local Name",
    0x0A: "TX Power Level",
    0xFF: "Manufacturer Specific",
}

def _dump_ad_structures(mac: str, data: bytes) -> None:
    """Print all AD structures from raw advertisement bytes (for --dump-ads)."""
    if not data:
        print(f"    {_yellow('(no advertisement data)')}")
        return
    print(f"    raw ({len(data)} bytes): {data.hex()}")
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        value   = data[i + 2 : i + 1 + length]
        name    = _AD_TYPE_NAMES.get(ad_type, f"type=0x{ad_type:02X}")
        hex_val = value.hex()
        extra   = ""
        if ad_type in (0x02, 0x03) and len(value) >= 2:
            # Show 16-bit UUIDs
            uuids_16 = [f"0x{value[j+1]:02X}{value[j]:02X}" for j in range(0, len(value) - 1, 2)]
            geberit_match = "  ✅ GEBERIT 16-bit UUID (0x3EA0)" if GEBERIT_SERVICE_UUID_16 in [value[j:j+2] for j in range(0, len(value)-1, 2)] else ""
            extra = f"  → {', '.join(uuids_16)}{geberit_match}"
        elif ad_type in (0x06, 0x07) and len(value) >= 16:
            # Show as UUID (little-endian → standard form)
            b = value[:16]
            uuid_str = (
                f"{b[15:11:-1].hex()}-{b[11:9:-1].hex()}-"
                f"{b[9:7:-1].hex()}-{b[7:5:-1].hex()}-{b[5::-1].hex()}"
            )
            geberit_match = "  ✅ GEBERIT 128-bit UUID" if value[:16] == GEBERIT_SERVICE_UUID_BYTES else ""
            extra = f"  → {uuid_str}{geberit_match}"
        elif ad_type in (0x08, 0x09):
            extra = f"  → \"{value.decode('utf-8', errors='replace')}\""
        print(f"    AD 0x{ad_type:02X}  {name:<30}  {hex_val}{extra}")
        i += 1 + length
    print()


def parse_service_uuids_128(data: bytes) -> bool:
    """Return True if the Geberit 128-bit service UUID is found in raw BLE advertisement AD structures.

    Checks AD types 0x06 (incomplete) and 0x07 (complete) 128-bit UUID lists.
    The UUID is stored little-endian in the advertisement payload.
    NOTE: Geberit AC PRO does NOT include the 128-bit UUID in its advertisement —
    use parse_service_uuid_16() for reliable detection across all models.
    """
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type in (0x06, 0x07):   # 128-bit service UUID list
            payload = data[i + 2 : i + 1 + length]
            for start in range(0, len(payload) - 15, 16):
                if payload[start : start + 16] == GEBERIT_SERVICE_UUID_BYTES:
                    return True
        i += 1 + length
    return False


def parse_service_uuid_16(data: bytes) -> bool:
    """Return True if the Geberit 16-bit service UUID (0x3EA0) is found in raw BLE advertisement.

    Checks AD types 0x02 (incomplete) and 0x03 (complete) 16-bit UUID lists.
    Confirmed present in Geberit AC PRO advertisements; expected on all Geberit models.
    """
    i = 0
    while i < len(data):
        length = data[i]
        if length == 0 or i + length >= len(data):
            break
        ad_type = data[i + 1]
        if ad_type in (0x02, 0x03):   # 16-bit service UUID list
            payload = data[i + 2 : i + 1 + length]
            for start in range(0, len(payload) - 1, 2):
                if payload[start : start + 2] == GEBERIT_SERVICE_UUID_16:
                    return True
        i += 1 + length
    return False


def _is_geberit(
    name: str,
    identify_by: str,
    adv_data: bytes = b"",
    service_uuids: Optional[list] = None,
) -> bool:
    """Return True if the device is a Geberit AquaClean based on the chosen strategy.

    identify_by:
      'name' — name prefix 'HB' or 'geberit' in name (fast, model-dependent)
      'uuid' — Geberit service UUID in advertisement only (model-independent, for testing)
      'any'  — name OR uuid (production default; most robust)

    adv_data:     raw advertisement bytes (ESPHome path) — fed to parse_service_uuids_128()
    service_uuids: pre-parsed UUID strings (bleak path) — checked directly
    """
    by_name = name.startswith(GEBERIT_BLE_NAME_PREFIX) or "geberit" in name.lower()
    if identify_by == "name":
        return by_name
    # UUID check: bleak gives us parsed strings; ESPHome gives raw bytes
    if service_uuids is not None:
        uuids_lower = [str(u).lower() for u in service_uuids]
        by_uuid = (
            GEBERIT_SERVICE_UUID in uuids_lower
            or "0000" + "3ea0" + "-0000-1000-8000-00805f9b34fb" in uuids_lower
            or any("3ea0" in u for u in uuids_lower)
        )
    else:
        # 16-bit UUID (0x3EA0) is the reliable identifier — confirmed in Geberit AC PRO adverts
        # 128-bit UUID check kept as fallback for other models that may advertise it
        by_uuid = parse_service_uuid_16(adv_data) or parse_service_uuids_128(adv_data)
    if identify_by == "uuid":
        return by_uuid
    return by_name or by_uuid  # "any"

# ---------------------------------------------------------------------------
# Step 0a — mDNS / ESPHome discovery
# ---------------------------------------------------------------------------
async def _discover_esphome_mdns_macos(timeout: float) -> list[dict]:
    """macOS: use dns-sd subprocess — avoids mDNSResponder port-5353 conflict with Python zeroconf."""
    import re
    import socket as _socket

    # Step 1 — browse: collect instance names
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
        try: browse.kill()
        except Exception: pass
        await browse.wait()

    # Step 2 — lookup: resolve each instance to IP + port
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
                try: lookup.kill()
                except Exception: pass
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
    """Return list of ESPHome devices found via mDNS (_esphomelib._tcp.local.).

    On macOS uses dns-sd subprocess (avoids mDNSResponder port conflict).
    On Linux/Windows uses Python zeroconf library.
    """
    if sys.platform == "darwin":
        return await _discover_esphome_mdns_macos(timeout)

    # Linux / Windows — Python zeroconf
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []

    import socket as _socket
    found: list[dict] = []

    class _Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name)
            if info and info.addresses:
                found.append({
                    "name": name.replace("._esphomelib._tcp.local.", ""),
                    "ip":   _socket.inet_ntoa(info.addresses[0]),
                    "port": info.port,
                    "host": (info.server or "").rstrip("."),
                })
        def update_service(self, *_): pass
        def remove_service(self, *_): pass

    zc = Zeroconf()
    ServiceBrowser(zc, "_esphomelib._tcp.local.", _Listener())
    await asyncio.sleep(timeout)
    zc.close()
    return found


async def check_esphome_discovery(timeout: float = 5.0) -> tuple[Optional[str], Optional[int]]:
    """Step 0: discover ESPHome proxy via mDNS. Returns (host, port) or (None, None)."""
    _section("Step 0 — ESPHome Discovery (mDNS)")
    print(f"  Scanning for ESPHome devices on the network ({timeout:.0f}s) …  (mDNS / _esphomelib._tcp.local.)")

    devices = await _discover_esphome_mdns(timeout)

    if not devices:
        _report(
            "mDNS scan", _Result.FAIL, "No ESPHome devices found",
            hint=(
                "No _esphomelib._tcp.local. service found on the network.\n"
                "• Make sure the ESP32 is powered and connected to WiFi.\n"
                "• Supply the IP directly with --host 192.168.x.x\n"
                "• Or use --local-ble to scan with the local Bluetooth adapter instead."
            ),
        )
        return None, None

    print()
    print(f"  {'Name':<30} {'IP':<16} {'Port':<6}  Hostname")
    print("  " + "─" * 65)
    for d in devices:
        marker = _cyan("  ← aquaclean") if "aquaclean" in d["name"].lower() else ""
        print(f"  {d['name']:<30} {d['ip']:<16} {d['port']:<6}  {d['host']}{marker}")
    print()

    # Prefer a device with "aquaclean" in the name, else take the first
    preferred = next((d for d in devices if "aquaclean" in d["name"].lower()), devices[0])

    if len(devices) == 1:
        _report(
            "mDNS scan", _Result.PASS,
            f"Found 1 ESPHome device: {preferred['name']} at {preferred['ip']}:{preferred['port']}",
        )
    elif "aquaclean" in preferred["name"].lower():
        _report(
            "mDNS scan", _Result.WARN,
            f"Found {len(devices)} ESPHome devices — using '{preferred['name']}' (name contains 'aquaclean')",
            hint="Use --host to target a specific device.",
        )
    else:
        _report(
            "mDNS scan", _Result.WARN,
            f"Found {len(devices)} ESPHome devices — using first: '{preferred['name']}'",
            hint="Use --host to target a specific device.",
        )

    print(f"\n  {_cyan('→')} Using {preferred['name']} at {preferred['ip']}:{preferred['port']}\n")
    return preferred["ip"], preferred["port"]


# ---------------------------------------------------------------------------
# Step 0b — Local BLE scan via bleak (--local-ble mode)
# ---------------------------------------------------------------------------
async def check_local_ble_scan(target_mac: Optional[str], scan_duration: float, identify_by: str = "any") -> "list[str]":
    """Scan for Geberit devices using the local Bluetooth adapter (bleak)."""
    _section("Step 0 — Local BLE Scan (bleak)")

    try:
        from bleak import BleakScanner
    except ImportError:
        _report(
            "bleak import", _Result.FAIL, "bleak is not installed",
            hint=(
                "Install bleak to use local BLE scanning:\n"
                "  pip install bleak\n"
                "Or use --host / omit --local-ble to connect via an ESPHome BLE proxy instead."
            ),
        )
        return

    id_label = {"name": "name only", "uuid": "service UUID only (testing)", "any": "name or UUID"}.get(identify_by, identify_by)
    print(f"  Scanning for BLE devices ({scan_duration:.0f}s) …  identify-by: {id_label}")

    seen: dict[str, tuple[str, int, list, dict]] = {}   # mac -> (name, rssi, service_uuids, mfr_data)

    def _on_detection(device, advertisement_data) -> None:
        name = advertisement_data.local_name or device.name or ""
        rssi = getattr(advertisement_data, "rssi", None) or getattr(device, "rssi", 0) or 0
        uuids = list(advertisement_data.service_uuids or [])
        mfr  = dict(advertisement_data.manufacturer_data or {})
        seen[device.address.upper()] = (name, rssi, uuids, mfr)

    scanner = BleakScanner(detection_callback=_on_detection)
    await scanner.start()
    await asyncio.sleep(scan_duration)
    await scanner.stop()

    if not seen:
        _report(
            "BLE scan", _Result.FAIL, "No BLE devices found at all",
            hint=(
                "• Make sure your local Bluetooth adapter is enabled.\n"
                "• On Linux, check: hciconfig — adapter should show 'UP RUNNING'.\n"
                "• Try moving closer to the toilet."
            ),
        )
        return

    geberit_found: list[str] = []
    print()
    print(f"  {'MAC Address':<20} {'RSSI':>9}  Name")
    print("  " + "─" * 55)
    for mac, (name, rssi, uuids, mfr) in sorted(seen.items(), key=lambda x: -x[1][1]):
        is_target  = target_mac and mac.upper() == target_mac.upper()
        is_geberit = _is_geberit(name, identify_by, service_uuids=uuids)
        marker = ""
        if is_target and is_geberit:
            marker = _green("  ← TARGET") + _cyan(" + Geberit ✅")
            geberit_found.insert(0, mac)
        elif is_target:
            marker = _green("  ← TARGET")
            geberit_found.insert(0, mac)
        elif is_geberit:
            marker = _cyan("  ← Geberit device")
            geberit_found.append(mac)
        print(f"  {mac:<20} {rssi:>+5} dBm  {name}{marker}")

    print(f"\n  {len(seen)} device(s) found in {scan_duration:.0f}s.")

    if geberit_found:
        _report(
            "Geberit device(s) found", _Result.PASS,
            f"Found {len(geberit_found)} Geberit device(s) (identify-by: {identify_by})",
            hint=(
                f"Add to config.ini:\n"
                f"  [BLE]\n"
                f"  device_id = {geberit_found[0]}"
            ),
        )
    else:
        _report(
            "Geberit device detection", _Result.WARN,
            "No Geberit device found — showing service UUIDs to help identify your device",
            hint=(
                "• Close the Geberit Home app on any iPhone/tablet.\n"
                "• Try --scan-duration 30 for a longer scan.\n"
                "• Supply the known MAC with --mac to search specifically."
            ),
        )
        # Auto-dump service UUIDs so the user can identify their device
        print(f"\n  {_yellow('Service UUIDs and manufacturer data for all seen devices:')}")
        for mac, (name, rssi, uuids, mfr) in sorted(seen.items(), key=lambda x: -x[1][1]):
            geberit_16 = any("3ea0" in u.lower() for u in uuids) if uuids else False
            tag = _cyan("  ✅ Geberit UUID") if geberit_16 else ""
            print(f"\n  {mac}  {rssi:+d} dBm  {name or '(no name)'}{tag}")
            if uuids:
                for u in uuids:
                    print(f"    UUID  {u}")
            else:
                print(f"    (no service UUIDs advertised)")
            if mfr:
                for cid, data in mfr.items():
                    print(f"    MFR   company=0x{cid:04X}  data={bytes(data).hex()}")
    return geberit_found


# ---------------------------------------------------------------------------
# Check 1 — TCP port reachability
# ---------------------------------------------------------------------------
async def check_tcp(host: str, port: int) -> bool:
    _section("Step 1 — TCP Reachability")
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=TCP_TIMEOUT
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        _report(f"Port {port} on {host}", _Result.PASS, f"TCP connection succeeded")
        return True
    except asyncio.TimeoutError:
        _report(
            f"Port {port} on {host}", _Result.FAIL,
            f"No response within {TCP_TIMEOUT:.0f}s",
            hint=(
                "The ESP32 is not reachable or port 6053 is blocked.\n"
                "• Check that the ESP32 is powered and connected to WiFi.\n"
                "• Verify the IP address is correct (ping the ESP32 first).\n"
                "• If using a hostname, make sure mDNS resolution works on your network.\n"
                "• Flash the ESP32 with the aquaclean-proxy.yaml firmware if not done yet."
            ),
        )
        return False
    except OSError as e:
        _report(
            f"Port {port} on {host}", _Result.FAIL,
            f"Connection refused: {e}",
            hint=(
                "Port 6053 is not open — the ESPHome API server is not running.\n"
                "• Make sure the ESP32 is flashed with aquaclean-proxy.yaml.\n"
                "• The yaml must include 'api:' section (not commented out).\n"
                "• Try rebooting the ESP32 (power-cycle or press reset button)."
            ),
        )
        return False

# ---------------------------------------------------------------------------
# Check 2 — ESPHome API connect + device info
# ---------------------------------------------------------------------------
async def check_api_connect(
    host: str, port: int, noise_psk: Optional[str]
) -> Optional[APIClient]:
    _section("Step 2 — ESPHome API Connection")
    client = APIClient(address=host, port=port, password="", noise_psk=noise_psk)
    try:
        await asyncio.wait_for(client.connect(login=True), timeout=API_CONNECT_TIMEOUT)
    except TimeoutAPIError:
        _report(
            "API connect", _Result.FAIL, "Timed out after 10s",
            hint=(
                "The ESPHome API handshake timed out.\n"
                "• If api_encryption is enabled in aquaclean-proxy.yaml, supply --noise-psk.\n"
                "• Try rebooting the ESP32.\n"
                "• Check ESPHome logs for 'API server' startup messages."
            ),
        )
        return None
    except APIConnectionError as e:
        msg = str(e)
        if "noise" in msg.lower() or "handshake" in msg.lower() or "encryption" in msg.lower():
            _report(
                "API connect", _Result.FAIL, f"Encryption handshake failed: {e}",
                hint=(
                    "The ESP32 requires an API encryption key but none was supplied (or it is wrong).\n"
                    "• Find the key in your aquaclean-proxy.yaml under api_encryption_key (in secrets.yaml).\n"
                    "• Pass it with: --noise-psk \"<base64key>\""
                ),
            )
        else:
            _report(
                "API connect", _Result.FAIL, f"Connection error: {e}",
                hint="Try rebooting the ESP32 and running the test again.",
            )
        return None
    except Exception as e:
        _report(
            "API connect", _Result.FAIL, f"Unexpected error: {type(e).__name__}: {e}",
            hint="Check that the ESP32 is running ESPHome firmware (not Tasmota or other).",
        )
        return None

    # Get device info — also retrieves bluetooth_proxy_feature_flags needed for BLE connect
    feature_flags = 0
    try:
        info = await client.device_info()
        feature_flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
        _report("API connect", _Result.PASS, f"Connected to '{info.name}' (ESPHome {info.esphome_version})")
        _report("ESP32 MAC", _Result.PASS,
                f"{info.mac_address}  model: {info.model or 'unknown'}  "
                f"bt_feature_flags=0x{feature_flags:02X}")
    except Exception as e:
        _report("API connect", _Result.PASS, "Connected (device_info unavailable)")

    # Store feature_flags on client object so check_ble_connect can access it
    client._test_feature_flags = feature_flags  # type: ignore[attr-defined]
    return client

# ---------------------------------------------------------------------------
# Check 3 — BLE subscription (detects HA ESPHome integration conflict)
# ---------------------------------------------------------------------------
async def check_ble_subscription(client: APIClient) -> bool:
    _section("Step 3 — BLE Advertisement Subscription")

    received_packets: list[int] = []
    first_packet_time: Optional[float] = None
    subscription_error: Optional[str] = None
    subscription_start: float = time.monotonic()

    def on_raw_advertisements(resp) -> None:
        nonlocal first_packet_time
        received_packets.append(len(resp.advertisements))
        if first_packet_time is None:
            first_packet_time = time.monotonic()

    unsub = None
    try:
        unsub = client.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
    except (InvalidStateError, APIConnectionError, Exception) as e:
        msg = str(e)
        if "one api subscription" in msg.lower() or "subscription" in msg.lower():
            _report(
                "BLE subscription", _Result.FAIL, f"Rejected: {e}",
                hint=(
                    "The ESP32 already has a BLE subscription from another client.\n"
                    "This is almost always caused by the Home Assistant ESPHome integration:\n"
                    "\n"
                    "  FIX: In Home Assistant go to:\n"
                    "    Settings → Devices & Services → ESPHome\n"
                    "    Find your aquaclean-proxy entry → click ⋮ → Disable\n"
                    "\n"
                    "  The integration must be DISABLED (not deleted) while the bridge is running.\n"
                    "  After disabling, wait 60–90 s for the ESP32 to release the slot, then retry."
                ),
            )
            subscription_error = "conflict"
            return False
        else:
            _report(
                "BLE subscription", _Result.FAIL, f"Error: {e}",
                hint="Try rebooting the ESP32 and running the test again.",
            )
            return False

    # Wait for warmup — see if any packets arrive
    await asyncio.sleep(SUBSCRIPTION_WARMUP)

    if unsub:
        try:
            unsub()
        except Exception:
            pass

    total_packets = sum(received_packets)
    if total_packets == 0:
        _report(
            "BLE subscription", _Result.WARN, "Subscription accepted but 0 packets received in 3s",
            hint=(
                "The BLE scanner may be stuck or another client holds the subscription silently.\n"
                "• Check if the Home Assistant ESPHome integration is enabled — disable it.\n"
                "• Reboot the ESP32 (power-cycle, not just restart command).\n"
                "• Verify aquaclean-proxy.yaml has 'bluetooth_proxy: active: true'."
            ),
        )
        return False
    else:
        lag_ms = int((first_packet_time - subscription_start) * 1000) if first_packet_time else 0
        _report(
            "BLE subscription", _Result.PASS,
            f"Receiving BLE packets ({total_packets} total, first arrived in {lag_ms}ms)"
        )
        return True

# ---------------------------------------------------------------------------
# Check 4 — BLE scan: find Geberit device
# ---------------------------------------------------------------------------
async def check_ble_scan(
    client: APIClient, target_mac: Optional[str], scan_duration: float,
    identify_by: str = "any",
    dump_ads: bool = False,
) -> Optional[str]:
    _section("Step 4 — BLE Scan")
    id_label = {"name": "name only", "uuid": "service UUID only (testing)", "any": "name or UUID"}.get(identify_by, identify_by)
    print(f"  Scanning for {scan_duration:.0f} seconds …  identify-by: {id_label}")

    seen: dict[str, tuple[str, int, bytes]] = {}   # mac_str -> (name, rssi, adv_bytes)
    target_int = mac_str_to_int(target_mac) if target_mac else None

    def on_raw_advertisements(resp) -> None:
        for adv in resp.advertisements:
            mac_str = mac_int_to_str(adv.address)
            adv_bytes = bytes(adv.data)
            name = parse_local_name(adv_bytes)
            existing = seen.get(mac_str, ("", 0, b""))
            # Keep the longest adv_bytes seen (some packets carry more AD types than others)
            kept_bytes = adv_bytes if len(adv_bytes) >= len(existing[2]) else existing[2]
            seen[mac_str] = (name if name else existing[0], adv.rssi, kept_bytes)

    unsub = None
    try:
        unsub = client.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
    except Exception as e:
        _report("BLE scan", _Result.FAIL, f"Cannot subscribe: {e}")
        return None

    await asyncio.sleep(scan_duration)

    if unsub:
        try:
            unsub()
        except Exception:
            pass

    if not seen:
        _report(
            "BLE scan", _Result.FAIL, "No BLE devices found at all",
            hint=(
                "The ESP32 BLE scanner is not receiving any advertisements.\n"
                "• Reboot the ESP32 (power-cycle).\n"
                "• Make sure aquaclean-proxy.yaml sets 'active: true' under scan_parameters.\n"
                "• Check the ESP32 is physically close enough to BLE devices."
            ),
        )
        return None

    # Print all found devices, highlight Geberit candidates
    geberit_found: list[str] = []
    print()
    print(f"  {'MAC Address':<20} {'RSSI':>9}  Name")
    print("  " + "─" * 55)
    for mac, (name, rssi, adv_bytes) in sorted(seen.items(), key=lambda x: -x[1][1]):
        is_target  = target_mac and mac.upper() == target_mac.upper()
        is_geberit = _is_geberit(name, identify_by, adv_data=adv_bytes)
        marker = ""
        if is_target and is_geberit:
            marker = _green("  ← TARGET") + _cyan(" + Geberit UUID ✅" if identify_by == "uuid" else " + Geberit ✅")
            geberit_found.insert(0, mac)
        elif is_target:
            marker = _green("  ← TARGET")
            geberit_found.insert(0, mac)
        elif is_geberit:
            marker = _cyan("  ← Geberit device")
            geberit_found.append(mac)
        print(f"  {mac:<20} {rssi:>+5} dBm  {name}{marker}")
        if dump_ads:
            _dump_ad_structures(mac, adv_bytes)

    print(f"\n  {len(seen)} device(s) found in {scan_duration:.0f}s.")

    if target_mac:
        found = any(mac.upper() == target_mac.upper() for mac in seen)
        if found:
            rssi = seen.get(target_mac.upper(), seen.get(target_mac.lower(), ("", 0, b"")))[1]
            _report(
                "Target device found", _Result.PASS,
                f"MAC {target_mac} visible at {rssi:+d} dBm",
            )
            if rssi < -85:
                _report(
                    "Signal strength", _Result.WARN, f"RSSI {rssi:+d} dBm — weak signal",
                    hint=(
                        "Weak BLE signal can cause connection timeouts (E0002/E0003).\n"
                        "• Move the ESP32 closer to the toilet.\n"
                        "• An ESP32-POE-ISO on a PoE switch port is more stable than WiFi-powered."
                    ),
                )
            return target_mac
        else:
            _report(
                "Target device found", _Result.FAIL,
                f"MAC {target_mac} not seen during {scan_duration:.0f}s scan",
                hint=(
                    "The Geberit toilet was not advertising during the scan.\n"
                    "Possible causes:\n"
                    "• The Geberit Home app is open on a phone/tablet nearby.\n"
                    "  → Close the Geberit Home app completely, then retry.\n"
                    "• Another bridge instance is currently connected to the toilet.\n"
                    "  → Stop the other bridge, wait 30s, retry.\n"
                    "• The toilet is powered off or in sleep mode.\n"
                    "  → Try approaching the toilet (proximity sensor may wake it).\n"
                    "• The MAC address is wrong — check the scan output above for 'HB…' devices."
                ),
            )
            if geberit_found:
                print(
                    f"\n  {_yellow('Hint:')} Found {len(geberit_found)} other Geberit-looking device(s):\n"
                    + "\n".join(f"    {m}  ({seen[m][0]})" for m in geberit_found)
                    + "\n  → Try one of these MACs with --mac"
                )
            if not dump_ads and seen:
                print(f"\n  {_yellow('Auto-dumping advertisement data for all seen devices:')}")
                for mac, (name, rssi, adv_bytes) in sorted(seen.items(), key=lambda x: -x[1][1]):
                    print(f"\n  {mac}  {rssi:+d} dBm  {name or '(no name)'}")
                    _dump_ad_structures(mac, adv_bytes)
            return None
    else:
        if geberit_found:
            _report(
                "Geberit device(s) found", _Result.PASS,
                f"Found {len(geberit_found)} Geberit-looking device(s) (identify-by: {identify_by})",
                hint="Use --mac <MAC> to verify the correct device and test BLE connection.",
            )
            return geberit_found[0]
        else:
            _report(
                "Geberit device detection", _Result.WARN,
                "No Geberit device found — auto-dumping advertisements to help identify your device",
                hint=(
                    "No Geberit-looking device found. Either:\n"
                    "• The device is not advertising (close the Geberit Home app).\n"
                    "• The device name is different on your model — check the AD dump below.\n"
                    "• Extend the scan: --scan-duration 30\n"
                    "• Supply the known MAC with --mac to search specifically."
                ),
            )
            if not dump_ads and seen:
                print(f"\n  {_yellow('Advertisement data for all seen devices:')}")
                for mac, (name, rssi, adv_bytes) in sorted(seen.items(), key=lambda x: -x[1][1]):
                    print(f"\n  {mac}  {rssi:+d} dBm  {name or '(no name)'}")
                    _dump_ad_structures(mac, adv_bytes)
            return None

# ---------------------------------------------------------------------------
# Check 5 — BLE connect attempt
# ---------------------------------------------------------------------------
async def check_ble_connect(client: APIClient, mac: str, scan_duration: float) -> None:
    _section("Step 5 — BLE Connect Attempt")
    print(f"  Trying to connect to {mac} via ESP32 BLE proxy …")
    print(f"  (Scanning for device advertisement first, up to {scan_duration:.0f}s)")

    mac_int = mac_str_to_int(mac)
    found_event = asyncio.Event()
    address_type_seen: list[int] = []

    def on_raw_advertisements(resp) -> None:
        for adv in resp.advertisements:
            if adv.address == mac_int:
                if not found_event.is_set():
                    address_type_seen.append(adv.address_type)
                    found_event.set()

    unsub = None
    try:
        unsub = client.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
    except Exception as e:
        _report("BLE connect", _Result.FAIL, f"Cannot subscribe for scan: {e}")
        return

    try:
        await asyncio.wait_for(found_event.wait(), timeout=scan_duration)
    except asyncio.TimeoutError:
        if unsub:
            try: unsub()
            except Exception: pass
        _report(
            "BLE connect", _Result.FAIL, f"Device {mac} not found in {scan_duration:.0f}s",
            hint="Close the Geberit Home app and retry.",
        )
        return

    # IMPORTANT: keep the advertisement subscription alive through the BLE connect call.
    # The ESP32 needs its BLE scanner active to find the device and initiate the link.
    # Unsubscribing before bluetooth_device_connect() causes a 25s timeout.
    # (Same pattern as ESPHomeAPIClient._connect() in the bridge.)
    addr_type = address_type_seen[0] if address_type_seen else 0
    _report("Device advertisement", _Result.PASS, f"Seen MAC {mac}  address_type={addr_type}")

    # Attempt BLE connect using the callback API (aioesphomeapi >= 14)
    # bluetooth_device_connect takes an on_bluetooth_connection_state(connected, mtu, error) cb;
    # it returns a cancel-connection callable (not a handle).
    # feature_flags must come from device_info.bluetooth_proxy_feature_flags (not 0).
    feature_flags: int = getattr(client, "_test_feature_flags", 0)
    loop = asyncio.get_running_loop()
    connected_future: asyncio.Future = loop.create_future()
    cancel_connection: Optional[object] = None

    def on_bluetooth_connection_state(connected: bool, mtu: int, error: int) -> None:
        if connected_future.done():
            return
        if error:
            connected_future.set_exception(Exception(f"BLE connection error code {error}"))
        elif connected:
            connected_future.set_result(mtu)
        else:
            connected_future.set_exception(Exception("Disconnected during connection attempt"))

    try:
        cancel_connection = await asyncio.wait_for(
            client.bluetooth_device_connect(
                mac_int,
                on_bluetooth_connection_state,
                address_type=addr_type,
                has_cache=False,
                feature_flags=feature_flags,
                timeout=20.0,
                disconnect_timeout=5.0,
            ),
            timeout=25.0,
        )
        # Wait for the connection state callback to confirm the link is up
        mtu = await asyncio.wait_for(connected_future, timeout=20.0)
    except asyncio.TimeoutError:
        # Unsub advertisement subscription on failure
        if unsub:
            try: unsub()
            except Exception: pass
        _report(
            "BLE connect", _Result.FAIL, "Connection timed out after 25s",
            hint=(
                "The ESP32 could not establish the BLE link within 25 seconds.\n"
                "• Power-cycle the toilet (turn off at the wall, wait 30s, turn on).\n"
                "• Reboot the ESP32.\n"
                "• If this is a Geberit Alba, the device may require a different connection mode\n"
                "  — please open an issue at github.com/jens62/geberit-aquaclean/issues with\n"
                "  the full output of this script."
            ),
        )
        return
    except Exception as e:
        # Unsub advertisement subscription on failure
        if unsub:
            try: unsub()
            except Exception: pass
        msg = str(e)
        if "disconnect" in msg.lower() and ("0x16" in msg or "before connected" in msg.lower()):
            _report(
                "BLE connect", _Result.FAIL,
                f"Disconnected before connection established (reason 0x16): {e}",
                hint=(
                    "The ESP32 connected at the BLE link layer but the Geberit device immediately\n"
                    "disconnected (reason 0x16 = 'Connection Terminated By Local Host').\n"
                    "This has been seen on some devices (e.g. Geberit Alba).\n"
                    "Possible causes:\n"
                    "• The Geberit Home app is connected — close it completely.\n"
                    "• The toilet needs a PIN/pairing step not yet supported by this bridge.\n"
                    "• Try with has_cache=True (not testable with this script — report the issue).\n"
                    "\n"
                    "Please open an issue at github.com/jens62/geberit-aquaclean/issues and\n"
                    "include the full output of this script."
                ),
            )
        elif "one api subscription" in msg.lower():
            _report(
                "BLE connect", _Result.FAIL, "Only one API subscription allowed",
                hint=(
                    "Another client grabbed the BLE subscription slot between steps.\n"
                    "Disable the Home Assistant ESPHome integration for your aquaclean-proxy\n"
                    "and run the test again."
                ),
            )
        else:
            _report(
                "BLE connect", _Result.FAIL, f"Error: {type(e).__name__}: {e}",
                hint="Reboot the ESP32 and the toilet, then retry.",
            )
        return

    # Connected — unsub advertisement subscription, then disconnect BLE cleanly
    if unsub:
        try: unsub()
        except Exception: pass
    try:
        await asyncio.wait_for(
            client.bluetooth_device_disconnect(mac_int),
            timeout=5.0,
        )
    except Exception:
        pass  # best-effort disconnect

    _report(
        "BLE connect", _Result.PASS,
        f"Successfully connected to {mac} via ESP32 proxy!",
    )
    print(f"\n  {_green('The ESP32 proxy and Geberit BLE connection work correctly.')}")
    print(f"  If the bridge still fails, re-run with --verbose and open an issue.")


# ---------------------------------------------------------------------------
# Check 6 — Device Identification via bridge stack
# ---------------------------------------------------------------------------
async def check_device_identification(
    host: Optional[str],
    port: int,
    noise_psk: Optional[str],
    mac: str,
    local_ble: bool = False,
    scan_duration: float = 20.0,
) -> None:
    """Step 6: connect via the full bridge stack and print device identification.

    Requires the aquaclean_console_app package to be importable (pip-installed or
    running from the repo root).  Gracefully skips if the package is not found.
    """
    _section("Step 6 — Device Identification")

    # Add repo root to sys.path so the bridge package is importable when running
    # from the repo directory (e.g. python tools/aquaclean-connection-test.py).
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    try:
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            BluetoothLeConnector,
        )
        from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import (
            AquaCleanBaseClient,
        )
    except ImportError as e:
        _report(
            "Bridge package import", _Result.SKIP,
            f"aquaclean_console_app not importable: {e}",
            hint=(
                "The bridge package must be installed (pip install -e .) or this script\n"
                "must be run from the repo root directory."
            ),
        )
        return

    # Suppress bridge logging noise (same loggers as filter-probe.py)
    import logging as _logging
    for _logger_name in (
        "aquaclean_console_app", "aioesphomeapi", "bleak",
        "aquaclean_console_app.bluetooth_le", "aquaclean_console_app.aquaclean_core",
    ):
        _logging.getLogger(_logger_name).setLevel(_logging.WARNING)

    # Register TRACE/SILLY levels to avoid AttributeError in bridge modules
    for _lvl_name, _lvl_val in (("SILLY", 4), ("TRACE", 5)):
        if not hasattr(_logging, _lvl_name):
            _logging.addLevelName(_lvl_val, _lvl_name)
            setattr(_logging, _lvl_name, _lvl_val)
            setattr(_logging.Logger, _lvl_name.lower(),
                    lambda self, msg, *a, _v=_lvl_val, **kw: self.log(_v, msg, *a, **kw))

    esphome_host = None if local_ble else host
    esphome_port = port if not local_ble else 6053
    connector   = BluetoothLeConnector(esphome_host=esphome_host, esphome_port=esphome_port, esphome_noise_psk=noise_psk)
    base_client = AquaCleanBaseClient(connector)

    mode_label = "local BLE (bleak)" if local_ble else f"ESP32 proxy ({host})"
    print(f"  Connecting to {mac} via {mode_label} …")

    run_gatt_diagnostic = False

    try:
        try:
            await asyncio.wait_for(base_client.connect_async(mac), timeout=30.0)
            await base_client.subscribe_notifications_async()
        except asyncio.TimeoutError:
            _report(
                "BLE connect", _Result.FAIL, "Timed out after 30s",
                hint="Close the Geberit Home app and retry.",
            )
            return
        except Exception as e:
            _report(
                "BLE connect", _Result.FAIL, f"{type(e).__name__}: {e}",
                hint="Make sure the Geberit Home app is closed and the toilet is reachable.",
            )
            return

        _report("BLE connect", _Result.PASS, f"Connected to {mac}")

        # Identification
        try:
            ident = await asyncio.wait_for(
                base_client.get_device_identification_async(0),
                timeout=15.0,
            )
        except Exception as e:
            _report("Device identification", _Result.FAIL, f"{type(e).__name__}: {e}",
                    hint="BLE connected but GATT protocol failed — running GATT diagnostic below.")
            run_gatt_diagnostic = True
            return

        print()
        print(f"    {'Description':<22}  {getattr(ident, 'description', '?')}")
        print(f"    {'Serial Number':<22}  {getattr(ident, 'serial_number', '?')}")
        print(f"    {'SAP Number':<22}  {getattr(ident, 'sap_number', '?')}")
        print(f"    {'Production Date':<22}  {getattr(ident, 'production_date', '?')}")

        # Initial operation date
        try:
            initial_op = await asyncio.wait_for(
                base_client.get_device_initial_operation_date(),
                timeout=10.0,
            )
            print(f"    {'Initial Operation':<22}  {initial_op}")
        except Exception as e:
            print(f"    {'Initial Operation':<22}  (error: {e})")

        # Firmware / SOC versions
        try:
            soc = await asyncio.wait_for(
                base_client.get_soc_application_versions_async(),
                timeout=10.0,
            )
            print(f"    {'Firmware':<22}  {soc}")
        except Exception as e:
            print(f"    {'Firmware':<22}  (error: {e})")

        print()
        _report("Device identification", _Result.PASS, "All identification fields read successfully")

    finally:
        try:
            await asyncio.wait_for(base_client.disconnect(), timeout=5.0)
        except Exception:
            pass

    # GATT diagnostic — BLE slot is now free; open a fresh connection
    if run_gatt_diagnostic and not local_ble and host:
        await check_gatt_services(host, port, noise_psk, mac, scan_duration)


# ---------------------------------------------------------------------------
# GATT service dump — auto-triggered when Step 6 identification fails
# ---------------------------------------------------------------------------

# Known Geberit AquaClean GATT UUIDs (service + characteristics)
_GEBERIT_GATT_UUIDS: dict[str, str] = {
    "3334429d-90f3-4c41-a02d-5cb3a03e0000": "Geberit AquaClean Service",
    "3334429d-90f3-4c41-a02d-5cb3a13e0000": "WRITE_0  (outgoing commands, FIRST frame)",
    "3334429d-90f3-4c41-a02d-5cb3a23e0000": "WRITE_1  (outgoing commands, CONS frame)",
    "3334429d-90f3-4c41-a02d-5cb3a33e0000": "WRITE_2",
    "3334429d-90f3-4c41-a02d-5cb3a43e0000": "WRITE_3",
    "3334429d-90f3-4c41-a02d-5cb3a53e0000": "READ_0   (incoming notifications)",
    "3334429d-90f3-4c41-a02d-5cb3a63e0000": "READ_1",
    "3334429d-90f3-4c41-a02d-5cb3a73e0000": "READ_2",
    "3334429d-90f3-4c41-a02d-5cb3a83e0000": "READ_3",
}

_GATT_PROP_NAMES = [
    (0x02, "READ"), (0x04, "WRITE_NO_RESP"), (0x08, "WRITE"),
    (0x10, "NOTIFY"), (0x20, "INDICATE"),
]


def _format_gatt_props(props: int) -> str:
    return " | ".join(name for bit, name in _GATT_PROP_NAMES if props & bit) or f"0x{props:02X}"


async def _probe_via_bridge_stack(
    host: Optional[str],
    port: int,
    noise_psk: Optional[str],
    mac: str,
    svc_uuid: str,
    write_uuids: "list[str]",
    read_uuids: "list[str]",
    scan_duration: float,
) -> None:
    """Probe device identification using the full bridge stack with injected GATT UUIDs.

    Used by --dynamic-uuids and the auto-probe on unknown GATT profiles:
    1. Extracts the candidate service + characteristic UUIDs from the GATT table.
    2. Overrides the BluetoothLeConnector class-level UUID constants as instance
       attributes so the bridge's _post_connect() / _list_services() / send_message()
       pick up the dynamically discovered UUIDs instead of the hardcoded defaults.
    3. Runs the standard connect + subscribe_notifications + get_device_identification
       flow — identical to Step 6 (Device Identification) but with injected UUIDs.

    No protocol code is duplicated here — 100% of the bridge stack is reused.
    """
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    try:
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
        from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import (
            AquaCleanBaseClient, BLEPeripheralTimeoutError,
        )
    except ImportError as e:
        _report(
            "Bridge package import", _Result.SKIP,
            f"aquaclean_console_app not importable: {e}",
            hint="Run from the repo root or install the package with pip install -e .",
        )
        return

    import logging as _logging
    from uuid import UUID as _UUID

    for _logger_name in ("aquaclean_console_app", "aioesphomeapi", "bleak"):
        _logging.getLogger(_logger_name).setLevel(_logging.WARNING)
    for _lvl_name, _lvl_val in (("SILLY", 4), ("TRACE", 5)):
        if not hasattr(_logging, _lvl_name):
            _logging.addLevelName(_lvl_val, _lvl_name)
            setattr(_logging, _lvl_name, _lvl_val)
            setattr(_logging.Logger, _lvl_name.lower(),
                    lambda self, msg, *a, _v=_lvl_val, **kw: self.log(_v, msg, *a, **kw))

    is_standard = svc_uuid.lower() == GEBERIT_SERVICE_UUID

    label = "standard Geberit" if is_standard else "non-standard"
    print(f"\n  {_cyan(f'→ Injecting {label} UUIDs into bridge stack and probing …')}")
    print(f"    Service: {svc_uuid}")
    for i, u in enumerate(write_uuids[:4]):
        print(f"    WRITE_{i}: {u}")
    for i, u in enumerate(read_uuids[:4]):
        print(f"    READ_{i}:  {u}")
    print()

    # Pad write/read lists to 4 (bridge expects 4 of each; repeat last if fewer)
    def _pad4(lst: "list[str]") -> "list[str]":
        return (lst + [lst[-1]] * 4)[:4]

    w = _pad4(write_uuids)
    r = _pad4(read_uuids)

    connector = BluetoothLeConnector(
        esphome_host=host, esphome_port=port, esphome_noise_psk=noise_psk,
    )
    # Override class-level UUID constants as instance attributes.
    # BluetoothLeConnector._post_connect(), _list_services(), and send_message()
    # all read these via self.X — instance attrs shadow the class-level defaults.
    connector.SERVICE_UUID                = _UUID(svc_uuid)
    connector.BULK_CHAR_BULK_WRITE_0_UUID = _UUID(w[0])
    connector.BULK_CHAR_BULK_WRITE_1_UUID = _UUID(w[1])
    connector.BULK_CHAR_BULK_WRITE_2_UUID = _UUID(w[2])
    connector.BULK_CHAR_BULK_WRITE_3_UUID = _UUID(w[3])
    connector.BULK_CHAR_BULK_READ_0_UUID  = _UUID(r[0])
    connector.BULK_CHAR_BULK_READ_1_UUID  = _UUID(r[1])
    connector.BULK_CHAR_BULK_READ_2_UUID  = _UUID(r[2])
    connector.BULK_CHAR_BULK_READ_3_UUID  = _UUID(r[3])

    base_client = AquaCleanBaseClient(connector)
    mode_label = f"ESP32 proxy ({host})" if host else "local BLE"
    print(f"  Connecting to {mac} via {mode_label} …")

    try:
        await asyncio.wait_for(base_client.connect_async(mac), timeout=30.0)
        await base_client.subscribe_notifications_async()
    except asyncio.TimeoutError:
        _report(
            "Protocol probe", _Result.FAIL, "BLE connect timed out after 30s",
            hint="Close the Geberit Home app and retry.",
        )
        try: await base_client.disconnect()
        except Exception: pass
        return
    except Exception as e:
        _report(
            "Protocol probe", _Result.FAIL, f"BLE connect failed: {type(e).__name__}: {e}",
            hint="Make sure the Geberit Home app is closed and the toilet is reachable.",
        )
        try: await base_client.disconnect()
        except Exception: pass
        return

    ident = None
    try:
        ident = await asyncio.wait_for(
            base_client.get_device_identification_async(0),
            timeout=15.0,
        )
    except BLEPeripheralTimeoutError:
        _report(
            "Protocol probe", _Result.FAIL,
            "GetDeviceIdentification timed out — device did not respond",
            hint=(
                "BLE connected and init sequence sent, but no response to identification.\n"
                "• Power-cycle the toilet (30s off) and retry.\n"
                "• If this is a non-standard model, open a GitHub issue with this full output:\n"
                "  https://github.com/jens62/geberit-aquaclean/issues"
            ),
        )
    except Exception as e:
        _report(
            "Protocol probe", _Result.FAIL, f"{type(e).__name__}: {e}",
            hint="Open a GitHub issue with the full output of this script.",
        )
    finally:
        try: await base_client.disconnect()
        except Exception: pass

    if ident is None:
        return

    desc = getattr(ident, "description",    None)
    sn   = getattr(ident, "serial_number",  None)
    sap  = getattr(ident, "sap_number",     None)
    pd   = getattr(ident, "production_date", None)
    print()
    if desc: print(f"    {'Description':<22}  {desc}")
    if sn:   print(f"    {'Serial Number':<22}  {sn}")
    if sap:  print(f"    {'SAP Number':<22}  {sap}")
    if pd:   print(f"    {'Production Date':<22}  {pd}")

    if is_standard:
        _report(
            "Protocol probe", _Result.PASS,
            "Device identification succeeded — bridge stack + injected UUIDs work correctly",
        )
    else:
        _report(
            "Protocol probe", _Result.PASS,
            (
                f"Device responds to Geberit protocol on non-standard service!\n"
                f"         Service UUID: {svc_uuid}\n"
                f"         WRITE_0 UUID: {write_uuids[0]}\n"
                f"         READ_0 UUID:  {read_uuids[0]}"
            ),
            hint=(
                "This device uses non-standard Geberit GATT UUIDs.\n"
                "Please open a GitHub issue with the full output of this script\n"
                "so support for this model can be added:\n"
                "  https://github.com/jens62/geberit-aquaclean/issues"
            ),
        )


async def check_gatt_services(
    host: str,
    port: int,
    noise_psk: Optional[str],
    mac: str,
    scan_duration: float,
    force_probe: bool = False,
) -> None:
    """Diagnostic: connect BLE via raw ESPHome API and dump GATT service table.

    Auto-triggered when Step 6 (Device Identification) fails after a successful
    BLE connect.  Prints the GATT profile and then probes via the full bridge
    stack (_probe_via_bridge_stack) when the Geberit service is not found.

    force_probe (--dynamic-uuids): also run the bridge-stack probe when the known
    Geberit service UUID IS found, to validate that the probe mechanism works and
    that GetDeviceIdentification succeeds with the discovered UUIDs.
    """
    print()
    print(f"  {_yellow('→ Auto-running GATT service discovery to diagnose the failure …')}")

    mac_int = mac_str_to_int(mac)
    gatt_client = APIClient(address=host, port=port, password="", noise_psk=noise_psk)

    try:
        await asyncio.wait_for(gatt_client.connect(login=True), timeout=API_CONNECT_TIMEOUT)
    except Exception as e:
        print(f"  {_red('GATT diagnostic: could not connect to ESP32:')} {e}")
        return

    # Scan for device to get address_type
    found_event = asyncio.Event()
    addr_type_seen: list[int] = []

    def _on_adv(resp) -> None:
        for adv in resp.advertisements:
            if adv.address == mac_int and not found_event.is_set():
                addr_type_seen.append(adv.address_type)
                found_event.set()

    unsub = None
    try:
        unsub = gatt_client.subscribe_bluetooth_le_raw_advertisements(_on_adv)
    except Exception as e:
        print(f"  {_red('GATT diagnostic: BLE subscription failed:')} {e}")
        await gatt_client.disconnect()
        return

    try:
        await asyncio.wait_for(found_event.wait(), timeout=scan_duration)
    except asyncio.TimeoutError:
        if unsub:
            try: unsub()
            except Exception: pass
        print(f"  {_red('GATT diagnostic: device not found in scan — cannot read GATT profile')}")
        await gatt_client.disconnect()
        return

    addr_type = addr_type_seen[0] if addr_type_seen else 0
    feature_flags: int = 0
    try:
        info = await gatt_client.device_info()
        feature_flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
    except Exception:
        pass

    # Connect BLE
    loop = asyncio.get_running_loop()
    conn_future: asyncio.Future = loop.create_future()
    cancel_conn = None

    def _on_conn_state(connected: bool, mtu: int, error: int) -> None:
        if conn_future.done():
            return
        if error:
            conn_future.set_exception(Exception(f"BLE error {error}"))
        elif connected:
            conn_future.set_result(mtu)
        else:
            conn_future.set_exception(Exception("Disconnected during connect"))

    try:
        cancel_conn = await asyncio.wait_for(
            gatt_client.bluetooth_device_connect(
                mac_int, _on_conn_state,
                address_type=addr_type, has_cache=False,
                feature_flags=feature_flags, timeout=20.0, disconnect_timeout=5.0,
            ),
            timeout=25.0,
        )
        await asyncio.wait_for(conn_future, timeout=20.0)
    except Exception as e:
        if unsub:
            try: unsub()
            except Exception: pass
        print(f"  {_red('GATT diagnostic: BLE connect failed:')} {e}")
        try: await gatt_client.disconnect()
        except Exception: pass
        return

    # NOTE: do NOT call unsub() here — doing so while BLE is active queues an
    # UnsubscribeBluetoothLEAdvertisementsRequest which is flushed on the next
    # await, causing the ESP32 to drop the BLE link (trap 12 in CLAUDE.md).
    # unsub() is called after bluetooth_device_disconnect below.

    # Read GATT services
    try:
        services = await asyncio.wait_for(
            gatt_client.bluetooth_gatt_get_services(mac_int),
            timeout=15.0,
        )
    except Exception as e:
        _report(
            "GATT service discovery", _Result.FAIL,
            f"{type(e).__name__}: {e}" if str(e) else type(e).__name__,
            hint=(
                "GATT service discovery failed. The BLE connection was established but the\n"
                "ESP32 could not retrieve the device's GATT service table.\n"
                "• Power-cycle the toilet (30s off) and retry.\n"
                "• Make sure no other app (Geberit Home) is connected to the device."
            ),
        )
        services = None

    # Print GATT table (diagnostic output only — candidate extraction is delegated
    # to classify_services() from GattDiscovery, shared with the bridge stack).
    if services:
        print()
        print(f"  {'─'*62}")
        print(f"  GATT profile for {mac}:")
        print(f"  {'─'*62}")
        for svc in services.services:
            uuid_lower = svc.uuid.lower()
            label = _GEBERIT_GATT_UUIDS.get(uuid_lower, "")
            marker = _green(f"  ✅ {label}") if label else ""
            print(f"  Service  {svc.uuid}  (handle 0x{svc.handle:04X}){marker}")
            for char in svc.characteristics:
                cuuid = char.uuid.lower()
                clabel = _GEBERIT_GATT_UUIDS.get(cuuid, "")
                cmarker = _green(f"  ← {clabel}") if clabel else ""
                props_str = _format_gatt_props(char.properties)
                print(f"    Char   {char.uuid}  [{props_str}]{cmarker}")
        print(f"  {'─'*62}")
        print()

    # Classify using shared GattDiscovery logic (same algorithm used by the
    # bridge HACS config flow — avoids duplicating the candidate extraction).
    _repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _repo_root not in sys.path:
        sys.path.insert(0, _repo_root)
    try:
        from aquaclean_console_app.bluetooth_le.LE.GattDiscovery import classify_services
        _profile = classify_services(services.services if services else [])
        geberit_service_found = _profile.is_standard
        candidate = (
            (_profile.svc_uuid, _profile.write_uuids, _profile.notify_uuids)
            if not _profile.is_standard and _profile.svc_uuid != "unknown" and _profile.write_uuids
            else None
        )
    except ImportError:
        # Bridge package not installed — minimal fallback (probe step will also be skipped).
        geberit_service_found = bool(services) and any(
            svc.uuid.lower() == GEBERIT_SERVICE_UUID for svc in services.services
        )
        candidate = None

        if geberit_service_found:
            _report(
                "GATT profile", _Result.PASS,
                "Geberit AquaClean service found — device GATT profile is correct",
                hint=(
                    "The BLE GATT profile is correct but the bridge protocol failed.\n"
                    "Most likely causes:\n"
                    "• The device was busy (another connection between Step 5 and Step 6).\n"
                    "• Device stuck in an unusual state — power-cycle the toilet and retry.\n"
                    "• Geberit Home app was open on a nearby phone — close it and retry."
                ),
            )
        else:
            _report(
                "GATT profile", _Result.FAIL,
                "Geberit AquaClean service NOT found in GATT table",
                hint=(
                    "The connected device does not have the expected Geberit GATT service.\n"
                    "Either:\n"
                    "• Wrong device — check the MAC address.\n"
                    "• Device is in an unusual firmware state — power-cycle and retry.\n"
                    "• This is an unsupported Geberit model — probing below to check compatibility."
                ),
            )
    elif services is None:
        pass  # already reported above via _report(FAIL)
    else:
        _report(
            "GATT service discovery", _Result.WARN,
            "No GATT services returned (empty list)",
        )

    # Determine whether to run the bridge-stack probe:
    # • force_probe (--dynamic-uuids): always probe using discovered UUIDs
    # • Geberit service not found: auto-probe to check if device uses non-standard UUIDs
    run_probe = candidate is not None and (force_probe or (services is not None and not geberit_service_found))

    # Disconnect GATT discovery BLE before probe — device only allows one connection.
    # For force_probe the bridge stack will reconnect on its own.
    try:
        await asyncio.wait_for(gatt_client.bluetooth_device_disconnect(mac_int), timeout=5.0)
    except Exception:
        pass
    if unsub:
        try: unsub()
        except Exception: pass
    try:
        await asyncio.wait_for(gatt_client.disconnect(), timeout=3.0)
    except Exception:
        pass

    if run_probe:
        svc_uuid, write_uuids, read_uuids = candidate
        await _probe_via_bridge_stack(
            host, port, noise_psk, mac,
            svc_uuid, write_uuids, read_uuids, scan_duration,
        )
    elif candidate is None and services is not None and not geberit_service_found:
        print(f"  {_yellow('No WRITE+NOTIFY service found in GATT table — cannot probe protocol.')}")


# ---------------------------------------------------------------------------
# Step 7 — Stream ESP32 device logs
# ---------------------------------------------------------------------------

_LOG_LEVEL_MAP = {
    "verbose": "LOG_LEVEL_VERBOSE",
    "debug":   "LOG_LEVEL_DEBUG",
    "info":    "LOG_LEVEL_INFO",
    "warn":    "LOG_LEVEL_WARN",
    "error":   "LOG_LEVEL_ERROR",
}

# ESPHome log level int → (label, color_fn)
_ESPHOME_LEVEL_STYLE: dict[int, tuple[str, object]] = {
    1: ("E", _red),
    2: ("W", _yellow),
    3: ("I", str),
    4: ("C", str),
    5: ("D", _cyan),
    6: ("V", lambda s: f"\033[2m{s}\033[0m" if _USE_COLOR else s),  # dim
    7: ("T", lambda s: f"\033[2m{s}\033[0m" if _USE_COLOR else s),
}

import re as _re
_ANSI_RE = _re.compile(r'(?:\x1b|\033)\[[0-9;]*[mK]')
_ESPHOME_LOG_RE = _re.compile(r'^\[([EWICBDVT])\]\[([^\]]+?)(?::\d+)?\]:\s*(.*)$')


def _format_log_entry(entry) -> Optional[str]:
    """Format an ESPHome SubscribeLogsResponse for terminal output."""
    raw = entry.message
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    clean = _ANSI_RE.sub("", raw).strip()
    if not clean:
        return None

    level_int = getattr(entry, "level", 5)
    label, color_fn = _ESPHOME_LEVEL_STYLE.get(level_int, ("D", _cyan))

    m = _ESPHOME_LOG_RE.match(clean)
    if m:
        _, component, message = m.groups()
        line = f"[{label}] [{component}] {message}"
    else:
        line = f"[{label}] {clean}"

    return color_fn(line)  # type: ignore[operator]


async def check_esp32_logs(
    host: str,
    port: int,
    noise_psk: Optional[str],
    duration: float,
    log_level_name: str = "debug",
) -> None:
    """Step 7: connect to the ESP32 and stream its logs for *duration* seconds."""
    from aioesphomeapi import LogLevel

    _section("Step 7 — ESP32 Log Stream")

    level_attr = _LOG_LEVEL_MAP.get(log_level_name.lower(), "LOG_LEVEL_DEBUG")
    log_level  = getattr(LogLevel, level_attr, LogLevel.LOG_LEVEL_DEBUG)

    print(f"  Connecting to {host}:{port} for log streaming …")
    print(f"  Level: {log_level_name}  |  Duration: {duration:.0f}s")
    print(f"  {_yellow('Note:')} BLE steps must be fully done — log streaming holds the ESP32 API slot.")
    print()

    log_client = APIClient(address=host, port=port, password="", noise_psk=noise_psk)
    try:
        await asyncio.wait_for(log_client.connect(login=True), timeout=API_CONNECT_TIMEOUT)
    except Exception as e:
        _report(
            "ESP32 log connect", _Result.FAIL, f"{type(e).__name__}: {e}",
            hint="The ESP32 API connection failed. Is the host reachable?",
        )
        return

    _report("ESP32 log connect", _Result.PASS, f"Connected to {host}:{port}")
    print()
    print(f"  {'─' * 60}")

    log_count = 0
    unsub = None

    def on_log(entry) -> None:
        nonlocal log_count
        line = _format_log_entry(entry)
        if line:
            log_count += 1
            print(f"  {line}")

    try:
        # dump_config=True asks the ESP32 to immediately re-emit its component
        # configuration as log messages.  This guarantees at least a few lines of
        # output even when the BLE scanner is idle — useful to confirm the
        # callback path works and that the firmware logger is not completely silent.
        unsub = log_client.subscribe_logs(on_log, log_level=log_level, dump_config=True)
        await asyncio.sleep(duration)
    except Exception as e:
        print()
        _report("ESP32 log stream", _Result.FAIL, f"{type(e).__name__}: {e}")
        return
    finally:
        if unsub:
            try:
                unsub()
            except Exception:
                pass
        try:
            await asyncio.wait_for(log_client.disconnect(), timeout=3.0)
        except Exception:
            pass

    print(f"  {'─' * 60}")
    print()
    if log_count == 0:
        _report(
            "ESP32 log stream", _Result.WARN,
            f"0 log lines received in {duration:.0f}s — ESP32 logger may be silenced",
            hint=(
                "The ESP32 firmware logger is likely set to WARN or NONE in your YAML:\n"
                "  logger:\n"
                "    level: DEBUG   ← add or change this line, then reflash\n"
                "\n"
                "Without DEBUG level, BLE scanner activity (device found/lost,\n"
                "connection events) is never emitted to the API log stream.\n"
                "The config dump (dump_config=True) also produced 0 lines, which\n"
                "confirms the logger is silent at the firmware level."
            ),
        )
    else:
        _report(
            "ESP32 log stream", _Result.PASS,
            f"Streamed {log_count} log line(s) in {duration:.0f}s",
        )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
def print_summary() -> None:
    _section("Summary")
    passed = sum(1 for _, s, _ in _results if s == _Result.PASS)
    warned = sum(1 for _, s, _ in _results if s == _Result.WARN)
    failed = sum(1 for _, s, _ in _results if s == _Result.FAIL)
    total  = len(_results)

    for step, status, detail in _results:
        color = {"PASS": _green, "WARN": _yellow, "FAIL": _red, "SKIP": _cyan}.get(status, str)
        tag = color(f"[{status:<4}]")
        print(f"  {tag}  {step}")

    print()
    if failed == 0 and warned == 0:
        print(f"  {_green('All checks passed.')}  The connection stack is healthy.")
    elif failed == 0:
        print(f"  {_yellow(f'{warned} warning(s).')}  Review hints above.")
    else:
        print(f"  {_red(f'{failed} check(s) failed')} — follow the hints above to fix them.")

    print()
    print(f"  Script version:        {SCRIPT_VERSION}")
    print(f"  aioesphomeapi version: {_AIOESPHOMEAPI_VERSION}")
    print(f"  Python version:        {sys.version.split()[0]}")
    print()
    if failed > 0:
        print(
            f"  If you cannot resolve the issue, open a GitHub issue at:\n"
            f"    https://github.com/jens62/geberit-aquaclean/issues\n"
            f"  and paste the full output of this script."
        )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run(args: argparse.Namespace) -> None:
    print()
    print(_bold(f"Geberit AquaClean — Connection Test  (v{SCRIPT_VERSION})"))

    # -----------------------------------------------------------------------
    # Local BLE mode (--local-ble)
    # -----------------------------------------------------------------------
    if args.local_ble:
        print(f"  Mode:           Local BLE (bleak)")
        print(f"  Target MAC:     {args.mac or '(not specified — scan only)'}")
        print(f"  Scan duration:  {args.scan_duration:.0f}s")
        print()
        geberit_found = await check_local_ble_scan(args.mac, args.scan_duration, identify_by=args.identify_by)
        # Step 6 — if a MAC is known (explicit or auto-detected), connect via bridge stack
        ble_mac = args.mac or (geberit_found[0] if len(geberit_found) == 1 else None)
        if ble_mac:
            await check_device_identification(
                host=None, port=ESPHOME_API_PORT, noise_psk=None,
                mac=ble_mac.upper(), local_ble=True,
            )
        elif len(geberit_found) > 1:
            _section("Step 6 — Device Identification")
            _report(
                "Device identification", _Result.SKIP,
                f"Multiple Geberit devices found — specify one with --mac",
                hint=f"Found: {', '.join(geberit_found)}",
            )
        else:
            _section("Step 6 — Device Identification")
            _report(
                "Device identification", _Result.SKIP,
                "Skipped — no Geberit device found in scan",
                hint="Re-run with --mac <MAC> to test full device identification.",
            )
        if args.stream_logs:
            _section("Step 7 — ESP32 Log Stream")
            _report(
                "ESP32 log stream", _Result.SKIP,
                "Skipped — local BLE mode has no ESP32 to stream logs from",
            )
        print_summary()
        return

    # -----------------------------------------------------------------------
    # ESPHome proxy mode (default)
    # -----------------------------------------------------------------------
    host = args.host
    port = args.port

    if not host:
        # Step 0 — mDNS auto-discovery
        host, port = await check_esphome_discovery(timeout=8.0)
        if not host:
            print_summary()
            return
    else:
        print(f"  Host:           {host}:{port}")

    print(f"  Noise PSK:      {'(set)' if args.noise_psk else '(none)'}")
    print(f"  Target MAC:     {args.mac or '(not specified — scan only)'}")
    print(f"  Scan duration:  {args.scan_duration:.0f}s")
    print()

    # Step 1 — TCP
    tcp_ok = await check_tcp(host, port)
    if not tcp_ok:
        print_summary()
        return

    # Step 2 — API connect
    client = await check_api_connect(host, port, args.noise_psk)
    if client is None:
        print_summary()
        return

    mac_to_connect: Optional[str] = None
    sub_ok = False

    try:
        # Step 3 — subscription check (3-second warmup only)
        sub_ok = await check_ble_subscription(client)

        # Step 4 — full scan
        if sub_ok:
            found_mac = await check_ble_scan(client, args.mac, args.scan_duration, identify_by=args.identify_by, dump_ads=args.dump_ads)
        else:
            _report("BLE scan", _Result.SKIP, "Skipped — subscription check failed")
            found_mac = None

        # Step 5 — BLE connect (only if we have a MAC to try)
        mac_to_connect = found_mac or args.mac
        if mac_to_connect and sub_ok:
            await check_ble_connect(client, mac_to_connect.upper(), args.scan_duration)
        elif not mac_to_connect:
            _section("Step 5 — BLE Connect Attempt")
            _report(
                "BLE connect", _Result.SKIP,
                "Skipped — no MAC address known",
                hint="Re-run with --mac <MAC> to test the BLE connection itself.",
            )

    finally:
        # Disconnect the test client BEFORE Step 6 so the bridge stack can open
        # its own TCP connection to the ESP32 (ESP32 allows only one BLE subscription).
        try:
            await client.disconnect()
        except Exception:
            pass

    # Step 6 — Device Identification via bridge stack (after ESPHome client disconnected)
    if mac_to_connect and sub_ok:
        if args.dynamic_uuids:
            _section("Step 6 — GATT Discovery + Protocol Probe (--dynamic-uuids)")
            await check_gatt_services(
                host, port, args.noise_psk, mac_to_connect.upper(),
                scan_duration=args.scan_duration, force_probe=True,
            )
        else:
            await check_device_identification(
                host, port, args.noise_psk, mac_to_connect.upper(),
                scan_duration=args.scan_duration,
            )
    else:
        _section("Step 6 — Device Identification")
        _report(
            "Device identification", _Result.SKIP,
            "Skipped — no confirmed Geberit MAC address",
            hint="Re-run with --mac <MAC> after confirming the device in the scan above.",
        )

    # Step 7 — ESP32 log streaming (optional, only when --stream-logs is set)
    if args.stream_logs:
        await check_esp32_logs(
            host, port, args.noise_psk,
            duration=args.stream_duration,
            log_level_name=args.log_level,
        )

    print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host", default=None,
        help="ESPHome proxy hostname or IP (e.g. 192.168.0.160 or aquaclean-proxy.local). "
             "If omitted, ESPHome devices are auto-discovered via mDNS.",
    )
    parser.add_argument(
        "--port", type=int, default=ESPHOME_API_PORT,
        help=f"ESPHome API port (default: {ESPHOME_API_PORT})",
    )
    parser.add_argument(
        "--noise-psk", default=None, dest="noise_psk",
        help="Base64 API encryption key from secrets.yaml (api_encryption_key)",
    )
    parser.add_argument(
        "--mac", default=None,
        help="Geberit BLE MAC address to look for (e.g. AA:BB:CC:DD:EE:FF). "
             "If omitted, all devices are listed.",
    )
    parser.add_argument(
        "--scan-duration", type=float, default=20.0, dest="scan_duration", metavar="SECONDS",
        help="How long to scan for BLE advertisements (default: 20s)",
    )
    parser.add_argument(
        "--local-ble", action="store_true", dest="local_ble",
        help="Use local Bluetooth adapter (bleak) instead of ESPHome proxy. "
             "Skips mDNS discovery and all ESPHome checks.",
    )
    parser.add_argument(
        "--dump-ads", action="store_true", dest="dump_ads",
        help="Dump raw advertisement AD structures for every device in the BLE scan. "
             "Use with --identify-by uuid to diagnose whether the Geberit service UUID "
             "is present in the advertisement payload.",
    )
    parser.add_argument(
        "--identify-by", default="any", dest="identify_by",
        choices=["any", "name", "uuid"],
        help=(
            "How to identify Geberit devices in the BLE scan. "
            "'any' (default): name OR service UUID — most robust. "
            "'name': name prefix 'HB' or 'Geberit' in name only. "
            "'uuid': Geberit service UUID in advertisement only — use to test UUID parser."
        ),
    )
    parser.add_argument(
        "--stream-logs", action="store_true", dest="stream_logs",
        help=(
            "After all BLE checks complete, stream ESP32 device logs to the terminal. "
            "Useful for diagnosing BLE scanner issues (e.g. stuck scanner, advertisement gaps). "
            "ESPHome mode only — skipped with --local-ble."
        ),
    )
    parser.add_argument(
        "--stream-duration", type=float, default=30.0, dest="stream_duration", metavar="SECONDS",
        help="How long to stream ESP32 logs (default: 30s). Used with --stream-logs.",
    )
    parser.add_argument(
        "--log-level", default="debug", dest="log_level",
        choices=list(_LOG_LEVEL_MAP.keys()),
        help="ESP32 log level to stream (default: debug). Used with --stream-logs.",
    )
    parser.add_argument(
        "--dynamic-uuids", action="store_true", dest="dynamic_uuids",
        help=(
            "Step 6: discover GATT services dynamically and probe with the Geberit protocol "
            "instead of using the hardcoded Geberit UUIDs. "
            "Useful to test the probe on a known device or to identify models with "
            "non-standard UUIDs. Requires --mac and ESPHome mode."
        ),
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
