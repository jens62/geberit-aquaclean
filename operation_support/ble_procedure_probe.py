#!/usr/bin/env python3
"""
BLE Procedure Probe — brute-force scan of API-layer procedure codes.

Connects once to the AquaClean device and sends an empty-payload request for
each procedure code in a configurable range.  Raw response bytes are printed
as hex so unknown procedures can be identified (e.g. GetStatisticsFilter).

Usage:
    python ble_procedure_probe.py [--start 0x40] [--end 0x60] [--config /path/to/config.ini]

Defaults:
    --start  0x40   (includes 0x45 = GetStatisticsDescale as a sanity check)
    --end    0x60
    --config aquaclean_console_app/config.ini

Output:
    0x45  OK   16 bytes  01 00 3d 00 0e 00 01 00 00 00 00 00 00 00 02 00  <- known: GetStatisticsDescale
    0x46  OK    4 bytes  ...
    0x47  ERR  timeout
    ...
"""

import asyncio
import argparse
import configparser
import os
import sys
import logging

from haggis import logs

# Make sure the package root is on sys.path when run directly
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ---------------------------------------------------------------------------
# Main probe loop
# ---------------------------------------------------------------------------
async def probe(start: int, end: int, delay: float, config: configparser.ConfigParser):
    from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute  import ApiCallAttribute
    from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient      import AquaCleanBaseClient, BLEPeripheralTimeoutError
    from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector            import BluetoothLeConnector

    class _ProbeCall:
        def __init__(self, procedure: int):
            self._attr = ApiCallAttribute(0x01, procedure, 0x01)
        def get_api_call_attribute(self):
            return self._attr
        def get_payload(self) -> bytearray:
            return bytearray()

    device_id    = config.get("BLE",     "device_id")
    esphome_host = config.get("ESPHOME", "host",      fallback=None) or None
    esphome_port = int(config.get("ESPHOME", "port",  fallback="6053"))
    esphome_psk  = config.get("ESPHOME", "noise_psk", fallback=None) or None

    connector = BluetoothLeConnector(esphome_host, esphome_port, esphome_psk)
    client    = AquaCleanBaseClient(connector)

    print(f"Connecting to {device_id} …")
    await client.connect_async(device_id)
    print("Connected.\n")

    known = {0x45: "GetStatisticsDescale", 0x51: "GetStoredCommonSetting",
             0x53: "GetStoredProfileSetting", 0x54: "SetStoredProfileSetting",
             0x56: "SetDeviceRegistrationLevel"}

    print(f"Probing procedure codes 0x{start:02X} – 0x{end:02X}\n")
    print(f"{'Code':<6}  {'Status':<5}  {'Bytes':>5}  Hex")
    print("-" * 72)

    for proc in range(start, end + 1):
        label = f"0x{proc:02X}"
        note  = f"  <- known: {known[proc]}" if proc in known else ""
        try:
            await client.send_request(_ProbeCall(proc))
            raw = client.message_context.result_bytes or bytearray()
            hex_str = raw.hex(" ") if raw else "(empty)"
            print(f"{label:<6}  OK     {len(raw):>5}  {hex_str}{note}")
        except BLEPeripheralTimeoutError:
            print(f"{label:<6}  ERR    {'':>5}  timeout{note}")
            # Device drops BLE after unknown procedures — wait, then reconnect
            if proc < end:
                print(f"         waiting {delay:.0f}s for device to recover …")
                await asyncio.sleep(delay)
                try:
                    await connector.disconnect()
                except Exception:
                    pass
                await client.connect_async(device_id)
        except Exception as e:
            print(f"{label:<6}  ERR    {'':>5}  {type(e).__name__}: {e}{note}")
            if proc < end:
                print(f"         waiting {delay:.0f}s for device to recover …")
                await asyncio.sleep(delay)
                try:
                    await connector.disconnect()
                except Exception:
                    pass
                await client.connect_async(device_id)

    print("\nDone. Disconnecting …")
    await connector.disconnect()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="BLE procedure code probe")
    parser.add_argument("--start",  default="0x40",
                        help="First procedure code to probe (hex or int, default 0x40)")
    parser.add_argument("--end",    default="0x60",
                        help="Last procedure code to probe (hex or int, default 0x60)")
    parser.add_argument("--config", default=os.path.join(_ROOT, "aquaclean_console_app", "config.ini"),
                        help="Path to config.ini")
    parser.add_argument("--delay",  default="30",
                        help="Seconds to wait after a timeout before reconnecting (default 30)")
    parser.add_argument("--log",    default="WARNING",
                        help="Log level (default WARNING — use DEBUG for full BLE trace)")
    args = parser.parse_args()

    config = configparser.ConfigParser(inline_comment_prefixes=('#', ';'))
    config.read(args.config)

    logs.add_logging_level('TRACE', logging.DEBUG - 5)
    logs.add_logging_level('SILLY', logging.DEBUG - 7)

    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.WARNING),
                        format="%(levelname)s %(name)s: %(message)s")

    asyncio.run(probe(int(args.start, 0), int(args.end, 0), float(args.delay), config))


if __name__ == "__main__":
    main()
