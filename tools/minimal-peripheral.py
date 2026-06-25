#!/usr/bin/env python3
"""
minimal-peripheral.py — minimal BlueZ GATT peripheral to reproduce the
Read-By-Type mixed-length truncation bug in gatt-server.c.

Service layout:
  - 1× READ  characteristic with 16-bit UUID 0xABCD  → char-decl item_len=7
  - 4× NOTIFY characteristics with 128-bit UUIDs     → char-decl item_len=21
  - 2× WRITE_WITHOUT_RESPONSE characteristics         → char-decl item_len=21

BlueZ's process_read_by_type packs only same-length entries in one ATT
response.  When a central (iOS, macOS, Android) sends ATT_READ_BY_TYPE_REQ
for char declarations (type=0x2803), BlueZ returns only the 16-bit-UUID
entry in the first response (len=7) and stops — the 128-bit-UUID entries
are never sent.  Result: the central sees 1 characteristic instead of 7.

The READ characteristic is sorted alphabetically first (name "char_short"
comes before "notify_*") so it gets the lowest handle, ensuring it is the
first entry BlueZ encounters in the packing loop.

Run on the BlueZ host (requires bluez-peripheral):
  sudo python3 minimal-peripheral.py
"""

import asyncio
import subprocess

from bluez_peripheral.advert import Advertisement
from bluez_peripheral.gatt.service import Service, ServiceCollection
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.util import get_message_bus

# Generic test UUIDs — no vendor association.
SERVICE_UUID  = "12345678-90ab-cdef-0000-000000000001"
CHAR_SHORT    = "0000abcd-0000-1000-8000-00805f9b34fb"  # 16-bit UUID → item_len=7
CHAR_NOTIFY_1 = "12345678-90ab-cdef-0000-000000000002"
CHAR_NOTIFY_2 = "12345678-90ab-cdef-0000-000000000003"
CHAR_NOTIFY_3 = "12345678-90ab-cdef-0000-000000000004"
CHAR_NOTIFY_4 = "12345678-90ab-cdef-0000-000000000005"
CHAR_WRITE_1  = "12345678-90ab-cdef-0000-000000000006"
CHAR_WRITE_2  = "12345678-90ab-cdef-0000-000000000007"


class TestService(Service):
    def __init__(self):
        super().__init__(SERVICE_UUID, True)

    # "char_short" sorts before "notify_*" → assigned the lowest handle,
    # so it is the first entry BlueZ encounters in the Read-By-Type packing loop.
    @characteristic(CHAR_SHORT, CharFlags.READ)
    def char_short(self, options):
        return b"\x01"

    @characteristic(CHAR_NOTIFY_1, CharFlags.NOTIFY)
    def notify_1(self, options):
        return b""

    @characteristic(CHAR_NOTIFY_2, CharFlags.NOTIFY)
    def notify_2(self, options):
        return b""

    @characteristic(CHAR_NOTIFY_3, CharFlags.NOTIFY)
    def notify_3(self, options):
        return b""

    @characteristic(CHAR_NOTIFY_4, CharFlags.NOTIFY)
    def notify_4(self, options):
        return b""

    @characteristic(CHAR_WRITE_1, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_1(self, options):
        pass

    @write_1.setter
    def write_1(self, value, options):
        pass

    @characteristic(CHAR_WRITE_2, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_2(self, options):
        pass

    @write_2.setter
    def write_2(self, value, options):
        pass


async def main():
    subprocess.run(["btmgmt", "pairable", "off"], capture_output=True)

    bus = await get_message_bus()

    svc = TestService()
    coll = ServiceCollection()
    coll.add_service(svc)
    await coll.register(bus)

    adv = Advertisement("RBT-Test", [SERVICE_UUID], timeout=0, appearance=0)
    await adv.register(bus)

    print("--- Minimal Peripheral Active ---")
    print(f"Service: {SERVICE_UUID}")
    print("7 chars: char_short(0xABCD/READ) + notify_1-4 + write_1/2")
    print("Expected: central discovers all 7 characteristics")
    print("Bug:      central discovers only 1 (char_short) — BlueZ truncates the rest")
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
