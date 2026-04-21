#!/usr/bin/env python3
"""
android-ble-analyze.py — Analyze Android HCI BLE captures for Geberit AquaClean traffic.

Handles three formats automatically:
  - Android BTSNOOP_LOG_SUMMARY  (base64 + zlib, 9-byte prefix, custom 8-byte record header,
                                   no per-packet timestamps, type bytes 0x10/0x20)
  - Standard binary BTSnoop      (btsnoop\\0 magic, 16-byte file header, 24-byte record header
                                   with timestamps, standard H4 type bytes 0x01-0x04)
  - pcapng                       (.pcapng, Wireshark capture, SHB magic 0x0A0D0D0A,
                                   link type 201 = BLUETOOTH_HCI_H4_WITH_PHDR,
                                   4-byte direction phdr + standard H4 HCI)

Usage:
  python tools/android-ble-analyze.py local-assets/Android-BLE-Logs/BTSNOOP_LOG.log
  python tools/android-ble-analyze.py file.pcapng --mac AA:BB:CC:DD:EE:FF
  python tools/android-ble-analyze.py file.log --all-macs
  python tools/android-ble-analyze.py file.log --raw
"""

import argparse
import base64
import gzip
import struct
import sys
import zlib
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_MAC = "38:AB:41:2A:0D:67"   # Geberit AquaClean

BTSNOOP_MAGIC  = b"btsnoop\x00"
PCAPNG_MAGIC   = b"\x0a\x0d\x0d\x0a"   # SHB block type (little-endian)

# pcapng block types
PCAPNG_SHB = 0x0A0D0D0A
PCAPNG_IDB = 0x00000001
PCAPNG_EPB = 0x00000006

# pcapng link type for HCI captures from Wireshark/hcidump
PCAPNG_LINKTYPE_HCI_H4_WITH_PHDR = 201

# Standard H4 HCI type bytes
HCI_COMMAND = 0x01
HCI_ACL     = 0x02
HCI_EVENT   = 0x04

# Android BTSNOOP_LOG_SUMMARY type bytes (non-standard)
ANDROID_HCI_EVENT = 0x10  # controller → host (events)
ANDROID_HCI_ACL   = 0x20  # both directions (ACL data)

# HCI event codes
EVT_DISCONNECTION_COMPLETE = 0x05
EVT_LE_META               = 0x3E

# LE meta subevent codes
LE_CONN_COMPLETE          = 0x01
LE_ADV_REPORT             = 0x02
LE_CONN_UPDATE_COMPLETE   = 0x03
LE_ENHANCED_CONN_COMPLETE = 0x0A

# HCI command opcodes
OGF_LE             = 0x08
OCF_LE_CREATE_CONN = 0x000D

# ATT opcodes
ATT_NAMES = {
    0x01: "ATT_ERROR_RSP",
    0x02: "ATT_EXCHANGE_MTU_REQ",
    0x03: "ATT_EXCHANGE_MTU_RSP",
    0x08: "ATT_READ_BY_TYPE_REQ",
    0x09: "ATT_READ_BY_TYPE_RSP",
    0x0A: "ATT_READ_REQ",
    0x0B: "ATT_READ_RSP",
    0x10: "ATT_READ_BY_GROUP_TYPE_REQ",
    0x11: "ATT_READ_BY_GROUP_TYPE_RSP",
    0x12: "ATT_WRITE_REQ",
    0x13: "ATT_WRITE_RSP",
    0x1B: "ATT_HANDLE_VALUE_NOTIF",
    0x1D: "ATT_HANDLE_VALUE_IND",
    0x1E: "ATT_HANDLE_VALUE_CONF",
    0x52: "ATT_WRITE_CMD",
}

# Geberit GATT characteristic handles (confirmed from pcapng analysis 2026-04-21)
# A1/A2 are the outgoing write channels; A5-A8 are incoming notify channels.
GEBERIT_HANDLES = {
    0x0003: "WRITE_0 (A1)",
    0x0006: "WRITE_1/CONS (A2)",
    0x000F: "READ_0 (A5/notify)",
    0x0010: "CCCD-A5",
    0x0013: "READ_1 (A6/notify)",
    0x0014: "CCCD-A6",
    0x0017: "READ_2 (A7/notify)",
    0x0018: "CCCD-A7",
    0x001B: "READ_3 (A8/notify)",
    0x001C: "CCCD-A8",
}

