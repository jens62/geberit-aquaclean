#!/usr/bin/env python3
"""
alba-ble20-probe.py — Geberit AquaClean Alba (Ble20) Application-Layer Probe
=============================================================================

Connects to an Alba device, completes the Arendi Security BLE handshake, then
runs DataPointInventory to discover every DpId the device supports.  Optionally
reads or writes specific data points by numeric DpId.

Does NOT require a running bridge.  Reads device address and ESP32 config from
config.ini when not supplied on the command line.

Usage
-----
  # Inventory only (prints all DpIds with type, range, behavior):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08

  # Read DpId 1008 (LID_LIFTER_POSITION):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --read 1008

  # Trigger lid (DpId 1009 = TRIGGER_LID_LIFTING, value 1):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --toggle-lid

  # Write arbitrary DpId with explicit value (4-byte LE uint32):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --write 1009 1

  # Via ESPHome proxy (kstr device):
  python tools/alba-ble20-probe.py \\
      --esphome-host 192.168.0.50 --device E4:85:01:CD:6B:04

  # Watch userIsSitting (DpId 60) — prints on every change:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:6B:04 --watch 60

  # Watch lid angle (DpId 1008) at 0.5s interval:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:6B:04 --watch 1008 --interval 0.5

  # Enable bridge-level DEBUG logging:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --debug
"""

import argparse
import asyncio
import configparser
import logging
import os
import struct
import sys
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Project root on path (works when installed via pip and when run from repo)
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ---------------------------------------------------------------------------
# Register TRACE/SILLY log levels used by bridge modules.
# Must happen before any bridge import.
# ---------------------------------------------------------------------------
def _add_level(name: str, value: int):
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

_add_level('SILLY', 4)
_add_level('TRACE', 5)

_FMT = '%(asctime)s %(name)s %(lineno)d %(levelname)s: %(message)s'
logging.basicConfig(level=logging.WARNING, format=_FMT)
log = logging.getLogger('alba-probe')
log.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Bridge imports
# ---------------------------------------------------------------------------
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.bluetooth_le.LE.dp_ids import dp_name
from aquaclean_console_app.bluetooth_le.LE.dp_type import DpType
from aquaclean_console_app.bluetooth_le.LE.dp_behavior import DpBehavior
from aquaclean_console_app.bluetooth_le.LE.Ble20Client import Ble20Client, encode_address, decode_address


def _dp_type_name(dt: int) -> str:
    try:
        return DpType(dt).name
    except ValueError:
        return f"0x{dt:02X}"


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def _format_value(raw: bytes, datatype: int) -> str:
    if not raw:
        return "(empty)"
    if datatype == DpType.Signed:
        if len(raw) >= 4:
            return str(struct.unpack_from('<i', raw)[0])
    elif datatype == DpType.OffOn:
        return "On" if raw[0] else "Off"
    elif datatype == DpType.OffOnAuto:
        return {0: "Off", 1: "On", 2: "Auto"}.get(raw[0], str(raw[0]))
    elif datatype == DpType.String:
        return raw.rstrip(b'\x00').decode('ascii', errors='replace')
    # Generic: show as uint32 / hex
    if len(raw) >= 4:
        u = struct.unpack_from('<I', raw)[0]
        s = struct.unpack_from('<i', raw)[0]
        return f"{u}  (signed: {s})  [0x{u:08X}]"
    if len(raw) == 2:
        return f"{struct.unpack_from('<H', raw)[0]}  [hex: {raw.hex()}]"
    if len(raw) == 1:
        return f"{raw[0]}  [0x{raw[0]:02X}]"
    return f"[{raw.hex()}]"


