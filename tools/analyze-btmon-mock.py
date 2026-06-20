#!/usr/bin/env python3
"""
analyze-btmon-mock.py — Correlate btmon btsnoop with mock-geberit-alba log
==========================================================================

Produces a unified timeline showing both the HCI/ATT level (btsnoop) and the
application-layer Ble20/HDLC decode (mock log) side by side.

Usage:
  python tools/analyze-btmon-mock.py <btsnoop_file> <mock_log>
  python tools/analyze-btmon-mock.py <btsnoop_file> <mock_log> --no-srr
  python tools/analyze-btmon-mock.py <btsnoop_file> <mock_log> --att-only

Key questions answered:
  - When did BLE connect/disconnect at HCI level, and with what reason?
  - Did the app send any frames after Phase 1 complete?
  - Does the btsnoop gap match the mock-log gap before ^C?
  - What ATT MTU was negotiated?

Btsnoop format (monitor, datalink 0x7D1):
  File header: 16 bytes — magic "btsnoop\0" + version uint32 BE + datalink uint32 BE
  Each record: 24-byte header + data
    uint32 BE: original length
    uint32 BE: included length
    uint32 BE: flags  →  index = flags>>16,  opcode = flags & 0xFFFF
    uint32 BE: cumulative drops
    int64  BE: timestamp (µs since midnight Jan 1, year 0 CE nominal Gregorian)

  Key opcodes:
    0  NEW_INDEX      — new HCI controller
    2  HCI_CMD        — HCI command (host→controller)
    3  HCI_EVT        — HCI event (controller→host)
    4  ACL_TX         — ACL data host→controller (mock sends to iOS)
    5  ACL_RX         — ACL data controller→host (iOS sends to mock)
    10 INDEX_INFO     — controller address + manufacturer
    12 SYSTEM_NOTE    — kernel/bluetoothd text note

  HCI events decoded:
    0x05  Disconnection Complete  — reason code and connection handle
    0x3E  LE Meta:
            0x01  LE Connection Complete
            0x0A  LE Enhanced Connection Complete

  ATT opcodes decoded (L2CAP CID 0x0004):
    0x02  Exchange MTU Request
    0x03  Exchange MTU Response
    0x12  Write Request       (ACL_TX)
    0x13  Write Response      (ACL_RX)
    0x52  Write Command       (ACL_TX) — WRITE_WITHOUT_RESPONSE; value = raw mock payload
    0x1B  Handle Value Notification (ACL_RX) — mock→app data
    0x1D  Handle Value Indication  (ACL_RX)
    0x01  Error Response

Time correlation:
  The first [BLE←] raw hex in the mock log must match the value bytes of the
  first ATT Write Command (0x52) in btsnoop.  The script auto-detects the
  clock offset between the two sources and applies it to btsnoop timestamps.
"""

import argparse
import datetime
import re
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# btmon uses this offset so that btsnoop timestamps are in the nominal-Gregorian
# "year 0" epoch (standard BTSnoop spec).
# Microseconds from year-0-Jan-1 to Unix epoch (1970-Jan-1).
_BTSNOOP_EPOCH_OFFSET_US = 0x00DCDDB30F2F8000

# btmon monitor opcodes — from BlueZ monitor/pcap.h (BTSNOOP_OPCODE_*)
_OP_NEW_INDEX    = 0
_OP_DEL_INDEX    = 1
_OP_HCI_CMD      = 2
_OP_HCI_EVT      = 3
_OP_ACL_TX       = 4   # host→controller: mock sends notifications/responses to iOS
_OP_ACL_RX       = 5   # controller→host: iOS sends write-cmds/reqs to mock
_OP_OPEN_INDEX   = 8
_OP_CLOSE_INDEX  = 9
_OP_INDEX_INFO   = 10
_OP_VENDOR_DIAG  = 11
_OP_SYSTEM_NOTE  = 12
_OP_USER_LOG     = 13

_OP_NAMES = {
    _OP_NEW_INDEX:   "NEW_INDEX",
    _OP_DEL_INDEX:   "DEL_INDEX",
    _OP_HCI_CMD:     "HCI_CMD",
    _OP_HCI_EVT:     "HCI_EVT",
    _OP_ACL_TX:      "ACL_TX",
    _OP_ACL_RX:      "ACL_RX",
    _OP_OPEN_INDEX:  "OPEN_INDEX",
    _OP_CLOSE_INDEX: "CLOSE_INDEX",
    _OP_INDEX_INFO:  "INDEX_INFO",
    _OP_VENDOR_DIAG: "VENDOR_DIAG",
    _OP_SYSTEM_NOTE: "SYSTEM_NOTE",
    _OP_USER_LOG:    "USER_LOG",
}

# HCI event codes
_HCI_EVT_DISCONNECT    = 0x05
_HCI_EVT_CMD_COMPLETE  = 0x0E
_HCI_EVT_LE_META       = 0x3E

# LE Meta subevent codes
_LE_CONN_COMPLETE          = 0x01
_LE_ENH_CONN_COMPLETE_V1   = 0x0A
_LE_ENH_CONN_COMPLETE_V2   = 0x0B

# HCI command opcodes (OGF|OCF packed)
_HCI_CMD_LE_SET_SCAN_ENABLE        = 0x200C
_HCI_CMD_LE_SET_EXT_SCAN_ENABLE    = 0x2042
_HCI_CMD_LE_CREATE_CONN            = 0x200D
_HCI_CMD_LE_EXT_CREATE_CONN        = 0x2043
_HCI_CMD_DISCONNECT                = 0x0406

# HCI LE advertising commands — legacy (ADV_IND) path
_HCI_CMD_LE_SET_ADV_PARAMS         = 0x2006
_HCI_CMD_LE_SET_ADV_DATA           = 0x2008
_HCI_CMD_LE_SET_SCAN_RSP_DATA      = 0x2009
_HCI_CMD_LE_SET_ADV_ENABLE         = 0x200A  # ← legacy

# HCI LE advertising commands — extended (ADV_EXT_IND / BT5) path
_HCI_CMD_LE_SET_EXT_ADV_PARAMS     = 0x2036
_HCI_CMD_LE_SET_EXT_ADV_DATA       = 0x2037
_HCI_CMD_LE_SET_EXT_SCAN_RSP_DATA  = 0x2038
_HCI_CMD_LE_SET_EXT_ADV_ENABLE     = 0x2039  # ← extended / BT5

# ATT opcodes
_ATT_MTU_REQ       = 0x02
_ATT_MTU_RESP      = 0x03
_ATT_READ_BY_TYPE_REQ  = 0x08
_ATT_READ_BY_TYPE_RESP = 0x09
_ATT_READ_REQ      = 0x0A
_ATT_READ_RESP     = 0x0B
_ATT_READ_BY_GROUP_TYPE_REQ  = 0x10
_ATT_READ_BY_GROUP_TYPE_RESP = 0x11
_ATT_WRITE_REQ     = 0x12
_ATT_WRITE_RESP    = 0x13
_ATT_NOTIF         = 0x1B
_ATT_IND           = 0x1D
_ATT_WRITE_CMD     = 0x52
_ATT_ERROR_RESP    = 0x01

_ATT_OP_NAMES = {
    _ATT_MTU_REQ:   "Exchange MTU Req",
    _ATT_MTU_RESP:  "Exchange MTU Resp",
    _ATT_READ_BY_TYPE_REQ:  "Read By Type Req",
    _ATT_READ_BY_TYPE_RESP: "Read By Type Resp",
    _ATT_READ_REQ:  "Read Req",
    _ATT_READ_RESP: "Read Resp",
    _ATT_READ_BY_GROUP_TYPE_REQ:  "Read By Group Type Req",
    _ATT_READ_BY_GROUP_TYPE_RESP: "Read By Group Type Resp",
    _ATT_WRITE_REQ: "Write Req",
    _ATT_WRITE_RESP: "Write Resp",
    _ATT_NOTIF:     "Handle Value Notif",
    _ATT_IND:       "Handle Value Ind",
    _ATT_WRITE_CMD: "Write Cmd",
    _ATT_ERROR_RESP: "Error Resp",
}

