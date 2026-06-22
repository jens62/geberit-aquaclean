#!/usr/bin/env python3
"""
mock-geberit-mera.py v1.25.11
BLE peripheral mock for Geberit AquaClean Mera Comfort.

Simulates the GATT service and AquaClean procedure protocol used by the
Geberit Home App when onboarding to a Mera Comfort for the first time.

Protocol (no encryption, no SMP):
  - App writes 20-byte procedure requests to write characteristic
  - Mock responds via ATT notify on the A5 notify characteristic
  - Button press ceremony: app reads UUID 0x3A2B characteristic (returns b"ro"),
    waits for button; web UI "Press Button" triggers InfoFrame notify on A5

Requirements (Linux VM with BlueZ >= 5.50):
  pip install bluez_peripheral dbus-next aiohttp
  # geberit-aquaclean package (or clone) must be on PYTHONPATH

Run:
  sudo /path/to/python tools/mock-geberit-mera.py [--port 8766]

Web UI: http://<vm-ip>:8766/
"""

import sys
import asyncio
import subprocess
import argparse
import hashlib
import time
import json
from pathlib import Path

# ---- add project root so bridge modules are importable without pip install ----
_proj_root = Path(__file__).parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

# ---- bridge code uses custom log levels TRACE=5 / SILLY=1 ----
# Register them before any bridge import so Logger.trace()/silly() exist.
import logging as _logging


def _add_logging_level(level_name: str, level_num: int) -> None:
    method_name = level_name.lower()
    if not hasattr(_logging, level_name):
        _logging.addLevelName(level_num, level_name)
        setattr(_logging, level_name, level_num)

    def _for_level(self, message, *args, **kwargs):
        if self.isEnabledFor(level_num):
            self._log(level_num, message, args, **kwargs)

    if not hasattr(_logging.Logger, method_name):
        setattr(_logging.Logger, method_name, _for_level)


_add_logging_level("TRACE", 5)
_add_logging_level("SILLY", 1)

# ---- Logger ----
logger = _logging.getLogger("mera_mock")
logger.setLevel(_logging.DEBUG)
logger.propagate = False   # don't bubble to root logger

_log_fmt = _logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
_console_h = _logging.StreamHandler(sys.stdout)
_console_h.setFormatter(_log_fmt)
logger.addHandler(_console_h)

# ---- import CrcMessage from bridge — avoids duplicating the proprietary CRC16 ----
from aquaclean_console_app.aquaclean_core.Message.CrcMessage import CrcMessage  # noqa: E402

_BLEMSG_ID_CRC_RSP = 5   # matches Message.BLEMSG_ID_CRC_RSP

# ---- version ----
_MOCK_VERSION = "1.27.0"
_SCRIPT_HASH = hashlib.md5(Path(__file__).read_bytes()).hexdigest()[:8]

try:
    from importlib.metadata import version as _pkg_ver
    _BRIDGE_VERSION = _pkg_ver("geberit-aquaclean")
except Exception:
    _BRIDGE_VERSION = "unknown"

# ---- D-Bus / bluez_peripheral (mirror Alba mock import pattern) ----
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.util import Adapter

if "dbus_fast" in sys.modules:
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType, Variant
    from dbus_fast.service import dbus_property
    from dbus_fast.constants import PropertyAccess
else:
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant
    from dbus_next.service import dbus_property
    from dbus_next.constants import PropertyAccess

# ---- GATT UUIDs (Geberit AquaClean — matches BluetoothLeConnector constants) ----
_SVC_UUID       = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
_WRITE_0_UUID   = "3334429d-90f3-4c41-a02d-5cb3a13e0000"   # handle 0x0003 (requests)
_WRITE_1_UUID   = "3334429d-90f3-4c41-a02d-5cb3a23e0000"   # handle 0x0006 (FIRST continuation)
_NOTIFY_A5_UUID = "3334429d-90f3-4c41-a02d-5cb3a53e0000"   # handle 0x000F (primary response)
_NOTIFY_A6_UUID = "3334429d-90f3-4c41-a02d-5cb3a63e0000"   # handle 0x0013
_NOTIFY_A7_UUID = "3334429d-90f3-4c41-a02d-5cb3a73e0000"   # handle 0x0017
_NOTIFY_A8_UUID = "3334429d-90f3-4c41-a02d-5cb3a83e0000"   # handle 0x001B

# A6 InfoFrame burst — sent 9× immediately after iOS enables CCCD-A6.
# This is the Connection 1 trigger: iOS will not call GetDeviceIdentification
# until it receives at least one notify on A6.
# Source: nRF capture of iOS app v2.14.1 against real Mera Comfort.
_A6_INFO_FRAME = bytes.fromhex("800130140c030003000000003130001200b70800")
_READ_UUID      = "3a2b"   # handle 0x0020 (button-state, 16-bit UUID 0x3A2B — short form required for BlueZ Read By Type match)

