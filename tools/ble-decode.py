#!/usr/bin/env python3
"""
Geberit AquaClean BLE / iOS PacketLogger decoder
=================================================

Parses Xcode PacketLogger captures (.txt export) and decodes Geberit GATT frames.

Usage
-----
    python ble-decode.py <logfile> [options]

Options
    --mac  MAC       filter to one device (default: 38:AB:41:2A:0D:67)
    --from HH:MM:SS  include only lines at or after this time
    --to   HH:MM:SS  include only lines at or before this time
    --filter PROC    show only frames matching procedure name or hex code (e.g. 0x0E or firmware)
    --verbose        also print raw result bytes for responses
    --raw            print every raw 20-byte frame (no decoding)
    --firmware       shorthand for --filter 0x0E (GetFirmwareVersionList)
    --decode-fw      parse and pretty-print firmware version records from GetFirmwareVersionList
    --filter-status  shorthand for --filter 0x59 (GetFilterStatus)
    --decode-filter  parse and pretty-print filter status records from GetFilterStatus
    --impl           after each decoded procedure, show Python CallClass implementation hint
    --markdown       render the full session as annotated markdown (grouped by logical phase)
    --output FILE    write markdown output to FILE instead of stdout (requires --markdown)

Log line format (PacketLogger text export)
    Apr 11 12:52:36.909  ATT Send  0x0403  38:AB:41:2A:0D:67  <desc>  <full hex>

Full hex layout (bytes, 0-indexed)
    0-1   HCI header
    2-3   HCI data length (LE)
    4-5   L2CAP PDU length (LE)
    6-7   L2CAP channel (0x0004 = ATT)
    8     ATT opcode: 0x52 = Write Without Response, 0x1B = Handle Value Notification
    9-10  ATT handle (LE)
    11-30 Geberit 20-byte frame

Frame header byte (byte 11 of full hex = byte 0 of Geberit frame)
    byte[0] = (FrameType << 5) | (HasMsgTypeByte ? 0x10 : 0) | (SubFrameCountOrIndex << 1) | IsSubFrameCount

    FrameType: 0=MSG, 2=CONS(device), 3=CONTROL, 4=INFO
    SINGLE  : FrameType=0, HasMsgType=1, IsCount=1, Count=0  → header=0x11
    FIRST(N): FrameType=0, HasMsgType=1, IsCount=1, Count=N  → header=0x11+2*N (0x13/0x15/0x17)
    CONS(N) : FrameType=0 or 2, IsCount=0, Index=N          → 0x12/0x14/0x42/0x44 ...
    CONTROL : FrameType=3                                    → 0x60/0x70
    INFO    : FrameType=4                                    → 0x80+

SINGLE payload (bytes 1-19 of 20-byte frame)
    [0]  0x04/0x05  message type (04=request, 05=response)
    [1]  0xFF/0x00
    [2]  0x00
    [3]  body_len
    [4]  counter_lo
    [5]  counter_hi
    [6]  0x01/0x00  node or status
    [7]  0x01       node (response) or context (request)
    [8]  context    (response)
    [9]  procedure  (response)
    [10] result_len (response)
    [11+] result data

FIRST+CONS assembly
    body = FIRST[2][5:] + FIRST[1] + CONS1[1:] + CONS2[1:] + ...
    Response body: [status][node][context][procedure][result_len][result...]
"""

import argparse
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Protocol tables
# ---------------------------------------------------------------------------

PROCEDURES = {
    (0x00, 0x82): "GetDeviceIdentification",
    (0x00, 0x86): "GetDeviceInitialOperationDate",
    (0x01, 0x05): "UnknownProc_0x05",
    (0x01, 0x07): "UnknownProc_0x07",
    (0x01, 0x09): "SetCommand",
    (0x01, 0x0A): "GetStoredProfileSetting",
    (0x01, 0x0B): "SetStoredProfileSetting",
    (0x01, 0x0D): "GetSystemParameterList",
    (0x01, 0x0E): "GetFirmwareVersionList",
    (0x01, 0x11): "SubscribeNotifications_Pre",
    (0x01, 0x13): "SubscribeNotifications",
    (0x01, 0x45): "GetStatisticsDescale",
    (0x01, 0x51): "GetStoredCommonSetting",
    (0x01, 0x53): "GetStoredProfileSetting_C#",
    (0x01, 0x54): "SetStoredProfileSetting_C#",
    (0x01, 0x56): "SetDeviceRegistrationLevel",
    (0x01, 0x59): "GetFilterStatus",
    (0x01, 0x81): "GetSOCApplicationVersions",
}

COMMANDS = {
    0x00: "ToggleAnalShower",
    0x01: "ToggleLadyShower",
    0x02: "ToggleDryer",
    0x04: "StartCleaningDevice",
    0x05: "ExecuteNextCleaningStep",
    0x06: "PrepareDescaling",
    0x07: "ConfirmDescaling",
    0x08: "CancelDescaling",
    0x09: "PostponeDescaling",
    0x0A: "ToggleLidPosition",
    0x14: "ToggleOrientationLight",
    0x21: "StartLidPositionCalibration",
    0x22: "LidPositionOffsetSave",
    0x23: "LidPositionOffsetIncrement",
    0x24: "LidPositionOffsetDecrement",
    0x25: "TriggerFlushManually",
    0x2F: "ResetFilterCounter",
}

