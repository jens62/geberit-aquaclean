#!/usr/bin/env python3
"""
Geberit AquaClean — BLE ATT sniffer
====================================

Uses the Nordic SnifferAPI (Sniffer.py / Packet.py) to follow the Mera Comfort
and write a .pcapng file for Wireshark analysis.

The SnifferAPI's sendFollow() is the ONLY reliable way to lock the nRF52840
firmware onto a specific device before the connection happens.  The nrfutil CLI
(--follow / --follow-by-name) does not replicate this correctly.

Prerequisites
-------------
1. Download the Nordic nRF Sniffer for Bluetooth LE zip from:
     https://www.nordicsemi.com/Products/Development-tools/nRF-Sniffer-for-Bluetooth-LE/Download

2. Extract the zip. Inside the `extcap/` directory you will find `SnifferAPI/`.

3. Place the `SnifferAPI/` folder in one of these locations (first found wins):
     a)  Next to this script:
           tools/developer/SnifferAPI/
     b)  Wireshark user plugin path (macOS):
           ~/Library/Application Support/Wireshark/extcap/SnifferAPI/
     c)  Wireshark global plugin path (macOS):
           /Applications/Wireshark.app/Contents/PlugIns/wireshark/extcap/SnifferAPI/

4. Install pyserial:
     pip install pyserial

Usage
-----
  python3 sniff.py                          # auto-detect port
  python3 sniff.py --port /dev/tty.usbmodemXXXX
  python3 sniff.py --output-dir ~/Desktop
  python3 sniff.py --loop                   # restart after each session

How it works
------------
1. Open the serial port to the nRF52840 dongle.
2. Call sendFollow(TARGET_MAC) — tells the firmware to watch advertising
   channels 37/38/39 and lock onto the CONNECT_IND from the Geberit device.
3. Once the CONNECT_IND is captured the firmware extracts the hop increment
   and follows the piconet across all 37 data channels automatically.
4. All packets (advertising + connected data) are written to a .pcapng file.
5. When the target is first seen advertising, print "OPEN APP NOW".
"""

import glob
import os
import platform
import struct
import sys
import time
import argparse
from datetime import datetime

# ── SnifferAPI path resolution ────────────────────────────────────────────────

_API_CANDIDATES = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "SnifferAPI"),
    os.path.expanduser("~/Library/Application Support/Wireshark/extcap/SnifferAPI"),
    "/Applications/Wireshark.app/Contents/PlugIns/wireshark/extcap/SnifferAPI",
    os.path.expanduser("~/.local/lib/wireshark/extcap/SnifferAPI"),
    os.path.expanduser("~/Downloads/SnifferAPI"),
]

def _find_api():
    for p in _API_CANDIDATES:
        if os.path.isfile(os.path.join(p, "Sniffer.py")):
            return p
    return None

_api_path = _find_api()
if _api_path is None:
    print("ERROR: SnifferAPI not found. Install it as follows:")
    print()
    print("  1. Download the Nordic nRF Sniffer zip from:")
    print("       https://www.nordicsemi.com/Products/Development-tools/nRF-Sniffer-for-Bluetooth-LE/Download")
    print("  2. Extract it and copy the extcap/SnifferAPI/ folder next to this script:")
    print(f"       {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'SnifferAPI')}/")
    print("  3. pip install pyserial")
    sys.exit(1)

sys.path.insert(0, _api_path)
import Sniffer  # noqa: E402
import Packet   # noqa: E402

# ── constants ────────────────────────────────────────────────────────────────

# Mera Comfort public BLE address as byte list [MSB … LSB]
TARGET_MAC = [0x38, 0xAB, 0x41, 0x2A, 0x0D, 0x67]
TARGET_MAC_STR = "38:ab:41:2a:0d:67"

OUTPUT_DIR_DEFAULT = os.path.expanduser("~/Downloads")

# ── serial port detection ─────────────────────────────────────────────────────

def find_port():
    system = platform.system()
    if system == "Darwin":
        ports = glob.glob("/dev/tty.usbmodem*")
    elif system == "Linux":
        ports = glob.glob("/dev/ttyACM*")
    elif system == "Windows":
        import re, subprocess
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
        print(f"  Multiple ports found: {ports} — using {ports[0]}")
    return ports[0]

# ── minimal pcapng writer ─────────────────────────────────────────────────────
# Link type 272 = LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR (Nordic Tap)
# Wireshark decodes this natively when the file comes from an nRF Sniffer.

_PCAPNG_SHB = (
    b"\x0a\x0d\x0d\x0a"   # SHB block type
    + b"\x1c\x00\x00\x00"  # block length 28
    + b"\x4d\x3c\x2b\x1a"  # byte-order magic
    + b"\x01\x00"          # major version
    + b"\x00\x00"          # minor version
    + b"\xff\xff\xff\xff\xff\xff\xff\xff"  # section length unknown
    + b"\x1c\x00\x00\x00"  # block length (repeated)
)

def _idb(link_type=272):
    body = struct.pack("<HHI", link_type, 0, 0)  # link type, reserved, snap len 0=unlimited
    length = 12 + len(body)
    return (struct.pack("<I", 1)        # IDB block type
            + struct.pack("<I", length)
            + body
            + struct.pack("<I", length))

