#!/usr/bin/env python3
"""
ble-session-replay.py — Geberit AquaClean BLE Session Replay
=============================================================

Parses an iPhone PacketLogger log, extracts the BLE procedure calls the iPhone
app sent to the device, and re-sends them to the actual device.

Primary use case
----------------
Testing whether a specific init sequence can recover E0003 ("device visible but
no response") without a power cycle.  The iPhone's captured session is the
reference; this script replays it verbatim so you can compare the device's
response with and without specific frame sequences.

What is replayed
----------------
Only outgoing frames (ATT Send) to the GATT write handles (0x0003 WRITE_0,
0x0006 WRITE_1).  Excluded:
  - ATT Receive frames (device → phone)   — we capture our own live responses
  - CCCD write frames (0x0010, 0x0014…)   — the bridge sets up notifications
  - CONTROL/ACK frames                    — the bridge handles its own ACKs
  - INFO frames                           — device-initiated, ignored

Supports local bleak (Linux/Mac) and ESPHome BLE proxy (ESP32).

Usage examples
--------------
  # Replay entire init sequence from a captured log:
  python tools/ble-session-replay.py local-assets/Bluetooth-Logs/Connect-Toggle-Lid-shutdown-app.txt

  # Replay only specific procedures (space- or comma-separated hex codes):
  python tools/ble-session-replay.py <log> --procs 0x11,0x13

  # Replay only from a time window:
  python tools/ble-session-replay.py <log> --from 12:52:36 --to 12:52:50

  # Stop after N procedure calls:
  python tools/ble-session-replay.py <log> --limit 20

  # Dry-run: show what would be sent, without connecting:
  python tools/ble-session-replay.py <log> --dry-run

  # Override device and ESP32 host:
  python tools/ble-session-replay.py <log> --device AA:BB:CC:DD:EE:FF --esphome-host 192.168.0.50

  # Skip bridge's subscribe sequence (replay log frames as-is, including 0x11/0x13):
  python tools/ble-session-replay.py <log> --no-pre-subscribe

  # Send Proc_0x55 before the log calls (test if it unlocks GetSPL):
  python tools/ble-session-replay.py <log> --procs 0x0D --prepend 0x01:0x55:01
"""

import argparse
import asyncio
import configparser
import logging
import os
import re
import struct
import sys
from datetime import datetime
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Project root on path
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ---------------------------------------------------------------------------
# Register TRACE/SILLY log levels used by bridge modules
# ---------------------------------------------------------------------------
def _add_level(name: str, value: int):
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

_add_level('SILLY', 4)
_add_level('TRACE', 5)

logging.basicConfig(level=logging.WARNING, format='%(levelname)s  %(message)s')
log = logging.getLogger('replay')
log.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Bridge imports
# ---------------------------------------------------------------------------
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import AquaCleanBaseClient
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute

# ---------------------------------------------------------------------------
# Protocol tables (kept minimal — ble-decode.py has the full version)
# ---------------------------------------------------------------------------
PROCS = {
    (0x00, 0x82): "GetDeviceIdentification",
    (0x00, 0x86): "GetDeviceInitialOperationDate",
    (0x01, 0x05): "UnknownProc_0x05",
    (0x01, 0x07): "UnknownProc_0x07",
    (0x01, 0x09): "SetCommand",
    (0x01, 0x0A): "GetStoredProfileSetting (init)",
    (0x01, 0x0B): "SetStoredProfileSetting (init)",
    (0x01, 0x0D): "GetSystemParameterList",
    (0x01, 0x0E): "GetFirmwareVersionList",
    (0x01, 0x11): "SubscribeNotifications_Pre",
    (0x01, 0x13): "SubscribeNotifications",
    (0x01, 0x45): "GetStatisticsDescale",
    (0x01, 0x51): "GetStoredCommonSetting",
    (0x01, 0x52): "SetStoredCommonSetting",
    (0x01, 0x53): "GetStoredProfileSetting",
    (0x01, 0x54): "SetStoredProfileSetting",
    (0x01, 0x55): "UnknownProc_0x55",
    (0x01, 0x56): "SetDeviceRegistrationLevel",
    (0x01, 0x59): "GetFilterStatus",
    (0x01, 0x81): "GetSOCApplicationVersions",
}

# GATT write handles: 0x0003 = WRITE_0 (SINGLE/FIRST), 0x0006 = WRITE_1 (CONS)
WRITE_HANDLES = {0x0003, 0x0006, 0x0009, 0x000C}
# CCCD notification enable handles — skip these, bridge handles them
CCCD_HANDLES  = {0x0010, 0x0014, 0x0018, 0x001C}


