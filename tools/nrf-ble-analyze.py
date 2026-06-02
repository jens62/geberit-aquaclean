#!/usr/bin/env python3
"""
nrf-ble-analyze.py — Analyze nRF52840 BLE sniffer captures for Geberit AquaClean traffic.

Reads .pcapng files produced by Wireshark + nRF Sniffer for Bluetooth LE (PCA10059 dongle,
link type 251 = BLUETOOTH_LE_LL).  Uses tshark to extract ATT frames, then decodes the
Geberit Mera Comfort or Alba application protocol.

Requires: tshark installed (brew install wireshark on macOS; wireshark package on Linux).

Usage:
  python tools/nrf-ble-analyze.py capture.pcapng
  python tools/nrf-ble-analyze.py capture.pcapng --mac 38:AB:41:2A:0D:67
  python tools/nrf-ble-analyze.py capture.pcapng --markdown
  python tools/nrf-ble-analyze.py capture.pcapng --markdown --output session.md
  python tools/nrf-ble-analyze.py capture.pcapng --raw
"""

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Sibling imports — filenames contain hyphens so importlib is required
# ---------------------------------------------------------------------------

def _load_sibling(filename: str):
    path = Path(__file__).parent / filename
    if not path.exists():
        raise FileNotFoundError(f"Sibling tool not found: {path}")
    spec = importlib.util.spec_from_file_location(
        filename.replace("-", "_").replace(".py", ""), path
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _android_ble = _load_sibling("android-ble-analyze.py")
except Exception as e:
    print(f"Error: cannot load android-ble-analyze.py: {e}", file=sys.stderr)
    sys.exit(1)

try:
    _arendi = _load_sibling("arendi-parse-capture.py")
    _ARENDI_AVAILABLE = True
except Exception:
    _arendi = None
    _ARENDI_AVAILABLE = False  # bridge package not installed — Alba analysis unavailable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MAC = "38:AB:41:2A:0D:67"   # Geberit AquaClean Mera Comfort

# ATT opcodes we care about
_OP_WRITE_REQ = 0x12   # ATT_WRITE_REQ  (also used for CCCD enables)
_OP_WRITE_CMD = 0x52   # ATT_WRITE_CMD  (Geberit procedure requests)
_OP_NOTIF     = 0x1B   # ATT_HANDLE_VALUE_NOTIF

_OP_TO_EVENT = {
    _OP_WRITE_REQ: "ATT_WRITE_REQ",
    _OP_WRITE_CMD: "ATT_WRITE_CMD",
    _OP_NOTIF:     "ATT_HANDLE_VALUE_NOTIF",
}

# GATT handles by device type
_MERA_WRITE_HANDLE  = 0x0003   # Mera Comfort: outgoing procedure requests
_ALBA_WRITE_HANDLE  = 0x001E   # Alba: Arendi Security channel (write)
_ALBA_NOTIFY_HANDLE = 0x0020   # Alba: Arendi Security channel (notify)

# Advertising "Complete Local Name" → device type
_LOCAL_NAME_MAP = {
    "aquaclean mera comfort": "mera",
    "acmeracomfort":          "mera",
    "acalba":                 "alba",
}

# ---------------------------------------------------------------------------
# tshark helpers
# ---------------------------------------------------------------------------

def _find_tshark() -> str:
    tshark = shutil.which("tshark")
    if not tshark:
        app = "/Applications/Wireshark.app/Contents/MacOS/tshark"
        if Path(app).exists():
            return app
        print("Error: tshark not found. Install: brew install wireshark", file=sys.stderr)
        sys.exit(1)
    return tshark


def _run_tshark(tshark: str, pcapng: Path, display_filter: str,
                fields: list) -> list:
    """
    Run tshark with a display filter and field list.
    Returns list of rows, each row being a list of field strings.
    Separator is | (pipe) — safe because BLE values never contain it.
    """
    cmd = [tshark, "-r", str(pcapng), "-Y", display_filter,
           "-T", "fields", "-E", "separator=|", "-E", "occurrence=f"]
    for f in fields:
        cmd += ["-e", f]

    result = subprocess.run(cmd, capture_output=True, text=True)
    rows = []
    for line in result.stdout.splitlines():
        rows.append(line.split("|"))
    return rows


def _peripheral_addr_field(tshark: str, pcapng: Path) -> str:
    """
    Return the correct tshark field name for the BLE peripheral address.
    Wireshark ≥ 4.0 renamed master/slave → central/peripheral.
    Probes the file once to find which name is populated.
    """
    for field in ("btle.peripheral_bd_addr", "btle.slave_bd_addr"):
        rows = _run_tshark(tshark, pcapng, "btatt", [field])
        if rows and any(r[0].strip() for r in rows[:10]):
            return field
    return "btle.peripheral_bd_addr"  # safe default


def _parse_int(s: str) -> int:
    """Parse decimal or 0x-prefixed hex string to int."""
    s = s.strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def _value_hex(raw: str) -> str:
    """Convert tshark colon-separated hex bytes to a plain hex string."""
    return raw.strip().replace(":", "")


def _ts_display(ts_str: str) -> str:
    """Format frame.time_relative float as 't=82.8s'."""
    try:
        return f"t={float(ts_str):.1f}s"
    except (ValueError, TypeError):
        return ts_str.strip()

# ---------------------------------------------------------------------------
# Device auto-detection
# ---------------------------------------------------------------------------

def _detect_device(tshark: str, pcapng: Path, addr_field: str) -> tuple:
    """
    Return (mac, device_type) by inspecting the pcapng.

    Strategy (in priority order):
      1. BLE advertising 'Complete Local Name' EIR/AD record
         → "AcAlba" etc. (Mera Comfort does NOT advertise a local name —
           it uses manufacturer-specific payload only, so this pass catches Alba only)
      2. First ATT write handle seen in the connection
         → 0x0003 = Mera Comfort, 0x001E = Alba
    """
    # Pass 1 — advertising local name (Alba only; Mera uses mfr-specific adv)
    rows = _run_tshark(tshark, pcapng,
                       "btcommon.eir_ad.entry.local_name",
                       ["btle.advertising_address",
                        "btcommon.eir_ad.entry.local_name"])
    for row in rows:
        if len(row) < 2:
            continue
        mac_str, name = row[0].strip(), row[1].strip()
        if not name:
            continue
        dtype = _LOCAL_NAME_MAP.get(name.lower())
        if dtype:
            return mac_str.upper() or None, dtype

    # Pass 2 — ATT write handle
    rows = _run_tshark(tshark, pcapng,
                       "btatt.opcode == 0x52 || btatt.opcode == 0x12",
                       [addr_field, "btatt.handle"])
    for row in rows:
        if len(row) < 2:
            continue
        mac_str, handle_str = row[0].strip(), row[1].strip()
        if not handle_str:
            continue
        try:
            h = _parse_int(handle_str)
        except ValueError:
            continue
        if h == _MERA_WRITE_HANDLE:
            return (mac_str.upper() or None), "mera"
        if h == _ALBA_WRITE_HANDLE:
            return (mac_str.upper() or None), "alba"

    return None, None

# ---------------------------------------------------------------------------
# Mera Comfort — reuse android-ble-analyze.py decode pipeline
# ---------------------------------------------------------------------------

def _extract_mera_events(tshark: str, pcapng: Path, mac: str,
                         addr_field: str) -> tuple:
    """
    Extract ATT events from a Mera Comfort nRF52840 capture.
    Returns (events, att_frame_count) in the format expected by
    android-ble-analyze._collect_calls().
    """
    dfilter = "btatt.opcode == 0x52 || btatt.opcode == 0x12 || btatt.opcode == 0x1b"
    if mac:
        dfilter = f"({dfilter}) && {addr_field} == {mac.lower()}"

    rows = _run_tshark(tshark, pcapng, dfilter,
                       ["frame.time_relative", addr_field,
                        "btatt.opcode", "btatt.handle", "btatt.value"])

    # Total ATT frame count for the header
    all_rows = _run_tshark(tshark, pcapng, "btatt", ["frame.number"])
    att_count = len(all_rows)

    events = []
    for row in rows:
        if len(row) < 5:
            continue
        ts_raw, slave, op_raw, handle_raw, value_raw = (row + [""] * 5)[:5]

        if not op_raw.strip() or not handle_raw.strip():
            continue

        try:
            opcode = _parse_int(op_raw)
            handle = _parse_int(handle_raw)
        except ValueError:
            continue

        etype = _OP_TO_EVENT.get(opcode)
        if not etype:
            continue

        events.append({
            "ts":         _ts_display(ts_raw),
            "type":       etype,
            "direction":  "RX" if opcode == _OP_NOTIF else "TX",
            "mac":        slave.strip().upper() or mac,
            "att_handle": f"0x{handle:04X}",
            "label":      _android_ble.GEBERIT_HANDLES.get(handle, ""),
            "value":      _value_hex(value_raw),
        })

    return events, att_count


def _analyze_mera(tshark: str, pcapng: Path, mac: str, args,
                  addr_field: str) -> None:
    events, att_count = _extract_mera_events(tshark, pcapng, mac, addr_field)

    if not events:
        print(f"No Geberit ATT frames found"
              + (f" for {mac}" if mac else "") + ".", file=sys.stderr)
        return

    print(f"[+] {att_count:,} ATT frames, {len(events):,} matching events",
          file=sys.stderr)

    if args.raw:
        for e in events:
            print(f"  {e['ts']:<12}  {e['direction']}  {e['type']:<30}  "
                  f"handle={e['att_handle']}  {e['value']}")
        return

    calls = _android_ble._collect_calls(events)

    if args.markdown:
        md = _android_ble.render_markdown_android(
            calls, pcapng, mac, "nRF52840 pcapng", att_count)
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"[+] Markdown written to {args.output}", file=sys.stderr)
        else:
            print(md)
    else:
        _print_mera_table(calls, pcapng, mac, att_count)


