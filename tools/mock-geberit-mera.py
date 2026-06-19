#!/usr/bin/env python3
"""
mock-geberit-mera.py v1.0.0
BLE peripheral mock for Geberit AquaClean Mera Comfort.

Simulates the GATT service and AquaClean procedure protocol used by the
Geberit Home App when onboarding to a Mera Comfort for the first time.

Protocol (no encryption, no SMP):
  - App writes 20-byte procedure requests to write characteristic
  - Mock responds via ATT notify on the A5 notify characteristic
  - Button press ceremony: app reads Device Name ("ro"), waits for button,
    web UI "Press Button" triggers notify on A5

Requirements (Linux VM with BlueZ >= 5.50):
  pip install bluez_peripheral dbus-next aiohttp
  # geberit-aquaclean package (or clone) must be on PYTHONPATH

Run:
  sudo /path/to/python tools/mock-geberit-mera.py [--port 8766]

Web UI: http://<vm-ip>:8766/
"""

import sys
import asyncio
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

# ---- import CrcMessage from bridge — avoids duplicating the proprietary CRC16 ----
from aquaclean_console_app.aquaclean_core.Message.CrcMessage import CrcMessage  # noqa: E402

_BLEMSG_ID_CRC_RSP = 5   # matches Message.BLEMSG_ID_CRC_RSP

# ---- version ----
_MOCK_VERSION = "1.0.0"
_SCRIPT_HASH = hashlib.md5(Path(__file__).read_bytes()).hexdigest()[:8]

try:
    from importlib.metadata import version as _pkg_ver
    _BRIDGE_VERSION = _pkg_ver("geberit-aquaclean")
except Exception:
    _BRIDGE_VERSION = "unknown"

# ---- redirect print to add timestamps ----
import builtins as _builtins
_real_print = _builtins.print


def print(*args, **kwargs):  # noqa: A001
    ts = time.strftime("%H:%M:%S")
    _real_print(f"[{ts}]", *args, **kwargs)


# ---- D-Bus / bluez_peripheral (mirror Alba mock import pattern) ----
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.util import Adapter

if "dbus_fast" in sys.modules:
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType, Variant
else:
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant

# ---- GATT UUIDs (Geberit AquaClean — matches BluetoothLeConnector constants) ----
_SVC_UUID       = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
_WRITE_0_UUID   = "3334429d-90f3-4c41-a02d-5cb3a13e0000"   # handle 0x0003 (requests)
_WRITE_1_UUID   = "3334429d-90f3-4c41-a02d-5cb3a23e0000"   # handle 0x0006 (FIRST continuation)
_NOTIFY_A5_UUID = "3334429d-90f3-4c41-a02d-5cb3a53e0000"   # handle 0x000F (primary response)
_NOTIFY_A6_UUID = "3334429d-90f3-4c41-a02d-5cb3a63e0000"   # handle 0x0013
_NOTIFY_A7_UUID = "3334429d-90f3-4c41-a02d-5cb3a73e0000"   # handle 0x0017
_NOTIFY_A8_UUID = "3334429d-90f3-4c41-a02d-5cb3a83e0000"   # handle 0x001B

# ---- Device identity ----
_ARTICLE     = "14621"
_SAP_NUMBER  = "HB2300EU000001"
_SERIAL      = "GB2000EU000001"
_DESCRIPTION = "AquaClean Mera Comfort"
_VARIANT     = 0x0D   # Mera Comfort

# Node IDs confirmed from real Mera onboarding capture
_NODE_IDS = bytes([3, 4, 5, 6, 7, 8, 9, 0xa, 0xb, 0xc, 0xe, 0xf])

# ---- Global state ----
_session_log: list = []
_button_pressed = False
_connected = False


def _log(direction: str, msg: str) -> None:
    entry = (time.strftime("%H:%M:%S"), direction, msg)
    _session_log.append(entry)
    if len(_session_log) > 200:
        _session_log.pop(0)
    print(f"  {direction} {msg}")


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


