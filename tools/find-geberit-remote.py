#!/usr/bin/env python3
"""
find-geberit-remote.py — Find the BLE address of a Geberit remote control
==========================================================================

Two modes:

  pcapng (default)
    Analyses an nRF Sniffer for Bluetooth LE capture (.pcapng) using tshark.
    Requires: tshark on PATH (ships with Wireshark).

  --live
    Talks directly to the nRF52840 dongle over serial — no Wireshark needed.
    Requires: pyserial  (pip install pyserial)

The remote never advertises.  It acts as BLE central and sends CONNECT_IND
frames directly to the toilet's MAC.  This script finds those frames and
extracts the initiator (remote) address.

How to use (--live mode)
-------------------------
1. Flash nRF Sniffer firmware on an nRF52840 dongle:
     https://github.com/NordicSemiconductor/nRF-Sniffer-for-Bluetooth-LE
2. Plug the dongle into USB.
3. Run: python tools/find-geberit-remote.py --live
4. When prompted, press a button on the remote control.
   The script prints the remote's MAC as soon as it catches the CONNECT_IND.

How to use (pcapng mode)
-------------------------
1. Open Wireshark with the nRF Sniffer as the capture interface.
2. Press a button on the remote.
3. Save the capture as .pcapng.
4. Run: python tools/find-geberit-remote.py capture.pcapng

Usage
-----
  python tools/find-geberit-remote.py capture.pcapng
  python tools/find-geberit-remote.py capture.pcapng --toilet 38:AB:41:2A:0D:67
  python tools/find-geberit-remote.py --live
  python tools/find-geberit-remote.py --live --port /dev/tty.usbmodem14201
  python tools/find-geberit-remote.py --live --toilet 38:AB:41:2A:0D:67
"""

import argparse
import configparser
import shutil
import struct
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants — shared
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Known Texas Instruments OUI prefixes (Geberit uses TI BLE chips).
# Toilet OUI = 38:AB:41; remote seen so far = B0:10:A0.  Both are TI-assigned.
_TI_OUIS = {
    "38:ab:41", "b0:10:a0", "00:18:da", "34:b1:f7", "04:a3:16",
    "00:17:e9", "00:24:d6", "a4:34:d9", "98:5d:ad", "d0:b5:c2",
}

# ---------------------------------------------------------------------------
# Constants — nRF Sniffer serial protocol (version 3)
# Reference: Nordic nRF-Sniffer-for-Bluetooth-LE extcap / SnifferAPI
# ---------------------------------------------------------------------------

# SLIP framing bytes
_SLIP_START     = 0xAB
_SLIP_END       = 0xBC
_SLIP_ESC       = 0xCD
_SLIP_ESC_START = 0xAC  # follows ESC, represents START byte in payload
_SLIP_ESC_END   = 0xBD  # follows ESC, represents END byte in payload
_SLIP_ESC_ESC   = 0xCE  # follows ESC, represents ESC byte in payload

# Packet IDs (host → sniffer commands)
_CMD_SCAN_CONT  = 0x07  # start continuous advertising-channel scan
_CMD_PING       = 0x0D  # ping (verify connection)

# Packet IDs (sniffer → host events)
_EVT_PACKET     = 0x06  # BLE packet received
_EVT_PING       = 0x0E  # ping response

# BLE constants
_ADV_ACCESS_ADDR = 0x8E89BED6   # fixed access address for all advertising channels
_PDU_CONNECT_IND = 0x05          # CONNECT_IND PDU type

# Serial settings
_BAUD_RATE   = 1_000_000
_READ_TIMEOUT = 0.1  # seconds per serial.read() call

# Nordic/Segger USB vendor IDs used by nRF52840 with Sniffer firmware
_NORDIC_VIDS = {0x1366, 0x1915}


# ---------------------------------------------------------------------------
# SLIP framing
# ---------------------------------------------------------------------------

def _slip_encode(data: bytes) -> bytes:
    out = bytearray([_SLIP_START])
    for b in data:
        if b == _SLIP_START:
            out += [_SLIP_ESC, _SLIP_ESC_START]
        elif b == _SLIP_END:
            out += [_SLIP_ESC, _SLIP_ESC_END]
        elif b == _SLIP_ESC:
            out += [_SLIP_ESC, _SLIP_ESC_ESC]
        else:
            out.append(b)
    out.append(_SLIP_END)
    return bytes(out)