def _print_mera_table(calls, pcapng: Path, mac: str, att_count: int) -> None:
    """Compact procedure table (default non-markdown output for Mera)."""
    print(f"\n{'='*72}")
    print(f"File   : {pcapng.name}  [nRF52840 pcapng, {att_count:,} ATT frames]")
    print(f"Device : {mac}  (Geberit AquaClean Mera Comfort)")
    print(f"{'='*72}\n")

    if not calls:
        print("  No Geberit procedures decoded.\n")
        return

    col_t   = 12
    col_p   =  4
    col_n   = 34
    header  = f"  {'Time':<{col_t}}  {'Proc':>{col_p}}  {'Name':<{col_n}}  Args"
    sep     = f"  {'-'*col_t}  {'-'*col_p}  {'-'*col_n}  {'-'*35}"
    print(header)
    print(sep)
    for call in calls:
        if call.proc == _android_ble._PROC_CCCD:
            continue
        proc_name = _android_ble._PROC_NAMES_MD.get(
            (call.ctx, call.proc), f"Proc(0x{call.proc:02x})")
        ann = _android_ble._annotate_req(call.ctx, call.proc, call.args)
        print(f"  {call.req_ts:<{col_t}}  0x{call.proc:02x}  "
              f"{proc_name:<{col_n}}  {ann}")
    print()