PROFILE_SETTINGS = {
    0: "OdourExtraction",
    1: "OscillatorState",
    2: "AnalShowerPressure",
    3: "LadyShowerPressure",
    4: "AnalShowerPosition",
    5: "LadyShowerPosition",
    6: "WaterTemperature",
    7: "WcSeatHeat",
    8: "DryerTemperature",
    9: "DryerState",
    10: "SystemFlush",
}

# ATT handles → channel names
HANDLES = {
    0x0003: "WRITE_0",
    0x0006: "WRITE_1",
    0x0009: "WRITE_2",
    0x000C: "WRITE_3",
    0x000F: "READ_0",
    0x0013: "READ_1",
    0x0017: "READ_2",
    0x001B: "READ_3",
}


# ---------------------------------------------------------------------------
# Frame decoding helpers
# ---------------------------------------------------------------------------

def _frame_type(hdr: int) -> int:
    return (hdr >> 5) & 7


def _has_msg_type(hdr: int) -> bool:
    return bool(hdr & 0x10)


def _sub_frame_field(hdr: int) -> int:
    return (hdr >> 1) & 7


def _is_sub_frame_count(hdr: int) -> bool:
    return bool(hdr & 1)


def frame_kind(hdr: int) -> str:
    ft = _frame_type(hdr)
    if ft == 3:
        return "CONTROL"
    if ft == 4:
        return "INFO"
    if ft == 2:
        idx = _sub_frame_field(hdr)
        return f"CONS_DEV[{idx}]"
    if ft == 1:
        # FrameType=1 = device-originated FIRST (e.g. header 0x30).
        # Count is NOT encoded in the header; derived from body_len in _assemble().
        return "FIRST_DEV"
    # FrameType 0 (MSG)
    if _is_sub_frame_count(hdr):
        count = _sub_frame_field(hdr)
        return "SINGLE" if count == 0 else f"FIRST[{count}]"
    idx = _sub_frame_field(hdr)
    return f"CONS[{idx}]"


def decode_single_frame(frame: bytes, direction: str) -> str:
    """Decode a SINGLE (20-byte) Geberit frame into a human-readable string."""
    hdr = frame[0]
    kind = frame_kind(hdr)

    if kind == "CONTROL":
        err = frame[1]
        limit = frame[2]
        ack_mask = int.from_bytes(frame[4:12], "little")
        acked = [i for i in range(8) if ack_mask & (1 << i)]
        return f"CONTROL err={err:#04x} limit={limit} acked={acked}"

    if kind == "INFO":
        return f"INFO {frame[1:].hex()}"

    if kind == "SINGLE":
        payload = frame[1:20]
        return _decode_payload(payload, direction)

    # FIRST or CONS — caller handles multi-frame assembly
    return f"{kind} raw={frame.hex()}"


def _decode_payload(payload: bytes, direction: str) -> str:
    """Decode the 19-byte payload of a SINGLE frame."""
    if len(payload) < 10:
        return f"short payload {payload.hex()}"

    msg_type = payload[0]
    body_len = payload[3]
    counter = payload[4] | (payload[5] << 8)

    if msg_type == 0x04:  # request
        node = payload[6]
        ctx = payload[7]
        proc = payload[8]
        arg_len = payload[9]
        args = payload[10:10 + arg_len]
        return _fmt_request(counter, ctx, proc, args)
    elif msg_type == 0x05:  # response
        status = payload[6]
        node = payload[7]
        ctx = payload[8]
        proc = payload[9]
        result_len = payload[10] if len(payload) > 10 else 0
        result = payload[11:11 + result_len]
        return _fmt_response(counter, ctx, proc, status, result)
    else:
        return f"payload msg_type={msg_type:#04x} {payload.hex()}"


def _fmt_request(counter: int, ctx: int, proc: int, args: bytes) -> str:
    name = PROCEDURES.get((ctx, proc), f"Proc({ctx:#04x},{proc:#04x})")
    extra = ""
    if (ctx, proc) == (0x01, 0x09) and len(args) >= 1:
        cmd_name = COMMANDS.get(args[0], f"cmd={args[0]:#04x}")
        extra = f" → {cmd_name}"
    elif (ctx, proc) in ((0x01, 0x0A), (0x01, 0x53)) and len(args) >= 1:
        setting = PROFILE_SETTINGS.get(args[0], f"idx={args[0]}")
        extra = f" → {setting}"
    elif (ctx, proc) == (0x01, 0x0D) and len(args) >= 1:
        indices = list(args[1:1 + args[0]]) if args[0] <= len(args) - 1 else list(args[1:])
        extra = f" → indices={indices}"
    elif args:
        extra = f" args={args.hex()}"
    return f"REQ  #{counter:5d}  {name}{extra}"


def _fmt_response(counter: int, ctx: int, proc: int, status: int, result: bytes) -> str:
    name = PROCEDURES.get((ctx, proc), f"Proc({ctx:#04x},{proc:#04x})")
    status_str = "OK" if status == 0 else f"ERR={status:#04x}"
    extra = ""
    if (ctx, proc) == (0x01, 0x81) and len(result) >= 3:
        v1, v2, build = result[0], result[1], result[2]
        extra = f" → SOC {chr(v1)}{chr(v2)}.{build}"
    elif result:
        extra = f" result={result.hex()}"
    return f"RESP #{counter:5d}  {name}  {status_str}{extra}"


FILTER_STATUS_NAMES = {
    0:  "status",
    1:  "shower_cycles",
    2:  "unknown_02",
    3:  "unknown_03",
    4:  "unknown_ts_04",
    5:  "unknown_05",
    6:  "unknown_06",
    7:  "days_until_filter_change",
    8:  "last_filter_reset (unix ts)",
    9:  "next_filter_change (unix ts)",
    10: "filter_reset_count",
}


