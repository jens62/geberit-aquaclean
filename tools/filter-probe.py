#!/usr/bin/env python3
"""
filter-probe.py — GetFilterStatus only
=======================================

Connects to the Geberit device, subscribes notifications, and calls
GetFilterStatus (proc 0x59).  No GetSPL or any other call is made.

Use this to:
  - Read raw filter data and all record IDs without side-effects
  - Verify the filter counter after a reset
  - Map unknown record IDs by comparing before/after a filter reset

Usage
-----
  python tools/filter-probe.py
  python tools/filter-probe.py --trace
  python tools/filter-probe.py --trace --log-file /tmp/filter-probe.log
  python tools/filter-probe.py --esphome-host 192.168.0.50
  python tools/filter-probe.py --device 38:AB:00:00:00:01
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
# Probe
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

    print(f"\n=== filter-probe ===")
    print(f"Device: {device}" + (f"  via {host}:{port}" if host else "  via local BLE"))
    print()

    connector   = BluetoothLeConnector(esphome_host=host, esphome_port=port, esphome_noise_psk=psk)
    base_client = AquaCleanBaseClient(connector)

    log.info("Connecting to %s...", device)
    await base_client.connect_async(device)
    log.info("Connected. Subscribing...")
    await base_client.subscribe_notifications_async()
    log.info("Subscribed.")
    print("Connected and subscribed.\n")

    result = None
    error  = None

    print("GetFilterStatus (proc 0x59) ...")
    log.info("→ Sending GetFilterStatus")
    t0 = datetime.datetime.now()
    try:
        result = await base_client.get_filter_status_async()
        ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
        print(f"  ✅ OK  ({ms} ms)")
        log.info("← GetFilterStatus OK  ms=%d", ms)
    except BLEPeripheralTimeoutError as e:
        ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
        error = e
        print(f"  ❌ TIMEOUT ({ms} ms)")
        log.error("← GetFilterStatus TIMEOUT  ms=%d", ms)
    except Exception as e:
        ms = int((datetime.datetime.now() - t0).total_seconds() * 1000)
        error = e
        print(f"  ❌ FAIL ({ms} ms)  {type(e).__name__}: {e}")
        log.error("← GetFilterStatus FAIL  ms=%d  %s: %s", ms, type(e).__name__, e)
    finally:
        log.info("Disconnecting...")
        try:
            await base_client.disconnect()
        except Exception:
            pass
        log.info("Disconnected.")

    # -----------------------------------------------------------------------
    # Results
    # -----------------------------------------------------------------------
    print()
    print("=== RESULTS ===")
    if result is not None:
        days  = result.get("days_until_filter_change")
        reset = result.get("last_filter_reset")
        next_ = result.get("next_filter_change")
        count = result.get("filter_reset_count")
        raws  = result.get("raw_records", {})

        print(f"  days_until_filter_change : {days}")
        print(f"  last_filter_reset        : {reset}"
              + (f"  ({datetime.datetime.fromtimestamp(reset)})" if reset else ""))
        print(f"  next_filter_change       : {next_}"
              + (f"  ({datetime.datetime.fromtimestamp(next_)})" if next_ else ""))
        print(f"  filter_reset_count       : {count}")
        print()
        print("  Raw records (all returned IDs):")
        for rec_id in sorted(raws):
            val = raws[rec_id]
            print(f"    ID {rec_id:3d} (0x{rec_id:02x}) = {val:10d}  (0x{val:08x})")
    else:
        print(f"  ❌ {type(error).__name__}: {error}")

    print()
    return 0 if result is not None else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    p = argparse.ArgumentParser(
        prog='filter-probe',
        description='Call GetFilterStatus (proc 0x59) only — no GetSPL.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument('--device', metavar='MAC',
                   help='BLE MAC address (default: [BLE] device_id in config.ini)')
    p.add_argument('--trace', action='store_true',
                   help='TRACE-level bridge logging: full frame-level details')
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

    bridge_level = logging.TRACE if args.trace else (logging.DEBUG if args.debug else logging.WARNING)

    fmt = '%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s'
    handlers = [logging.StreamHandler(sys.stdout)]
    if args.log_file:
        handlers.append(logging.FileHandler(args.log_file))
        print(f"Logging to: {args.log_file}")

    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format=fmt, datefmt='%H:%M:%S')

    for pkg in ('aquaclean_console_app', 'aioesphomeapi', 'bleak'):
        logging.getLogger(pkg).setLevel(bridge_level)

    log = logging.getLogger('filter-probe')
    log.setLevel(logging.INFO)

    return asyncio.run(probe(args, log))


if __name__ == '__main__':
    sys.exit(main())
