#!/usr/bin/env python3
"""
Geberit AquaClean — BLE ATT sniffer
====================================

Captures the GATT session between the Geberit Home App and the toilet
using an nRF52840 USB dongle running the Nordic BLE sniffer firmware.

The script prompts you to open the app at exactly the right moment —
after the sniffer has confirmed the device is advertising and the follow
request has been armed.

Platform support
----------------
  macOS Intel / Apple Silicon  /dev/tty.usbmodem*   nrfutil aarch64/x86_64-apple-darwin
  Windows 10 x86_64            COMxx                nrfutil x86_64-pc-windows-msvc
  Linux x86_64                 /dev/ttyACM*         nrfutil x86_64-unknown-linux-gnu

  Raspberry Pi / Linux ARM64   NOT supported — no nrfutil-ble-sniffer
                                ARM64 binary (Nordic confirmed).

nrfutil v0.19.0 follow strategy
--------------------------------
  --follow <MAC>       : timing bug — sends follow 28 ms before the device
                         appears; is_followed stays false; no data captured.
  --follow-by-name     : nrfutil must receive a SCAN_RSP matching the name
                         before it can register the follow → race condition
                         cannot fire.  Used here with --scan-follow-rsp and
                         --timeout 30000 (default 500 ms too short).

  Wireshark extcap shim is broken on macOS v0.19.0 (Nordic DevZone #127996).
  This script calls the sniff subcommand directly — not affected.

Usage
-----
  python3 sniff.py "Geberit AC PRO"
  python3 sniff.py "Geberit AC PRO" --loop
  python3 sniff.py "Geberit AC PRO" --port /dev/tty.usbmodemXXXX
  python3 sniff.py "Geberit AC PRO" --output-dir ~/Desktop
  python3 sniff.py "Geberit AC PRO" --mac          # fallback: two-phase MAC follow

Install
-------
  macOS / Linux : curl -L https://files.nordicsemi.com/.../nrfutil -o ~/.nrfutil/bin/nrfutil
                  nrfutil install ble-sniffer
  Windows       : download nrfutil.exe from Nordic, then:
                  nrfutil.exe install ble-sniffer
"""

import argparse
import glob
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime

# ── constants ────────────────────────────────────────────────────────────────

# nrfutil-ble-sniffer binary location per platform
_SNIFFER_BIN = {
    "Darwin":  os.path.expanduser("~/.nrfutil/bin/nrfutil-ble-sniffer"),
    "Linux":   os.path.expanduser("~/.nrfutil/bin/nrfutil-ble-sniffer"),
    "Windows": os.path.expandvars(r"%LOCALAPPDATA%\nrfutil\bin\nrfutil-ble-sniffer.exe"),
}

# Mera Comfort public BLE address (used only for --mac fallback mode)
MERA_COMFORT_MAC = "38:ab:41:2a:0d:67"
# nrfutil strips leading zeros from hex octets: 0d → d (v0.19.0 display bug)
MERA_COMFORT_MAC_NORM = "38:ab:41:2a:d:67"

PRESCAN_TIMEOUT = 30   # seconds to wait for device in pre-scan phase
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
        # Query COM ports via PowerShell without requiring pyserial
        try:
            out = subprocess.check_output(
                ["powershell", "-Command",
                 "Get-PnpDevice -Class Ports -Status OK | "
                 "Select-Object -ExpandProperty FriendlyName"],
                text=True, stderr=subprocess.DEVNULL
            )
            # Extract COMxx from lines like "USB Serial Device (COM3)"
            import re
            ports = re.findall(r"COM\d+", out)
        except Exception:
            ports = []
    else:
        sys.exit(f"ERROR: Unsupported platform: {system}")

    if not ports:
        hint = "COMxx in Device Manager" if system == "Windows" else "/dev/ttyACM0 or /dev/tty.usbmodem*"
        sys.exit(
            f"ERROR: nRF52840 dongle not found.\n"
            f"  Is it plugged in?  Try --port {hint}"
        )
    if len(ports) > 1:
        print(f"Multiple ports found: {ports} — using {ports[0]}")
    return ports[0]