def decode_filter_status_records(result_data: bytes) -> str:
    """Parse GetFilterStatus result bytes into readable filter maintenance values."""
    import struct
    if not result_data:
        return "  (no data)"
    count = result_data[0]
    lines = [f"  {count} filter status records:"]
    pos = 1
    while pos + 4 < len(result_data):
        rec_id = result_data[pos]
        value = struct.unpack_from('<I', result_data, pos + 1)[0]
        name = FILTER_STATUS_NAMES.get(rec_id, f"id={rec_id:#04x}")
        lines.append(f"  [{rec_id:2d}] {name} = {value}")
        pos += 5
    return "\n".join(lines)


# Implementation hints: (context, proc) → hint text shown with --impl
IMPL_HINTS = {
    (0x01, 0x0D): """\
  # GetSystemParameterList — template for all "get list" procedures
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Response: N records of [index(1)][value uint32 LE(4)] — already implemented""",

    (0x01, 0x0E): """\
  # GetFirmwareVersionList — 13-byte padded payload, FIRST+CONS required
  # send_request(api_call, send_as_first_cons=True)   [FIRST→WRITE_0, CONS→WRITE_1]
  # Payload: bytes([count, id0, id1, ..., 0x00, ...])  # pad to 13 bytes total
  # Response: N records of [comp_id(1)][ascii_v1(1)][ascii_v2(1)][build(1)][0x00(1)]
  # Already implemented in GetFirmwareVersionList.py""",

    (0x01, 0x59): """\
  # GetFilterStatus — 13-byte padded payload, SINGLE frame
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Payload: bytes([count, id0, id1, ..., 0x00, ...])  # pad to 13 bytes total
  # Response: N records of [rec_id(1)][uint32 LE(4)]
  #   ID 7 = days_until_filter_change, 8 = last_reset ts, 9 = next_change ts, 10 = reset_count
  # Already implemented in GetFilterStatus.py""",

    (0x01, 0x09): """\
  # SetCommand — 1-byte arg = Commands enum value
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Already implemented; use SetCommandAsync(Commands.X)""",

    (0x01, 0x0A): """\
  # GetStoredProfileSetting — 1-byte arg = ProfileSettings index
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Already implemented in GetStoredProfileSetting.py""",

    (0x01, 0x45): """\
  # GetStatisticsDescale — no args
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Already implemented in GetStatisticsDescale.py""",

    (0x01, 0x51): """\
  # GetStoredCommonSetting — 1-byte arg = storedCommonSettingId
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # NOT YET IMPLEMENTED — may bridge to DpIds in BLE_COMMAND_REFERENCE.md
  # Template:
  #   api_call_attribute = ApiCallAttribute(0x01, 0x51, 0x01)
  #   def get_payload(self): return bytes([self.setting_id])
  #   def result(self, data): return struct.unpack('<H', data[:2])[0]""",

    (0x00, 0x82): """\
  # GetDeviceIdentification — no args
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Already implemented in GetDeviceIdentification.py""",

    (0x00, 0x86): """\
  # GetDeviceInitialOperationDate — no args
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Already implemented in GetDeviceInitialOperationDate.py""",

    (0x01, 0x81): """\
  # GetSOCApplicationVersions — no args
  # send_request(api_call)   [SINGLE frame, WRITE_0]
  # Already implemented in GetSOCApplicationVersions.py""",
}


def decode_firmware_records(result_data: bytes) -> str:
    """Parse GetFirmwareVersionList result bytes into readable firmware versions."""
    if not result_data:
        return "  (no data)"
    count = result_data[0]
    lines = [f"  {count} firmware components:"]
    pos = 1
    while pos + 4 < len(result_data):
        comp_id = result_data[pos]
        v1 = chr(result_data[pos + 1]) if 0x20 <= result_data[pos + 1] <= 0x7E else f"\\x{result_data[pos+1]:02x}"
        v2 = chr(result_data[pos + 2]) if 0x20 <= result_data[pos + 2] <= 0x7E else f"\\x{result_data[pos+2]:02x}"
        build = result_data[pos + 3]
        lines.append(f"  FW{comp_id:02X}: \"{v1}{v2}\" build={build} (0x{build:02X}={build}d)")
        pos += 5
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Multi-frame assembler
# ---------------------------------------------------------------------------

WRITE_HANDLES = {0x0003, 0x0006, 0x0009, 0x000C}
READ_HANDLES  = {0x000F, 0x0013, 0x0017, 0x001B}