# ---------------------------------------------------------------------------
# Alba — reuse arendi-parse-capture.py decode pipeline
# ---------------------------------------------------------------------------

def _analyze_alba(tshark: str, pcapng: Path, mac: str, args,
                  addr_field: str) -> None:
    if not _ARENDI_AVAILABLE:
        print("Error: Alba analysis requires the bridge package (aquaclean_console_app). "
              "Run from the repo root with the venv active.", file=sys.stderr)
        sys.exit(1)

    dfilter = (f"(btatt.opcode == 0x52 || btatt.opcode == 0x12 || btatt.opcode == 0x1b)"
               f" && (btatt.handle == {_ALBA_WRITE_HANDLE}"
               f" || btatt.handle == {_ALBA_NOTIFY_HANDLE})")
    if mac:
        dfilter += f" && {addr_field} == {mac.lower()}"

    rows = _run_tshark(tshark, pcapng, dfilter,
                       ["frame.time_relative", "btatt.opcode",
                        "btatt.handle", "btatt.value"])

    print(f"=== Arendi Security Capture Parser (nRF52840) ===")
    print(f"File: {pcapng.name}")
    if mac:
        print(f"Alba: {mac}")
    print()

    app_parser = _arendi._FrameParser()
    dev_parser = _arendi._FrameParser()
    state: dict = {}
    total = 0

    for row in rows:
        if len(row) < 4:
            continue
        ts_raw, op_raw, handle_raw, value_raw = (row + [""] * 4)[:4]

        if not op_raw.strip() or not handle_raw.strip():
            continue

        try:
            opcode = _parse_int(op_raw)
            handle = _parse_int(handle_raw)
            data   = bytes.fromhex(_value_hex(value_raw))
        except (ValueError, TypeError):
            continue

        ts = _ts_display(ts_raw)

        if opcode in (_OP_WRITE_CMD, _OP_WRITE_REQ) and handle == _ALBA_WRITE_HANDLE:
            direction, parser = "App→Dev", app_parser
        elif opcode == _OP_NOTIF and handle == _ALBA_NOTIFY_HANDLE:
            direction, parser = "Dev→App", dev_parser
        else:
            continue

        total += 1
        for ctrl, payload, crc_ok in parser.feed(data):
            _arendi._print_frame(ts, direction, ctrl, payload, crc_ok, state)

    print()
    if state.get("handshake_done"):
        enc_app = state.get("enc_App→Dev", 0)
        enc_dev = state.get("enc_Dev→App", 0)
        print("=== Handshake complete ===")
        print(f"Post-handshake encrypted frames: App→Dev={enc_app}  Dev→App={enc_dev}")
    else:
        print("[!] Handshake did not complete in this capture")
    print(f"\nTotal ATT PDUs on Alba Arendi channel: {total}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Analyze nRF52840 BLE sniffer captures for Geberit AquaClean traffic.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python tools/nrf-ble-analyze.py capture.pcapng
  python tools/nrf-ble-analyze.py capture.pcapng --mac 38:AB:41:2A:0D:67
  python tools/nrf-ble-analyze.py capture.pcapng --markdown
  python tools/nrf-ble-analyze.py capture.pcapng --markdown --output session.md
  python tools/nrf-ble-analyze.py capture.pcapng --raw
""",
    )
    ap.add_argument("pcapng", type=Path,
                    help="nRF52840 .pcapng file from Wireshark")
    ap.add_argument("--mac",
                    help="BLE MAC of the toilet (auto-detected if omitted)")
    ap.add_argument("--markdown", action="store_true",
                    help="Render as annotated markdown grouped by phase (Mera only)")
    ap.add_argument("--output", metavar="FILE",
                    help="Write markdown to FILE instead of stdout (requires --markdown)")
    ap.add_argument("--raw", action="store_true",
                    help="Print raw ATT bytes without decoding")
    args = ap.parse_args()

    if not args.pcapng.exists():
        print(f"Error: file not found: {args.pcapng}", file=sys.stderr)
        sys.exit(1)

    tshark    = _find_tshark()
    addr_field = _peripheral_addr_field(tshark, args.pcapng)

    mac, device_type = _detect_device(tshark, args.pcapng, addr_field)
    if args.mac:
        mac = args.mac.upper()

    if device_type is None:
        print("[!] Could not auto-detect device type — defaulting to Mera Comfort. "
              "Pass --mac to help with detection.", file=sys.stderr)
        device_type = "mera"

    print(f"[+] Detected: {mac or 'MAC unknown'}  type={device_type}  "
          f"addr_field={addr_field}", file=sys.stderr)

    if device_type == "mera":
        _analyze_mera(tshark, args.pcapng, mac or "", args, addr_field)
    else:
        _analyze_alba(tshark, args.pcapng, mac or "", args, addr_field)


if __name__ == "__main__":
    main()
