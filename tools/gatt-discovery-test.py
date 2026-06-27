"""gatt-discovery-test.py — Verify that a BlueZ GATT peripheral exposes all 7
characteristics of the mock-geberit-mera service to a macOS CoreBluetooth client.

Run (mock must be active on VM):
  /Users/jens/venv/bin/python tools/gatt-discovery-test.py
  /Users/jens/venv/bin/python tools/gatt-discovery-test.py --mac A0:AD:9F:72:C4:0F
"""
import asyncio
import argparse

from bleak import BleakScanner, BleakClient

DEVICE_NAME      = "Geberit AC PRO"
VENDOR_PREFIX    = "3334429d"
EXPECTED_CHARS   = 7


async def list_devices(timeout: float) -> None:
    print(f"Scanning for all BLE devices ({timeout}s)…")
    devices = await BleakScanner.discover(timeout=timeout)
    for d in sorted(devices, key=lambda x: x.name or ""):
        print(f"  {d.address}  {d.name!r}")


async def run(timeout: float, mac: str | None, name: str) -> None:
    if mac:
        device = await BleakScanner.find_device_by_address(mac, timeout=timeout)
        if device is None:
            print(f"Device {mac} not found within {timeout}s scan.")
            return
    else:
        print(f"Scanning for '{name}' (up to {timeout}s)…")
        device = await BleakScanner.find_device_by_name(name, timeout=timeout)
        if device is None:
            print(f"'{name}' not found. Is the mock running on the VM?")
            return

    print(f"Found: {device.address}  name={device.name!r}")
    print("Connecting…")

    async with BleakClient(device) as client:
        try:
            print(f"Connected. MTU={client.mtu_size}")
        except Exception:
            print("Connected.")
        print()

        vendor_found = False
        for svc in client.services:
            is_vendor = svc.uuid.startswith(VENDOR_PREFIX)
            tag = "►" if is_vendor else " "
            print(f"{tag} Service  {svc.uuid}  ({svc.description})")

            chars = list(svc.characteristics)
            for c in chars:
                print(f"      {c.uuid}  [{', '.join(sorted(c.properties))}]")

            if is_vendor:
                vendor_found = True
                n = len(chars)
                print()
                if n == EXPECTED_CHARS:
                    print(f"  PASS — {n}/{EXPECTED_CHARS} characteristics discovered ✓")
                else:
                    print(f"  FAIL — {n}/{EXPECTED_CHARS} characteristics discovered")
                    print("         (BlueZ Read By Type bug: response truncated to 1 item)")
            print()

        if not vendor_found:
            print(f"FAIL — vendor service ({VENDOR_PREFIX}…) not found at all")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="GATT discovery test for mock-geberit-mera"
    )
    ap.add_argument(
        "--list", action="store_true",
        help="Scan and list all visible BLE devices (useful to find the mock's name on macOS)",
    )
    ap.add_argument(
        "--timeout", type=float, default=10.0,
        help="BLE scan timeout in seconds (default: 10)",
    )
    ap.add_argument(
        "--mac",
        help="CoreBluetooth UUID (macOS) or MAC address (Linux) — "
             "NOTE: raw Bluetooth MACs are hidden by macOS/CoreBluetooth; "
             "use the UUID shown by --list or omit this flag to scan by name",
    )
    ap.add_argument(
        "--name", default=DEVICE_NAME,
        help=f"BLE advertisement name to scan for (default: {DEVICE_NAME!r})",
    )
    args = ap.parse_args()
    if args.list:
        asyncio.run(list_devices(args.timeout))
    else:
        asyncio.run(run(args.timeout, args.mac, args.name))


if __name__ == "__main__":
    main()