# ---------------------------------------------------------------------------
# Log line parser (same format as ble-decode.py)
# ---------------------------------------------------------------------------
_LINE_RE = re.compile(
    r"(\w+ +\d+ \d+:\d+:\d+\.\d+)\s+"    # timestamp
    r"(ATT Send|ATT Receive)\s+"          # direction
    r"0x[0-9A-Fa-f]+\s+"                 # connection handle
    r"([\dA-Fa-f:]{17})\s+"             # MAC address
    r".*?"                               # description
    r"\s{2,}"                            # gap before hex
    r"((?:[0-9A-Fa-f]{2} )+[0-9A-Fa-f]{2})\s*$"
)


def _parse_line(line: str):
    """Return (timestamp_str, direction, mac, raw_bytes) or None."""
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    ts_str, direction, mac, hex_str = m.groups()
    raw = bytes(int(x, 16) for x in hex_str.split())
    return ts_str, direction, mac, raw


def _parse_ts(ts_str: str) -> Optional[datetime]:
    try:
        return datetime.strptime("2000 " + ts_str, "%Y %b %d %H:%M:%S.%f")
    except ValueError:
        return None


def _parse_hhmm(s: str) -> Optional[datetime]:
    try:
        t = datetime.strptime(s, "%H:%M:%S")
        return datetime(2000, 1, 1, t.hour, t.minute, t.second)
    except ValueError:
        return None


def _extract_frame(raw: bytes):
    """
    Extract (att_handle, geberit_20_bytes) from a raw HCI/L2CAP/ATT packet.
    Returns None if not a Geberit GATT frame.
    """
    # Layout: HCI(2) + HCI_len(2) + L2CAP_len(2) + channel(2) + ATT_op(1) + handle(2) + value(20)
    if len(raw) < 31:
        return None
    att_opcode = raw[8]
    if att_opcode not in (0x52, 0x1B):  # Write Without Response or Handle Value Notification
        return None
    handle = raw[9] | (raw[10] << 8)
    frame = raw[11:31]
    if len(frame) != 20:
        return None
    return handle, bytes(frame)


# ---------------------------------------------------------------------------
# Frame kind / type helpers
# ---------------------------------------------------------------------------
def _frame_kind(hdr: int) -> str:
    ft = (hdr >> 5) & 7
    if ft == 3:
        return "CONTROL"
    if ft == 4:
        return "INFO"
    if ft == 2:
        return f"CONS_DEV[{(hdr >> 1) & 7}]"
    if ft == 1:
        return "FIRST_DEV"
    # FrameType 0 (MSG)
    if hdr & 1:  # IsSubFrameCount
        count = (hdr >> 1) & 7
        return "SINGLE" if count == 0 else f"FIRST[{count}]"
    return f"CONS[{(hdr >> 1) & 7}]"


# ---------------------------------------------------------------------------
# Decode a SINGLE request frame → (ctx, proc, args)
# ---------------------------------------------------------------------------
def _decode_single_request(frame: bytes) -> Optional[Tuple[int, int, bytes]]:
    """Return (ctx, proc, args_bytes) if this is a SINGLE request frame, else None."""
    if len(frame) != 20 or _frame_kind(frame[0]) != "SINGLE":
        return None
    payload = frame[1:20]
    if payload[0] != 0x04:  # not a request
        return None
    ctx     = payload[7]
    proc    = payload[8]
    arg_len = payload[9]
    args    = payload[10:10 + arg_len]
    return ctx, proc, bytes(args)


