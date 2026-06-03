#!/usr/bin/env python3
"""
find-geberit-remote.py — Find the BLE address of a Geberit remote control
==========================================================================

Analyses an nRF Sniffer for Bluetooth LE capture (.pcapng) to identify the
remote control paired with a Geberit AquaClean toilet.

The remote never advertises — it acts as BLE central and sends CONNECT_IND
frames directly to the toilet's known MAC.  This script finds all CONNECT_IND
frames targeting the toilet and extracts the initiator (remote) address.

Requires: tshark on PATH (ships with Wireshark).

How to capture for MuusLee
---------------------------
1. Flash nRF Sniffer firmware on an nRF52840 dongle (Nordic / SEGGER):
     https://github.com/NordicSemiconductor/nRF-Sniffer-for-Bluetooth-LE
2. Open Wireshark, select the sniffer as the capture interface.
3. Press a button on the remote to trigger a BLE connection to the toilet.
   (Lid open/close works; pressing multiple times gives more frames.)
4. After 60–120 seconds, File → Save As → save as .pcapng.
5. Run this script.

Usage
-----
  python tools/find-geberit-remote.py capture.pcapng
  python tools/find-geberit-remote.py capture.pcapng --toilet 38:AB:41:2A:0D:67
  python tools/find-geberit-remote.py capture.pcapng --config /path/to/config.ini
"""

import argparse
import configparser
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Known Texas Instruments OUI prefixes (Geberit uses TI BLE chips).
# The toilet's OUI is 38:AB:41; remotes seen so far use B0:10:A0.
# Both are TI-assigned.  The list is a hint only — a non-TI remote would
# still be reported; it just won't be tagged "← likely Geberit".
_TI_OUIS = {
    "38:ab:41", "b0:10:a0", "00:18:da", "34:b1:f7", "04:a3:16",
    "00:17:e9", "00:24:d6", "a4:34:d9", "98:5d:ad", "d0:b5:c2",
}

# OUIs that are definitely NOT a Geberit remote (skip tagging but still show).
_SKIP_OUIS = {
    "00:17:f2",  # Apple
    "dc:a9:04",  # Apple
    "f8:ff:c2",  # Apple
    # add more as needed
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_tshark() -> str:
    path = shutil.which("tshark")
    if not path:
        sys.exit(
            "Error: tshark not found on PATH.\n"
            "Install Wireshark: https://www.wireshark.org/\n"
            "  macOS:  brew install wireshark\n"
            "  Ubuntu: sudo apt install tshark"
        )
    return path


def _read_toilet_mac(config_path: Path) -> str | None:
    if not config_path.exists():
        return None
    cfg = configparser.ConfigParser(inline_comment_prefixes=("#",))
    cfg.read(config_path)
    val = cfg.get("BLE", "device_id", fallback="").strip()
    return val or None


def _oui(mac: str) -> str:
    return mac[:8].lower()


def _tag(mac: str) -> str:
    o = _oui(mac)
    if o in _TI_OUIS:
        return "Texas Instruments  ← likely Geberit"
    return ""


def _find_initiators(pcapng: Path, toilet_mac: str, tshark: str) -> Counter:
    """
    Run tshark once and return a Counter of initiator MACs seen in
    CONNECT_IND frames whose advertising address matches toilet_mac.
    """
    cmd = [
        tshark, "-r", str(pcapng),
        "-T", "fields",
        "-e", "btle.advertising_header.pdu_type",
        "-e", "btle.initiator_address",
        "-e", "btle.advertising_address",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"tshark failed:\n{r.stderr.strip()}")

    toilet_lower = toilet_mac.lower()
    counter: Counter = Counter()
    for line in r.stdout.splitlines():
        parts = line.strip().split("\t")
        if len(parts) < 3:
            continue
        pdu_type, initiator, adv_addr = parts[0], parts[1], parts[2]
        if pdu_type != "0x05":
            continue
        # Guard against bit-error variants of the toilet MAC (differ by ≤2 bits)
        if adv_addr.lower() != toilet_lower:
            continue
        if initiator:
            counter[initiator.lower()] += 1
    return counter


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Find the BLE address of a Geberit remote control from an nRF Sniffer capture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage")[1] if "Usage" in __doc__ else "",
    )
    parser.add_argument("pcapng", help="Path to .pcapng sniffer capture")
    parser.add_argument(
        "--toilet", metavar="MAC",
        help="Toilet BLE MAC (default: read from config.ini → 38:AB:41:2A:0D:67)",
    )
    parser.add_argument(
        "--config", metavar="PATH", default=str(_REPO_ROOT / "config.ini"),
        help="Path to config.ini  (default: %(default)s)",
    )
    args = parser.parse_args()

    pcapng = Path(args.pcapng)
    if not pcapng.exists():
        sys.exit(f"Error: file not found: {pcapng}")

    # Resolve toilet MAC: CLI > config.ini > built-in default
    toilet_mac = args.toilet
    if not toilet_mac:
        toilet_mac = _read_toilet_mac(Path(args.config))
        if toilet_mac:
            print(f"[config]  toilet MAC from config.ini: {toilet_mac}")
    if not toilet_mac:
        toilet_mac = "38:ab:41:2a:0d:67"
        print(f"[default] toilet MAC: {toilet_mac}")

    tshark = _find_tshark()
    print(f"[info]    parsing {pcapng.name} …\n")

    counter = _find_initiators(pcapng, toilet_mac, tshark)

    # -----------------------------------------------------------------------
    # Report
    # -----------------------------------------------------------------------
    if not counter:
        print("No CONNECT_IND frames targeting the toilet were found.")
        print()
        print("Troubleshooting:")
        print("  • Press a button on the remote while Wireshark is capturing.")
        print(f"  • Confirm the toilet MAC is correct: {toilet_mac}")
        print("  • Confirm the capture is from an nRF Sniffer (link type 186).")
        print("  • Bit-error variants of the toilet MAC are excluded — try --toilet")
        print("    with the MAC exactly as shown in Wireshark.")
        return

    total = sum(counter.values())
    print(f"Found {total} CONNECT_IND frame(s) targeting toilet {toilet_mac}:\n")
    print(f"  {'Address':<22} {'Hits':>5}  {'OUI / Note'}")
    print(f"  {'-'*22}  {'-'*5}  {'-'*35}")

    for addr, hits in counter.most_common():
        print(f"  {addr:<22} {hits:>5}  {_tag(addr)}")

    ti_candidates = [a for a in counter if _oui(a) in _TI_OUIS]

    print()
    if len(counter) == 1:
        result = next(iter(counter)).upper()
        print(f"Result:  {result}")
    elif len(ti_candidates) == 1:
        result = ti_candidates[0].upper()
        print(f"Result:  {result}  (only Texas Instruments initiator)")
    elif ti_candidates:
        print("Multiple TI addresses found.  Most likely the one with the highest hit count.")
        result = max(ti_candidates, key=lambda a: counter[a]).upper()
        print(f"Best guess:  {result}")
    else:
        print("Could not auto-select — no Texas Instruments OUI found.")
        print("The remote is most likely the address with the highest hit count above.")


if __name__ == "__main__":
    main()
