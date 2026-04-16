#!/usr/bin/env python3
"""
geberit-ble-probe.py — Geberit AquaClean BLE Procedure Probe
==============================================================

Connects to a Geberit AquaClean toilet and calls a single BLE procedure by code,
returning the raw response bytes. Designed for investigating unknown procedures
(0x05, 0x07, 0x55, 0x56) and validating new protocol discoveries before wiring
them into the bridge.

Does NOT require a running bridge — imports bridge classes from the installed venv
package (or the project tree).  Reads device address, ESP32 host/psk from
config.ini when not supplied on the command line.

Supports local bleak (Linux/Mac) and ESPHome BLE proxy (ESP32).

Usage examples
--------------
  # Probe unknown GetNodeList (proc 0x05) — no args:
  python tools/geberit-ble-probe.py --proc 0x05

  # GetStoredCommonSetting (0x51) for setting_id=4 (WcLidSensorSensitivity):
  python tools/geberit-ble-probe.py --proc 0x51 --args 04

  # GetSystemParameterList (0x0D) for params 0,1,2,3:
  python tools/geberit-ble-probe.py --proc 0x0D --args 04 00 01 02 03 00 00 00 00 00 00 00 00

  # Proc 0x55 (present in every iPhone session end-of-init):
  python tools/geberit-ble-probe.py --proc 0x55 --args 01

  # Call 3 times — useful to see if E0003 clears on retry:
  python tools/geberit-ble-probe.py --proc 0x0D --args 04 00 01 02 03 00 00 00 00 00 00 00 00 --repeat 3

  # Override device and ESP32 host from config.ini:
  python tools/geberit-ble-probe.py --proc 0x05 --device AA:BB:CC:DD:EE:FF --esphome-host 192.168.0.50

  # GetFirmwareVersionList requires FIRST+CONS send mode:
  python tools/geberit-ble-probe.py --proc 0x0E --first-cons \\
      --args 08 01 03 04 05 06 07 08 09 00 00 00 00

  # Skip subscribe sequence (faster; may not work if device is in stuck state):
  python tools/geberit-ble-probe.py --proc 0x0D --no-subscribe \\
      --args 04 00 01 02 03 00 00 00 00 00 00 00 00
"""

import argparse
import asyncio
import configparser
import logging
import os
import struct
import sys
from typing import Optional

# ---------------------------------------------------------------------------
# Project root on path (works both when installed via pip and run from repo)
# ---------------------------------------------------------------------------
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# ---------------------------------------------------------------------------
# Register TRACE/SILLY log levels used by imported bridge modules.
# Must happen before any bridge import.
# ---------------------------------------------------------------------------
def _add_level(name: str, value: int):
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

_add_level('SILLY', 4)
_add_level('TRACE', 5)

# ---------------------------------------------------------------------------
# Logging: silence bridge internals; show only probe output at DEBUG+
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.WARNING, format='%(levelname)s  %(message)s')
log = logging.getLogger('probe')
log.setLevel(logging.DEBUG)

# ---------------------------------------------------------------------------
# Bridge imports (after path and level setup)
# ---------------------------------------------------------------------------
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import AquaCleanBaseClient
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute

# ---------------------------------------------------------------------------
# Known procedures (for display)
# ---------------------------------------------------------------------------
PROCS = {
    (0x00, 0x82): "GetDeviceIdentification",
    (0x00, 0x86): "GetDeviceInitialOperationDate",
    (0x01, 0x05): "UnknownProc_0x05 (GetNodeList?)",
    (0x01, 0x07): "UnknownProc_0x07",
    (0x01, 0x08): "SetActiveProfileSetting",
    (0x01, 0x09): "SetCommand",
    (0x01, 0x0A): "GetStoredProfileSetting (init/0x0A)",
    (0x01, 0x0B): "SetStoredProfileSetting (init/0x0B)",
    (0x01, 0x0D): "GetSystemParameterList",
    (0x01, 0x0E): "GetFirmwareVersionList",
    (0x01, 0x45): "GetStatisticsDescale",
    (0x01, 0x51): "GetStoredCommonSetting",
    (0x01, 0x52): "SetStoredCommonSetting",
    (0x01, 0x53): "GetStoredProfileSetting",
    (0x01, 0x54): "SetStoredProfileSetting",
    (0x01, 0x55): "UnknownProc_0x55 (session-ready?)",
    (0x01, 0x56): "SetDeviceRegistrationLevel",
    (0x01, 0x59): "GetFilterStatus",
    (0x01, 0x81): "GetSOCApplicationVersions",
}


