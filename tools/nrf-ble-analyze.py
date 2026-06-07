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

# Arendi chip OUI — used by Alba toilet and physical remote.
_ALBA_DEVICE_OUIS = {"e4:85:01"}

# Embedded-device OUI prefixes — identifies initiators as physical remotes rather than
# phones.  Includes Texas Instruments (Mera Comfort remote, toilet) and Arendi chips.
_EMBEDDED_DEVICE_OUIS = {
    "38:ab:41", "b0:10:a0", "00:18:da", "34:b1:f7", "04:a3:16",
    "00:17:e9", "00:24:d6", "a4:34:d9", "98:5d:ad", "d0:b5:c2",
} | _ALBA_DEVICE_OUIS

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
# Connection Events — CONNECT_IND timeline + ADV_DIRECT_IND (displacement)
# ---------------------------------------------------------------------------

def _oui(mac: str) -> str:
    return mac[:8].lower()


def _is_embedded_device(mac: str) -> bool:
    return _oui(mac) in _EMBEDDED_DEVICE_OUIS


def _get_connection_events(tshark: str, pcapng: Path,
                           toilet_mac: str) -> tuple:
    """
    Return (connect_inds, directed_advs).

    connect_inds: list of {ts, initiator} — CONNECT_IND frames targeting toilet.
    directed_advs: list of {ts, target} — ADV_DIRECT_IND frames FROM toilet
                   (toilet actively inviting a specific device back).
    """
    toilet_lower = toilet_mac.lower()

    # CONNECT_IND (pdu_type=5) targeting the toilet
    rows = _run_tshark(tshark, pcapng,
                       "btle.advertising_header.pdu_type == 0x05",
                       ["frame.time_relative",
                        "btle.initiator_address",
                        "btle.advertising_address"])
    connect_inds = []
    for row in rows:
        if len(row) < 3:
            continue
        ts_s, initiator, advertiser = row[0].strip(), row[1].strip(), row[2].strip()
        if advertiser.lower() != toilet_lower:
            continue
        if not initiator:
            continue
        try:
            connect_inds.append({"ts": float(ts_s), "initiator": initiator.upper()})
        except ValueError:
            pass

    # ADV_DIRECT_IND (pdu_type=1) FROM the toilet
    # btle.initiator_address holds TargetA (directed destination) in this PDU type
    rows = _run_tshark(tshark, pcapng,
                       f'btle.advertising_header.pdu_type == 0x01 '
                       f'&& btle.advertising_address == "{toilet_lower}"',
                       ["frame.time_relative", "btle.initiator_address"])
    directed_advs = []
    for row in rows:
        if len(row) < 2:
            continue
        ts_s, target = row[0].strip(), row[1].strip()
        try:
            directed_advs.append({"ts": float(ts_s), "target": target.upper()})
        except ValueError:
            pass

    return connect_inds, directed_advs


