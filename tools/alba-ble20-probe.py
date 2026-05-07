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
# Bridge import
# ---------------------------------------------------------------------------
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector

# ---------------------------------------------------------------------------
# Ble20 protocol constants
# ---------------------------------------------------------------------------
CMD_INVENTORY       = 0x00
CMD_INVENTORY_COUNT = 0x01
CMD_INVENTORY_DATA  = 0x02
CMD_READ            = 0x10
CMD_READ_ANS        = 0x11
CMD_READ_ERROR      = 0x12
CMD_WRITE           = 0x20
CMD_WRITE_ACK       = 0x21
CMD_WRITE_ERROR     = 0x22
CMD_CAPABILITIES    = 0xFD
CMD_CAPABILITIES_ACK= 0xFE
CMD_DEVICE_STATUS   = 0xE0

# DataPointType enum (index = wire byte value, from DataPointType.cs)
DP_TYPES = {
    0:  "Unused",        1:  "Binary",       2:  "MilliSeconds",
    3:  "Seconds",       4:  "Minutes",      5:  "Hours",
    6:  "Permill",       7:  "Percent",      8:  "String",
    9:  "Counter",       10: "Enum",         11: "OffOn",
    12: "OffOnAuto",     13: "TimeStampUtc", 14: "TimeStampLocal",
    15: "Signed",
}

# DpBehavior enum (index = wire value, from DpBehavior.cs)
DP_BEHAVIORS = {
    0: "Info", 1: "Status", 2: "Command",
    3: "Nvm",  4: "Protected", 5: "CommandLocked",
}

# TransmissionStatus codes
TX_STATUS = {
    0x00: "Ok",           0x01: "InvalidId",       0x02: "InvalidInstance",
    0x03: "OutOfRange",   0x04: "InvalidStorage",  0x05: "Locked",
    0x06: "NotNotifiable",0x07: "OptionNotSupported",0x08: "InvalidLength",
    0x09: "InvalidType",  0x0A: "InvalidBehavior", 0x0B: "AlreadyInUse",
}

# Known DpId names (subset from DpId.cs; inventory shows the rest as unnamed)
DP_NAMES = {
    # Orientation light (Alba Ble20 — direct DpId control, no power-cycle needed)
    42:   "ORIENTATION_LIGHT_LED",              # Status, Percent 0-100, notifiable
    43:   "ORIENTATION_LIGHT_SET_LED",          # Command, Percent 0-100 (only in override mode)
    44:   "ORIENTATION_LIGHT_MODE",             # Nvm, OffOnAuto: 0=Off 1=On 2=Auto
    45:   "ORIENTATION_LIGHT_SENSOR_DEPENDENT", # Nvm, OffOn: auto mode uses proximity sensor
    46:   "ORIENTATION_LIGHT_AMBIENT_DEPENDENT",# Nvm, OffOn: auto mode uses ambient light sensor
    47:   "ORIENTATION_LIGHT_LED_OVERRIDE",     # Command, OffOn: 1=bypass firmware, enable SET_LED
    48:   "ORIENTATION_LIGHT_INTENSITY",        # Nvm, Enum 0-4 (brightness step)
    50:   "ORIENTATION_LIGHT_FOLLOW_UP_TIME",   # Nvm, Enum 0-4 (follow-up duration step)
    51:   "ORIENTATION_LIGHT_SENSOR_DIST",      # Nvm, Enum 0-4 (proximity trigger distance)
    53:   "ORIENTATION_LIGHT_SENSOR_SENS",      # Nvm, Enum 0-4 (proximity sensitivity)
    55:   "ORIENTATION_LIGHT_SENSOR_MOVE",      # Status, OffOn, notifiable — movement detected
    56:   "ORIENTATION_LIGHT_AMBIENT_SENS",     # Nvm, Enum 0-4 (ambient light threshold)
    58:   "ORIENTATION_LIGHT_AMBIENT_DARK",     # Status, OffOn, notifiable — ambient is dark
    66:   "ORIENTATION_LIGHT_FOLLOW_UP_TIME_STEPS", # Nvm, internal steps table
    112:  "BLOCK_FLUSH",
    118:  "PRE_FLUSH",
    119:  "POST_FLUSH",
    563:  "START_STOP_ANAL_SHOWER",
    564:  "ANAL_SHOWER_STATUS",
    565:  "ANAL_SHOWER_PROGRESS",
    570:  "SET_ACTIVE_ANAL_SPRAY_INTENSITY",
    572:  "SET_ACTIVE_ANAL_SPRAY_ARM_POSITION",
    580:  "STORED_ANAL_SPRAY_INTENSITY",
    581:  "STORED_ANAL_SPRAY_ARM_POSITION",
    849:  "SET_ACTIVE_ANAL_SHOWER_TIME",
    852:  "SET_ACTIVE_ANAL_SHOWER_WATER_TEMP",
    868:  "START_STOP_LADY_SHOWER",
    872:  "LADY_SHOWER_STATUS",
    873:  "LADY_SHOWER_PROGRESS",
    60:   "USER_PRESENT",                       # AC_STATUS_USER_PRESENT (65596 truncates to 60)
    990:  "ACTIVE_LID_LIFTING_OPENING_BEHAVIOR",
    992:  "STORED_LID_LIFTING_OPENING_BEHAVIOR",
    996:  "ACTIVE_LID_LIFTING_CLOSING_DELAY",
    999:  "ACTIVE_LID_LIFTING_CLOSING_BEHAVIOR",
    1002: "ACTIVE_LID_LIFTING_DETECTION_RANGE",
    1008: "LID_LIFTER_POSITION",
    1009: "TRIGGER_LID_LIFTING",           # ← ToggleLid equivalent
}

