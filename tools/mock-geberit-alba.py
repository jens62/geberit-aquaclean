#!/usr/bin/env python3
"""
Mock BLE peripheral using bluez_peripheral on Linux (BlueZ).
- reliably reads adapter path/address via ObjectManager with introspection
- registers two services and an advertisement
- performs graceful cleanup on Ctrl-C (unregister advertisement/services, disconnect bus)
Requirements:
- bluez (Experimental=true may be required)
- python packages: dbus-next, bluez_peripheral
- run with sufficient privileges (or use sudo) to access system bus
"""

import argparse
import asyncio
import inspect
from dbus_next.aio import MessageBus
from dbus_next import BusType
from dbus_next import Variant
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.util import Adapter

# --- Services ----------------------------------------------------------------
class GeberitServiceA(Service):
    def __init__(self):
        super().__init__("559eb100-2390-11e8-b467-0ed5f89f718b", True)

    @characteristic("559eb101-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_char(self, options):
        pass

    @write_char.setter
    def write_char(self, value, options):
        print(f"Geberit A [Write]: {value.hex()}")

    @characteristic("559eb110-2390-11e8-b467-0ed5f89f718b", CharFlags.READ)
    async def read_char(self, options):
        return b"Geberit-Mock"


class BtSigDataService(Service):
    def __init__(self):
        super().__init__("0000fd48-0000-1000-8000-00805f9b34fb", True)

    @characteristic("559eb001-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE_WITHOUT_RESPONSE)
    def sig_write(self, options):
        pass

    @sig_write.setter
    def sig_write(self, value, options):
        print(f"Data Channel [Write]: {value.hex()}")

    @characteristic("559eb002-2390-11e8-b467-0ed5f89f718b", CharFlags.NOTIFY)
    async def sig_notify(self, options):
        print("Subscribed to Data Channel Notifications")
        return b"\x00"


# --- Helper functions --------------------------------------------------------
async def find_first_adapter_path_and_address(bus):
    """
    Use org.freedesktop.DBus.ObjectManager (with introspection) to find the first Adapter1 path and Address.
    Returns (path, address, objmgr) so the caller can reuse the ObjectManager interface for signal
    subscriptions (e.g. to detect incoming BLE connections).
    """
    # introspect root to get proper introspection.Node for get_proxy_object
    introspection = await bus.introspect('org.bluez', '/')
    proxy = bus.get_proxy_object('org.bluez', '/', introspection)
    objmgr = proxy.get_interface('org.freedesktop.DBus.ObjectManager')
    managed = await objmgr.call_get_managed_objects()

    # managed: dict { path: { interface: { prop: value, ... }, ... }, ... }
    for path, ifaces in managed.items():
        if 'org.bluez.Adapter1' in ifaces:
            adapter_props = ifaces['org.bluez.Adapter1']
            addr = adapter_props.get('Address')
            if isinstance(addr, Variant):
                addr = addr.value
            return path, addr, objmgr
    return None, None, objmgr


async def safe_call(obj, method_name, *args, **kwargs):
    """
    Call method if exists and is awaitable/callable. Return True if called, False otherwise.
    """
    fn = getattr(obj, method_name, None)
    if not fn:
        return False
    try:
        if inspect.iscoroutinefunction(fn):
            await fn(*args, **kwargs)
        else:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        return True
    except Exception as e:
        # swallow errors during cleanup but print for debugging
        print(f"Cleanup: calling {method_name} raised: {e}")
        return False


# --- Main --------------------------------------------------------------------
async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Try to get the bluez_peripheral Adapter wrapper (used for register calls)
    adapter_wrapper = await Adapter.get_first(bus)
    if adapter_wrapper:
        print("Adapter wrapper obtained from bluez_peripheral.")
    else:
        print("No Bluetooth adapter wrapper found via bluez_peripheral.Adapter.get_first()")

    # Use ObjectManager with introspection to find adapter path and address reliably
    objmgr = None
    try:
        adapter_path, adapter_address, objmgr = await find_first_adapter_path_and_address(bus)
        print("Adapter DBus path:", adapter_path)
        print("Adapter BLE address:", adapter_address)
    except Exception as e:
        print("Could not read adapter path/address via ObjectManager:", e)
        adapter_path = None
        adapter_address = None

    if not adapter_wrapper:
        print("No adapter wrapper available; cannot register GATT services/advertisement.")
        await bus.disconnect()
        return

    # Subscribe to BlueZ ObjectManager signals to detect incoming BLE connections.
    # BlueZ adds a Device1 object when a central connects to this peripheral.
    if objmgr is not None:
        def on_device_connected(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                addr = interfaces['org.bluez.Device1'].get('Address')
                if isinstance(addr, Variant):
                    addr = addr.value
                print(f"[Mock] BLE client connected:    {addr or path}")

        def on_device_disconnected(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                print(f"[Mock] BLE client disconnected: {path}")

        objmgr.on_interfaces_added(on_device_connected)
        objmgr.on_interfaces_removed(on_device_disconnected)

    # Instantiate services
    geb_service = GeberitServiceA()
    sig_service = BtSigDataService()

    # Register services under unique DBus paths
    try:
        await geb_service.register(bus, "/org/bluez/example/geberit", adapter_wrapper)
        await sig_service.register(bus, "/org/bluez/example/sigdata", adapter_wrapper)
    except Exception as e:
        print("Service registration failed:", e)
        # continue to attempt advertisement or cleanup

    # Advertisement: name first, list of service UUIDs second (positional)
    adv = Advertisement(
        "Geberit-Alba-Mock",
        [
            "559eb100-2390-11e8-b467-0ed5f89f718b",
            "0000fd48-0000-1000-8000-00805f9b34fb"
        ],
        appearance=0,
        timeout=0
    )

    adv_registered = False
    try:
        await adv.register(bus, adapter_wrapper)
        adv_registered = True
    except Exception as e:
        print("Advertisement registration failed:", e)

    print("--- Mock Device Active ---")
    print("Advertising as: Geberit-Alba-Mock")

    # Wait until cancelled; handle graceful shutdown on KeyboardInterrupt
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()  # will block until set or cancelled
    except asyncio.CancelledError:
        # normal cancellation path
        pass
    except KeyboardInterrupt:
        # if KeyboardInterrupt is raised inside asyncio.run, it may not reach here,
        # but keep for completeness
        pass
    finally:
        print("\nShutting down: cleaning up advertisement and services...")

        # Unregister advertisement if possible
        if adv_registered:
            # try common unregister signatures
            await safe_call(adv, "unregister", bus, adapter_wrapper)
            await safe_call(adv, "unregister", adapter_wrapper)
            await safe_call(adv, "unregister", bus)
            await safe_call(adv, "unregister")

        # Unregister services (Service.unregister() takes no arguments)
        await safe_call(geb_service, "unregister")
        await safe_call(sig_service, "unregister")

        # Disconnect bus (synchronous in dbus-next — do not await)
        try:
            result = bus.disconnect()
            if inspect.isawaitable(result):
                await result
        except Exception as e:
            print("Error disconnecting bus:", e)

        print("Cleanup complete. Exiting.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Mock Geberit AquaClean Alba BLE peripheral (Linux/BlueZ).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
How to test the HACS 'unsupported device' detection
----------------------------------------------------
Prerequisites (free the ESP32 BLE subscription slot):
  1. In HA: Settings → Integrations → aquaclean-proxy → Disable
     (this is the main slot thief; must be disabled while testing)
  2. In HA: Settings → Integrations → Geberit AquaClean → Configure
     → set Poll Interval to 300 s and leave all other fields unchanged
     (widens the gap between polls so the test slot is usually free)

Test sequence:
  3. Start this mock (run as root or with BlueZ privileges):
       sudo python mock-geberit-alba.py
     Wait for '--- Mock Device Active ---' before continuing.
  4. Immediately in HA: Settings → Integrations
     → 'Eintrag hinzufügen' (German) / 'Add entry' (English)  ← bottom-right
     → search 'Geberit AquaClean' → select it
     → BLE MAC: <adapter MAC printed above, e.g. 88:A2:9E:2C:EA:F7>
     → ESPHome host: <ESP32 IP, e.g. 192.168.0.114>
     → Port: 6053 → Submit

     Do NOT use the Configure button on the existing integration —
     that reconfigures the real device. 'Add entry' adds a second instance.

Expected result:
  - HA shows the 'unsupported device' abort screen with the GATT UUIDs
  - This terminal prints:
      [Mock] BLE client connected:    <ESP32 BLE MAC>
      [Mock] BLE client disconnected: /org/bluez/hci0/dev_...
  - If nothing is printed, the mock was not found during the 10 s scan
    → restart the mock and submit the HA form immediately after step 3
""",
    )
    parser.parse_args()  # handles --help; no other arguments accepted

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # ensure clean exit if asyncio.run raises KeyboardInterrupt
        print("\nInterrupted by user. Exiting.")
