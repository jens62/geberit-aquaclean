#!/usr/bin/env python3
"""
active-common-settings-probe.py — probe proc 0x0A / 0x0B (Active Common Settings)
====================================================================================

Reads ALL CommonSetting IDs 0–12 via both:
  - proc 0x51 (GetStoredCommonSetting) — value persisted in NVM, needs power cycle to apply
  - proc 0x0A (GetActiveCommonSetting) — value currently live in the device

Then optionally writes a single setting via proc 0x0B (SetActiveCommonSetting) and reads
it back to confirm it applied immediately — answering the "immediate vs power cycle" question.

Background
----------
Proc 0x0A/0x0B confirmed from AcDataPointDefinitionFactory.cs (Geberit Home v2.14.1):
  RpcNumberGet = 10 (0x0A)  →  GetActiveCommonSetting
  RpcNumberSet = 11 (0x0B)  →  SetActiveCommonSetting
Both supported on AcMeraComfort per factory device-type filter.

Key finding: SetActiveCommonSetting (0x0B) applies immediately — the iPhone uses it at
every session init to restore orientation light settings without requiring a power cycle.

Usage
-----
  # Read all CommonSettings active vs stored (read-only):
  python tools/active-common-settings-probe.py

  # Write orientation light mode = Off (ID=3, value=0) and read back:
  python tools/active-common-settings-probe.py --write 3 0

  # Write orientation light mode = On (ID=3, value=1):
  python tools/active-common-settings-probe.py --write 3 1

  # Restore to WhenApproached (ID=3, value=2):
  python tools/active-common-settings-probe.py --write 3 2

  # Write colour to Blue (ID=2, value=0):
  python tools/active-common-settings-probe.py --write 2 0

  # Override device / ESPHome:
  python tools/active-common-settings-probe.py --device AA:BB:CC:DD:EE:FF
  python tools/active-common-settings-probe.py --esphome-host 192.168.0.50

CommonSetting ID reference (confirmed)
---------------------------------------
  ID  Name                       Range  Notes
   0  WaterHardness              0–?
   1  OrientationLightBrightness 0–4
   2  OrientationLightColour     0–6    0=Blue 1=Turquoise 2=Magenta 3=Orange 4=Yellow 5=WarmWhite 6=ColdWhite
   3  OrientationLightMode       0–2    0=Off 1=On 2=WhenApproached  ← control orientation light
   4  LidSensorRange             0–4    Mera Comfort
   5  OdourExtractionRunOn       0–?
   6  LidAutoOpen                0–1    Mera Comfort
   7  LidAutoClose               0–1    Mera Comfort
   8  AutoFlush                  0–1
   9  DemoMode                   0–1
  10  LightSensorSensitivity     AcSela only
  11  CareMode                   Mera Floorstanding only
  12  Language                   0–?
"""

import argparse
import asyncio
import configparser
import logging
import os
import struct
import sys
from typing import Optional

_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)


def _add_level(name: str, value: int):
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

_add_level('SILLY', 4)
_add_level('TRACE', 5)

logging.basicConfig(level=logging.WARNING)
log = logging.getLogger('active-common-settings-probe')
log.setLevel(logging.DEBUG)

from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import AquaCleanBaseClient
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute
# (AquaCleanClientFactory is NOT used — base client created directly like geberit-ble-probe.py)

# ---------------------------------------------------------------------------
# Setting metadata
# ---------------------------------------------------------------------------
SETTING_NAMES = {
    0:  "WaterHardness",
    1:  "OrientationLightBrightness",
    2:  "OrientationLightColour",
    3:  "OrientationLightMode",
    4:  "LidSensorRange",
    5:  "OdourExtractionRunOn",
    6:  "LidAutoOpen",
    7:  "LidAutoClose",
    8:  "AutoFlush",
    9:  "DemoMode",
    10: "LightSensorSensitivity",
    11: "CareMode",
    12: "Language",
}

SETTING_VALUES = {
    2: {0: "Blue", 1: "Turquoise", 2: "Magenta", 3: "Orange", 4: "Yellow", 5: "WarmWhite", 6: "ColdWhite"},
    3: {0: "Off", 1: "On", 2: "WhenApproached"},
}


# ---------------------------------------------------------------------------
# Minimal CallClass wrapper
# ---------------------------------------------------------------------------
class _AdHoc:
    def __init__(self, ctx: int, proc: int, args: bytes):
        self._attr = ApiCallAttribute(ctx, proc, 0x01)
        self._args = args

    def get_api_call_attribute(self):
        return self._attr

    def get_payload(self) -> bytes:
        return self._args

    def result(self, data: bytearray) -> bytes:
        return bytes(data)


async def _call(client: AquaCleanBaseClient, ctx: int, proc: int, args: bytes) -> Optional[bytes]:
    call = _AdHoc(ctx, proc, args)
    try:
        await client.send_request(call)
        return call.result(client.message_context.result_bytes)
    except Exception:
        return None