# HCI disconnect reason codes (subset)
_DISCONNECT_REASONS = {
    0x05: "Authentication Failure",
    0x08: "Connection Timeout",
    0x13: "Remote User Terminated",
    0x14: "Remote Low Resources",
    0x15: "Remote Power Off",
    0x16: "Local Host Terminated",
    0x1A: "Unsupported Remote Feature",
    0x1F: "Unspecified Error",
    0x22: "LMP Response Timeout",
    0x3B: "Connection Failed to Establish",
}

# Known 128-bit UUIDs in the Geberit Ble20 service family
_KNOWN_UUIDS_128 = {
    "559eb100-2390-11e8-b467-0ed5f89f718b": "Ble20Service",
    "559eb110-2390-11e8-b467-0ed5f89f718b": "OtaVersion/DeviceSeries (read before Phase 1)",
    "559eb001-2390-11e8-b467-0ed5f89f718b": "Ble20Write (Write Cmd target)",
    "559eb002-2390-11e8-b467-0ed5f89f718b": "Ble20Notify (Notification source)",
}

# ATT Characteristic Declaration property bits
_PROP_BITS = [
    (0x02, "Read"), (0x04, "WriteNoResp"), (0x08, "Write"),
    (0x10, "Notify"), (0x20, "Indicate"),
]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class BtsnoopEvent:
    ts_us: int           # raw btsnoop timestamp (µs, year-0 epoch)
    ts_dt: datetime.datetime  # converted to local datetime (set after offset applied)
    opcode: int
    hci_index: int
    data: bytes
    # decoded fields (populated by decode_*)
    kind: str = ""       # 'connect', 'disconnect', 'att_write', 'att_notif', 'mtu', 'note',
                         # 'ccc_enable', 'ccc_disable', etc.
    summary: str = ""    # human-readable one-liner
    payload_hex: str = ""  # hex of ATT value (for write/notif correlation)
    acl_handle: int = 0  # HCI ACL connection handle (0x0FFF mask); set for all ACL events


@dataclass
class MockEvent:
    ts_dt: datetime.datetime
    line: str
    kind: str = ""   # 'ble_rx', 'hdlc', 'proto', 'session', 'phase', 'other'
    payload_hex: str = ""  # for [BLE←] lines


@dataclass
class RefEvent:
    """One ATT event from the reference TSV (GeberitFirstconnection.att-fields.tsv)."""
    rel_s: float            # seconds relative to capture start (from TSV)
    ts_dt: datetime.datetime = field(default_factory=datetime.datetime.now)
    opcode: int = 0
    handle: int = 0
    info: str = ""
    value_hex: str = ""
    uuid128: str = ""


# ---------------------------------------------------------------------------
# btsnoop parser
# ---------------------------------------------------------------------------

def _btsnoop_ts_to_dt(ts_us: int) -> datetime.datetime:
    unix_us = ts_us - _BTSNOOP_EPOCH_OFFSET_US
    return datetime.datetime.fromtimestamp(unix_us / 1_000_000)


def parse_btsnoop(path: Path) -> list[BtsnoopEvent]:
    """Parse a btmon btsnoop file (monitor type 0x7d1) into raw records."""
    data = path.read_bytes()
    if len(data) < 16:
        sys.exit(f"btsnoop file too small: {path}")

    magic = data[:8]
    if magic != b"btsnoop\x00":
        sys.exit(f"Not a btsnoop file (bad magic): {path}")

    version, datalink = struct.unpack_from(">II", data, 8)
    if version != 1:
        sys.exit(f"Unsupported btsnoop version {version} (expected 1)")
    if datalink != 0x07D1:
        print(f"[warn] btsnoop datalink type 0x{datalink:04X} — expected 0x07D1 (btmon monitor); "
              "some decoding may be incorrect", file=sys.stderr)

    records = []
    offset = 16
    while offset + 24 <= len(data):
        orig_len, incl_len, flags, _drops = struct.unpack_from(">IIII", data, offset)
        ts_us = struct.unpack_from(">q", data, offset + 16)[0]
        payload = data[offset + 24 : offset + 24 + incl_len]
        offset += 24 + incl_len

        opcode = flags & 0xFFFF
        hci_index = (flags >> 16) & 0xFFFF

        ev = BtsnoopEvent(
            ts_us=ts_us,
            ts_dt=_btsnoop_ts_to_dt(ts_us),
            opcode=opcode,
            hci_index=hci_index,
            data=payload,
        )
        records.append(ev)

    return records


# ---------------------------------------------------------------------------
# btsnoop event decoder
# ---------------------------------------------------------------------------

def _mac_bytes_le(b: bytes, start: int) -> str:
    return ":".join(f"{b[start + 5 - i]:02x}" for i in range(6))


def _decode_ad_elements(data: bytes) -> str:
    """Decode BLE AD structure into a human-readable summary string."""
    parts = []
    idx = 0
    while idx < len(data):
        length = data[idx]
        if length == 0:
            idx += 1
            continue
        if idx + 1 + length > len(data):
            parts.append(f"TRUNCATED(need {length} got {len(data)-idx-1})")
            break
        ad_type = data[idx + 1]
        ad_data = data[idx + 2 : idx + 1 + length]
        if ad_type == 0x01:
            flags = ad_data[0] if ad_data else 0
            fnames = []
            if flags & 0x01: fnames.append("LE_Lim_Disc")
            if flags & 0x02: fnames.append("LE_Gen_Disc")
            if flags & 0x04: fnames.append("No_BR/EDR")
            parts.append(f"Flags=0x{flags:02X}({'+'.join(fnames) or 'none'})")
        elif ad_type in (0x02, 0x03):
            uuids = [f"0x{struct.unpack_from('<H', ad_data, i)[0]:04X}"
                     for i in range(0, len(ad_data) - 1, 2)]
            label = "UUID16" if ad_type == 0x03 else "UUID16_inc"
            parts.append(f"{label}={','.join(uuids)}")
        elif ad_type in (0x06, 0x07):
            uuids = []
            for i in range(0, len(ad_data) - 15, 16):
                b = bytes(reversed(ad_data[i:i+16]))
                uuids.append(f"{b[0:4].hex()}-{b[4:6].hex()}-{b[6:8].hex()}-"
                              f"{b[8:10].hex()}-{b[10:16].hex()}")
            label = "UUID128" if ad_type == 0x07 else "UUID128_inc"
            parts.append(f"{label}={','.join(uuids)}")
        elif ad_type == 0x08:
            parts.append(f"ShortName='{ad_data.decode('utf-8', errors='replace')}'")
        elif ad_type == 0x09:
            parts.append(f"Name='{ad_data.decode('utf-8', errors='replace')}'")
        elif ad_type == 0x0A:
            power = struct.unpack_from('b', ad_data)[0] if ad_data else 0
            parts.append(f"TxPower={power}dBm")
        elif ad_type == 0xFF:
            if len(ad_data) >= 2:
                company = struct.unpack_from('<H', ad_data, 0)[0]
                parts.append(f"MfrData=co=0x{company:04X},data={ad_data[2:].hex()}")
            else:
                parts.append(f"MfrData={ad_data.hex()}")
        else:
            parts.append(f"0x{ad_type:02X}={ad_data.hex()}")
        idx += 1 + length
    return "  ".join(parts) if parts else "(empty)"


_ADV_OPS = {0x00: "Intermediate", 0x01: "First", 0x02: "Last",
            0x03: "Complete", 0x04: "Unchanged"}


