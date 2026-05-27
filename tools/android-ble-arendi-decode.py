#!/usr/bin/env python3
"""Decode Arendi/HDLC frame structure from an Android BLE pcapng or pre-parsed text.

Companion to android-ble-analyze.py.  Shows per-session COBS/HDLC frame
classification for app→device (handle 0x001E) and device→app (0x0020) writes,
with ciphertext sizes mapped to Ble20 payload sizes.

Useful for identifying the post-inventory initialisation sequence
(CapabilitiesCmd + EventStorageInventory) in captures of the Geberit Home App.

Usage:
    python tools/android-ble-arendi-decode.py <capture.pcapng>
    python tools/android-ble-arendi-decode.py <parsed.txt>   # pre-parsed output

When a .pcapng is supplied, android-ble-analyze.py is invoked automatically.
"""

import os
import re
import subprocess
import sys


# ---------------------------------------------------------------------------
# COBS decoder
# ---------------------------------------------------------------------------

def cobs_decode(data: bytes) -> bytes:
    out = bytearray()
    i = 0
    while i < len(data):
        code = data[i]
        i += 1
        for _ in range(code - 1):
            if i >= len(data):
                return bytes(out)
            out.append(data[i])
            i += 1
        if code < 0xFF and i < len(data):
            out.append(0)
    return bytes(out)


# ---------------------------------------------------------------------------
# Frame parser: extract one or more COBS-framed HDLC frames from raw ATT bytes
# ---------------------------------------------------------------------------

def parse_frames(hex_val: str) -> list[bytes]:
    raw = bytes.fromhex(hex_val)
    frames = []
    i = 0
    while i < len(raw):
        if raw[i] == 0:
            try:
                end = raw.index(0, i + 1)
            except ValueError:
                break
            cobs_data = raw[i + 1:end]
            if cobs_data:
                frames.append(cobs_decode(cobs_data))
            i = end + 1
        else:
            i += 1
    return frames


# ---------------------------------------------------------------------------
# HDLC frame classifier
# ---------------------------------------------------------------------------

_S_TYPES = {0: 'RR', 1: 'REJ', 2: 'RNR', 3: 'SREJ'}

def classify_frame(decoded: bytes) -> str:
    if len(decoded) < 3:
        return f"SHORT({len(decoded)}B)"
    ctrl = decoded[0]
    payload = decoded[1:-2]  # strip ctrl byte + 2-byte CRC
    if (ctrl & 0x01) == 0:
        # I-frame
        ns = (ctrl >> 1) & 7
        nr = (ctrl >> 5) & 7
        if payload and payload[0] == 0x20:
            ct = len(payload) - 1
            ble20 = ct - 5
            return f"I-frame(ns={ns},nr={nr}) ENC {ct}B → Ble20 {ble20}B"
        elif payload:
            names = {0x00: 'SABM', 0x03: 'UA', 0x10: 'SEC_EP_REQ', 0x11: 'SEC_EP_RESP',
                     0x12: 'SEC_KE_REQ', 0x13: 'SEC_KE_RESP'}
            t = payload[0]
            return f"I-frame(ns={ns},nr={nr}) PLAIN type=0x{t:02x}({names.get(t,'?')})"
        return f"I-frame(ns={ns},nr={nr}) EMPTY"
    elif (ctrl & 0x03) == 0x01:
        # S-frame
        nr = (ctrl >> 5) & 7
        stype = (ctrl >> 2) & 3
        return f"S-frame {_S_TYPES.get(stype,'?')}(nr={nr})"
    elif (ctrl & 0x03) == 0x03:
        # U-frame
        return f"U-frame ctrl=0x{ctrl:02x}"
    return f"UNKNOWN ctrl=0x{ctrl:02x}"


# ---------------------------------------------------------------------------
# Session decoder: iterate over parsed lines and classify frames
# ---------------------------------------------------------------------------

def decode_sessions(lines: list[str]) -> None:
    session = 0
    for line in lines:
        line = line.rstrip()

        if 'CONNECT' in line and 'handle=' in line:
            session += 1
            ts = re.search(r'(\d+:\d+:\d+\.\d+)', line)
            print(f"\n{'='*70}")
            print(f"SESSION {session}  {ts.group(1) if ts else ''}")
            print(f"{'='*70}")
            continue

        if 'DISCONNECT' in line:
            ts = re.search(r'(\d+:\d+:\d+\.\d+)', line)
            print(f"  {ts.group(1) if ts else '?'}  DISCONNECT")
            continue

        # app → device  (ATT_WRITE_CMD handle 0x001E)
        m = re.search(
            r'(\d+:\d+:\d+\.\d+)\s+▶\s+ATT_WRITE_CMD\s+att_handle=0x001E\s+value=([0-9a-f]+)',
            line,
        )
        if m:
            ts, hex_val = m.group(1), m.group(2)
            for f in parse_frames(hex_val):
                print(f"  {ts}  →  {classify_frame(f)}")
            continue

        # device → app  (ATT_HANDLE_VALUE_NOTIF handle 0x0020)
        m2 = re.search(
            r'(\d+:\d+:\d+\.\d+)\s+◀\s+ATT_HANDLE_VALUE_NOTIF\s+att_handle=0x0020\s+value=([0-9a-f]+)',
            line,
        )
        if m2:
            ts2, hex_val2 = m2.group(1), m2.group(2)
            for f in parse_frames(hex_val2):
                cls = classify_frame(f)
                if 'ENC' in cls or 'PLAIN' in cls:
                    print(f"  {ts2}  ←  {cls}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    path = sys.argv[1]
    if not os.path.exists(path):
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    if path.endswith('.pcapng'):
        analyzer = os.path.join(os.path.dirname(__file__), 'android-ble-analyze.py')
        result = subprocess.run(
            [sys.executable, analyzer, path, '--all-macs'],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"android-ble-analyze.py failed:\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
        lines = result.stdout.splitlines()
    else:
        with open(path) as f:
            lines = f.readlines()

    decode_sessions(lines)


if __name__ == '__main__':
    main()
