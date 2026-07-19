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
import datetime
import importlib.util
import re
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
_OP_ERROR_RSP         = 0x01   # ATT_ERROR_RSP      (peripheral → central)
_OP_READ_BY_TYPE_REQ  = 0x08   # ATT_READ_BY_TYPE_REQ (central → peripheral, carries UUID)
_OP_READ_BY_TYPE_RSP  = 0x09   # ATT_READ_BY_TYPE_RSP (peripheral → central)
_OP_READ_REQ          = 0x0A   # ATT_READ_REQ       (central → peripheral)
_OP_READ_RSP          = 0x0B   # ATT_READ_RSP       (peripheral → central)
_OP_WRITE_REQ         = 0x12   # ATT_WRITE_REQ      (also used for CCCD enables)
_OP_WRITE_CMD         = 0x52   # ATT_WRITE_CMD      (Geberit procedure requests)
_OP_NOTIF             = 0x1B   # ATT_HANDLE_VALUE_NOTIF (peripheral → central)

_OP_TO_EVENT = {
    _OP_ERROR_RSP:        "ATT_ERROR_RSP",
    _OP_READ_BY_TYPE_REQ: "ATT_READ_BY_TYPE_REQ",
    _OP_READ_BY_TYPE_RSP: "ATT_READ_BY_TYPE_RSP",
    _OP_READ_REQ:         "ATT_READ_REQ",
    _OP_READ_RSP:         "ATT_READ_RSP",
    _OP_WRITE_REQ:        "ATT_WRITE_REQ",
    _OP_WRITE_CMD:        "ATT_WRITE_CMD",
    _OP_NOTIF:            "ATT_HANDLE_VALUE_NOTIF",
}

# Opcodes that flow peripheral → central (RX from the Geberit device's perspective)
_RX_OPCODES = {_OP_NOTIF, _OP_READ_BY_TYPE_RSP, _OP_READ_RSP, _OP_ERROR_RSP}

# LL Control PDU opcodes (pre-encryption, visible to sniffer)
_LL_ENC_REQ_OPCODE = 0x03   # LL_ENC_REQ — central requests link-layer encryption

# LL Control PDU name table
_LL_CTRL_NAMES: dict = {
    0x00: "LL_CONNECTION_UPDATE_IND",
    0x01: "LL_CHANNEL_MAP_IND",
    0x02: "LL_TERMINATE_IND",
    0x03: "LL_ENC_REQ",
    0x04: "LL_ENC_RSP",
    0x05: "LL_START_ENC_REQ",
    0x06: "LL_START_ENC_RSP",
    0x07: "LL_UNKNOWN_RSP",
    0x08: "LL_FEATURE_REQ",
    0x09: "LL_FEATURE_RSP",
    0x0A: "LL_PAUSE_ENC_REQ",
    0x0B: "LL_PAUSE_ENC_RSP",
    0x0C: "LL_VERSION_IND",
    0x0D: "LL_REJECT_IND",
    0x0E: "LL_PERIPHERAL_FEATURE_REQ",
    0x0F: "LL_CONNECTION_PARAM_REQ",
    0x10: "LL_CONNECTION_PARAM_RSP",
    0x11: "LL_REJECT_EXT_IND",
    0x12: "LL_PING_REQ",
    0x13: "LL_PING_RSP",
    0x14: "LL_LENGTH_REQ",
    0x15: "LL_LENGTH_RSP",
    0x16: "LL_PHY_REQ",
    0x17: "LL_PHY_RSP",
    0x18: "LL_PHY_UPDATE_IND",
    0x19: "LL_MIN_USED_CHANNELS_IND",
}

_LL_TERMINATE_REASONS: dict = {
    0x02: "Unknown Conn ID",
    0x05: "Authentication Failure",
    0x08: "Connection Timeout",
    0x13: "Remote User Terminated",
    0x14: "Remote Low Resources",
    0x15: "Remote Power Off",
    0x16: "Local Host Terminated",
    0x1A: "Unsupported Remote Feature",
    0x3B: "Unacceptable Connection Parameters",
}

_BT_VERSIONS: dict = {
    6: "BT 4.0", 7: "BT 4.1", 8: "BT 4.2",
    9: "BT 5.0", 10: "BT 5.1", 11: "BT 5.2", 12: "BT 5.3",
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
                fields: list, occurrence: str = "f") -> list:
    """
    Run tshark with a display filter and field list.
    Returns list of rows, each row being a list of field strings.
    Separator is | (pipe) — safe because BLE values never contain it.
    occurrence: "f" (first, default) or "a" (all, comma-joined per field) —
    "a" is needed when one packet can carry more than one instance of the
    same AD-structure field (e.g. two Manufacturer Specific Data entries).
    """
    cmd = [tshark, "-r", str(pcapng), "-Y", display_filter,
           "-T", "fields", "-E", "separator=|", "-E", f"occurrence={occurrence}"]
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


def _get_capture_start(tshark: str, pcapng: Path) -> tuple:
    """
    Return (epoch_base, tz, start_str) from the first packet of a pcapng.

    epoch_base — float Unix epoch seconds (microsecond precision)
    tz         — datetime.timezone parsed from the capture's local clock, or None
    start_str  — formatted header string e.g. "2026-06-26 04:29:26.649 +0200"
    """
    rows = _run_tshark(tshark, pcapng, "frame.number == 1",
                       ["frame.time_epoch", "frame.time"])
    if not rows or not rows[0][0].strip():
        return (0.0, None, "")

    try:
        epoch_base = float(rows[0][0].strip())
    except ValueError:
        return (0.0, None, "")

    tz = None
    start_str = ""
    if len(rows[0]) > 1:
        raw = rows[0][1].strip()   # e.g. "2026-06-26T04:29:26.649664000+0200"
        m = re.search(r'([+-])(\d{2})(\d{2})$', raw)
        if m:
            sign = 1 if m.group(1) == "+" else -1
            offset = datetime.timedelta(hours=int(m.group(2)),
                                        minutes=int(m.group(3)))
            tz = datetime.timezone(sign * offset)

    if tz is not None:
        dt = datetime.datetime.fromtimestamp(epoch_base, tz=tz)
        ms  = dt.microsecond // 1000
        tz_str = dt.strftime("%z")
        start_str = dt.strftime(f"%Y-%m-%d %H:%M:%S.{ms:03d} {tz_str}")

    return (epoch_base, tz, start_str)


def _abs_ts(epoch_base: float, rel_s: float,
            tz: "datetime.timezone | None") -> str:
    """
    Convert a relative timestamp (seconds since capture start) to HH:MM:SS.mmm
    using the capture's local timezone.  Returns empty string if unavailable.
    """
    if not tz or not epoch_base:
        return ""
    dt  = datetime.datetime.fromtimestamp(epoch_base + rel_s, tz=tz)
    ms  = dt.microsecond // 1000
    return dt.strftime(f"%H:%M:%S.{ms:03d}")


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
                        "btle.advertising_address",
                        "btle.link_layer_data.interval",
                        "btle.link_layer_data.latency",
                        "btle.link_layer_data.timeout"])
    connect_inds = []
    for row in rows:
        if len(row) < 3:
            continue
        padded = (row + [""] * 6)[:6]
        ts_s, initiator, advertiser, ci_raw, lat_raw, to_raw = padded
        ts_s, initiator, advertiser = ts_s.strip(), initiator.strip(), advertiser.strip()
        if advertiser.lower() != toilet_lower:
            continue
        if not initiator:
            continue
        try:
            ts_f = float(ts_s)
        except ValueError:
            continue
        entry: dict = {"ts": ts_f, "initiator": initiator.upper()}
        try:
            entry["ci_ms"] = _parse_int(ci_raw) * 1.25
        except (ValueError, TypeError):
            pass
        try:
            entry["latency"] = _parse_int(lat_raw)
        except (ValueError, TypeError):
            pass
        try:
            entry["timeout_ms"] = _parse_int(to_raw) * 10
        except (ValueError, TypeError):
            pass
        connect_inds.append(entry)

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

    # Build quick lookup: ts → CI params dict for CONNECT_INDs
    _ci_by_ts: dict = {c["ts"]: c for c in connect_inds}

    # Print timeline
    conn_index = 0
    for ts, evt, addr in all_events:
        if evt == "CONNECT_IND":
            conn_index += 1
            if _is_embedded_device(addr) and addr.lower() != toilet_lower:
                tag = "← remote (embedded-device OUI)"
            else:
                tag = "← app / other"
            ci_info = _ci_by_ts.get(ts, {})
            params  = ""
            if "ci_ms" in ci_info:
                params += f"  CI={ci_info['ci_ms']:.2f}ms"
            if "latency" in ci_info:
                params += f"  latency={ci_info['latency']}"
            if "timeout_ms" in ci_info:
                params += f"  supv={ci_info['timeout_ms']}ms"
        else:   # ADV_DIRECT_IND
            tag    = "← toilet → directed advert"
            params = ""
            if remote_mac and addr.lower() == remote_mac.lower():
                tag += " (to remote)"

        line = f"  t={ts:>8.1f}s  {evt:<16}  {addr:<22}  {tag}{params}"
        if markdown:
            lines.append(f"- `t={ts:>7.1f}s`  **{evt}**  `{addr}`  {tag}{params}")
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
# GATT handle → UUID mapping
# ---------------------------------------------------------------------------

