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

nrfutil v0.19.0 behaviour
--------------------------
  --log-output stdout  GLOBAL flag — must appear BEFORE the `sniff` subcommand.
                       Without it, DEVICE_ADDED events go only to the log file.
                       With it, stdout receives JSON Lines:
                         {"type":"log","data":{"level":"INFO",
                          "message":"Device added: {...}","timestamp":"..."}}
                       The `message` value contains an embedded JSON payload.

  --follow-by-name     Completely broken — `name` is always null in every
                       DEVICE_ADDED event, so nrfutil can never match it and
                       logs "WARN: Can't send follow request call".  Never used.

  --follow <MAC>       Sends a follow request to the sniffer firmware.  Known
                       timing bug: if the follow request is sent before the
                       device appears in nrfutil's internal list, nrfutil marks
                       is_followed=false and discards all connection data even
                       though the firmware may have captured it.
                       Workaround: retry the follow immediately if is_followed=false
                       when the target MAC is first seen — statistically the device
                       appears within 28 ms on subsequent fast retries.

  MAC normalisation    nrfutil strips leading zeros from hex octets in display:
                       38:ab:41:2a:0d:67 → 38:ab:41:2a:d:67.  Always compare
                       with zero-padding normalised to canonical form.

  Wireshark extcap     Broken on macOS v0.19.0 (Nordic DevZone #127996).
                       This script calls the sniff subcommand directly.

Usage
-----
  python3 sniff.py "Geberit AC PRO"
  python3 sniff.py "Geberit AC PRO" --loop
  python3 sniff.py "Geberit AC PRO" --port /dev/tty.usbmodemXXXX
  python3 sniff.py "Geberit AC PRO" --output-dir ~/Desktop
"""

import argparse
import glob
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime

# ── constants ────────────────────────────────────────────────────────────────

_SNIFFER_BIN = {
    "Darwin":  os.path.expanduser("~/.nrfutil/bin/nrfutil-ble-sniffer"),
    "Linux":   os.path.expanduser("~/.nrfutil/bin/nrfutil-ble-sniffer"),
    "Windows": os.path.expandvars(r"%LOCALAPPDATA%\nrfutil\bin\nrfutil-ble-sniffer.exe"),
}

# Mera Comfort public BLE address (canonical zero-padded form)
MERA_COMFORT_MAC = "38:ab:41:2a:0d:67"

OUTPUT_DIR_DEFAULT = os.path.expanduser("~/Downloads")

# Max retries when is_followed=false before giving up on a session
MAX_FOLLOW_RETRIES = 8


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


def normalize_mac(mac: str) -> str:
    """Canonical form: lowercase, each octet zero-padded to 2 chars."""
    return ":".join(p.zfill(2).lower() for p in mac.split(":"))


TARGET_MAC_NORM = normalize_mac(MERA_COMFORT_MAC)


def parse_device_added(line: str):
    """
    Parse a JSON Lines stdout event from nrfutil --log-output stdout --json.

    Expected format:
      {"type":"log","data":{"level":"INFO",
       "message":"Device added: {<embedded json>}","timestamp":"..."}}

    Returns (addr_norm, rssi, is_followed) or None if not a DEVICE_ADDED line.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None

    if obj.get("type") != "log":
        return None

    msg = obj.get("data", {}).get("message", "")
    if not msg.startswith("Device added: "):
        return None

    try:
        dev = json.loads(msg[len("Device added: "):])
    except json.JSONDecodeError:
        return None

    addr        = (dev.get("address") or {}).get("address", "")
    rssi        = dev.get("rssi", "?")
    is_followed = dev.get("is_followed", False)
    return normalize_mac(addr), rssi, is_followed


# ── one follow attempt ────────────────────────────────────────────────────────

def _attempt_follow(bin_path, port, output_file):
    """
    Start nrfutil with --follow MAC.  Stream stdout JSON Lines.
    Print every discovered device.

    Returns:
      "followed"  — target found with is_followed=true  → print OPEN APP NOW
      "retry"     — target found with is_followed=false → restart immediately
      "stopped"   — Ctrl-C or process exited cleanly
    """
    cmd = [
        bin_path,
        "--log-output", "stdout",   # GLOBAL flag — must be BEFORE the subcommand
        "--log-level",  "info",
        "--json",
        "sniff",
        "--port",            port,
        "--follow",          f"{MERA_COMFORT_MAC} public",
        "--scan-follow-rsp",
        "--output-pcap-file", output_file,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )

    result = "stopped"
    try:
        for raw in proc.stdout:
            parsed = parse_device_added(raw.strip())
            if parsed is None:
                continue

            addr_norm, rssi, is_followed = parsed
            is_tgt = (addr_norm == TARGET_MAC_NORM)
            marker = "  ← TARGET" if is_tgt else ""
            print(f"    {addr_norm}  RSSI {rssi} dBm{marker}", flush=True)

            if is_tgt:
                if is_followed:
                    result = "followed"
                else:
                    result = "retry"
                break   # stop reading; let caller decide

    except KeyboardInterrupt:
        result = "stopped"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    return result


# ── capture session ───────────────────────────────────────────────────────────

def run_capture(bin_path, port, output_file):
    """
    Retry _attempt_follow until is_followed=true or MAX_FOLLOW_RETRIES exceeded.
    Returns True if capture started, False otherwise.
    """
    print(f"  Output : {output_file}")
    print(f"  Scanning — devices found:\n")

    for attempt in range(1, MAX_FOLLOW_RETRIES + 1):
        if attempt > 1:
            print(f"\n  is_followed=false — retry {attempt}/{MAX_FOLLOW_RETRIES}…\n")

        result = _attempt_follow(bin_path, port, output_file)

        if result == "stopped":
            print("\n  Stopped.")
            return False

        if result == "followed":
            print()
            print("  ┌─────────────────────────────────────────────────────┐")
            print("  │   OPEN THE GEBERIT HOME APP ON YOUR PHONE NOW        │")
            print("  └─────────────────────────────────────────────────────┘")
            print("  Ctrl-C to stop capture.\n")

            # Continue capture — nrfutil is already running in _attempt_follow
            # but we terminated it above.  Restart for the actual capture phase.
            cmd = [
                bin_path,
                "--log-output", "stdout",
                "--log-level",  "info",
                "--json",
                "sniff",
                "--port",            port,
                "--follow",          f"{MERA_COMFORT_MAC} public",
                "--scan-follow-rsp",
                "--output-pcap-file", output_file,
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            try:
                proc.wait()
            except KeyboardInterrupt:
                proc.terminate()
                proc.wait(timeout=3)
            return True

        # result == "retry" — loop immediately

    print(f"\n  Could not get is_followed=true after {MAX_FOLLOW_RETRIES} attempts.")
    print("  Is the toilet powered?  Not connected to another device?")
    return False


# ── main loop ─────────────────────────────────────────────────────────────────

def run(port, output_dir, loop):
    bin_path = sniffer_bin()
    session  = 0

    while True:
        session += 1
        timestamp   = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = os.path.join(output_dir, f"geberit-{timestamp}.pcapng")

        if session == 1:
            print(f"nRF52840 port : {port}")
            print(f"Target MAC    : {MERA_COMFORT_MAC}")
            print(f"Output dir    : {output_dir}")
            print()

        print(f"[Session {session}]  Make sure the toilet is powered and NOT")
        print( "  already connected to the app, then press Enter…")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            break

        result = run_capture(bin_path, port, output_file)
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
        help='BLE name shown in phone Bluetooth settings (informational only, '
             'e.g. "Geberit AC PRO") — matching is by MAC address',
    )
    parser.add_argument(
        "--loop", action="store_true",
        help="Restart automatically after each session",
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
    run(port, args.output_dir, args.loop)


if __name__ == "__main__":
    main()
