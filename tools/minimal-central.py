#!/usr/bin/env python3
"""
minimal-central.py

Scan for a peripheral advertising SERVICE_UUID, connect, list services/characteristics,
and verify the vendor service has EXPECTED_CHARS characteristics.

Usage:
  python minimal-central.py            # scan by service UUID (recommended on macOS)
  python minimal-central.py --timeout 15 --verbose
  python minimal-central.py --mac <addr_or_corebluetooth_uuid>
  python minimal-central.py --name "Geberit AC PRO"
"""

import asyncio
import argparse
from typing import Optional

from bleak import BleakScanner, BleakClient

SERVICE_UUID = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
EXPECTED_CHARS = 7
DEFAULT_NAME = "AC250"


async def discover_by_service(timeout: float, name_fallback: str, verbose: bool = False) -> Optional[object]:
    """
    Prefer discover(service_uuids=[...]) which is reliable on macOS.
    If that returns nothing, fall back to a name-based discover.
    """
    if verbose:
        print(f"[+] Scanning for device advertising service {SERVICE_UUID} (up to {timeout}s)…")

    # Try filtered discovery first (macOS/CoreBluetooth friendly)
    try:
        devices = await BleakScanner.discover(timeout=timeout, service_uuids=[SERVICE_UUID])
    except TypeError:
        # Older Bleak versions may not accept service_uuids kwarg
        if verbose:
            print("[!] BleakScanner.discover does not accept service_uuids on this Bleak version; doing full discover.")
        devices = await BleakScanner.discover(timeout=timeout)

    if devices:
        # pick first device that advertises the service (Bleak already filtered if service_uuids used)
        if verbose:
            print(f"[+] Found {len(devices)} device(s) during filtered discover; selecting first.")
        return devices[0]

    if verbose:
        print("[i] No device advertising the service UUID found; falling back to name-based scan.")

    # Fallback: full discover and match by name or by advertisement service_uuids if available
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if (d.name or "").lower() == name_fallback.lower():
            if verbose:
                print(f"[+] Matched by name: {d.address} {d.name!r}")
            return d
    # As a last attempt, check advertisement service_uuids if Bleak exposes them in the device object
    for d in devices:
        adv_uuids = getattr(d, "metadata", {}).get("uuids") or getattr(d, "service_uuids", None)
        if adv_uuids:
            if any(u.lower() == SERVICE_UUID.lower() for u in adv_uuids):
                if verbose:
                    print(f"[+] Matched by advertisement UUIDs: {d.address} {d.name!r}")
                return d
    return None


async def dump_advertisements(timeout: float) -> None:
    """
    Diagnostic helper: print every advertisement seen during the timeout.
    Useful when debugging why a service UUID is not visible to the central.
    """
    def cb(device, adv):
        svc_uuids = getattr(adv, "service_uuids", None)
        svc_data = getattr(adv, "service_data", None)
        mfg = getattr(adv, "manufacturer_data", None)
        print(f"ADV: {device.address} name={device.name!r} uuids={svc_uuids} service_data={svc_data} mfg={mfg}")

    scanner = BleakScanner()
    scanner.register_detection_callback(cb)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()


async def run(timeout: float, mac: Optional[str], name: str, verbose: bool = False, diagnostics: bool = False) -> None:
    # Optional diagnostics: print raw advertisements while scanning
    if diagnostics:
        print("[*] Running advertisement dump for diagnostics...")
        await dump_advertisements(timeout)
        return

    device = None
    if mac:
        if verbose:
            print(f"[+] Looking up device by address/UUID: {mac} (timeout {timeout}s)…")
        device = await BleakScanner.find_device_by_address(mac, timeout=timeout)
        if device is None:
            print(f"[!] Device {mac} not found within {timeout}s scan.")
            return
    else:
        device = await discover_by_service(timeout, name, verbose=verbose)
        if device is None:
            print(f"[!] No device found (service {SERVICE_UUID} or name {name!r}). Is the peripheral advertising?")
            return

    print(f"[+] Found: {device.address}  name={device.name!r}")
    print("[+] Connecting…")

    # Use context manager; Bleak performs connect on __aenter__
    async with BleakClient(device) as client:
        # Some Bleak versions populate services during connect; others may do it slightly later.
        # Wait a short while for services to appear (robust across versions/backends).
        for attempt in range(50):  # up to ~5 seconds (50 * 0.1)
            services = getattr(client, "services", None)
            if services:
                break
            await asyncio.sleep(0.1)

        # If still empty, try a short explicit connect call (some backends require it)
        if not getattr(client, "services", None):
            try:
                # Some Bleak versions expose connect() and discovery behavior; attempt to trigger it.
                await client.connect()
            except Exception:
                # ignore if connect is not supported or already connected
                pass
            # wait a bit more
            for _ in range(20):
                if getattr(client, "services", None):
                    break
                await asyncio.sleep(0.1)

        services = getattr(client, "services", None) or []
        try:
            mtu = getattr(client, "mtu_size", None)
            if mtu:
                print(f"[+] Connected. MTU={mtu}")
            else:
                print("[+] Connected.")
        except Exception:
            print("[+] Connected.")
        print()

        vendor_found = False
        for svc in services:
            is_vendor = svc.uuid.lower() == SERVICE_UUID.lower()
            tag = "►" if is_vendor else " "
            print(f"{tag} Service  {svc.uuid}  ({getattr(svc, 'description', '')})")
            chars = list(svc.characteristics)
            for c in chars:
                props = ", ".join(sorted(c.properties)) if getattr(c, "properties", None) else ""
                print(f"      {c.uuid}  [{props}]")
            if is_vendor:
                vendor_found = True
                n = len(chars)
                print()
                if n == EXPECTED_CHARS:
                    print(f"  PASS — {n}/{EXPECTED_CHARS} characteristics discovered ✓")
                else:
                    print(f"  FAIL — {n}/{EXPECTED_CHARS} characteristics discovered")
                    print("         (Possible discovery timing or Read-By-Type truncation)")
            print()

        if not vendor_found:
            print(f"FAIL — vendor service ({SERVICE_UUID}) not found at all")


def main() -> None:
    ap = argparse.ArgumentParser(description="Minimal central to verify GATT discovery")
    ap.add_argument("--timeout", type=float, default=10.0, help="BLE scan timeout in seconds (default: 10)")
    ap.add_argument("--mac", help="CoreBluetooth UUID (macOS) or MAC address (Linux)")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"Advertisement name fallback (default: {DEFAULT_NAME!r})")
    ap.add_argument("--verbose", action="store_true", help="Print extra debug messages")
    ap.add_argument("--diagnostics", action="store_true", help="Dump raw advertisements for the timeout and exit")
    args = ap.parse_args()
    asyncio.run(run(args.timeout, args.mac, args.name, verbose=args.verbose, diagnostics=args.diagnostics))


if __name__ == "__main__":
    main()