def _decode_hci_evt(ev: BtsnoopEvent):
    d = ev.data
    if len(d) < 2:
        return
    evt_code = d[0]
    # param_len = d[1]

    if evt_code == _HCI_EVT_DISCONNECT and len(d) >= 6:
        # status(1), handle(2 LE), reason(1)
        status = d[2]
        handle = struct.unpack_from("<H", d, 3)[0]
        reason = d[5]
        reason_str = _DISCONNECT_REASONS.get(reason, f"0x{reason:02X}")
        ev.kind = "disconnect"
        ev.summary = (f"HCI_EVT Disconnection Complete  "
                      f"handle=0x{handle:04X}  status={status}  "
                      f"reason=0x{reason:02X} ({reason_str})")

    elif evt_code == _HCI_EVT_LE_META and len(d) >= 3:
        subevent = d[2]

        if subevent in (_LE_CONN_COMPLETE, _LE_ENH_CONN_COMPLETE_V1) and len(d) >= 20:
            # LE Connection Complete:
            #   status(1) handle(2LE) role(1) peer_addr_type(1) peer_addr(6) ...
            status = d[3]
            handle = struct.unpack_from("<H", d, 4)[0]
            role = d[6]
            peer_type = d[7]
            peer_mac = _mac_bytes_le(d, 8)
            interval = struct.unpack_from("<H", d, 14)[0] * 1.25
            ev.kind = "connect"
            ev.summary = (f"LE_CONNECTION_COMPLETE  status={status}  "
                          f"handle=0x{handle:04X}  role={'central' if role==0 else 'peripheral'}  "
                          f"peer={'random' if peer_type else 'public'} {peer_mac}  "
                          f"interval={interval:.2f}ms")

        elif subevent == _LE_ENH_CONN_COMPLETE_V2 and len(d) >= 30:
            status = d[3]
            handle = struct.unpack_from("<H", d, 4)[0]
            role = d[6]
            peer_type = d[7]
            peer_mac = _mac_bytes_le(d, 8)
            ev.kind = "connect"
            ev.summary = (f"LE_ENHANCED_CONNECTION_COMPLETE_V2  status={status}  "
                          f"handle=0x{handle:04X}  role={'central' if role==0 else 'peripheral'}  "
                          f"peer={'random' if peer_type else 'public'} {peer_mac}")


def _decode_acl(ev: BtsnoopEvent, direction: str):
    """Decode ACL data; direction = 'TX' (host→ctrl) or 'RX' (ctrl→host)."""
    d = ev.data
    if len(d) < 9:
        return
    # ACL header: handle[11:0] + pb_flag[13:12] + bc_flag[15:14] in 2 bytes LE, then length 2 LE
    acl_handle = struct.unpack_from("<H", d, 0)[0] & 0x0FFF
    ev.acl_handle = acl_handle          # always store for Phase 3 connection-reuse analysis
    acl_len    = struct.unpack_from("<H", d, 2)[0]
    if len(d) < 4 + acl_len:
        return

    # L2CAP header: length(2 LE) + CID(2 LE)
    l2cap_len = struct.unpack_from("<H", d, 4)[0]
    l2cap_cid = struct.unpack_from("<H", d, 6)[0]

    if l2cap_cid != 0x0004:  # not ATT channel
        return
    if l2cap_len < 1:
        return
    # Use available bytes — L2CAP PDU may span multiple HCI ACL fragments
    att = d[8 : 8 + l2cap_len]
    att_op = att[0]

    if att_op == _ATT_MTU_REQ and len(att) >= 3:
        mtu = struct.unpack_from("<H", att, 1)[0]
        ev.kind = "mtu"
        ev.summary = f"ATT Exchange MTU Req  client_mtu={mtu}  (ACL_{direction} handle=0x{acl_handle:04X})"

    elif att_op == _ATT_MTU_RESP and len(att) >= 3:
        mtu = struct.unpack_from("<H", att, 1)[0]
        ev.kind = "mtu"
        ev.summary = f"ATT Exchange MTU Resp  server_mtu={mtu}  (ACL_{direction} handle=0x{acl_handle:04X})"

    elif att_op == _ATT_WRITE_CMD and len(att) >= 3:
        att_handle = struct.unpack_from("<H", att, 1)[0]
        value = att[3:]
        hex_val = value.hex()
        ev.kind = "att_write"
        ev.payload_hex = hex_val
        ev.summary = (f"ATT Write Cmd  acl=0x{acl_handle:04X}  "
                      f"att_handle=0x{att_handle:04X}  "
                      f"len={len(value)}  value: {hex_val}")

    elif att_op == _ATT_WRITE_REQ and len(att) >= 3:
        att_handle = struct.unpack_from("<H", att, 1)[0]
        value = att[3:]
        hex_val = value.hex()
        # CCC writes: 2-byte value 0x0001 = enable notifications, 0x0000 = disable
        if len(value) == 2:
            ccc_val = struct.unpack_from("<H", value, 0)[0]
            if ccc_val == 0x0001:
                ev.kind = "ccc_enable"
                ev.payload_hex = hex_val
                ev.summary = (f"ATT Write Req [CCC ENABLE]  acl=0x{acl_handle:04X}  "
                              f"att_handle=0x{att_handle:04X}  value=0100")
                return
            elif ccc_val == 0x0000:
                ev.kind = "ccc_disable"
                ev.payload_hex = hex_val
                ev.summary = (f"ATT Write Req [CCC DISABLE ← Phase 2 disconnect trigger]  "
                              f"acl=0x{acl_handle:04X}  att_handle=0x{att_handle:04X}  value=0000")
                return
        ev.kind = "att_write_req"
        ev.payload_hex = hex_val
        ev.summary = (f"ATT Write Req  acl=0x{acl_handle:04X}  "
                      f"att_handle=0x{att_handle:04X}  "
                      f"len={len(value)}  value: {hex_val}")

    elif att_op == _ATT_NOTIF and len(att) >= 3:
        att_handle = struct.unpack_from("<H", att, 1)[0]
        value = att[3:]
        hex_val = value.hex()
        ev.kind = "att_notif"
        ev.payload_hex = hex_val
        ev.summary = (f"ATT Handle Value Notif  acl=0x{acl_handle:04X}  "
                      f"att_handle=0x{att_handle:04X}  "
                      f"len={len(value)}  value: {hex_val}")

    elif att_op == _ATT_ERROR_RESP and len(att) >= 5:
        req_op   = att[1]
        err_hdl  = struct.unpack_from("<H", att, 2)[0]
        err_code = att[4]
        ev.kind = "att_error"
        ev.summary = (f"ATT Error Resp  for_opcode=0x{req_op:02X}  "
                      f"handle=0x{err_hdl:04X}  error=0x{err_code:02X}  "
                      f"(acl=0x{acl_handle:04X})")

    elif att_op == _ATT_IND and len(att) >= 3:
        att_handle = struct.unpack_from("<H", att, 1)[0]
        value = att[3:]
        hex_val = value.hex()
        ev.kind = "att_ind"
        ev.payload_hex = hex_val
        ev.summary = (f"ATT Handle Value Ind  acl=0x{acl_handle:04X}  "
                      f"att_handle=0x{att_handle:04X}  "
                      f"len={len(value)}  value: {hex_val}")

    elif att_op == _ATT_READ_REQ and len(att) >= 3:
        att_handle = struct.unpack_from("<H", att, 1)[0]
        ev.kind = "att_read_req"
        ev.payload_hex = f"{att_handle:04x}"
        ev.summary = (f"ATT Read Req  acl=0x{acl_handle:04X}  "
                      f"att_handle=0x{att_handle:04X}")

    elif att_op == _ATT_READ_RESP and len(att) >= 1:
        value = att[1:]
        hex_val = value.hex()
        ev.kind = "att_read_resp"
        ev.payload_hex = hex_val
        ev.summary = (f"ATT Read Resp  acl=0x{acl_handle:04X}  "
                      f"len={len(value)}  value: {hex_val}")

    elif att_op == _ATT_READ_BY_TYPE_REQ and len(att) >= 5:
        start_h = struct.unpack_from("<H", att, 1)[0]
        end_h   = struct.unpack_from("<H", att, 3)[0]
        uuid_b  = att[5:]
        uuid_hex = uuid_b.hex()
        ev.kind = "att_read_by_type_req"
        ev.summary = (f"ATT Read By Type Req  acl=0x{acl_handle:04X}  "
                      f"start=0x{start_h:04X}  end=0x{end_h:04X}  uuid={uuid_hex}")

    elif att_op == _ATT_READ_BY_TYPE_RESP and len(att) >= 2:
        item_len = att[1]
        pairs = []
        off = 2
        while off + item_len <= len(att):
            h = struct.unpack_from("<H", att, off)[0]
            v = att[off+2 : off+item_len].hex()
            pairs.append(f"0x{h:04X}={v}")
            off += item_len
        ev.kind = "att_read_by_type_resp"
        ev.summary = (f"ATT Read By Type Resp  acl=0x{acl_handle:04X}  "
                      f"item_len={item_len}  [{', '.join(pairs)}]")

    elif att_op == _ATT_READ_BY_GROUP_TYPE_REQ and len(att) >= 5:
        start_h = struct.unpack_from("<H", att, 1)[0]
        end_h   = struct.unpack_from("<H", att, 3)[0]
        uuid_b  = att[5:]
        uuid_hex = uuid_b.hex()
        ev.kind = "att_read_by_group_type_req"
        ev.summary = (f"ATT Read By Group Type Req  acl=0x{acl_handle:04X}  "
                      f"start=0x{start_h:04X}  end=0x{end_h:04X}  uuid={uuid_hex}")

    elif att_op == _ATT_READ_BY_GROUP_TYPE_RESP and len(att) >= 2:
        item_len = att[1]
        groups = []
        off = 2
        while off + item_len <= len(att):
            sh = struct.unpack_from("<H", att, off)[0]
            eh = struct.unpack_from("<H", att, off+2)[0]
            v  = att[off+4 : off+item_len].hex()
            groups.append(f"0x{sh:04X}-0x{eh:04X}={v}")
            off += item_len
        ev.kind = "att_read_by_group_type_resp"
        ev.summary = (f"ATT Read By Group Type Resp  acl=0x{acl_handle:04X}  "
                      f"[{', '.join(groups)}]")


