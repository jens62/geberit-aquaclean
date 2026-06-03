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
_EVT_PACKET     = 0x02  # BLE advertising packet (confirmed from wire: pcapng "Packet ID: 2")
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
    """Build a SLIP-encoded command packet (host → sniffer).

    Wire format (confirmed from debug hex dump):
      [0:2] remaining_len (uint16_le) — bytes after this 6-byte header
      [2]   version = 3
      [3:5] counter (uint16_le)
      [5]   packet_id
      [6:]  payload (remaining_len bytes)
    """
    hdr = struct.pack('<H', len(payload)) + bytes([
        3,                          # version
        counter & 0xFF,             # counter low byte
        (counter >> 8) & 0xFF,      # counter high byte
        packet_id,
    ])
    return _slip_encode(hdr + payload)


def _parse_packet(frame: bytes) -> tuple[int, bytes] | None:
    """
    Parse a SLIP-decoded v3 frame.
    Returns (packet_id, payload) or None on malformed input.

    Wire format (confirmed from debug hex dump of nRF52840 Dongle VID 1915:522A):
      [0:2] remaining_len (uint16_le) — payload bytes after this 6-byte header
      [2]   version = 3
      [3:5] counter (uint16_le)
      [5]   packet_id
      [6:]  payload (remaining_len bytes)
    """
    if len(frame) < 6:
        return None
    version = frame[2]
    if version != 3:
        return None
    packet_id    = frame[5]
    remaining_len = struct.unpack_from('<H', frame, 0)[0]
    return packet_id, frame[6: 6 + remaining_len]


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
    if len(payload) < 10 + 4 + 2 + 12:   # minimum for CONNECT_IND
        return None

    pdu = payload[10:]   # BLE PDU starts after the 10-byte event header

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


def _parse_adv_address(payload: bytes) -> str | None:
    """Return the advertising address from any ADV_IND/ADV_NONCONN_IND/ADV_SCAN_IND packet, or None."""
    if len(payload) < 10 + 4 + 2 + 6:
        return None
    pdu = payload[10:]
    aa = struct.unpack_from("<I", pdu, 0)[0]
    if aa != _ADV_ACCESS_ADDR:
        return None
    pdu_type = pdu[4] & 0x0F
    # 0=ADV_IND, 2=ADV_NONCONN_IND, 6=ADV_SCAN_IND — connectable advertisers only matter
    if pdu_type not in (0, 2, 6):
        return None
    adv_a = pdu[6:12]
    return ":".join(f"{x:02x}" for x in reversed(adv_a))


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

def _run_live(toilet_mac: str, port: str | None, debug: bool = False) -> None:
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

    # The nRF52840 Dongle resets when the serial port is opened (DTR line
    # transition).  Give it time to finish booting before sending commands.
    time.sleep(1.0)
    ser.reset_input_buffer()

    toilet_lower = toilet_mac.lower()
    counter = Counter()
    cmd_counter = 0

    def send(packet_id: int, payload: bytes = b"") -> None:
        nonlocal cmd_counter
        ser.write(_build_cmd(packet_id, payload, cmd_counter))
        cmd_counter += 1

    # ---- Debug mode: raw hex dump ----------------------------------------
    if debug:
        send(_CMD_SCAN_CONT)
        print("[debug]  sent REQ_SCAN_CONT — reading raw bytes for 5 s …")
        print("[debug]  START=0xAB  END=0xBC  ESC=0xCD\n")
        deadline = time.monotonic() + 5.0
        raw_all = bytearray()
        try:
            while time.monotonic() < deadline:
                chunk = ser.read(256)
                if chunk:
                    raw_all.extend(chunk)
        except KeyboardInterrupt:
            pass
        finally:
            ser.close()
        if not raw_all:
            print("No bytes received.")
        else:
            print(f"Received {len(raw_all)} bytes:")
            for i in range(0, len(raw_all), 16):
                row = raw_all[i:i+16]
                hex_part = " ".join(f"{b:02x}" for b in row)
                asc_part = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in row)
                print(f"  {i:04x}  {hex_part:<48}  {asc_part}")
        return

    # ---- Start continuous scan -----------------------------------------
    # Note: the sniffer does not respond to ping until scanning has started.
    # Send REQ_SCAN_CONT, then verify by waiting for the first EVENT_PACKET
    # (any BLE advertisement nearby confirms the sniffer is alive).
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

    if toilet_lower:
        print(f"[sniffer] toilet MAC: {toilet_lower}")
    print("[sniffer] Scanning for ALL CONNECT_IND frames — press a button on the remote now.")
    print("          (Ctrl+C to stop)\n")

    adv_count = 0
    toilet_adv_count = 0
    toilet_rssi_last = None
    buf = bytearray()
    # pairs: Counter keyed by (initiator, advertiser)
    pairs: Counter = Counter()

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

                # Track whether the toilet is advertising (proximity check)
                if toilet_lower and len(payload) >= 4:
                    adv_mac = _parse_adv_address(payload)
                    if adv_mac == toilet_lower:
                        toilet_adv_count += 1
                        toilet_rssi_last = -payload[3]  # rssi_raw negated

                if adv_count % 200 == 0:
                    if toilet_lower:
                        toilet_status = (
                            f"toilet seen: {toilet_adv_count}× (last {toilet_rssi_last} dBm)"
                            if toilet_adv_count else "toilet NOT seen yet — move dongle closer"
                        )
                        print(f"  … {adv_count} pkts, {sum(pairs.values())} CONNECT_IND, {toilet_status}",
                              flush=True)
                    else:
                        print(f"  … {adv_count} BLE packets seen, {sum(pairs.values())} CONNECT_IND hit(s) …",
                              flush=True)

                result = _parse_evt_packet(payload)
                if not result:
                    continue
                initiator, advertiser = result

                pairs[(initiator, advertiser)] += 1
                tag = _tag(initiator)
                match = "  ← YOUR TOILET" if advertiser == toilet_lower else ""
                print(f"\n  CONNECT_IND  {initiator.upper()} → {advertiser.upper()}  {tag}{match}",
                      flush=True)

    except KeyboardInterrupt:
        print("\n[stopped]")
    finally:
        ser.close()

    _print_live_result(pairs, toilet_lower)