# ---------------------------------------------------------------------------
# AdHocCall: minimal CallClass for arbitrary (context, procedure, args)
# ---------------------------------------------------------------------------
class AdHocCall:
    """Wraps (context, procedure, args) as a bridge-compatible CallClass."""

    def __init__(self, context: int, procedure: int, args: bytes = b'', node: int = 0x01):
        self._attr = ApiCallAttribute(context, procedure, node)
        self._args = bytes(args)

    def get_api_call_attribute(self):
        return self._attr

    def get_payload(self) -> bytes:
        return self._args

    def result(self, data: bytearray) -> bytes:
        """Return raw result bytes — caller handles interpretation."""
        return bytes(data)


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------
def _load_config(config_path: Optional[str] = None):
    """Return (esphome_host, esphome_port, esphome_psk, device_id) from config.ini."""
    cfg = configparser.ConfigParser(inline_comment_prefixes=('#',))
    path = config_path or os.path.join(_repo_root, 'config.ini')
    read = cfg.read(path)
    if config_path and not read:
        log.warning(f"Config file not found: {config_path}")
    host = cfg.get('ESPHOME', 'host', fallback='').strip() or None
    port = int(cfg.get('ESPHOME', 'port', fallback='6053').strip() or 6053)
    psk  = cfg.get('ESPHOME', 'noise_psk', fallback='').strip() or None
    dev  = cfg.get('BLE', 'device_id', fallback='').strip() or None
    return host, port, psk, dev


def _parse_hex_args(raw: str) -> bytes:
    """Accept hex args as continuous string or space-separated bytes."""
    cleaned = raw.replace(' ', '').replace('0x', '').replace(',', '')
    if not cleaned:
        return b''
    return bytes.fromhex(cleaned)


# ---------------------------------------------------------------------------
# Pretty-print a result payload
# ---------------------------------------------------------------------------
def _print_result(raw: bytes, ctx: int, proc: int):
    if not raw:
        print("  Result: (empty)")
        return
    print(f"  Hex     : {raw.hex()}")
    print(f"  Bytes   : {list(raw)}")
    if len(raw) >= 2:
        print(f"  uint16 LE: {struct.unpack_from('<H', raw)[0]}"
              f"   int16 LE: {struct.unpack_from('<h', raw)[0]}")
    if len(raw) >= 4:
        print(f"  uint32 LE: {struct.unpack_from('<I', raw)[0]}")
    # If most bytes are printable ASCII, show the null-separated strings
    printable = sum(0x20 <= b < 0x7F or b == 0 for b in raw)
    if printable == len(raw) and any(0x20 <= b < 0x7F for b in raw):
        parts = [s for s in raw.split(b'\x00') if s]
        decoded = [p.decode('ascii', errors='replace') for p in parts]
        print(f"  Strings : {decoded}")
    # Proc-specific interpretation
    if (ctx, proc) == (0x01, 0x51) and len(raw) >= 2:
        val = struct.unpack_from('<H', raw)[0]
        print(f"  → GetStoredCommonSetting value = {val}")
    elif (ctx, proc) == (0x01, 0x53) and len(raw) >= 2:
        val = struct.unpack_from('<H', raw)[0]
        print(f"  → GetStoredProfileSetting value = {val}")
    elif (ctx, proc) == (0x01, 0x81) and len(raw) >= 3:
        v1, v2, build = chr(raw[0]), chr(raw[1]), raw[2]
        print(f"  → SOC version = {v1}{v2}.{build}")
    elif (ctx, proc) == (0x01, 0x05) and len(raw) >= 1:
        print(f"  → GetNodeList: A={raw[0]}, B({len(raw)-1} bytes)={raw[1:].hex()}")
    elif (ctx, proc) == (0x00, 0x82) and len(raw) >= 26:
        # DeviceIdentification struct (offsets confirmed from live device response):
        #   [0:12]  SAP number (12 bytes)
        #   [12:26] Serial number (14 bytes)
        #   [26:32] 6 null padding bytes
        #   [32:42] Production date (10 bytes)
        #   [42:]   Description (null-padded)
        sap    = raw[0:12].rstrip(b'\x00').decode('ascii', errors='replace')
        serial = raw[12:26].rstrip(b'\x00').decode('ascii', errors='replace')
        prod   = raw[32:42].rstrip(b'\x00').decode('ascii', errors='replace') if len(raw) >= 42 else '?'
        desc   = raw[42:].rstrip(b'\x00').decode('ascii', errors='replace') if len(raw) >= 43 else '?'
        print(f"  → SAP        : {sap}")
        print(f"  → Serial     : {serial}")
        print(f"  → Production : {prod}")
        print(f"  → Description: {desc}")


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
        log.error("No device address. Provide --device or set [BLE] device_id in config.ini.")
        return 1

    ctx       = int(args.ctx,  16)
    proc      = int(args.proc, 16)
    args_bytes = _parse_hex_args(args.args)

    proc_name = PROCS.get((ctx, proc), f"UnknownProc(ctx=0x{ctx:02X}, proc=0x{proc:02X})")

    print(f"\nGeberit BLE Probe")
    print(f"  Procedure : {proc_name}  (ctx=0x{ctx:02X} proc=0x{proc:02X})")
    print(f"  Args      : {args_bytes.hex() or '(none)'}  ({len(args_bytes)} bytes)")
    print(f"  Device    : {device}")
    if host:
        print(f"  Via ESP32 : {host}:{port}")
    else:
        print(f"  Via       : local BLE (bleak)")
    if args.no_subscribe:
        print(f"  Subscribe : SKIPPED (--no-subscribe)")
    if args.first_cons:
        print(f"  Frame mode: FIRST+CONS")
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

        if not args.no_subscribe:
            log.info("Subscribe sequence (4×0x11 + 4×0x13)...")
            await base_client.subscribe_notifications_async()
            print("Subscribed.")

        if args.pre_check:
            print("\nPre-check: GetDeviceIdentification (ctx=0x00, proc=0x82)")
            try:
                pre_call = AdHocCall(0x00, 0x82, b'')
                await base_client.send_request(pre_call)
                raw = pre_call.result(base_client.message_context.result_bytes)
                if len(raw) >= 42:
                    sap    = raw[0:12].rstrip(b'\x00').decode('ascii', errors='replace')
                    serial = raw[12:26].rstrip(b'\x00').decode('ascii', errors='replace')
                    prod   = raw[32:42].rstrip(b'\x00').decode('ascii', errors='replace')
                    desc   = raw[42:].rstrip(b'\x00').decode('ascii', errors='replace')
                    print(f"  SAP: {sap}  Serial: {serial}  Production: {prod}  Desc: {desc}")
                else:
                    print(f"  Raw ({len(raw)} bytes): {raw.hex()}")
            except Exception as exc:
                print(f"  Pre-check FAILED: {type(exc).__name__}: {exc}")
            print()

        call = AdHocCall(ctx, proc, args_bytes)

        for i in range(args.repeat):
            label = f"Call {i+1}/{args.repeat}" if args.repeat > 1 else "Call"
            print(f"\n{label}: {proc_name}")
            try:
                await base_client.send_request(
                    call,
                    send_as_first_cons=args.first_cons,
                )
                raw = call.result(base_client.message_context.result_bytes)
                print(f"  Result ({len(raw)} bytes):")
                _print_result(raw, ctx, proc)
            except Exception as exc:
                print(f"  FAILED: {proc_name} — {type(exc).__name__}: {exc}")

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
        prog='geberit-ble-probe',
        description='Call a single Geberit AquaClean BLE procedure and print the response.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Known procedures (ctx 0x01 unless noted):
  0x05  UnknownProc / GetNodeList?   — no args
  0x07  UnknownProc_0x07             — 1 byte arg
  0x0D  GetSystemParameterList       — 13-byte padded list (count + indices)
  0x0E  GetFirmwareVersionList       — 13-byte padded list, use --first-cons
  0x51  GetStoredCommonSetting       — 1 byte: setting_id (0–7)
  0x53  GetStoredProfileSetting      — 2 bytes: profile_id(0), setting_id
  0x55  UnknownProc_0x55             — 1 byte arg: 0x01
  0x56  SetDeviceRegistrationLevel   — 2 bytes: 0x01 0x01
  0x59  GetFilterStatus              — 13-byte padded list
  0x81  GetSOCApplicationVersions    — no args
  0x82  GetDeviceIdentification (ctx 0x00) — no args

