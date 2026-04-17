#!/usr/bin/env python3
"""
aquaclean-connection-test.py — Geberit AquaClean ESPHome Proxy Connection Test
===============================================================================

Diagnoses why the Geberit AquaClean bridge cannot connect via an ESPHome BLE
proxy. Runs a series of checks and prints PASS / WARN / FAIL with actionable
hints for each step.

Requires ONLY aioesphomeapi — no bridge installation needed.

Usage
-----
  python aquaclean-connection-test.py --host 192.168.0.160
  python aquaclean-connection-test.py --host 192.168.0.160 --mac AA:BB:CC:DD:EE:FF
  python aquaclean-connection-test.py --host aquaclean-proxy.local --noise-psk "base64key=="
  python aquaclean-connection-test.py --host 192.168.0.160 --scan-duration 30

Install
-------
  pip install aioesphomeapi

See docs/connection-test.md for full installation and usage guide.
"""

from __future__ import annotations

import argparse
import asyncio
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
GEBERIT_BLE_NAME_PREFIX = "HB"   # Geberit devices advertise names like HB2304EU…
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
    client: APIClient, target_mac: Optional[str], scan_duration: float
) -> Optional[str]:
    _section("Step 4 — BLE Scan")
    print(f"  Scanning for {scan_duration:.0f} seconds …  (Geberit devices advertise as 'HB…')")

    seen: dict[str, tuple[str, int]] = {}   # mac_str -> (name, rssi)
    target_int = mac_str_to_int(target_mac) if target_mac else None

    def on_raw_advertisements(resp) -> None:
        for adv in resp.advertisements:
            mac_str = mac_int_to_str(adv.address)
            name = parse_local_name(bytes(adv.data))
            existing_name = seen.get(mac_str, ("", 0))[0]
            seen[mac_str] = (name if name else existing_name, adv.rssi)

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
    for mac, (name, rssi) in sorted(seen.items(), key=lambda x: -x[1][1]):
        is_target = target_mac and mac.upper() == target_mac.upper()
        is_geberit = name.startswith(GEBERIT_BLE_NAME_PREFIX)
        marker = ""
        if is_target:
            marker = _green("  ← TARGET")
        elif is_geberit:
            marker = _cyan("  ← Geberit device")
            geberit_found.append(mac)
        print(f"  {mac:<20} {rssi:>+5} dBm  {name}{marker}")
        if is_target:
            geberit_found.insert(0, mac)

    print(f"\n  {len(seen)} device(s) found in {scan_duration:.0f}s.")

    if target_mac:
        found = any(mac.upper() == target_mac.upper() for mac in seen)
        if found:
            rssi = seen.get(target_mac.upper(), seen.get(target_mac.lower(), ("", 0)))[1]
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
            return None
    else:
        if geberit_found:
            _report(
                "Geberit device(s) found", _Result.PASS,
                f"Found {len(geberit_found)} Geberit-looking device(s) (name starts with 'HB')",
                hint="Use --mac <MAC> to verify the correct device and test BLE connection.",
            )
            return geberit_found[0]
        else:
            _report(
                "Geberit device detection", _Result.WARN,
                "No device with name starting 'HB' found",
                hint=(
                    "No Geberit-looking device found. Either:\n"
                    "• The device is not advertising (close the Geberit Home app).\n"
                    "• The device name is different on your model.\n"
                    "  → Look for an 'HB…' or unfamiliar name in the scan output above.\n"
                    "• Extend the scan: --scan-duration 30\n"
                    "• Supply the known MAC with --mac to search specifically."
                ),
            )
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
    print(_bold("Geberit AquaClean — ESPHome Proxy Connection Test"))
    print(f"  Host:           {args.host}:{args.port}")
    print(f"  Noise PSK:      {'(set)' if args.noise_psk else '(none)'}")
    print(f"  Target MAC:     {args.mac or '(not specified — scan only)'}")
    print(f"  Scan duration:  {args.scan_duration:.0f}s")
    print()

    # Step 1 — TCP
    tcp_ok = await check_tcp(args.host, args.port)
    if not tcp_ok:
        print_summary()
        return

    # Step 2 — API connect
    client = await check_api_connect(args.host, args.port, args.noise_psk)
    if client is None:
        print_summary()
        return

    try:
        # Step 3 — subscription check (3-second warmup only)
        sub_ok = await check_ble_subscription(client)

        # Step 4 — full scan
        if sub_ok:
            found_mac = await check_ble_scan(client, args.mac, args.scan_duration)
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
        try:
            await client.disconnect()
        except Exception:
            pass

    print_summary()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host", required=True,
        help="ESPHome proxy hostname or IP (e.g. 192.168.0.160 or aquaclean-proxy.local)",
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
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
