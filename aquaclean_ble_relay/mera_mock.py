"""
MeraMock — class-based BLE peripheral mock for Geberit AquaClean Mera Comfort.

Structural port of tools/mock-geberit-mera.py (v1.76.0b1) for mock_service.py's
multi-device orchestration (docs/developer/mock-service-requirements.md Phase 2).
tools/mock-geberit-mera.py is intentionally left untouched — its logic is
duplicated here for now, not shared, until a later phase decides the cutover
to a thin wrapper (requirements doc §2/§10 decision 2).

Scope of this port (2026-07-16, requirements doc §11 "Phase 2 — scope decision"):
  - Module-level globals -> instance attributes, so N MeraMock instances can
    coexist in one process without clobbering each other's state.
  - Per-instance logger (mock.mera.<adapter>) instead of one hardcoded
    "mera_mock" logger.
  - Adapter selection via the shared aquaclean_ble_relay.mock_bluez_adapter
    module instead of the script's own inline _find_adapter()/Adapter.get_first()
    pair — this is what makes the `adapter` constructor arg actually mean
    something (a *specific* adapter, not just "first found").
  - D-Bus GATT application paths and the auto-named log file are now tagged
    with the adapter name, and btmgmt/sysfs calls that used to hardcode hci0
    now derive the HCI index from `adapter` — all three would otherwise
    silently break (collide, or target the wrong adapter) the moment a second
    instance runs in the same process, which is the entire point of this class.
  - Everything else — protocol framing, procedure handlers, GATT service
    definitions, web UI, BLE connection-tracking workarounds — is a faithful
    behavioral port. No new mutation logic: the currently-stubbed Set*
    procedures (0x09, 0x08/0x14/0x15, 0x0B) remain the same no-op stubs.
    Persistence wiring is deferred to Phase 2b.

NOT tested against real BlueZ/D-Bus/hardware from this environment (no
bluez_peripheral/dbus_next available here — dev machine is out of BLE range
per memory/test-setup-live-ble.md). Verified by careful manual port + syntax
check only. Needs a real run on the mock VM before being trusted.
"""

import sys
import asyncio
import subprocess
import hashlib
import time
import json
import logging
from pathlib import Path

# ---- add project root so bridge modules are importable without pip install ----
_proj_root = Path(__file__).parent.parent
if str(_proj_root) not in sys.path:
    sys.path.insert(0, str(_proj_root))

# ---- bridge code uses custom log levels TRACE=5 / SILLY=1 ----
# Register them before any bridge import so Logger.trace()/silly() exist.
# Idempotent (hasattr-guarded) — harmless if tools/mock-geberit-mera.py already
# did this in the same process.


def _add_logging_level(level_name: str, level_num: int) -> None:
    method_name = level_name.lower()
    if not hasattr(logging, level_name):
        logging.addLevelName(level_num, level_name)
        setattr(logging, level_name, level_num)

    def _for_level(self, message, *args, **kwargs):
        if self.isEnabledFor(level_num):
            self._log(level_num, message, args, **kwargs)

    if not hasattr(logging.Logger, method_name):
        setattr(logging.Logger, method_name, _for_level)


_add_logging_level("TRACE", 5)
_add_logging_level("SILLY", 1)

# ---- import CrcMessage from bridge — avoids duplicating the proprietary CRC16 ----
from aquaclean_console_app.aquaclean_core.Message.CrcMessage                        import CrcMessage       # noqa: E402
from aquaclean_console_app.aquaclean_core.Frames.FrameFactory                       import FrameFactory     as _FrameFactory  # noqa: E402
from aquaclean_console_app.aquaclean_core.Frames.Frames.FrameType                  import FrameType        as _FrameType     # noqa: E402
from aquaclean_console_app.aquaclean_core.Frames.Frames.FlowControlFrame            import FlowControlFrame as _FlowControlFrame  # noqa: E402

from aquaclean_ble_relay.mock_bluez_adapter import select_adapter  # noqa: E402
from aquaclean_ble_relay import mock_persistence  # noqa: E402

_BLEMSG_ID_CRC_RSP = 5   # matches Message.BLEMSG_ID_CRC_RSP

# ---- version ----
_MOCK_VERSION = "1.76.0b1"
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
# Protocol constants: identical for every MeraMock instance, so these stay
# module-level rather than becoming per-instance state.
_SVC_UUID       = "3334429d-90f3-4c41-a02d-5cb3a03e0000"
_WRITE_0_UUID   = "3334429d-90f3-4c41-a02d-5cb3a13e0000"   # handle 0x0003 (requests)
_WRITE_1_UUID   = "3334429d-90f3-4c41-a02d-5cb3a23e0000"   # handle 0x0006 (FIRST continuation)
_WRITE_2_UUID   = "3334429d-90f3-4c41-a02d-5cb3a33e0000"   # A3 (cy[2] in AquaCleanProduct.cs)
_WRITE_3_UUID   = "3334429d-90f3-4c41-a02d-5cb3a43e0000"   # A4 (cy[3] in AquaCleanProduct.cs)
_NOTIFY_A5_UUID = "3334429d-90f3-4c41-a02d-5cb3a53e0000"   # handle 0x000F (primary response)
_NOTIFY_A6_UUID = "3334429d-90f3-4c41-a02d-5cb3a63e0000"   # handle 0x0013
_NOTIFY_A7_UUID = "3334429d-90f3-4c41-a02d-5cb3a73e0000"   # handle 0x0017
_NOTIFY_A8_UUID = "3334429d-90f3-4c41-a02d-5cb3a83e0000"   # handle 0x001B

# InfoFrame payload — sent on A5 (for bridge wait_for_info_frames_async threshold=10)
# AND on A6 (for iOS ConnectionState.Ready check in GeberitDeviceCoreService.Connect()).
# Real device: 9x on A6 after CCCD-A7 enable (nRF capture iOS v2.14.1, real Mera Comfort).
_A6_INFO_FRAME = bytes.fromhex("800130140c030003000000003130001200b70800")
_READ_UUID      = "3a2b"   # handle 0x0020 (button-state, 16-bit UUID 0x3A2B — short form required for BlueZ Read By Type match)

# Node IDs confirmed from real Mera onboarding capture
_NODE_IDS = bytes([3, 4, 5, 6, 7, 8, 9, 0xa, 0xb, 0xc, 0xe, 0xf])

# iOS sends [0..11] (12 indices). Real Mera returns all 12; indices 8-11
# return 0 (device-variant specific but safe — confirmed nRF capture 2026-06-26).
_SPL_MERA_INDICES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]

# ---- Procedure names (for progress log) ----
_PROC_NAMES: dict = {
    0x05: "GetNodeInventory",
    0x07: "GetPerNodeProfileSetting",
    0x08: "SetActiveProfileSetting",
    0x09: "SetCommand",
    0x0A: "GetActiveCommonSetting",
    0x0B: "SetActiveCommonSetting",
    0x0D: "GetSystemParameterList",
    0x0E: "GetFirmwareVersionList",
    0x11: "SubscribeNotif_0x11",
    0x13: "SubscribeNotif_0x13",
    0x14: "SubscribeNotif_0x14",
    0x15: "SubscribeNotif_0x15",
    0x45: "GetStatisticsDescale",
    0x51: "GetStoredCommonSetting",
    0x52: "SetStoredCommonSetting",
    0x53: "GetStoredProfileSetting",
    0x54: "SetStoredProfileSetting",
    0x55: "GetDeviceRegistrationLevel",
    0x59: "GetFilterStatus",
    0x81: "GetSOCApplicationVersions",
    0x82: "GetDeviceIdentification",
    0x86: "GetDeviceInitialOperationDate",
}

# Per-component firmware versions — real post-update values from a genuine
# RS28.0->RS30.0 Mera Comfort update, captured 2026-07-14
# (local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/).
# Format per record: (v1, v2, build) where version=chr(v1)+chr(v2), build=int.
# Read-only protocol data (not mutated anywhere) — stays module-level.
_FW_COMPONENT_VERSIONS = {
    1:  (0x33, 0x30, 0xCE),  # RS30.0 TS206 — Steuerung (main controller) — updated by the real device
    3:  (0x30, 0x38, 0x1F),  # RS08.0 TS31  — Geruchsabsaugung
    4:  (0x30, 0x38, 0x25),  # RS08.0 TS37  — Duscheinheit
    5:  (0x31, 0x31, 0x3C),  # RS11.0 TS60  — Deckelheber
    6:  (0x30, 0x38, 0x30),  # RS08.0 TS48  — Föhnmodul
    7:  (0x31, 0x31, 0x29),  # RS11.0 TS41  — WW-Bereitung
    8:  (0x30, 0x39, 0x1F),  # RS09.0 TS31  — WC-Sitz-Heizung
    9:  (0x30, 0x37, 0x13),  # RS07.0 TS19  — Bedienfeld
    10: (0x30, 0x37, 0x12),  # RS07.0 TS18  — Benutzererkennung
    11: (0x30, 0x38, 0x17),  # RS08.0 TS23  — Bewegungserkennung — updated by the real device (was RS07.0 TS22)
    12: (0x30, 0x37, 0x12),  # RS07.0 TS18  — Orientierungslicht
    14: (0x30, 0x37, 0x1B),  # RS07.0 TS27  — Föhneinheit
    15: (0x30, 0x31, 0x00),  # RS01.0 TS0   — Schnittstellenmodul
}