class Assembler:
    """Accumulates FIRST + CONS frames and returns decoded body when complete."""

    def __init__(self):
        self._pending: Optional[dict] = None

    def feed(self, frame: bytes) -> Optional[bytes]:
        """
        Feed a 20-byte frame.  Returns assembled body bytes when all CONS frames
        have arrived, or None if still waiting.
        """
        hdr = frame[0]
        kind = frame_kind(hdr)

        if "FIRST" in kind:
            first_payload = frame[2:20]  # 18 bytes
            # Detect payload format:
            #   0x17-type: payload[0] is flags (0x00), body_len at [2], body starts at [5]
            #   0x30-type: payload[0] is msg_type (0x04/0x05), body_len at [3], body starts at [6]
            if first_payload[0] in (0x04, 0x05):
                body_len = first_payload[3]
                body_start = 6
            else:
                body_len = first_payload[2]
                body_start = 5

            # Compute how many CONS frames are needed from body_len.
            # For header-encoded count (FIRST[N]): use that directly.
            # For FIRST_DEV (0x30): derive from body_len.
            if kind == "FIRST_DEV":
                data_from_first = (18 - body_start) + 1  # payload[body_start:] + frame[1]
                remaining = max(0, body_len - data_from_first)
                cons_needed = (remaining + 18) // 19  # ceil(remaining / 19)
            else:
                cons_needed = _sub_frame_field(hdr)

            self._pending = {
                "cons_needed": cons_needed,
                "body_len": body_len,
                "body_start": body_start,
                "first": frame,
                "cons": [],
            }
            if cons_needed == 0:
                return self._assemble()
            return None

        if "CONS" in kind and self._pending is not None:
            self._pending["cons"].append(frame)
            if len(self._pending["cons"]) >= self._pending["cons_needed"]:
                return self._assemble()
            return None

        return None

    def _assemble(self) -> bytes:
        first = self._pending["first"]
        cons_frames = self._pending["cons"]
        body_len = self._pending["body_len"]
        body_start = self._pending["body_start"]
        self._pending = None

        first_payload = first[2:20]
        parts = [first_payload[body_start:], bytes([first[1]])]
        for c in cons_frames:
            parts.append(c[1:])

        raw = b"".join(parts)
        return raw[:body_len]


def decode_assembled_body(body: bytes, is_response: bool) -> str:
    """Decode an assembled multi-frame body."""
    if len(body) < 4:
        return f"short body {body.hex()}"
    if is_response:
        # body: [status][node][ctx][proc][result_len][result...]
        status = body[0]
        ctx = body[2]
        proc = body[3]
        result_len = body[4] if len(body) > 4 else 0
        result = body[5:5 + result_len]
        return _fmt_response(0, ctx, proc, status, result)
    else:
        # body: [node][ctx][proc][arg_len][args...]
        ctx = body[1]
        proc = body[2]
        arg_len = body[3]
        args = body[4:4 + arg_len]
        return _fmt_request(0, ctx, proc, args)


# ---------------------------------------------------------------------------
# Log parser
# ---------------------------------------------------------------------------

LINE_RE = re.compile(
    r"(\w+ +\d+ \d+:\d+:\d+\.\d+)\s+"   # timestamp
    r"(ATT Send|ATT Receive)\s+"          # direction
    r"0x[0-9A-Fa-f]+\s+"                 # connection handle
    r"([\dA-Fa-f:]{17})\s+"             # MAC address
    r".*?"                               # description (non-greedy)
    r"\s{2,}"                            # gap before hex dump
    r"((?:[0-9A-Fa-f]{2} )+[0-9A-Fa-f]{2})\s*$"  # hex bytes
)


def parse_line(line: str):
    """Return (timestamp_str, direction, mac, bytes_list) or None."""
    m = LINE_RE.match(line.strip())
    if not m:
        return None
    ts_str, direction, mac, hex_str = m.groups()
    raw = bytes(int(x, 16) for x in hex_str.split())
    return ts_str, direction, mac, raw


def extract_geberit_frame(raw: bytes):
    """
    Return (att_handle, geberit_20_bytes) from a raw HCI/L2CAP/ATT packet,
    or None if not a Geberit GATT frame.
    """
    # Minimum: HCI(2) + HCI_len(2) + L2CAP_len(2) + channel(2) + ATT_op(1) + handle(2) + value(20)
    if len(raw) < 31:
        return None
    att_opcode = raw[8]
    if att_opcode not in (0x52, 0x1B):  # Write Without Response or Handle Value Notification
        return None
    handle = raw[9] | (raw[10] << 8)
    if handle not in HANDLES:
        return None
    frame = raw[11:31]
    if len(frame) != 20:
        return None
    return handle, frame


def _parse_time(ts_str: str) -> Optional[datetime]:
    """Parse 'Apr 11 12:52:36.909' into a datetime (year=2000 dummy)."""
    try:
        return datetime.strptime("2000 " + ts_str, "%Y %b %d %H:%M:%S.%f")
    except ValueError:
        return None


def _time_from_str(s: str) -> Optional[datetime]:
    """Parse HH:MM:SS into a dummy datetime for comparison."""
    try:
        t = datetime.strptime(s, "%H:%M:%S")
        return datetime(2000, 1, 1, t.hour, t.minute, t.second)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

# GetSystemParameterList index → human label
# Source: GetSystemParameterList.py docstring (authoritative); indices 7–11 unnamed there.
# Our bridge polls [0,1,2,3,4,5,7,9]. iPhone polls [0,1,2,3,4,5,6,7,4,8,9,10].
_SPL_PARAM_NAMES = {
    0: "user_sitting",
    1: "anal_shower",
    2: "lady_shower",
    3: "dryer",
    4: "descaling_state",
    5: "descaling_min",
    6: "last_error",
    7: "unknown7",
    8: "unknown8",
    9: "orientation_light",
    10: "unknown10",
    11: "unknown11",
}

_PHASE_SUMMARIES = {
    "Init":
        "iPhone sends pre-subscription and notification subscription commands "
        "to open the BLE communication channel.",
    "Identification":
        "iPhone reads static device metadata: model, serial number, "
        "firmware versions, and installation date.",
    "State Poll":
        "iPhone reads the current live device state (seat occupied, showers, dryer).",
    "Filter Status":
        "iPhone checks the ceramic honeycomb filter replacement countdown.",
    "Profile Settings":
        "iPhone reads stored user preference settings via proc 0x53 "
        "(the correct storage area that matches the in-app sliders).",
    "Firmware Versions":
        "iPhone reads detailed firmware component versions.",
    "Descale Statistics":
        "iPhone reads descale cycle history and statistics.",
}