# BTSnoop timestamp epoch: microseconds since 2000-01-01
import datetime as _dt
_BTSNOOP_EPOCH_SECS = _dt.datetime(2000, 1, 1, tzinfo=_dt.timezone.utc).timestamp()


def _ts_from_btsnoop(ts_us: int) -> str:
    try:
        dt = _dt.datetime.fromtimestamp(
            _BTSNOOP_EPOCH_SECS + ts_us / 1_000_000, tz=_dt.timezone.utc
        )
        return dt.strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return f"ts={ts_us}"


def _ts_from_pcapng(ts_raw: int, ts_resol: int) -> str:
    """
    Format a pcapng 64-bit timestamp as HH:MM:SS.mmm (local time).
    ts_resol: decimal exponent of resolution (6=μs, 9=ns; from if_tsresol option).
    """
    try:
        ts_sec = ts_raw / (10 ** ts_resol)
        dt = _dt.datetime.fromtimestamp(ts_sec)
        return dt.strftime("%H:%M:%S.%f")[:-3]
    except Exception:
        return f"ts={ts_raw}"


def _mac_from_le(b: bytes) -> str:
    return ":".join(f"{x:02X}" for x in reversed(b[:6]))


# ---------------------------------------------------------------------------
# File loading
# ---------------------------------------------------------------------------

def _decompress_android_payload(raw: bytes) -> bytes:
    """
    Android BTSNOOP_LOG_SUMMARY base64 payload has a 9-byte custom prefix
    followed by a standard zlib stream.  Scan for the zlib magic and decompress.
    """
    for i in range(min(32, len(raw) - 2)):
        if raw[i] == 0x78 and raw[i + 1] in (0x01, 0x5E, 0x9C, 0xDA):
            try:
                result = zlib.decompress(raw[i:])
                print(f"[+] zlib decompressed (prefix={i}B): {len(result):,} bytes",
                      file=sys.stderr)
                return result
            except Exception:
                continue
    # Fallback: try gzip and raw deflate
    for name, fn in [("gzip", gzip.decompress), ("raw deflate", lambda d: zlib.decompress(d, -15))]:
        try:
            result = fn(raw)
            print(f"[+] {name} decompressed: {len(result):,} bytes", file=sys.stderr)
            return result
        except Exception:
            continue
    raise ValueError(f"Cannot decompress payload (first bytes: {raw[:12].hex()})")


def load(path: Path) -> tuple[bytes, str]:
    """
    Return (binary_data, format_name) where format_name is 'btsnoop', 'android', or 'pcapng'.
    """
    raw = path.read_bytes()

    if raw[:8] == BTSNOOP_MAGIC:
        print(f"[+] Standard BTSnoop file: {len(raw):,} bytes", file=sys.stderr)
        return raw, "btsnoop"

    if raw[:4] == PCAPNG_MAGIC:
        print(f"[+] pcapng file: {len(raw):,} bytes", file=sys.stderr)
        return raw, "pcapng"

    text = raw.decode("ascii", errors="replace")
    if "BEGIN:BTSNOOP_LOG_SUMMARY" in text and "END:BTSNOOP_LOG_SUMMARY" in text:
        b64_start = text.index("\n", text.index("BEGIN:BTSNOOP_LOG_SUMMARY")) + 1
        b64_end   = text.index("--- END:BTSNOOP_LOG_SUMMARY")
        b64_body  = text[b64_start:b64_end].strip()
        decoded   = base64.b64decode(b64_body)
        print(f"[+] Android BTSNOOP_LOG_SUMMARY: base64 payload {len(decoded):,} bytes",
              file=sys.stderr)

        if decoded[:8] == BTSNOOP_MAGIC:
            return decoded, "btsnoop"

        decompressed = _decompress_android_payload(decoded)
        return decompressed, "android"

    raise ValueError(
        f"Unrecognized file format (first bytes: {raw[:8].hex()}, "
        f"text prefix: {raw[:40]!r})"
    )


# ---------------------------------------------------------------------------
# Packet iterators
# ---------------------------------------------------------------------------