def decode_btsnoop_events(records: list[BtsnoopEvent]):
    """Decode each record in-place; skip uninteresting ones."""
    for ev in records:
        if ev.opcode == _OP_SYSTEM_NOTE:
            note = ev.data.rstrip(b"\x00").decode("utf-8", errors="replace")
            ev.kind = "note"
            ev.summary = f"System: {note}"

        elif ev.opcode == _OP_NEW_INDEX and len(ev.data) >= 8:
            # type(1) bus(1) addr(6) name(8)
            mac = _mac_bytes_le(ev.data, 2)
            name = ev.data[8:16].rstrip(b"\x00").decode("ascii", errors="replace")
            ev.kind = "note"
            ev.summary = f"NEW_INDEX  addr={mac}  name={name}"

        elif ev.opcode == _OP_INDEX_INFO and len(ev.data) >= 8:
            mac = _mac_bytes_le(ev.data, 0)
            mfr = struct.unpack_from("<H", ev.data, 6)[0]
            ev.kind = "note"
            ev.summary = f"INDEX_INFO  addr={mac}  manufacturer=0x{mfr:04X}"

        elif ev.opcode == _OP_HCI_EVT:
            _decode_hci_evt(ev)

        elif ev.opcode == _OP_ACL_TX:
            _decode_acl(ev, "TX")

        elif ev.opcode == _OP_ACL_RX:
            _decode_acl(ev, "RX")

        elif ev.opcode == _OP_HCI_CMD and len(ev.data) >= 3:
            opcode = struct.unpack_from("<H", ev.data, 0)[0]
            if opcode == _HCI_CMD_DISCONNECT:
                handle = struct.unpack_from("<H", ev.data, 3)[0] & 0x0FFF
                reason = ev.data[5] if len(ev.data) >= 6 else 0
                ev.kind = "disconnect_cmd"
                ev.summary = (f"HCI_CMD Disconnect  handle=0x{handle:04X}  "
                              f"reason=0x{reason:02X} "
                              f"({_DISCONNECT_REASONS.get(reason, '?')})")
            elif opcode == _HCI_CMD_LE_CREATE_CONN:
                ev.kind = "connect_cmd"
                ev.summary = "HCI_CMD LE_Create_Connection"
            elif opcode == _HCI_CMD_LE_EXT_CREATE_CONN:
                ev.kind = "connect_cmd"
                ev.summary = "HCI_CMD LE_Extended_Create_Connection"

            # ── Legacy advertising commands ──────────────────────────────
            elif opcode == _HCI_CMD_LE_SET_ADV_ENABLE and len(ev.data) >= 4:
                enable = ev.data[3]
                ev.kind = "adv_enable"
                ev.summary = (f"HCI_CMD LE_Set_Advertise_Enable  enable={enable}  "
                              f"← LEGACY ADV_IND (iOS can see this)")

            elif opcode == _HCI_CMD_LE_SET_ADV_DATA and len(ev.data) >= 4:
                adv_len = ev.data[3]
                adv_bytes = ev.data[4:4 + adv_len]
                ev.kind = "adv_data"
                ev.summary = (f"HCI_CMD LE_Set_Advertising_Data  "
                              f"len={adv_len}  data={adv_bytes.hex()}")

            elif opcode == _HCI_CMD_LE_SET_ADV_PARAMS and len(ev.data) >= 10:
                min_iv = struct.unpack_from("<H", ev.data, 3)[0]
                max_iv = struct.unpack_from("<H", ev.data, 5)[0]
                adv_type = ev.data[7]
                _ADV_TYPES = {0: "ADV_IND", 1: "ADV_DIRECT_IND", 2: "ADV_SCAN_IND",
                              3: "ADV_NONCONN_IND", 4: "ADV_DIRECT_IND_LOW"}
                ev.kind = "adv_params"
                ev.summary = (f"HCI_CMD LE_Set_Advertising_Parameters  "
                              f"type={_ADV_TYPES.get(adv_type, hex(adv_type))}  "
                              f"min={min_iv * 0.625:.1f}ms  max={max_iv * 0.625:.1f}ms")

            # ── Extended (BT5) advertising commands ──────────────────────
            elif opcode == _HCI_CMD_LE_SET_EXT_ADV_ENABLE and len(ev.data) >= 4:
                enable = ev.data[3]
                num_sets = ev.data[4] if len(ev.data) >= 5 else 0
                ev.kind = "adv_enable_ext"
                ev.summary = (f"HCI_CMD LE_Set_Extended_Advertising_Enable  enable={enable}  "
                              f"num_sets={num_sets}  "
                              f"← EXTENDED ADV_EXT_IND (iOS CANNOT see this in scan-all mode!)")

            elif opcode == _HCI_CMD_LE_SET_EXT_ADV_DATA and len(ev.data) >= 7:
                # layout: opcode(2)+param_len(1)+handle(1)+operation(1)+fragment_pref(1)+adv_data_len(1)+adv_data(N)
                adv_len = ev.data[6]
                adv_bytes = ev.data[7:7 + adv_len]
                ev.kind = "adv_data_ext"
                ev.summary = (f"HCI_CMD LE_Set_Extended_Advertising_Data  "
                              f"handle={ev.data[3]}  op={_ADV_OPS.get(ev.data[4], hex(ev.data[4]))}  "
                              f"len={adv_len}  [{_decode_ad_elements(adv_bytes)}]")

            elif opcode == _HCI_CMD_LE_SET_EXT_SCAN_RSP_DATA and len(ev.data) >= 7:
                rsp_len = ev.data[6]
                rsp_bytes = ev.data[7:7 + rsp_len]
                ev.kind = "scan_rsp_data_ext"
                ev.summary = (f"HCI_CMD LE_Set_Extended_Scan_Response_Data  "
                              f"handle={ev.data[3]}  op={_ADV_OPS.get(ev.data[4], hex(ev.data[4]))}  "
                              f"len={rsp_len}  [{_decode_ad_elements(rsp_bytes)}]")

            elif opcode == _HCI_CMD_LE_SET_EXT_ADV_PARAMS and len(ev.data) >= 6:
                # struct: opcode(2B) + param_len(1B) + adv_handle(1B) + properties(2B LE) + ...
                adv_handle = ev.data[3]
                props = struct.unpack_from("<H", ev.data, 4)[0]
                _ADV_PROP = {0x0001:"connectable", 0x0002:"scannable", 0x0004:"directed",
                             0x0010:"LEGACY-PDU", 0x0020:"anonymous"}
                set_bits = [name for bit, name in _ADV_PROP.items() if props & bit]
                is_legacy = bool(props & 0x0010)
                ev.kind = "adv_params_ext" if not is_legacy else "adv_params"
                ev.summary = (
                    f"HCI_CMD LE_Set_Extended_Advertising_Parameters  "
                    f"handle={adv_handle}  properties=0x{props:04X}  bits={set_bits}  "
                    f"← {'LEGACY PDU (ADV_IND on air) ✓' if is_legacy else 'EXTENDED PDU (ADV_EXT_IND) — iOS scan-all BLIND'}"
                )

    return records