# ---------------------------------------------------------------------------
# Assembler for FIRST+CONS pairs (outgoing)
# ---------------------------------------------------------------------------
class _Assembler:
    """Accumulate FIRST + CONS frames and return decoded (ctx, proc, args) when complete."""

    def __init__(self):
        self._pending = None

    def feed(self, frame: bytes) -> Optional[Tuple[int, int, bytes, bool]]:
        """
        Feed a 20-byte outgoing frame.
        Returns (ctx, proc, args, is_first_cons) when all CONS have arrived, else None.
        """
        kind = _frame_kind(frame[0])

        if "FIRST" in kind and "DEV" not in kind:
            # Outgoing FIRST frame (FrameType=0, IsCount=1, Count=N>0)
            cons_needed = (frame[0] >> 1) & 7
            first_payload = frame[2:20]
            # FIRST frame payload: [0xFF][0x00][body_len][ctr_lo][ctr_hi][node][ctx][proc][arg_len][args...]
            self._pending = {
                "cons_needed": cons_needed,
                "body_len": None,   # computed in _assemble() from first_payload[2]
                "first": frame,
                "cons": [],
            }
            if cons_needed == 0:
                return self._assemble()
            return None

        if kind.startswith("CONS[") and self._pending is not None:
            self._pending["cons"].append(frame)
            if len(self._pending["cons"]) >= self._pending["cons_needed"]:
                return self._assemble()
            return None

        return None

    def _assemble(self):
        first       = self._pending["first"]
        cons_frames = self._pending["cons"]
        body_len    = self._pending["body_len"]
        self._pending = None

        # FIRST frame: bytes[2..19] = first_payload (18 bytes); byte[1] = first data byte
        # FIRST frames do NOT have the 0x04 msg_type byte (unlike SINGLE frames).
        # first_payload layout: [0xFF][0x00][body_len][ctr_lo][ctr_hi][node][ctx][proc][arg_len][args...]
        # body starts at first_payload[5] (node byte); frame[1] is also part of the data stream.
        first_payload = first[2:20]  # 18 bytes
        body_len      = first_payload[2]       # correct offset for body_len
        body_from_first = first_payload[5:]    # 13 bytes starting at node
        body_byte1 = bytes([first[1]])         # 1 byte — also part of the arg data

        parts = [body_from_first, body_byte1]
        for c in cons_frames:
            parts.append(c[1:])  # 19 bytes each

        raw_body = b"".join(parts)[:body_len]
        # Body layout: [node][ctx][proc][arg_len][args...]
        if len(raw_body) < 4:
            return None
        ctx     = raw_body[1]
        proc    = raw_body[2]
        arg_len = raw_body[3]
        args    = raw_body[4:4 + arg_len]
        return ctx, proc, bytes(args), True  # True = was FIRST+CONS


# ---------------------------------------------------------------------------
# Parse log → list of (ctx, proc, args, first_cons, timestamp_str, proc_name)
# ---------------------------------------------------------------------------
def parse_log(
    path: str,
    mac_filter: Optional[str],
    proc_filter: Optional[set],
    time_from: Optional[datetime],
    time_to: Optional[datetime],
    limit: int,
) -> List[Tuple[int, int, bytes, bool, str, str]]:
    """
    Parse a PacketLogger log and return a list of decoded outgoing procedure calls.
    Each entry: (ctx, proc, args, send_as_first_cons, timestamp_str, proc_name)
    """
    calls = []
    assembler = _Assembler()

    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            parsed = _parse_line(line)
            if parsed is None:
                continue
            ts_str, direction, mac, raw = parsed

            # Direction filter: only outgoing (app → device)
            if direction != "ATT Send":
                continue

            # MAC filter
            if mac_filter and mac.upper() != mac_filter.upper():
                continue

            # Time range filter
            if time_from or time_to:
                ts = _parse_ts(ts_str)
                if ts:
                    check = ts.replace(year=2000, month=1, day=1)
                    if time_from and check < time_from:
                        continue
                    if time_to and check > time_to:
                        continue

            extracted = _extract_frame(raw)
            if extracted is None:
                continue
            handle, frame = extracted

            # Skip CCCD writes — bridge handles notification setup
            if handle in CCCD_HANDLES:
                continue

            # Only process WRITE_0 and WRITE_1
            if handle not in WRITE_HANDLES:
                continue

            # Try SINGLE frame decode first
            decoded = _decode_single_request(frame)
            if decoded:
                ctx, proc, args = decoded
                _emit(calls, ctx, proc, args, False, ts_str, proc_filter)
                if limit and len(calls) >= limit:
                    break
                continue

            # Try FIRST+CONS assembler
            result = assembler.feed(frame)
            if result:
                ctx, proc, args, _ = result
                _emit(calls, ctx, proc, args, True, ts_str, proc_filter)
                if limit and len(calls) >= limit:
                    break

    return calls


def _emit(calls, ctx, proc, args, first_cons, ts_str, proc_filter):
    if proc_filter and proc not in proc_filter:
        return
    name = PROCS.get((ctx, proc), f"Proc(ctx=0x{ctx:02X},proc=0x{proc:02X})")
    calls.append((ctx, proc, args, first_cons, ts_str, name))