def _run_tshark_pdml(tshark: str, pcapng: Path, display_filter: str) -> list:
    """Run tshark with -T pdml (XML) and return every matching frame's
    <proto name="btatt"> element.

    PDML is the only tshark output format that preserves the REAL tree
    structure of repeated sibling fields without collapsing or flattening
    them by field name. Confirmed 2026-07-14/18 (see docs/developer/
    nrf-ble-analyze-completeness-audit.md): for GATT discovery response
    opcodes (0x05/0x09/0x11), the SAME field name (e.g. btatt.uuid16) can
    appear at multiple tree depths per frame — once as the real per-entry
    value, and again as an unrelated "attribute type" echo or a decorative
    per-handle lookup annotation. -T fields (used by _run_tshark) flattens
    by field name regardless of depth, silently conflating these. -T json
    is no better — it collapses repeated same-named sibling elements (e.g.
    multiple attribute_data entries) down to just the last one. Only PDML's
    XML tree lets us walk each entry's DIRECT children explicitly and avoid
    both traps.
    """
    import xml.etree.ElementTree as ET
    cmd = [tshark, "-r", str(pcapng), "-Y", display_filter, "-T", "pdml"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        root = ET.fromstring(result.stdout)
    except ET.ParseError:
        return []
    return [
        proto
        for packet in root.findall("packet")
        for proto in packet.findall("proto")
        if proto.get("name") == "btatt"
    ]


def _extract_gatt_handles(tshark: str, pcapng: Path) -> dict:
    """Extract handle→UUID map from GATT discovery frames in a capture.

    Parses three ATT discovery response opcodes:
      0x11 READ_BY_GROUP_TYPE_RSP  — service declarations (start handle, UUID)
      0x09 READ_BY_TYPE_RSP        — characteristic declarations (decl+value handles, UUID)
      0x05 FIND_INFO_RSP            — descriptor handles (handle, UUID — 1:1 mapping)

    Rewritten 2026-07-18 to use -T pdml (_run_tshark_pdml) instead of flat
    -T fields extraction. All three opcodes routinely pack MULTIPLE entries
    into one response PDU — not an edge case, the common case for any
    service/characteristic list longer than one item — and each entry's own
    handle/UUID fields share a name with unrelated fields at other tree
    depths in the same frame (see _run_tshark_pdml's docstring). The
    previous -T fields + occurrence="a" + comma-split approach (the code
    already anticipated multi-value output, it just never got any, and
    naively "fixing" that by flipping occurrence produced MISALIGNED
    handle<->UUID pairs, confirmed by direct testing — worse than the
    original under-reporting).

    Both 0x11 and 0x09 use the wire-format element name "attribute_data";
    0x05 uses "information_data". Each entry's LAST direct-child
    "btatt.handle" is the real addressable handle (for 0x09 this correctly
    picks the characteristic VALUE handle, not the declaration handle, since
    it's the second of two handle children in document order); the entry's
    own direct-child uuid16/uuid128 (not any uuid16 nested inside a handle's
    own sub-tree, which is a decorative "known attribute" annotation, not
    the entry's real value) is the UUID.

    Returns dict[int handle → str uuid].  Called from --gatt-map and included
    in markdown output so future captures can be explored without ad-hoc tshark.
    """
    handle_map: dict = {}
    dfilter = "btatt.opcode == 0x11 || btatt.opcode == 0x09 || btatt.opcode == 0x05"
    for btatt in _run_tshark_pdml(tshark, pcapng, dfilter):
        for entry_name in ("btatt.attribute_data", "btatt.information_data"):
            for entry in btatt.findall(f"field[@name='{entry_name}']"):
                handle = None
                uuid = None
                for child in entry:
                    name = child.get("name")
                    if name == "btatt.handle":
                        try:
                            handle = int(child.get("show"), 16)
                        except (TypeError, ValueError):
                            pass
                    elif name == "btatt.uuid128":
                        uuid = child.get("show")
                    elif name == "btatt.uuid16":
                        raw = child.get("show")
                        try:
                            uuid = f"0x{int(raw, 16):04X}"
                        except (TypeError, ValueError):
                            uuid = raw
                if handle is not None and uuid is not None and handle not in handle_map:
                    handle_map[handle] = uuid
    return handle_map


def _format_gatt_map(handle_map: dict) -> str:
    """Format handle→UUID map as a readable table."""
    if not handle_map:
        return "  (no GATT discovery frames found)\n"
    lines = ["  Handle  UUID"]
    lines.append("  " + "-" * 40)
    for handle in sorted(handle_map):
        lines.append(f"  0x{handle:04X}   {handle_map[handle]}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# BLE LL encryption detection
# ---------------------------------------------------------------------------

def _detect_ll_encryption(tshark: str, pcapng: Path) -> dict | None:
    """
    Scan for LL_ENC_REQ (opcode 0x03) in data channel frames.

    LL Control PDUs are sent in plaintext before encryption starts, so the
    nRF52840 sniffer captures them even when all subsequent ATT frames are
    AES-CCM encrypted.  Returns {ts, ediv, rand_hex} when found, else None.

    Called when no ATT frames are decoded for a Mera connection — replaces
    the misleading "No Geberit ATT frames found" message with an actionable
    diagnostic pointing to the BlueZ/btmon alternative.

    tshark field notes:
      btle.control.random_number       — Rand as decimal uint64, LE-packed on wire
      btle.control.encrypted_diversifier — EDIV as decimal uint16
    """
    rows = _run_tshark(tshark, pcapng,
                       "btle.control_opcode == 0x03",
                       ["frame.time_relative",
                        "btle.control.random_number",
                        "btle.control.encrypted_diversifier"])
    if not rows:
        return None
    for row in rows:
        ts_raw   = row[0].strip() if len(row) > 0 else ""
        rand_raw = row[1].strip() if len(row) > 1 else ""
        ediv_raw = row[2].strip() if len(row) > 2 else ""
        try:
            ediv = int(ediv_raw) if ediv_raw else None
        except ValueError:
            ediv = None
        try:
            # tshark returns Rand as a big-endian uint64; recover the 8 wire bytes
            # in little-endian order (as transmitted on air)
            rand_bytes = int(rand_raw).to_bytes(8, "big")[::-1]
            rand_hex = rand_bytes.hex()
        except (ValueError, OverflowError):
            rand_hex = ""
        return {
            "ts":       _ts_display(ts_raw),
            "ediv":     ediv,
            "rand_hex": rand_hex,
        }
    return None


def _report_ll_encryption(enc: dict, mac: str) -> None:
    """Print a diagnostic when LL_ENC_REQ is detected instead of ATT frames."""
    ediv_str = f"0x{enc['ediv']:04x}" if enc["ediv"] is not None else "unknown"
    rand_str  = (
        " ".join(enc["rand_hex"][i:i+2] for i in range(0, len(enc["rand_hex"]), 2))
        if enc["rand_hex"] else "unknown"
    )
    target = f" for {mac}" if mac else ""
    print(
        f"\n[!] BLE LL encryption detected{target} — ATT frames are AES-CCM encrypted.\n"
        f"    LL_ENC_REQ at {enc['ts']}  EDIV={ediv_str}  Rand={rand_str}\n\n"
        f"    The remote uses BLE SMP bonding; tshark cannot decode the payload\n"
        f"    without the Long Term Key (LTK) stored on the peripheral.\n\n"
        f"    Alternative: pair the remote with a Linux BlueZ peripheral hub and\n"
        f"    use btmon to capture decrypted ATT frames (BlueZ stores LTK automatically).\n"
        f"    See docs/developer/aquaclean-application-layer-relay.md § 8.5.\n",
        file=sys.stderr,
    )


def _render_ll_encryption_markdown(enc: dict, pcapng: Path, mac: str,
                                    connect_inds: list,
                                    directed_advs: list) -> str:
    """Build a markdown analysis document for a capture with LL encryption."""
    ediv_str = f"0x{enc['ediv']:04x}" if enc["ediv"] is not None else "unknown"
    rand_str  = (
        " ".join(enc["rand_hex"][i:i+2] for i in range(0, len(enc["rand_hex"]), 2))
        if enc["rand_hex"] else "unknown"
    )
    conn_events_md = _format_connection_events(
        connect_inds, directed_advs, mac, markdown=True)

    lines = [
        f"# BLE Capture Analysis — {pcapng.name}",
        "",
        f"**Device:** `{mac}` (Geberit AquaClean Mera Comfort)",
        f"**Source:** nRF52840 pcapng",
        "",
        "---",
        "",
        conn_events_md,
        "## BLE Link-Layer Encryption",
        "",
        "ATT application frames are **AES-CCM encrypted** — tshark cannot decode",
        "the payload without the Long Term Key (LTK) stored on the peripheral.",
        "",
        "### LL_ENC_REQ parameters",
        "",
        "| Field | Value |",
        "|-------|-------|",
        f"| Timestamp | `{enc['ts']}` |",
        f"| EDIV | `{ediv_str}` |",
        f"| Rand | `{rand_str}` |",
        "",
        "### Protocol sequence",
        "",
        "```",
        "CONNECT_IND        remote → toilet     (connection established)",
        "LL_ENC_REQ         remote → toilet     (central requests encryption, plaintext)",
        "LL_ENC_RSP         toilet → remote     (peripheral acknowledges, plaintext)",
        "LL_START_ENC_REQ   remote → toilet     (plaintext — signals encryption start)",
        "LL_START_ENC_RSP   toilet → remote     (first encrypted frame)",
        "LL_START_ENC_RSP   remote → toilet     (encryption active on both sides)",
        "<all subsequent L2CAP/ATT frames are AES-CCM encrypted>",
        "```",
        "",
        "### Why tshark cannot decode",
        "",
        "The nRF52840 sniffer captures raw radio frames including all LL Control PDUs.",
        "Once `LL_START_ENC_RSP` confirms encryption, the sniffer sees only ciphertext.",
        "tshark requires the session key (derived from LTK + SKD + IV) to decrypt.",
        "The LTK is stored in the toilet's non-volatile memory — not extractable from",
        "a passive sniffer capture without prior bonding knowledge.",
        "",
        "### Path forward — BlueZ pairing",
        "",
        "Pair the remote with a Linux BlueZ peripheral hub (acting as the toilet).",
        "BlueZ negotiates SMP automatically and stores the LTK.",
        "`btmon` then shows decrypted ATT frames at the kernel level — no manual",
        "key extraction needed.",
        "",
        "See `docs/developer/aquaclean-application-layer-relay.md` § 8.5.",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Mera Comfort — reuse android-ble-analyze.py decode pipeline
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# BLE Control Layer — LL Control PDUs, L2CAP signaling, ATT meta
# ---------------------------------------------------------------------------

def _get_ll_control_events(tshark: str, pcapng: Path) -> list:
    """Extract all LL Control PDUs with decoded fields."""
    rows = _run_tshark(tshark, pcapng,
                       "btle.control_opcode",
                       ["frame.time_relative",
                        "btle.control_opcode",
                        "btle.control.error_code",           # LL_TERMINATE_IND reason
                        "btle.control.feature_set",          # LL_FEATURE_*
                        "btle.control.version_number",       # LL_VERSION_IND
                        "btle.control.company_id",           # LL_VERSION_IND
                        "btle.control.interval",             # LL_CONNECTION_UPDATE_IND / PARAM_*
                        "btle.control.latency",
                        "btle.control.timeout",
                        "btle.control.max_rx_octets",        # LL_LENGTH_REQ/RSP
                        "btle.control.max_tx_octets",
                        "btle.control.max_rx_time",
                        "btle.control.max_tx_time",
                        "btle.control.tx_phys",              # LL_PHY_REQ/RSP
                        "btle.control.rx_phys",
                        "btle.control.m_to_s_phy",           # LL_PHY_UPDATE_IND
                        "btle.control.s_to_m_phy",
                        "btle.control.instant"])
    _PHY_MAP = {"1": "1M", "2": "2M", "3": "Coded", "4": "Coded"}
    events = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        padded = (row + [""] * 18)[:18]
        ts_raw, op_raw = padded[0].strip(), padded[1].strip()
        if not op_raw:
            continue
        try:
            opcode = _parse_int(op_raw)
            ts_f   = float(ts_raw)
        except ValueError:
            continue
        name    = _LL_CTRL_NAMES.get(opcode, f"LL_CTRL_0x{opcode:02X}")
        details = []
        if opcode == 0x02:  # LL_TERMINATE_IND
            err_raw = padded[2].strip()
            try:
                err    = _parse_int(err_raw)
                reason = _LL_TERMINATE_REASONS.get(err, f"0x{err:02X}")
                details.append(f"reason={reason}")
            except ValueError:
                pass
        elif opcode in (0x08, 0x09, 0x0E):  # LL_FEATURE_REQ/RSP
            feat = padded[3].strip()
            if feat:
                details.append(f"features={feat}")
        elif opcode == 0x0C:  # LL_VERSION_IND
            ver     = padded[4].strip()
            company = padded[5].strip()
            if ver:
                try:
                    ver_int = _parse_int(ver)
                    details.append(_BT_VERSIONS.get(ver_int, f"v0x{ver_int:02X}"))
                except ValueError:
                    details.append(f"v{ver}")
            if company:
                try:
                    details.append(f"company=0x{_parse_int(company):04X}")
                except ValueError:
                    details.append(f"company={company}")
        elif opcode in (0x00, 0x0F, 0x10):  # CONN_UPDATE_IND / PARAM_REQ/RSP
            ci_raw, lat_raw, to_raw = padded[6].strip(), padded[7].strip(), padded[8].strip()
            try:
                details.append(f"CI={_parse_int(ci_raw) * 1.25:.2f}ms")
            except ValueError:
                pass
            try:
                details.append(f"latency={_parse_int(lat_raw)}")
            except ValueError:
                pass
            try:
                details.append(f"supv={_parse_int(to_raw) * 10}ms")
            except ValueError:
                pass
            instant = padded[17].strip()
            if instant:
                try:
                    details.append(f"instant={_parse_int(instant)}")
                except ValueError:
                    pass
        elif opcode in (0x14, 0x15):  # LL_LENGTH_REQ/RSP
            for label, idx in (("maxRxOct", 9), ("maxTxOct", 10),
                                ("maxRxTime", 11), ("maxTxTime", 12)):
                raw = padded[idx].strip()
                if raw:
                    try:
                        v = _parse_int(raw)
                        suffix = "μs" if "Time" in label else ""
                        details.append(f"{label}={v}{suffix}")
                    except ValueError:
                        pass
        elif opcode in (0x16, 0x17):  # LL_PHY_REQ/RSP
            tx_raw, rx_raw = padded[13].strip(), padded[14].strip()
            if tx_raw:
                try:
                    details.append(f"tx={_PHY_MAP.get(str(_parse_int(tx_raw)), tx_raw)}")
                except ValueError:
                    pass
            if rx_raw:
                try:
                    details.append(f"rx={_PHY_MAP.get(str(_parse_int(rx_raw)), rx_raw)}")
                except ValueError:
                    pass
        elif opcode == 0x18:  # LL_PHY_UPDATE_IND
            m2s, s2m = padded[15].strip(), padded[16].strip()
            if m2s:
                try:
                    details.append(f"M→S={_PHY_MAP.get(str(_parse_int(m2s)), m2s)}")
                except ValueError:
                    pass
            if s2m:
                try:
                    details.append(f"S→M={_PHY_MAP.get(str(_parse_int(s2m)), s2m)}")
                except ValueError:
                    pass
        events.append({
            "ts_raw": ts_f,
            "ts":     _ts_display(ts_raw),
            "opcode": opcode,
            "name":   name,
            "details": "  ".join(details),
        })
    return events


def _get_l2cap_events(tshark: str, pcapng: Path) -> list:
    """Extract L2CAP Connection Parameter Update requests/responses (iOS → CI negotiation)."""
    rows = _run_tshark(tshark, pcapng,
                       "btl2cap.cmd_code == 0x12 || btl2cap.cmd_code == 0x13",
                       ["frame.time_relative",
                        "btl2cap.cmd_code",
                        "btl2cap.min_interval",
                        "btl2cap.max_interval",
                        "btl2cap.peripheral_latency",
                        "btl2cap.timeout_multiplier"])
    events = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        padded = (row + [""] * 6)[:6]
        ts_raw, cmd_raw, f1, f2, f3, f4 = padded
        try:
            cmd  = _parse_int(cmd_raw)
            ts_f = float(ts_raw.strip())
        except ValueError:
            continue
        name    = "L2CAP_CONN_PARAM_UPDATE_REQ" if cmd == 0x12 else "L2CAP_CONN_PARAM_UPDATE_RSP"
        details = []
        if cmd == 0x12:
            try:
                min_ci = _parse_int(f1) * 1.25
                max_ci = _parse_int(f2) * 1.25
                details.append(f"CI={min_ci:.2f}–{max_ci:.2f}ms")
            except ValueError:
                pass
            try:
                details.append(f"latency={_parse_int(f3)}")
            except ValueError:
                pass
            try:
                details.append(f"supv={_parse_int(f4) * 10}ms")
            except ValueError:
                pass
        else:
            try:
                result = _parse_int(f1)
                details.append("accepted" if result == 0 else f"rejected(0x{result:04X})")
            except ValueError:
                pass
        events.append({
            "ts_raw": ts_f,
            "ts":     _ts_display(ts_raw),
            "name":   name,
            "details": "  ".join(details),
        })
    return events


def _get_att_meta_events(tshark: str, pcapng: Path,
                         mac: str, addr_field: str) -> list:
    """Extract ATT setup frames: MTU exchange and Write RSP confirmations."""
    dfilter = "btatt.opcode == 0x02 || btatt.opcode == 0x03 || btatt.opcode == 0x13"
    if mac:
        dfilter = f"({dfilter}) && {addr_field} == {mac.lower()}"
    rows = _run_tshark(tshark, pcapng, dfilter,
                       ["frame.time_relative", "btatt.opcode",
                        "btatt.client_rx_mtu", "btatt.server_rx_mtu", "btatt.handle"])
    _ATT_META_NAMES = {
        0x02: "ATT_MTU_REQ",
        0x03: "ATT_MTU_RSP",
        0x13: "ATT_WRITE_RSP",
    }
    events = []
    for row in rows:
        if not row or not row[0].strip():
            continue
        padded = (row + [""] * 5)[:5]
        ts_raw, op_raw, client_mtu_raw, server_mtu_raw, handle_raw = padded
        try:
            opcode = _parse_int(op_raw)
            ts_f   = float(ts_raw.strip())
        except ValueError:
            continue
        name    = _ATT_META_NAMES.get(opcode, f"ATT_0x{opcode:02X}")
        details = []
        if opcode == 0x02 and client_mtu_raw.strip():
            try:
                details.append(f"clientMTU={_parse_int(client_mtu_raw)}")
            except ValueError:
                pass
        elif opcode == 0x03 and server_mtu_raw.strip():
            try:
                details.append(f"serverMTU={_parse_int(server_mtu_raw)}")
            except ValueError:
                pass
        if opcode == 0x13 and handle_raw.strip():
            try:
                details.append(f"handle=0x{_parse_int(handle_raw):04X}")
            except ValueError:
                pass
        events.append({
            "ts_raw": ts_f,
            "ts":     _ts_display(ts_raw),
            "opcode": opcode,
            "name":   name,
            "details": "  ".join(details),
        })
    return events


def _format_ctrl_section(ll_events: list, l2cap_events: list,
                          att_meta: list, markdown: bool,
                          epoch_base: float = 0.0, tz=None) -> str:
    """Render LL Control + L2CAP + ATT meta as a markdown or plain-text section."""
    lines: list[str] = []
    if markdown:
        lines.append("## BLE Control Layer\n")
    else:
        lines.append("BLE Control Layer")
        lines.append("-" * 72)

    # Merge and sort all control-layer events; tuple: (ts_raw_f, layer, rel_ts, name, details)
    all_ctrl = (
        [(e["ts_raw"], "LL",    e["ts"], e["name"], e["details"]) for e in ll_events]
      + [(e["ts_raw"], "L2CAP", e["ts"], e["name"], e["details"]) for e in l2cap_events]
      + [(e["ts_raw"], "ATT",   e["ts"], e["name"], e["details"]) for e in att_meta]
    )
    all_ctrl.sort()

    if not all_ctrl:
        msg = "No LL Control, L2CAP, or ATT setup frames found."
        lines.append(f"_{msg}_" if markdown else f"  {msg}")
    elif markdown:
        lines.append("| Time | Layer | PDU | Details |")
        lines.append("|------|-------|-----|---------|")
        for ts_f, layer, rel_ts, name, details in all_ctrl:
            ts = _abs_ts(epoch_base, ts_f, tz) or rel_ts
            lines.append(f"| `{ts}` | {layer} | `{name}` | {details or '—'} |")
    else:
        lines.append(f"  {'Time':<12}  {'Layer':<6}  {'PDU':<35}  Details")
        lines.append(f"  {'-'*12}  {'-'*6}  {'-'*35}  {'-'*30}")
        for ts_f, layer, rel_ts, name, details in all_ctrl:
            ts = _abs_ts(epoch_base, ts_f, tz) or rel_ts
            lines.append(f"  {ts:<12}  {layer:<6}  {name:<35}  {details or ''}")

    lines.append("")
    return "\n".join(lines)


def _get_adv_packets(tshark: str, pcapng: Path, mac: str) -> list:
    """Extract ADV_IND and SCAN_RSP advertising packets for the target MAC.

    Issues two separate _run_tshark queries (one per PDU type) and filters
    by MAC address in Python — mirrors the pattern used in _get_connection_events.
    Returns list of dicts: {ts, pdu, uuids16, company, name}
    """
    mac_lower = mac.lower()
    packets = []

    def _norm_uuid(raw: str) -> str:
        """Strip tshark's 0x prefix so we can format consistently."""
        s = raw.strip()
        return s[2:] if s.lower().startswith("0x") else s

    def _addr_matches(addr_field: str) -> bool:
        """Accept row when address matches mac or when tshark left the field empty."""
        a = addr_field.strip().lower()
        return a == mac_lower or a == ""

    # ADV_IND (pdu_type=0): primary advertisement — contains UUID16, manufacturer data.
    # occurrence="a" on type/company_id/data (not the tool's usual "f"/first-only) —
    # a single ADV_IND can carry more than one Manufacturer Specific Data (0xFF) AD
    # structure, e.g. Mera's real advertisement: company=0x0100 (state+article) is a
    # SEPARATE, shorter AD entry from a second one under a bogus/reserved company ID
    # (2026-07-18 finding — see docs/developer/mera-home-app-onboarding.md). "f" would
    # silently report only the first and hide the second.
    for row in _run_tshark(tshark, pcapng,
                           "btle.advertising_header.pdu_type == 0x00",
                           ["frame.time_relative", "btle.advertising_address",
                            "btcommon.eir_ad.entry.uuid_16",
                            "btcommon.eir_ad.entry.type",
                            "btcommon.eir_ad.entry.company_id",
                            "btcommon.eir_ad.entry.data"],
                           occurrence="a"):
        if len(row) < 6:
            continue
        ts_s, addr, uuid_raw, type_raw, company_raw, data_raw = row[:6]
        if addr.strip().lower() != mac_lower:
            continue
        try:
            ts = float(ts_s.strip())
        except ValueError:
            continue
        uuids = [_norm_uuid(u) for u in uuid_raw.split(",") if u.strip()]
        ad_types = [t.strip() for t in type_raw.split(",") if t.strip()]
        ad_companies = [c.strip() for c in company_raw.split(",") if c.strip()]
        ad_datas = [d.strip() for d in data_raw.split(",") if d.strip()]
        packets.append({
            "ts": ts, "pdu": "ADV_IND",
            "uuids16": uuids,
            "company": ad_companies[0] if ad_companies else None,
            "name": None,
            "ad_types": ad_types,
            "ad_companies": ad_companies,
            "ad_datas": ad_datas,
        })

    # SCAN_RSP (pdu_type=4): Wireshark 4.x exposes raw payload in btle.scan_response_data
    # (colon-separated hex bytes).  Parse the AD structure ourselves — more reliable than
    # btcommon.eir_ad field extraction which tshark sometimes skips for SCAN_RSP.
    _SCAN_RSP_FILTER = "btle.advertising_header.pdu_type == 0x04"

    def _parse_ad_bytes(raw_hex: str) -> tuple:
        """Parse BLE AD structure from colon-separated hex.  Returns (uuids16, name)."""
        uuids16: list = []
        name_str = None
        data = bytes.fromhex(raw_hex.replace(":", ""))
        i = 0
        while i < len(data):
            length = data[i]
            if length == 0 or i + length >= len(data):
                break
            ad_type = data[i + 1]
            payload = data[i + 2: i + 1 + length]
            if ad_type in (0x02, 0x03):
                for j in range(0, len(payload) - 1, 2):
                    uuid_val = payload[j] | (payload[j + 1] << 8)
                    uuids16.append(f"{uuid_val:04x}")
            elif ad_type in (0x08, 0x09):
                name_str = payload.decode("utf-8", errors="replace")
            i += 1 + length
        return uuids16, name_str

    srsp_rows = _run_tshark(tshark, pcapng, _SCAN_RSP_FILTER,
                             ["frame.time_relative", "btle.advertising_address",
                              "btle.scan_response_data"])
    seen_srsp: set = set()
    for row in srsp_rows:
        if len(row) < 2:
            continue
        ts_s, addr = row[0], row[1]
        if not _addr_matches(addr):
            continue
        try:
            ts = float(ts_s.strip())
        except ValueError:
            continue
        raw_hex = row[2].strip() if len(row) >= 3 else ""
        uuids16, name_str = [], None
        if raw_hex:
            try:
                uuids16, name_str = _parse_ad_bytes(raw_hex)
            except Exception:
                pass
        key = (tuple(sorted(uuids16)), name_str)
        if key in seen_srsp:
            continue
        seen_srsp.add(key)
        packets.append({"ts": ts, "pdu": "SCAN_RSP",
                         "uuids16": uuids16, "company": None, "name": name_str})

    packets.sort(key=lambda p: p["ts"])
    return packets


def _print_adv_packets(packets: list, mac: str, pcapng_name: str) -> None:
    """Print advertising packet summary — unique combinations only."""
    print(f"\nAdvertising packets from {mac}  ({pcapng_name})")
    print("-" * 72)
    if not packets:
        print("  No ADV_IND or SCAN_RSP found for this MAC.")
        return

    seen: set = set()
    for p in packets:
        ad_companies = tuple(p.get("ad_companies") or ())
        ad_datas = tuple(p.get("ad_datas") or ())
        key = (p["pdu"], tuple(sorted(p["uuids16"])), ad_companies, ad_datas, p["name"])
        if key in seen:
            continue
        seen.add(key)
        uuids_str = ", ".join(f"0x{u.upper()}" for u in p["uuids16"]) if p["uuids16"] else "(none)"
        print(f"  t={p['ts']:>8.1f}s  {p['pdu']:<16}  UUIDs={uuids_str}")
        # Print each Manufacturer Specific Data AD entry separately — a single
        # advertisement can carry more than one (2026-07-18 finding).
        ad_types = p.get("ad_types") or ()
        for i, company in enumerate(ad_companies):
            type_hex = ad_types[i] if i < len(ad_types) else "?"
            data_hex = ad_datas[i] if i < len(ad_datas) else ""
            print(f"              {'':16}  AD type={type_hex}  company={company}  data=0x{data_hex}")
        if len(ad_companies) > 1:
            print(f"              {'':16}  ↑ {len(ad_companies)} separate Manufacturer Specific Data AD structures in this packet")
        if p["name"]:
            print(f"              {'':16}  name={p['name']!r}")

    print()
    print(f"  Total captured:  {len(packets)}")
    has_scan_rsp = any(p["pdu"] == "SCAN_RSP" for p in packets)
    print(f"  SCAN_RSP frames: {'yes ← active scan received by sniffer' if has_scan_rsp else 'no (passive capture or device does not respond to SCAN_REQ)'}")
    all_uuids = sorted({u for p in packets for u in p["uuids16"]})
    if all_uuids:
        print(f"  All 16-bit UUIDs seen: {', '.join(f'0x{u.upper()}' for u in all_uuids)}")


def _format_adv_section(packets: list, mac: str) -> str:
    """Markdown rendering of the same deduplicated advertising summary
    _print_adv_packets prints as plain text — for --markdown --include-adv.
    Deliberately a flat table, not phase-grouped like the rest of the
    markdown: advertising is continuous pre-connection beacon traffic, not
    request/response pairs, so it doesn't fit that structure. Deduplicated
    to unique combinations (first occurrence's timestamp kept) rather than
    every raw packet — a real capture can have hundreds of near-identical
    advertisements before a connection completes."""
    lines = ["## Advertising (pre-connection)",
             "",
             f"*Pre-connection ADV_IND/SCAN_RSP for `{mac}`, deduplicated to unique "
             "combinations — not phase-grouped like the rest of this document, and not "
             "validated the way connected-session traffic below is. Real captures often "
             "have RF noise here (garbled UUIDs/names/company IDs on unrelated or "
             "corrupted frames) — treat unexpected values with suspicion.*",
             ""]
    if not packets:
        lines.append("No ADV_IND or SCAN_RSP found for this MAC.")
        lines.append("")
        return "\n".join(lines) + "\n"

    lines.append("| Time | PDU | UUIDs | Manufacturer data | Name |")
    lines.append("|------|-----|-------|--------------------|------|")
    seen: set = set()
    for p in packets:
        ad_companies = tuple(p.get("ad_companies") or ())
        ad_datas = tuple(p.get("ad_datas") or ())
        key = (p["pdu"], tuple(sorted(p["uuids16"])), ad_companies, ad_datas, p["name"])
        if key in seen:
            continue
        seen.add(key)
        uuids_str = ", ".join(f"0x{u.upper()}" for u in p["uuids16"]) if p["uuids16"] else "(none)"
        ad_types = p.get("ad_types") or ()
        mfg_parts = []
        for i, company in enumerate(ad_companies):
            type_hex = ad_types[i] if i < len(ad_types) else "?"
            data_hex = ad_datas[i] if i < len(ad_datas) else ""
            mfg_parts.append(f"type={type_hex} company={company} data=0x{data_hex}")
        mfg_str = "; ".join(mfg_parts) if mfg_parts else "(none)"
        name_str = repr(p["name"]) if p["name"] else "(none)"
        lines.append(f"| `t={p['ts']:.1f}s` | {p['pdu']} | {uuids_str} | {mfg_str} | {name_str} |")

    lines.append("")
    has_scan_rsp = any(p["pdu"] == "SCAN_RSP" for p in packets)
    lines.append(f"**Total captured:** {len(packets)}  \n"
                 f"**SCAN_RSP frames:** {'yes — active scan received by sniffer' if has_scan_rsp else 'no (passive capture, or device does not respond to SCAN_REQ)'}")
    all_uuids = sorted({u for p in packets for u in p["uuids16"]})
    if all_uuids:
        lines.append(f"**All 16-bit UUIDs seen:** {', '.join(f'0x{u.upper()}' for u in all_uuids)}")
    lines.append("")
    lines.append("---")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Geberit bidirectional raw traffic log (every write + notify frame)
# ---------------------------------------------------------------------------

def _decode_write_payload(v: bytes) -> str:
    """Decode a Geberit WRITE_CMD/WRITE_REQ payload (handle 0x0003)."""
    if not v:
        return "(empty)"
    h = v[0]
    if h in (0x60, 0x70) or (h & 0x80):
        return f"CTRL  byte0=0x{h:02x}"
    parsed = _android_ble._parse_req(v)
    if parsed:
        ctx, proc, _arg_len, args = parsed
        name = (_android_ble._PROC_NAMES_MD.get((ctx, proc))
                or _android_ble._PROC_NAMES_MD.get((0, proc))
                or _android_ble._PROC_NAMES_MD.get((1, proc))
                or f"0x{proc:02x}")
        ann = _android_ble._annotate_req(ctx, proc, args) if args is not None else ""
        frame_type = "SINGLE" if h == 0x11 else "FIRST"
        return f"{frame_type}  {name}" + (f"  {ann}" if ann else "")
    return f"WRITE  byte0=0x{h:02x}"


def _decode_notif_payload(v: bytes) -> str:
    """Decode a Geberit NOTIF payload (handle 0x000F / A5)."""
    if not v:
        return "(empty)"
    h = v[0]
    if h in (0x60, 0x70) or (h & 0x80):
        return f"CTRL  byte0=0x{h:02x}"
    parsed = _android_ble._parse_resp(v)
    if parsed is None:
        return f"NOTIF byte0=0x{h:02x}"
    kind = parsed[0]
    if kind == 'CONTROL':
        return f"CTRL  byte0=0x{h:02x}"
    if kind == 'SINGLE':
        _, ctx, proc, status, result = parsed
        name = (_android_ble._PROC_NAMES_MD.get((ctx, proc))
                or _android_ble._PROC_NAMES_MD.get((0, proc))
                or _android_ble._PROC_NAMES_MD.get((1, proc))
                or f"0x{proc:02x}")
        res_str = result.hex() if result else "(none)"
        stat = f"  status=0x{status:02x}" if status else ""
        return f"RESP  {name}  result={res_str}{stat}"
    if kind == 'MULTI':
        _, cons_needed, first_v = parsed
        ctx  = first_v[9]  if len(first_v) > 9  else 0
        proc = first_v[10] if len(first_v) > 10 else 0
        name = (_android_ble._PROC_NAMES_MD.get((ctx, proc))
                or _android_ble._PROC_NAMES_MD.get((0, proc))
                or _android_ble._PROC_NAMES_MD.get((1, proc))
                or f"0x{proc:02x}")
        return f"FIRST[{cons_needed}]  {name}"
    if kind == 'MULTI_EXT':
        _, cons_needed, first_v = parsed
        ctx  = first_v[10] if len(first_v) > 10 else 0
        proc = first_v[11] if len(first_v) > 11 else 0
        name = (_android_ble._PROC_NAMES_MD.get((ctx, proc))
                or _android_ble._PROC_NAMES_MD.get((0, proc))
                or _android_ble._PROC_NAMES_MD.get((1, proc))
                or f"0x{proc:02x}")
        return f"FIRST_EXT[{cons_needed}]  {name}"
    return f"NOTIF byte0=0x{h:02x}"


# GATT discovery + READ_BLOB opcodes — included in the Raw ATT Traffic table
_GATT_OPCODES = {0x04, 0x05, 0x06, 0x07, 0x0c, 0x0d, 0x10, 0x11}


def _decode_gatt_frame(opcode: int, handle_raw: str,
                       raw_bytes: bytes, offset_val: int) -> tuple:
    """Decode a GATT service-discovery or READ_BLOB frame.
    Returns (direction, decoded_str).
    handle_raw may be a comma-separated list (tshark multi-value for RSP frames).
    """
    def _ph(s):
        try:
            return _parse_int(s.strip())
        except (ValueError, TypeError):
            return 0

    parts = [p for p in handle_raw.split(",") if p.strip()]
    first_h = _ph(parts[0]) if parts else 0

    if opcode == 0x04:   # FIND_INFO_REQ
        return "App→Dev", "GATT  FIND_INFO_REQ"
    if opcode == 0x05:   # FIND_INFO_RSP — handle field = descriptor handles found
        hdls = "  ".join(f"0x{_ph(p):04X}" for p in parts)
        return "Dev→App", f"GATT  FIND_INFO_RSP  descriptors={hdls}"
    if opcode == 0x06:   # FIND_BY_TYPE_VALUE_REQ — value field = UUID (2 bytes LE)
        uuid = int.from_bytes(raw_bytes[:2], "little") if len(raw_bytes) >= 2 else 0
        return "App→Dev", f"GATT  FIND_BY_TYPE_VALUE_REQ  UUID=0x{uuid:04X}"
    if opcode == 0x07:   # FIND_BY_TYPE_VALUE_RSP — handle field = found handle(s)
        hdls = "  ".join(f"0x{_ph(p):04X}" for p in parts)
        return "Dev→App", f"GATT  FIND_BY_TYPE_VALUE_RSP  found={hdls}"
    if opcode == 0x0c:   # READ_BLOB_REQ
        return "App→Dev", f"READ_BLOB_REQ  handle=0x{first_h:04X}  offset={offset_val}"
    if opcode == 0x0d:   # READ_BLOB_RSP
        hex_str = raw_bytes.hex() if raw_bytes else ""
        try:
            label = f"  ({raw_bytes.decode('ascii')!r})" if all(0x20 <= b < 0x7f for b in raw_bytes) else ""
        except Exception:
            label = ""
        return "Dev→App", f"READ_BLOB_RSP  {hex_str}{label}"
    if opcode == 0x10:   # READ_BY_GROUP_TYPE_REQ
        return "App→Dev", "GATT  READ_BY_GROUP_TYPE_REQ"
    if opcode == 0x11:   # READ_BY_GROUP_TYPE_RSP — handle field = service start handles
        hdls = "  ".join(f"0x{_ph(p):04X}" for p in parts)
        return "Dev→App", f"GATT  READ_BY_GROUP_TYPE_RSP  services={hdls}"
    return ("App→Dev" if opcode % 2 == 0 else "Dev→App"), f"GATT  opcode=0x{opcode:02x}"


def _get_geberit_traffic(tshark: str, pcapng: Path,
                          mac: str, addr_field: str,
                          epoch_base: float = 0.0,
                          tz=None) -> list:
    """
    Return every ATT WRITE and NOTIF frame in chronological order with decoded payload.
    Covers both directions: App→Dev (writes) and Dev→App (notifies + WRITE_RSP acks).
    Each dict: ts, ts_str, abs_ts, direction, opcode, handle, decoded, raw_hex.
    abs_ts is HH:MM:SS.mmm when epoch_base/tz are provided, else empty string.
    """
    _MERA_WRITE_H = 0x0003
    _MERA_A5_H    = 0x000F
    _SIDE_HANDLES = {0x0013, 0x0017, 0x001B}
    _CCCD_HANDLES = {0x0010, 0x0014, 0x0018, 0x001C}

    dfilter = ("btatt.opcode == 0x04 || btatt.opcode == 0x05"
               " || btatt.opcode == 0x06 || btatt.opcode == 0x07"
               " || btatt.opcode == 0x0c || btatt.opcode == 0x0d"
               " || btatt.opcode == 0x10 || btatt.opcode == 0x11"
               " || btatt.opcode == 0x12 || btatt.opcode == 0x52"
               " || btatt.opcode == 0x1b || btatt.opcode == 0x13")
    if mac:
        dfilter = f"({dfilter}) && {addr_field} == {mac.lower()}"

    # occurrence="a" (2026-07-18): 0x05/0x11 (FIND_INFO_RSP/READ_BY_GROUP_TYPE_RSP)
    # routinely pack multiple handles into one frame — _decode_gatt_frame already
    # has comma-split logic for exactly this (was dead code under the default
    # occurrence="f", which only ever returns the first handle). Safe here
    # specifically because this call queries btatt.handle/value/offset only, not
    # uuid16/uuid128 — those fields have a tree-depth-conflation trap for these
    # same opcodes (see _extract_gatt_handles / _run_tshark_pdml's docstring)
    # that btatt.handle itself does not have.
    rows = _run_tshark(tshark, pcapng, dfilter,
                       ["frame.time_relative", "btatt.opcode",
                        "btatt.handle", "btatt.value", "btatt.offset"],
                       occurrence="a")

    frames = []
    for row in rows:
        padded = (row + [""] * 5)[:5]
        ts_raw, op_raw, handle_raw, value_raw, offset_raw = padded
        try:
            opcode = _parse_int(op_raw)
            _ts    = float(ts_raw.strip())
        except (ValueError, TypeError):
            continue

        raw_hex = _value_hex(value_raw)
        try:
            raw_bytes = bytes.fromhex(raw_hex)
        except Exception:
            raw_bytes = b""

        if opcode in _GATT_OPCODES:
            try:
                offset_val = int(offset_raw.strip()) if offset_raw.strip() else 0
            except ValueError:
                offset_val = 0
            direction, decoded = _decode_gatt_frame(opcode, handle_raw, raw_bytes, offset_val)
            # first handle for the Handle column; may be 0 when tshark returns nothing
            parts = [p for p in handle_raw.split(",") if p.strip()]
            try:
                handle = _parse_int(parts[0]) if parts else 0
            except (ValueError, TypeError):
                handle = 0
        else:
            try:
                handle = _parse_int(handle_raw)
            except (ValueError, TypeError):
                handle = 0

            handle_label = _android_ble.GEBERIT_HANDLES.get(handle, f"0x{handle:04X}")

            if opcode in (0x12, 0x52):        # WRITE_REQ / WRITE_CMD  (App→Dev)
                direction = "App→Dev"
                if handle in _CCCD_HANDLES:
                    val   = int.from_bytes(raw_bytes[:2], "little") if len(raw_bytes) >= 2 else 0
                    state = "enable" if val & 1 else "disable"
                    decoded = f"CCCD {state} notif  {handle_label}"
                else:
                    # Try Geberit protocol decode on any write handle (real=0x0003, mock varies)
                    geberit_decoded = _decode_write_payload(raw_bytes)
                    if geberit_decoded.startswith("WRITE  byte0=") or geberit_decoded == "(empty)":
                        decoded = f"write → {handle_label}  {geberit_decoded}"
                    else:
                        decoded = geberit_decoded
            elif opcode == 0x13:              # WRITE_RSP  (Dev→App, ack)
                direction = "Dev→App"
                decoded   = "WRITE_RSP ack"
            else:                             # 0x1B NOTIF  (Dev→App)
                direction = "Dev→App"
                if handle == _MERA_A5_H or handle in _SIDE_HANDLES:
                    # Real device: A5=0x000F, side=0x0013/17/1B
                    if handle in _SIDE_HANDLES:
                        seq     = raw_bytes[0] if raw_bytes else 0
                        decoded = f"CONS  seq=0x{seq:02x}  {handle_label}"
                    else:
                        decoded = _decode_notif_payload(raw_bytes)
                else:
                    # Try Geberit protocol decode on any notif handle (mock uses different handles)
                    geberit_decoded = _decode_notif_payload(raw_bytes)
                    if geberit_decoded.startswith("NOTIF byte0=") or geberit_decoded == "(empty)":
                        decoded = f"notif on {handle_label}  {geberit_decoded}"
                    else:
                        decoded = geberit_decoded

        frames.append({
            "ts":        _ts,
            "ts_str":    _ts_display(ts_raw),
            "abs_ts":    _abs_ts(epoch_base, _ts, tz),
            "direction": direction,
            "opcode":    opcode,
            "handle":    handle,
            "decoded":   decoded,
            "raw_hex":   raw_hex,
        })

    return frames


def _format_traffic_log(frames: list, markdown: bool) -> str:
    """Render the chronological ATT traffic log as markdown or plain text.
    Uses abs_ts (HH:MM:SS.mmm) when available, falls back to relative ts_str."""
    has_abs = bool(frames and frames[0].get("abs_ts"))
    lines: list[str] = []
    if markdown:
        lines.append("## Raw ATT Traffic\n")
        if not frames:
            lines.append("_No ATT write or notify frames found._\n")
            return "\n".join(lines)
        lines.append("| Time | Dir | Handle | Decoded |")
        lines.append("|------|-----|--------|---------|")
        for f in frames:
            h_str  = f"0x{f['handle']:04X}"
            t_str  = f["abs_ts"] if has_abs else f["ts_str"]
            lines.append(f"| `{t_str}` | {f['direction']} | `{h_str}` | {f['decoded']} |")
    else:
        lines.append("Raw ATT Traffic")
        lines.append("-" * 100)
        if not frames:
            lines.append("  No ATT write or notify frames found.")
        else:
            col_t = 12 if has_abs else 12
            lines.append(f"  {'Time':<{col_t}}  {'Dir':<9}  {'Handle':<8}  Decoded")
            lines.append(f"  {'-'*col_t}  {'-'*9}  {'-'*8}  {'-'*50}")
            for f in frames:
                h_str = f"0x{f['handle']:04X}"
                t_str = f["abs_ts"] if has_abs else f["ts_str"]
                lines.append(
                    f"  {t_str:<{col_t}}  {f['direction']:<9}  {h_str:<8}  {f['decoded']}")
    lines.append("")
    return "\n".join(lines)


def _extract_mera_events(tshark: str, pcapng: Path, mac: str,
                         addr_field: str) -> tuple:
    """
    Extract ATT events from a Mera Comfort nRF52840 capture.
    Returns (events, att_frame_count) in the format expected by
    android-ble-analyze._collect_calls().
    """
    dfilter = (
        "btatt.opcode == 0x01 || btatt.opcode == 0x08 || btatt.opcode == 0x09"
        " || btatt.opcode == 0x0a || btatt.opcode == 0x0b"
        " || btatt.opcode == 0x12 || btatt.opcode == 0x52 || btatt.opcode == 0x1b"
    )
    if mac:
        dfilter = f"({dfilter}) && {addr_field} == {mac.lower()}"

    rows = _run_tshark(tshark, pcapng, dfilter,
                       ["frame.time_relative", addr_field,
                        "btatt.opcode", "btatt.handle", "btatt.value",
                        "btatt.uuid16", "btatt.error_code"])

    # Total ATT frame count for the header
    all_rows = _run_tshark(tshark, pcapng, "btatt", ["frame.number"])
    att_count = len(all_rows)

    events = []
    for row in rows:
        if len(row) < 3:
            continue
        padded = (row + [""] * 7)[:7]
        ts_raw, slave, op_raw, handle_raw, value_raw, uuid16_raw, err_code_raw = padded

        if not op_raw.strip():
            continue

        try:
            opcode = _parse_int(op_raw)
        except ValueError:
            continue

        etype = _OP_TO_EVENT.get(opcode)
        if not etype:
            continue

        ev: dict = {
            "ts":        _ts_display(ts_raw),
            "type":      etype,
            "direction": "RX" if opcode in _RX_OPCODES else "TX",
            "mac":       slave.strip().upper() or mac,
        }

        if opcode == _OP_READ_BY_TYPE_REQ:
            # btatt.handle = start handle; UUID is in btatt.uuid16 (16-bit) or btatt.value
            uuid_str = uuid16_raw.strip()
            if uuid_str:
                try:
                    ev["value"] = f"0x{int(uuid_str, 16):04X}"
                except ValueError:
                    ev["value"] = uuid_str
            elif value_raw.strip():
                ev["value"] = _value_hex(value_raw)
            try:
                handle = _parse_int(handle_raw)
                ev["att_handle"] = f"0x{handle:04X}"
                ev["end_handle"] = "0xFFFF"
            except ValueError:
                pass
        elif opcode == _OP_ERROR_RSP:
            try:
                handle = _parse_int(handle_raw)
                ev["att_handle"] = f"0x{handle:04X}"
            except ValueError:
                pass
            try:
                ev["req_opcode"] = f"0x{_parse_int(op_raw):02X}"
                ev["error_code"] = int(err_code_raw.strip(), 16) if err_code_raw.strip() else 0xFF
            except (ValueError, AttributeError):
                ev["error_code"] = 0xFF
        elif opcode == _OP_READ_BY_TYPE_RSP:
            # READ_BY_TYPE_RSP routinely packs MULTIPLE (handle, UUID) pairs into
            # one PDU (confirmed 2026-07-18: 25/26 real frames in one capture).
            # handle_raw/value_raw here only ever see the first pair (occurrence
            # is deliberately left at "f" — btatt.uuid16 for this opcode has a
            # tree-depth-conflation trap, see _run_tshark_pdml's docstring, so
            # simply switching to occurrence="a" would produce a WRONG single
            # value here, not just an incomplete one). Flag it plainly instead
            # of silently showing a partial decode as if it were the full
            # response — --gatt-map has the correct, complete decode (-T pdml).
            ev["value"] = "(multi-entry characteristic discovery — see --gatt-map for the full list)"
        elif handle_raw.strip():
            try:
                handle = _parse_int(handle_raw)
                ev["att_handle"] = f"0x{handle:04X}"
                ev["label"]      = _android_ble.GEBERIT_HANDLES.get(handle, "")
            except ValueError:
                pass
            ev["value"] = _value_hex(value_raw)
        else:
            ev["value"] = _value_hex(value_raw)

        events.append(ev)

    return events, att_count


def _analyze_mera(tshark: str, pcapng: Path, mac: str, args,
                  addr_field: str) -> None:
    events, att_count = _extract_mera_events(tshark, pcapng, mac, addr_field)

    # Opt-in pre-connection advertising section (--markdown --include-adv only —
    # see _format_adv_section's docstring for why this isn't on by default).
    adv_md = ""
    if args.markdown and getattr(args, "include_adv", False):
        adv_md = _format_adv_section(
            _get_adv_packets(tshark, pcapng, mac or DEFAULT_MAC), mac or DEFAULT_MAC)

    if not events:
        enc = _detect_ll_encryption(tshark, pcapng)
        connect_inds, directed_advs = _get_connection_events(tshark, pcapng, mac or DEFAULT_MAC)
        if enc:
            if args.markdown:
                md = adv_md + _render_ll_encryption_markdown(
                    enc, pcapng, mac or DEFAULT_MAC, connect_inds, directed_advs)
                if args.output:
                    Path(args.output).write_text(md, encoding="utf-8")
                    print(f"[+] Markdown written to {args.output}", file=sys.stderr)
                else:
                    print(md)
            else:
                if connect_inds or directed_advs:
                    print(_format_connection_events(
                        connect_inds, directed_advs, mac or DEFAULT_MAC, markdown=False))
                _report_ll_encryption(enc, mac or DEFAULT_MAC)
        else:
            has_conn = bool(connect_inds or directed_advs)
            if args.markdown and (has_conn or adv_md):
                conn_events_md = _format_connection_events(
                    connect_inds, directed_advs, mac or DEFAULT_MAC, markdown=True) if has_conn else ""
                if has_conn:
                    no_att_note = (
                        "## No ATT Traffic\n\n"
                        "Connection event(s) were found for this MAC, but no Geberit ATT\n"
                        "frames and no LL_ENC_REQ were captured — the nRF52840 sniffer likely\n"
                        "didn't lock onto this connection's data channel hops (a known sniffer\n"
                        "limitation: ADV/CONNECT_IND are always captured, but following the\n"
                        "hopping data channel requires the sniffer to \"catch\" it in time), or\n"
                        "the connection closed before any GATT activity took place.\n"
                    )
                else:
                    no_att_note = (
                        "## No ATT Traffic\n\n"
                        "No BLE connection to this MAC was captured at all — advertising only.\n"
                    )
                md = adv_md + conn_events_md + "\n" + no_att_note
                if args.output:
                    Path(args.output).write_text(md, encoding="utf-8")
                    print(f"[+] Markdown written to {args.output}", file=sys.stderr)
                else:
                    print(md)
            elif has_conn:
                print(_format_connection_events(
                    connect_inds, directed_advs, mac or DEFAULT_MAC, markdown=False))
                print(f"No Geberit ATT frames and no LL_ENC_REQ found"
                      + (f" for {mac}" if mac else "") + " — sniffer likely didn't "
                      "follow this connection's data channel.", file=sys.stderr)
            else:
                print(f"No Geberit ATT frames found"
                      + (f" for {mac}" if mac else "") + ".", file=sys.stderr)
        return

    print(f"[+] {att_count:,} ATT frames, {len(events):,} matching events",
          file=sys.stderr)

    connect_inds, directed_advs = _get_connection_events(tshark, pcapng, mac or DEFAULT_MAC)
    conn_events_plain = _format_connection_events(
        connect_inds, directed_advs, mac or DEFAULT_MAC, markdown=False)
    conn_events_md = _format_connection_events(
        connect_inds, directed_advs, mac or DEFAULT_MAC, markdown=True)

    # Absolute timestamp base — used by both ctrl section and traffic log
    epoch_base, tz, start_str = _get_capture_start(tshark, pcapng)

    # BLE control layer (LL PDUs, L2CAP signaling, ATT MTU/write-RSP)
    ll_events   = _get_ll_control_events(tshark, pcapng)
    l2cap_events = _get_l2cap_events(tshark, pcapng)
    att_meta    = _get_att_meta_events(tshark, pcapng, mac, addr_field)
    ctrl_plain  = _format_ctrl_section(ll_events, l2cap_events, att_meta, markdown=False,
                                        epoch_base=epoch_base, tz=tz)
    ctrl_md     = _format_ctrl_section(ll_events, l2cap_events, att_meta, markdown=True,
                                        epoch_base=epoch_base, tz=tz)

    # Bidirectional raw traffic log (every write + notify, decoded)
    traffic      = _get_geberit_traffic(tshark, pcapng, mac, addr_field, epoch_base, tz)
    traffic_plain = _format_traffic_log(traffic, markdown=False)
    traffic_md    = _format_traffic_log(traffic, markdown=True)

    if args.raw:
        print(conn_events_plain)
        print(ctrl_plain)
        print(traffic_plain)
        for e in events:
            print(f"  {e['ts']:<12}  {e['direction']}  {e['type']:<30}  "
                  f"handle={e.get('att_handle', '')}  {e.get('value', '')}")
        return

    calls = _android_ble._collect_calls(events)

    if args.markdown:
        _rel_re = re.compile(r'^t=(\d+\.?\d*)s$')
        def _ts_to_abs(rel: str) -> str:
            m = _rel_re.match(rel)
            if m and tz:
                return _abs_ts(epoch_base, float(m.group(1)), tz)
            return rel

        capture_header = f"**Capture start:** `{start_str}`\n\n" if start_str else ""
        md = (capture_header + adv_md + conn_events_md + "\n" + ctrl_md + "\n"
              + traffic_md + "\n"
              + _android_ble.render_markdown_android(
                  calls, pcapng, mac or DEFAULT_MAC, "nRF52840 pcapng", att_count,
                  ts_fmt=_ts_to_abs if tz else None))
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
            print(f"[+] Markdown written to {args.output}", file=sys.stderr)
        else:
            print(md)
    else:
        _print_mera_table(calls, pcapng, mac or DEFAULT_MAC, att_count,
                          conn_events_plain + ctrl_plain + traffic_plain)


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
  python tools/nrf-ble-analyze.py capture.pcapng --markdown --include-adv --output session.md
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
    ap.add_argument("--gatt-map", action="store_true",
                    help="Extract and print GATT handle→UUID map from discovery frames, then exit")
    ap.add_argument("--adv", action="store_true",
                    help="Show advertising packets (ADV_IND + SCAN_RSP) for the target MAC, then exit")
    ap.add_argument("--include-adv", action="store_true",
                    help="With --markdown: prepend a pre-connection advertising section "
                         "(same data as --adv, deduplicated) before Phase 1. Opt-in — "
                         "advertising data is often noisy (RF interference, overlapping "
                         "devices) and doesn't fit the phase-table format, so it's not "
                         "included by default.")
    args = ap.parse_args()

    if not args.pcapng.exists():
        print(f"Error: file not found: {args.pcapng}", file=sys.stderr)
        sys.exit(1)

    tshark    = _find_tshark()
    addr_field = _peripheral_addr_field(tshark, args.pcapng)

    if args.gatt_map:
        handle_map = _extract_gatt_handles(tshark, args.pcapng)
        print(f"GATT handle map ({args.pcapng.name}):")
        print(_format_gatt_map(handle_map))
        sys.exit(0)

    mac, device_type = _detect_device(tshark, args.pcapng, addr_field)
    if args.mac:
        mac = args.mac.upper()

    if args.adv:
        _print_adv_packets(
            _get_adv_packets(tshark, args.pcapng, mac or DEFAULT_MAC),
            mac or DEFAULT_MAC,
            args.pcapng.name,
        )
        sys.exit(0)

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
