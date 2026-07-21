#!/usr/bin/env python3
"""
minimal-central.py — connect to minimal-peripheral.py and verify multi-service discovery.

Scans for a peripheral advertising the service UUID minimal-peripheral.py's first
registered service uses (--advertised-uuid; falls back to name matching, --name,
if that doesn't find anything), connects, enumerates ALL discovered services/
characteristics, and reports whether the expected number of services were found,
with correct UUIDs (matching the "0000{BASE_ALIAS+i:04x}-0000-1000-8000-
00805f9b34fb" pattern minimal-peripheral.py registers) and no obviously wrong/
garbled UUID. See minimal-peripheral.py's docstring and local-assets/bluez-
multi-service-question.md for the investigation this supports — bisecting the exact
service count where BlueZ's discovery response corrupts UUIDs/handle ranges and
silently drops services registered after the corrupted one.

NOTE (2026-07-21): on macOS/CoreBluetooth, bleak's plain name-based discovery is
unreliable for a peripheral this host has never connected to before — confirmed
directly: a peripheral visible in nRF Connect (Android) and in this script's own
--diagnostics dump was still not found by --name from a Mac scanning for the first
time. --advertised-uuid (service-UUID-filtered scanning) is the reliable path;
--name is a fallback only, and --expect alone won't help if discovery itself fails.

Usage:
  python3 minimal-central.py --expect 6            # bisect: try 1..6 against a peripheral
                                                    #   started with the matching --num-services
  python3 minimal-central.py --expect 4 --verbose
  python3 minimal-central.py --mac <addr_or_uuid> --expect 6
  python3 minimal-central.py --name "Geberit AC PRO" --advertised-uuid ''   # sanity-check against the real mock
  python3 minimal-central.py --diagnostics          # dump all raw advertisements
"""

import asyncio
import argparse
import re
from typing import Optional

from bleak import BleakScanner, BleakClient

_SCRIPT_VERSION = "1.2.0"

BASE_ALIAS   = 0x1000
DEFAULT_NAME = "MultiSvc-Test"
_VENDOR_UUID_RE = re.compile(r"^0000([0-9a-f]{4})-0000-1000-8000-00805f9b34fb$", re.IGNORECASE)


def _expected_uuid(i: int) -> str:
    return f"0000{BASE_ALIAS + i:04x}-0000-1000-8000-00805f9b34fb"