# ---------------------------------------------------------------------------
# AdHocCall (same as in geberit-ble-probe.py)
# ---------------------------------------------------------------------------
class AdHocCall:
    def __init__(self, context: int, procedure: int, args: bytes = b'', node: int = 0x01):
        self._attr = ApiCallAttribute(context, procedure, node)
        self._args = bytes(args)

    def get_api_call_attribute(self):
        return self._attr

    def get_payload(self) -> bytes:
        return self._args

    def result(self, data: bytearray) -> bytes:
        return bytes(data)


# ---------------------------------------------------------------------------
# Config helper
# ---------------------------------------------------------------------------
def _load_config(config_path: Optional[str] = None):
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    path = config_path or os.path.join(_repo_root, 'config.ini')
    read = cfg.read(path)
    if config_path and not read:
        log.warning(f"Config file not found: {config_path}")
    host = cfg.get('ESPHOME', 'host', fallback='').strip() or None
    port = int(cfg.get('ESPHOME', 'port', fallback='6053').strip() or 6053)
    psk  = cfg.get('ESPHOME', 'noise_psk', fallback='').strip() or None
    dev  = cfg.get('BLE', 'device_id', fallback='').strip() or None
    mac  = cfg.get('BLE', 'device_id', fallback='').strip() or None
    return host, port, psk, dev, mac


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------
async def run(args):
    cfg_host, cfg_port, cfg_psk, cfg_dev, cfg_mac = _load_config(args.config)

    host    = args.esphome_host or cfg_host
    port    = args.esphome_port or cfg_port
    psk     = args.esphome_psk  or cfg_psk
    device  = args.device       or cfg_dev

    # MAC filter for log parsing — default to device_id from config
    mac_filter = args.mac or cfg_mac or None

    # Procedure filter
    proc_filter = None
    if args.procs:
        raw_procs = re.split(r'[\s,]+', args.procs)
        proc_filter = set(int(p, 16) for p in raw_procs if p)

    time_from = _parse_hhmm(args.from_time) if args.from_time else None
    time_to   = _parse_hhmm(args.to_time)   if args.to_time   else None

    print(f"\nGeberit BLE Session Replay")
    print(f"  Log file  : {args.log}")
    if mac_filter:
        print(f"  MAC filter: {mac_filter}")
    if proc_filter:
        print(f"  Proc filter: {[hex(p) for p in sorted(proc_filter)]}")
    if time_from or time_to:
        print(f"  Time range: {args.from_time or 'start'} → {args.to_time or 'end'}")
    if args.limit:
        print(f"  Limit     : {args.limit} calls")
    print()

    # Parse log
    calls = parse_log(
        args.log,
        mac_filter=mac_filter,
        proc_filter=proc_filter,
        time_from=time_from,
        time_to=time_to,
        limit=args.limit or 0,
    )

    if not calls:
        print("No matching procedure calls found in log.")
        return 1

    print(f"Found {len(calls)} procedure call(s) to replay:\n")
    for i, (ctx, proc, args_b, fc, ts, name) in enumerate(calls):
        mode = " [FIRST+CONS]" if fc else ""
        print(f"  {i+1:3d}. [{ts}]  {name}  args={args_b.hex() or '(none)'}{mode}")

    if args.dry_run:
        print("\nDry-run — not connecting to device.")
        return 0

    if not device:
        log.error("No device address. Provide --device or set [BLE] device_id in config.ini.")
        return 1

    print(f"\nDevice    : {device}")
    if host:
        print(f"Via ESP32 : {host}:{port}")
    else:
        print(f"Via       : local BLE (bleak)")
    print()

    connector = BluetoothLeConnector(
        esphome_host=host,
        esphome_port=port,
        esphome_noise_psk=psk,
    )
    base_client = AquaCleanBaseClient(connector)

    try:
        log.info("Connecting...")
        await base_client.connect_async(device)
        print("Connected.")

        if not args.no_pre_subscribe:
            log.info("Subscribe sequence (4×0x11 + 4×0x13)...")
            await base_client.subscribe_notifications_async()
            print("Subscribed.\n")
        else:
            print("(pre-subscribe skipped)\n")

        if args.prepend:
            parts = args.prepend.split(':')
            pre_ctx  = int(parts[0], 16)
            pre_proc = int(parts[1], 16)
            pre_args = bytes.fromhex(parts[2]) if len(parts) > 2 and parts[2] else b''
            pre_call = AdHocCall(pre_ctx, pre_proc, pre_args)
            pre_name = f"Prepend(ctx=0x{pre_ctx:02X}, proc=0x{pre_proc:02X})"
            print(f"[PRE] {pre_name}  args={pre_args.hex() or '(none)'}")
            try:
                await base_client.send_request(pre_call)
                raw = pre_call.result(base_client.message_context.result_bytes)
                if raw:
                    print(f"      → {raw.hex()}  ({len(raw)} bytes)")
                else:
                    print(f"      → (empty response / OK)")
            except Exception as exc:
                print(f"      → FAILED: {type(exc).__name__}: {exc}")
            print()

        for i, (ctx, proc, args_b, first_cons, ts, name) in enumerate(calls):
            call = AdHocCall(ctx, proc, args_b)
            mode = " [F+C]" if first_cons else ""
            print(f"[{i+1:3d}] {name}{mode}  args={args_b.hex() or '(none)'}")
            try:
                await base_client.send_request(
                    call,
                    send_as_first_cons=first_cons,
                )
                raw = call.result(base_client.message_context.result_bytes)
                if raw:
                    print(f"      → {raw.hex()}  ({len(raw)} bytes)")
                    if len(raw) >= 2:
                        val16 = struct.unpack_from('<H', raw)[0]
                        print(f"         uint16 LE: {val16}")
                else:
                    print(f"      → (empty response / OK)")
            except Exception as exc:
                print(f"      → FAILED: {type(exc).__name__}: {exc}")

            if args.delay > 0:
                await asyncio.sleep(args.delay)

    finally:
        try:
            await base_client.disconnect()
        except Exception:
            pass
        print("\nDisconnected.")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        prog='ble-session-replay',
        description='Parse a Geberit iPhone BLE log and re-send the procedure calls to the device.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Replay the full init sequence (0x11, 0x13 subscribe + all reads):
  python tools/ble-session-replay.py local-assets/Bluetooth-Logs/Connect-Toggle-Lid-shutdown-app.txt

  # Replay only the subscribe procs (0x11 and 0x13) — test E0003 unlock:
  python tools/ble-session-replay.py <log> --procs 0x11,0x13

  # Show what would be sent, without connecting:
  python tools/ble-session-replay.py <log> --dry-run

  # First 30 calls, 100ms delay between each:
  python tools/ble-session-replay.py <log> --limit 30 --delay 0.1

  # Filter to a specific time window:
  python tools/ble-session-replay.py <log> --from 12:52:36 --to 12:52:50