def _decode(raw: Optional[bytes]) -> Optional[int]:
    if raw and len(raw) >= 2:
        return struct.unpack_from('<H', raw)[0]
    return None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_config(config_path: Optional[str] = None):
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    path = config_path or os.path.join(_repo_root, 'config.ini')
    cfg.read(path)
    host   = cfg.get('ESPHOME', 'host',      fallback='').strip() or None
    port   = int(cfg.get('ESPHOME', 'port',  fallback='6053') or 6053)
    psk    = cfg.get('ESPHOME', 'noise_psk', fallback='').strip() or None
    device = cfg.get('BLE',     'device_id', fallback='').strip() or None
    return host, port, psk, device


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def run(args):
    cfg_host, cfg_port, cfg_psk, cfg_dev = _load_config(args.config)
    host   = args.esphome_host or cfg_host
    port   = getattr(args, 'esphome_port', None) or cfg_port
    psk    = getattr(args, 'esphome_psk',  None) or cfg_psk
    device = args.device or cfg_dev

    if not device:
        print("ERROR: No device address. Provide --device or set [BLE] device_id in config.ini.")
        return 1

    print(f"\nActive Common Settings Probe")
    print(f"  Device  : {device}")
    print(f"  Via     : {f'{host}:{port}' if host else 'local BLE (bleak)'}")
    if args.write is not None:
        print(f"  Write   : ID={args.write[0]}  value={args.write[1]}")
    print()

    connector = BluetoothLeConnector(
        esphome_host=host,
        esphome_port=port,
        esphome_noise_psk=psk,
    )
    client = AquaCleanBaseClient(connector)

    try:
        await client.connect_async(device)
        await client.subscribe_notifications_async()

        print(f"  {'ID':<3}  {'Name':<35}  {'Stored (0x51)':<15}  {'Active (0x0A)':<15}  Match?")
        print(f"  {'-'*3}  {'-'*35}  {'-'*15}  {'-'*15}  {'-'*6}")

        for setting_id in range(13):
            name = SETTING_NAMES.get(setting_id, f"ID{setting_id}")
            arg  = bytes([setting_id])

            stored_raw = await _call(client, 0x01, 0x51, arg)
            active_raw = await _call(client, 0x01, 0x0A, arg)

            stored = _decode(stored_raw)
            active = _decode(active_raw)

            def _fmt(val, raw):
                if val is None:
                    return "(error)" if raw is None else "(empty)"
                label = SETTING_VALUES.get(setting_id, {}).get(val, "")
                return f"{val}  {label}" if label else str(val)

            match = "✓" if stored == active else "≠"
            print(f"  {setting_id:<3}  {name:<35}  {_fmt(stored, stored_raw):<15}  {_fmt(active, active_raw):<15}  {match}")

        # Write test
        if args.write is not None:
            write_id, write_val = args.write
            print()
            name    = SETTING_NAMES.get(write_id, f"ID{write_id}")
            val_str = SETTING_VALUES.get(write_id, {}).get(write_val, str(write_val))
            print(f"Writing: proc 0x0B  ID={write_id} ({name})  value={write_val} ({val_str}) ...")

            write_args = bytes([write_id, write_val & 0xFF, (write_val >> 8) & 0xFF])
            result = await _call(client, 0x01, 0x0B, write_args)
            print(f"  Write response: {result.hex() if result else '(none)'}")

            # Read back immediately
            await asyncio.sleep(0.2)
            readback_raw = await _call(client, 0x01, 0x0A, bytes([write_id]))
            readback = _decode(readback_raw)
            readback_str = SETTING_VALUES.get(write_id, {}).get(readback, str(readback)) if readback is not None else "error"

            if readback == write_val:
                print(f"  ✅  Read-back confirmed: active value = {readback} ({readback_str})")
                print(f"  → Proc 0x0B applies IMMEDIATELY (no power cycle needed).")
            else:
                print(f"  ❌  Read-back mismatch: active value = {readback} ({readback_str}), expected {write_val}")
                print(f"  → Check device response; may require power cycle.")

    finally:
        try:
            await client.disconnect_async()
        except Exception:
            pass

    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Probe proc 0x0A/0x0B (Active Common Settings) on Geberit AquaClean.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument('--device',       metavar='MAC',  help='BLE MAC (default: config.ini [BLE] device_id)')
    ap.add_argument('--esphome-host', metavar='HOST', help='ESPHome proxy hostname/IP')
    ap.add_argument('--config',       metavar='PATH', help='Path to config.ini')
    ap.add_argument('--write', nargs=2, type=int, metavar=('ID', 'VALUE'),
                    help='Write active CommonSetting: --write 3 0 = OrientationLight Off')
    args = ap.parse_args()
    sys.exit(asyncio.run(run(args)) or 0)


if __name__ == '__main__':
    main()