# ---- Advertisement D-Bus path (bluez_peripheral default, used for unregister) ----
_ADVERT_PATH = "/com/spacecheese/bluez_peripheral/advert0"


def _parse_request(frame: bytes):
    """Parse 20-byte request frame -> (ctx, proc, args). Pure function, no
    instance state — kept module-level (identical to the original script).

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


def _build_frames(ctx: int, proc: int, result: bytes, status: int = 0, node_id: int = 0x01) -> list:
    """Build ATT notify frames matching the real Mera Comfort wire format.
    Pure function, no instance state — kept module-level.

    CrcMessage body: [status, node_id, ctx, proc, result_len, ...result]
    node_id defaults to 0x01 (real device default); proc 0x07 echoes the queried node_id.
    CrcMessage header: id=5, seg=0x00 (real device value, not 0xFF)

    Two formats selected by content_len = 6 (CrcMsg header) + 5 (body prefix) + len(result):

    Legacy SINGLE-type (bits[6:5]=00), 19-byte payload, max 4 frames (n_cons <= 3):
      Used when content_len <= 76 bytes (<= 4 x 19).
      FIRST header: 0x11 | (n_cons << 1)     e.g. 0x13 for n_cons=1
      CONS  header: 0x10 | (frame_index << 1) e.g. 0x12 for first CONS

    Extended FIRST+CONS (bits[6:5]=01/10), 18-byte payload:
      Used when content_len > 76 bytes (proc 0x82 = 93 bytes -> 6 frames).
      FIRST:  [0x30, total_frame_count] + 18 bytes payload
      CONS i: [byte1, frame_index]      + 18 bytes payload
        byte1 = 0x40 | window_flag | channel_bits
        channel_bits: A6=0x02, A7=0x04, A8=0x06, A5=0x00 (rotation for CONS slots)
        window_flag:  0x10 for CONS frames 1-3 (first FC window), 0x00 thereafter

    Characteristic routing (both formats):
      frame 0 -> A5,  frame 1 -> A6,  frame 2 -> A7,  frame 3 -> A8,
      frame 4 -> A5,  frame 5 -> A6,  ...  (rotation mod 4)

    FlowControl bitmask (from iOS): bit i set = frame i received (bit 0 = FIRST frame).
    All-acked expected value for n total frames: (1 << n) - 1.
    """
    body = bytearray(5 + len(result))
    body[0] = status
    body[1] = node_id
    body[2] = ctx
    body[3] = proc
    body[4] = len(result) & 0xFF
    body[5:] = result

    crc_msg = CrcMessage.create(_BLEMSG_ID_CRC_RSP, 0x00, body)  # seg=0x00
    serialized = bytes(crc_msg.serialize())   # 262-byte buffer; extra zeros ignored by receiver
    content_len = 6 + len(body)

    _LEGACY_MAX = 4 * 19   # 76 bytes — max content for legacy format (SubFrameCount fits 2 bits)

    if content_len <= _LEGACY_MAX:
        # Legacy SINGLE-type: 1-byte header, 19-byte payload
        _P = 19
        n_frames = (content_len + _P - 1) // _P
        n_cons = n_frames - 1
        frames = []
        for i in range(n_frames):
            chunk = serialized[i * _P: (i + 1) * _P]
            chunk = bytes(chunk) + bytes(_P - len(chunk))   # pad last chunk
            if i == 0:
                hdr = bytes([0x11 | (n_cons << 1)])
            else:
                hdr = bytes([0x10 | (i << 1)])
            frames.append(hdr + chunk)
    else:
        # Extended FIRST+CONS: 2-byte header, 18-byte payload
        _P = 18
        n_frames = (content_len + _P - 1) // _P
        # CONS byte1 channel rotation: CONS slot 0->A6, 1->A7, 2->A8, 3->A5, 4->A6, ...
        _CHAN = [0x02, 0x04, 0x06, 0x00]   # A6, A7, A8, A5
        frames = []
        for i in range(n_frames):
            chunk = serialized[i * _P: (i + 1) * _P]
            chunk = bytes(chunk) + bytes(_P - len(chunk))   # pad last chunk
            if i == 0:
                hdr = bytes([0x30, n_frames])
            else:
                ch = _CHAN[(i - 1) % 4]
                wf = 0x10 if i <= 3 else 0x00   # window flag: set for first 3 CONS
                hdr = bytes([0x40 | wf | ch, i])
            frames.append(hdr + chunk)
    return frames


# ---- GATT Service ----
class MeraService(Service):
    """Geberit AquaClean Mera Comfort GATT service.

    Write characteristics (A1-A4) accept 20-byte request frames.
    The app rotates across cy[channelId % 4] = A1/A2/A3/A4; all four
    must be present or the app throws "Bulk transfer characteristic missing"
    and shows "connection could not be established" without writing any CCC.
    A5 notify delivers single-frame responses and the InfoFrame burst (for bridge).
    A6 delivers the InfoFrame burst (iOS ConnectionState.Ready check); A6/A7/A8
    also deliver continuation frames for multi-frame responses.

    Holds a back-reference to the owning MeraMock instance (`mock`) — bluez_peripheral's
    Service base class instantiates with fixed args, so instance state/logging/dispatch
    live on the mock, not on this GATT wrapper.
    """

    def __init__(self, mock: "MeraMock"):
        super().__init__(_SVC_UUID, True)
        self._mock = mock
        self._notify_value = bytes(20)
        self._notify_iface = None         # wired after register() via wire_notify()
        self._notify_a6_iface = None      # wired after register() via wire_notify_a6()
        self._notify_a7_iface = None      # wired after register() via wire_notify_a7()
        self._notify_a8_iface = None      # wired after register() via wire_notify_a8()
        self._last_a5_frames: list = []   # last response frames; used for FlowControl retransmit
        self._last_a5_proc: int = 0       # proc code of last multi-frame response (for progress log)
        self._retransmit_count: int = 0   # retransmits for current transaction; reset on new proc
        self._request_lock = asyncio.Lock()   # serialise _handle_request — prevents concurrent frame interleave
        self._a6_burst_done: asyncio.Event = asyncio.Event()
        self._a6_burst_done.set()         # no burst in progress initially

    def wire_notify(self, iface) -> None:
        self._notify_iface = iface

    def wire_notify_a6(self, iface) -> None:
        self._notify_a6_iface = iface

    def wire_notify_a7(self, iface) -> None:
        self._notify_a7_iface = iface

    def wire_notify_a8(self, iface) -> None:
        self._notify_a8_iface = iface

    @dbus_property(PropertyAccess.READ)
    def Includes(self) -> "ao":  # type: ignore
        # bluez_peripheral 0.1.7 bug: base class unconditionally appended self._path,
        # creating a self-include declaration that displaces A6-A8/A1/A2 char declarations.
        return []

    async def push_notify(self, frame: bytes) -> None:
        """Send an ATT notification on A5."""
        self._mock._log("→", f"NOTIFY A5 ({len(frame)}B): {frame.hex()}")
        self._notify_value = frame
        if self._notify_iface is None:
            self._mock._log("·", "WARNING: notify interface not wired — cannot push frame")
            return
        try:
            if hasattr(self._notify_iface, "changed"):
                self._notify_iface.changed(frame)
            else:
                self._notify_iface.emit_properties_changed(
                    {"Value": Variant("ay", list(frame))}
                )
        except Exception as e:
            self._mock._log("·", f"WARNING: push_notify failed: {e}")

    async def push_notify_a6(self, frame: bytes) -> None:
        """Send an ATT notification on A6."""
        self._mock._log("→", f"NOTIFY A6 ({len(frame)}B): {frame.hex()}")
        if self._notify_a6_iface is None:
            self._mock._log("·", "WARNING: A6 notify interface not wired — cannot push frame")
            return
        try:
            if hasattr(self._notify_a6_iface, "changed"):
                self._notify_a6_iface.changed(frame)
            else:
                self._notify_a6_iface.emit_properties_changed(
                    {"Value": Variant("ay", list(frame))}
                )
        except Exception as e:
            self._mock._log("·", f"WARNING: push_notify_a6 failed: {e}")

    async def push_notify_a7(self, frame: bytes) -> None:
        """Send an ATT notification on A7."""
        self._mock._log("→", f"NOTIFY A7 ({len(frame)}B): {frame.hex()}")
        if self._notify_a7_iface is None:
            self._mock._log("·", "WARNING: A7 notify interface not wired — cannot push frame")
            return
        try:
            if hasattr(self._notify_a7_iface, "changed"):
                self._notify_a7_iface.changed(frame)
            else:
                self._notify_a7_iface.emit_properties_changed(
                    {"Value": Variant("ay", list(frame))}
                )
        except Exception as e:
            self._mock._log("·", f"WARNING: push_notify_a7 failed: {e}")

    async def push_notify_a8(self, frame: bytes) -> None:
        """Send an ATT notification on A8."""
        self._mock._log("→", f"NOTIFY A8 ({len(frame)}B): {frame.hex()}")
        if self._notify_a8_iface is None:
            self._mock._log("·", "WARNING: A8 notify interface not wired — cannot push frame")
            return
        try:
            if hasattr(self._notify_a8_iface, "changed"):
                self._notify_a8_iface.changed(frame)
            else:
                self._notify_a8_iface.emit_properties_changed(
                    {"Value": Variant("ay", list(frame))}
                )
        except Exception as e:
            self._mock._log("·", f"WARNING: push_notify_a8 failed: {e}")

    def _push_method(self, frame_index: int):
        """Return push_notify method for frame i: rotates A5->A6->A7->A8->A5->..."""
        return [self.push_notify, self.push_notify_a6,
                self.push_notify_a7, self.push_notify_a8][frame_index % 4]

    async def _handle_request(self, raw: bytes) -> None:
        mock = self._mock
        async with self._request_lock:
            if len(raw) < 11:
                mock._log("·", f"frame too short ({len(raw)} B) — ignored")
                return
            hdr = raw[0]
            ft = _FrameFactory.getFrameTypeFromHeaderByte(hdr)

            if ft == _FrameType.CONTROL:
                # FlowControlFrame — app reports which response frames it received.
                # Bitmask bit i=1 -> frame i received (bit 0 = FIRST, bit 1 = CONS[0], ...).
                # Expected all-acked value for n total frames: (1 << n) - 1.
                fc = _FlowControlFrame.create_flow_control_frame(raw)
                ack = fc.AckdFrameBitmask[0]
                n = len(self._last_a5_frames)
                if n == 0:
                    mock._log("·", f"FlowControl: no pending frames (bitmask=0x{ack:02x})")
                    return
                expected = (1 << n) - 1
                if ack == expected:
                    name = _PROC_NAMES.get(self._last_a5_proc, f"0x{self._last_a5_proc:02X}")
                    mock._log("✅", f"{name} ({n} frames all ACKed)")
                    self._last_a5_frames = []
                    self._retransmit_count = 0
                    return
                self._retransmit_count += 1
                if self._retransmit_count > 3:
                    mock._log("!", f"FlowControl: giving up after {self._retransmit_count - 1} retransmit(s) — app will retry proc")
                    self._last_a5_frames = []
                    self._retransmit_count = 0
                    return
                missing = [i for i in range(n) if not (ack >> i) & 1]
                mock._log("!", f"FlowControl: bitmask=0x{ack:02x} (expected 0x{expected:02x}) — "
                                f"retransmit #{self._retransmit_count} of frame(s) {missing}")
                await asyncio.sleep(0.2)   # drain ATT queue before retransmit
                for i in missing:
                    await self._push_method(i)(self._last_a5_frames[i])
                    await asyncio.sleep(0.01)
                return

            if ft == _FrameType.SINGLE:
                if hdr & 0x01:
                    # SINGLE (SubFrameCount=0) or FIRST[N] (N>0) — new request
                    # Wait for any in-progress A6 burst to finish; prevents ATT congestion
                    # that causes iOS to drop A5 frames and send a partial FlowControl ACK.
                    try:
                        await asyncio.wait_for(self._a6_burst_done.wait(), timeout=3.0)
                    except asyncio.TimeoutError:
                        mock._log("·", "A6 burst wait timed out — sending A5 response anyway")
                    ctx, proc, args = _parse_request(raw)
                    mock._log("←", f"proc 0x{proc:02X}  ctx={ctx}  args={args.hex() if args else '(none)'}")
                    # Connection 2 (Save) runs on the SAME BLE connection as Connection 1.
                    # iOS re-subscribes CCCDs but BlueZ omits the external callback when
                    # the value is unchanged -> no new burst from _on_device_connected.
                    # Fire A6 burst whenever proc 0x82 arrives and the initial burst is done.
                    if proc == 0x82 and self._a6_burst_done.is_set():
                        a6 = self._notify_a6_iface
                        if a6 is not None and a6._notify:
                            asyncio.ensure_future(mock._send_a6_reconnect_burst(self, mock._connection_gen))
                    frames = mock._dispatch(ctx, proc, args)
                    self._last_a5_frames = frames   # store for potential FlowControl retransmit
                    self._last_a5_proc = proc
                    self._retransmit_count = 0
                    for i, frame in enumerate(frames):
                        if i:
                            await asyncio.sleep(0.012)  # 12ms > CI(10ms): each frame its own CE
                        await self._push_method(i)(frame)
                    if len(frames) == 1:
                        name = _PROC_NAMES.get(proc, f"0x{proc:02X}")
                        mock._log("✅", f"{name}")
                else:
                    # CONS continuation frame (bit 0 = 0) — multi-frame request not yet assembled
                    mock._log("·", f"CONS frame received (multi-frame request not yet assembled): {raw[:4].hex()}")

    @characteristic(_WRITE_0_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_0(self, options):
        return bytes(20)

    @write_0.setter
    def write_0(self, value, options):
        raw = bytes(value)
        self._mock._log("←", f"WRITE_0 ({len(raw)}B): {raw.hex()}")
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_1_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_1(self, options):
        return bytes(20)

    @write_1.setter
    def write_1(self, value, options):
        raw = bytes(value)
        self._mock._log("←", f"WRITE_1 ({len(raw)}B): {raw.hex()}")
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_2_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_2(self, options):
        return bytes(20)

    @write_2.setter
    def write_2(self, value, options):
        raw = bytes(value)
        self._mock._log("←", f"WRITE_2 ({len(raw)}B): {raw.hex()}")
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_3_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_3(self, options):
        return bytes(20)

    @write_3.setter
    def write_3(self, value, options):
        raw = bytes(value)
        self._mock._log("←", f"WRITE_3 ({len(raw)}B): {raw.hex()}")
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


class _DISService(Service):
    """Device Information Service (0x180A) for Remote Control compatibility.

    RC does FIND_BY_TYPE_VALUE UUID=0x180A before pairing, then reads the
    Manufacturer Name String (0x2A29) characteristic.  Real Mera returns
    b"3.60.101.860/0000\\x00" (17 bytes, from nRF52840 capture).
    """

    def __init__(self):
        super().__init__("0000180a-0000-1000-8000-00805f9b34fb", True)

    @characteristic("00002a29-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def manufacturer_name(self, options):
        return b"3.60.101.860/0000\x00"


class _RCPairingService(Service):
    """Remote Control pairing service stub (UUID 0xC526).

    RC does FIND_BY_TYPE_VALUE UUID=0xC526 and verifies the service exists
    before initiating BLE pairing (LL_ENC_REQ).  Contents beyond the service
    declaration are unknown — stub characteristic returns empty bytes.
    All post-pairing RC traffic is encrypted and not yet decoded.
    """

    def __init__(self):
        super().__init__("0000c526-0000-1000-8000-00805f9b34fb", True)

    @characteristic("0000c527-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def rc_stub(self, options):
        return b""


# ---- Advertisement ----
class _MeraAdvertisement(Advertisement):
    """Advertisement matching the real Mera Comfort BLE payload (11-byte total).

    BlueZ exposes manufacturer data as (company_id, payload). The iOS app receives the
    full manufacturer-specific data INCLUDING the 2-byte company ID, so byte offsets
    in AquaCleanProduct.cs are counted from the company ID:

      full_data[0]   company ID low  0x00  (Geberit 0x0100) | 0xAA = IsEmergencyConnectPermitted
      full_data[1]   company ID high 0x01
      full_data[2]   payload[0]      state_b  0x00 idle | 0x01 = IsButtonPressed <- iOS reads THIS
      full_data[3-7] payload[1-5]    article  5-char ASCII (e.g. "14621") -> model detection
      full_data[8]   payload[6]      0x00
      full_data[9-10]payload[7-8]    RS fw prefix "30"

    Total: 2 (company ID) + 9 (payload) = 11 bytes — the "11-byte variant" in ble-protocol.md.

    AquaCleanProduct.cs UpdateAdvertisingData():
      IsButtonPressed             = (full_data[2] == 1)    <- payload[0] = state_b
      IsEmergencyConnectPermitted = (full_data[0] == 0xAA) <- company ID low byte

    The iOS 15-second scan loop selects a device only when IsButtonPressed=True.
    _update_advert(1) sets state_b=0x01 -> full_data[2]=0x01 -> triggers Connection 1.

    `article` used to be a module-level constant; now a constructor arg so each
    MeraMock instance can (eventually) advertise its own identity.
    """

    def __init__(self, article: str, state_b: int = 0):
        super().__init__(
            "Geberit AC PRO",                            # name -> SCAN_RSP (BlueZ splits automatically)
            ["00003ea0-0000-1000-8000-00805f9b34fb"],    # service_uuids -> ADV_IND
            appearance=0,
            timeout=0,
            manufacturerData={
                0x0100: bytes([state_b]) + article.encode("ascii") + bytes([0x00]) + b"30"
            },
        )


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


class MeraMock:
    """One Mera Comfort BLE peripheral mock instance.

    Everything that used to be a module-level global in tools/mock-geberit-mera.py
    (session log, button/registration/connection state, advertisement handles,
    the D-Bus connection) is now an instance attribute, so multiple MeraMock
    instances can run concurrently in one process (mock_service.py's whole point).

    `adapter`: BlueZ node name (e.g. "hci1") to bind to, or None for "first found"
    (same default as the original script). Threaded through to:
      - GATT/advertisement registration (via mock_bluez_adapter.select_adapter)
      - the per-instance logger name and log filename
      - btmgmt/sysfs calls that used to hardcode hci0
      - D-Bus GATT application object paths (so two instances don't collide)
    """

    def __init__(self, adapter: str | None = None, web_port: int = 8765, state_dir=None):
        self.adapter = adapter
        self.web_port = web_port
        self._adapter_tag = adapter or "default"

        if state_dir is not None:
            # Process-wide: all mock instances share one DB file, isolated by
            # (device_type, device_key) rows — see mock_persistence.py. Setting
            # this per-instance is harmless as long as only one state_dir is
            # ever configured per process, which is the only case that exists
            # today (mock_service.py's orchestrator, Phase 4, will set this
            # once for the whole process instead).
            mock_persistence.set_state_dir(state_dir)

        # ---- identity (was module-level constants; instance now so a future
        # variant/model registry can override per instance without touching
        # this class again) ----
        self._ARTICLE      = "14621"          # BLE advertisement article prefix (model lookup)
        self._ARTICLE_FULL = "146.21x.xx.1"  # proc 0x82 ArticleNumber field: 12-char fixed-width
        self._SAP_NUMBER      = "HB2300EU000001"
        self._SERIAL          = "HB2300EU000001"
        self._PRODUCTION_DATE = "11.04.2023"  # real device format: DD.MM.YYYY
        self._DESCRIPTION     = "AquaClean Mera Comfort"
        self._VARIANT     = 0x0D   # Mera Comfort

        # SPL values for indices that need non-zero defaults (none currently needed).
        self._SPL_MERA_VALUES: dict = {}

        # Values from real Mera Comfort onboarding capture (onboarding-real-mera_timing.md).
        # Hardcoded real-device defaults — overridden below by anything already
        # persisted for this device (startup never overwrites an existing store,
        # requirements doc §5). ACTIVE_PROFILE_SETTINGS is the write-target for
        # proc 0x08 (SetActiveProfileSetting) — session-only, never persisted,
        # same as ACTIVE_COMMON_SETTINGS below; it has no confirmed getter of its
        # own so it's never read back, only written.
        self._ACTIVE_PROFILE_SETTINGS  = {0: 1, 1: 3, 2: 2, 3: 2, 4: 2, 5: 0, 6: 1, 7: 1, 8: 0, 9: 0}
        self._STORED_PROFILE_SETTINGS  = {0: 1, 1: 3, 2: 2, 3: 2, 4: 2, 5: 0, 6: 1, 7: 1, 8: 0, 9: 0}
        self._STORED_COMMON_SETTINGS   = {0: 1, 1: 3, 2: 2, 3: 2, 4: 2, 5: 0, 6: 1, 7: 1, 8: 0, 9: 0}
        self._PER_NODE_PROFILE_SETTINGS = {
            0x00: 1, 0x01: 1, 0x02: 2, 0x03: 1, 0x04: 2,
            0x05: 1, 0x06: 4, 0x07: 0, 0x08: 3, 0x09: 1, 0x0d: 1,
        }

        # Persisted values win over the hardcoded defaults above — this is what
        # makes settings survive a mock restart (requirements doc §0/§5).
        persisted = mock_persistence.load_all("mera", self._adapter_tag)
        for key, value in persisted.items():
            namespace, _, idx_str = key.partition(":")
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            if namespace == "common_setting":
                self._STORED_COMMON_SETTINGS[idx] = value
            elif namespace == "profile_setting":
                self._STORED_PROFILE_SETTINGS[idx] = value

        # Active CommonSetting store (proc 0x0A/0x0B) — session-scoped, seeded
        # from Stored (including any persisted overrides just applied above) at
        # mock startup, held only in memory thereafter, never persisted. Mirrors
        # the real device: a power-cycle re-derives Active from Stored NVM.
        # Simplification: seeded once here, not re-seeded per BLE session.
        self._ACTIVE_COMMON_SETTINGS = dict(self._STORED_COMMON_SETTINGS)

        # ---- mutable session/connection state (was module-level globals) ----
        self._session_log: list = []
        self._button_pressed = False
        self._registration_level: int = 0   # 0=Not registered, 1=Private, 2=Public — real device returns 0 during onboarding
        self._connected = False
        self._connection_gen = 0     # incremented on each new connection; guards stale burst tasks
        self._current_device_path = None  # D-Bus path of the currently connected device
        self._advert = None          # current _MeraAdvertisement instance
        self._advert_bus = None      # D-Bus connection stored for advert updates
        self._advert_adapter = None  # Adapter stored for advert updates
        self._advert_lock: asyncio.Lock | None = None  # created in run(), needs a running loop
        self._bus = None             # system D-Bus connection; set in run()

        # ---- per-instance logger (was one hardcoded logging.getLogger("mera_mock")) ----
        self.logger = logging.getLogger(f"mock.mera.{self._adapter_tag}")
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False   # don't bubble to root logger
        self._log_fmt = logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")
        if not self.logger.handlers:
            console_h = logging.StreamHandler(sys.stdout)
            console_h.setFormatter(self._log_fmt)
            self.logger.addHandler(console_h)

    def _hci_index(self) -> str:
        """BlueZ node name (e.g. "hci1") -> HCI index string ("1") for btmgmt/sysfs
        calls that address an adapter by index rather than by D-Bus path. Defaults
        to "0" when no specific adapter was requested, matching the original
        script's unconditional hci0 (which only ever ran a single instance)."""
        if self.adapter and self.adapter.startswith("hci") and self.adapter[3:].isdigit():
            return self.adapter[3:]
        return "0"

    def _log(self, direction: str, msg: str) -> None:
        entry = (time.strftime("%H:%M:%S"), direction, msg)
        self._session_log.append(entry)
        if len(self._session_log) > 200:
            self._session_log.pop(0)
        self.logger.info("  %s %s", direction, msg)

    async def _update_advert(self, state_b: int) -> None:
        """Unregister current advertisement and re-register with updated IsButtonPressed flag.

        state_b=0x01 -> iOS scan sees IsButtonPressed=True -> device selected for Connection 1.
        state_b=0x00 -> normal idle (button not pressed).

        bluez_peripheral.Advertisement has no unregister() method.
        We call LEAdvertisingManager1.UnregisterAdvertisement() directly via the cached
        adapter proxy, using the known fixed D-Bus path _ADVERT_PATH.
        """
        if self._advert_bus is None or self._advert_adapter is None:
            self.logger.warning("_update_advert: bus/adapter not initialised")
            return
        async with self._advert_lock:
            try:
                mgr = self._advert_adapter._proxy.get_interface("org.bluez.LEAdvertisingManager1")
                await mgr.call_unregister_advertisement(_ADVERT_PATH)
            except Exception as e:
                self.logger.warning("advert unregister: %s", e)
            self._advert = _MeraAdvertisement(self._ARTICLE, state_b)
            try:
                await self._advert.register(self._advert_bus, self._advert_adapter)
                self._log("·", f"Advertisement updated: byte[2]=0x{state_b:02X}  IsButtonPressed={bool(state_b)}")
            except Exception as e:
                self.logger.error("advert re-register failed: %s", e)

    # ---- Procedure dispatch ----

    def _set_registration_level(self, level: int) -> None:
        self._registration_level = level

    def _dispatch(self, ctx: int, proc: int, args: bytes) -> list:
        """Return list of 20-byte frames for the response to proc."""
        self._log("←", f"proc=0x{proc:02X} ctx={ctx} args={args.hex() if args else '(none)'}")

        response_node_id = 0x01

        if proc == 0x82:              # GetDeviceIdentification
            result = self._proc_82()
        elif proc == 0x05:            # GetNodeInventory
            result = self._proc_05()
        elif proc == 0x81:            # GetSOCApplicationVersions
            result = self._proc_81()
        elif proc == 0x0E:            # GetFirmwareVersionList
            result = self._proc_0e(args)
        elif proc == 0x86:            # GetDeviceInitialOperationDate
            result = self._proc_86()
        elif proc == 0x0D:            # GetSystemParameterList
            result = self._proc_0d(args)
        elif proc == 0x09:            # SetCommand (1-byte command code)
            result = self._proc_09(args)
        elif proc in (0x11, 0x13):
            result = self._proc_subscribenotif(proc, args)
        elif proc in (0x14, 0x15):    # SubscribeNotif variants — no confirmed distinct behavior
            result = b""
        elif proc == 0x08:            # SetActiveProfileSetting — confirmed format [count=3, setting_id, value]
            result = self._proc_08(args)
        elif proc == 0x07:            # GetPerNodeProfileSetting — echo queried node_id
            response_node_id = args[0] if args else 0x01
            result = self._proc_07(args)
        elif proc == 0x0A:            # GetActiveCommonSetting (fixed in Phase 2b — was misreading ProfileSettings)
            result = self._proc_0a(args)
        elif proc == 0x0B:            # SetActiveCommonSetting — session-only, not persisted
            result = self._proc_0b(args)
        elif proc == 0x51:            # GetStoredCommonSetting
            result = self._proc_51(args)
        elif proc == 0x52:            # SetStoredCommonSetting — persisted
            result = self._proc_52(args)
        elif proc == 0x53:            # GetStoredProfileSetting
            result = self._proc_53(args)
        elif proc == 0x54:            # SetStoredProfileSetting — persisted
            result = self._proc_54(args)
        elif proc == 0x55:            # GetDeviceRegistrationLevel
            result = bytes([self._registration_level])
        elif proc == 0x56:            # SetDeviceRegistrationLevel
            self._set_registration_level(args[0] if args else 1)
            result = b""
        elif proc == 0x45:            # GetStatisticsDescale
            result = self._proc_45()
        elif proc == 0x59:            # GetFilterStatus
            result = self._proc_59()
        else:
            self._log("·", f"  unknown proc 0x{proc:02X} — returning empty OK")
            result = b""

        frames = _build_frames(ctx, proc, result, node_id=response_node_id)
        for f in frames:
            self._log("→", f"  {f.hex()}")
        return frames

    # ---- Procedure result builders ----
    def _proc_82(self) -> bytes:
        """GetDeviceIdentification: 82-byte fixed-width payload.

        AcDeviceIdentification requires exactly 82 bytes (null-padded, no leading variant byte):
          ArticleNumber[12] + SerialNumber[20] + ProductionDate[10] + Description[40]
        """
        def _pad(s: str, n: int) -> bytes:
            b = s.encode("ascii")[:n]
            return b + bytes(n - len(b))
        return (
            _pad(self._ARTICLE_FULL, 12)      # ArticleNumber  offset  0
            + _pad(self._SAP_NUMBER, 20)      # SerialNumber   offset 12
            + _pad(self._PRODUCTION_DATE, 10) # ProductionDate offset 32
            + _pad(self._DESCRIPTION, 40)     # Description    offset 42
        )                                     # total = 82 bytes

    def _proc_05(self) -> bytes:
        """GetNodeInventory: count(1) + node IDs + zero-pad to 129 bytes total."""
        payload = bytes([len(_NODE_IDS)]) + _NODE_IDS
        return payload + bytes(129 - len(payload))

    def _proc_81(self) -> bytes:
        """GetSOCApplicationVersions: major_str(2) + minor_byte + null = 4 bytes.
        Real device sends "10.18" as b"10" + 0x12 + 0x00.
        """
        return b"10\x12\x00"

    def _proc_0d(self, args: bytes) -> bytes:
        """GetSystemParameterList: count(1) + count x (index(1)+value_le(4)).

        iOS sends [0..11] (12 indices). Real Mera returns all 12 with zeros for 8-11
        (confirmed nRF52840 capture 2026-06-26 — no corruption). Mock mirrors this.
        Including index bytes is mandatory — iOS maps each value by its index field, not position.
        """
        result = bytes([len(_SPL_MERA_INDICES)])
        for idx in _SPL_MERA_INDICES:
            val = self._SPL_MERA_VALUES.get(idx, 0)
            result += bytes([idx]) + val.to_bytes(4, "little")
        return result

    def _proc_45(self) -> bytes:
        """GetStatisticsDescale: 16-byte StatisticsDescale struct.

        Wire format (little-endian):
          unposted_shower_cycles(1) + days_until_next_descale(2) +
          days_until_shower_restricted(2) + shower_cycles_until_confirmation(1) +
          date_time_at_last_descale(4) + date_time_at_last_descale_prompt(4) +
          number_of_descale_cycles(2)

        Simulates a device last descaled 3 weeks ago; next descaling due in ~69 days.
        """
        last_descale = int(time.time()) - 21 * 24 * 3600   # 3 weeks ago (Unix timestamp)
        result  = bytes([12])                               # unposted_shower_cycles
        result += (69).to_bytes(2, "little")                # days_until_next_descale
        result += (76).to_bytes(2, "little")                # days_until_shower_restricted
        result += bytes([20])                               # shower_cycles_until_confirmation
        result += last_descale.to_bytes(4, "little")        # date_time_at_last_descale
        result += last_descale.to_bytes(4, "little")        # date_time_at_last_descale_prompt
        result += (3).to_bytes(2, "little")                 # number_of_descale_cycles
        return result

    def _proc_59(self) -> bytes:
        """GetFilterStatus: a_byte(1) + a_byte x (id(1)+value_le(4)).

        iOS requests IDs [0..11] via FIRST+CONS. Real Mera responds with a_byte=11
        (11 valid records, IDs 0-10) plus one zero-padded slot — ID 11 is not a real
        entry (confirmed onboarding-real-mera.md). Mock mirrors this exactly.

        Values matched to real Mera Comfort capture (onboarding-real-mera.md 2026-06-26):
        id=4 and id=8 are Unix timestamps (TimestampAtLastFilterChange /
        TimestampAtLastFilterChangePrompt). Returning 0 for these while id=7 and
        id=10 are non-zero triggers the "Fehler / Ein Fehler ist aufgetreten" popup —
        the app detects an inconsistency (filter has been changed but no date recorded).
        id=7=348: filter changed 17 days ago (365-348).
        id=10=5: 5 filter changes total.
        """
        last_change = int(time.time()) - 17 * 24 * 3600  # 17 days ago, matching 365-348
        items = [
            (0, 1), (1, 130), (2, 14), (3, 1),
            (4, last_change), (5, 0), (6, 3),
            (7, 348),  # DaysUntilNextFilterChange
            (8, last_change), (9, 0), (10, 5),
        ]
        result = bytes([len(items)])
        for id_, val in items:
            result += bytes([id_]) + val.to_bytes(4, "little")
        return result

    def _proc_subscribenotif(self, proc: int, args: bytes) -> bytes:
        """SubscribeNotif 0x11/0x13: count(1) + count x (node_id(1)+data(12)).

        iOS batches node IDs 4 at a time; args = count(1) + node_ids(count).
        0x11: 12-byte ASCII firmware version per node (all same version string).
        0x13: 12 zero bytes per node (profile settings); node 5 has byte[6]=0x04.
        """
        n = args[0] if args else 0
        nodes = list(args[1:1 + n])
        result = bytes([len(nodes)])
        for nid in nodes:
            if proc == 0x11:
                result += bytes([nid]) + b"818.802.00.0"
            else:
                profile = bytearray(12)
                if nid == 5:
                    profile[6] = 0x04
                result += bytes([nid]) + bytes(profile)
        return result

    def _proc_0e(self, args: bytes) -> bytes:
        """GetFirmwareVersionList: 5-byte records per requested component.

        Format: [count] + per component: [comp_id, v1, v2, build, 0x00]
        Values from real Mera Comfort capture (onboarding-real-mera.md 2026-06-26).
        """
        if not args:
            return b""
        count = min(args[0], len(args) - 1)
        comp_ids = list(args[1:1 + count])
        records = bytes([len(comp_ids)])
        for cid in comp_ids:
            v1, v2, build = _FW_COMPONENT_VERSIONS.get(cid, (0x30, 0x30, 0))
            records += bytes([cid, v1, v2, build, 0])
        return records + bytes(max(0, 61 - len(records)))  # always pad to 61 bytes

    def _proc_86(self) -> bytes:
        """GetDeviceInitialOperationDate: UTF-8 date string, no null terminator (real device: 31.05.2024)."""
        return b"31.05.2024"

    def _proc_07(self, args: bytes) -> bytes:
        """GetPerNodeProfileSetting: args[0] = node_id, returns 16-bit LE value."""
        node_id = args[0] if args else 0
        value = self._PER_NODE_PROFILE_SETTINGS.get(node_id, 0)
        return bytes([value & 0xFF, (value >> 8) & 0xFF])

    def _proc_0a(self, args: bytes) -> bytes:
        """GetActiveCommonSetting: args[0] = setting ID, returns 16-bit LE value.

        FIXED in Phase 2b: this used to read _ACTIVE_PROFILE_SETTINGS under a
        "GetActiveProfileSetting" docstring, contradicting _PROC_NAMES's own
        "GetActiveCommonSetting" label for proc 0x0A and ble-protocol.md's
        "Active vs Stored" section (0x0A/0x0B operate on the CommonSetting ID
        space, same as 0x51/0x52, just applied immediately, no power-cycle).
        """
        setting_id = args[0] if args else 0
        value = self._ACTIVE_COMMON_SETTINGS.get(setting_id, 0)
        return bytes([value & 0xFF, (value >> 8) & 0xFF])

    @staticmethod
    def _parse_set_setting_args(args: bytes):
        """[arg_count=3, setting_id, value] — confirmed format for 0x08
        (SetActiveProfileSetting, real OTA capture 2026-06-01). Assumed by
        analogy for 0x0B/0x52/0x54 — structurally the same setter shape for
        the Active/Stored, Common/Profile setting pairs — not independently
        confirmed for those three. Verify against a real capture if one
        surfaces. Returns (setting_id, value) or (None, None) if args too short.
        """
        if len(args) < 3:
            return None, None
        return args[1], args[2]

    def _proc_08(self, args: bytes) -> bytes:
        """SetActiveProfileSetting — session-only, never persisted (no confirmed
        getter exists for this proc; write-only as far as this mock is concerned)."""
        setting_id, value = self._parse_set_setting_args(args)
        if setting_id is not None:
            self._ACTIVE_PROFILE_SETTINGS[setting_id] = value
            self._log("·", f"SetActiveProfileSetting id={setting_id} value={value} (session-only, not persisted)")
        return b""

    def _proc_0b(self, args: bytes) -> bytes:
        """SetActiveCommonSetting — session-only, never persisted (mirrors the
        real device: Active is re-derived from Stored NVM on every power-cycle)."""
        setting_id, value = self._parse_set_setting_args(args)
        if setting_id is not None:
            self._ACTIVE_COMMON_SETTINGS[setting_id] = value
            self._log("·", f"SetActiveCommonSetting id={setting_id} value={value} (session-only, not persisted)")
        return b""

    def _proc_51(self, args: bytes) -> bytes:
        """GetStoredCommonSetting: args[0] = setting ID, returns 16-bit LE value."""
        setting_id = args[0] if args else 0
        value = self._STORED_COMMON_SETTINGS.get(setting_id, 0)
        return bytes([value & 0xFF, (value >> 8) & 0xFF])

    def _proc_52(self, args: bytes) -> bytes:
        """SetStoredCommonSetting — persisted immediately via mock_persistence.py.
        Requires a power-cycle to take effect on a real device; the mock applies
        it to _STORED_COMMON_SETTINGS right away since there is no separate
        "pending write" state to model here."""
        setting_id, value = self._parse_set_setting_args(args)
        if setting_id is not None:
            self._STORED_COMMON_SETTINGS[setting_id] = value
            mock_persistence.save("mera", self._adapter_tag, f"common_setting:{setting_id}", value)
            self._log("·", f"SetStoredCommonSetting id={setting_id} value={value} — persisted")
        return b""

    def _proc_53(self, args: bytes) -> bytes:
        """GetStoredProfileSetting: args[0] = setting ID, returns 16-bit LE value."""
        setting_id = args[0] if args else 0
        value = self._STORED_PROFILE_SETTINGS.get(setting_id, 0)
        return bytes([value & 0xFF, (value >> 8) & 0xFF])

    def _proc_54(self, args: bytes) -> bytes:
        """SetStoredProfileSetting — persisted immediately via mock_persistence.py."""
        setting_id, value = self._parse_set_setting_args(args)
        if setting_id is not None:
            self._STORED_PROFILE_SETTINGS[setting_id] = value
            mock_persistence.save("mera", self._adapter_tag, f"profile_setting:{setting_id}", value)
            self._log("·", f"SetStoredProfileSetting id={setting_id} value={value} — persisted")
        return b""

    def _proc_09(self, args: bytes) -> bytes:
        """SetCommand: args[0] = 1-byte command code (ble-protocol.md Layer 1).

        Only the two commands with an unambiguous SPL effect are implemented —
        ToggleAnalShower flips spl[1], ToggleLadyShower flips spl[2]. Both SPL
        indices are classified NO PERSIST (live sensor state) in the roadmap's
        Mera namespace/index enumeration, so this only mutates _SPL_MERA_VALUES,
        never mock_persistence.py. Other command codes are left as no-ops —
        not guessing effects that aren't confirmed anywhere.
        """
        code = args[0] if args else None
        if code == 0:      # ToggleAnalShower
            running = self._SPL_MERA_VALUES.get(1, 0) != 0
            self._SPL_MERA_VALUES[1] = 0 if running else 1281  # packed: temp=1, pressure running
            self._log("·", f"ToggleAnalShower -> spl[1]={self._SPL_MERA_VALUES[1]}")
        elif code == 1:    # ToggleLadyShower
            running = self._SPL_MERA_VALUES.get(2, 0) != 0
            self._SPL_MERA_VALUES[2] = 0 if running else 1  # no packed-field spec confirmed for index 2
            self._log("·", f"ToggleLadyShower -> spl[2]={self._SPL_MERA_VALUES[2]}")
        return b""

    # ---- BLE connection bursts ----

    async def _request_short_ci(self) -> None:
        """Request a shorter BLE connection interval from iOS via BlueZ UpdateConnectionParameters.

        Called right after iOS enables A5 CCCD.  The new CI (8.75-10ms) takes effect
        within ~200ms (6 x 30ms CEs) — well before the first proc request (~480ms later).

        With CI=10ms and 12ms inter-frame delay, all 4 CONS frames of the largest
        proc response (proc 0x82, 82-byte payload) arrive within iOS's ~54ms FlowControl
        window, instead of running past it when CI=30ms.
        """
        if self._bus is None or self._current_device_path is None:
            return
        try:
            dev_intro = await self._bus.introspect("org.bluez", self._current_device_path)
            dev_proxy = self._bus.get_proxy_object("org.bluez", self._current_device_path, dev_intro)
            dev_iface = dev_proxy.get_interface("org.bluez.Device1")
            # minInterval=7 (8.75ms), maxInterval=8 (10ms), latency=0, supervision_timeout=200 (2s)
            await dev_iface.call_update_connection_parameters(7, 8, 0, 200)
            self._log("·", f"Requested CI=8.75-10ms — {self._current_device_path}")
        except Exception as e:
            self._log("·", f"UpdateConnectionParameters: {e}")

    async def _send_info_frame_burst(self, service: MeraService, gen: int) -> None:
        """Send InfoFrame burst on A5 (for bridge) and on A6 (for iOS ConnectionState.Ready).

        Real device (nRF capture v2.14.1): 9 InfoFrames on A6 after CCCD-A7 enable.
        Bridge wait_for_info_frames_async counts frames on A5 only -> also send 10x on A5.

        Event-driven: polls A5 CCCD at 100 ms intervals; fires the burst the instant
        BlueZ sets it to True.  A fixed timer MUST NOT be used — it fires after iOS has
        already shown "cannot connect" and disconnected.

        gen: connection generation guard — stale tasks exit without sending.
        IsButtonPressed is NOT reset on disconnect; resets here after both bursts complete.
        """
        a5 = service._notify_iface
        for _ in range(80):          # max 8 s — iOS gives up well before this
            if not self._connected or self._connection_gen != gen:
                if not self._connected and self._connection_gen == gen:
                    self._log("·", f"Attempt {gen}: client disconnected before A5 CCCD — keep mock running, attempt again")
                return
            if a5 is not None and a5._notify:
                break
            await asyncio.sleep(0.1)
        else:
            self._log("·", f"Attempt {gen}: GATT cache built — A5 CCCD not written within 8 s. Keep mock running, attempt again")
            return
        if not self._connected or self._connection_gen != gen:
            return
        # Request shorter CI so multi-frame responses arrive within iOS's FlowControl window
        asyncio.ensure_future(self._request_short_ci())
        self._log("·", f"Attempt {gen}: sending A5 InfoFrame burst (10x)")
        service._a6_burst_done.clear()   # block A5 responses during both bursts
        for _ in range(10):
            await service.push_notify(_A6_INFO_FRAME)
            await asyncio.sleep(0.05)
        # Also send on A6: iOS watches A6 for InfoFrames to set ConnectionState=Ready
        # (GeberitDeviceCoreService.Connect() line 175).  Wait for CCCD-A6 — written
        # ~200 ms after CCCD-A5, so it will be set by the time the A5 burst finishes.
        a6 = service._notify_a6_iface
        a6_ready = False
        for _ in range(30):          # max 3 s
            if not self._connected or self._connection_gen != gen:
                break
            if a6 is not None and a6._notify:
                a6_ready = True
                break
            await asyncio.sleep(0.1)
        if a6_ready and self._connected and self._connection_gen == gen:
            self._log("·", f"Attempt {gen}: sending A6 InfoFrame burst (9x)")
            for _ in range(9):
                await service.push_notify_a6(_A6_INFO_FRAME)
                await asyncio.sleep(0.05)
        else:
            self._log("·", f"Attempt {gen}: A6 CCCD not set within 3 s — skipping A6 burst")
        if self._button_pressed:
            self._button_pressed = False
            subprocess.run(["btmgmt", "-i", self._hci_index(), "pairable", "off"], capture_output=True)
            self._log("·", "Adapter set to pairable=off (RC pairing window closed)")
            await self._update_advert(0)      # await: HCI commands must finish before A5 responses start
        service._a6_burst_done.set()     # bursts complete — A5 responses may now proceed

    async def _send_a6_reconnect_burst(self, service: MeraService, gen: int) -> None:
        """A6 InfoFrame burst for Connection 2 (same BLE connection, iOS re-subscribes CCCDs).

        Connection 1 (button detection) and Connection 2 (Save/Speichern) share the same
        BLE connection.  iOS re-writes the CCCDs at the Save phase start, but BlueZ omits
        the external ccc_write_cb() when the value is unchanged (0x0001 -> 0x0001), so
        _send_info_frame_burst is never re-triggered.

        Triggered from _handle_request when proc 0x82 arrives with _a6_burst_done already
        set (i.e. the Connection 1 burst completed).  Clears _a6_burst_done to gate
        concurrent A5 proc responses during the burst, same as the initial burst.
        """
        if not self._connected or self._connection_gen != gen:
            return
        a6 = service._notify_a6_iface
        if a6 is None or not a6._notify:
            return
        service._a6_burst_done.clear()
        self._log("·", "Connection 2: sending A6 InfoFrame burst (9x) for ConnectionState.Ready")
        for _ in range(9):
            if not self._connected or self._connection_gen != gen:
                break
            await service.push_notify_a6(_A6_INFO_FRAME)
            await asyncio.sleep(0.05)
        service._a6_burst_done.set()

    # ---- Web UI ----

    def _render_log(self) -> str:
        lines = []
        for ts, direction, msg in self._session_log[-100:]:
            css = {"←": "recv", "→": "send"}.get(direction, "info")
            lines.append(f'<div class="{css}">[{ts}] {direction} {msg}</div>')
        return "\n".join(lines) or "<div class='info'>(no activity)</div>"

    async def _handle_root(self, request):
        from aiohttp import web
        html = _HTML.format(
            version=_MOCK_VERSION,
            conn_cls="ok" if self._connected else "idle",
            conn_txt="Connected" if self._connected else "Idle",
            btn_cls="ok" if self._button_pressed else "warn",
            btn_txt="Pressed" if self._button_pressed else "Waiting",
            article=self._ARTICLE, sap=self._SAP_NUMBER, serial=self._SERIAL,
            description=self._DESCRIPTION, variant=self._VARIANT,
            log_html=self._render_log(),
        )
        return web.Response(content_type="text/html", text=html)

    async def _handle_button(self, request, service: MeraService):
        from aiohttp import web
        if self._button_pressed:
            raise web.HTTPFound("/")
        self._button_pressed = True
        self._log("·", "Button pressed via web UI — advertisement byte[2]=0x01 (IsButtonPressed=True)")
        subprocess.run(["btmgmt", "-i", self._hci_index(), "pairable", "on"], capture_output=True)
        self._log("·", "Adapter set to pairable=on (RC pairing window open)")
        await self._update_advert(1)
        raise web.HTTPFound("/")

    async def _handle_status(self, request):
        from aiohttp import web
        return web.Response(
            content_type="application/json",
            text=json.dumps({
                "mock_version": _MOCK_VERSION,
                "connected": self._connected,
                "button_pressed": self._button_pressed,
                "log_entries": len(self._session_log),
            }),
        )

    async def _handle_clear_log(self, request):
        from aiohttp import web
        self._session_log.clear()
        raise web.HTTPFound("/")

    # ---- Main ----

    async def run(self) -> None:
        self._advert_lock = asyncio.Lock()

        # Auto-named log file alongside this module, tagged with the adapter so two
        # instances started in the same process/minute don't collide on one filename.
        log_path = Path(__file__).parent / f"mock-geberit-mera_{self._adapter_tag}_{time.strftime('%Y-%m-%d_%H-%M')}.log"
        file_h = logging.FileHandler(log_path, encoding="utf-8")
        file_h.setFormatter(self._log_fmt)
        self.logger.addHandler(file_h)
        self.logger.info("Log: %s", log_path.name)

        # Clear any bond records without restarting the daemon.
        # btmgmt unpair removes the device (including stored IRK) from BlueZ memory and
        # disk — iOS's RPA cannot resolve to a bonded identity, preventing auth
        # enforcement on CCCDs.  Skipping the daemon restart preserves the battery
        # plugin's per-session device cache (see test-infrastructure.md).
        hci_addr_path = Path(f"/sys/class/bluetooth/hci{self._hci_index()}/address")
        if hci_addr_path.exists():
            adapter_mac = hci_addr_path.read_text().strip()
            bt_dev_dir = Path("/var/lib/bluetooth") / adapter_mac
            if bt_dev_dir.is_dir():
                for e in bt_dev_dir.iterdir():
                    if e.is_dir() and len(e.name) == 17 and e.name.count(":") == 5:
                        subprocess.run(
                            ["btmgmt", "-i", self._hci_index(), "unpair", e.name],
                            capture_output=True,
                        )
                        self.logger.info("Unpaired bond record: %s", e.name)

        # Reset any lingering pairable=on state from older mock versions.
        # pairable=on causes BlueZ to send an SMP Security Request to iOS -> iOS shows
        # a pairing dialog, interrupting the Connection 1 flow.  Always force off.
        subprocess.run(["btmgmt", "-i", self._hci_index(), "pairable", "off"], capture_output=True)
        self.logger.info("Adapter set to pairable=off")

        from aiohttp import web

        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        self._bus = bus

        # Suppress harmless "does not have property TxPower" error from dbus_next
        class _SuppressDbusPropertyErrors(logging.Filter):
            def filter(self, record):
                return "does not have property" not in record.getMessage()

        logging.getLogger().addFilter(_SuppressDbusPropertyErrors())

        try:
            adapter_wrapper, adapter_path, adapter_addr, _objmgr = await select_adapter(bus, self.adapter)
        except ValueError as e:
            self.logger.error(str(e))
            await bus.disconnect()
            return
        if not adapter_wrapper:
            self.logger.error("no Bluetooth adapter found")
            await bus.disconnect()
            return
        self.logger.info("Adapter: %s  path: %s", adapter_addr, adapter_path)

        # Set Device Name (GATT 0x2a00) to "ro" — cosmetic, matches real Mera Comfort.
        # The App's actual button-state gating check reads the READ characteristic
        # (UUID 0x3A2B, handle 0x0020) which also returns b"ro". Both are set to "ro".
        if adapter_path:
            try:
                ai = await bus.introspect("org.bluez", adapter_path)
                ap = bus.get_proxy_object("org.bluez", adapter_path, ai)
                props = ap.get_interface("org.freedesktop.DBus.Properties")
                await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", "ro"))
                self.logger.info("Adapter alias set to 'ro'  (GATT 0x2a00 Device Name)")
            except Exception as e:
                self.logger.warning("could not set adapter alias: %s", e)

        # Register GATT services.
        #
        # Why _emit_interface_added is suppressed:
        # dbus_next queues all D-Bus messages for async sending.  When bus.export()
        # is called for each characteristic inside service.register(), it fires
        # _emit_interface_added(), which queues an InterfacesAdded signal for that
        # characteristic.  Because the queue is FIFO, ALL 7 InterfacesAdded signals
        # are sent to BlueZ BEFORE the RegisterApplication method call arrives.
        # BlueZ processes those pre-registration signals and creates preliminary
        # handle allocations for all 7 characteristics.  When RegisterApplication
        # then arrives and BlueZ calls GetManagedObjects, BlueZ's GDBusClient watcher
        # dedup logic sees characteristics 2-6 as already tracked and skips creating
        # ATT Characteristic Declaration (0x2803) attributes for them.  Result:
        # only 3a2b and A5 get char decls; A6-A8, A1, A2 are handle-allocated but
        # invisible to ATT Read By Type uuid=0x2803 — iOS cannot find A6 and the
        # Connection 1 flow fails.
        #
        # Fix: suppress _emit_interface_added during the initial export so BlueZ
        # learns about all characteristics exclusively via GetManagedObjects.
        # bus.export() still adds every characteristic to _path_exports (line 120
        # of message_bus.py runs before _emit_interface_added), so GetManagedObjects
        # returns all 7 and BlueZ creates char decls for all 7.
        #
        # Fixed for Phase 5 (multi-device in one process): patch bus._emit_interface_added
        # as an INSTANCE attribute, not the BaseMessageBus CLASS. Assigning a plain
        # function to bus._emit_interface_added shadows the class method for this bus
        # object only (Python attribute lookup checks the instance __dict__ before the
        # class) — calling it as self._emit_interface_added(...) still works identically,
        # since instance-dict lookups return the function as-is, unbound, matching
        # _counting_emit's plain (*a, **kw) signature. This means two MeraMock/AlbaMock
        # instances (each with their own bus = await MessageBus(...).connect()) can
        # register GATT concurrently without one's patch/restore racing the other's.
        #
        # v1.34.0b1: pre-cleanup — unregister any stale GATT application from a previous
        # mock run that exited without calling UnregisterApplication.  BlueZ retains
        # GDBusClient watcher entries for those paths; stale entries cause it to skip
        # creating ATT Characteristic Declarations (0x2803) for chars 2-6 on the next
        # RegisterApplication, leaving only 3a2b + A5 visible to iOS.
        #
        # App paths are tagged with the adapter so two instances (e.g. once Sela
        # reuses this same protocol module) don't collide on one D-Bus object path.
        # Prefixed with the model name, not just the adapter: "battery"/"dis" are generic
        # service names that AlbaMock also uses — tagging by adapter alone would collide
        # if a Mera and an Alba mock ever share one adapter (Phase 5, mock_service.py).
        app_paths = {
            "mera": f"/org/bluez/example/mera_gatt_{self._adapter_tag}",
            "battery": f"/org/bluez/example/mera_battery_{self._adapter_tag}",
            "dis": f"/org/bluez/example/mera_dis_{self._adapter_tag}",
            "rc_pairing": f"/org/bluez/example/mera_rc_pairing_{self._adapter_tag}",
        }
        try:
            gatt_manager = adapter_wrapper._proxy.get_interface("org.bluez.GattManager1")
            stale_cleaned = []
            for app_path in app_paths.values():
                try:
                    await gatt_manager.call_unregister_application(app_path)
                    stale_cleaned.append(app_path.split("/")[-1])
                except Exception:
                    pass
            if stale_cleaned:
                self.logger.info("Pre-cleanup: removed stale GATT apps: %s", stale_cleaned)
            else:
                self.logger.debug("Pre-cleanup: no stale GATT app (OK on first run)")
        except Exception as e:
            self.logger.debug("Pre-cleanup: no stale GATT app (OK on first run): %s", e)

        emit_count = [0]

        def _counting_emit(*a, **kw):
            emit_count[0] += 1
            # intentionally suppressed — do not forward to BlueZ

        bus._emit_interface_added = _counting_emit
        service = MeraService(self)
        battery_service = BatteryService()
        dis_service = _DISService()
        rc_pairing_service = _RCPairingService()
        try:
            try:
                await service.register(bus, app_paths["mera"], adapter_wrapper)
                await battery_service.register(bus, app_paths["battery"], adapter_wrapper)
                await dis_service.register(bus, app_paths["dis"], adapter_wrapper)
                await rc_pairing_service.register(bus, app_paths["rc_pairing"], adapter_wrapper)
            finally:
                del bus._emit_interface_added
            self.logger.info("GATT service registered (suppressed %d InterfacesAdded signals)", emit_count[0])
            exported = list(getattr(bus, "_path_exports", {}).keys())
            self.logger.info("D-Bus exported paths (%d): %s", len(exported), exported)
            for attr in ("_characteristics", "_chars"):
                chars_list = getattr(service, attr, None)
                if chars_list:
                    self.logger.info("GATT characteristics (%d):", len(chars_list))
                    for c in chars_list:
                        uuid  = getattr(c, "uuid",  getattr(c, "_uuid",  "?"))
                        flags = getattr(c, "flags", getattr(c, "_flags", "?"))
                        self.logger.info("  UUID=%s  flags=%s", uuid, flags)
                    break

        except Exception as e:
            self.logger.error("GATT registration failed: %s", e)
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
            self.logger.info("Notify characteristic wired (A5)")
        else:
            self.logger.warning("notify characteristic not found — push notifications disabled")

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
            self.logger.info("A6 notify characteristic wired")
        else:
            self.logger.warning("A6 notify characteristic not found — Connection 1 burst disabled")

        # Wire A7 and A8 notify by UUID so multi-frame responses can distribute across characteristics
        for uuid_target, wire_fn, label in [
            (_NOTIFY_A7_UUID, service.wire_notify_a7, "A7"),
            (_NOTIFY_A8_UUID, service.wire_notify_a8, "A8"),
        ]:
            found = None
            for attr in ("_characteristics", "_chars"):
                chars = getattr(service, attr, None)
                if chars:
                    for c in chars:
                        uuid = str(getattr(c, "uuid", getattr(c, "_uuid", ""))).lower()
                        if uuid == uuid_target.lower():
                            found = c
                            break
                if found:
                    break
            if found:
                wire_fn(found)
                self.logger.info("%s notify characteristic wired", label)
            else:
                self.logger.warning("%s notify characteristic not found — multi-frame distribution degraded", label)

        # Advertise via D-Bus LEAdvertisingManager1 (same path as mock-geberit-alba).
        # BlueZ encodes UUID 0x3EA0 and manufacturer data into the ADV_IND payload;
        # the local name is placed in SCAN_RSP automatically.
        # Store bus/adapter on the instance so _update_advert() can unregister/re-register
        # on button press.
        self._advert_bus = bus
        self._advert_adapter = adapter_wrapper
        self._advert = _MeraAdvertisement(self._ARTICLE)
        await self._advert.register(bus, adapter_wrapper)
        self.logger.info(
            "Advertising: UUID=0x3EA0  company=0x0100  byte[2]=0x00 (IsButtonPressed=False)"
            "  article=%s  name='Geberit AC PRO'", self._ARTICLE
        )

        # Track BLE connections via ObjectManager + PropertiesChanged bus listener.
        # InterfacesAdded fires only for new Device1 objects; PropertiesChanged fires for
        # every Connected=True/False change including iOS RPA reconnects. Use both.
        try:
            intro = await bus.introspect("org.bluez", "/")
            proxy = bus.get_proxy_object("org.bluez", "/", intro)
            objmgr = proxy.get_interface("org.freedesktop.DBus.ObjectManager")

            async def _force_remove_and_reregister(device_path: str) -> None:
                # BlueZ marks every non-bonded disconnected device as "temporary" and
                # starts a ~20 s cleanup timer. When the timer fires, device_remove()
                # -> device_free() triggers service_disconnect for our mock's D-Bus name
                # -> proxy_removed_cb tears down our GATT app registration and sends a
                # Service Changed indication to any active iOS connection -> iOS
                # re-discovers an empty GATT database and fails.
                #
                # Fix: force-remove the device NOW via Adapter1.RemoveDevice so the
                # teardown fires immediately while no iOS client is connected, then
                # re-register both GATT apps so they are intact for the next attempt.
                mac = device_path.split("/")[-1][4:].replace("_", ":")
                self._log("·", f"Force-removing {mac} to prevent GATT teardown on next connection")
                try:
                    ai = await bus.introspect("org.bluez", adapter_path)
                    ap = bus.get_proxy_object("org.bluez", adapter_path, ai)
                    await ap.get_interface("org.bluez.Adapter1").call_remove_device(device_path)
                except Exception as exc:
                    self._log("!", f"RemoveDevice {mac} failed: {exc} — GATT teardown may fire during Connection 2")
                    return
                # Wait for BlueZ to finish the teardown (service_disconnect fires async
                # in the next GLib event-loop iteration after RemoveDevice returns).
                await asyncio.sleep(0.5)
                try:
                    gm = adapter_wrapper._proxy.get_interface("org.bluez.GattManager1")
                    for app in app_paths.values():
                        try:
                            await gm.call_unregister_application(app)
                        except Exception:
                            pass
                    await gm.call_register_application(app_paths["mera"], {})
                    await gm.call_register_application(app_paths["battery"], {})
                    await gm.call_register_application(app_paths["dis"], {})
                    await gm.call_register_application(app_paths["rc_pairing"], {})
                    self._log("·", "GATT apps re-registered — ready for Connection 2")
                except Exception as exc:
                    self._log("!", f"GATT re-registration failed: {exc}")

            def _on_device_connected(device_path: str, addr: str) -> None:
                if self._connected:
                    return  # deduplicate: InterfacesAdded and PropertiesChanged may both fire
                self._connected = True
                self._current_device_path = device_path
                self._connection_gen += 1
                gen = self._connection_gen
                self._log("·", f"BLE client connected: {addr}")
                asyncio.ensure_future(self._send_info_frame_burst(service, gen))

            def _on_device_disconnected(device_path: str) -> None:
                if not self._connected or device_path != self._current_device_path:
                    return  # stale disconnect for an old/untracked device
                self._connected = False
                self._current_device_path = None
                self._log("·", f"BLE client disconnected: {device_path}")
                # IsButtonPressed resets only after the A5 burst fires (in
                # _send_info_frame_burst). While it is still True, pairing is
                # incomplete and iOS may retry — force-remove this device now so
                # BlueZ's ~20 s cleanup timer cannot fire during the next attempt.
                if self._button_pressed:
                    asyncio.ensure_future(_force_remove_and_reregister(device_path))

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
                # dev_XX_XX_XX_XX_XX_XX -> XX:XX:XX:XX:XX:XX
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
                dbus_intro = await bus.introspect("org.freedesktop.DBus", "/org/freedesktop/DBus")
                dbus_iface = bus.get_proxy_object(
                    "org.freedesktop.DBus", "/org/freedesktop/DBus", dbus_intro
                ).get_interface("org.freedesktop.DBus")
                await dbus_iface.call_add_match(
                    "type='signal',sender='org.bluez',"
                    "interface='org.freedesktop.DBus.Properties',member='PropertiesChanged'"
                )
                self.logger.info("PropertiesChanged match rule registered")
            except Exception as me:
                self.logger.warning("AddMatch PropertiesChanged failed: %s", me)

            bus.add_message_handler(_on_props_msg)
            self.logger.info("Connection tracking active (InterfacesAdded + PropertiesChanged)")
        except Exception as e:
            self.logger.warning("connection tracking unavailable: %s", e)

        # aiohttp web server
        app = web.Application()
        app.router.add_get("/", self._handle_root)
        app.router.add_post("/button", lambda r: self._handle_button(r, service))
        app.router.add_get("/status", self._handle_status)
        app.router.add_post("/clear-log", self._handle_clear_log)

        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.web_port).start()

        self.logger.info("")
        self.logger.info("--- Mera Comfort Mock Active ---")
        self.logger.info("    mock: %s  script: %s  bridge: %s", _MOCK_VERSION, _SCRIPT_HASH, _BRIDGE_VERSION)
        self.logger.info("    adapter: %s", self.adapter or "(first found)")
        self.logger.info("    SAP: %s  article: %s", self._SAP_NUMBER, self._ARTICLE)
        self.logger.info("    Device Name (0x2a00): 'ro'")
        self.logger.info("    Web UI: http://0.0.0.0:%d/", self.web_port)
        self.logger.info("    Log file: %s", log_path.name)
        self.logger.info("")

        await asyncio.get_event_loop().create_future()   # run forever


if __name__ == "__main__":
    # Minimal standalone entry point — not mock_service.py (Phase 4), just enough
    # to run this refactored class by hand on the mock VM and confirm Phase 2
    # didn't change behavior versus tools/mock-geberit-mera.py.
    import argparse

    parser = argparse.ArgumentParser(
        description="mera_mock.py — class-based BLE peripheral mock for Geberit AquaClean Mera Comfort"
    )
    parser.add_argument("--port", type=int, default=8765, metavar="PORT",
                        help="Web UI port (default: 8765)")
    parser.add_argument("--adapter", metavar="ADAPTER", default=None,
                        help="BlueZ adapter node name, e.g. hci1 (default: first found)")
    parser.add_argument("--state-dir", metavar="DIR", default=None,
                        help="Directory for the shared persistence DB (default: alongside this module)")
    parser.add_argument("--version", action="version",
                        version=f"mera_mock {_MOCK_VERSION}")
    parsed = parser.parse_args()

    mock = MeraMock(adapter=parsed.adapter, web_port=parsed.port, state_dir=parsed.state_dir)
    try:
        asyncio.run(mock.run())
    except KeyboardInterrupt:
        mock.logger.info("Mock stopped.")