_ADV_KINDS = frozenset(("adv_enable", "adv_enable_ext", "adv_data", "adv_data_ext",
                        "adv_params", "adv_params_ext", "scan_rsp_data_ext"))


def interesting_btsnoop(ev: BtsnoopEvent, att_only: bool) -> bool:
    """Return True if this event should appear in the timeline."""
    if att_only:
        return ev.kind in ("connect", "disconnect", "disconnect_cmd",
                           "att_write", "att_write_req", "att_notif",
                           "att_ind", "att_error", "mtu",
                           "ccc_enable", "ccc_disable",
                           "att_read_req", "att_read_resp",
                           "att_read_by_type_req", "att_read_by_type_resp",
                           "att_read_by_group_type_req", "att_read_by_group_type_resp")
    return bool(ev.kind)


# ---------------------------------------------------------------------------
# GATT characteristic map
# ---------------------------------------------------------------------------

def _parse_uuid128_le(b: bytes) -> str:
    """Convert 16 little-endian ATT bytes to UUID string (xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx)."""
    r = bytes(reversed(b))
    return (f"{r[0:4].hex()}-{r[4:6].hex()}-{r[6:8].hex()}-"
            f"{r[8:10].hex()}-{r[10:16].hex()}")


def build_gatt_char_map(records: list) -> list:
    """
    Extract GATT characteristic declarations from Read By Type Resp frames.
    Returns sorted list of (decl_handle, value_handle, properties, uuid_str).
    Handles both 16-bit UUID (item_len=7) and 128-bit UUID (item_len=21).
    """
    chars = []
    for ev in records:
        if ev.opcode not in (_OP_ACL_TX, _OP_ACL_RX):
            continue
        d = ev.data
        if len(d) < 9:
            continue
        l2cap_len = struct.unpack_from("<H", d, 4)[0]
        l2cap_cid = struct.unpack_from("<H", d, 6)[0]
        if l2cap_cid != 0x0004:
            continue
        if l2cap_len < 2:
            continue
        att = d[8 : 8 + l2cap_len]  # may be truncated for fragmented L2CAP
        if att[0] != _ATT_READ_BY_TYPE_RESP:
            continue
        item_len = att[1]
        if item_len not in (7, 21):
            continue
        uuid_byte_count = item_len - 5  # 7→2, 21→16
        off = 2
        while off + item_len <= len(att):
            decl_h = struct.unpack_from("<H", att, off)[0]
            props   = att[off + 2]
            val_h   = struct.unpack_from("<H", att, off + 3)[0]
            if uuid_byte_count == 16:
                uuid_str = _parse_uuid128_le(att[off + 5 : off + 21])
            else:
                uid16 = struct.unpack_from("<H", att, off + 5)[0]
                uuid_str = f"0x{uid16:04X}"
            chars.append((decl_h, val_h, props, uuid_str))
            off += item_len
    return sorted(chars, key=lambda x: x[0])


def print_gatt_map(chars: list):
    print()
    print("=" * 90)
    print("GATT CHARACTERISTIC MAP (from ATT characteristic discovery in this capture)")
    print("=" * 90)
    print(f"  {'DeclH':6}  {'ValH':6}  {'Props':22}  {'UUID':<40}  Known name")
    print("-" * 90)
    for decl_h, val_h, props, uuid_str in chars:
        prop_names = "|".join(n for bit, n in _PROP_BITS if props & bit) or "0x00"
        name = _KNOWN_UUIDS_128.get(uuid_str, "")
        print(f"  0x{decl_h:04X}  0x{val_h:04X}  0x{props:02X} ({prop_names:<15})  {uuid_str:<40}  {name}")


# ---------------------------------------------------------------------------
# Reference TSV loader (GeberitFirstconnection.att-fields.tsv)
# ---------------------------------------------------------------------------

def load_reference_tsv(path) -> list:
    """
    Load an .att-fields.tsv produced by decode-pcapng.sh and return
    list of RefEvent.  Only retains ATT opcodes relevant to cross-ref.
    """
    _INTERESTING_OPS = {
        _ATT_MTU_REQ, _ATT_MTU_RESP,
        _ATT_READ_REQ, _ATT_READ_RESP,
        _ATT_READ_BY_GROUP_TYPE_REQ, _ATT_READ_BY_GROUP_TYPE_RESP,
        _ATT_READ_BY_TYPE_REQ, _ATT_READ_BY_TYPE_RESP,
        _ATT_WRITE_CMD, _ATT_NOTIF, _ATT_WRITE_REQ, _ATT_WRITE_RESP,
        _ATT_ERROR_RESP,
    }
    events = []
    with open(path, encoding="utf-8") as f:
        header = None
        sep = '\t'
        for raw in f:
            line = raw.rstrip('\n')
            if header is None:
                sep = '|' if '|' in line else '\t'
                header = [c.lower() for c in line.split(sep)]
                continue
            parts = line.split(sep)
            row = dict(zip(header, parts + [''] * max(0, len(header) - len(parts))))
            try:
                rel_s = float(row.get('frame.time_relative', '0') or '0')
            except ValueError:
                continue
            opcode_raw = (row.get('btatt.opcode') or '').strip()
            try:
                opcode = int(opcode_raw, 16) if opcode_raw.startswith('0x') else \
                         int(opcode_raw, 0) if opcode_raw else 0
            except ValueError:
                opcode = 0
            if opcode not in _INTERESTING_OPS:
                continue
            handle_raw = (row.get('btatt.handle') or '').strip().split(',')[0]
            try:
                handle = int(handle_raw, 0) if handle_raw else 0
            except ValueError:
                handle = 0
            events.append(RefEvent(
                rel_s=rel_s,
                opcode=opcode,
                handle=handle,
                info=(row.get('_ws.col.info') or row.get('_ws.col.Info') or '').strip(),
                value_hex=(row.get('btatt.value') or '').strip(),
                uuid128=(row.get('btatt.uuid128') or '').strip(),
            ))
    return events


def _find_first_write_cmd_dt(btsnoop_events: list, offset_us: int) -> Optional[datetime.datetime]:
    """Return wall-clock time of first ATT Write Cmd (0x52) after applying offset."""
    for ev in btsnoop_events:
        if ev.kind == "att_write":
            shifted_us = int(ev.ts_dt.timestamp() * 1_000_000) + offset_us
            return datetime.datetime.fromtimestamp(shifted_us / 1_000_000)
    return None


def normalize_and_build_ref_entries(ref_events: list, ref_tsv_path: str,
                                    curr_sabm_dt: datetime.datetime) -> list:
    """
    Find the first Write Cmd (0x52) in ref_events as T=0, then shift all
    ref times so that SABM aligns with curr_sabm_dt.
    Returns list of (datetime, "RF", summary_str, colour).
    """
    # Find SABM offset in reference
    ref_sabm_s: Optional[float] = None
    for ev in ref_events:
        if ev.opcode == _ATT_WRITE_CMD:
            ref_sabm_s = ev.rel_s
            break
    if ref_sabm_s is None:
        print(f"  [warn] No Write Cmd (0x52) found in {ref_tsv_path} — cross-ref skipped",
              file=sys.stderr)
        return []

    entries = []
    for ev in ref_events:
        delta_s = ev.rel_s - ref_sabm_s
        dt = curr_sabm_dt + datetime.timedelta(seconds=delta_s)
        op_name = _ATT_OP_NAMES.get(ev.opcode, f"0x{ev.opcode:02X}")
        hdl = f"h=0x{ev.handle:04X}" if ev.handle else ""
        uuid_short = ""
        if ev.uuid128:
            uuid_short = _KNOWN_UUIDS_128.get(ev.uuid128, ev.uuid128[:18])
        val_short = ""
        if ev.value_hex and len(ev.value_hex) <= 40:
            val_short = f" val={ev.value_hex}"
        elif ev.value_hex:
            val_short = f" val={ev.value_hex[:40]}…"
        parts = [op_name]
        if hdl:
            parts.append(hdl)
        if uuid_short:
            parts.append(uuid_short)
        if val_short:
            parts.append(val_short)
        summary = "  ".join(parts)
        entries.append((dt, "RF", f"[kstr] {summary}", ""))
    return entries


