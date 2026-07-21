#!/usr/bin/env python3
"""
minimal-peripheral.py — minimal BlueZ GATT peripheral for multi-service discovery testing.

Registers N separate primary services (default 6), each using the same
Bluetooth-Base-UUID-pattern form ("0000XXXX-0000-1000-8000-00805F9B34FB") that
aquaclean_ble_relay/mera_mock.py's RC-pairing services use — this is the exact
pattern under investigation for a suspected BlueZ gatt-database.c bug where
serving ~6 externally-registered custom services produces garbled UUIDs and an
incorrect open-ended (0xffff) end handle on the last one, hiding services
registered afterward from discovery. See local-assets/bluez-multi-service-
question.md and local-assets/bluez-multi-service-question-chatGPT-answer.md
for the full investigation and the experiment matrix this script runs.

Variables under test (see --help):
  --num-services N   how many services to register (bisect 1..6 to find the
                      exact threshold where discovery breaks)
  --num-apps M        how many separate GattManager1.RegisterApplication calls
                      to spread the N services across (default: 1 — all N
                      services in one ServiceCollection/app, confirmed clean
                      at N=4,5,6). Distribution mirrors mera_mock.py's real
                      topology: the first (M-1) apps get exactly 1 service
                      each (registered directly via Service.register(), one
                      app path each), and the LAST app gets all remaining
                      services bundled in one ServiceCollection. E.g.
                      --num-services 6 --num-apps 4 reproduces the real mock's
                      exact broken topology: 3 single-service apps + 1 app
                      holding 3 services.
  --chars-per-service K   characteristics per service, held constant while N
                      varies (isolates service-count from characteristic-count)
  --empty             overrides --chars-per-service to 0 (services with no
                      characteristics at all) — isolates whether corruption
                      depends on characteristic/descriptor count vs pure
                      service count
  --reverse           registers services in reverse UUID order — distinguishes
                      "whichever service ends up last gets corrupted" (handle-
                      assignment-order bug) from "this specific service object
                      always gets corrupted" (object-identity bug)

Advertises exactly ONE service UUID (the first registered service) alongside the
name — see minimal-central.py's --advertised-uuid, the reliable discovery method
on macOS/CoreBluetooth (plain name-based scanning there is unreliable for a
peripheral the scanning host has never connected to before).

Run on the BlueZ host (requires bluez-peripheral):
  sudo python3 minimal-peripheral.py --num-services 4
  sudo python3 minimal-peripheral.py --num-services 6 --empty
  sudo python3 minimal-peripheral.py --num-services 6 --reverse
  sudo python3 minimal-peripheral.py --num-services 6 --num-apps 4   # reproduce real mock's topology
"""

import argparse
import asyncio
import subprocess

_SCRIPT_VERSION = "1.3.0"

from bluez_peripheral.advert import Advertisement
from bluez_peripheral.gatt.service import Service, ServiceCollection
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.util import get_message_bus

# Base alias range — service i uses UUID 0000{BASE_ALIAS + i:04x}-0000-1000-8000-00805f9b34fb.
# Matches the real mock's pattern (e.g. 0x8A30, 0xE0DB, 0xC526) closely enough to exercise the
# same BlueZ code path without colliding with any real SIG-registered 16-bit UUID.
BASE_ALIAS = 0x1000


def _service_uuid(i: int) -> str:
    return f"0000{BASE_ALIAS + i:04x}-0000-1000-8000-00805f9b34fb"


def _char_uuid(i: int, j: int) -> str:
    # Distinct 128-bit UUID per characteristic, tagged with (service index, char index).
    return f"12345678-90ab-cdef-{i:04x}-{j:012x}"


