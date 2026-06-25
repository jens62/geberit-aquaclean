#!/usr/bin/env python3
"""
minimal-central.py — connect to minimal-peripheral.py and verify characteristic discovery.

Scans for a peripheral advertising SERVICE_UUID, connects, enumerates all
services/characteristics, and reports whether all EXPECTED_CHARS characteristics
were discovered.

NOTE: this script does NOT demonstrate a behavioral difference between original and
patched BlueZ. char_short sorts alphabetically first → lowest handle → first in queue,
so both versions return identical first responses. Original BlueZ 5.77 is spec-correct
(ATT §3.4.4.2 + GATT §4.6.1) and the Geberit mock works without any BlueZ patch.

Usage:
  python3 minimal-central.py                      # scan by service UUID
  python3 minimal-central.py --timeout 15 --verbose
  python3 minimal-central.py --mac <addr_or_uuid>
  python3 minimal-central.py --name "RBT-Test"
  python3 minimal-central.py --diagnostics        # dump all raw advertisements
"""

import asyncio
import argparse
from typing import Optional

from bleak import BleakScanner, BleakClient

SERVICE_UUID   = "12345678-90ab-cdef-0000-000000000001"
EXPECTED_CHARS = 7
DEFAULT_NAME   = "RBT-Test"


async def discover_by_service(timeout: float, name_fallback: str, verbose: bool = False) -> Optional[object]:
    if verbose:
        print(f"[+] Scanning for device advertising service {SERVICE_UUID} (up to {timeout}s)…")

    try:
        devices = await BleakScanner.discover(timeout=timeout, service_uuids=[SERVICE_UUID])
    except TypeError:
        if verbose:
            print("[!] BleakScanner.discover does not accept service_uuids on this Bleak version; doing full discover.")
        devices = await BleakScanner.discover(timeout=timeout)

    if devices:
        if verbose:
            print(f"[+] Found {len(devices)} device(s) during filtered discover; selecting first.")
        return devices[0]

    if verbose:
        print("[i] No device advertising the service UUID found; falling back to name-based scan.")

    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if (d.name or "").lower() == name_fallback.lower():
            if verbose:
                print(f"[+] Matched by name: {d.address} {d.name!r}")
            return d
    for d in devices:
        adv_uuids = getattr(d, "metadata", {}).get("uuids") or getattr(d, "service_uuids", None)
        if adv_uuids:
            if any(u.lower() == SERVICE_UUID.lower() for u in adv_uuids):
                if verbose:
                    print(f"[+] Matched by advertisement UUIDs: {d.address} {d.name!r}")
                return d
    return None


async def dump_advertisements(timeout: float) -> None:
    def cb(device, adv):
        svc_uuids = getattr(adv, "service_uuids", None)
        svc_data  = getattr(adv, "service_data", None)
        mfg       = getattr(adv, "manufacturer_data", None)
        print(f"ADV: {device.address} name={device.name!r} uuids={svc_uuids} service_data={svc_data} mfg={mfg}")

    scanner = BleakScanner()
    scanner.register_detection_callback(cb)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()


async def run(timeout: float, mac: Optional[str], name: str, verbose: bool = False, diagnostics: bool = False) -> None:
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

    async with BleakClient(device) as client:
        for attempt in range(50):
            services = getattr(client, "services", None)
            if services:
                break
            await asyncio.sleep(0.1)

        if not getattr(client, "services", None):
            try:
                await client.connect()
            except Exception:
                pass
            for _ in range(20):
                if getattr(client, "services", None):
                    break
                await asyncio.sleep(0.1)

        services = getattr(client, "services", None) or []
        try:
            mtu = getattr(client, "mtu_size", None)
            print(f"[+] Connected. MTU={mtu}" if mtu else "[+] Connected.")
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
            print()

        if not vendor_found:
            print(f"FAIL — test service ({SERVICE_UUID}) not found at all")


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify GATT characteristic discovery (Read-By-Type bug reproducer)")
    ap.add_argument("--timeout", type=float, default=10.0, help="BLE scan timeout in seconds (default: 10)")
    ap.add_argument("--mac", help="CoreBluetooth UUID (macOS) or MAC address (Linux/Android)")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"Advertisement name fallback (default: {DEFAULT_NAME!r})")
    ap.add_argument("--verbose", action="store_true", help="Print extra debug messages")
    ap.add_argument("--diagnostics", action="store_true", help="Dump raw advertisements for the timeout and exit")
    args = ap.parse_args()
    asyncio.run(run(args.timeout, args.mac, args.name, verbose=args.verbose, diagnostics=args.diagnostics))


if __name__ == "__main__":
    main()
