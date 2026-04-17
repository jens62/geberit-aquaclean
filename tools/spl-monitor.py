#!/usr/bin/env python3
"""
spl-monitor.py — Continuous GetSystemParameterList monitor
===========================================================

Stays connected to the Geberit AquaClean over BLE and polls
GetSystemParameterList every N seconds, logging timestamped values and
highlighting any changes.  Designed for live discovery of unknown parameter
semantics: operate the toilet while this runs, then correlate changes with
what you did.

One GetSPL call per poll cycle using the iPhone's 12-param list:
  [0, 1, 2, 3, 4, 5, 6, 7, 4, 8, 9, 10]

Note: param index 4 (descalingState) appears twice in the iPhone list —
the duplicate is preserved exactly as observed in iPhone BLE traffic.

Usage
-----
  # Poll every 1 s (default):
  python tools/spl-monitor.py

  # Poll every 2 s, write to log file:
  python tools/spl-monitor.py --interval 2 --log-file /tmp/spl-monitor.log

  # Ctrl-C to stop.
"""

import argparse
import asyncio
import configparser
import datetime
import logging
import os
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Project root on path
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ---------------------------------------------------------------------------
# Register TRACE/SILLY log levels used by imported bridge modules
# ---------------------------------------------------------------------------
def _add_level(name: str, value: int):
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

_add_level('SILLY', 4)
_add_level('TRACE', 5)

# Silence bridge internals — we only want the monitor's own output
logging.basicConfig(level=logging.WARNING)
log = logging.getLogger('spl-monitor')
log.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Bridge imports
# ---------------------------------------------------------------------------
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import AquaCleanBaseClient
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute

# ---------------------------------------------------------------------------
# AdHocCall (same pattern as geberit-ble-probe.py)
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
# SPL parameter definitions
# ---------------------------------------------------------------------------
PARAM_NAMES = {
    0:  "userIsSitting",
    1:  "sitting (code: analShowerIsRunning)",
    2:  "ladyShowerIsRunning",
    3:  "analShower (code: dryerIsRunning)",
    4:  "descalingState",
    5:  "descalingDurationInMinutes",
    6:  "lastErrorCode",
    7:  "unknown_7",
    8:  "unknown_8",
    9:  "orientationLightState",
    10: "unknown_10",
}

# iPhone's 12-param list — exactly as observed in BLE traffic.
# Note: param index 4 (descalingState) appears twice; the duplicate is preserved.
IPHONE_PARAMS = [0, 1, 2, 3, 4, 5, 6, 7, 4, 8, 9, 10]

# Unique param indices for display (preserves order, drops the duplicate 4)
_DISPLAY_PARAMS = list(dict.fromkeys(IPHONE_PARAMS))

# GetSPL args: count (1 byte) + param indices + zero-pad to 13 bytes
def _spl_args(params: list) -> bytes:
    data = bytearray(13)
    data[0] = len(params)
    for i, p in enumerate(params):
        data[i + 1] = p
    return bytes(data)


# ---------------------------------------------------------------------------
# Decode SPL result bytes → {param_index: value}
# ---------------------------------------------------------------------------
def _decode_spl(result_bytes: bytes, params: list) -> dict:
    """
    result_bytes layout (from Deserializer.py):
      byte 0     : 'a' header byte (valid record count)
      bytes 1..N : 5-byte records — [idx_byte (1)][value LE uint32 (4)]
                   one record per requested param, in request order
    """
    values = {}
    for i, param_idx in enumerate(params):
        base = i * 5 + 1          # skip 'a' byte
        if base + 5 > len(result_bytes):
            values[param_idx] = None
            continue
        idx_echo = result_bytes[base]
        value    = int.from_bytes(result_bytes[base + 1: base + 5], 'little')
        # idx_echo == param_idx confirms device returned this param;
        # idx_echo == 0 when param_idx != 0 means not supported
        if idx_echo == param_idx or (param_idx == 0):
            values[param_idx] = value
        else:
            values[param_idx] = None   # device does not support this param
    return values


# ---------------------------------------------------------------------------
# Config loader
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
# Output (console + optional file)
# ---------------------------------------------------------------------------
_log_file = None

def _out(line: str = ''):
    print(line, flush=True)
    if _log_file:
        _log_file.write(line + '\n')
        _log_file.flush()