def iter_standard_btsnoop(data: bytes):
    """
    Yield (ts_str, direction, hci_type, payload) for standard BTSnoop files.
    direction: 'TX' | 'RX'
    hci_type:  HCI_COMMAND / HCI_ACL / HCI_EVENT
    """
    _version, datalink = struct.unpack_from(">II", data, 8)
    has_h4 = datalink in (1001, 1002)

    pos = 16
    while pos + 24 <= len(data):
        _orig, inc_len, flags, _drops = struct.unpack_from(">IIII", data, pos)
        ts_hi, ts_lo = struct.unpack_from(">II", data, pos + 16)
        pos += 24

        if pos + inc_len > len(data):
            break
        pkt = data[pos:pos + inc_len]
        pos += inc_len

        if not pkt:
            continue

        ts_us     = (ts_hi << 32) | ts_lo
        ts_str    = _ts_from_btsnoop(ts_us)
        direction = "TX" if (flags & 1) == 0 else "RX"

        if has_h4:
            hci_type = pkt[0]
            payload  = pkt[1:]
        else:
            hci_type = HCI_COMMAND if (flags & 2 and direction == "TX") else \
                       HCI_EVENT   if (flags & 2 and direction == "RX") else HCI_ACL
            payload = pkt

        yield ts_str, direction, hci_type, payload


def iter_android_records(data: bytes):
    """
    Yield (ts_str, direction, hci_type, payload) for Android BTSNOOP_LOG_SUMMARY data.

    Record format: [inc_len:2LE] [orig_len:2LE] [flags:4LE] [payload:inc_len bytes]
    No per-packet timestamps — sequence index used instead.
    Type bytes:  0x10 = HCI Event,  0x20 = HCI ACL
    Flags:       0x00000000 = RX,   0x01000000 = TX
    """
    pos = 0
    seq = 0
    while pos + 8 <= len(data):
        inc_len  = struct.unpack_from("<H", data, pos)[0]
        orig_len = struct.unpack_from("<H", data, pos + 2)[0]
        flags    = struct.unpack_from("<I", data, pos + 4)[0]

        if inc_len == 0 or inc_len > 65535 or inc_len != orig_len:
            pos += 1
            continue

        payload   = data[pos + 8: pos + 8 + inc_len]
        pos      += 8 + inc_len
        seq      += 1

        if not payload:
            continue

        ts_str    = f"#{seq:05d}"
        direction = "TX" if flags == 0x01000000 else "RX"
        pkt_type  = payload[0]

        if pkt_type == ANDROID_HCI_EVENT:
            hci_type = HCI_EVENT
        elif pkt_type == ANDROID_HCI_ACL:
            hci_type = HCI_ACL
        else:
            hci_type = pkt_type   # unknown — pass through

        yield ts_str, direction, hci_type, payload[1:]


def iter_pcapng(data: bytes):
    """
    Yield (ts_str, direction, hci_type, payload) for pcapng files.

    Supports link type 201 (BLUETOOTH_HCI_H4_WITH_PHDR):
      - 4-byte direction phdr: 0x00000000 = TX (host→controller),
                               0x00000001 = RX (controller→host)
      - 1-byte H4 type: 0x01=CMD, 0x02=ACL, 0x04=EVENT
      - remaining bytes: HCI payload

    Timestamps are wall-clock, formatted as HH:MM:SS.mmm in local time.
    Timestamp resolution is read from the IDB if_tsresol option (default: 10^-6 = μs).
    Wireshark 4.x captures typically use nanosecond resolution (if_tsresol=9).
    """
    pos = 0
    link_type = None
    ts_resol  = 6    # default: 10^-6 = microseconds

    while pos + 8 <= len(data):
        block_type = struct.unpack_from("<I", data, pos)[0]
        block_len  = struct.unpack_from("<I", data, pos + 4)[0]
        if block_len < 12 or pos + block_len > len(data):
            break

        body = data[pos + 8 : pos + block_len - 4]   # strip type(4) + len(4) + trailing len(4)
        pos += block_len

        if block_type == PCAPNG_SHB:
            pass   # byte-order magic + version — no useful fields needed here

        elif block_type == PCAPNG_IDB:
            link_type = struct.unpack_from("<H", body, 0)[0]
            if link_type != PCAPNG_LINKTYPE_HCI_H4_WITH_PHDR:
                raise ValueError(
                    f"pcapng link type {link_type} not supported "
                    f"(expected {PCAPNG_LINKTYPE_HCI_H4_WITH_PHDR} = BLUETOOTH_HCI_H4_WITH_PHDR). "
                    f"Re-capture with 'Bluetooth HCI H4 with linux header' dissector."
                )
            # Parse IDB options: link(2) + reserved(2) + snaplen(4) = 8 bytes before options
            opt_pos = 8
            while opt_pos + 4 <= len(body):
                opt_code = struct.unpack_from("<H", body, opt_pos)[0]
                opt_len  = struct.unpack_from("<H", body, opt_pos + 2)[0]
                if opt_code == 0:
                    break
                if opt_code == 9 and opt_len >= 1:   # if_tsresol
                    resol_byte = body[opt_pos + 4]
                    # high bit 0 = base-10 power, high bit 1 = base-2 power
                    if resol_byte & 0x80:
                        ts_resol = resol_byte & 0x7F  # base-2 — approximate as decimal
                    else:
                        ts_resol = resol_byte & 0x7F
                opt_pos += 4 + opt_len
                if opt_len % 4:
                    opt_pos += 4 - (opt_len % 4)

        elif block_type == PCAPNG_EPB:
            if link_type is None:
                continue
            if len(body) < 20:
                continue
            ts_hi   = struct.unpack_from("<I", body, 4)[0]
            ts_lo   = struct.unpack_from("<I", body, 8)[0]
            cap_len = struct.unpack_from("<I", body, 12)[0]
            pkt     = body[20 : 20 + cap_len]

            if len(pkt) < 5:
                continue

            ts_raw    = (ts_hi << 32) | ts_lo
            ts_str    = _ts_from_pcapng(ts_raw, ts_resol)
            # direction phdr: big-endian uint32 at offset 0
            # 0x00000000 = sent (host→controller = TX)
            # 0x00000001 = received (controller→host = RX)
            dir_raw   = struct.unpack_from(">I", pkt, 0)[0]
            direction = "TX" if dir_raw == 0 else "RX"
            h4type    = pkt[4]
            payload   = pkt[5:]

            yield ts_str, direction, h4type, payload