def _format_connection_events(connect_inds: list, directed_advs: list,
                               toilet_mac: str, markdown: bool = False) -> str:
    """
    Format CONNECT_IND + ADV_DIRECT_IND into a connection timeline with
    displacement verdict.
    """
    lines: list[str] = []

    if markdown:
        lines.append("## Connection Events\n")
    else:
        lines.append("Connection Events")
        lines.append("-" * 72)

    if not connect_inds and not directed_advs:
        lines.append("  No CONNECT_IND or ADV_DIRECT_IND frames found.")
        return "\n".join(lines)

    # Auto-detect remote: first embedded-device OUI initiator that is NOT the toilet itself
    toilet_lower = toilet_mac.lower()
    remote_mac: str | None = None
    for c in connect_inds:
        if c["initiator"].lower() != toilet_lower and _is_embedded_device(c["initiator"]):
            remote_mac = c["initiator"]
            break

    # Merge and sort all events chronologically
    all_events: list[tuple] = (
        [(c["ts"], "CONNECT_IND",    c["initiator"]) for c in connect_inds]
      + [(d["ts"], "ADV_DIRECT_IND", d["target"])    for d in directed_advs]
    )
    all_events.sort()

    # Print timeline
    for ts, evt, addr in all_events:
        if evt == "CONNECT_IND":
            if _is_embedded_device(addr) and addr.lower() != toilet_lower:
                tag = "← remote (embedded-device OUI)"
            else:
                tag = "← app / other"
        else:   # ADV_DIRECT_IND
            tag = "← toilet → directed advert"
            if remote_mac and addr.lower() == remote_mac.lower():
                tag += " (to remote)"

        line = f"  t={ts:>8.1f}s  {evt:<16}  {addr:<22}  {tag}"
        if markdown:
            lines.append(f"```")
            lines.append(line)
            lines.append(f"```")
            lines[-3] = f"`{line}`"
            lines.pop(-2)
            lines.pop(-1)
            lines.append(f"- `t={ts:>7.1f}s`  **{evt}**  `{addr}`  {tag}")
        else:
            lines.append(line)

    # --- Verdict ---
    lines.append("")
    n_remote = sum(1 for c in connect_inds
                   if _is_embedded_device(c["initiator"])
                   and c["initiator"].lower() != toilet_lower)
    n_other  = sum(1 for c in connect_inds
                   if not (_is_embedded_device(c["initiator"])
                           and c["initiator"].lower() != toilet_lower))
    n_direct = len(directed_advs)
    n_direct_to_remote = sum(
        1 for d in directed_advs
        if remote_mac and d["target"].lower() == remote_mac.lower()
    )

    if remote_mac:
        remote_line = f"Remote MAC (auto-detected): {remote_mac}"
    else:
        remote_line = "Remote MAC: not detected (no embedded-device OUI initiator found)"

    # Displacement verdict
    if n_remote > 0 and n_other > 0:
        # Check if remote connected AFTER at least one app/other session
        remote_ts = sorted(c["ts"] for c in connect_inds
                           if _is_embedded_device(c["initiator"])
                           and c["initiator"].lower() != toilet_lower)
        other_ts  = sorted(c["ts"] for c in connect_inds
                           if not (_is_embedded_device(c["initiator"])
                                   and c["initiator"].lower() != toilet_lower))
        last_other = max(other_ts)
        recoveries = sum(1 for t in remote_ts if t > last_other)
        if recoveries > 0:
            verdict = f"✅ NO displacement — remote reconnected after app session(s)"
        else:
            verdict = f"⚠️  Remote did NOT reconnect after last app/other session"
    elif n_remote > 0:
        verdict = f"ℹ️  Only remote connections seen (no app/other session to compare)"
    else:
        verdict = f"⚠️  No remote (embedded-device OUI) connections detected"

    direct_line = (
        f"ADV_DIRECT_IND from toilet: {n_direct} frame(s)"
        + (f", {n_direct_to_remote} addressed to remote" if remote_mac else "")
        + (" — remote must reconnect proactively" if n_direct_to_remote == 0 and n_remote > 0 else "")
    )

    if markdown:
        lines.append(f"**{remote_line}**  ")
        lines.append(f"**{verdict}**  ")
        lines.append(f"{direct_line}  ")
        lines.append(f"CONNECT_IND totals: remote={n_remote}  other={n_other}  ")
    else:
        lines.append(f"  {remote_line}")
        lines.append(f"  {verdict}")
        lines.append(f"  {direct_line}")
        lines.append(f"  CONNECT_IND totals: remote={n_remote}  other={n_other}")

    lines.append("")
    return "\n".join(lines)


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

    connect_inds, directed_advs = _get_connection_events(tshark, pcapng, mac)
    conn_events_plain = _format_connection_events(
        connect_inds, directed_advs, mac, markdown=False)
    conn_events_md = _format_connection_events(
        connect_inds, directed_advs, mac, markdown=True)

    if args.raw:
        print(conn_events_plain)
        for e in events:
            print(f"  {e['ts']:<12}  {e['direction']}  {e['type']:<30}  "
                  f"handle={e['att_handle']}  {e['value']}")
        return

    calls = _android_ble._collect_calls(events)

    if args.markdown:
        md = conn_events_md + "\n" + _android_ble.render_markdown_android(
            calls, pcapng, mac, "nRF52840 pcapng", att_count)
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"[+] Markdown written to {args.output}", file=sys.stderr)
        else:
            print(md)
    else:
        _print_mera_table(calls, pcapng, mac, att_count, conn_events_plain)


def _print_mera_table(calls, pcapng: Path, mac: str, att_count: int,
                      conn_events: str = "") -> None:
    """Compact procedure table (default non-markdown output for Mera)."""
    print(f"\n{'='*72}")
    print(f"File   : {pcapng.name}  [nRF52840 pcapng, {att_count:,} ATT frames]")
    print(f"Device : {mac}  (Geberit AquaClean Mera Comfort)")
    print(f"{'='*72}\n")
    if conn_events:
        print(conn_events)

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
# Alba — shared row helpers (also imported by arendi-decrypt-session.py)
# ---------------------------------------------------------------------------