# ---- Device identity ----
_ARTICLE     = "14621"
_SAP_NUMBER  = "HB2300EU000001"
_SERIAL      = "GB2000EU000001"
_DESCRIPTION = "AquaClean Mera Comfort"
_VARIANT     = 0x0D   # Mera Comfort

# Node IDs confirmed from real Mera onboarding capture
_NODE_IDS = bytes([3, 4, 5, 6, 7, 8, 9, 0xa, 0xb, 0xc, 0xe, 0xf])

# ---- Advertisement D-Bus path (bluez_peripheral default, used for unregister) ----
_ADVERT_PATH = "/com/spacecheese/bluez_peripheral/advert0"

# ---- Global state ----
_session_log: list = []
_button_pressed = False
_connected = False
_advert = None          # current _MeraAdvertisement instance
_advert_bus = None      # D-Bus connection stored for advert updates
_advert_adapter = None  # Adapter stored for advert updates
_advert_lock: asyncio.Lock = None  # prevents concurrent _update_advert calls


async def _update_advert(state_b: int) -> None:
    """Unregister current advertisement and re-register with updated IsButtonPressed flag.

    state_b=0x01 → iOS scan sees IsButtonPressed=True → device selected for Connection 1.
    state_b=0x00 → normal idle (button not pressed).

    bluez_peripheral.Advertisement has no unregister() method.
    We call LEAdvertisingManager1.UnregisterAdvertisement() directly via the cached
    adapter proxy, using the known fixed D-Bus path _ADVERT_PATH.
    """
    global _advert, _advert_bus, _advert_adapter
    if _advert_bus is None or _advert_adapter is None:
        logger.warning("_update_advert: bus/adapter not initialised")
        return
    async with _advert_lock:
        try:
            mgr = _advert_adapter._proxy.get_interface("org.bluez.LEAdvertisingManager1")
            await mgr.call_unregister_advertisement(_ADVERT_PATH)
        except Exception as e:
            logger.warning("advert unregister: %s", e)
        _advert = _MeraAdvertisement(state_b)
        try:
            await _advert.register(_advert_bus, _advert_adapter)
            _log("·", f"Advertisement updated: byte[2]=0x{state_b:02X}  IsButtonPressed={bool(state_b)}")
        except Exception as e:
            logger.error("advert re-register failed: %s", e)


def _log(direction: str, msg: str) -> None:
    entry = (time.strftime("%H:%M:%S"), direction, msg)
    _session_log.append(entry)
    if len(_session_log) > 200:
        _session_log.pop(0)
    logger.info("  %s %s", direction, msg)


# ---- Request parsing ----
def _parse_request(frame: bytes):
    """Parse 20-byte request frame → (ctx, proc, args).

    Request layout (SINGLE 0x11, FIRST 0x13/15/17):
      frame[0]    type header
      frame[1]    CrcMessage.id = 4 (CRC_REQ)
      frame[2]    segments = 255
      frame[3:5]  len (hi, lo)
      frame[5:7]  crc16 (hi, lo)
      frame[7]    node = 0x01
      frame[8]    ctx
      frame[9]    proc
      frame[10]   arg_len
      frame[11:]  args
    """
    ctx     = frame[8]
    proc    = frame[9]
    arg_len = frame[10]
    args    = bytes(frame[11:11 + arg_len]) if arg_len else b""
    return ctx, proc, args


# ---- Response building ----
def _build_frames(ctx: int, proc: int, result: bytes, status: int = 0) -> list:
    """Build 20-byte ATT notify frames for a procedure response.

    Uses CrcMessage.create() from the bridge for CRC16 computation.

    Response body: [status, 0x00, ctx, proc, result_len, ...result]
    Wrapped in CrcMessage: [id=5, seg=255, len_hi, len_lo, crc_hi, crc_lo, ...body]
    Split into 20-byte BLE frames: frame[0]=header, frame[1:20]=CrcMessage chunk.

    Headers encode SINGLE vs FIRST[N]+CONS[i]:
      0x11 = SINGLE  (IsSubFrameCount=1, SubFrameCount=0, no CONS)
      0x13 = FIRST + 1 CONS   (SubFrameCount=1)
      0x15 = FIRST + 2 CONS
      0x17 = FIRST + 3 CONS
      0x10 = CONS[0]  (IsSubFrameCount=0, SubFrameIndex=0)
      0x12 = CONS[1]
      0x14 = CONS[2]
    """
    body = bytearray(5 + len(result))
    body[0] = status
    body[1] = 0x00     # reserved (always 0 in device responses)
    body[2] = ctx
    body[3] = proc
    body[4] = len(result) & 0xFF
    body[5:] = result

    crc_msg = CrcMessage.create(_BLEMSG_ID_CRC_RSP, 0xFF, body)
    serialized = crc_msg.serialize()                   # 262 bytes

    content_len = 6 + len(body)                       # CrcMessage header + body bytes
    chunks = []
    for i in range(0, content_len, 19):
        chunk = serialized[i:i + 19]
        chunks.append(chunk + bytes(19 - len(chunk))) # pad last chunk to 19 bytes

    n_cons = len(chunks) - 1
    frames = []
    for i, chunk in enumerate(chunks):
        hdr = (0x11 | (n_cons << 1)) if i == 0 else (0x10 | ((i - 1) << 1))
        frames.append(bytes([hdr]) + bytes(chunk))
    return frames


