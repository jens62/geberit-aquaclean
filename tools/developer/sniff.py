#!/usr/bin/env python3
"""
Geberit AquaClean — BLE ATT sniffer
====================================

Captures the GATT session between the Geberit Home App and the toilet
using an nRF52840 USB dongle running the Nordic BLE sniffer firmware.

The script shows every BLE device the dongle discovers, highlights the
target when it appears, and prints "OPEN APP NOW" at the exact moment the
sniffer has locked on and is ready to follow the connection.

Platform support
----------------
  macOS Intel / Apple Silicon  /dev/tty.usbmodem*   nrfutil aarch64/x86_64-apple-darwin
  Windows 10 x86_64            COMxx                nrfutil x86_64-pc-windows-msvc
  Linux x86_64                 /dev/ttyACM*         nrfutil x86_64-unknown-linux-gnu

  Raspberry Pi / Linux ARM64   NOT supported — no nrfutil-ble-sniffer
                                ARM64 binary (Nordic confirmed).

nrfutil v0.19.0 notes
----------------------
  --follow <MAC>       : timing bug — follow request sent 28 ms before the
                         device appears; is_followed stays false; no data.
  --follow-by-name     : nrfutil must receive a SCAN_RSP with the matching
                         name before registering the follow → race condition
                         cannot fire.  Used with --scan-follow-rsp.
  --json / --log-output stdout :  DEVICE_ADDED events do NOT appear on stdout
                         or stderr regardless of these flags — they only ever
                         appear in ~/.nrfutil/logs/nrfutil-ble-sniffer.log.
                         This script tails that file directly.
  --timeout 30000      : default 500 ms too short to catch the first SCAN_RSP.

  Wireshark extcap shim broken on macOS v0.19.0 (Nordic DevZone #127996).
  This script calls the sniff subcommand directly — not affected.

Usage
-----
  python3 sniff.py "Geberit AC PRO"
  python3 sniff.py "Geberit AC PRO" --loop
  python3 sniff.py "Geberit AC PRO" --port /dev/tty.usbmodemXXXX
  python3 sniff.py "Geberit AC PRO" --output-dir ~/Desktop
  python3 sniff.py "Geberit AC PRO" --mac   # fallback: two-phase MAC follow
"""

import argparse
import glob
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

# ── constants ────────────────────────────────────────────────────────────────

_SNIFFER_BIN = {
    "Darwin":  os.path.expanduser("~/.nrfutil/bin/nrfutil-ble-sniffer"),
    "Linux":   os.path.expanduser("~/.nrfutil/bin/nrfutil-ble-sniffer"),
    "Windows": os.path.expandvars(r"%LOCALAPPDATA%\nrfutil\bin\nrfutil-ble-sniffer.exe"),
}

_LOG_FILE = {
    "Darwin":  os.path.expanduser("~/.nrfutil/logs/nrfutil-ble-sniffer.log"),
    "Linux":   os.path.expanduser("~/.nrfutil/logs/nrfutil-ble-sniffer.log"),
    "Windows": os.path.expandvars(r"%LOCALAPPDATA%\nrfutil\logs\nrfutil-ble-sniffer.log"),
}

# Mera Comfort public BLE address — used for highlighting and --mac fallback
MERA_COMFORT_MAC      = "38:ab:41:2a:0d:67"
MERA_COMFORT_MAC_NORM = "38:ab:41:2a:d:67"   # nrfutil strips leading zeros (0d → d)

OUTPUT_DIR_DEFAULT = os.path.expanduser("~/Downloads")

# Matches:  ... Device added: {"address":{"address":"xx:xx","type":"..."},...}
_DEVICE_RE = re.compile(r"Device added: ({.+})")


# ── helpers ──────────────────────────────────────────────────────────────────

def sniffer_bin():
    path = _SNIFFER_BIN.get(platform.system())
    if not path or not os.path.isfile(path):
        sys.exit(
            f"ERROR: nrfutil-ble-sniffer not found at {path}\n"
            "Install with:  nrfutil install ble-sniffer\n"
            "(Raspberry Pi / Linux ARM64 is not supported by Nordic.)"
        )
    return path


def log_file():
    return _LOG_FILE.get(platform.system(),
                         os.path.expanduser("~/.nrfutil/logs/nrfutil-ble-sniffer.log"))


def find_sniffer_port():
    system = platform.system()
    if system == "Darwin":
        ports = glob.glob("/dev/tty.usbmodem*")
    elif system == "Linux":
        ports = glob.glob("/dev/ttyACM*")
    elif system == "Windows":
        try:
            out = subprocess.check_output(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class Ports -Status OK | "
                 "Select-Object -ExpandProperty FriendlyName"],
                text=True, stderr=subprocess.DEVNULL
            )
            ports = re.findall(r"COM\d+", out)
        except Exception:
            ports = []
    else:
        sys.exit(f"ERROR: Unsupported platform: {system}")

    if not ports:
        hint = "COMxx in Device Manager" if system == "Windows" else "/dev/ttyACM0 or /dev/tty.usbmodem*"
        sys.exit(
            "ERROR: nRF52840 dongle not found.\n"
            f"  Is it plugged in?  Try --port {hint}"
        )
    if len(ports) > 1:
        print(f"  Multiple ports: {ports} — using {ports[0]}")
    return ports[0]


def is_target(addr, device_name, name):
    addr_match = addr.lower() in (MERA_COMFORT_MAC.lower(), MERA_COMFORT_MAC_NORM.lower())
    name_match = bool(name) and name.lower() == device_name.lower()
    return addr_match or name_match


# ── log tailer ────────────────────────────────────────────────────────────────