@dataclass
class _MdFrame:
    ts_short: str
    direction: str      # "→" or "←"
    handle_name: str
    ctx: int
    proc: int
    is_response: bool
    status: int = 0
    args: bytes = b""   # request argument bytes
    result: bytes = b"" # response result bytes
    decoded: str = ""   # fallback text from existing decoder


def _extract_ascii(data: bytes, min_len: int = 4) -> str:
    """Extract printable ASCII runs from binary data, joined with ' | '."""
    runs = re.findall(rb'[ -~]{' + str(min_len).encode() + rb',}', data)
    return " | ".join(r.decode('ascii', errors='replace') for r in runs)


def _md_annotate(frame: _MdFrame, counters: dict) -> str:
    """Return a human-readable one-line annotation for a decoded BLE frame."""
    ctx, proc = frame.ctx, frame.proc

    if (ctx, proc) == (0x01, 0x11):
        if frame.is_response:
            return "ACK"
        n = counters.get("0x11", 0) + 1
        counters["0x11"] = n
        return f"Pre-subscription handshake ({n} of 4)"

    if (ctx, proc) == (0x01, 0x13):
        if frame.is_response:
            return "ACK"
        n = counters.get("0x13", 0) + 1
        counters["0x13"] = n
        return f"Notification subscription ({n} of 4)"

    if (ctx, proc) == (0x00, 0x82):
        if frame.is_response:
            if frame.result:
                return "Device: " + (_extract_ascii(frame.result) or "OK")
            return "OK"
        return "Reading device model and SAP number"

    if (ctx, proc) == (0x00, 0x86):
        if frame.is_response:
            if frame.result:
                return "Date: " + (_extract_ascii(frame.result, min_len=3) or "OK")
            return "OK"
        return "Reading installation date"

    if (ctx, proc) == (0x01, 0x81):
        if frame.is_response and len(frame.result) >= 3:
            v1 = chr(frame.result[0]) if 0x20 <= frame.result[0] <= 0x7E else f"\\x{frame.result[0]:02x}"
            v2 = chr(frame.result[1]) if 0x20 <= frame.result[1] <= 0x7E else f"\\x{frame.result[1]:02x}"
            build = frame.result[2]
            return f"Firmware: {v1}{v2}.{build}"
        return "ACK" if frame.is_response else "Reading firmware versions (RS/TS build numbers)"

    if (ctx, proc) == (0x01, 0x0D):
        if not frame.is_response:
            # Parse the requested param list and store for positional response decoding.
            if frame.args and len(frame.args) >= 1:
                count = frame.args[0]
                params = list(frame.args[1:1 + count])
                counters["last_spl_params"] = params
                return f"Polling {count} params: [{', '.join(str(p) for p in params)}]"
            return "Polling live device state"
        # Response: use the stored request param list for positional label lookup.
        # Value layout (from Deserializer.py): result[0]=a_byte, then for each record i:
        #   result[i*5+1] = echoed param id (may be 0 on some firmware — do NOT rely on it)
        #   result[i*5+2:i*5+6] = LE uint32 value  ← positional, matches request order
        if frame.result:
            import struct as _struct
            result = frame.result
            params = counters.get("last_spl_params", list(range(12)))
            parts = []
            i = 0
            while i * 5 + 5 < len(result):
                param_id = params[i] if i < len(params) else i
                val = _struct.unpack_from('<I', result, i * 5 + 2)[0]
                label = _SPL_PARAM_NAMES.get(param_id, f"unknown{param_id}")
                parts.append(f"{param_id}: {label} = {val}")
                i += 1
            return "<br>".join(parts) if parts else "OK"
        return "OK"

    if (ctx, proc) == (0x01, 0x59):
        if frame.is_response:
            if frame.result:
                import struct
                count = frame.result[0]
                days = cycles = reset_count = None
                pos = 1
                while pos + 4 < len(frame.result):
                    rid = frame.result[pos]
                    val = struct.unpack_from('<I', frame.result, pos + 1)[0]
                    if rid == 7:
                        days = val
                    elif rid == 1:
                        cycles = val
                    elif rid == 10:
                        reset_count = val
                    pos += 5
                parts = []
                if days is not None:
                    parts.append(f"days_remaining={days}")
                if cycles is not None:
                    parts.append(f"cycles={cycles}")
                if reset_count is not None:
                    parts.append(f"resets={reset_count}")
                summary = f"{count} record(s)"
                if parts:
                    summary += ": " + ", ".join(parts)
                return summary
            return "ACK"
        return "Checking ceramic filter replacement status"

    if (ctx, proc) == (0x01, 0x53):
        # payload: [profile_id, setting_id]
        setting_id = frame.args[1] if len(frame.args) >= 2 else (frame.args[0] if frame.args else -1)
        setting_name = PROFILE_SETTINGS.get(setting_id, f"setting_{setting_id}")
        if frame.is_response:
            val = int.from_bytes(frame.result[:2], "little") if len(frame.result) >= 2 else "?"
            return f"→ {val}"
        profile_id = frame.args[0] if frame.args else 0
        return f"Reading {setting_name} (profile {profile_id})"

    if (ctx, proc) == (0x01, 0x0A):
        # payload: [setting_id]  — iPhone init/unlock storage area, different from 0x53
        setting_id = frame.args[0] if frame.args else -1
        setting_name = PROFILE_SETTINGS.get(setting_id, f"setting_{setting_id}")
        if frame.is_response:
            val = int.from_bytes(frame.result[:2], "little") if len(frame.result) >= 2 else "?"
            return f"→ {val} *(init area — not user preference)*"
        return f"Init-unlock read: {setting_name} *(proc 0x0A — different storage from 0x53)*"

    if (ctx, proc) == (0x01, 0x0B):
        setting_id = frame.args[0] if frame.args else -1
        setting_name = PROFILE_SETTINGS.get(setting_id, f"setting_{setting_id}")
        if frame.is_response:
            return "ACK"
        val = int.from_bytes(frame.args[1:3], "little") if len(frame.args) >= 3 else "?"
        return f"Init-unlock write: {setting_name} = {val} *(proc 0x0B)*"

    if (ctx, proc) == (0x01, 0x54):
        # payload: [profile_id, setting_id, val_lo, val_hi]
        if len(frame.args) >= 4:
            setting_name = PROFILE_SETTINGS.get(frame.args[1], f"setting_{frame.args[1]}")
            val = int.from_bytes(frame.args[2:4], "little")
            return "ACK" if frame.is_response else f"**Changing {setting_name} → {val}**"
        return "ACK" if frame.is_response else "Changing profile setting"

    if (ctx, proc) == (0x01, 0x09):
        if frame.is_response:
            return "ACK"
        cmd_name = COMMANDS.get(frame.args[0] if frame.args else -1,
                                 f"cmd=0x{frame.args.hex() if frame.args else '??'}")
        return f"**Triggering {cmd_name}**"

    if (ctx, proc) == (0x01, 0x0E):
        if frame.is_response and frame.result:
            parts = []
            pos = 1
            while pos + 4 <= len(frame.result):
                cid = frame.result[pos]
                v1 = chr(frame.result[pos+1]) if 0x20 <= frame.result[pos+1] <= 0x7E else "?"
                v2 = chr(frame.result[pos+2]) if 0x20 <= frame.result[pos+2] <= 0x7E else "?"
                build = frame.result[pos+3]
                parts.append(f"FW{cid:02X}:{v1}{v2}.{build}")
                pos += 5
            if parts:
                display = " | ".join(parts[:4])
                if len(parts) > 4:
                    display += f" (+{len(parts)-4} more)"
                return display
        return "Reading detailed firmware component versions"

    if (ctx, proc) == (0x01, 0x45):
        if frame.is_response and frame.result:
            return f"Descale stats: {len(frame.result)} bytes"
        return "ACK" if frame.is_response else "Reading descale cycle statistics"

    if (ctx, proc) == (0x01, 0x51):
        setting_id = frame.args[0] if frame.args else -1
        if frame.is_response:
            if frame.result and len(frame.result) >= 2:
                val = int.from_bytes(frame.result[:2], "little")
                return f"Common setting value={val}"
            return "ACK"
        return f"Reading common setting id={setting_id} (mapping TBD)"

    if (ctx, proc) == (0x01, 0x56):
        return "ACK" if frame.is_response else f"Setting device registration level (args={frame.args.hex()})"

    name = PROCEDURES.get((ctx, proc), f"Proc({ctx:#04x},{proc:#04x})")
    return f"*(unknown: {name})*"