def _build_info_frame() -> bytes:
    """Build an InfoFrame sent as unsolicited notify on button press.

    FrameType=INFO=4: bits[7:5]=0b100 → 0x80
    HasMsgType=True (bit4) + IsSubFrameCount=True (bit0): 0x80|0x10|0x01 = 0x91
    Body byte 1 = 0x01 (button-pressed state hint).
    InfoFrames are spontaneous device events — not CrcMessage-wrapped.
    """
    frame = bytearray(20)
    frame[0] = 0x91   # INFO, HasMsgType, IsSubFrameCount
    frame[1] = 0x01   # state: button pressed
    return bytes(frame)


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
    elif proc in (0x0D, 0x0E):   # GetSystemParameterList
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
        self._notify_iface = None    # wired after register() via wire_notify()

    def wire_notify(self, iface) -> None:
        self._notify_iface = iface

    async def push_notify(self, frame: bytes) -> None:
        """Send an ATT notification on A5."""
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

    async def _handle_request(self, raw: bytes) -> None:
        if len(raw) < 11:
            _log("·", f"frame too short ({len(raw)} B) — ignored")
            return
        hdr = raw[0]
        if hdr & 0x01:
            # IsSubFrameCount=1: SINGLE (SubFrameCount=0) or FIRST[N] (N>0)
            # For onboarding all app requests are SINGLE — multi-frame not yet assembled
            ctx, proc, args = _parse_request(raw)
            for frame in _dispatch(ctx, proc, args):
                await self.push_notify(frame)
                await asyncio.sleep(0.01)    # small gap between FIRST and CONS frames
        else:
            # IsSubFrameCount=0: CONS continuation frame — accumulate if needed later
            _log("·", f"CONS frame received (multi-frame request not yet assembled): {raw[:4].hex()}")

    @characteristic(_WRITE_0_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_0(self, options):
        return bytes(20)

    @write_0.setter
    def write_0(self, value, options):
        asyncio.ensure_future(self._handle_request(bytes(value)))

    @characteristic(_WRITE_1_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_1(self, options):
        return bytes(20)

    @write_1.setter
    def write_1(self, value, options):
        asyncio.ensure_future(self._handle_request(bytes(value)))

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


# ---- Advertisement ----
class _MeraAdvertisement(Advertisement):
    """Manufacturer-specific advertisement matching real Mera Comfort payload.

    Real device: AD type 0xFF, company 0x0001, data=[state_byte, article_chars]
    Example: 00 31 34 36 32 31  (state=0x00, article="14621")

    16-bit UUID 0x3EA0 (incomplete list) used by the app to filter Geberit devices.
    """

    def __init__(self, state_byte: int = 0):
        super().__init__(
            "",                                            # local_name (positional)
            ["00003ea0-0000-1000-8000-00805f9b34fb"],     # service_uuids (positional)
            appearance=0,
            timeout=0,
            manufacturerData={0x0001: bytes([state_byte]) + _ARTICLE.encode("ascii")},
        )


# ---- Adapter discovery (from Alba mock) ----
async def _find_adapter(bus):
    intro = await bus.introspect("org.bluez", "/")
    proxy = bus.get_proxy_object("org.bluez", "/", intro)
    objmgr = proxy.get_interface("org.freedesktop.DBus.ObjectManager")
    objects = await objmgr.call_get_managed_objects()
    for path, ifaces in objects.items():
        if "org.bluez.Adapter1" in ifaces:
            props = ifaces["org.bluez.Adapter1"]
            addr = props.get("Address")
            if hasattr(addr, "value"):
                addr = addr.value
            return str(path), str(addr)
    raise RuntimeError("No BlueZ adapter found")


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
    _button_pressed = True
    _log("·", "Button pressed via web UI — sending InfoFrame on A5")
    await service.push_notify(_build_info_frame())
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


# ---- Main ----
async def main(web_port: int = 8766) -> None:
    from aiohttp import web

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Suppress harmless "does not have property TxPower" error from dbus_next
    class _SuppressDbusPropertyErrors(_logging.Filter):
        def filter(self, record):
            return "does not have property" not in record.getMessage()

    _logging.getLogger().addFilter(_SuppressDbusPropertyErrors())

    adapter_wrapper = await Adapter.get_first(bus)
    if not adapter_wrapper:
        print("ERROR: no Bluetooth adapter found")
        await bus.disconnect()
        return

    adapter_path = None
    try:
        adapter_path, adapter_addr = await _find_adapter(bus)
        print(f"Adapter: {adapter_addr}  path: {adapter_path}")
    except Exception as e:
        print(f"Warning: could not enumerate adapter: {e}")

    # Set Device Name (GATT 0x2a00) to "ro" — the Geberit Home App reads this
    # during the button-press ceremony and expects exactly b"ro" from a Mera Comfort.
    if adapter_path:
        try:
            ai = await bus.introspect("org.bluez", adapter_path)
            ap = bus.get_proxy_object("org.bluez", adapter_path, ai)
            props = ap.get_interface("org.freedesktop.DBus.Properties")
            await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", "ro"))
            print("Adapter alias set to 'ro'  (GATT 0x2a00 Device Name)")
        except Exception as e:
            print(f"Warning: could not set adapter alias: {e}")

    # Register GATT service
    service = MeraService()
    try:
        await service.register(bus, "/org/bluez/example/mera", adapter_wrapper)
        print("GATT service registered")
    except Exception as e:
        print(f"GATT registration failed: {e}")
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
        print("Notify characteristic wired (A5)")
    else:
        print("WARNING: notify characteristic not found — push notifications disabled")

    # Register advertisement
    adv = _MeraAdvertisement(state_byte=0x00)
    try:
        await adv.register(bus, adapter_wrapper)
        print(f"Advertising: company=0x0001 article={_ARTICLE} UUID=0x3EA0")
    except Exception as e:
        print(f"Advertisement registration failed: {e}")

    # Track BLE connections via ObjectManager (best-effort)
    global _connected, _button_pressed
    try:
        intro = await bus.introspect("org.bluez", "/")
        proxy = bus.get_proxy_object("org.bluez", "/", intro)
        objmgr = proxy.get_interface("org.freedesktop.DBus.ObjectManager")

        def _on_added(path, ifaces):
            global _connected
            if "org.bluez.Device1" in ifaces:
                addr = ifaces["org.bluez.Device1"].get("Address", "?")
                if hasattr(addr, "value"):
                    addr = addr.value
                _connected = True
                _log("·", f"BLE client connected: {addr}")

        def _on_removed(path, ifaces):
            global _connected, _button_pressed
            if "org.bluez.Device1" in ifaces:
                _connected = False
                _button_pressed = False     # reset for next session
                _log("·", f"BLE client disconnected: {path}")

        objmgr.on_interfaces_added(_on_added)
        objmgr.on_interfaces_removed(_on_removed)
        print("Connection tracking active")
    except Exception as e:
        print(f"Warning: connection tracking unavailable: {e}")

    # aiohttp web server
    app = web.Application()
    app.router.add_get("/", _handle_root)
    app.router.add_post("/button", lambda r: _handle_button(r, service))
    app.router.add_get("/status", _handle_status)
    app.router.add_post("/clear-log", _handle_clear_log)

    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", web_port).start()

    print()
    print(f"--- Mera Comfort Mock Active ---")
    print(f"    mock: {_MOCK_VERSION}  script: {_SCRIPT_HASH}  bridge: {_BRIDGE_VERSION}")
    print(f"    SAP: {_SAP_NUMBER}  article: {_ARTICLE}")
    print(f"    Device Name (0x2a00): 'ro'")
    print(f"    Web UI: http://0.0.0.0:{web_port}/")
    print()

    await asyncio.get_event_loop().create_future()   # run forever


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="mock-geberit-mera.py — BLE peripheral mock for Geberit AquaClean Mera Comfort"
    )
    parser.add_argument("--port", type=int, default=8766, metavar="PORT",
                        help="Web UI port (default: 8766)")
    parser.add_argument("--version", action="version",
                        version=f"mock-geberit-mera {_MOCK_VERSION}")
    parsed = parser.parse_args()

    try:
        asyncio.run(main(web_port=parsed.port))
    except KeyboardInterrupt:
        print("\nMock stopped.")