def _print_inventory(inv: dict[int, dict]):
    external = sorted((k, v) for k, v in inv.items() if not v['is_internal'])
    internal = sorted((k, v) for k, v in inv.items() if v['is_internal'])

    for section, section_entries in [("External", external), ("Internal", internal)]:
        if not section_entries:
            continue
        print(f"\n── {section} DpIds ({len(section_entries)}) " + "─" * 60)
        print(f"{'DpId':>6}  {'Inst':>4}  {'Type':<13}  {'Behavior':<14}  "
              f"{'Min':>10}  {'Max':>10}  {'Ver':>3}  Name")
        print("─" * 100)
        for dp_id, e in sorted(section_entries, key=lambda x: (x[1]['instance'] or 0, x[0])):
            inst  = f"{e['instance']}" if e['instance'] is not None else ""
            tp    = _dp_type_name(e['datatype'])
            try:
                beh = DpBehavior(e['behavior']).name
            except ValueError:
                beh = f"{e['behavior']}"
            min_v = e['min_s'] if e['datatype'] == DpType.Signed else e['min_u']
            max_v = e['max_s'] if e['datatype'] == DpType.Signed else e['max_u']
            name  = dp_name(dp_id)
            print(f"{dp_id:>6}  {inst:>4}  {tp:<13}  {beh:<14}  "
                  f"{min_v:>10}  {max_v:>10}  {e['version']:>3}  {name}")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _load_config(config_path: Optional[str] = None):
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    path = config_path or os.path.join(_repo_root, 'config.ini')
    cfg.read(path)
    host = cfg.get('ESPHOME', 'host', fallback='').strip() or None
    port = int(cfg.get('ESPHOME', 'port', fallback='6053').strip() or 6053)
    psk  = cfg.get('ESPHOME', 'noise_psk', fallback='').strip() or None
    dev  = cfg.get('BLE', 'device_id', fallback='').strip() or None
    return host, port, psk, dev


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------
async def run(args):
    cfg_host, cfg_port, cfg_psk, cfg_dev = _load_config(args.config)

    host   = args.esphome_host or cfg_host
    port   = args.esphome_port or cfg_port
    psk    = args.esphome_psk  or cfg_psk
    device = args.device       or cfg_dev

    if not device:
        log.error("No device address.  Provide --device or set [BLE] device_id in config.ini.")
        return 1

    print(f"\nAlba Ble20 Probe")
    print(f"  Device  : {device}")
    if host:
        print(f"  Via     : ESPHome proxy {host}:{port}")
    else:
        print(f"  Via     : local BLE (bleak)")
    print()

    connector = BluetoothLeConnector(
        esphome_host=host,
        esphome_port=port,
        esphome_noise_psk=psk,
    )
    session = Ble20Client(connector)

    try:
        print("Connecting + Arendi handshake...", flush=True)
        await connector.connect_async(device)

        if not connector.arendi_handshake_done:
            print(
                "ERROR: Arendi handshake did NOT complete.\n"
                "  This device is either not an Alba (Variant A) or the handshake failed.\n"
                "  Check that the device address is correct and that it is not already\n"
                "  connected to the Geberit Home app."
            )
            return 1

        print(f"Connected.  Arendi handshake done.  Device: {connector.device_address}")
        if connector.ble_dis_info:
            print(f"  BLE DIS: {connector.ble_dis_info}")
        print()

        # ── Always run inventory first (mandatory per protocol; gives us DpId types) ──
        print("Running DataPointInventory...", flush=True)
        inv = await session.inventory()
        print(f"  {len(inv)} DpIds returned.")

        _print_inventory(inv)

        # ── Optional: read one DpId ───────────────────────────────────────────
        if args.read is not None:
            dp_id = args.read
            entry = inv.get(dp_id)
            name  = dp_name(dp_id) or f"DpId {dp_id}"
            print(f"\nRead {name} (DpId {dp_id})...")
            raw = await session.read(dp_id)
            datatype = entry['datatype'] if entry else -1
            tp  = _dp_type_name(datatype)
            print(f"  Raw   : {raw.hex()}")
            print(f"  Type  : {tp}")
            print(f"  Value : {_format_value(raw, datatype)}")

        # ── Optional: write one DpId ─────────────────────────────────────────
        write_dp_id    = None
        write_value_b  = None

        if args.toggle_lid:
            write_dp_id   = 1009
            write_value_b = bytes([0x01])
        elif args.write:
            write_dp_id, write_int = args.write
            # Encode as 1 byte if fits, else 4-byte LE uint32
            if 0 <= write_int <= 255:
                write_value_b = bytes([write_int])
            else:
                write_value_b = struct.pack('<I', write_int)

        if write_dp_id is not None:
            entry = inv.get(write_dp_id)
            if entry is None:
                print(f"\nWARNING: DpId {write_dp_id} not found in inventory — "
                      f"device may not support it.  Attempting write anyway.")
            name = dp_name(write_dp_id) or f"DpId {write_dp_id}"
            print(f"\nWrite {name} (DpId {write_dp_id})  value={write_value_b.hex()}")
            if not args.yes:
                confirm = input("  Confirm? [y/N] ").strip().lower()
                if confirm != 'y':
                    print("  Cancelled.")
                    return 0
            await session.write(write_dp_id, write_value_b)
            print("  WriteAck received — OK.")

        # ── Optional: watch a DpId (poll until Ctrl+C) ───────────────────────
        if args.watch is not None:
            dp_id = args.watch
            entry  = inv.get(dp_id)
            name   = dp_name(dp_id) or f"DpId {dp_id}"
            dt     = entry['datatype'] if entry else -1
            tp     = _dp_type_name(dt)
            ivl    = args.interval
            print(f"\nWatching {name} (DpId {dp_id}, type={tp})  every {ivl}s — Ctrl+C to stop\n")
            last_raw = None
            try:
                while True:
                    raw = await session.read(dp_id)
                    if raw != last_raw:
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        print(f"  {ts}  {_format_value(raw, dt)}  [{raw.hex()}]")
                        last_raw = raw
                    await asyncio.sleep(ivl)
            except (KeyboardInterrupt, asyncio.CancelledError):
                print("\nWatch stopped.")

    except asyncio.TimeoutError:
        print("ERROR: Timeout waiting for device response.")
        return 1
    except IOError as e:
        print(f"ERROR: {e}")
        return 1
    finally:
        try:
            await connector.disconnect()
        except Exception:
            pass
        print("\nDisconnected.")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