def _md_phase(ctx: int, proc: int, args: bytes) -> str:
    """Map a request's (ctx, proc) to a logical phase name."""
    if (ctx, proc) in ((0x01, 0x11), (0x01, 0x13)):
        return "Init"
    if (ctx, proc) in ((0x00, 0x82), (0x00, 0x86), (0x01, 0x81)):
        return "Identification"
    if (ctx, proc) == (0x01, 0x0D):
        return "State Poll"
    if (ctx, proc) == (0x01, 0x59):
        return "Filter Status"
    if (ctx, proc) in ((0x01, 0x53), (0x01, 0x0A), (0x01, 0x0B)):
        return "Profile Settings"
    if (ctx, proc) == (0x01, 0x54):
        setting_name = PROFILE_SETTINGS.get(args[1] if len(args) > 1 else -1, "Setting")
        return f"User Action: Change {setting_name}"
    if (ctx, proc) == (0x01, 0x09):
        cmd_name = COMMANDS.get(args[0] if args else -1, "Command")
        return f"User Action: {cmd_name}"
    if (ctx, proc) == (0x01, 0x0E):
        return "Firmware Versions"
    if (ctx, proc) == (0x01, 0x45):
        return "Descale Statistics"
    name = PROCEDURES.get((ctx, proc), f"Proc({ctx:#04x},{proc:#04x})")
    return name


