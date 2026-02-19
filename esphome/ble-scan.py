#!/usr/bin/env python3
"""
BLE scanner via ESPHome Bluetooth Proxy.

Lists all BLE devices visible to the ESP32-POE-ISO in the area.
Device names require active scanning — aquaclean-proxy.yaml already sets
  esp32_ble_tracker.scan_parameters.active: true
so names are included when the device broadcasts them (scan response packet).

Uses aioesphomeapi directly — works as a standalone script without Home
Assistant's habluetooth infrastructure.

Usage:
  python ble-scan.py <host> [--duration N] [--noise-psk KEY]

Examples:
  python ble-scan.py 192.168.0.160
  python ble-scan.py aquaclean-proxy.fritz.box --duration 20
  python ble-scan.py 192.168.0.160 --noise-psk "base64key=="

Requires: pip install aioesphomeapi
"""

import asyncio
import argparse
from aioesphomeapi import APIClient
from aioesphomeapi.core import TimeoutAPIError, APIConnectionError


def mac_int_to_str(addr: int) -> str:
    return ":".join(f"{(addr >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))


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
        if ad_type == 0x09:  # Complete Local Name — prefer this
            return value.decode("utf-8", errors="replace")
        elif ad_type == 0x08:  # Shortened Local Name — keep as fallback
            name = value.decode("utf-8", errors="replace")
        i += 1 + length
    return name


async def check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Fast TCP reachability check — fails immediately if port is not open."""
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def scan(host: str, noise_psk: str | None, duration: float) -> None:
    print(f"Checking port 6053 on {host} …", end=" ", flush=True)
    if not await check_port(host, 6053):
        print(f"UNREACHABLE\nPort 6053 is not open on {host}. Is the ESP32 running?")
        return
    print("OK")

    client = APIClient(address=host, port=6053, password="", noise_psk=noise_psk)
    seen: dict[str, tuple[str, int]] = {}  # mac -> (name, rssi)

    def on_raw_advertisements(resp) -> None:
        for adv in resp.advertisements:
            mac = mac_int_to_str(adv.address)
            name = parse_local_name(bytes(adv.data))
            rssi = adv.rssi
            # keep the entry with the best name; update rssi always
            existing_name = seen.get(mac, ("", 0))[0]
            seen[mac] = (name if name else existing_name, rssi)

    print("Connecting …")
    try:
        await asyncio.wait_for(client.connect(login=True), timeout=10.0)
    except (TimeoutAPIError, APIConnectionError, TimeoutError) as exc:
        print(f"FAILED\nCould not connect to ESPHome API: {exc}")
        print("Tip: restart the ESP32 and try again, or flash aquaclean-proxy.yaml first.")
        return

    unsub = client.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
    print(f"Scanning for {duration:.0f} s …")
    try:
        await asyncio.sleep(duration)
    finally:
        unsub()
        try:
            await client.disconnect()
        except Exception:
            pass  # ESP32 sometimes resets connection before DisconnectResponse arrives

    if not seen:
        print("No devices found.")
        return

    print(f"\n{'MAC Address':<20} {'RSSI':>9}  Name")
    print("-" * 58)
    for mac, (name, rssi) in sorted(seen.items()):
        print(f"{mac:<20} {rssi:>+5} dBm  {name}")
    print(f"\n{len(seen)} device(s) found.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("host", help="ESPHome proxy hostname or IP (e.g. 192.168.0.160)")
    parser.add_argument("--noise-psk", default=None, dest="noise_psk",
                        help="base64 encryption key (matches api_encryption_key in secrets.yaml)")
    parser.add_argument("--duration",  type=float, default=10.0, metavar="SECONDS")
    args = parser.parse_args()
    asyncio.run(scan(args.host, args.noise_psk, args.duration))


if __name__ == "__main__":
    main()
