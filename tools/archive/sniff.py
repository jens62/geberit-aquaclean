#!/usr/bin/env python3
# ARCHIVED — dead end. nrf-ble-analyze.py + Wireshark pcapng is the correct approach.
"""
Geberit AquaClean — BLE ATT sniffer
====================================

Uses tshark + the nrfutil-ble-sniffer extcap shim (installed by
`nrfutil ble-sniffer bootstrap`) to capture the GATT session between the
Geberit Home App and the toilet.

This is the correct approach for the modern nrfutil toolchain:
- nrfutil bootstrap installs a compiled Rust shim into Wireshark's extcap dir
- tshark calls the shim natively, which handles the sendFollow() timing correctly
- The older Python SnifferAPI is incompatible with firmware v4.x from nrfutil

Prerequisites
-------------
  nrfutil ble-sniffer bootstrap   (run once)
  tshark                          (installed with Wireshark)

Usage
-----
  python3 sniff.py                          # auto-detect interface + port
  python3 sniff.py --port /dev/tty.usbmodemXXXX
  python3 sniff.py --output-dir ~/Desktop
  python3 sniff.py --list                   # show available tshark interfaces
  python3 sniff.py --loop                   # restart after each session
"""

import argparse
import glob
import os
import platform
import re
import subprocess
import sys
import time
from datetime import datetime

# ── constants ────────────────────────────────────────────────────────────────

TARGET_MAC     = "38:ab:41:2a:0d:67"
TARGET_ADDR_TYPE = "public"          # Mera Comfort uses a stable public address

TSHARK_CANDIDATES = [
    "/Applications/Wireshark.app/Contents/MacOS/tshark",
    "tshark",           # on PATH (Linux / Windows)
    r"C:\Program Files\Wireshark\tshark.exe",
]

OUTPUT_DIR_DEFAULT = os.path.expanduser("~/Downloads")


# ── helpers ───────────────────────────────────────────────────────────────────

def find_tshark():
    for path in TSHARK_CANDIDATES:
        try:
            subprocess.run([path, "--version"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           check=True)
            return path
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    sys.exit(
        "ERROR: tshark not found.\n"
        "  Install Wireshark from https://www.wireshark.org/ and try again."
    )


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
                text=True, stderr=subprocess.DEVNULL)
            ports = re.findall(r"COM\d+", out)
        except Exception:
            ports = []
    else:
        ports = []

    if not ports:
        sys.exit(
            "ERROR: nRF52840 dongle not found.\n"
            "  Is it plugged in?  Use --port to specify manually."
        )
    if len(ports) > 1:
        print(f"  Multiple ports: {ports} — using {ports[0]}")
    return ports[0]


def list_interfaces(tshark):
    """Print all tshark interfaces and highlight any nRF Sniffer entry."""
    result = subprocess.run(
        [tshark, "-D"],
        capture_output=True, text=True
    )
    lines = (result.stdout + result.stderr).splitlines()
    print("Available tshark interfaces:")
    for line in lines:
        marker = "  ← nRF Sniffer" if re.search(r"nrf|sniffer|nRF", line, re.I) else ""
        print(f"  {line}{marker}")


def find_nrf_interface(tshark):
    """
    Return the tshark interface name/number for the nRF Sniffer extcap,
    or None if not found.
    """
    result = subprocess.run(
        [tshark, "-D"],
        capture_output=True, text=True
    )
    for line in (result.stdout + result.stderr).splitlines():
        if re.search(r"nrf|sniffer", line, re.I):
            # tshark -D output: "3. nRF Sniffer for Bluetooth LE ..."
            m = re.match(r"(\d+)\.", line)
            if m:
                return m.group(1)   # use number — avoids quoting issues
    return None


def extcap_tool_name(tshark):
    """
    Derive the extcap tool key used in tshark -o options.
    e.g. interface "nRF Sniffer for Bluetooth LE" → tool key "nrfutil-ble-sniffer-shim"
    Inspect the shim config file if present.
    """
    config_paths = [
        os.path.expanduser("~/.local/lib/wireshark/extcap/nrfutil-ble-sniffer-shim-config.json"),
        os.path.expanduser("~/Library/Application Support/Wireshark/extcap/nrfutil-ble-sniffer-shim-config.json"),
    ]
    for p in config_paths:
        if os.path.isfile(p):
            import json
            with open(p) as f:
                cfg = json.load(f)
            # The config typically has "tool" or "extcap_tool" key
            return cfg.get("tool", cfg.get("extcap_tool", "nrfutil-ble-sniffer-shim"))
    return "nrfutil-ble-sniffer-shim"