def render_markdown(md_frames: List[_MdFrame], logfile: str, mac: str) -> str:
    """Group decoded frames into logical phases and render as annotated markdown."""
    if not md_frames:
        return "*(no frames decoded)*\n"

    out: List[str] = []
    basename = os.path.basename(logfile)
    ts_first = md_frames[0].ts_short
    ts_last = md_frames[-1].ts_short

    out.append(f"# BLE Traffic Analysis: {basename}")
    out.append(f"**Device:** `{mac}` &nbsp; **Time window:** {ts_first} – {ts_last}")
    out.append("")

    # Group frames into (phase_name, [frames]) segments.
    # A new phase starts whenever a *request* frame carries a different phase label.
    # Response frames are always appended to the current phase.
    phases: List[Tuple[str, List[_MdFrame]]] = []
    current_phase_name: Optional[str] = None
    current_phase_frames: List[_MdFrame] = []

    for frame in md_frames:
        if not frame.is_response:
            phase = _md_phase(frame.ctx, frame.proc, frame.args)
            if phase != current_phase_name:
                if current_phase_name is not None:
                    phases.append((current_phase_name, current_phase_frames))
                current_phase_name = phase
                current_phase_frames = [frame]
            else:
                current_phase_frames.append(frame)
        else:
            current_phase_frames.append(frame)

    if current_phase_name is not None:
        phases.append((current_phase_name, current_phase_frames))

    # Render each phase as a markdown section.
    phase_occurrence: dict = {}
    ann_counters: dict = {}

    for phase_name, p_frames in phases:
        phase_occurrence[phase_name] = phase_occurrence.get(phase_name, 0) + 1
        occurrence = phase_occurrence[phase_name]

        suffix = f" #{occurrence}" if occurrence > 1 else ""
        ts_range = p_frames[0].ts_short
        if len(p_frames) > 1:
            ts_range += f" – {p_frames[-1].ts_short}"
        n_frames = len(p_frames)
        heading = f"## {phase_name}{suffix} ({ts_range}, {n_frames} frame{'s' if n_frames != 1 else ''})"

        out.append("---")
        out.append("")
        out.append(heading)

        summary = _PHASE_SUMMARIES.get(phase_name)
        if not summary and phase_name.startswith("User Action"):
            summary = "User triggered an action on the device."
        if summary:
            out.append(f"*{summary}*")
        out.append("")

        out.append("| Time | Dir | Procedure | Annotation |")
        out.append("|------|-----|-----------|------------|")

        for frame in p_frames:
            proc_name = PROCEDURES.get((frame.ctx, frame.proc),
                                       f"Proc({frame.ctx:#04x},{frame.proc:#04x})")
            annotation = _md_annotate(frame, ann_counters)
            out.append(f"| {frame.ts_short} | {frame.direction} | {proc_name} | {annotation} |")

        out.append("")

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Decode Geberit AquaClean BLE frames from iOS PacketLogger export"
    )
    parser.add_argument("logfile", help="Path to PacketLogger .txt export")
    parser.add_argument("--mac", default="38:AB:41:2A:0D:67",
                        help="Filter to MAC address (default: %(default)s)")
    parser.add_argument("--from", dest="from_time", metavar="HH:MM:SS",
                        help="Start time filter")
    parser.add_argument("--to", dest="to_time", metavar="HH:MM:SS",
                        help="End time filter")
    parser.add_argument("--filter", dest="filter_proc", metavar="PROC",
                        help="Show only frames matching procedure name or hex (e.g. 0x0E or firmware)")
    parser.add_argument("--firmware", action="store_true",
                        help="Shorthand for --filter 0x0E (GetFirmwareVersionList)")
    parser.add_argument("--decode-fw", action="store_true",
                        help="Pretty-print firmware version records from GetFirmwareVersionList")
    parser.add_argument("--filter-status", action="store_true",
                        help="Shorthand for --filter 0x59 (GetFilterStatus)")
    parser.add_argument("--decode-filter", action="store_true",
                        help="Pretty-print filter status records from GetFilterStatus")
    parser.add_argument("--impl", action="store_true",
                        help="Show Python CallClass implementation hint after each procedure")
    parser.add_argument("--verbose", action="store_true",
                        help="Print result hex for all responses")
    parser.add_argument("--raw", action="store_true",
                        help="Print every raw 20-byte Geberit frame without decoding")
    parser.add_argument("--markdown", action="store_true",
                        help="Render session as annotated markdown grouped by logical phase")
    parser.add_argument("--output", metavar="FILE",
                        help="Write markdown to FILE instead of stdout (requires --markdown)")
    args = parser.parse_args()

    if args.firmware:
        args.filter_proc = "0x0E"
    if args.filter_status:
        args.filter_proc = "0x59"

    mac_filter = args.mac.upper()
    time_from = _time_from_str(args.from_time) if args.from_time else None
    time_to = _time_from_str(args.to_time) if args.to_time else None

    # Build procedure filter set
    proc_filter: Optional[set] = None
    if args.filter_proc:
        proc_filter = set()
        token = args.filter_proc.lower()
        for (ctx, proc), name in PROCEDURES.items():
            if token in name.lower() or token == hex(proc).lower():
                proc_filter.add((ctx, proc))
        if not proc_filter:
            print(f"Warning: no procedures matched filter '{args.filter_proc}'", file=sys.stderr)

    # Per-device, per-direction assemblers: key = (mac, "R") or (mac, "W")
    assemblers: dict = {}

    # Frames collected for --markdown mode
    md_frames: List[_MdFrame] = []

    try:
        with open(args.logfile, encoding="utf-8", errors="replace") as f:
            for lineno, line in enumerate(f, 1):
                parsed = parse_line(line)
                if not parsed:
                    continue
                ts_str, direction, mac, raw = parsed

                if mac.upper() != mac_filter:
                    continue

                # Time filter
                ts = _parse_time(ts_str)
                if ts:
                    ts_cmp = datetime(2000, 1, 1, ts.hour, ts.minute, ts.second,
                                      ts.microsecond)
                    if time_from and ts_cmp < time_from:
                        continue
                    if time_to and ts_cmp > time_to:
                        continue

                result = extract_geberit_frame(raw)
                if not result:
                    continue
                handle, frame = result

                dir_arrow = "→" if direction == "ATT Send" else "←"
                handle_name = HANDLES.get(handle, f"h{handle:#06x}")
                ts_short = ts_str.split()[2] if ts_str else "??"
                is_response = handle in READ_HANDLES

                if args.raw:
                    print(f"{ts_short}  {dir_arrow}  {handle_name:8s}  {frame.hex()}")
                    continue

                hdr = frame[0]
                kind = frame_kind(hdr)

                # Feed multi-frame assembler — separate per direction to avoid
                # cross-contamination between outgoing CONS (WRITE_1) and incoming
                # FIRST/CONS (READ channels).
                asm_key = (mac, "R" if is_response else "W")
                asm = assemblers.setdefault(asm_key, Assembler())

                if "FIRST" in kind or ("CONS" in kind and asm._pending):
                    assembled = asm.feed(frame)
                    if assembled and len(assembled) >= 4:
                        if is_response:
                            ctx, proc = assembled[2], assembled[3]
                            result_len = assembled[4] if len(assembled) > 4 else 0
                            result_data = assembled[5:5 + result_len]
                        else:
                            ctx, proc = assembled[1], assembled[2]
                            arg_len = assembled[3]
                            result_data = assembled[4:4 + arg_len]

                        if proc_filter and (ctx, proc) not in proc_filter:
                            continue

                        decoded = decode_assembled_body(assembled, is_response)

                        if not args.markdown:
                            print(f"{ts_short}  {dir_arrow}  {handle_name:8s}  {decoded}")

                            if args.decode_fw and (ctx, proc) == (0x01, 0x0E) and is_response:
                                print(decode_firmware_records(result_data))

                            if args.decode_filter and (ctx, proc) == (0x01, 0x59) and is_response:
                                print(decode_filter_status_records(result_data))

                            if args.impl:
                                hint = IMPL_HINTS.get((ctx, proc))
                                if hint:
                                    print(hint)

                            if args.verbose and result_data:
                                print(f"          result_hex={result_data.hex()}")

                        if args.markdown:
                            if is_response:
                                status_byte = assembled[0] if assembled else 0
                                md_result = assembled[5:5 + result_len] if len(assembled) > 5 else b""
                                md_frames.append(_MdFrame(
                                    ts_short=ts_short, direction=dir_arrow,
                                    handle_name=handle_name, ctx=ctx, proc=proc,
                                    is_response=True, status=status_byte,
                                    result=md_result, decoded=decoded,
                                ))
                            else:
                                md_args = assembled[4:4 + arg_len]
                                md_frames.append(_MdFrame(
                                    ts_short=ts_short, direction=dir_arrow,
                                    handle_name=handle_name, ctx=ctx, proc=proc,
                                    is_response=False, args=md_args, decoded=decoded,
                                ))
                    continue

                if kind in ("CONTROL", "INFO"):
                    # Don't show control/info frames unless raw mode
                    continue

                if kind == "SINGLE":
                    payload = frame[1:20]
                    if len(payload) < 10:
                        continue
                    msg_type = payload[0]
                    if msg_type == 0x04:
                        ctx, proc = payload[7], payload[8]
                    elif msg_type == 0x05:
                        ctx, proc = payload[8], payload[9]
                    else:
                        continue

                    if proc_filter and (ctx, proc) not in proc_filter:
                        continue

                    decoded = decode_single_frame(frame, direction)

                    if not args.markdown:
                        print(f"{ts_short}  {dir_arrow}  {handle_name:8s}  {decoded}")

                        # Decode firmware versions inline
                        if args.decode_fw and (ctx, proc) == (0x01, 0x0E) and msg_type == 0x05:
                            result_data = payload[11:11 + payload[10]] if len(payload) > 10 else b""
                            print(decode_firmware_records(result_data))

                        if args.decode_filter and (ctx, proc) == (0x01, 0x59) and msg_type == 0x05:
                            result_data = payload[11:11 + payload[10]] if len(payload) > 10 else b""
                            print(decode_filter_status_records(result_data))

                        if args.impl:
                            hint = IMPL_HINTS.get((ctx, proc))
                            if hint:
                                print(hint)

                        if args.verbose and msg_type == 0x05:
                            result_len = payload[10] if len(payload) > 10 else 0
                            result_data = payload[11:11 + result_len]
                            if result_data:
                                print(f"          result_hex={result_data.hex()}")

                    if args.markdown:
                        if msg_type == 0x04:
                            arg_len_s = payload[9]
                            md_args = payload[10:10 + arg_len_s]
                            md_frames.append(_MdFrame(
                                ts_short=ts_short, direction=dir_arrow,
                                handle_name=handle_name, ctx=ctx, proc=proc,
                                is_response=False, args=md_args, decoded=decoded,
                            ))
                        elif msg_type == 0x05:
                            md_status = payload[6]
                            md_result_len = payload[10] if len(payload) > 10 else 0
                            md_result = payload[11:11 + md_result_len]
                            md_frames.append(_MdFrame(
                                ts_short=ts_short, direction=dir_arrow,
                                handle_name=handle_name, ctx=ctx, proc=proc,
                                is_response=True, status=md_status,
                                result=md_result, decoded=decoded,
                            ))

    except FileNotFoundError:
        print(f"Error: file not found: {args.logfile}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass

    if args.markdown:
        md_output = render_markdown(md_frames, args.logfile, mac_filter)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(md_output)
            print(f"Written to {args.output}", file=sys.stderr)
        else:
            print(md_output)


if __name__ == "__main__":
    main()