def _extract_frames(buf: bytearray) -> tuple[list[bytes], bytearray]:
    """
    Pull all complete SLIP frames out of buf.
    Returns (list_of_raw_frames, remaining_buf).
    Each raw frame is the bytes between START and END, not yet SLIP-decoded.
    """
    frames = []
    while True:
        try:
            start = buf.index(_SLIP_START)
        except ValueError:
            buf = bytearray()
            break
        try:
            end = buf.index(_SLIP_END, start + 1)
        except ValueError:
            buf = buf[start:]   # keep incomplete frame for next read
            break
        frames.append(bytes(buf[start + 1: end]))
        buf = buf[end + 1:]
    return frames, buf


def _slip_decode(raw: bytes) -> bytes | None:
    """Decode one SLIP frame.  Returns payload bytes, or None if framing error."""
    out = bytearray()
    i = 0
    while i < len(raw):
        b = raw[i]
        if b == _SLIP_ESC:
            i += 1
            if i >= len(raw):
                return None
            nxt = raw[i]
            if nxt == _SLIP_ESC_START:
                out.append(_SLIP_START)
            elif nxt == _SLIP_ESC_END:
                out.append(_SLIP_END)
            elif nxt == _SLIP_ESC_ESC:
                out.append(_SLIP_ESC)
            else:
                return None  # invalid escape
        else:
            out.append(b)
        i += 1
    return bytes(out)


# ---------------------------------------------------------------------------
# Sniffer packet construction & parsing
# ---------------------------------------------------------------------------

def _build_cmd(packet_id: int, payload: bytes = b"", counter: int = 0) -> bytes:
    """Build a SLIP-encoded command packet (host → sniffer)."""
    hdr = bytes([
        6,                          # hdr_len (always 6 for v3)
        3,                          # version
        counter & 0xFF,             # counter low byte
        (counter >> 8) & 0xFF,      # counter high byte
        packet_id,
        len(payload),
    ])
    return _slip_encode(hdr + payload)


def _parse_packet(frame: bytes) -> tuple[int, bytes] | None:
    """
    Parse a SLIP-decoded v3 frame.
    Returns (packet_id, payload) or None on malformed input.
    """
    if len(frame) < 6:
        return None
    hdr_len     = frame[0]
    version     = frame[1]
    packet_id   = frame[4]
    payload_len = frame[5]
    if version != 3 or hdr_len != 6:
        return None
    if len(frame) < 6 + payload_len:
        return None
    return packet_id, frame[6: 6 + payload_len]


def _parse_evt_packet(payload: bytes) -> tuple[str, str] | None:
    """
    Parse an EVENT_PACKET payload.
    Returns (initiator_mac, advertiser_mac) if this is a CONNECT_IND targeting
    any known advertiser, else None.

    payload layout (protocol v3):
      [0]   flags
      [1]   channel_index
      [2]   rssi_raw   (actual dBm = -value)
      [3:5] event_counter  (uint16_le)
      [5:9] timestamp_us   (uint32_le)
      [9:]  BLE PDU

    BLE PDU layout (advertising channel):
      [0:4]  access_address (should be 0x8E89BED6 LE)
      [4]    PDU header byte 0 — bits[3:0]=type, bit[6]=TxAdd, bit[7]=RxAdd
      [5]    PDU length
      [6:12] InitA (6 bytes LSB-first) — present only for CONNECT_IND
      [12:18]AdvA  (6 bytes LSB-first) — present only for CONNECT_IND
    """
    if len(payload) < 9 + 4 + 2 + 12:   # minimum for CONNECT_IND
        return None

    pdu = payload[9:]

    # Verify advertising access address
    aa = struct.unpack_from("<I", pdu, 0)[0]
    if aa != _ADV_ACCESS_ADDR:
        return None

    pdu_type = pdu[4] & 0x0F
    if pdu_type != _PDU_CONNECT_IND:
        return None

    if len(pdu) < 4 + 2 + 12:
        return None

    init_a = pdu[6:12]   # InitA — initiator (remote control)
    adv_a  = pdu[12:18]  # AdvA  — advertiser (toilet)

    def _mac(b: bytes) -> str:
        return ":".join(f"{x:02x}" for x in reversed(b))

    return _mac(init_a), _mac(adv_a)


# ---------------------------------------------------------------------------
# Serial port detection
# ---------------------------------------------------------------------------