# ---------------------------------------------------------------------------
# HCI / ATT parser
# ---------------------------------------------------------------------------

class Session:
    def __init__(self, target_mac: str, show_all: bool = False):
        self.target     = target_mac.upper()
        self.show_all   = show_all
        self.conn_map: dict[int, str] = {}   # handle → mac
        self.events: list[dict]       = []
        self.adv_macs: dict[str, int] = defaultdict(int)

    def feed(self, ts: str, direction: str, hci_type: int, payload: bytes):
        if hci_type == HCI_EVENT:
            self._event(ts, direction, payload)
        elif hci_type == HCI_ACL:
            self._acl(ts, direction, payload)
        elif hci_type == HCI_COMMAND:
            self._command(ts, direction, payload)

    # ------------------------------------------------------------------
    def _event(self, ts, direction, data):
        if len(data) < 2:
            return
        code = data[0]
        body = data[2:]  # skip code + param_len

        if code == EVT_DISCONNECTION_COMPLETE and len(body) >= 4:
            handle = struct.unpack_from("<H", body, 1)[0]
            reason = body[3]
            mac    = self.conn_map.pop(handle, None)
            if mac and (mac == self.target or self.show_all):
                self._emit(ts, "DISCONNECT", mac=mac, handle=f"0x{handle:04X}",
                           reason=f"0x{reason:02X}")

        elif code == EVT_LE_META and body:
            self._le_meta(ts, direction, body)

    def _le_meta(self, ts, direction, data):
        sub  = data[0]
        body = data[1:]

        if sub == LE_ADV_REPORT:
            self._adv_report(ts, body)
        elif sub in (LE_CONN_COMPLETE, LE_ENHANCED_CONN_COMPLETE):
            self._conn_complete(ts, sub, body)
        elif sub == LE_CONN_UPDATE_COMPLETE and len(body) >= 5:
            handle = struct.unpack_from("<H", body, 1)[0]
            mac    = self.conn_map.get(handle)
            if mac and (mac == self.target or self.show_all):
                interval_ms = struct.unpack_from("<H", body, 3)[0] * 1.25
                self._emit(ts, "CONN_UPDATE", mac=mac, handle=f"0x{handle:04X}",
                           interval=f"{interval_ms:.2f}ms")

    def _adv_report(self, ts, data):
        if not data:
            return
        num = data[0]
        pos = 1
        for _ in range(num):
            if pos + 9 > len(data):
                break
            evt_type  = data[pos]
            addr_type = data[pos + 1]
            mac       = _mac_from_le(data[pos + 2:pos + 8])
            adv_len   = data[pos + 8]
            pos += 9
            adv_data  = data[pos:pos + adv_len]
            pos += adv_len
            rssi = None
            if pos < len(data):
                raw_rssi = data[pos]
                rssi     = raw_rssi - 256 if raw_rssi > 127 else raw_rssi
                pos += 1

            self.adv_macs[mac] += 1

            if mac == self.target or self.show_all:
                self._emit(ts, "ADV_REPORT", mac=mac,
                           evt_type=f"0x{evt_type:02X}",
                           addr_type=("random" if addr_type else "public"),
                           rssi=(f"{rssi} dBm" if rssi is not None else "?"),
                           adv_data=adv_data.hex() if adv_data else "")

    def _conn_complete(self, ts, sub, data):
        if len(data) < 11:
            return
        status = data[0]
        handle = struct.unpack_from("<H", data, 1)[0]
        # data[3]=role, data[4]=peer_addr_type, data[5:11]=peer_addr (6 bytes, little-endian)
        mac    = _mac_from_le(data[5:11])

        self.conn_map[handle] = mac

        if mac == self.target or self.show_all:
            if sub == LE_CONN_COMPLETE:
                interval_raw = struct.unpack_from("<H", data, 10)[0] if len(data) >= 12 else 0
            else:
                interval_raw = struct.unpack_from("<H", data, 22)[0] if len(data) >= 24 else 0
            self._emit(ts, "CONNECT", mac=mac, handle=f"0x{handle:04X}",
                       status=("OK" if status == 0 else f"FAIL 0x{status:02X}"),
                       interval=f"{interval_raw * 1.25:.2f}ms")

    def _command(self, ts, direction, data):
        if len(data) < 3:
            return
        opcode = struct.unpack_from("<H", data, 0)[0]
        ogf    = (opcode >> 10) & 0x3F
        ocf    = opcode & 0x03FF
        params = data[3:]
        if ogf == OGF_LE and ocf == OCF_LE_CREATE_CONN and len(params) >= 13:
            mac = _mac_from_le(params[7:13])
            if mac == self.target or self.show_all:
                self._emit(ts, "CREATE_CONN", mac=mac)

    def _acl(self, ts, direction, data):
        if len(data) < 4:
            return
        handle = struct.unpack_from("<H", data, 0)[0] & 0x0FFF
        mac    = self.conn_map.get(handle)
        if not mac:
            return
        if mac != self.target and not self.show_all:
            return

        total_len  = struct.unpack_from("<H", data, 2)[0]
        l2cap      = data[4:4 + total_len]
        if len(l2cap) < 4:
            return

        l2cap_len = struct.unpack_from("<H", l2cap, 0)[0]
        l2cap_cid = struct.unpack_from("<H", l2cap, 2)[0]
        att       = l2cap[4:4 + l2cap_len]

        if l2cap_cid == 0x0004:
            self._att(ts, direction, handle, mac, att)

    def _att(self, ts, direction, conn_handle, mac, data):
        if not data:
            return
        op    = data[0]
        name  = ATT_NAMES.get(op, f"ATT_OP_0x{op:02X}")
        extra: dict = {"direction": direction}

        if op in (0x12, 0x52) and len(data) >= 3:   # WRITE_REQ / WRITE_CMD
            att_h = struct.unpack_from("<H", data, 1)[0]
            extra["att_handle"] = f"0x{att_h:04X}"
            extra["label"]      = GEBERIT_HANDLES.get(att_h, "")
            extra["value"]      = data[3:].hex()
        elif op == 0x1B and len(data) >= 3:           # HANDLE_VALUE_NOTIF
            att_h = struct.unpack_from("<H", data, 1)[0]
            extra["att_handle"] = f"0x{att_h:04X}"
            extra["label"]      = GEBERIT_HANDLES.get(att_h, "")
            extra["value"]      = data[3:].hex()
        elif op in (0x0A,) and len(data) >= 3:        # READ_REQ
            att_h = struct.unpack_from("<H", data, 1)[0]
            extra["att_handle"] = f"0x{att_h:04X}"
            extra["label"]      = GEBERIT_HANDLES.get(att_h, "")
        elif op in (0x0B,) and len(data) >= 1:        # READ_RSP
            extra["value"]      = data[1:].hex()

        self._emit(ts, name, mac=mac, **extra)

    def _emit(self, ts: str, event_type: str, **kwargs):
        self.events.append({"ts": ts, "type": event_type, **kwargs})


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def print_report(session: Session, path: Path, fmt: str, pkt_count: int):
    events = session.events
    target = session.target

    print(f"\n{'='*72}")
    print(f"File   : {path.name}  [{fmt} format, {pkt_count:,} packets]")
    print(f"Target : {target}")
    print(f"{'='*72}\n")

    if not events:
        print(f"  ✗  {target} NOT FOUND in this capture.\n")
        print(f"  Possible reasons:")
        print(f"  • Toilet was off or not advertising during the capture session.")
        print(f"  • The Geberit Home App was not used during this capture.")
        print(f"  • Wrong MAC address.")
        print()
    else:
        conn_start = None
        for e in events:
            ts_str    = e["ts"]
            etype     = e["type"]
            direction = e.get("direction", "")
            arrow     = " ▶" if direction == "TX" else " ◀" if direction == "RX" else "  "
            skip      = {"ts", "type", "mac", "direction"}
            parts     = [f"{k}={v}" for k, v in e.items() if k not in skip and v != ""]
            extra_str = "  ".join(parts)

            if etype == "CONNECT":
                conn_start = ts_str
                print(f"  ┌─ {ts_str}{arrow}  {etype:<30}  {extra_str}")
            elif etype == "DISCONNECT":
                print(f"  └─ {ts_str}{arrow}  {etype:<30}  {extra_str}")
                conn_start = None
            elif etype == "ADV_REPORT":
                print(f"     {ts_str}{arrow}  {etype:<30}  {extra_str}")
            else:
                indent = "  │  " if conn_start else "     "
                print(f"{indent}{ts_str}{arrow}  {etype:<30}  {extra_str}")

        print()
        connects = [e for e in events if e["type"] == "CONNECT"]
        notifs   = [e for e in events if e["type"] == "ATT_HANDLE_VALUE_NOTIF"]
        writes   = [e for e in events if e["type"] in ("ATT_WRITE_REQ", "ATT_WRITE_CMD")]
        adv      = [e for e in events if e["type"] == "ADV_REPORT"]
        print(f"  Summary:")
        print(f"    Advertising reports : {len(adv)}")
        print(f"    Connections         : {len(connects)}")
        print(f"    ATT writes          : {len(writes)}")
        print(f"    ATT notifications   : {len(notifs)}")
        print()

    # Always show the full advertising landscape
    if session.adv_macs:
        print(f"  All BLE devices seen advertising ({len(session.adv_macs)} unique MACs):")
        for mac, cnt in sorted(session.adv_macs.items(), key=lambda x: -x[1])[:20]:
            mark = "  ← TARGET" if mac == target else ""
            print(f"    {mac}  ({cnt:4d} adv){mark}")
        if len(session.adv_macs) > 20:
            print(f"    ... and {len(session.adv_macs) - 20} more")
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Analyze Android HCI BTSnoop logs for BLE traffic with a specific device.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Examples:
  python tools/android-ble-analyze.py local-assets/Android-BLE-Logs/BTSNOOP_LOG.log
  python tools/android-ble-analyze.py file.pcapng --mac 38:AB:41:2A:0D:67
  python tools/android-ble-analyze.py file.log --all-macs
  python tools/android-ble-analyze.py file.log --raw