async def discover_by_service(timeout: float, service_uuid: str, verbose: bool = False) -> Optional[object]:
    """Scan filtered by service_uuid, then INDEPENDENTLY VERIFY the match against the
    device's own advertised UUIDs (via return_adv=True) rather than trusting bleak's
    built-in service_uuids filter blindly — confirmed 2026-07-21 that on at least one
    macOS/CoreBluetooth + bleak combination, the filter silently fails to restrict
    results (an unrelated real device with no matching UUID was returned as the sole
    "match"), so treat the filter as a hint/speed-up only, not as proof of a real match."""
    if verbose:
        print(f"[+] Scanning for device advertising service {service_uuid} (up to {timeout}s)…")
    try:
        found = await BleakScanner.discover(timeout=timeout, service_uuids=[service_uuid], return_adv=True)
    except TypeError:
        if verbose:
            print("[!] BleakScanner.discover does not accept service_uuids/return_adv on this Bleak "
                  "version; doing an unfiltered discover and checking advertised UUIDs manually.")
        found = await BleakScanner.discover(timeout=timeout, return_adv=True)

    # found is typically {address: (BLEDevice, AdvertisementData)} when return_adv=True.
    candidates = list(found.values()) if isinstance(found, dict) else [(d, None) for d in found]

    verified = []
    unverified_hits = []
    for device, adv in candidates:
        adv_uuids = [u.lower() for u in (getattr(adv, "service_uuids", None) or [])]
        if service_uuid.lower() in adv_uuids:
            verified.append(device)
        else:
            unverified_hits.append((device, adv_uuids))

    if verbose:
        print(f"[+] Scan returned {len(candidates)} device(s) total; "
              f"{len(verified)} independently confirmed advertising {service_uuid}.")
        for device, adv_uuids in unverified_hits:
            print(f"    (scan filter also returned {device.address} {device.name!r} "
                  f"but it advertises {adv_uuids or '(no UUIDs)'} — NOT the target UUID, skipped)")

    if verified:
        return verified[0]
    return None


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
              advertised_uuid: Optional[str],
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
        # Service-UUID filtering first: on macOS/CoreBluetooth, bleak's plain name-based
        # discover() is unreliable for a peripheral this host has never connected to
        # before (confirmed 2026-07-21 — a peripheral visible in nRF Connect and general
        # --diagnostics scanning was still not found by name). Name is only a fallback.
        if advertised_uuid:
            device = await discover_by_service(timeout, advertised_uuid, verbose=verbose)
        if device is None:
            device = await discover_by_name(timeout, name, verbose=verbose)
        if device is None:
            hint = f" or service {advertised_uuid}" if advertised_uuid else ""
            print(f"[!] No device found (name {name!r}{hint}). Is the peripheral advertising?")
            return

    print(f"[+] Found: {device.address}  name={device.name!r}")
    print("[+] Connecting…")

    async def _poll_for_services(client: BleakClient, max_attempts: int) -> list:
        # BleakGATTServiceCollection has neither __bool__ nor __len__ on newer bleak,
        # so a bare `if services:` is always True once it exists at all (even empty) —
        # materialize to a list and check that instead, so this actually waits for
        # discovery to populate at least one service, not just for the attribute to
        # stop being None.
        found = []
        for _ in range(max_attempts):
            found = list(getattr(client, "services", None) or [])
            if found:
                break
            await asyncio.sleep(0.1)
        return found

    async with BleakClient(device) as client:
        services = await _poll_for_services(client, 50)
        if not services:
            try:
                await client.connect()
            except Exception:
                pass
            services = await _poll_for_services(client, 20)

        # `services` is already a materialized list from the readiness-polling loops above.
        try:
            mtu = getattr(client, "mtu_size", None)
            print(f"[+] Connected. MTU={mtu}" if mtu else "[+] Connected.")
        except Exception:
            print("[+] Connected.")
        print()

        expected_uuids = {_expected_uuid(i).lower() for i in range(1, (expect or 0) + 1)}
        found_expected = set()
        unexpected_vendor_uuids = []

        print(f"Found {len(services)} total service(s):")
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

        if expect is None:
            # No --expect given: just report what was found (e.g. pointing this at the
            # real mock or any other peripheral to sanity-check that discovery itself
            # works) — skip the pass/fail comparison, there's nothing to compare against.
            return

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
                         "minimal-peripheral.py) — optional; omit to just report whatever is found "
                         "(e.g. when pointing this at a different peripheral, like the real mock, "
                         "just to sanity-check that discovery works)")
    ap.add_argument("--timeout", type=float, default=10.0, help="BLE scan timeout in seconds (default: 10)")
    ap.add_argument("--mac", help="CoreBluetooth UUID (macOS) or MAC address (Linux/Android)")
    ap.add_argument("--name", default=DEFAULT_NAME,
                    help=f"Advertisement name to scan for, used as fallback if --advertised-uuid doesn't "
                         f"match (default: {DEFAULT_NAME!r}; the real mock advertises as 'Geberit AC PRO')")
    ap.add_argument("--advertised-uuid", default=_expected_uuid(1),
                    help="service UUID to filter the scan by — primary discovery method, more reliable "
                         f"than --name on macOS/CoreBluetooth (default: {_expected_uuid(1)!r}, "
                         "minimal-peripheral.py's first registered service; pass '' to disable and use "
                         "--name only)")
    ap.add_argument("--verbose", action="store_true", help="Print extra debug messages")
    ap.add_argument("--diagnostics", action="store_true", help="Dump raw advertisements for the timeout and exit")
    args = ap.parse_args()
    asyncio.run(run(args.timeout, args.mac, args.name, args.expect, args.advertised_uuid or None,
                    verbose=args.verbose, diagnostics=args.diagnostics))


if __name__ == "__main__":
    main()