Example: investigate proc 0x07 with arg 0x00
  python tools/geberit-ble-probe.py --proc 0x07 --args 00
""")
    p.add_argument('--device',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--proc', default='0x05',
                   help='Procedure code in hex, e.g. 0x05 (default: 0x05)')
    p.add_argument('--ctx', default='0x01',
                   help='Context byte in hex (default: 0x01)')
    p.add_argument('--args', default='',
                   help='Argument bytes as hex — space-separated or continuous, '
                        'e.g. "04 00 01 02 03 00 00 00 00 00 00 00 00" (default: empty)')
    p.add_argument('--esphome-host', metavar='HOST',
                   help='ESP32 hostname/IP (default: [ESPHOME] host in config.ini)')
    p.add_argument('--esphome-port', type=int, metavar='PORT',
                   help='ESP32 API port (default: 6053)')
    p.add_argument('--esphome-psk', metavar='PSK',
                   help='ESP32 noise PSK (default: [ESPHOME] noise_psk in config.ini)')
    p.add_argument('--no-subscribe', action='store_true',
                   help='Skip the 4×0x11 + 4×0x13 subscribe sequence')
    p.add_argument('--first-cons', action='store_true',
                   help='Send as FIRST+CONS (required for GetFirmwareVersionList)')
    p.add_argument('--timeout', type=float, default=5.0,
                   help='Response timeout in seconds (default: 5.0)')
    p.add_argument('--repeat', type=int, default=1,
                   help='Call the procedure N times (default: 1)')
    p.add_argument('--config', metavar='PATH',
                   help='Path to config.ini (default: config.ini in repo root)')
    p.add_argument('--pre-check', action='store_true',
                   help='Call GetDeviceIdentification first to confirm device identity')
    return asyncio.run(run(p.parse_args()))


if __name__ == '__main__':
    sys.exit(main())