def _epb(data: bytes, ts_us: int):
    pad = (4 - len(data) % 4) % 4
    cap_len = len(data)
    body = (struct.pack("<II", 0, 0)           # interface id, timestamp high
            + struct.pack("<I", ts_us & 0xFFFFFFFF)  # timestamp low
            + struct.pack("<II", cap_len, cap_len)   # cap len, orig len
            + data + b"\x00" * pad)
    length = 12 + len(body)
    return (struct.pack("<I", 6)        # EPB block type
            + struct.pack("<I", length)
            + body
            + struct.pack("<I", length))


class PcapWriter:
    def __init__(self, path):
        self._f = open(path, "wb")
        self._f.write(_PCAPNG_SHB)
        self._f.write(_idb(link_type=272))
        self._start = time.time()
        self._count = 0

    def write(self, raw_bytes: bytes):
        ts_us = int((time.time() - self._start) * 1_000_000)
        self._f.write(_epb(raw_bytes, ts_us))
        self._f.flush()
        self._count += 1

    def close(self):
        self._f.close()

    @property
    def count(self):
        return self._count


# ── capture session ───────────────────────────────────────────────────────────

def run_capture(port, output_file):
    print(f"  Port   : {port}")
    print(f"  Output : {output_file}")
    print(f"  API    : {_api_path}")
    print()

    pcap = PcapWriter(output_file)

    print(f"  Initialising nRF52840 sniffer…")
    sniffer = Sniffer.Sniffer(port)
    sniffer.start()
    time.sleep(1)   # let firmware initialise

    print(f"  Sending follow({TARGET_MAC_STR})…")
    sniffer.sendFollow(TARGET_MAC)

    print(f"  Scanning — devices found:\n")
    open_app_printed = False

    try:
        while True:
            packets = sniffer.getPackets()
            for pkt in packets:
                # Write every valid packet to pcap
                try:
                    raw = pkt.getPayload() if hasattr(pkt, "getPayload") else None
                    if raw:
                        pcap.write(bytes(raw))
                except Exception:
                    pass

                if not pkt.OK:
                    continue

                ble = pkt.blePacket
                if ble is None:
                    continue

                # Show advertising packets so the user sees discovered devices
                try:
                    addr = ble.advertisingAddress
                    if addr:
                        addr_str = ":".join(f"{b:02x}" for b in reversed(addr)) \
                                   if isinstance(addr, (list, bytes, bytearray)) \
                                   else str(addr).lower()
                        is_tgt = (addr_str == TARGET_MAC_STR)
                        marker = "  ← TARGET" if is_tgt else ""
                        rssi   = getattr(pkt, "RSSI", "?")
                        print(f"    {addr_str}  RSSI {rssi} dBm{marker}", flush=True)

                        if is_tgt and not open_app_printed:
                            open_app_printed = True
                            print()
                            print("  ┌─────────────────────────────────────────────────────┐")
                            print("  │   OPEN THE GEBERIT HOME APP ON YOUR PHONE NOW        │")
                            print("  └─────────────────────────────────────────────────────┘")
                            print("  Ctrl-C to stop.\n")
                except Exception:
                    pass

                # Announce CONNECT_IND
                try:
                    pdu_type = getattr(ble, "PDUType", None)
                    if pdu_type == 5:   # CONNECT_IND
                        print(f"  [!!!] CONNECT_IND captured — firmware now hopping data channels")
                except Exception:
                    pass

            time.sleep(0.005)

    except KeyboardInterrupt:
        pass
    finally:
        sniffer.stop()
        pcap.close()

    print(f"\n  Packets written : {pcap.count}")
    print(f"  Saved → {output_file}")
    return True


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Capture Geberit AquaClean BLE ATT traffic via SnifferAPI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "device_name", nargs="?", default="Geberit AC PRO",
        help='BLE device name (informational only, e.g. "Geberit AC PRO")',
    )
    parser.add_argument("--loop",       action="store_true", help="Restart after each session")
    parser.add_argument("--port",       help="Serial port (auto-detected if omitted)")
    parser.add_argument("--output-dir", default=OUTPUT_DIR_DEFAULT,
                        help=f"Output directory (default: {OUTPUT_DIR_DEFAULT})")
    args = parser.parse_args()

    port = args.port or find_port()
    os.makedirs(args.output_dir, exist_ok=True)

    session = 0
    while True:
        session += 1
        ts          = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_file = os.path.join(args.output_dir, f"geberit-{ts}.pcapng")

        if session == 1:
            print(f"SnifferAPI     : {_api_path}")
            print(f"nRF52840 port  : {port}")
            print(f"Target MAC     : {TARGET_MAC_STR}")
            print(f"Output dir     : {args.output_dir}")
            print()

        print(f"[Session {session}]  Make sure the toilet is powered and NOT")
        print( "  already connected to the app, then press Enter…")
        try:
            input()
        except (KeyboardInterrupt, EOFError):
            print("\nAborted.")
            break

        run_capture(port, output_file)

        if not args.loop:
            break

        print("\n  Restarting in 3 s (Ctrl-C to quit)…\n")
        try:
            time.sleep(3)
        except KeyboardInterrupt:
            break


if __name__ == "__main__":
    main()