""")
    p.add_argument('log',
                   help='Path to PacketLogger log file (.txt)')
    p.add_argument('--mac',
                   help='Filter log to this MAC address '
                        '(default: [BLE] device_id from config.ini or first seen)')
    p.add_argument('--procs', metavar='HEX,...',
                   help='Comma-separated proc codes to replay, e.g. "0x11,0x13" '
                        '(default: replay all)')
    p.add_argument('--from', dest='from_time', metavar='HH:MM:SS',
                   help='Only replay frames at or after this time')
    p.add_argument('--to', dest='to_time', metavar='HH:MM:SS',
                   help='Only replay frames at or before this time')
    p.add_argument('--limit', type=int, default=0,
                   help='Stop after N procedure calls (default: no limit)')
    p.add_argument('--delay', type=float, default=0.0,
                   help='Seconds to wait between calls (default: 0)')
    p.add_argument('--timeout', type=float, default=5.0,
                   help='Response timeout per call in seconds (default: 5.0)')
    p.add_argument('--no-pre-subscribe', action='store_true',
                   help='Skip the bridge subscribe sequence before replay '
                        '(the 0x11/0x13 frames in the log will still be replayed if present)')
    p.add_argument('--dry-run', action='store_true',
                   help='Parse and list calls without connecting to the device')
    p.add_argument('--device',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--esphome-host', metavar='HOST',
                   help='ESP32 hostname/IP (default: [ESPHOME] host in config.ini)')
    p.add_argument('--esphome-port', type=int, metavar='PORT',
                   help='ESP32 API port (default: 6053)')
    p.add_argument('--esphome-psk', metavar='PSK',
                   help='ESP32 noise PSK (default: [ESPHOME] noise_psk in config.ini)')
    p.add_argument('--prepend', metavar='CTX:PROC[:ARGS]',
                   help='Send one extra call before the log, e.g. "0x01:0x55:01" for Proc_0x55. '
                        'CTX and PROC are hex; ARGS is optional hex bytes (no spaces). '
                        'Runs after the subscribe sequence, before any log calls.')
    p.add_argument('--config', metavar='PATH',
                   help='Path to config.ini (default: config.ini in repo root)')
    return asyncio.run(run(p.parse_args()))


if __name__ == '__main__':
    sys.exit(main())
