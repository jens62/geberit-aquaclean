#!/usr/bin/env python3
"""
Decrypt Arendi Security session frames from an nRF52840 pcapng using session
keys written by the bridge at DEBUG log level.

Usage:
    python tools/arendi-decrypt-session.py <logfile> <pcapng> [--mac MAC] [--tshark PATH]

The bridge must have been running with --log-level debug (or silly) during the
nRF52840 capture.  Session matching: the EP Response nonce2 in the pcapng is
matched against the nonce2 in the log's "AriendiSecurity: session_keys" line.

Decrypted inner frames are decoded as Ble20 CommandId payloads where possible.
"""

import argparse
import importlib.util as _ilu
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def _load_sibling(filename: str):
    path = Path(__file__).parent / filename
    spec = _ilu.spec_from_file_location(
        filename.replace("-", "_").replace(".py", ""), path
    )
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_nrf    = _load_sibling("nrf-ble-analyze.py")
_arendi = _load_sibling("arendi-parse-capture.py")

from aquaclean_console_app.bluetooth_le.LE.AriendiSecurity import (
    _AesCtrState,
    _cobs_decode,
    _crc16_kermit,
)

_SEC_ENCRYPTED = 0x20

# Ble20 CommandId names (CommandId.cs)
_CMD_NAMES = {
    0x00: "Inventory",
    0x01: "InventoryResponse",
    0x04: "ReadData",
    0x05: "ReadDataResponse",
    0x10: "Subscribe",
    0x11: "SubscribeResponse",
    0x12: "Unsubscribe",
    0x20: "WriteData",
    0x21: "WriteDataResponse",
    0x30: "NotifySubscription",
    0x34: "NotifyData",
}

# CommandIds that carry a 2-byte little-endian DpId immediately after the command byte
_CMD_HAS_DPID = {0x04, 0x05, 0x20, 0x21, 0x34}

# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

_LOG_RE = re.compile(
    r"AriendiSecurity: session_keys"
    r"\s+shared_secret=([0-9a-f]+)"
    r"\s+rx_key=([0-9a-f]+)"
    r"\s+tx_key=([0-9a-f]+)"
    r"\s+nonce2=([0-9a-f]+)"
)


def _parse_log(logfile: Path) -> dict:
    """Return {nonce2_hex: {rx_key, tx_key, nonce2}} for every session_keys entry."""
    sessions = {}
    with open(logfile, errors="replace") as f:
        for line in f:
            m = _LOG_RE.search(line)
            if not m:
                continue
            nonce2 = bytes.fromhex(m.group(4))
            sessions[nonce2.hex()] = {
                "rx_key": bytes.fromhex(m.group(2)),
                "tx_key": bytes.fromhex(m.group(3)),
                "nonce2": nonce2,
            }
    return sessions


# ---------------------------------------------------------------------------
# Inner-COBS decode (plaintext after AES-CTR decrypt)
# ---------------------------------------------------------------------------

def _decode_inner(data: bytes) -> list:
    """
    data = AES-CTR plaintext = [0x00][COBS(payload+CRC16_LE)][0x00]...
    Returns list of (geberit_payload: bytes, crc_ok: bool).
    """
    results = []
    for part in data.split(b"\x00"):
        if not part:
            continue
        try:
            decoded = _cobs_decode(part)
        except ValueError:
            continue
        if len(decoded) < 3:
            continue
        crc_recv = decoded[-2] | (decoded[-1] << 8)
        payload  = decoded[:-2]
        results.append((payload, _crc16_kermit(payload) == crc_recv))
    return results


def _print_ble20(ts: str, direction: str, payload: bytes, crc_ok: bool) -> None:
    crc_tag  = "✓" if crc_ok else "✗ CRC MISMATCH"
    if not payload:
        print(f"  [DECRYPTED {direction}] (empty)  CRC {crc_tag}")
        return
    cmd_id   = payload[0]
    cmd_name = _CMD_NAMES.get(cmd_id, f"0x{cmd_id:02X}")
    rest     = payload[1:]

    dpid_str  = ""
    value_str = ""
    if cmd_id in _CMD_HAS_DPID and len(rest) >= 2:
        dpid      = int.from_bytes(rest[:2], "little")
        dpid_str  = f"  DpId={dpid}"
        value_hex = rest[2:].hex().upper()
        if value_hex:
            value_str = f"  value={value_hex}"
    elif rest:
        value_str = f"  data={rest.hex().upper()}"

    print(f"  [DECRYPTED {direction}] {cmd_name}{dpid_str}{value_str}  CRC {crc_tag}")


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

