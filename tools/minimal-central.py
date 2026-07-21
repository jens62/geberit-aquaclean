#!/usr/bin/env python3
"""
minimal-central.py — connect to minimal-peripheral.py and verify multi-service discovery.

Scans for a peripheral advertising the "MultiSvc-Test" name, connects, enumerates
ALL discovered services/characteristics, and reports whether the expected number of
services were found, with correct UUIDs (matching the "0000{BASE_ALIAS+i:04x}-0000-
1000-8000-00805f9b34fb" pattern minimal-peripheral.py registers) and no obviously
wrong/garbled UUID. See minimal-peripheral.py's docstring and local-assets/bluez-
multi-service-question.md for the investigation this supports — bisecting the exact
service count where BlueZ's discovery response corrupts UUIDs/handle ranges and
silently drops services registered after the corrupted one.

Usage:
  python3 minimal-central.py --expect 6            # bisect: try 1..6 against a peripheral
                                                    #   started with the matching --num-services
  python3 minimal-central.py --expect 4 --verbose
  python3 minimal-central.py --mac <addr_or_uuid> --expect 6
  python3 minimal-central.py --diagnostics          # dump all raw advertisements
"""

import asyncio
import argparse
import re
from typing import Optional

from bleak import BleakScanner, BleakClient

_SCRIPT_VERSION = "1.1.0"

BASE_ALIAS   = 0x1000
DEFAULT_NAME = "MultiSvc-Test"
_VENDOR_UUID_RE = re.compile(r"^0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb$", re.IGNORECASE)


def _expected_uuid(i: int) -> str:
    return f"0000{BASE_ALIAS + i:04x}-0000-1000-8000-00805f9b34fb"


async def discover_by_name(timeout: float, name: str, verbose: bool = False) -> Optional[object]:
    if verbose:
        print(f"[+] Scanning for device named {name!r} (up to {timeout}s)…")
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if (d.name or "").lower() == name.lower():
            if verbose:
                print(f"[+] Matched by name: {d.address} {d.name!r}")
            return d
    return None


async def dump_advertisements(timeout: float) -> None:
    def cb(device, adv):
        svc_uuids = getattr(adv, "service_uuids", None)
        print(f"ADV: {device.address} name={device.name!r} uuids={svc_uuids}")

    # register_detection_callback() was removed in newer bleak — pass the callback
    # to the constructor instead, which has worked across both old and new bleak.
    scanner = BleakScanner(detection_callback=cb)
    await scanner.start()
    await asyncio.sleep(timeout)
    await scanner.stop()


async def run(timeout: float, mac: Optional[str], name: str, expect: Optional[int],
              verbose: bool = False, diagnostics: bool = False) -> None:
    print(f"minimal-central.py v{_SCRIPT_VERSION}")
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
        device = await discover_by_name(timeout, name, verbose=verbose)
        if device is None:
            print(f"[!] No device found (name {name!r}). Is the peripheral advertising?")
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

        expected_uuids = {_expected_uuid(i).lower() for i in range(1, expect + 1)}
        found_expected = set()
        unexpected_vendor_uuids = []

        for svc in services:
            handle = getattr(svc, "handle", None)
            m = _VENDOR_UUID_RE.match(svc.uuid.lower())
            tag = "►" if m else " "
            handle_str = f"handle={handle}" if handle is not None else ""
            print(f"{tag} Service  {svc.uuid}  {handle_str}")
            chars = list(svc.characteristics)
            for c in chars:
                props = ", ".join(sorted(c.properties)) if getattr(c, "properties", None) else ""
                c_handle = getattr(c, "handle", None)
                print(f"      {c.uuid}  [{props}]" + (f"  handle={c_handle}" if c_handle is not None else ""))
            print()

            if m:
                if svc.uuid.lower() in expected_uuids:
                    found_expected.add(svc.uuid.lower())
                else:
                    unexpected_vendor_uuids.append(svc.uuid)

        missing = expected_uuids - found_expected
        print("=" * 60)
        print(f"Expected {expect} service(s) matching the 0000XXXX-...-00805f9b34fb pattern:")
        for i in range(1, expect + 1):
            u = _expected_uuid(i)
            status = "FOUND" if u.lower() in found_expected else "MISSING"
            print(f"  {u}  {status}")
        if unexpected_vendor_uuids:
            print(f"Unexpected/garbled vendor-pattern UUIDs found (not in expected set):")
            for u in unexpected_vendor_uuids:
                print(f"  {u}  <-- garbled or unexpected")
        print()
        if not missing and not unexpected_vendor_uuids:
            print(f"PASS — all {expect} services found with correct UUIDs, none garbled.")
        else:
            print(f"FAIL — {len(missing)} missing, {len(unexpected_vendor_uuids)} garbled/unexpected.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify multi-service GATT discovery (BlueZ gatt-database.c bisection)")
    ap.add_argument("--expect", type=int, default=None,
                    help="number of services the peripheral was started with (--num-services on "
                         "minimal-peripheral.py) — required unless --diagnostics is used")
    ap.add_argument("--timeout", type=float, default=10.0, help="BLE scan timeout in seconds (default: 10)")
    ap.add_argument("--mac", help="CoreBluetooth UUID (macOS) or MAC address (Linux/Android)")
    ap.add_argument("--name", default=DEFAULT_NAME, help=f"Advertisement name to scan for (default: {DEFAULT_NAME!r})")
    ap.add_argument("--verbose", action="store_true", help="Print extra debug messages")
    ap.add_argument("--diagnostics", action="store_true", help="Dump raw advertisements for the timeout and exit")
    args = ap.parse_args()
    if not args.diagnostics and args.expect is None:
        ap.error("--expect is required unless --diagnostics is used")
    asyncio.run(run(args.timeout, args.mac, args.name, args.expect, verbose=args.verbose, diagnostics=args.diagnostics))


if __name__ == "__main__":
    main()