class _WriteAction(argparse.Action):
    """Parse --write DPID VALUE as (int, int)."""
    def __call__(self, parser, namespace, values, option_string=None):
        try:
            dp_id = int(values[0])
            value = int(values[1])
            setattr(namespace, self.dest, (dp_id, value))
        except (ValueError, IndexError):
            parser.error(f"--write requires two integers: DPID VALUE")


def main():
    p = argparse.ArgumentParser(
        prog='alba-ble20-probe',
        description='Discover and interact with an Alba device via the Ble20 application protocol.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full inventory:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08

  # Read lid position:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --read 1008

  # Toggle lid (asks for confirmation):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --toggle-lid

  # Toggle lid without confirmation prompt:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --toggle-lid --yes

  # Write arbitrary DpId (value encoded as 1 byte if ≤ 255, else 4-byte LE uint32):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:B0:08 --write 1009 1

  # Watch userIsSitting (DpId 60 = AC_STATUS_USER_PRESENT):
  python tools/alba-ble20-probe.py --device E4:85:01:CD:6B:04 --watch 60

  # Watch lid angle every 0.5s:
  python tools/alba-ble20-probe.py --device E4:85:01:CD:6B:04 --watch 1008 --interval 0.5
""")
    p.add_argument('--device', metavar='MAC',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--read', type=int, metavar='DPID',
                   help='After inventory, read this DpId and print the value')
    p.add_argument('--write', nargs=2, metavar=('DPID', 'VALUE'), action=_WriteAction,
                   help='After inventory, write VALUE (int) to DPID')
    p.add_argument('--toggle-lid', action='store_true',
                   help='Shorthand for --write 1009 1 (TRIGGER_LID_LIFTING)')
    p.add_argument('--watch', type=int, metavar='DPID',
                   help='After inventory, poll DPID every --interval seconds and print on change (Ctrl+C to stop)')
    p.add_argument('--interval', type=float, default=1.0, metavar='SEC',
                   help='Poll interval for --watch (default: 1.0 s)')
    p.add_argument('--yes', '-y', action='store_true',
                   help='Skip write confirmation prompt')
    p.add_argument('--esphome-host', metavar='HOST',
                   help='ESPHome proxy hostname/IP (default: [ESPHOME] host in config.ini)')
    p.add_argument('--esphome-port', type=int, metavar='PORT',
                   help='ESPHome API port (default: 6053)')
    p.add_argument('--esphome-psk', metavar='PSK',
                   help='ESPHome noise PSK (default: [ESPHOME] noise_psk in config.ini)')
    p.add_argument('--config', metavar='PATH',
                   help='Path to config.ini (default: config.ini in repo root)')
    p.add_argument('--debug', action='store_true',
                   help='Enable DEBUG logging from bridge modules')
    p.add_argument('--trace', action='store_true',
                   help='Enable TRACE logging from bridge modules (very verbose)')

    args = p.parse_args()

    if args.trace or args.debug:
        level = logging.TRACE if args.trace else logging.DEBUG  # type: ignore[attr-defined]
        for name in ('aquaclean_console_app', 'aioesphomeapi'):
            logging.getLogger(name).setLevel(level)
        logging.getLogger().setLevel(level)
        for h in logging.getLogger().handlers:
            h.setLevel(level)

    sys.exit(asyncio.run(run(args)))


if __name__ == '__main__':
    main()