def _make_service_class(index: int, num_chars: int) -> type:
    """Build a Service subclass for service `index` with `num_chars` characteristics
    (1 READ + up to (num_chars-1) NOTIFY, matching the real mock's read/notify mix)."""
    uuid = _service_uuid(index)
    attrs = {}

    def __init__(self):
        Service.__init__(self, uuid, True)

    attrs["__init__"] = __init__

    for j in range(num_chars):
        char_uuid = _char_uuid(index, j)
        flag = CharFlags.READ if j == 0 else CharFlags.NOTIFY

        def make_getter(flag=flag):
            def getter(self, options):
                return b"\x01" if flag == CharFlags.READ else b""
            return getter

        attrs[f"char_{j}"] = characteristic(char_uuid, flag)(make_getter())

    return type(f"TestService{index}", (Service,), attrs)


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--num-services", type=int, default=6, help="number of services to register (default: 6)")
    ap.add_argument("--num-apps", type=int, default=1,
                    help="separate GattManager1.RegisterApplication calls to spread --num-services across "
                         "(default: 1 — all services in one app/ServiceCollection). The first (N-1) apps "
                         "get 1 service each; the last app gets the remainder — matching mera_mock.py's "
                         "real mera/battery/dis/rc topology (3 single-service apps + 1 app with 3 services).")
    ap.add_argument("--chars-per-service", type=int, default=2,
                    help="characteristics per service, held constant while --num-services varies (default: 2)")
    ap.add_argument("--empty", action="store_true", help="services have 0 characteristics (overrides --chars-per-service)")
    ap.add_argument("--reverse", action="store_true", help="register services in reverse UUID order")
    args = ap.parse_args()

    if not (1 <= args.num_apps <= args.num_services):
        ap.error(f"--num-apps must be between 1 and --num-services ({args.num_services})")

    num_chars = 0 if args.empty else args.chars_per_service
    indices = list(range(1, args.num_services + 1))
    if args.reverse:
        indices = list(reversed(indices))

    subprocess.run(["btmgmt", "pairable", "off"], capture_output=True)

    bus = await get_message_bus()

    services = []
    for i in indices:
        cls = _make_service_class(i, num_chars)
        services.append(cls())

    # Split into --num-apps groups: the first (num_apps - 1) apps get exactly 1
    # service each; the last app gets whatever remains. Mirrors mera_mock.py's
    # real topology (mera/battery/dis = 3 single-service apps, rc = 1 app with
    # 3 services bundled in a ServiceCollection) exactly when
    # num_services=6, num_apps=4.
    groups = [[s] for s in services[: args.num_apps - 1]]
    groups.append(services[args.num_apps - 1 :])

    apps = []
    for app_idx, group in enumerate(groups):
        coll = ServiceCollection(group)
        path = f"/org/bluez/example/minimal_app{app_idx}"
        await coll.register(bus, path=path)
        apps.append(coll)

    # Advertise exactly ONE service UUID (the first registered service), not all N —
    # listing all N 128-bit UUIDs (16 bytes each) blows past the legacy ADV_IND payload
    # limit (31 bytes total). One UUID + the name fits: bluez_peripheral/BlueZ splits
    # Name into SCAN_RSP automatically when ADV_IND is full (confirmed in mera_mock.py).
    # A service UUID (not just the name) matters for discovery too: on macOS/CoreBluetooth,
    # bleak's plain name-based BleakScanner.discover() is unreliable for a peripheral the
    # host has never connected to before (Apple's privacy model reliably exposes advertised
    # names mainly when scanning is filtered by service UUID) — confirmed 2026-07-21, a
    # peripheral visible in nRF Connect (Android) and general --diagnostics scanning was
    # still not found by name from a Mac that had never connected to it before.
    adv = Advertisement("MultiSvc-Test", [_service_uuid(indices[0])], timeout=0, appearance=0)
    await adv.register(bus)

    print(f"--- Minimal Multi-Service Peripheral Active (v{_SCRIPT_VERSION}) ---")
    print(f"num_services={args.num_services}  num_apps={args.num_apps}  chars_per_service={num_chars}  reverse={args.reverse}")
    print(f"Advertised service UUID (for --advertised-uuid on minimal-central.py): {_service_uuid(indices[0])}")
    print(f"Registered {len(groups)} app(s) (in registration order):")
    for app_idx, group in enumerate(groups):
        group_indices = indices[sum(len(g) for g in groups[:app_idx]): sum(len(g) for g in groups[: app_idx + 1])]
        print(f"  app{app_idx} (/org/bluez/example/minimal_app{app_idx}): {len(group)} service(s)")
        for i in group_indices:
            print(f"    {_service_uuid(i)}  ({num_chars} characteristics)")
    print()
    print(f"Expected on the central side: {args.num_services} services found, "
          f"each with UUID 0000{{{BASE_ALIAS:04x}+i}}-...-00805f9b34fb and {num_chars} characteristics, "
          f"none with a garbled UUID or an open-ended (0xffff) handle range.")
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
        for app in apps:
            try:
                await app.unregister()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