""",
    )
    ap.add_argument("log", type=Path, help="BTSnoop .log file")
    ap.add_argument("--mac", default=DEFAULT_MAC,
                    help=f"MAC address to filter (default: {DEFAULT_MAC})")
    ap.add_argument("--all-macs", action="store_true",
                    help="Show events for all MAC addresses")
    ap.add_argument("--raw", action="store_true",
                    help="Dump every raw HCI packet (very verbose)")
    args = ap.parse_args()

    if not args.log.exists():
        print(f"Error: file not found: {args.log}", file=sys.stderr)
        sys.exit(1)

    try:
        data, fmt = load(args.log)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if fmt == "btsnoop" and data[:8] != BTSNOOP_MAGIC:
        print("Error: expected BTSnoop magic not found after decode", file=sys.stderr)
        sys.exit(1)

    session   = Session(target_mac=args.mac, show_all=args.all_macs)
    pkt_count = 0
    if fmt == "btsnoop":
        iterator = iter_standard_btsnoop(data)
    elif fmt == "pcapng":
        iterator = iter_pcapng(data)
    else:
        iterator = iter_android_records(data)

    for ts_str, direction, hci_type, payload in iterator:
        pkt_count += 1
        if args.raw:
            tn = {HCI_COMMAND: "CMD", HCI_ACL: "ACL", HCI_EVENT: "EVT"}.get(hci_type, f"{hci_type:02X}")
            print(f"  {ts_str}  {direction}  {tn}  {payload.hex()}")
        session.feed(ts_str, direction, hci_type, payload)

    print(f"[+] Parsed {pkt_count:,} HCI packets", file=sys.stderr)
    print_report(session, args.log, fmt, pkt_count)


if __name__ == "__main__":
    main()
