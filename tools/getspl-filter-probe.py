#!/usr/bin/env python3
"""
getspl-filter-probe.py — GetSPL + GetFilterStatus ordering probe
=================================================================

Verifies the GetFilterStatus ordering fix:
  GetFilterStatus MUST be called BEFORE GetSystemParameterList with the
  12-param iPhone list [0,1,2,3,4,5,6,7,4,8,9,10].

After GetSPL receives unsupported params (8/9/10 on HB2304EU298413, idx_echo=0),
the device ACKs but ignores the next GetFilterStatus → BLEPeripheralTimeoutError.

Default mode (--order filter-first):
  1. Subscribe (4×Proc_0x13)
  2. GetFilterStatus   ← first: device is clean, always succeeds
  3. GetSPL(12 params) ← second: may corrupt device state, but filter data already captured

Reversed mode (--order spl-first) — reproduces the bug:
  1. Subscribe
  2. GetSPL(12 params) ← first: corrupts device state
  3. GetFilterStatus   ← second: ACKd but no data → timeout

Usage
-----
  # Test the fix (default):
  python tools/getspl-filter-probe.py

  # Reproduce the bug:
  python tools/getspl-filter-probe.py --order spl-first

  # Full TRACE log:
  python tools/getspl-filter-probe.py --trace --log-file /tmp/probe-ordering.log
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
# Register TRACE/SILLY log levels used by bridge modules
# ---------------------------------------------------------------------------
def _add_level(name: str, value: int):
    logging.addLevelName(value, name)
    setattr(logging, name, value)
    setattr(logging.Logger, name.lower(),
            lambda self, msg, *a, **kw: self.log(value, msg, *a, **kw))

_add_level('SILLY', 4)
_add_level('TRACE', 5)

# ---------------------------------------------------------------------------
# Bridge imports
# ---------------------------------------------------------------------------
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import AquaCleanBaseClient
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import BLEPeripheralTimeoutError

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
# Main probe
# ---------------------------------------------------------------------------
IPHONE_PARAMS = [0, 1, 2, 3, 4, 5, 6, 7, 4, 8, 9, 10]
PARAM_NAMES = {
    0: 'userIsSitting',
    1: 'analShowerIsRunning',
    2: 'ladyShowerIsRunning',
    3: 'dryerIsRunning',
    4: 'descalingState',
    5: 'descalingDurationInMinutes',
    6: 'lastErrorCode',
    7: 'unknown_7',
    8: 'unknown_8',
    9: 'orientationLightState',
    10: 'unknown_10',
}


async def probe(args):
    cfg_host, cfg_port, cfg_psk, cfg_dev = _load_config(args.config)
    host   = args.esphome_host or cfg_host
    port   = args.esphome_port or cfg_port
    psk    = args.esphome_psk  or cfg_psk
    device = args.device       or cfg_dev

    if not device:
        print("ERROR: No device address. Provide --device or set [BLE] device_id in config.ini.")
        return 1

    # Logging
    log_level = logging.TRACE if args.trace else logging.WARNING
    handlers = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
    logging.basicConfig(level=log_level, handlers=handlers,
                        format='%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s',
                        datefmt='%H:%M:%S')

    connector   = BluetoothLeConnector(esphome_host=host, esphome_port=port, esphome_noise_psk=psk)
    base_client = AquaCleanBaseClient(connector)

    order_desc = ('GetFilterStatus → GetSPL  [FIX — expected: both succeed]'
                  if args.order == 'filter-first' else
                  'GetSPL → GetFilterStatus  [BUG REPRO — expected: GetFilterStatus times out]')
    print(f"\n=== getspl-filter-probe ===")
    print(f"Order:  {order_desc}")
    print(f"Device: {device}" + (f" via {host}:{port}" if host else " via local BLE"))
    print()

    await base_client.connect_async(device)
    print("Connected. Subscribing...")
    await base_client.subscribe_notifications_async()
    print("Subscribed.\n")

    spl_result    = None
    spl_error     = None
    filter_result = None
    filter_error  = None

    async def do_spl():
        nonlocal spl_result, spl_error
        print(f"GetSPL (proc 0x0D), params {IPHONE_PARAMS} ...")
        t0 = datetime.datetime.now()
        try:
            spl_result = await base_client.get_system_parameter_list_async(IPHONE_PARAMS)
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            print(f"  ✅ OK  ({ms} ms)  a_byte={spl_result.a_byte}")
        except Exception as e:
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            spl_error = e
            print(f"  ❌ FAIL ({ms} ms)  {type(e).__name__}: {e}")

    async def do_filter():
        nonlocal filter_result, filter_error
        print("GetFilterStatus (proc 0x59) ...")
        t0 = datetime.datetime.now()
        try:
            filter_result = await base_client.get_filter_status_async()
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            print(f"  ✅ OK  ({ms} ms)")
        except BLEPeripheralTimeoutError as e:
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            filter_error = e
            print(f"  ❌ TIMEOUT ({ms} ms)  {e}")
        except Exception as e:
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            filter_error = e
            print(f"  ❌ FAIL ({ms} ms)  {type(e).__name__}: {e}")

    try:
        if args.order == 'filter-first':
            print("[1/2]", end=' ')
            await do_filter()
            print("[2/2]", end=' ')
            await do_spl()
        else:
            print("[1/2]", end=' ')
            await do_spl()
            print("[2/2]", end=' ')
            await do_filter()
    finally:
        try:
            await base_client.disconnect()
        except Exception:
            pass

    # Summary
    print()
    print("=== RESULTS ===")
    if spl_result is not None:
        print(f"GetSPL:          ✅  a_byte={spl_result.a_byte}, {len(spl_result.data_array)} values")
        unique_params = list(dict.fromkeys(IPHONE_PARAMS))
        for p in unique_params:
            idx = IPHONE_PARAMS.index(p)
            val = spl_result.data_array[idx] if idx < len(spl_result.data_array) else None
            name = PARAM_NAMES.get(p, f'param_{p}')
            val_str = str(val) if val is not None else 'N/A'
            print(f"  [{p:2d}] {name:<40s} = {val_str}")
    elif spl_error:
        print(f"GetSPL:          ❌  {type(spl_error).__name__}: {spl_error}")

    if filter_result is not None:
        print(f"GetFilterStatus: ✅")
        fs = filter_result
        if hasattr(fs, 'days_remaining'):
            print(f"  days_until_filter_change = {fs.days_remaining}")
        if hasattr(fs, 'last_filter_reset'):
            print(f"  last_filter_reset        = {fs.last_filter_reset}")
        if hasattr(fs, 'next_filter_change'):
            print(f"  next_filter_change       = {fs.next_filter_change}")
        if hasattr(fs, 'filter_reset_count'):
            print(f"  filter_reset_count       = {fs.filter_reset_count}")
    elif filter_error is not None:
        print(f"GetFilterStatus: ❌  {type(filter_error).__name__}: {filter_error}")

    print()
    if args.order == 'filter-first':
        if filter_error is None and spl_result is not None:
            print("✅  FIX CONFIRMED: both calls succeeded with GetFilterStatus called first.")
        elif filter_error is not None:
            print("❌  UNEXPECTED: GetFilterStatus failed even when called first.")
        else:
            print("⚠   Partial result.")
    else:
        if filter_error is not None and isinstance(filter_error, BLEPeripheralTimeoutError):
            print("✅  BUG REPRODUCED: GetFilterStatus timed out after GetSPL with params 8/9/10.")
        elif filter_error is not None:
            print(f"⚠   GetFilterStatus failed (not a timeout): {filter_error}")
        else:
            print("ℹ   GetFilterStatus succeeded despite spl-first order (device may not exhibit this bug).")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        prog='getspl-filter-probe',
        description='Probe GetFilterStatus + GetSPL ordering to verify the fix or reproduce the bug.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Root cause (confirmed 2026-04-17, log 7d0d8216):
  After GetSPL with unsupported param indices 8/9/10 (HB2304EU298413, idx_echo=0),
  the device ACKs GetFilterStatus CONTROL frame but sends no data → 5 s timeout.

Fix: call GetFilterStatus BEFORE GetSPL (--order filter-first, the default).
See memory/getspl-getfilterstatus-ordering.md for full analysis.
""")
    p.add_argument('--order', choices=['filter-first', 'spl-first'], default='filter-first',
                   help='filter-first = fix (default); spl-first = bug repro')
    p.add_argument('--device',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--trace', action='store_true',
                   help='Enable TRACE-level bridge logging')
    p.add_argument('--log-file', metavar='PATH',
                   help='Write output to FILE in addition to stdout')
    p.add_argument('--esphome-host', metavar='HOST')
    p.add_argument('--esphome-port', type=int, metavar='PORT')
    p.add_argument('--esphome-psk',  metavar='PSK')
    p.add_argument('--config', metavar='PATH',
                   help='Path to config.ini (default: repo root config.ini)')
    args = p.parse_args()
    return asyncio.run(probe(args))


if __name__ == '__main__':
    sys.exit(main())