def _process_rows(row_list: list, sessions: dict, label: str) -> None:
    if not row_list:
        return
    if label:
        print(f"--- {label} ---")

    parser_app = _arendi._FrameParser()
    parser_dev = _arendi._FrameParser()
    state: dict = {}
    rx_cipher = None
    tx_cipher = None

    for row in row_list:
        parsed = _nrf._parse_arendi_row(row)
        if parsed is None:
            continue
        ts, opcode, handle, data = parsed
        direction = _nrf._arendi_direction(opcode, handle)
        if direction is None:
            continue
        parser = parser_app if direction == "App→Dev" else parser_dev

        for ctrl, payload, crc_ok in parser.feed(data):
            ft, _, _ = _arendi._decode_hdlc_ctrl(ctrl)

            # EP Response → try to match session keys
            if (ft == "I" and payload
                    and payload[0] == _arendi._SEC_EP_RESP
                    and len(payload) >= 33):
                nonce2 = payload[17:33]
                sess   = sessions.get(nonce2.hex())
                if sess:
                    rx_cipher = _AesCtrState(sess["rx_key"], nonce2)
                    tx_cipher = _AesCtrState(sess["tx_key"], nonce2)
                    _arendi._print_frame(ts, direction, ctrl, payload, crc_ok, state)
                    print(f"  [✓ session keys matched — decryption active]")
                    continue
                else:
                    rx_cipher = None
                    tx_cipher = None
                    _arendi._print_frame(ts, direction, ctrl, payload, crc_ok, state)
                    print(f"  [✗ no matching session keys in log — frames will be opaque]")
                    continue

            # Encrypted frame with active keys → decrypt
            if (ft == "I" and payload
                    and payload[0] == _SEC_ENCRYPTED
                    and rx_cipher is not None):
                cipher    = rx_cipher if direction == "Dev→App" else tx_cipher
                plaintext = cipher.process(payload[1:])
                inner     = _decode_inner(plaintext)
                _arendi._print_frame(ts, direction, ctrl, payload, crc_ok, state)
                if inner:
                    for geo_payload, inner_crc_ok in inner:
                        _print_ble20(ts, direction, geo_payload, inner_crc_ok)
                else:
                    print(f"  [inner COBS decode failed — {len(plaintext)} bytes plaintext]")
                continue

            _arendi._print_frame(ts, direction, ctrl, payload, crc_ok, state)

    print()


def _analyze(tshark: str, pcapng: Path, mac: str, addr_field: str, sessions: dict) -> None:
    pre_rows, main_rows = _nrf._get_arendi_rows(tshark, pcapng, mac, addr_field)

    print(f"=== Arendi Decryption — bridge session keys from log ===")
    print(f"File   : {pcapng.name}")
    print(f"Sessions in log: {len(sessions)}")
    if mac:
        print(f"Alba   : {mac}")
    print()

    _process_rows(pre_rows,  sessions, "Pre-capture connection" if pre_rows else "")
    _process_rows(main_rows, sessions, "Main session" if pre_rows else "")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Decrypt bridge Arendi session frames from nRF52840 pcapng.",
        epilog=(
            "Requires the bridge to have run with --log-level debug during the capture.\n"
            "The log file may be a raw bridge log or a Home Assistant log — the tool\n"
            "searches for 'AriendiSecurity: session_keys' lines anywhere in the file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("logfile", help="Bridge or HA log file with session_keys debug lines")
    ap.add_argument("pcapng",  help="nRF52840 BLE sniffer pcapng capture")
    ap.add_argument("--mac",    default="", metavar="MAC",
                    help="Alba device MAC (auto-detected if omitted)")
    ap.add_argument("--tshark", default="tshark", metavar="PATH",
                    help="tshark binary (default: tshark)")
    args = ap.parse_args()

    logfile = Path(args.logfile)
    pcapng  = Path(args.pcapng)

    for p, label in [(logfile, "log file"), (pcapng, "pcapng")]:
        if not p.exists():
            print(f"Error: {label} not found: {p}", file=sys.stderr)
            sys.exit(1)

    sessions = _parse_log(logfile)
    if not sessions:
        print("Error: no 'AriendiSecurity: session_keys' lines found in log.", file=sys.stderr)
        print("Ensure the bridge ran with --log-level debug during the capture.", file=sys.stderr)
        sys.exit(1)
    print(f"[+] Found {len(sessions)} session key set(s) in log", file=sys.stderr)

    mac       = args.mac.strip()
    tshark    = args.tshark

    if not mac:
        mac = _nrf._detect_mac(tshark, pcapng) or ""
    addr_field = _nrf._detect_addr_field(tshark, pcapng)

    _analyze(tshark, pcapng, mac, addr_field, sessions)


if __name__ == "__main__":
    main()