# ── phase 1: pre-scan ─────────────────────────────────────────────────────────

def prescan_until_device_seen(bin_path, port, device_name):
    """
    Scan in advertising-only JSON mode until either the device name or the
    known Mera Comfort MAC appears.  Kills the scan and returns True/False.

    Why: even with --follow-by-name the sniffer benefits from confirming the
    device is actively advertising before arming the follow, so we can print
    "Open the app NOW" at exactly the right moment.
    """
    cmd = [
        bin_path, "sniff",
        "--port", port,
        "--only-advertising",
        "--json", "--skip-overhead",
    ]
    print(f"  Pre-scan: waiting for '{device_name}' to appear "
          f"(up to {PRESCAN_TIMEOUT}s)…")
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
    )
    found = False
    deadline = time.time() + PRESCAN_TIMEOUT
    try:
        for raw in proc.stdout:
            if time.time() > deadline:
                break
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
                data  = event.get("data", {})
                name  = data.get("name") or ""
                addr  = (data.get("address", {}) or {}).get("address", "")
                if (name.lower() == device_name.lower()
                        or addr.lower() in (MERA_COMFORT_MAC.lower(),
                                            MERA_COMFORT_MAC_NORM.lower())):
                    rssi = data.get("rssi", "?")
                    print(f"  Device found (RSSI {rssi} dBm).")
                    found = True
                    break
            except (json.JSONDecodeError, KeyError, AttributeError):
                pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    return found


# ── phase 2: follow capture ───────────────────────────────────────────────────

def capture_session(bin_path, port, device_name, output_file, use_mac):
    """
    Arm the sniffer (phase 1 pre-scan already confirmed the device is up),
    then prompt the user to open the app.  Returns True on clean exit,
    None on Ctrl-C.
    """
    if use_mac:
        cmd = [
            bin_path, "sniff",
            "--port", port,
            "--follow", f"{MERA_COMFORT_MAC} public",
            "--output-pcap-file", output_file,
        ]
        mode_desc = f"follow-by-MAC ({MERA_COMFORT_MAC})"
    else:
        cmd = [
            bin_path, "sniff",
            "--port", port,
            "--follow-by-name", device_name,
            "--scan-follow-rsp",   # fetch SCAN_RSP so nrfutil resolves the name
            "--timeout", "30000",  # default 500 ms too short; 30 s gives margin
            "--output-pcap-file", output_file,
        ]
        mode_desc = f"follow-by-name ('{device_name}')"

    print(f"\n  Mode   : {mode_desc}")
    print(f"  Output : {output_file}")
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print(f"  │  OPEN THE GEBERIT HOME APP ON YOUR PHONE NOW        │")
    print("  └─────────────────────────────────────────────────────┘")
    print("  Ctrl-C to stop capture.\n")

    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print(f"\n  Capture stopped.")
        return None
    return True


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
        except KeyboardInterrupt:
            print("\nAborted.")
            break

        # Phase 1: confirm device is advertising
        seen = prescan_until_device_seen(bin_path, port, device_name)
        if not seen:
            print(f"\n  WARNING: '{device_name}' not seen within {PRESCAN_TIMEOUT}s.")
            print("  • Is the toilet powered?")
            print("  • Is it already connected to the Geberit Home App?")
            print("  • Is the dongle plugged in?")
            if not loop:
                break
            print("  Retrying in 5s…\n")
            try:
                time.sleep(5)
            except KeyboardInterrupt:
                break
            continue

        # Phase 2: follow capture
        result = capture_session(bin_path, port, device_name, output_file, use_mac)

        if result is None:   # Ctrl-C
            print(f"  Saved → {output_file}")
            break

        print(f"  Saved → {output_file}")

        if not loop:
            break

        print("\n  Restarting for next session in 3s (Ctrl-C to quit)…\n")
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
        help='BLE device name shown in phone Bluetooth settings, e.g. "Geberit AC PRO"',
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