# ---------------------------------------------------------------------------
# Mock log parser
# ---------------------------------------------------------------------------

# Timestamp prefix — two formats:
#   Alba mock:  "HH:MM:SS.mmm rest"      (no brackets, has ms)
#   Mera mock:  "[HH:MM:SS] rest"         (brackets, no ms)
_TS_RE = re.compile(r"^\[?(\d{2}:\d{2}:\d{2})(?:\.\d{3})?\]?\s+(.*)")
# [BLE←] N B  <hex>
_BLE_RX_RE = re.compile(r"\[BLE←\]\s+(\d+)\s+B\s+([0-9a-fA-F]+)")


def _parse_mock_ts(ts_str: str, date: datetime.date) -> datetime.datetime:
    h, m, s_part = ts_str.split(":")
    if "." in s_part:
        s, ms_str = s_part.split(".")
        us = int(ms_str) * 1000
    else:
        s, us = s_part, 0
    return datetime.datetime(date.year, date.month, date.day,
                             int(h), int(m), int(s), us)


def _mock_event_kind(line: str) -> str:
    if "[BLE←]" in line:
        return "ble_rx"
    if "[HDLC←]" in line:
        return "hdlc"
    if "[MockServer]" in line or "[MockBle20]" in line:
        return "proto"
    if "SESSION" in line or "waiting for client" in line:
        return "session"
    if "Phase 1" in line or "Phase 2" in line or "HANDSHAKE" in line:
        return "phase"
    if "Shutting down" in line or "Cleanup complete" in line:
        return "shutdown"
    return "other"


def parse_mock_log(path: Path, date: Optional[datetime.date] = None) -> list[MockEvent]:
    """Parse mock server log; infer date from btsnoop filename if not given."""
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    # use today if no date hint
    ref_date = date or datetime.date.today()

    events = []
    for line in lines:
        m = _TS_RE.match(line)
        if not m:
            continue
        ts_str, rest = m.group(1), m.group(2)
        ts_dt = _parse_mock_ts(ts_str, ref_date)
        kind = _mock_event_kind(rest)
        ev = MockEvent(ts_dt=ts_dt, line=rest, kind=kind)

        # extract raw hex for [BLE←] lines
        bm = _BLE_RX_RE.search(rest)
        if bm:
            ev.payload_hex = bm.group(2).lower()

        events.append(ev)

    return events


# ---------------------------------------------------------------------------
# Time-offset correlation
# ---------------------------------------------------------------------------

def find_time_offset_us(btsnoop_events: list[BtsnoopEvent],
                        mock_events: list[MockEvent]) -> Optional[int]:
    """
    Auto-detect the clock offset between btsnoop and mock log.

    Strategy: for each ATT Write Command in btsnoop, find all mock [BLE←] events
    with the same payload, compute mock_ts - btsnoop_ts.  Pick the candidate with
    the smallest absolute delta (i.e. both sources were on the same machine and
    should agree to within a few seconds; a 34-minute mismatch means a different
    session was matched).

    Returns offset in microseconds (add to btsnoop unix_us to get mock wall time).
    """
    candidates = []  # (abs_delta_us, offset_us)

    # Index mock events by payload for fast lookup
    mock_by_payload: dict[str, list[MockEvent]] = {}
    for mev in mock_events:
        if mev.kind == "ble_rx" and mev.payload_hex:
            mock_by_payload.setdefault(mev.payload_hex, []).append(mev)

    for bev in btsnoop_events:
        if bev.kind != "att_write" or not bev.payload_hex:
            continue
        for mev in mock_by_payload.get(bev.payload_hex, []):
            mock_us = int(mev.ts_dt.timestamp() * 1_000_000)
            btsnoop_us = int(bev.ts_dt.timestamp() * 1_000_000)
            offset = mock_us - btsnoop_us
            candidates.append((abs(offset), offset))

    if not candidates:
        return None

    # Pick the candidate with the smallest absolute offset
    candidates.sort()
    return candidates[0][1]


# ---------------------------------------------------------------------------
# Timeline output
# ---------------------------------------------------------------------------

# ANSI colours (disabled if stdout is not a tty)
_USE_COLOR = sys.stdout.isatty()

def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"

_COL_BTSNOOP_CONNECT    = "1;32"   # bold green
_COL_BTSNOOP_DISCONNECT = "1;31"   # bold red
_COL_BTSNOOP_ATT_WRITE  = "36"     # cyan
_COL_BTSNOOP_ATT_NOTIF  = "35"     # magenta
_COL_BTSNOOP_MTU        = "33"     # yellow
_COL_BTSNOOP_NOTE       = "2"      # dim
_COL_BTSNOOP_CCC_ENABLE  = "1;36"  # bold cyan
_COL_BTSNOOP_CCC_DISABLE = "1;31"  # bold red (Phase 2 disconnect trigger)
_COL_ADV_LEGACY         = "1;32"   # bold green — legacy ADV_IND (good)
_COL_ADV_EXTENDED       = "1;31"   # bold red   — extended ADV_EXT_IND (iOS scan-all blind)
_COL_ADV_DATA           = "2;32"   # dim green
_COL_MOCK_BLE_RX        = "36"     # cyan
_COL_MOCK_PROTO         = "37"     # white
_COL_MOCK_PHASE         = "1;33"   # bold yellow
_COL_MOCK_SESSION       = "1"      # bold
_COL_MOCK_SHUTDOWN      = "1;31"   # bold red
_COL_GAP                = "1;35"   # bold magenta


def _format_ts(dt: datetime.datetime) -> str:
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _gap_marker(prev_dt: Optional[datetime.datetime],
                cur_dt: datetime.datetime,
                threshold_ms: int = 500) -> Optional[str]:
    if prev_dt is None:
        return None
    delta_ms = (cur_dt - prev_dt).total_seconds() * 1000
    if delta_ms >= threshold_ms:
        return _c(_COL_GAP, f"  ─── gap {delta_ms:.0f} ms ───")
    return None


def print_timeline(
    btsnoop_events: list,
    mock_events: list,
    offset_us: int,
    no_srr: bool = True,
    att_only: bool = False,
    gap_threshold_ms: int = 500,
    ref_entries: Optional[list] = None,
):
    """Print a merged, chronologically sorted timeline."""

    # Apply offset to btsnoop events
    for bev in btsnoop_events:
        if offset_us != 0:
            shifted_us = int(bev.ts_dt.timestamp() * 1_000_000) + offset_us
            bev.ts_dt = datetime.datetime.fromtimestamp(shifted_us / 1_000_000)

    # Filter mock events
    def keep_mock(ev: MockEvent) -> bool:
        if att_only:
            return ev.kind in ("ble_rx", "phase", "session", "shutdown")
        if no_srr and ev.kind == "hdlc" and "S-RR" in ev.line:
            return False
        return True

    # Filter btsnoop events
    def keep_btsnoop(ev: BtsnoopEvent) -> bool:
        return interesting_btsnoop(ev, att_only)

    # Build unified list: (datetime, source, display_str, colour)
    entries = []

    for bev in btsnoop_events:
        if not keep_btsnoop(bev):
            continue
        if bev.kind in ("connect", "connect_cmd"):
            col = _COL_BTSNOOP_CONNECT
        elif bev.kind in ("disconnect", "disconnect_cmd"):
            col = _COL_BTSNOOP_DISCONNECT
        elif bev.kind == "ccc_disable":
            col = _COL_BTSNOOP_CCC_DISABLE
        elif bev.kind == "ccc_enable":
            col = _COL_BTSNOOP_CCC_ENABLE
        elif bev.kind in ("att_write", "att_write_req"):
            col = _COL_BTSNOOP_ATT_WRITE
        elif bev.kind in ("att_notif", "att_ind"):
            col = _COL_BTSNOOP_ATT_NOTIF
        elif bev.kind == "mtu":
            col = _COL_BTSNOOP_MTU
        elif bev.kind == "adv_enable":
            col = _COL_ADV_LEGACY
        elif bev.kind == "adv_enable_ext":
            col = _COL_ADV_EXTENDED
        elif bev.kind in ("adv_data", "adv_data_ext", "adv_params", "adv_params_ext",
                          "scan_rsp_data_ext"):
            col = _COL_ADV_DATA
        else:
            col = _COL_BTSNOOP_NOTE
        entries.append((bev.ts_dt, "BT", bev.summary, col))

    for mev in mock_events:
        if not keep_mock(mev):
            continue
        if mev.kind == "ble_rx":
            col = _COL_MOCK_BLE_RX
        elif mev.kind == "phase":
            col = _COL_MOCK_PHASE
        elif mev.kind in ("session", "shutdown"):
            col = _COL_MOCK_SESSION
        elif mev.kind == "proto":
            col = _COL_MOCK_PROTO
        else:
            col = ""
        entries.append((mev.ts_dt, "MK", mev.line, col))

    if ref_entries:
        entries.extend(ref_entries)

    entries.sort(key=lambda x: x[0])

    prev_dt = None
    for dt, src, text, col in entries:
        gap = _gap_marker(prev_dt, dt, gap_threshold_ms)
        if gap:
            print(gap)
        ts = _format_ts(dt)
        line = f"[{ts}] [{src}] {text}"
        print(_c(col, line) if col else line)
        prev_dt = dt