def _find_sniffer_port() -> str | None:
    """Return the serial port of the first nRF52840 sniffer found, or None."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None
    for p in list_ports.comports():
        vid = getattr(p, "vid", None)
        if vid in _NORDIC_VIDS:
            return p.device
        # Fallback: name heuristics for systems where VID isn't exposed
        name = p.device.lower()
        if "usbmodem" in name or "ttyacm" in name:
            desc = (p.description or "").lower()
            if "nrf" in desc or "sniffer" in desc or "segger" in desc:
                return p.device
    return None


# ---------------------------------------------------------------------------
# Live capture mode (Option B — pyserial, no tshark)
# ---------------------------------------------------------------------------

def _run_live(toilet_mac: str, port: str | None) -> None:
    try:
        import serial
        from serial.tools import list_ports as _lp
    except ImportError:
        sys.exit(
            "Error: pyserial is required for --live mode.\n"
            "Install it:  pip install pyserial"
        )

    # Resolve port
    if not port:
        port = _find_sniffer_port()
    if not port:
        # Show available ports so the user can pick one
        ports = [p.device for p in _lp.comports()]
        if ports:
            sys.exit(
                "Could not auto-detect nRF Sniffer port.\n"
                f"Available ports: {', '.join(ports)}\n"
                "Re-run with:  --port <device>"
            )
        sys.exit(
            "No serial ports found.  Is the nRF52840 dongle plugged in?"
        )

    print(f"[serial]  opening {port} at {_BAUD_RATE} bps …")
    try:
        ser = serial.Serial(port, baudrate=_BAUD_RATE, timeout=_READ_TIMEOUT)
    except serial.SerialException as e:
        sys.exit(f"Error opening {port}: {e}")

    toilet_lower = toilet_mac.lower()
    counter = Counter()
    cmd_counter = 0

    def send(packet_id: int, payload: bytes = b"") -> None:
        nonlocal cmd_counter
        ser.write(_build_cmd(packet_id, payload, cmd_counter))
        cmd_counter += 1

    # ---- Start continuous scan -----------------------------------------
    # Note: the sniffer does not respond to ping until scanning has started.
    # Send REQ_SCAN_CONT immediately, then verify by waiting for the first
    # EVENT_PACKET (any BLE advertisement nearby confirms the sniffer is alive).
    send(_CMD_SCAN_CONT)
    print("[sniffer] waiting for first BLE packet to confirm dongle is alive …",
          end="", flush=True)

    buf = bytearray()
    alive = False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        chunk = ser.read(256)
        if chunk:
            buf.extend(chunk)
            frames, buf = _extract_frames(buf)
            for raw_frame in frames:
                frame = _slip_decode(raw_frame)
                if not frame:
                    continue
                parsed = _parse_packet(frame)
                if parsed and parsed[0] == _EVT_PACKET:
                    alive = True
                    break
        if alive:
            break

    if not alive:
        print(" nothing received.")
        print()
        print("Troubleshooting:")
        print(f"  • Confirm the dongle is on {port}. Try --list-ports to see all ports.")
        print("  • Flash the nRF52840 with Nordic nRF Sniffer firmware.")
        print("  • Try --port with a different device path.")
        ser.close()
        sys.exit(1)
    print(" ok")

    print(f"[sniffer] scanning …  toilet target: {toilet_lower}")
    print("[sniffer] Press a button on the remote control now.")
    print("          (Ctrl+C to stop)\n")

    adv_count = 0
    buf = bytearray()

    try:
        while True:
            chunk = ser.read(256)
            if not chunk:
                continue
            buf.extend(chunk)

            raw_frames, buf = _extract_frames(buf)
            for raw_frame in raw_frames:
                frame = _slip_decode(raw_frame)
                if not frame:
                    continue
                parsed = _parse_packet(frame)
                if not parsed:
                    continue
                packet_id, payload = parsed

                if packet_id != _EVT_PACKET:
                    continue

                adv_count += 1
                if adv_count % 200 == 0:
                    print(f"  … {adv_count} BLE packets seen, waiting for CONNECT_IND …",
                          flush=True)

                result = _parse_evt_packet(payload)
                if not result:
                    continue
                initiator, advertiser = result

                if advertiser != toilet_lower:
                    continue

                counter[initiator] += 1
                tag = _tag(initiator)
                print(f"\n  CONNECT_IND → toilet from {initiator.upper()}  {tag}")

    except KeyboardInterrupt:
        print("\n[stopped]")
    finally:
        ser.close()

    _print_result(counter, toilet_lower)


# ---------------------------------------------------------------------------
# Shared reporting
# ---------------------------------------------------------------------------

def _oui(mac: str) -> str:
    return mac[:8].lower()


def _tag(mac: str) -> str:
    if _oui(mac) in _TI_OUIS:
        return "← Texas Instruments / likely Geberit"
    return ""


def _print_result(counter: Counter, toilet_mac: str) -> None:
    if not counter:
        print("No CONNECT_IND frames targeting the toilet were found.")
        print("\nTips:")
        print("  • Press a button on the remote while the script is running.")
        print(f"  • Confirm the toilet MAC is correct: {toilet_mac}")
        return

    total = sum(counter.values())
    print(f"\nFound {total} CONNECT_IND hit(s) targeting {toilet_mac}:\n")
    print(f"  {'Address':<22} {'Hits':>5}  Note")
    print(f"  {'-'*22}  {'-'*5}  {'-'*38}")
    for addr, hits in counter.most_common():
        print(f"  {addr:<22} {hits:>5}  {_tag(addr)}")

    ti_candidates = [a for a in counter if _oui(a) in _TI_OUIS]
    print()
    if len(counter) == 1:
        print(f"Result:  {next(iter(counter)).upper()}")
    elif len(ti_candidates) == 1:
        print(f"Result:  {ti_candidates[0].upper()}  (only TI initiator)")
    elif ti_candidates:
        best = max(ti_candidates, key=lambda a: counter[a])
        print(f"Best guess:  {best.upper()}  (highest-count TI address)")
    else:
        print("Could not auto-select — no Texas Instruments OUI found.")
        print("The remote is most likely the address with the highest hit count.")


# ---------------------------------------------------------------------------
# pcapng mode (Option A — tshark)
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
    return cfg.get("BLE", "device_id", fallback="").strip() or None


def _find_initiators_pcapng(pcapng: Path, toilet_mac: str, tshark: str) -> Counter:
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
        if adv_addr.lower() != toilet_lower:
            continue
        if initiator:
            counter[initiator.lower()] += 1
    return counter


def _run_pcapng(pcapng_path: str, toilet_mac: str) -> None:
    pcapng = Path(pcapng_path)
    if not pcapng.exists():
        sys.exit(f"Error: file not found: {pcapng}")
    tshark = _find_tshark()
    print(f"[info]  parsing {pcapng.name} …\n")
    counter = _find_initiators_pcapng(pcapng, toilet_mac, tshark)
    _print_result(counter, toilet_mac.lower())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Find the BLE address of a Geberit remote control.\n"
            "  pcapng mode (default): analyses an nRF Sniffer .pcapng file via tshark.\n"
            "  --live mode:           talks directly to the nRF52840 dongle via serial."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pcapng", nargs="?",
        help="Path to .pcapng sniffer capture (pcapng mode only)",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Live serial mode — scan in real time, no pcapng file needed",
    )
    parser.add_argument(
        "--port", metavar="DEVICE",
        help="Serial port of the nRF52840 dongle (default: auto-detect)",
    )
    parser.add_argument(
        "--list-ports", action="store_true",
        help="List available serial ports and exit (useful when auto-detect picks wrong port)",
    )
    parser.add_argument(
        "--toilet", metavar="MAC",
        help="Toilet BLE MAC (default: read from config.ini → 38:AB:41:2A:0D:67)",
    )
    parser.add_argument(
        "--config", metavar="PATH", default=str(_REPO_ROOT / "config.ini"),
        help="Path to config.ini (default: %(default)s)",
    )
    args = parser.parse_args()

    if args.list_ports:
        try:
            from serial.tools import list_ports as _lp
        except ImportError:
            sys.exit("pyserial required for --list-ports.  pip install pyserial")
        ports = list(_lp.comports())
        if not ports:
            print("No serial ports found.")
        else:
            print(f"{'Device':<25} {'VID:PID':<12} Description")
            print("-" * 70)
            for p in sorted(ports, key=lambda x: x.device):
                vid_pid = f"{p.vid:04X}:{p.pid:04X}" if p.vid else "—"
                print(f"{p.device:<25} {vid_pid:<12} {p.description or ''}")
        return

    if not args.live and not args.pcapng:
        parser.print_help()
        sys.exit(1)

    # Resolve toilet MAC: CLI > config.ini > built-in default
    toilet_mac = args.toilet
    if not toilet_mac:
        toilet_mac = _read_toilet_mac(Path(args.config))
        if toilet_mac:
            print(f"[config]  toilet MAC from config.ini: {toilet_mac}")
    if not toilet_mac:
        toilet_mac = "38:ab:41:2a:0d:67"
        print(f"[default] toilet MAC: {toilet_mac}")

    if args.live:
        _run_live(toilet_mac, args.port)
    else:
        _run_pcapng(args.pcapng, toilet_mac)


if __name__ == "__main__":
    main()
