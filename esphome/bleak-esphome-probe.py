#!/usr/bin/env python3
"""
bleak-esphome GATT probe — tests bleak through ESP32 proxy.

Connects to a BLE device by MAC address via the ESP32 using bleak-esphome v3.x.
Uses ESPHomeClient directly, bypassing the habluetooth scanning complexity.
Prints all GATT services and characteristics.

This tests the same code path used by the AquaClean bridge when configured
to use the ESP32 proxy. If this works, the bridge will work.

Usage:
    python bleak-esphome-probe.py <proxy_ip> <ble_mac> [--address-type 0|1]

Example:
    python bleak-esphome-probe.py 192.168.0.154 93:4B:5C:A6:84:61
    python bleak-esphome-probe.py 192.168.0.154 B6:49:F1:FC:8B:C7 --address-type 1

address_type: 0 = public, 1 = random (default: 1 for randomized MACs)
"""

import asyncio
import argparse
from aioesphomeapi import APIClient
from aioesphomeapi.core import TimeoutAPIError, APIConnectionError
from bleak_esphome.connect import connect_scanner
from bleak_esphome.backend.client import ESPHomeClient
from bleak.backends.device import BLEDevice


async def probe(proxy_host: str, ble_mac: str, address_type: int) -> None:
    """Connect to BLE device via ESP32 proxy and dump GATT services."""

    # Connect to ESP32 proxy
    api = APIClient(address=proxy_host, port=6053, password="", noise_psk=None)

    print(f"Connecting to ESP32 proxy at {proxy_host}:6053 …", flush=True)
    try:
        await asyncio.wait_for(api.connect(login=True), timeout=10.0)
    except (TimeoutAPIError, APIConnectionError, TimeoutError) as exc:
        print(f"FAILED: {exc}")
        print("Tip: Check the ESP32 is reachable and port 6053 is open.")
        return

    device_info = await api.device_info()
    print(f"Proxy: {device_info.name} (BLE MAC: {device_info.bluetooth_mac_address})")

    # Set up the bleak-esphome scanner infrastructure
    # Note: connect_scanner calls habluetooth.get_manager() internally
    # This might fail in standalone mode, but we'll try anyway
    print("Setting up bleak-esphome client …", flush=True)
    try:
        client_data = connect_scanner(api, device_info, available=True)
        client_data.scanner.async_setup()
    except Exception as exc:
        print(f"FAILED: {exc}")
        print("\nNote: bleak-esphome v3.x requires habluetooth infrastructure.")
        print("Standalone usage may not be fully supported.")
        await api.disconnect()
        return

    # Build a BLEDevice manually — no scanning needed since we know the MAC
    # This bypasses the habluetooth advertisement discovery layer
    source = device_info.bluetooth_mac_address or device_info.mac_address
    ble_device = BLEDevice(
        address=ble_mac.upper(),
        name="",
        details={
            "source": source,
            "address_type": address_type,
        },
        rssi=-80,  # dummy value, not used for connection
    )

    # Create ESPHomeClient (a BleakClient backend for the ESP32 proxy)
    print(f"Connecting to BLE device {ble_mac} via proxy …", flush=True)
    esp_client = ESPHomeClient(ble_device, client_data=client_data)

    try:
        await esp_client.connect(pair=False)
    except Exception as exc:
        print(f"FAILED: {exc}")
        print("\nTroubleshooting:")
        print("  - Is the BLE device powered on and in range of the ESP32?")
        print("  - Try --address-type 0 if the device uses a public MAC address")
        await api.disconnect()
        return

    print("Connected!")

    # Dump GATT services
    print(f"\nGATT services for {ble_mac}:\n")
    for svc in esp_client.services:
        print(f"[{svc.handle:04x}] Service: {svc.uuid}")
        for char in svc.characteristics:
            props = ", ".join(char.properties)
            print(f"  [{char.handle:04x}] Char: {char.uuid}")
            print(f"         Properties: {props}")

    print(f"\n{len(esp_client.services)} service(s) found.")

    # Clean up
    await esp_client.disconnect()
    print("\nDisconnected.")
    await api.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("proxy_host", help="ESP32 proxy IP or hostname")
    parser.add_argument("ble_mac", help="BLE device MAC address (e.g., 93:4B:5C:A6:84:61)")
    parser.add_argument("--address-type", type=int, default=1, dest="address_type",
                        choices=[0, 1],
                        help="0=public MAC, 1=random MAC (default: 1)")
    args = parser.parse_args()

    try:
        asyncio.run(probe(args.proxy_host, args.ble_mac, args.address_type))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
