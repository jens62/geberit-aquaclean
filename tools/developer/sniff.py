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
                         cannot fire.  Used here with --scan-follow-rsp.
  --skip-overhead      : suppresses DEVICE_ADDED events on stdout — do NOT
                         use it; without it all JSON events flow through.
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

# Mera Comfort public BLE address — used only for --mac fallback and match highlighting
MERA_COMFORT_MAC      = "38:ab:41:2a:0d:67"
MERA_COMFORT_MAC_NORM = "38:ab:41:2a:d:67"   # nrfutil strips leading zeros (0d → d)

OUTPUT_DIR_DEFAULT = os.path.expanduser("~/Downloads")


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


def find_sniffer_port():
    system = platform.system()
    if system == "Darwin":
        ports = glob.glob("/dev/tty.usbmodem*")
    elif system == "Linux":
        ports = glob.glob("/dev/ttyACM*")
    elif system == "Windows":
        try:
            import re
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
    """True if this device entry matches either our known MAC or the requested name."""
    addr_match = addr.lower() in (MERA_COMFORT_MAC.lower(), MERA_COMFORT_MAC_NORM.lower())
    name_match = bool(name) and name.lower() == device_name.lower()
    return addr_match or name_match


def extract_device_fields(event):
    """
    nrfutil --json without --skip-overhead wraps events in an outer object.
    Recursively search for the dict that contains address + rssi + is_followed.
    """
    if isinstance(event, dict):
        if "rssi" in event and "is_followed" in event:
            return event
        for v in event.values():
            found = extract_device_fields(v)
            if found is not None:
                return found
    return None


# ── capture ───────────────────────────────────────────────────────────────────

def run_capture(bin_path, port, device_name, output_file, use_mac):
    """
    Start the sniffer, stream JSON events from stdout, print each discovered
    device, and print "OPEN APP NOW" when the target is followed.

    Why --json without --skip-overhead:
      --skip-overhead suppresses DEVICE_ADDED events on stdout; they only
      appear in ~/.nrfutil/logs/nrfutil-ble-sniffer.log.  Without the flag,
      every JSON event (including DEVICE_ADDED) flows to stdout so we can
      monitor device discovery in real time.
    """
    if use_mac:
        cmd = [
            bin_path, "sniff",
            "--port", port,
            "--follow", f"{MERA_COMFORT_MAC} public",
            "--output-pcap-file", output_file,
            "--json",
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
            "--json",
        ]
        mode = f"follow-by-name ('{device_name}')"

    print(f"  Mode   : {mode}")
    print(f"  Output : {output_file}")
    print(f"  Scanning — devices found so far:\n")

    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1
    )

    open_app_printed = False

    try:
        for raw in proc.stdout:
            raw = raw.strip()
            if not raw:
                continue

            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            dev = extract_device_fields(event)
            if dev is None:
                continue

            addr        = (dev.get("address") or {}).get("address", "")
            name        = dev.get("name") or ""
            rssi        = dev.get("rssi", "?")
            is_followed = dev.get("is_followed", False)

            target = is_target(addr, device_name, name)
            marker = "  ← TARGET" if target else ""
            label  = f" ({name})" if name else ""
            print(f"    {addr}{label}  RSSI {rssi} dBm{marker}")

            if (is_followed or target) and not open_app_printed:
                open_app_printed = True
                print()
                print("  ┌─────────────────────────────────────────────────────┐")
                print("  │   OPEN THE GEBERIT HOME APP ON YOUR PHONE NOW        │")
                print("  └─────────────────────────────────────────────────────┘")
                print("  Ctrl-C to stop capture.\n")
                # Drain stdout in background so the pipe buffer never fills
                threading.Thread(
                    target=lambda: [_ for _ in proc.stdout],
                    daemon=True
                ).start()
                proc.wait()
                return True

    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not open_app_printed:
        print(f"\n  Stopped — target '{device_name}' was not found.")
        print("  Check: is the toilet powered?  Not connected to another device?")
    else:
        print(f"\n  Capture stopped.")
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