# ---- Procedure dispatch ----
def _dispatch(ctx: int, proc: int, args: bytes) -> list:
    """Return list of 20-byte frames for the response to proc."""
    _log("←", f"proc=0x{proc:02X} ctx={ctx} args={args.hex() if args else '(none)'}")

    if proc == 0x82:              # GetDeviceIdentification
        result = _proc_82()
    elif proc == 0x05:            # GetNodeInventory
        result = _proc_05()
    elif proc == 0x81:            # GetSOCApplicationVersions
        result = _proc_81()
    elif proc == 0x0E:            # GetFirmwareVersionList
        result = _proc_0e(args)
    elif proc == 0x86:            # GetDeviceInitialOperationDate
        result = _proc_86()
    elif proc == 0x0D:            # GetSystemParameterList
        result = _proc_0d(args)
    elif proc == 0x09:            # SetCommand (shower/lid/flush toggle)
        result = b""
    elif proc in (0x08, 0x11, 0x13, 0x14, 0x15):  # Subscribe* / SetStored*
        result = b""
    elif proc == 0x07:            # GetPerNodeProfileSetting
        result = b""
    elif proc == 0x0A:            # GetActiveCommonSetting
        result = bytes([0, 0])    # 16-bit value = 0
    elif proc == 0x0B:            # SetActiveCommonSetting
        result = b""
    elif proc in (0x51, 0x53):    # GetStoredCommonSetting / GetStoredProfileSetting
        result = bytes([0, 0])
    elif proc == 0x55:            # GetDeviceRegistrationLevel
        result = bytes([0])
    elif proc == 0x59:            # GetFilterStatus
        result = bytes(10)
    else:
        _log("·", f"  unknown proc 0x{proc:02X} — returning empty OK")
        result = b""

    frames = _build_frames(ctx, proc, result)
    for f in frames:
        _log("→", f"  {f.hex()}")
    return frames


# ---- Procedure result builders ----
def _proc_82() -> bytes:
    """GetDeviceIdentification: variant + SAP + serial + description."""
    return (
        bytes([_VARIANT])
        + _SAP_NUMBER.encode("ascii") + b"\x00"
        + _SERIAL.encode("ascii") + b"\x00"
        + _DESCRIPTION.encode("ascii") + b"\x00"
    )


def _proc_05() -> bytes:
    """GetNodeInventory: count + node IDs."""
    return bytes([len(_NODE_IDS)]) + _NODE_IDS


def _proc_81() -> bytes:
    """GetSOCApplicationVersions: minimal version string."""
    ver = b"RS30TS206\x00"
    return bytes([1, len(ver)]) + ver


def _proc_0d(args: bytes) -> bytes:
    """GetSystemParameterList: 4-byte zero (uint32) per queried index."""
    if not args:
        return b""
    count = args[0]
    return bytes([count]) + bytes(count * 4)


def _proc_0e(args: bytes) -> bytes:
    """GetFirmwareVersionList: 5-byte records per requested component.

    Format: [count] + per component: [comp_id, v1, v2, build, reserved]
    Bridge parses: version=chr(v1)+chr(v2), main="RS{version}.0 TS{build}"
    Returning "RS30.0 TS206" for all components (consistent with _proc_81).
    """
    if not args:
        return b""
    count = min(args[0], len(args) - 1)
    comp_ids = list(args[1:1 + count])
    records = bytes([len(comp_ids)])
    for cid in comp_ids:
        records += bytes([cid, 0x33, 0x30, 206, 0])  # version="30", build=206
    return records


def _proc_86() -> bytes:
    """GetDeviceInitialOperationDate: UTF-8 date string."""
    return b"2023-01-01\x00"