def _get_arendi_rows(tshark: str, pcapng: Path,
                     mac: str, addr_field: str) -> tuple:
    """
    Fetch all ATT frames on the Alba Arendi channel and split into
    (pre_rows, main_rows).

    pre_rows  — frames with an empty peripheral_bd_addr; the connection was
                already active at capture start (no CONNECT_IND recorded).
    main_rows — frames whose peripheral_bd_addr matches *mac* (or all frames
                when *mac* is empty).

    Each row is [ts_raw, op_raw, handle_raw, value_raw] (4 strings).
    """
    dfilter = (f"(btatt.opcode == 0x52 || btatt.opcode == 0x12 || btatt.opcode == 0x1b)"
               f" && (btatt.handle == {_ALBA_WRITE_HANDLE}"
               f" || btatt.handle == {_ALBA_NOTIFY_HANDLE})")
    all_rows = _run_tshark(tshark, pcapng, dfilter,
                           ["frame.time_relative", "btatt.opcode",
                            "btatt.handle", "btatt.value", addr_field])
    if mac:
        pre_rows: list = []
        main_rows: list = []
        mac_lower = mac.lower()
        for row in all_rows:
            peripheral = row[4].strip() if len(row) > 4 else ""
            if peripheral.lower() == mac_lower:
                main_rows.append(row[:4])
            elif not peripheral:
                pre_rows.append(row[:4])
            # else: different peripheral, skip
    else:
        pre_rows  = []
        main_rows = [row[:4] for row in all_rows]
    return pre_rows, main_rows


def _parse_arendi_row(row: list):
    """
    Parse one row from *_get_arendi_rows* into (ts, opcode, handle, data).
    Returns None if the row is malformed or has missing fields.
    """
    if len(row) < 4:
        return None
    ts_raw, op_raw, handle_raw, value_raw = (row + [""] * 4)[:4]
    if not op_raw.strip() or not handle_raw.strip():
        return None
    try:
        opcode = _parse_int(op_raw)
        handle = _parse_int(handle_raw)
        data   = bytes.fromhex(_value_hex(value_raw))
    except (ValueError, TypeError):
        return None
    return _ts_display(ts_raw), opcode, handle, data


def _arendi_direction(opcode: int, handle: int):
    """Return "App→Dev", "Dev→App", or None (skip frame)."""
    if opcode in (_OP_WRITE_CMD, _OP_WRITE_REQ) and handle == _ALBA_WRITE_HANDLE:
        return "App→Dev"
    if opcode == _OP_NOTIF and handle == _ALBA_NOTIFY_HANDLE:
        return "Dev→App"
    return None


# ---------------------------------------------------------------------------
# Alba — reuse arendi-parse-capture.py decode pipeline
# ---------------------------------------------------------------------------

def _analyze_alba(tshark: str, pcapng: Path, mac: str, args,
                  addr_field: str) -> None:
    if not _ARENDI_AVAILABLE:
        print("Error: Alba analysis requires the bridge package (aquaclean_console_app). "
              "Run from the repo root with the venv active.", file=sys.stderr)
        sys.exit(1)

    pre_rows, main_rows = _get_arendi_rows(tshark, pcapng, mac, addr_field)

    print(f"=== Arendi Security Capture Parser (nRF52840) ===")
    print(f"File: {pcapng.name}")
    if mac:
        print(f"Alba: {mac}")
    print()

    if mac:
        connect_inds, directed_advs = _get_connection_events(tshark, pcapng, mac)
        print(_format_connection_events(connect_inds, directed_advs, mac, markdown=False))

    if pre_rows:
        print("Pre-capture connection (connection was already active at capture start)")
        print("-" * 72)
        pre_app_parser = _arendi._FrameParser()
        pre_dev_parser = _arendi._FrameParser()
        pre_state: dict = {}
        pre_total = 0
        for row in pre_rows:
            parsed = _parse_arendi_row(row)
            if parsed is None:
                continue
            ts, opcode, handle, data = parsed
            direction = _arendi_direction(opcode, handle)
            if direction is None:
                continue
            parser = pre_app_parser if direction == "App→Dev" else pre_dev_parser
            pre_total += 1
            for ctrl, payload, crc_ok in parser.feed(data):
                _arendi._print_frame(ts, direction, ctrl, payload, crc_ok, pre_state)
        print(f"\n  (pre-capture ATT PDUs: {pre_total})\n")

    app_parser = _arendi._FrameParser()
    dev_parser = _arendi._FrameParser()
    state: dict = {}
    total = 0

    for row in main_rows:
        parsed = _parse_arendi_row(row)
        if parsed is None:
            continue
        ts, opcode, handle, data = parsed
        direction = _arendi_direction(opcode, handle)
        if direction is None:
            continue
        parser = app_parser if direction == "App→Dev" else dev_parser
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

    # Infer alba from MAC OUI when auto-detection fails (e.g. no CONNECT_IND in file)
    if device_type is None and mac and mac[:8].lower() in _ALBA_DEVICE_OUIS:
        device_type = "alba"

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