# ---------------------------------------------------------------------------
# Summary stats
# ---------------------------------------------------------------------------

def print_summary(btsnoop_events: list[BtsnoopEvent],
                  mock_events: list[MockEvent],
                  offset_us: Optional[int]):
    print()
    print("=" * 72)
    print("SUMMARY")
    print("=" * 72)

    # ── Advertising type analysis ────────────────────────────────────────────
    print()
    print("=" * 72)
    print("ADVERTISING TYPE (determines iOS discoverability)")
    print("=" * 72)
    legacy_enables   = [e for e in btsnoop_events if e.kind == "adv_enable"]
    extended_enables = [e for e in btsnoop_events if e.kind == "adv_enable_ext"]
    adv_data_legacy  = [e for e in btsnoop_events if e.kind == "adv_data"]
    adv_data_ext     = [e for e in btsnoop_events if e.kind == "adv_data_ext"]
    scan_rsp_ext     = [e for e in btsnoop_events if e.kind == "scan_rsp_data_ext"]
    if legacy_enables:
        print(f"\n✓ LEGACY advertising (ADV_IND): {len(legacy_enables)} enable command(s)")
        print("  → iOS CAN see this when scanning without UUID filter (Mera path)")
        for e in legacy_enables:
            print(f"    {_format_ts(e.ts_dt)}  {e.summary}")
        for e in adv_data_legacy:
            print(f"    {_format_ts(e.ts_dt)}  {e.summary}")
    if extended_enables:
        print(f"\n✗ EXTENDED advertising (ADV_EXT_IND): {len(extended_enables)} enable command(s)")
        print("  → iOS CANNOT see this in scan-all mode (Mera app path uses legacy scan only)")
        for e in extended_enables:
            print(f"    {_format_ts(e.ts_dt)}  {e.summary}")
        for e in adv_data_ext:
            print(f"    {_format_ts(e.ts_dt)}  {e.summary}")
        for e in scan_rsp_ext:
            print(f"    {_format_ts(e.ts_dt)}  {e.summary}")
    if not legacy_enables and not extended_enables:
        print("\nNo advertising enable/disable commands found in capture.")
        print("  (btmon may have started after advertising was already active, or MGMT was used)")

    # Connection events
    connects = [e for e in btsnoop_events if e.kind == "connect"]
    disconnects = [e for e in btsnoop_events if e.kind == "disconnect"]

    if connects:
        print(f"\nBLE connections found in btsnoop: {len(connects)}")
        for e in connects:
            print(f"  {_format_ts(e.ts_dt)}  {e.summary}")
    else:
        print("\nNo LE Connection Complete events found in btsnoop")

    if disconnects:
        print(f"\nBLE disconnections found in btsnoop: {len(disconnects)}")
        for e in disconnects:
            print(f"  {_format_ts(e.ts_dt)}  {e.summary}")
    else:
        print("\nNo Disconnection Complete events found in btsnoop")

    # MTU
    mtus = [e for e in btsnoop_events if e.kind == "mtu"]
    if mtus:
        print(f"\nATT MTU exchanges:")
        for e in mtus:
            print(f"  {_format_ts(e.ts_dt)}  {e.summary}")

    # ATT writes / notifications
    att_writes = [e for e in btsnoop_events if e.kind in ("att_write", "att_write_req")]
    att_notifs = [e for e in btsnoop_events if e.kind in ("att_notif", "att_ind")]
    ccc_disables = [e for e in btsnoop_events if e.kind == "ccc_disable"]
    ccc_enables  = [e for e in btsnoop_events if e.kind == "ccc_enable"]
    print(f"\nATT Write Commands (app→mock):   {len(att_writes)}")
    print(f"ATT Notifications (mock→app):    {len(att_notifs)}")
    if ccc_enables:
        print(f"CCC Enable  (notifications on):  {len(ccc_enables)}")
        for e in ccc_enables:
            print(f"  {_format_ts(e.ts_dt)}  {e.summary}")
    if ccc_disables:
        print(f"CCC Disable (notifications off): {len(ccc_disables)}")
        for e in ccc_disables:
            print(f"  {_format_ts(e.ts_dt)}  {e.summary}")

    # Mock phase milestones
    phase1_done = [e for e in mock_events if "Phase 1 complete" in e.line]
    if phase1_done:
        print(f"\nPhase 1 complete: {_format_ts(phase1_done[-1].ts_dt)}")
        # Count writes after Phase 1
        p1_ts = phase1_done[-1].ts_dt
        writes_after = [e for e in btsnoop_events
                        if e.kind in ("att_write", "att_write_req") and e.ts_dt > p1_ts]
        print(f"ATT Write Cmds after Phase 1:  {len(writes_after)}")
        if writes_after:
            for e in writes_after:
                print(f"  {_format_ts(e.ts_dt)}  {e.summary}")

    # -------------------------------------------------------------------
    # Phase 2/3 transition analysis
    # -------------------------------------------------------------------
    # Goal: determine whether Phase 3 is a NEW BLE connection (new ACL handle)
    # or reuses the same BLE link as Phase 2 (same ACL handle, new SABM only).
    #
    # Trigger point: CCC Disable (0x0000) → bz.Reset() → bz.IsConnected=false.
    # If Phase 3 is a new BLE connection, a LE_CONNECTION_COMPLETE event follows.
    # If Phase 3 reuses the same link, the next ATT Write Cmd has the same ACL handle.
    print()
    print("=" * 72)
    print("PHASE 2/3 TRANSITION ANALYSIS")
    print("=" * 72)

    if not ccc_disables:
        print("No CCC Disable found — Phase 2/3 transition did not occur in this capture.")
    else:
        for i, ccc_ev in enumerate(ccc_disables):
            print(f"\nCCC Disable #{i+1}: {_format_ts(ccc_ev.ts_dt)}")
            print(f"  acl_handle=0x{ccc_ev.acl_handle:04X}  att_handle=0x"
                  f"{int(ccc_ev.summary.split('att_handle=0x')[1].split()[0], 16):04X}"
                  if "att_handle=0x" in ccc_ev.summary else "")

            # Find BLE reconnect (new LE_CONNECTION_COMPLETE) after this CCC disable
            reconnect_ev = next(
                (e for e in btsnoop_events
                 if e.kind == "connect" and e.ts_dt > ccc_ev.ts_dt),
                None
            )

            # Find first ATT Write Cmd after this CCC disable (= Phase 3 first frame)
            first_phase3_write = next(
                (e for e in btsnoop_events
                 if e.kind == "att_write" and e.ts_dt > ccc_ev.ts_dt),
                None
            )

            if reconnect_ev:
                gap_ms = (reconnect_ev.ts_dt - ccc_ev.ts_dt).total_seconds() * 1000
                print(f"  LE_CONNECTION_COMPLETE (Phase 3 new BLE conn) at "
                      f"{_format_ts(reconnect_ev.ts_dt)}  "
                      f"gap={gap_ms:.0f} ms after CCC Disable")
                if first_phase3_write:
                    write_gap_ms = (first_phase3_write.ts_dt - ccc_ev.ts_dt).total_seconds() * 1000
                    conn_reused = (first_phase3_write.acl_handle == ccc_ev.acl_handle)
                    print(f"  First Phase 3 ATT Write Cmd:  "
                          f"{_format_ts(first_phase3_write.ts_dt)}  "
                          f"gap={write_gap_ms:.0f} ms  "
                          f"acl=0x{first_phase3_write.acl_handle:04X}  "
                          f"({'SAME ACL handle — BLE link reused' if conn_reused else 'DIFFERENT ACL handle — new BLE connection'})")
            elif first_phase3_write:
                write_gap_ms = (first_phase3_write.ts_dt - ccc_ev.ts_dt).total_seconds() * 1000
                conn_reused = (first_phase3_write.acl_handle == ccc_ev.acl_handle)
                print(f"  No LE_CONNECTION_COMPLETE found after CCC Disable.")
                print(f"  First ATT Write Cmd after CCC Disable:  "
                      f"{_format_ts(first_phase3_write.ts_dt)}  "
                      f"gap={write_gap_ms:.0f} ms  "
                      f"acl=0x{first_phase3_write.acl_handle:04X}")
                if conn_reused:
                    print(f"  *** SAME ACL handle → Phase 3 reuses the existing BLE connection ***")
                    print(f"  *** No BLE disconnect/reconnect. 'bz.Reset()' must be reversed by  ***")
                    print(f"  *** CCC Re-enable (CCC Enable write) before Phase 3 SABM.          ***")
                else:
                    print(f"  *** DIFFERENT ACL handle → Phase 3 is a new BLE connection. ***")
            else:
                print(f"  No ATT Write Cmd found after CCC Disable — Phase 3 did not connect "
                      f"within the capture window.")

            # Check for CCC re-enable between CCC disable and first Phase 3 write
            if first_phase3_write:
                ccc_reenable = next(
                    (e for e in btsnoop_events
                     if e.kind == "ccc_enable"
                     and e.ts_dt > ccc_ev.ts_dt
                     and e.ts_dt < first_phase3_write.ts_dt),
                    None
                )
                if ccc_reenable:
                    reenable_gap_ms = (ccc_reenable.ts_dt - ccc_ev.ts_dt).total_seconds() * 1000
                    print(f"  CCC Re-enable at {_format_ts(ccc_reenable.ts_dt)}  "
                          f"gap={reenable_gap_ms:.0f} ms after CCC Disable")

    # Offset info
    if offset_us is not None:
        print(f"\nTime offset (mock − btsnoop): {offset_us / 1000:.1f} ms "
              f"({'btsnoop ahead' if offset_us < 0 else 'mock ahead or same timezone offset'})")
    else:
        print("\nTime offset: could not auto-detect (no matching payload found)")

    print()