# ---- GATT Service ----
class MeraService(Service):
    """Geberit AquaClean Mera Comfort GATT service.

    Write characteristics accept 20-byte request frames from the app.
    A5 notify characteristic delivers response frames back to the app.
    A6/A7/A8 are registered so the app's GATT discovery succeeds;
    the mock does not actively use them (all responses go on A5).
    """

    def __init__(self):
        super().__init__(_SVC_UUID, True)
        self._notify_value = bytes(20)
        self._notify_iface = None         # wired after register() via wire_notify()
        self._notify_a6_iface = None      # wired after register() via wire_notify_a6()

    def wire_notify(self, iface) -> None:
        self._notify_iface = iface

    def wire_notify_a6(self, iface) -> None:
        self._notify_a6_iface = iface

    @dbus_property(PropertyAccess.READ)
    def Includes(self) -> "ao":  # type: ignore
        # bluez_peripheral 0.1.7 bug: base class unconditionally appended self._path,
        # creating a self-include declaration that displaces A6–A8/A1/A2 char declarations.
        return []

    async def push_notify(self, frame: bytes) -> None:
        """Send an ATT notification on A5."""
        _log("→", f"NOTIFY A5 ({len(frame)}B): {frame.hex()}")
        self._notify_value = frame
        if self._notify_iface is None:
            _log("·", "WARNING: notify interface not wired — cannot push frame")
            return
        try:
            if hasattr(self._notify_iface, "changed"):
                self._notify_iface.changed(frame)
            else:
                self._notify_iface.emit_properties_changed(
                    {"Value": Variant("ay", list(frame))}
                )
        except Exception as e:
            _log("·", f"WARNING: push_notify failed: {e}")

    async def push_notify_a6(self, frame: bytes) -> None:
        """Send an ATT notification on A6."""
        _log("→", f"NOTIFY A6 ({len(frame)}B): {frame.hex()}")
        if self._notify_a6_iface is None:
            _log("·", "WARNING: A6 notify interface not wired — cannot push frame")
            return
        try:
            if hasattr(self._notify_a6_iface, "changed"):
                self._notify_a6_iface.changed(frame)
            else:
                self._notify_a6_iface.emit_properties_changed(
                    {"Value": Variant("ay", list(frame))}
                )
        except Exception as e:
            _log("·", f"WARNING: push_notify_a6 failed: {e}")

    async def _handle_request(self, raw: bytes) -> None:
        if len(raw) < 11:
            _log("·", f"frame too short ({len(raw)} B) — ignored")
            return
        hdr = raw[0]
        if hdr & 0x01:
            # IsSubFrameCount=1: SINGLE (SubFrameCount=0) or FIRST[N] (N>0)
            # For onboarding all app requests are SINGLE — multi-frame not yet assembled
            ctx, proc, args = _parse_request(raw)
            _log("←", f"proc 0x{proc:02X}  ctx={ctx}  args={args.hex() if args else '(none)'}")
            for frame in _dispatch(ctx, proc, args):
                await self.push_notify(frame)
                await asyncio.sleep(0.05)    # 50 ms between frames — D-Bus→BlueZ→ESP32→TCP pipeline needs time
        else:
            # IsSubFrameCount=0: CONS continuation frame — accumulate if needed later
            _log("·", f"CONS frame received (multi-frame request not yet assembled): {raw[:4].hex()}")

    @characteristic(_WRITE_0_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_0(self, options):
        return bytes(20)

    @write_0.setter
    def write_0(self, value, options):
        raw = bytes(value)
        _log("←", f"WRITE_0 ({len(raw)}B): {raw.hex()}")
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_1_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_1(self, options):
        return bytes(20)

    @write_1.setter
    def write_1(self, value, options):
        raw = bytes(value)
        _log("←", f"WRITE_1 ({len(raw)}B): {raw.hex()}")
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_NOTIFY_A5_UUID, CharFlags.NOTIFY)
    def notify_a5(self, options):
        return self._notify_value

    @characteristic(_NOTIFY_A6_UUID, CharFlags.NOTIFY)
    def notify_a6(self, options):
        return bytes(20)

    @characteristic(_NOTIFY_A7_UUID, CharFlags.NOTIFY)
    def notify_a7(self, options):
        return bytes(20)

    @characteristic(_NOTIFY_A8_UUID, CharFlags.NOTIFY)
    def notify_a8(self, options):
        return bytes(20)

    @characteristic(_READ_UUID, CharFlags.READ)
    def button_state_read(self, options):
        # App probes UUID 0x3A2B as a gating check immediately after MTU exchange.
        # Returns b"ro" while waiting for button press; App then waits for InfoFrame on A5.
        return b"ro"