# ---------------------------------------------------------------------------
# Shared reporting
# ---------------------------------------------------------------------------

def _print_live_result(pairs: Counter, toilet_lower: str) -> None:
    """Print summary of all CONNECT_IND pairs captured in live mode."""
    if not pairs:
        print("No CONNECT_IND frames were captured.")
        print("\nTips:")
        print("  • Press a button on the remote while the script is scanning.")
        print("  • Try pressing multiple times — the sniffer rotates channels and")
        print("    may miss a single press.  3–5 presses usually guarantees a hit.")
        return

    print(f"\nCaptured {sum(pairs.values())} CONNECT_IND frame(s):\n")
    print(f"  {'Initiator (remote?)':<22}  {'Advertiser (toilet?)':<22}  {'Hits':>4}  Note")
    print(f"  {'-'*22}  {'-'*22}  {'-'*4}  {'-'*35}")

    for (initiator, advertiser), hits in pairs.most_common():
        match = " ← YOUR TOILET" if advertiser == toilet_lower else ""
        print(f"  {initiator:<22}  {advertiser:<22}  {hits:>4}  {_tag(initiator)}{match}")

    # Best guess: TI initiator pointing at the toilet MAC (if known and matched)
    toilet_pairs = [(i, a) for (i, a) in pairs if a == toilet_lower]
    ti_toilet = [i for (i, _) in toilet_pairs if _oui(i) in _TI_OUIS]
    print()
    if ti_toilet:
        best = max(ti_toilet, key=lambda i: pairs[(i, toilet_lower)])
        print(f"Result:  remote = {best.upper()}")
    elif toilet_pairs:
        best = max(toilet_pairs, key=lambda ia: pairs[ia])[0]
        print(f"Best guess (no TI OUI match):  remote = {best.upper()}")
    else:
        print("Toilet MAC not seen as advertiser in any CONNECT_IND.")
        print("Find your toilet's MAC using 'aquaclean-connection-test.py --local-ble'")
        print("then re-run with:  --toilet XX:XX:XX:XX:XX:XX")


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
        "--debug", action="store_true",
        help="Dump raw bytes from the dongle for 5 s and exit (use when --live gets no packets)",
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

    # Resolve toilet MAC: CLI > config.ini > empty (live mode shows all)
    toilet_mac = args.toilet
    if not toilet_mac:
        toilet_mac = _read_toilet_mac(Path(args.config))
        if toilet_mac:
            print(f"[config]  toilet MAC from config.ini: {toilet_mac}")
    if not toilet_mac and not args.live:
        # pcapng mode needs a MAC to filter on; default to jens's for backwards compat
        toilet_mac = "38:ab:41:2a:0d:67"
        print(f"[default] toilet MAC: {toilet_mac}")

    if args.live:
        _run_live(toilet_mac or "", args.port, debug=args.debug)
    else:
        _run_pcapng(args.pcapng, toilet_mac)


if __name__ == "__main__":
    main()