# ---------------------------------------------------------------------------
# Date inference
# ---------------------------------------------------------------------------

def _infer_date(btsnoop_path: Path) -> Optional[datetime.date]:
    """Try to extract a date from the btsnoop filename, e.g. btmon_2026-06-13_09-04."""
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", btsnoop_path.name)
    if m:
        return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Correlate btmon btsnoop with mock-geberit-alba server log",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    ap.add_argument("btsnoop",  help="btmon btsnoop file (binary, datalink 0x7d1)")
    ap.add_argument("mock_log", nargs="?", default=None, help="mock-geberit-alba text log (optional)")
    ap.add_argument("--no-srr", action="store_true", default=True,
                    help="suppress [HDLC←] S-RR lines from mock log (default: on)")
    ap.add_argument("--keep-srr", action="store_true",
                    help="show S-RR acknowledgement lines")
    ap.add_argument("--att-only", action="store_true",
                    help="show only ATT and connection events (suppress non-ATT btsnoop + most mock lines)")
    ap.add_argument("--gap", type=int, default=500, metavar="MS",
                    help="gap threshold in ms to print separator (default: 500)")
    ap.add_argument("--no-color", action="store_true",
                    help="disable ANSI colour output")
    ap.add_argument("--summary-only", action="store_true",
                    help="print summary only (no timeline)")
    ap.add_argument("--offset-ms", type=float, default=None,
                    help="manually specify time offset (mock − btsnoop) in ms; "
                         "use when auto-detect fails")
    ap.add_argument("--gatt-map", action="store_true",
                    help="print GATT characteristic map (handle→UUID) derived from discovery frames")
    ap.add_argument("--cross-ref", metavar="TSV",
                    help="path to reference .att-fields.tsv (e.g. GeberitFirstconnection.att-fields.tsv); "
                         "adds normalised reference events to the timeline as [kstr] entries")
    args = ap.parse_args()

    if args.no_color:
        global _USE_COLOR
        _USE_COLOR = False

    no_srr = not args.keep_srr

    btsnoop_path = Path(args.btsnoop)
    mock_path = Path(args.mock_log) if args.mock_log else None

    if not btsnoop_path.exists():
        sys.exit(f"File not found: {btsnoop_path}")
    if mock_path and not mock_path.exists():
        sys.exit(f"File not found: {mock_path}")

    print(f"btsnoop : {btsnoop_path.name}  ({btsnoop_path.stat().st_size:,} bytes)")
    if mock_path:
        print(f"mock log: {mock_path.name}  ({mock_path.stat().st_size:,} bytes)")
    else:
        print("mock log: (none)")

    # Infer date for mock log timestamps
    date_hint = _infer_date(btsnoop_path)
    if date_hint:
        print(f"date    : {date_hint} (from btsnoop filename)")
    else:
        date_hint = datetime.date.today()
        print(f"date    : {date_hint} (today — override with --offset-ms if wrong)")

    print()

    # Parse
    print("Parsing btsnoop...", end=" ", flush=True)
    raw_records = parse_btsnoop(btsnoop_path)
    decode_btsnoop_events(raw_records)
    print(f"{len(raw_records)} records")

    if mock_path:
        print("Parsing mock log...", end=" ", flush=True)
        mock_events = parse_mock_log(mock_path, date_hint)
        print(f"{len(mock_events)} events")
    else:
        mock_events = []

    # Time offset
    if args.offset_ms is not None:
        offset_us = int(args.offset_ms * 1000)
        print(f"Time offset: {args.offset_ms:.1f} ms (manual)")
    elif mock_events:
        offset_us = find_time_offset_us(raw_records, mock_events)
        if offset_us is not None:
            print(f"Time offset: {offset_us / 1000:.1f} ms (auto-detected)")
        else:
            offset_us = 0
            print("Time offset: 0 (auto-detect failed — timestamps may not align)")
    else:
        offset_us = 0

    print()

    # Build GATT map (always, used by both --gatt-map and summary)
    gatt_chars = build_gatt_char_map(raw_records)

    # Load reference TSV for cross-ref if requested
    ref_entries = None
    if args.cross_ref:
        ref_path = Path(args.cross_ref)
        if not ref_path.exists():
            sys.exit(f"Reference TSV not found: {ref_path}")
        print(f"Loading reference TSV: {ref_path.name} ...", end=" ", flush=True)
        ref_events = load_reference_tsv(ref_path)
        print(f"{len(ref_events)} ATT events")
        curr_sabm_dt = _find_first_write_cmd_dt(raw_records, offset_us)
        if curr_sabm_dt:
            ref_entries = normalize_and_build_ref_entries(
                ref_events, args.cross_ref, curr_sabm_dt)
            print(f"Reference events normalised to SABM at {_format_ts(curr_sabm_dt)}")
        else:
            print("[warn] No Write Cmd found in btsnoop — cross-ref skipped",
                  file=sys.stderr)
    print()

    if not args.summary_only:
        print_timeline(raw_records, mock_events, offset_us,
                       no_srr=no_srr, att_only=args.att_only,
                       gap_threshold_ms=args.gap,
                       ref_entries=ref_entries)

    print_summary(raw_records, mock_events, offset_us)

    if args.gatt_map:
        print_gatt_map(gatt_chars)


if __name__ == "__main__":
    main()