# ── capture ───────────────────────────────────────────────────────────────────

def run_capture(tshark, iface, tool_key, port, output_file):
    """
    Run tshark with the nRF Sniffer extcap, filtering to the Geberit MAC.

    tshark extcap device selection uses -o:
      extcap.<tool_name>.device=<mac> <addr_type>

    The extcap shim handles sendFollow() timing correctly — this is why
    the Wireshark GUI works.  tshark uses the same shim code path.
    """
    device_filter = f"{TARGET_MAC} {TARGET_ADDR_TYPE}"

    # Build the tshark -o option key.  The exact key depends on the shim's
    # extcap tool name; try both the config-derived name and the raw filename.
    opt_key = f"extcap.{tool_key}.device"

    cmd = [
        tshark,
        "-i", iface,
        "-o", f"{opt_key}={device_filter}",
        "-w", output_file,
    ]

    print(f"  Interface : {iface}")
    print(f"  Device    : {device_filter}")
    print(f"  Output    : {output_file}")
    print(f"  Command   : {' '.join(cmd)}")
    print()
    print("  ┌─────────────────────────────────────────────────────┐")
    print("  │   OPEN THE GEBERIT HOME APP ON YOUR PHONE NOW        │")
    print("  │   (the extcap shim is already armed and waiting)     │")
    print("  └─────────────────────────────────────────────────────┘")
    print("  Ctrl-C to stop capture.\n")

    try:
        subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        pass

    print(f"\n  Saved → {output_file}")
    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Capture Geberit AquaClean BLE ATT traffic via tshark extcap",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "device_name", nargs="?", default="Geberit AC PRO",
        help='BLE device name (informational, e.g. "Geberit AC PRO")',
    )
    parser.add_argument("--list",       action="store_true",
                        help="List all tshark interfaces and exit")
    parser.add_argument("--loop",       action="store_true",
                        help="Restart after each session")
    parser.add_argument("--port",       help="Serial port of nRF52840 (auto-detected if omitted)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR_DEFAULT,
                        help=f"Output directory (default: {OUTPUT_DIR_DEFAULT})")
    args = parser.parse_args()

    tshark = find_tshark()

    if args.list:
        list_interfaces(tshark)
        return

    iface = find_nrf_interface(tshark)
    if iface is None:
        print("ERROR: No nRF Sniffer interface found in tshark -D output.")
        print()
        print("Possible causes:")
        print("  • nrfutil ble-sniffer bootstrap has not been run")
        print("  • The extcap shim is not in Wireshark's extcap directory")
        print("  • The extcap shim crashes on this OS/Wireshark version")
        print("    (known issue: nrfutil v0.19.0 + macOS Sequoia + Wireshark ≥4.6)")
        print()
        print("Run:  nrfutil ble-sniffer bootstrap")
        print("Then: python3 sniff.py --list   to verify the interface appears")
        sys.exit(1)

    tool_key = extcap_tool_name(tshark)
    port     = args.port or find_sniffer_port()
    os.makedirs(args.output_dir, exist_ok=True)

    session = 0
    while True:
        session += 1
        ts          = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = os.path.join(args.output_dir, f"geberit-{ts}.pcapng")

        if session == 1:
            print(f"tshark        : {tshark}")
            print(f"nRF interface : {iface}")
            print(f"extcap tool   : {tool_key}")
            print(f"Target MAC    : {TARGET_MAC} ({TARGET_ADDR_TYPE})")
            print(f"Output dir    : {args.output_dir}")
            print()

        print(f"[Session {session}]  Make sure the toilet is powered and NOT")
        print( "  already connected to the app, then press Enter…")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            break

        run_capture(tshark, iface, tool_key, port, output_file)

        if not args.loop:
            break

        print("\n  Restarting in 3 s (Ctrl-C to quit)…\n")
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