class BatteryService(Service):
    """Standard BLE Battery Service (0x180F).

    BlueZ auto-registers its own Battery Service which requires authentication
    (Insufficient Authentication, ATT error 0x05) for reads.  Registering our
    own service overrides it with an unauthenticated READ, silencing the
    spurious error from iOS without affecting the onboarding flow.
    """

    def __init__(self):
        super().__init__("0000180f-0000-1000-8000-00805f9b34fb", True)

    @characteristic("00002a19-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def battery_level(self, options):
        return bytes([100])


# ---- Advertisement ----
class _MeraAdvertisement(Advertisement):
    """Advertisement matching the real Mera Comfort BLE payload (11-byte total).

    BlueZ exposes manufacturer data as (company_id, payload). The iOS app receives the
    full manufacturer-specific data INCLUDING the 2-byte company ID, so byte offsets
    in AquaCleanProduct.cs are counted from the company ID:

      full_data[0]   company ID low  0x00  (Geberit 0x0100) | 0xAA = IsEmergencyConnectPermitted
      full_data[1]   company ID high 0x01
      full_data[2]   payload[0]      state_b  0x00 idle | 0x01 = IsButtonPressed ← iOS reads THIS
      full_data[3-7] payload[1-5]    article  5-char ASCII (e.g. "14621") → model detection
      full_data[8]   payload[6]      0x00
      full_data[9-10]payload[7-8]    RS fw prefix "30"

    Total: 2 (company ID) + 9 (payload) = 11 bytes — the "11-byte variant" in ble-protocol.md.

    AquaCleanProduct.cs UpdateAdvertisingData():
      IsButtonPressed             = (full_data[2] == 1)    ← payload[0] = state_b
      IsEmergencyConnectPermitted = (full_data[0] == 0xAA) ← company ID low byte

    The iOS 15-second scan loop selects a device only when IsButtonPressed=True.
    _update_advert(1) sets state_b=0x01 → full_data[2]=0x01 → triggers Connection 1.
    """

    def __init__(self, state_b: int = 0):
        super().__init__(
            "Geberit AC PRO",                            # name → SCAN_RSP (BlueZ splits automatically)
            ["00003ea0-0000-1000-8000-00805f9b34fb"],    # service_uuids → ADV_IND
            appearance=0,
            timeout=0,
            manufacturerData={
                0x0100: bytes([state_b]) + _ARTICLE.encode("ascii") + bytes([0x00]) + b"30"
            },
        )


# ---- Adapter discovery (from Alba mock) ----
async def _find_adapter(bus):
    intro = await bus.introspect("org.bluez", "/")
    proxy = bus.get_proxy_object("org.bluez", "/", intro)
    objmgr = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
    objects = await objmgr.call_get_managed_objects()
    found = []
    for path, ifaces in objects.items():
        if "org.bluez.Adapter1" in ifaces:
            props = ifaces["org.bluez.Adapter1"]
            addr = props.get("Address", "??:??:??:??:??:??")
            name = props.get("Name", "")
            if hasattr(addr, "value"):
                addr = addr.value
            if hasattr(name, "value"):
                name = name.value
            found.append((str(path), str(addr), str(name)))
    if not found:
        raise RuntimeError("No BlueZ adapter found")
    if len(found) > 1:
        logger.warning("%d Bluetooth adapters found — using first", len(found))
        for p, a, n in found:
            logger.info("  %s  %s  %s", p, a, n)
    return found[0]   # (path, addr, name)


# ---- Web UI ----
_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mera Mock {version}</title>
  <style>
    body {{ font-family: monospace; margin: 2em; background: #1a1a2e; color: #e0e0e0; }}
    h1 {{ color: #00d4aa; }}
    .badge {{ display: inline-block; padding: 2px 10px; border-radius: 4px; }}
    .ok   {{ background: #1a5c3a; color: #00ff88; }}
    .warn {{ background: #3a3a1a; color: #ffdd00; }}
    .idle {{ background: #333; color: #aaa; }}
    button {{ margin: 4px; padding: 10px 20px; font-size: 1em; cursor: pointer;
              background: #0066cc; color: white; border: none; border-radius: 4px; }}
    button:hover {{ background: #0088ff; }}
    .danger {{ background: #cc3300; }}
    table {{ border-collapse: collapse; width: 100%; margin-bottom: 1em; }}
    th,td {{ text-align: left; padding: 4px 8px; border-bottom: 1px solid #333; }}
    th {{ color: #00d4aa; }}
    .log {{ font-size: 0.85em; max-height: 400px; overflow-y: auto;
            background: #111; padding: 8px; border: 1px solid #333; }}
    .recv {{ color: #88ccff; }} .send {{ color: #88ffcc; }} .info {{ color: #aaa; }}
  </style>
</head>
<body>
  <h1>Geberit AquaClean Mera Comfort — Mock {version}</h1>
  <p>
    BLE: <span class="badge {conn_cls}">{conn_txt}</span>
    &nbsp;
    Button: <span class="badge {btn_cls}">{btn_txt}</span>
  </p>
  <h2>Identity</h2>
  <table>
    <tr><th>Article</th><td>{article}</td></tr>
    <tr><th>SAP</th><td>{sap}</td></tr>
    <tr><th>Serial</th><td>{serial}</td></tr>
    <tr><th>Description</th><td>{description}</td></tr>
    <tr><th>Variant</th><td>0x{variant:02X}</td></tr>
    <tr><th>Device Name (0x2a00)</th><td>ro</td></tr>
  </table>
  <h2>Controls</h2>
  <form method="post" action="/button">
    <button type="submit">Press Button (confirm pairing)</button>
  </form>
  <form method="post" action="/clear-log" style="display:inline">
    <button type="submit" class="danger">Clear log</button>
  </form>
  <h2>Session log</h2>
  <div class="log">{log_html}</div>
  <script>setTimeout(function(){{location.reload();}}, 3000);</script>
</body>
</html>
"""


def _render_log() -> str:
    lines = []
    for ts, direction, msg in _session_log[-100:]:
        css = {"←": "recv", "→": "send"}.get(direction, "info")
        lines.append(f'<div class="{css}">[{ts}] {direction} {msg}</div>')
    return "\n".join(lines) or "<div class='info'>(no activity)</div>"


async def _handle_root(request):
    from aiohttp import web
    html = _HTML.format(
        version=_MOCK_VERSION,
        conn_cls="ok" if _connected else "idle",
        conn_txt="Connected" if _connected else "Idle",
        btn_cls="ok" if _button_pressed else "warn",
        btn_txt="Pressed" if _button_pressed else "Waiting",
        article=_ARTICLE, sap=_SAP_NUMBER, serial=_SERIAL,
        description=_DESCRIPTION, variant=_VARIANT,
        log_html=_render_log(),
    )
    return web.Response(content_type="text/html", text=html)


async def _handle_button(request, service: MeraService):
    from aiohttp import web
    global _button_pressed
    if _button_pressed:
        raise web.HTTPFound("/")
    _button_pressed = True
    _log("·", "Button pressed via web UI — advertisement byte[2]=0x01 (IsButtonPressed=True)")
    await _update_advert(1)
    raise web.HTTPFound("/")


async def _handle_status(request):
    from aiohttp import web
    return web.Response(
        content_type="application/json",
        text=json.dumps({
            "mock_version": _MOCK_VERSION,
            "connected": _connected,
            "button_pressed": _button_pressed,
            "log_entries": len(_session_log),
        }),
    )


async def _handle_clear_log(request):
    from aiohttp import web
    _session_log.clear()
    raise web.HTTPFound("/")


async def _send_a6_connection_frames(service: MeraService) -> None:
    """Send A6 InfoFrame burst after iOS completes GATT setup.

    The real device sends 9 frames immediately when CCCD-A6 is written
    (~1.6 s after connect). A 4 s fixed delay gives iOS time to complete
    GATT discovery across the larger BlueZ GATT table (~15 services vs the
    real device) and write Geberit CCCDs before the burst fires.
    iOS will not call GetDeviceIdentification until it receives this burst.
    """
    await asyncio.sleep(4.0)
    if not _connected:
        return
    _log("·", "Connection 1: sending A6 InfoFrame burst (9×)")
    for _ in range(9):
        await service.push_notify_a6(_A6_INFO_FRAME)
        await asyncio.sleep(0.05)


# ---- Main ----
async def main(web_port: int = 8765) -> None:
    global _advert_lock
    _advert_lock = asyncio.Lock()

    # Auto-named log file alongside this script
    _log_path = Path(__file__).parent / f"mock-geberit-mera_{time.strftime('%Y-%m-%d_%H-%M')}.log"
    _file_h = _logging.FileHandler(_log_path, encoding="utf-8")
    _file_h.setFormatter(_log_fmt)
    logger.addHandler(_file_h)
    logger.info("Log: %s", _log_path.name)

    # Clear any bond records without restarting the daemon.
    # btmgmt unpair removes the device (including stored IRK) from BlueZ memory and
    # disk — iOS's RPA cannot resolve to a bonded identity, preventing auth
    # enforcement on CCCDs.  Skipping the daemon restart preserves the battery
    # plugin's per-session device cache (see test-infrastructure.md).
    _hci_addr_path = Path("/sys/class/bluetooth/hci0/address")
    if _hci_addr_path.exists():
        _adapter_mac = _hci_addr_path.read_text().strip()
        _bt_dev_dir = Path("/var/lib/bluetooth") / _adapter_mac
        if _bt_dev_dir.is_dir():
            for _e in _bt_dev_dir.iterdir():
                if _e.is_dir() and len(_e.name) == 17 and _e.name.count(":") == 5:
                    subprocess.run(
                        ["btmgmt", "-i", "0", "unpair", _e.name],
                        capture_output=True,
                    )
                    logger.info("Unpaired bond record: %s", _e.name)

    from aiohttp import web

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Suppress harmless "does not have property TxPower" error from dbus_next
    class _SuppressDbusPropertyErrors(_logging.Filter):
        def filter(self, record):
            return "does not have property" not in record.getMessage()

    _logging.getLogger().addFilter(_SuppressDbusPropertyErrors())

    adapter_wrapper = await Adapter.get_first(bus)
    if not adapter_wrapper:
        logger.error("no Bluetooth adapter found")
        await bus.disconnect()
        return

    adapter_path = None
    try:
        adapter_path, adapter_addr, adapter_name = await _find_adapter(bus)
        logger.info("Adapter: %s  (%s)  path: %s", adapter_addr, adapter_name, adapter_path)
    except Exception as e:
        logger.warning("could not enumerate adapter: %s", e)

    # Set Device Name (GATT 0x2a00) to "ro" — cosmetic, matches real Mera Comfort.
    # The App's actual button-state gating check reads the READ characteristic
    # (UUID 0x3A2B, handle 0x0020) which also returns b"ro". Both are set to "ro".
    if adapter_path:
        try:
            ai = await bus.introspect("org.bluez", adapter_path)
            ap = bus.get_proxy_object("org.bluez", adapter_path, ai)
            props = ap.get_interface("org.freedesktop.DBus.Properties")
            await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", "ro"))
            logger.info("Adapter alias set to 'ro'  (GATT 0x2a00 Device Name)")
        except Exception as e:
            logger.warning("could not set adapter alias: %s", e)

    # Register GATT services
    service = MeraService()
    battery_service = BatteryService()
    try:
        await service.register(bus, "/org/bluez/example/mera", adapter_wrapper)
        await battery_service.register(bus, "/org/bluez/example/battery", adapter_wrapper)
        logger.info("GATT service registered")
        for _attr in ("_characteristics", "_chars"):
            _chars_list = getattr(service, _attr, None)
            if _chars_list:
                logger.info("GATT characteristics (%d):", len(_chars_list))
                for _c in _chars_list:
                    _uuid  = getattr(_c, "uuid",  getattr(_c, "_uuid",  "?"))
                    _flags = getattr(_c, "flags", getattr(_c, "_flags", "?"))
                    logger.info("  UUID=%s  flags=%s", _uuid, _flags)
                break
    except Exception as e:
        logger.error("GATT registration failed: %s", e)
        await bus.disconnect()
        return

    # Wire notify interface so push_notify() can send frames to the connected app
    notify_char = None
    for attr in ("_characteristics", "_chars"):
        chars = getattr(service, attr, None)
        if chars:
            for c in chars:
                if hasattr(c, "flags") and CharFlags.NOTIFY in c.flags:
                    notify_char = c
                    break
        if notify_char:
            break
    if notify_char:
        service.wire_notify(notify_char)
        logger.info("Notify characteristic wired (A5)")
    else:
        logger.warning("notify characteristic not found — push notifications disabled")

    # Wire A6 notify by UUID so push_notify_a6() can send the Connection 1 InfoFrame burst
    notify_a6_char = None
    for attr in ("_characteristics", "_chars"):
        chars = getattr(service, attr, None)
        if chars:
            for c in chars:
                uuid = str(getattr(c, "uuid", getattr(c, "_uuid", ""))).lower()
                if uuid == _NOTIFY_A6_UUID.lower():
                    notify_a6_char = c
                    break
        if notify_a6_char:
            break
    if notify_a6_char:
        service.wire_notify_a6(notify_a6_char)
        logger.info("A6 notify characteristic wired")
    else:
        logger.warning("A6 notify characteristic not found — Connection 1 burst disabled")

    # Advertise via D-Bus LEAdvertisingManager1 (same path as mock-geberit-alba).
    # BlueZ encodes UUID 0x3EA0 and manufacturer data into the ADV_IND payload;
    # the local name is placed in SCAN_RSP automatically.
    # Store bus/adapter globally so _update_advert() can unregister/re-register on button press.
    global _advert, _advert_bus, _advert_adapter
    _advert_bus = bus
    _advert_adapter = adapter_wrapper
    _advert = _MeraAdvertisement()
    await _advert.register(bus, adapter_wrapper)
    logger.info(
        "Advertising: UUID=0x3EA0  company=0x0100  byte[2]=0x00 (IsButtonPressed=False)"
        "  article=%s  name='Geberit AC PRO'", _ARTICLE
    )

    # Track BLE connections via ObjectManager + PropertiesChanged bus listener.
    # InterfacesAdded fires only for new Device1 objects; PropertiesChanged fires for
    # every Connected=True/False change including iOS RPA reconnects. Use both.
    global _connected, _button_pressed
    try:
        intro = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", intro)
        objmgr = proxy.get_interface("org.freedesktop.DBus.ObjectManager")

        def _on_device_connected(device_path: str, addr: str) -> None:
            global _connected
            if _connected:
                return  # deduplicate: InterfacesAdded and PropertiesChanged may both fire
            _connected = True
            _log("·", f"BLE client connected: {addr}")
            asyncio.ensure_future(_send_a6_connection_frames(service))

        def _on_device_disconnected(device_path: str) -> None:
            global _connected, _button_pressed
            if not _connected:
                return
            _connected = False
            if _button_pressed:
                asyncio.ensure_future(_update_advert(0))
            _button_pressed = False
            _log("·", f"BLE client disconnected: {device_path}")

        def _on_added(path, ifaces):
            if "org.bluez.Device1" in ifaces:
                addr = ifaces["org.bluez.Device1"].get("Address", "?")
                if hasattr(addr, "value"):
                    addr = addr.value
                _on_device_connected(path, addr)

        def _on_removed(path, ifaces):
            if "org.bluez.Device1" in ifaces:
                _on_device_disconnected(path)

        def _on_props_msg(msg) -> None:
            # Primary connection detection: PropertiesChanged fires for every connect/
            # disconnect including iOS RPA reconnects where InterfacesAdded is silent.
            if (msg.member != "PropertiesChanged" or
                    not msg.body or msg.body[0] != "org.bluez.Device1"):
                return
            changed = msg.body[1]
            if "Connected" not in changed:
                return
            val = changed["Connected"]
            if hasattr(val, "value"):
                val = val.value
            dev_path = msg.path
            # dev_XX_XX_XX_XX_XX_XX → XX:XX:XX:XX:XX:XX
            addr = dev_path.split("/")[-1][4:].replace("_", ":")
            if val:
                _on_device_connected(dev_path, addr)
            else:
                _on_device_disconnected(dev_path)

        objmgr.on_interfaces_added(_on_added)
        objmgr.on_interfaces_removed(_on_removed)

        # add_message_handler only sees signals already DELIVERED to this bus connection.
        # Without an explicit AddMatch rule, org.bluez PropertiesChanged signals are not
        # delivered. on_interfaces_added works because dbus_fast adds its own match rule
        # internally; add_message_handler has no such magic — we must add it ourselves.
        try:
            _dbus_intro = await bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus")
            _dbus_iface = bus.get_proxy_object(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", _dbus_intro
            ).get_interface("org.freedesktop.DBus")
            await _dbus_iface.call_add_match(
                "type='signal',sender='org.bluez',"
                "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'"
            )
            logger.info("PropertiesChanged match rule registered")
        except Exception as _me:
            logger.warning("AddMatch PropertiesChanged failed: %s", _me)

        bus.add_message_handler(_on_props_msg)
        logger.info("Connection tracking active (InterfacesAdded + PropertiesChanged)")
    except Exception as e:
        logger.warning("connection tracking unavailable: %s", e)

    # aiohttp web server
    app = web.Application()
    app.router.add_get("/", _handle_root)
    app.router.add_post("/button", lambda r: _handle_button(r, service))
    app.router.add_get("/status", _handle_status)
    app.router.add_post("/clear-log", _handle_clear_log)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", web_port).start()

    logger.info("")
    logger.info("--- Mera Comfort Mock Active ---")
    logger.info("    mock: %s  script: %s  bridge: %s", _MOCK_VERSION, _SCRIPT_HASH, _BRIDGE_VERSION)
    logger.info("    SAP: %s  article: %s", _SAP_NUMBER, _ARTICLE)
    logger.info("    Device Name (0x2a00): 'ro'")
    logger.info("    Web UI: http://0.0.0.0:%d/", web_port)
    logger.info("    Log file: %s", _log_path.name)
    logger.info("")

    await asyncio.get_event_loop().create_future()   # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="mock-geberit-mera.py — BLE peripheral mock for Geberit AquaClean Mera Comfort"
    )
    parser.add_argument("--port", type=int, default=8765, metavar="PORT",
                        help="Web UI port (default: 8765)")
    parser.add_argument("--version", action="version",
                        version=f"mock-geberit-mera {_MOCK_VERSION}")
    parsed = parser.parse_args()

    try:
        asyncio.run(main(web_port=parsed.port))
    except KeyboardInterrupt:
        logger.info("Mock stopped.")
