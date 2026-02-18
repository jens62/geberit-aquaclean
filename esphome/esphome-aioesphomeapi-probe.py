#!/usr/bin/env python3
"""
Minimal BLE device probe via ESPHome Bluetooth Proxy — aioesphomeapi approach.

Connects to a known BLE device through the ESP32-POE-ISO proxy and dumps all
GATT services, characteristics and readable values.

This uses the ESPHome native API (aioesphomeapi) directly, bypassing bleak
entirely.  Useful for low-level debugging of the ESP32 proxy itself.

NOTE: This is NOT the migration path for aquaclean_console_app.
      For testing the bleak migration use bleak-esphome-probe.py instead.

Usage:
  python esphome-aioesphomeapi-probe.py <proxy_host> <ble_mac> [password]

Example:
  python esphome-aioesphomeapi-probe.py aquaclean-proxy.fritz.box 38:AB:XX:XX:ZZ:67 mypass

Requires: pip install aioesphomeapi
"""

import asyncio
import sys
from aioesphomeapi import APIClient
from aioesphomeapi import BluetoothProxyFeature

async def probe(proxy_host: str, ble_address: str, noise_psk: str | None = None):
    mac_int = int(ble_address.replace(":", ""), 16)

    client = APIClient(address=proxy_host, port=6053, password="", noise_psk=noise_psk)
    print(f"Connecting to ESPHome proxy at {proxy_host} …")

    client.set_debug(True)
    print("REMOTE_CACHING mask:", BluetoothProxyFeature.REMOTE_CACHING)
    await client.connect(login=True)

    # Print device/server info returned by the API
    try:
        info = await client.device_info()
        device_feature_flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
        print("device_info:", info)
        print("bluetooth_proxy_feature_flags:", getattr(info, "bluetooth_proxy_feature_flags", None))

        # after await client.connect(login=True) and after reading device info
        device_feature_flags = getattr(info, "bluetooth_proxy_feature_flags", 0)
        client.set_debug(True)

        # Quick scan to confirm the peripheral is advertising
        seen = False
        def on_adv(addr, rssi, adv):
            nonlocal seen
            if addr.upper() == ble_address:
                seen = True
                print("Advertisement seen:", addr, "rssi:", rssi)

        cancel_adv = await client.subscribe_bluetooth_le_advertisements(on_adv)
        await asyncio.sleep(5.0)
        cancel_adv()

        if not seen:
            print("Warning: target not seen in adverts. It may not be advertising or is connected elsewhere.")

        # Try connect with retries, RANDOM address type, longer timeout
        for attempt in range(3):
            try:
                print(f"Connect attempt {attempt+1}")
                cancel_connection = await client.bluetooth_device_connect(
                    mac_int,
                    on_bluetooth_connection_state,
                    timeout=60.0,
                    disconnect_timeout=10.0,
                    feature_flags=device_feature_flags,
                    has_cache=False,
                    address_type=1,  # try RANDOM
                )
                # wait for connected_future in your existing code
                break
            except Exception as e:
                print(f"Connect attempt {attempt+1} failed: {e}")
                await asyncio.sleep(2.0)
        else:
            raise RuntimeError("All connect attempts failed")


        # If the object has a features attribute, print it
        if hasattr(info, "features"):
            print("device_info.features:", info.features)
        # Print any other useful attributes
        for attr in ("name", "version", "board", "platform"):
            if hasattr(info, attr):
                print(f"{attr}: {getattr(info, attr)}")
    except Exception as e:
        print("Failed to get device_info:", e)
        
    print(f"Connected.  Connecting to BLE device {ble_address} …")

    # Set up connection state callback
    cancel_connection = None
    connected_future = asyncio.get_running_loop().create_future()

    def on_bluetooth_connection_state(connected: bool, mtu: int, error: int) -> None:
        if not connected_future.done():
            if error:
                connected_future.set_exception(Exception(f"Connection error: {error}"))
            elif connected:
                connected_future.set_result(mtu)
            else:
                connected_future.set_exception(Exception("Disconnected"))

    cancel_connection = await client.bluetooth_device_connect(
        mac_int,
        on_bluetooth_connection_state,
        timeout=30.0,
        disconnect_timeout=10.0,    # allow graceful disconnect
        feature_flags=device_feature_flags,
        has_cache=False,     # keep False unless you actually have a cache
        address_type=1,      # try RANDOM (1) if PUBLIC (0) fails
    )

    try:
        mtu = await asyncio.wait_for(connected_future, timeout=30.0)
        print(f"BLE connected (MTU: {mtu}).  Fetching GATT services …\n")
        resp = await client.bluetooth_gatt_get_services(mac_int)
        for svc in resp.services:
            print(f"[Service] {svc.uuid}")
            for ch in svc.characteristics:
                props = []
                if ch.properties & 0x02: props.append("READ")
                if ch.properties & 0x04: props.append("WRITE_NO_RSP")
                if ch.properties & 0x08: props.append("WRITE")
                if ch.properties & 0x10: props.append("NOTIFY")
                if ch.properties & 0x20: props.append("INDICATE")
                print(f"  ├─ {ch.uuid}  ({', '.join(props)})")
                if ch.properties & 0x02:
                    try:
                        val = await client.bluetooth_gatt_read(mac_int, ch.handle)
                        print(f"  │  └─ hex: {val.hex()}")
                        try:
                            txt = val.decode("utf-8").strip()
                            if txt.isprintable() and txt:
                                print(f"  │     txt: {txt}")
                        except Exception:
                            pass
                    except Exception as e:
                        print(f"  │  └─ read failed: {e}")
    finally:
        print("\nDisconnecting …")
        try:
            await client.bluetooth_device_disconnect(mac_int)
        except Exception:
            pass
        if cancel_connection:
            cancel_connection()
        await client.disconnect()


def main():
    if len(sys.argv) < 3 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        sys.exit(1 if len(sys.argv) < 3 else 0)
    noise_psk = sys.argv[3] if len(sys.argv) > 3 else None
    asyncio.run(probe(sys.argv[1], sys.argv[2].upper(), noise_psk))


if __name__ == "__main__":
    main()