def _tail_log(log_path, start_pos, device_name, found_event, stop_event):
    """
    Background thread: tail the nrfutil log file from start_pos, parse every
    'Device added: {json}' line, print it, and set found_event when the target
    appears.

    Why log file instead of stdout/stderr:
      DEVICE_ADDED events appear exclusively in ~/.nrfutil/logs/nrfutil-ble-sniffer.log
      regardless of --json, --log-output stdout, or --skip-overhead.  All three
      flags were tried and confirmed to not surface DEVICE_ADDED on stdout/stderr
      (the Geberit device appeared in the log within 100 ms in every session but
      the script never detected it via stdout parsing).
    """
    # Wait up to 2 s for nrfutil to create / start writing the log
    deadline = time.time() + 2.0
    while not os.path.isfile(log_path) and time.time() < deadline:
        time.sleep(0.05)

    if not os.path.isfile(log_path):
        print(f"  WARNING: log file not found at {log_path}")
        return

    try:
        with open(log_path, "r", errors="replace") as f:
            f.seek(start_pos)
            while not stop_event.is_set():
                line = f.readline()
                if not line:
                    time.sleep(0.05)
                    continue
                m = _DEVICE_RE.search(line)
                if not m:
                    continue
                try:
                    dev = json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue

                addr        = (dev.get("address") or {}).get("address", "")
                name        = dev.get("name") or ""
                rssi        = dev.get("rssi", "?")
                is_followed = dev.get("is_followed", False)

                target = is_target(addr, device_name, name)
                marker = "  ← TARGET" if target else ""
                label  = f" ({name})" if name else ""
                print(f"    {addr}{label}  RSSI {rssi} dBm{marker}", flush=True)

                if is_followed or target:
                    found_event.set()
    except Exception as e:
        print(f"  WARNING: log tail error: {e}")


# ── capture ───────────────────────────────────────────────────────────────────

def run_capture(bin_path, port, device_name, output_file, use_mac):
    if use_mac:
        cmd = [
            bin_path, "sniff",
            "--port", port,
            "--follow", f"{MERA_COMFORT_MAC} public",
            "--output-pcap-file", output_file,
        ]
        mode = f"follow-by-MAC ({MERA_COMFORT_MAC})"
    else:
        cmd = [
            bin_path, "sniff",
            "--port", port,
            "--follow-by-name", device_name,
            "--scan-follow-rsp",
            "--timeout", "30000",
            "--output-pcap-file", output_file,
        ]
        mode = f"follow-by-name ('{device_name}')"

    print(f"  Mode   : {mode}")
    print(f"  Output : {output_file}")
    print(f"  Scanning — devices found:\n")

    # Record log file position before starting so we only read new entries
    lf = log_file()
    start_pos = os.path.getsize(lf) if os.path.isfile(lf) else 0

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    found_event = threading.Event()
    stop_event  = threading.Event()

    tailer = threading.Thread(
        target=_tail_log,
        args=(lf, start_pos, device_name, found_event, stop_event),
        daemon=True,
    )
    tailer.start()

    open_app_printed = False
    try:
        while proc.poll() is None:
            if found_event.is_set() and not open_app_printed:
                open_app_printed = True
                print()
                print("  ┌─────────────────────────────────────────────────────┐")
                print("  │   OPEN THE GEBERIT HOME APP ON YOUR PHONE NOW        │")
                print("  └─────────────────────────────────────────────────────┘")
                print("  Ctrl-C to stop capture.\n")
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not open_app_printed:
        print(f"\n  Stopped — '{device_name}' was not found.")
        print("  Is the toilet powered?  Not connected to another device?")
        print(f"  Log file checked: {lf}")
    else:
        print("\n  Capture stopped.")
    return open_app_printed


# ── main loop ─────────────────────────────────────────────────────────────────

def run(device_name, port, output_dir, loop, use_mac):
    bin_path = sniffer_bin()
    session  = 0

    while True:
        session += 1
        timestamp   = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = os.path.join(output_dir, f"geberit-{timestamp}.pcapng")

        if session == 1:
            print(f"nRF52840 port : {port}")
            print(f"Device name   : {device_name}")
            print(f"Output dir    : {output_dir}")
            print(f"Log file      : {log_file()}")
            print()

        print(f"[Session {session}]  Make sure the toilet is powered and NOT")
        print( "  already connected to the app, then press Enter…")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            break

        result = run_capture(bin_path, port, device_name, output_file, use_mac)

        if result:
            print(f"  Saved → {output_file}")

        if not loop:
            break

        print("\n  Restarting for next session in 3 s (Ctrl-C to quit)…\n")
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            break


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Capture Geberit AquaClean BLE ATT traffic",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "device_name",
        help='BLE name shown in phone Bluetooth settings, e.g. "Geberit AC PRO"',
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Restart automatically after each session",
    )
    parser.add_argument(
        "--mac", action="store_true",
        help=f"Fallback: two-phase MAC follow ({MERA_COMFORT_MAC}) instead of name follow",
    )
    parser.add_argument(
        "--port",
        help="Serial port of the nRF52840 dongle (auto-detected if omitted)",
    )
    parser.add_argument(
        "--output-dir", default=OUTPUT_DIR_DEFAULT,
        help=f"Directory for .pcapng files (default: {OUTPUT_DIR_DEFAULT})",
    )
    args = parser.parse_args()

    port = args.port or find_sniffer_port()
    os.makedirs(args.output_dir, exist_ok=True)
    run(args.device_name, port, args.output_dir, args.loop, args.mac)


if __name__ == "__main__":
    main()
