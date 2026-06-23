#!/usr/bin/env python3
"""
minimal-peripheral.py — minimal BlueZ GATT peripheral for Read-By-Type bug testing.

Registers the same 7-characteristic vendor service as mock-geberit-mera.py:
  - 1× READ  char with 16-bit UUID 0x3A2B  (item_len=7)
  - 4× NOTIFY chars with 128-bit vendor UUIDs (item_len=21)
  - 2× WRITE_WITHOUT_RESPONSE chars  (item_len=21)

The READ char method is named "button_state" so it sorts alphabetically first
(b < n < w) and gets the lowest handle — matching mock-geberit-mera.py's layout.
This is the layout that exercises the BlueZ gatt-server.c Read-By-Type patch.

Run on the BlueZ VM:
  sudo /home/jens/venv/bin/python tools/minimal-peripheral.py
"""

import asyncio
import logging

# Suppress TxPower property noise from BlueZ.
try:
    from bluez_peripheral.advert import Advertisement
    if hasattr(Advertisement, "get_properties"):
        _orig = Advertisement.get_properties
        def _patched(self):
            p = _orig(self)
            (p.get("org.bluez.LEAdvertisement1") or p).pop("TxPower", None)
            return p
        Advertisement.get_properties = _patched
except Exception:
    pass

from bluez_peripheral.gatt.service import Service, ServiceCollection
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.util import get_message_bus

# Matches mock-geberit-mera.py exactly.
SERVICE_UUID  = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
CHAR_3A2B     = "00003a2b-0000-1000-8000-00805f9b34fb"  # READ, 16-bit UUID → item_len=7
CHAR_A5       = "3334429d-90f3-4c41-a02d-5cb3a53e0000"
CHAR_A6       = "3334429d-90f3-4c41-a02d-5cb3a63e0000"
CHAR_A7       = "3334429d-90f3-4c41-a02d-5cb3a73e0000"
CHAR_A8       = "3334429d-90f3-4c41-a02d-5cb3a83e0000"
CHAR_A1       = "3334429d-90f3-4c41-a02d-5cb3a13e0000"
CHAR_A2       = "3334429d-90f3-4c41-a02d-5cb3a23e0000"


class VendorService(Service):
    def __init__(self):
        super().__init__(SERVICE_UUID, True)

    # "button_state" sorts before "notify_*" → gets handle 0x0016 (first char),
    # matching mock-geberit-mera.py's layout for the BlueZ RBT patch test.
    @characteristic(CHAR_3A2B, CharFlags.READ)
    def button_state(self, options):
        return b"ro"

    @characteristic(CHAR_A5, CharFlags.NOTIFY)
    def notify_a5(self, options):
        return b""

    @characteristic(CHAR_A6, CharFlags.NOTIFY)
    def notify_a6(self, options):
        return b""

    @characteristic(CHAR_A7, CharFlags.NOTIFY)
    def notify_a7(self, options):
        return b""

    @characteristic(CHAR_A8, CharFlags.NOTIFY)
    def notify_a8(self, options):
        return b""

    @characteristic(CHAR_A1, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_0(self, options):
        pass

    @write_0.setter
    def write_0(self, value, options):
        pass

    @characteristic(CHAR_A2, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_1(self, options):
        pass

    @write_1.setter
    def write_1(self, value, options):
        pass


async def main():
    bus = await get_message_bus()

    svc = VendorService()
    coll = ServiceCollection()
    coll.add_service(svc)
    await coll.register(bus)

    adv = Advertisement("AC250", [SERVICE_UUID], timeout=0, appearance=0)
    await adv.register(bus)

    print("--- Minimal Peripheral Active ---")
    print(f"Service: {SERVICE_UUID}")
    print("7 chars: button_state(3a2b/READ) + notify_a5-a8 + write_0/1")
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            await adv.unregister()
        except Exception:
            pass
        try:
            await coll.unregister()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