# ---------------------------------------------------------------------------
# Address helpers
# ---------------------------------------------------------------------------
def _encode_address(dp_id: int, instance: Optional[int] = None) -> bytes:
    lo = dp_id & 0xFF
    hi = (dp_id >> 8) & 0x7F          # strip bit 15 (used for instance flag)
    if instance is not None:
        return bytes([lo, hi | 0x80, instance])
    return bytes([lo, hi])


def _decode_address(data: bytes, offset: int = 1):
    """Parse wire address at offset.  Returns (dp_id, instance_or_None, next_offset)."""
    lo = data[offset]
    hi = data[offset + 1]
    has_instance = bool(hi & 0x80)
    dp_id = ((hi & 0x7F) << 8) | lo
    if has_instance:
        return dp_id, data[offset + 2], offset + 3
    return dp_id, None, offset + 2


# ---------------------------------------------------------------------------
# Ble20Session — async send/receive over the Arendi-encrypted channel
# ---------------------------------------------------------------------------
class Ble20Session:
    RECV_TIMEOUT = 15.0

    def __init__(self, connector: BluetoothLeConnector):
        self.connector = connector
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        connector.data_received_handlers += self._on_data

    async def _on_data(self, data: bytes):
        await self._queue.put(bytes(data))

    async def _recv(self, timeout: Optional[float] = None) -> bytes:
        return await asyncio.wait_for(
            self._queue.get(),
            timeout=timeout or self.RECV_TIMEOUT,
        )

    async def send(self, data: bytes):
        log.debug(f"→ {data.hex()}")
        await self.connector.send_message(data)

    # ── Inventory ────────────────────────────────────────────────────────────

    async def inventory(self) -> list[dict]:
        """Run DataPointInventory.  Returns list of entry dicts."""
        await self.send(bytes([CMD_INVENTORY, 0x00]))

        # Collect InventoryCount, discarding any unsolicited frames that arrive first
        while True:
            frame = await self._recv()
            log.debug(f"← {frame.hex()}")
            if frame[0] == CMD_INVENTORY_COUNT:
                break
            log.debug(f"  (skipping pre-inventory frame cmd=0x{frame[0]:02X})")

        count = struct.unpack_from('<H', frame, 1)[0]
        log.debug(f"Inventory count: {count}")

        entries = []
        received = 0
        while received < count:
            frame = await self._recv()
            log.debug(f"← {frame.hex()}")
            if frame[0] != CMD_INVENTORY_DATA:
                log.debug(f"  (skipping non-inventory frame cmd=0x{frame[0]:02X})")
                continue
            dp_id, instance, payload_off = _decode_address(frame)
            payload = frame[payload_off:]
            if len(payload) < 11:
                log.warning(f"Short InventoryData payload for DpId {dp_id}: {payload.hex()}")
                received += 1
                continue
            version  = payload[0]
            datatype = payload[1]
            min_s = struct.unpack_from('<i', payload, 2)[0]
            max_s = struct.unpack_from('<i', payload, 6)[0]
            min_u = struct.unpack_from('<I', payload, 2)[0]
            max_u = struct.unpack_from('<I', payload, 6)[0]
            flags = payload[10]
            entries.append({
                'dp_id':       dp_id,
                'instance':    instance,
                'version':     version,
                'datatype':    datatype,
                'min_s':       min_s,
                'max_s':       max_s,
                'min_u':       min_u,
                'max_u':       max_u,
                'is_internal': bool(flags & 0x80),
                'behavior':    flags & 0x7F,
            })
            received += 1

        return entries

    # ── Read ─────────────────────────────────────────────────────────────────

    async def read(self, dp_id: int, instance: Optional[int] = None) -> bytes:
        addr = _encode_address(dp_id, instance)
        await self.send(bytes([CMD_READ]) + addr)
        while True:
            frame = await self._recv()
            log.debug(f"← {frame.hex()}")
            if frame[0] in (CMD_READ_ANS, CMD_READ_ERROR):
                break
            log.debug(f"  (skipping frame cmd=0x{frame[0]:02X} while waiting for read response)")
        if frame[0] == CMD_READ_ERROR:
            _, _, payload_off = _decode_address(frame)
            status = frame[payload_off] if payload_off < len(frame) else 0xFF
            raise IOError(
                f"ReadError dp_id={dp_id}: {TX_STATUS.get(status, f'0x{status:02X}')}"
            )
        _, _, payload_off = _decode_address(frame)
        return frame[payload_off:]

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, dp_id: int, value: bytes, instance: Optional[int] = None):
        addr = _encode_address(dp_id, instance)
        await self.send(bytes([CMD_WRITE]) + addr + value)
        while True:
            frame = await self._recv()
            log.debug(f"← {frame.hex()}")
            if frame[0] in (CMD_WRITE_ACK, CMD_WRITE_ERROR):
                break
            log.debug(f"  (skipping frame cmd=0x{frame[0]:02X} while waiting for write response)")
        if frame[0] == CMD_WRITE_ERROR:
            _, _, payload_off = _decode_address(frame)
            status = frame[payload_off] if payload_off < len(frame) else 0xFF
            raise IOError(
                f"WriteError dp_id={dp_id}: {TX_STATUS.get(status, f'0x{status:02X}')}"
            )


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------
def _format_value(raw: bytes, datatype: int) -> str:
    if not raw:
        return "(empty)"
    if datatype == 15:          # Signed — int32
        if len(raw) >= 4:
            return str(struct.unpack_from('<i', raw)[0])
    elif datatype == 11:        # OffOn
        return "On" if raw[0] else "Off"
    elif datatype == 12:        # OffOnAuto
        return {0: "Off", 1: "On", 2: "Auto"}.get(raw[0], str(raw[0]))
    elif datatype == 8:         # String
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