# ---------------------------------------------------------------------------
# Main monitor loop
# ---------------------------------------------------------------------------
async def monitor(args):
    global _log_file

    cfg_host, cfg_port, cfg_psk, cfg_dev = _load_config(args.config)
    host   = args.esphome_host or cfg_host
    port   = args.esphome_port or cfg_port
    psk    = args.esphome_psk  or cfg_psk
    device = args.device       or cfg_dev

    if not device:
        print("ERROR: No device address. Provide --device or set [BLE] device_id in config.ini.")
        return 1

    if args.log_file:
        _log_file = open(args.log_file, 'a')
        print(f"Logging to: {args.log_file}")

    spl_args = _spl_args(IPHONE_PARAMS)
    call_spl = AdHocCall(0x01, 0x0D, spl_args)

    connector   = BluetoothLeConnector(esphome_host=host, esphome_port=port, esphome_noise_psk=psk)
    base_client = AquaCleanBaseClient(connector)

    print(f"Connecting to {device}" + (f" via {host}:{port}" if host else " via local BLE") + "...")

    await base_client.connect_async(device)
    print("Connected. Running subscribe sequence...")
    await base_client.subscribe_notifications_async()
    print("Ready. Polling every {:.0f}s — Ctrl-C to stop.\n".format(args.interval))

    prev: dict = {}
    poll = 0

    try:
        while True:
            poll += 1
            ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            current: dict = {}
            error = None

            try:
                await base_client.send_request(call_spl, send_as_first_cons=True)
                raw = call_spl.result(base_client.message_context.result_bytes)
                current.update(_decode_spl(raw, IPHONE_PARAMS))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            # Find changes (compare against unique param set for display)
            changed = {p: (prev.get(p), current.get(p))
                       for p in _DISPLAY_PARAMS
                       if p in current and current.get(p) != prev.get(p) and prev}

            # Header line
            change_note = ''
            if changed:
                parts = [f"param {p} ({PARAM_NAMES.get(p, '?')}): {old} → {new}"
                         for p, (old, new) in changed.items()]
                change_note = '  *** CHANGED: ' + ' | '.join(parts) + ' ***'
            elif error:
                change_note = f'  ERROR: {error}'

            _out(f"[#{poll:04d}] {ts}{change_note}")

            if error:
                _out(f"  ERROR: {error}")
            else:
                for p in _DISPLAY_PARAMS:
                    val = current.get(p)
                    name = PARAM_NAMES.get(p, f'param_{p}')
                    val_str = str(val) if val is not None else 'N/A'
                    marker = '  <<<' if p in changed else ''
                    _out(f"  [{p:2d}] {name:<40s} = {val_str}{marker}")

            _out()
            prev = current
            await asyncio.sleep(args.interval)

    except KeyboardInterrupt:
        _out(f"\nStopped after {poll} polls.")
    finally:
        try:
            await base_client.disconnect()
        except Exception:
            pass
        if _log_file:
            _log_file.close()
        print("Disconnected.")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        prog='spl-monitor',
        description='Continuously poll GetSystemParameterList and log changes.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Polls the iPhone's 12-param list in a single GetSPL call per cycle:
  [0, 1, 2, 3, 4, 5, 6, 7, 4, 8, 9, 10]

Operate the toilet while running, then analyze the log to correlate
parameter changes with physical device actions.
""")
    p.add_argument('--device',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--interval', type=float, default=1.0, metavar='SECONDS',
                   help='Poll interval in seconds (default: 1.0)')
    p.add_argument('--log-file', metavar='PATH',
                   help='Append output to FILE in addition to stdout')
    p.add_argument('--esphome-host', metavar='HOST',
                   help='ESP32 hostname/IP (default: [ESPHOME] host in config.ini)')
    p.add_argument('--esphome-port', type=int, metavar='PORT',
                   help='ESP32 API port (default: 6053)')
    p.add_argument('--esphome-psk', metavar='PSK',
                   help='ESP32 noise PSK (default: [ESPHOME] noise_psk in config.ini)')
    p.add_argument('--config', metavar='PATH',
                   help='Path to config.ini (default: config.ini in repo root)')
    args = p.parse_args()
    return asyncio.run(monitor(args))


if __name__ == '__main__':
    sys.exit(main())
