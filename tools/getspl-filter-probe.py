#!/usr/bin/env python3
"""
getspl-filter-probe.py — GetSPL + GetFilterStatus ordering probe
=================================================================

Verifies the GetFilterStatus ordering fix:
  GetFilterStatus MUST be called BEFORE GetSystemParameterList with the
  12-param iPhone list [0,1,2,3,4,5,6,7,4,8,9,10].

Default mode (--order filter-first):
  1. Subscribe (4×Proc_0x13)
  2. GetFilterStatus   ← first: device is clean, always succeeds
  3. GetSPL(12 params) ← second

Reversed mode (--order spl-first) — reproduces the issue:
  1. Subscribe
  2. GetSPL(12 params)
  3. GetFilterStatus

Logging levels
--------------
  --trace   Full TRACE output from bridge internals (frame-level, FrameCollector, etc.)
  --debug   DEBUG-level bridge output (medium verbosity)
  default   Only bridge errors (WARNING); probe itself always logs at INFO

Usage
-----
  # Test the fix (default):
  python tools/getspl-filter-probe.py

  # Full TRACE capture to file:
  python tools/getspl-filter-probe.py --trace --log-file /tmp/probe-trace.log

  # Reproduce the issue (spl-first):
  python tools/getspl-filter-probe.py --order spl-first --trace --log-file /tmp/probe-spl-first.log

  # Debug level (less verbose than TRACE):
  python tools/getspl-filter-probe.py --debug
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
# Constants
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

# ---------------------------------------------------------------------------
# Main probe
# ---------------------------------------------------------------------------

async def probe(args, log):
    cfg_host, cfg_port, cfg_psk, cfg_dev = _load_config(args.config)
    host   = args.esphome_host or cfg_host
    port   = args.esphome_port or cfg_port
    psk    = args.esphome_psk  or cfg_psk
    device = args.device       or cfg_dev

    if not device:
        print("ERROR: No device address. Provide --device or set [BLE] device_id in config.ini.")
        return 1

    order_desc = ('GetFilterStatus → GetSPL  [fix — expect both ✅]'
                  if args.order == 'filter-first' else
                  'GetSPL → GetFilterStatus  [issue repro — expect GetFilterStatus ❌]')
    print(f"\n=== getspl-filter-probe ===")
    print(f"Order:  {order_desc}")
    print(f"Device: {device}" + (f" via {host}:{port}" if host else " via local BLE"))
    if args.trace:
        print("Level:  TRACE (full bridge internals — see log file or scroll up)")
    elif args.debug:
        print("Level:  DEBUG")
    print()

    connector   = BluetoothLeConnector(esphome_host=host, esphome_port=port, esphome_noise_psk=psk)
    base_client = AquaCleanBaseClient(connector)

    log.info("Connecting to %s...", device)
    await base_client.connect_async(device)
    log.info("Connected. Subscribing...")
    await base_client.subscribe_notifications_async()
    log.info("Subscribed.")
    print("Connected and subscribed.\n")

    spl_result    = None
    spl_error     = None
    filter_result = None
    filter_error  = None

    async def do_spl():
        nonlocal spl_result, spl_error
        print(f"GetSPL (proc 0x0D), params {IPHONE_PARAMS} ...")
        log.info("→ Sending GetSPL with %d params: %s", len(IPHONE_PARAMS), IPHONE_PARAMS)
        t0 = datetime.datetime.now()
        try:
            spl_result = await base_client.get_system_parameter_list_async(IPHONE_PARAMS)
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            print(f"  ✅ OK  ({ms} ms)  a={spl_result.a}  len(data_array)={len(spl_result.data_array)}")
            log.info("← GetSPL OK  a=%d  data_array_len=%d  ms=%d",
                     spl_result.a, len(spl_result.data_array), ms)
        except Exception as e:
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            spl_error = e
            print(f"  ❌ FAIL ({ms} ms)  {type(e).__name__}: {e}")
            log.error("← GetSPL FAIL  ms=%d  %s: %s", ms, type(e).__name__, e)

    async def do_filter():
        nonlocal filter_result, filter_error
        print("GetFilterStatus (proc 0x59) ...")
        log.info("→ Sending GetFilterStatus")
        t0 = datetime.datetime.now()
        try:
            filter_result = await base_client.get_filter_status_async()
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            print(f"  ✅ OK  ({ms} ms)")
            log.info("← GetFilterStatus OK  ms=%d", ms)
        except BLEPeripheralTimeoutError as e:
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            filter_error = e
            print(f"  ❌ TIMEOUT ({ms} ms)")
            log.error("← GetFilterStatus TIMEOUT  ms=%d", ms)
        except Exception as e:
            ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
            filter_error = e
            print(f"  ❌ FAIL ({ms} ms)  {type(e).__name__}: {e}")
            log.error("← GetFilterStatus FAIL  ms=%d  %s: %s", ms, type(e).__name__, e)

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
        log.info("Disconnecting...")
        try:
            await base_client.disconnect()
        except Exception:
            pass
        log.info("Disconnected.")

    # -----------------------------------------------------------------------
    # Results summary
    # -----------------------------------------------------------------------
    print()
    print("=== RESULTS ===")
    if spl_result is not None:
        print(f"GetSPL:          ✅  a={spl_result.a}, len(data_array)={len(spl_result.data_array)}")
        unique_params = list(dict.fromkeys(IPHONE_PARAMS))
        for p in unique_params:
            idx = IPHONE_PARAMS.index(p)
            val = spl_result.data_array[idx] if idx < len(spl_result.data_array) else '?'
            name = PARAM_NAMES.get(p, f'param_{p}')
            print(f"  [{p:2d}] {name:<40s} = {val}")
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
        print(f"GetFilterStatus: ❌  {type(filter_error).__name__}")

    print()
    if args.order == 'filter-first':
        if filter_error is None and spl_result is not None:
            print("✅  Both calls succeeded.")
        elif filter_error is not None:
            print("❌  GetFilterStatus failed even when called first.")
            print("    Run with --trace and --log-file to capture bridge frame-level details.")
    else:
        if filter_error is not None and isinstance(filter_error, BLEPeripheralTimeoutError):
            print("⚠   GetFilterStatus timed out after GetSPL (issue reproduced).")
        elif filter_error is None:
            print("ℹ   GetFilterStatus succeeded despite spl-first order.")

    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        prog='getspl-filter-probe',
        description='Probe GetFilterStatus + GetSPL ordering.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
The iPhone app sends GetSPL with [0,1,2,3,4,5,6,7,4,8,9,10] and GetFilterStatus
works fine. If GetFilterStatus fails in our bridge, the root cause is in how we
consume the GetSPL response — not in the device. Run with --trace to diagnose.

See memory/getspl-getfilterstatus-ordering.md for full analysis.
""")
    p.add_argument('--order', choices=['filter-first', 'spl-first'], default='filter-first',
                   help='filter-first (default) or spl-first (issue repro)')
    p.add_argument('--device',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--trace', action='store_true',
                   help='TRACE-level bridge logging: full frame-level details from FrameCollector etc.')
    p.add_argument('--debug', action='store_true',
                   help='DEBUG-level bridge logging: medium verbosity')
    p.add_argument('--log-file', metavar='PATH',
                   help='Write all output to FILE in addition to stdout')
    p.add_argument('--esphome-host', metavar='HOST')
    p.add_argument('--esphome-port', type=int, metavar='PORT')
    p.add_argument('--esphome-psk',  metavar='PSK')
    p.add_argument('--config', metavar='PATH',
                   help='Path to config.ini (default: repo root config.ini)')
    args = p.parse_args()

    # Determine bridge log level
    if args.trace:
        bridge_level = logging.TRACE
    elif args.debug:
        bridge_level = logging.DEBUG
    else:
        bridge_level = logging.WARNING

    # Handlers
    fmt = '%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s'
    handlers = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
        print(f"Logging to: {args.log_file}")

    # Root logger at INFO so probe's own messages always show
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format=fmt, datefmt='%H:%M:%S')

    # Bridge internals: set to bridge_level (may be higher/lower than INFO)
    for pkg in ('aquaclean_console_app', 'aioesphomeapi', 'bleak'):
        logging.getLogger(pkg).setLevel(bridge_level)

    log = logging.getLogger('getspl-filter-probe')
    log.setLevel(logging.INFO)

    return asyncio.run(probe(args, log))


if __name__ == '__main__':
    sys.exit(main())