def _print_inventory(entries: list[dict]):
    # Separate external and internal for clarity
    external = [e for e in entries if not e['is_internal']]
    internal = [e for e in entries if e['is_internal']]

    for section, section_entries in [("External", external), ("Internal", internal)]:
        if not section_entries:
            continue
        print(f"\n── {section} DpIds ({len(section_entries)}) " + "─" * 60)
        print(f"{'DpId':>6}  {'Inst':>4}  {'Type':<13}  {'Behavior':<14}  "
              f"{'Min':>10}  {'Max':>10}  {'Ver':>3}  Name")
        print("─" * 100)
        for e in sorted(section_entries, key=lambda x: (x['instance'] or 0, x['dp_id'])):
            dp_id = e['dp_id']
            inst  = f"{e['instance']}" if e['instance'] is not None else ""
            tp    = DP_TYPES.get(e['datatype'], f"0x{e['datatype']:02X}")
            beh   = DP_BEHAVIORS.get(e['behavior'], f"{e['behavior']}")
            # Use signed min/max for Signed type, unsigned for everything else
            min_v = e['min_s'] if e['datatype'] == 15 else e['min_u']
            max_v = e['max_s'] if e['datatype'] == 15 else e['max_u']
            name  = DP_NAMES.get(dp_id, "")
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
    session = Ble20Session(connector)

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
        entries = await session.inventory()
        print(f"  {len(entries)} DpIds returned.")

        _print_inventory(entries)

        # Build DpId → entry lookup for type info
        dp_map = {(e['dp_id'], e['instance']): e for e in entries}

        # ── Optional: read one DpId ───────────────────────────────────────────
        if args.read is not None:
            dp_id = args.read
            entry = dp_map.get((dp_id, None)) or dp_map.get((dp_id, 0))
            name  = DP_NAMES.get(dp_id, f"DpId {dp_id}")
            print(f"\nRead {name} (DpId {dp_id})...")
            raw = await session.read(dp_id)
            datatype = entry['datatype'] if entry else -1
            tp  = DP_TYPES.get(datatype, f"0x{datatype:02X}")
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
            entry = dp_map.get((write_dp_id, None)) or dp_map.get((write_dp_id, 0))
            if entry is None:
                print(f"\nWARNING: DpId {write_dp_id} not found in inventory — "
                      f"device may not support it.  Attempting write anyway.")
            name = DP_NAMES.get(write_dp_id, f"DpId {write_dp_id}")
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
            entry  = dp_map.get((dp_id, None)) or dp_map.get((dp_id, 0))
            name   = DP_NAMES.get(dp_id, f"DpId {dp_id}")
            dt     = entry['datatype'] if entry else -1
            tp     = DP_TYPES.get(dt, f"0x{dt:02X}")
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
