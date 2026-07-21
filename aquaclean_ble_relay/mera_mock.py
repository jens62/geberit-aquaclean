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
import shutil
import hashlib
import struct
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
from aquaclean_console_app.aquaclean_core.Frames.FrameCollector                     import FrameCollector   as _FrameCollector  # noqa: E402

from aquaclean_ble_relay.mock_bluez_adapter import select_adapter  # noqa: E402
from aquaclean_ble_relay import mock_persistence  # noqa: E402
from aquaclean_ble_relay import mock_logging  # noqa: E402

_BLEMSG_ID_CRC_RSP = 5   # matches Message.BLEMSG_ID_CRC_RSP

# ---- version ----
_MOCK_VERSION = "1.107.0b1"
_SCRIPT_HASH = hashlib.md5(Path(__file__).read_bytes()).hexdigest()[:8]

try:
    from importlib.metadata import version as _pkg_ver
    _BRIDGE_VERSION = _pkg_ver("geberit-aquaclean")
except Exception:
    _BRIDGE_VERSION = "unknown"

# ---- D-Bus / bluez_peripheral (mirror Alba mock import pattern) ----
from bluez_peripheral.gatt.service import Service, ServiceCollection
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.advert import Advertisement
from bluez_peripheral.agent import NoIoAgent

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
# Fallback defaults only — each MeraMock instance copies this into
# self._FW_COMPONENT_VERSIONS at init and overlays any persisted per-adapter
# values on top (docs/developer/mock-service-requirements.md §5 "Firmware
# version persistence").
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

# True real-life pre-update snapshot (2026-07-18) — the genuine RS28->RS30 capture
# (memory/mera-firmware-update-ble-protocol.md) showed only components 1 and 11
# actually changed: component 1 (main controller) RS28.0 TS199 -> RS30.0 TS206,
# component 11 (motion detection) RS07.0 TS22 -> RS08.0 TS23. Every other component
# was byte-identical before/after. This is the accurate real pre-update device
# state — not an experimental/synthetic variant (see docs/developer/
# firmware-version.md "Consolidated summary" for the several synthetic variants
# tried on 2026-07-17 before landing back on the real data here).
_FW_COMPONENT_VERSIONS_RS28 = {
    **_FW_COMPONENT_VERSIONS,
    1: (0x32, 0x38, 0xC7),   # RS28.0 TS199 — real pre-update value
    11: (0x30, 0x37, 0x16),  # RS07.0 TS22  — real pre-update value
}

# Canonical, real-life firmware profiles selectable via the webui dropdown — keyed
# by the value the generic <select> control (mock-controls.js) writes/reads. Both
# apply the full real-life 13-component set matching the main controller's (component
# 1) version, not a synthetic/uniform value (2026-07-18 correction — see above).
_FW_PROFILES = {
    "rs30": _FW_COMPONENT_VERSIONS,
    "rs28": _FW_COMPONENT_VERSIONS_RS28,
}

# "Reset to Factory Settings" recovery target. Was a synthetic uniform-RS28.0-TS199
# baseline (v1.88.0b1) — a workaround chosen 2026-07-17 to avoid the app's blocking
# force-update screen, before the real root cause (request-frame truncation + missing
# per-frame FlowControl ack in _handle_request, fixed v1.98.0b1-v1.99.1b1) was found.
# Updated 2026-07-19 to the real, non-uniform rs28 profile — the actual configuration
# confirmed end-to-end (full onboarding, no blocker, no "Fehler", correct version shown)
# once the real bug was fixed. See memory/mera-firmware-update-request-truncation.md.
_FW_COMPONENT_VERSIONS_FACTORY = dict(_FW_COMPONENT_VERSIONS_RS28)

# Device-identity factory defaults (unchanged since v1.88.0b1) — used both as
# __init__'s hardcoded starting point and as the "Reset to Factory Settings"
# target. Field names corrected 2026-07-18: proc 0x82 offset 0 is SapNumber (dotted
# format, e.g. "146.21x.xx.1"), not "ArticleNumber" as this file and mock-geberit-
# mera.md previously labeled it — confirmed by the app's own DeviceIdentification
# log line and docs/mqtt.md's dotted-format Identification/SapNumber topic. Offset
# 12 is plain SerialNumber (previously mislabeled "SerialNumber (SAP)").
_FACTORY_IDENTITY = {
    "article": "14621",                    # BLE advertisement model-lookup prefix (proc 0x82 unrelated)
    "sap_number": "146.21x.xx.1",          # proc 0x82 offset 0  — SapNumber
    "serial_number": "HB2300EU000001",     # proc 0x82 offset 12 — SerialNumber (fictional, avoids CRC32(SAP) collision — see mock-geberit-mera.md)
    "production_date": "11.04.2023",       # proc 0x82 offset 32 — ProductionDate
    "description": "AquaClean Mera Comfort",  # proc 0x82 offset 42 — Description
    "variant": 0x0D,                       # Mera Comfort
    "initial_operation_date": "31.05.2024",  # proc 0x86
    "soc_version": "10.18",                # proc 0x81
    # GATT 0x2a00 (Device Name), served via the adapter's Alias property. Corrected
    # 2026-07-18 — was hardcoded to "ro" with a comment claiming it "matches real Mera
    # Comfort"; that was false. Real value confirmed via aquaclean-...SILLY.log:
    # BlueZ's Device1.Name changes to this 270ms after connecting (before
    # ServicesResolved), i.e. read from GATT 0x2a00 — different from the advertised
    # local name ("Geberit AC PRO").
    "device_name": "Geberit AquaClean pro",
}

# Real-device reference values shown as the webui's "real: ..." hint next to each
# identity field. Distinct from _FACTORY_IDENTITY (the mock's actual startup
# defaults) because ONE field's factory default is deliberately fictional:
# serial_number ("HB2300EU000001") is chosen specifically to avoid a CRC32(SAP)
# collision with a real device (see mock-geberit-mera.md) — showing that same
# fictional value as if it were the "real" reference was a bug (2026-07-18).
# Confirmed real value from aquaclean-...SILLY.log line 3619 ("DeviceIdentification:
# SapNumber=146.21x.xx.1, SerialNumber=HB2304EU298413, ...") and line 9314 (MQTT
# publish of the same). All other fields' factory defaults already match the real
# device (independently confirmed against the same SILLY log and
# onboarding-real-mera.md), so they fall through to _FACTORY_IDENTITY unchanged.
_IDENTITY_REAL_REFERENCE = {
    **_FACTORY_IDENTITY,
    "serial_number": "HB2304EU298413",
}

# identity field key -> instance attribute name, shared by __init__'s persisted-value
# overlay, the webui write handler, and factory reset.
_IDENTITY_ATTR_MAP = {
    "article": "_ARTICLE",
    "sap_number": "_SAP_NUMBER",
    "serial_number": "_SERIAL_NUMBER",
    "production_date": "_PRODUCTION_DATE",
    "description": "_DESCRIPTION",
    "variant": "_VARIANT",
    "initial_operation_date": "_INITIAL_OPERATION_DATE",
    "soc_version": "_SOC_VERSION",
    "device_name": "_DEVICE_NAME",
}

# (label, max ascii-byte length) for each free-text identity field — max lengths
# match proc 0x82's fixed-width fields where applicable; "variant" is handled
# separately (hex byte, not text) both here and in the webui rows.
_IDENTITY_FIELD_META = [
    ("article", "Article (BLE adv. model prefix)", 5),
    ("sap_number", "SAP Number (proc 0x82 offset 0)", 12),
    ("serial_number", "Serial Number (proc 0x82 offset 12)", 20),
    ("production_date", "Production Date (proc 0x82 offset 32)", 10),
    ("description", "Description (proc 0x82 offset 42)", 40),
    ("initial_operation_date", "Initial Operation Date (proc 0x86)", 10),
    ("soc_version", "SOC Application Version (proc 0x81)", 5),
    ("device_name", "Device Name (GATT 0x2a00)", 40),
]

_DEFAULT_PROFILE_SETTINGS = {0: 1, 1: 3, 2: 2, 3: 2, 4: 2, 5: 0, 6: 1, 7: 1, 8: 0, 9: 0}
_DEFAULT_COMMON_SETTINGS  = {0: 1, 1: 3, 2: 2, 3: 2, 4: 2, 5: 0, 6: 1, 7: 1, 8: 0, 9: 0}


def _format_fw_version(v1: int, v2: int, build: int) -> str:
    return f"RS{chr(v1)}{chr(v2)}.0 TS{build}"


def _parse_fw_version(text: str) -> tuple:
    """Inverse of _format_fw_version — webui free-text firmware-version edits.
    Raises ValueError with a user-facing message on malformed input."""
    import re
    m = re.fullmatch(r"RS(\d)(\d)\.0 TS(\d{1,4})", text.strip())
    if not m:
        raise ValueError(f'expected format "RS30.0 TS206", got {text!r}')
    d1, d2, build = m.groups()
    return (ord(d1), ord(d2), int(build))


def _encode_soc_version(version_str: str) -> bytes:
    """proc 0x81 payload: major_str(2) + minor_byte + null. "10.18" -> b"10\\x12\\x00"."""
    major, _, minor = version_str.partition(".")
    try:
        minor_byte = int(minor) & 0xFF
    except ValueError:
        minor_byte = 0
    return major.encode("ascii", errors="replace")[:2].ljust(2, b"0") + bytes([minor_byte, 0x00])

# Friendly names for the webui's read-only Firmware Versions section — mirrors
# _FW_COMPONENT_VERSIONS's own trailing comments (duplicated as structured data
# since comments aren't machine-readable; see docs/developer/mock-service-
# requirements.md §6).
_FW_COMPONENT_NAMES = {
    1: "Steuerung (main controller)", 3: "Geruchsabsaugung", 4: "Duscheinheit",
    5: "Deckelheber", 6: "Föhnmodul", 7: "WW-Bereitung", 8: "WC-Sitz-Heizung",
    9: "Bedienfeld", 10: "Benutzererkennung", 11: "Bewegungserkennung",
    12: "Orientierungslicht", 14: "Föhneinheit", 15: "Schnittstellenmodul",
}

# Firmware-update proc sequence (ctx=0x40, plus a ctx=0x00/proc=0x01 companion
# frame) — Phase 9b, see .claude/rules/ble-protocol.md "Firmware update
# procedures (ctx=0x40)" and memory/mera-firmware-update-ble-protocol.md for
# the decoded real-device sequence this simulates. Timers are shortened from
# the real ~2m44s flash window / ~13s reboot silence — this is a mock aid for
# exercising the app's update UX, not a byte-exact replay; see docs/developer/
# mock-service-requirements.md Phase 9b "complete process" for the deferred
# byte-exact items (progress-notify frames, real timer lengths, bulk-transfer
# byte-count logging).
_FW_UPDATE_BUSY_SECONDS = 20    # simulated flash window (real: ~164s)
_FW_UPDATE_REBOOT_SECONDS = 8   # simulated reboot silence (real: ~13.3s)
# Progress-notify ticks during the busy window (A5, spontaneous) — real device
# emits +12 every ~2.2s over ~164s, reaching ~840-888. Compressed to the same
# final value over the shortened window: 10 ticks x +84 = 840.
_FW_UPDATE_PROGRESS_TICKS = 10
_FW_UPDATE_PROGRESS_STEP = 84
# Bulk firmware-binary write observability: log a byte-count summary every
# this many bytes per channel instead of a per-frame hex dump (which would be
# ~14,500 lines for the real ~290KB transfer and is meaningless binary anyway).
_FW_BULK_LOG_THRESHOLD = 8192
# Synthetic ctx=0x40/proc=0x00 keepalive payload — real captures show this
# telemetry-like value fluctuating per call without gating app progression
# (the app keeps polling it unchanged for 30+ seconds before the user taps
# "Update Now"), so a fixed template is sufficient.
_FW_UPDATE_KEEPALIVE = bytes([0x00, 0x20, 0x00, 0x08, 0xFF, 0xE7, 0x00, 0x08, 0x00, 0x04, 0x00, 0x00])

# Settings-table metadata (name, kind, min, max, options) for the webui's
# generic mock-controls.js renderer (docs/developer/mock-service-requirements.md
# §6). Names/ranges from .claude/rules/ble-protocol.md's ProfileSettings/
# CommonSetting tables (canonical source per CLAUDE.md's BLE protocol rules).
# kind mirrors the widget mock-controls.js builds: stepper|toggle|select|swatch.
# OscillatorState's real range is unconfirmed (ble-protocol.md notes no
# min/max) and the mock's shared default dict seeds it at 3 — rendered as a
# small stepper rather than a toggle so an out-of-[0,1] default doesn't look
# broken.
_PROFILE_SETTING_META = {
    0: ("Odour Extraction",     "toggle",  0, 1, None),
    1: ("Oscillator State",     "stepper", 0, 4, None),
    2: ("Anal Shower Pressure", "stepper", 0, 4, None),
    3: ("Lady Shower Pressure", "stepper", 0, 4, None),
    4: ("Anal Shower Position", "stepper", 0, 4, None),
    5: ("Lady Shower Position", "stepper", 0, 4, None),
    6: ("Water Temperature",    "stepper", 0, 5, None),
    7: ("WC Seat Heat",         "stepper", 0, 5, None),
    8: ("Dryer Temperature",    "stepper", 0, 5, None),
    9: ("Dryer State",          "toggle",  0, 1, None),
}

_ORIENTATION_LIGHT_COLORS = [
    (0, "Blue",       "#3366ff"),
    (1, "Turquoise",  "#33cccc"),
    (2, "Magenta",    "#cc33cc"),
    (3, "Orange",     "#ff8800"),
    (4, "Yellow",     "#ffee33"),
    (5, "Warm White", "#ffe0b3"),
    (6, "Cold White", "#e6f0ff"),
]
_ORIENTATION_LIGHT_MODES = [(0, "Off"), (1, "On"), (2, "When Approached")]

_COMMON_SETTING_META = {
    0: ("Water Hardness",               "stepper", 0, 4, None),
    1: ("Orientation Light Brightness", "stepper", 0, 4, None),
    2: ("Orientation Light Colour",     "swatch",  0, 6, _ORIENTATION_LIGHT_COLORS),
    3: ("Orientation Light Mode",       "select",  0, 2, _ORIENTATION_LIGHT_MODES),
    4: ("Lid Sensor Range",             "stepper", 0, 4, None),
    5: ("Odour Extraction Run-On",      "toggle",  0, 1, None),
    6: ("Lid Auto Open",                "toggle",  0, 1, None),
    7: ("Lid Auto Close",               "toggle",  0, 1, None),
    8: ("Auto Flush",                   "toggle",  0, 1, None),
    9: ("Demo Mode",                    "toggle",  0, 1, None),
}

# ---- Advertisement D-Bus path (bluez_peripheral default, used for unregister) ----
_ADVERT_PATH = "/com/spacecheese/bluez_peripheral/advert0"


def _parse_reassembled_request(data: bytes):
    """Parse a fully reassembled request body -> (ctx, proc, args). Pure
    function, no instance state.

    `data` is the concatenation of each frame's `Payload` (data[1:20] per
    frame — see FrameCollector/SingleFrame.create_single_frame in the
    bridge's aquaclean_core.Frames), i.e. every frame's leading type-header
    byte already stripped. For a single-frame request this is just one
    19-byte chunk; for a multi-frame request (e.g. GetFirmwareVersionList's
    12-component query, which needs 13 args bytes — more than the 9 that fit
    in one frame) it's the FIRST frame's payload followed by one or more
    CONS frames' payloads, in order. Field offsets below are the original
    20-byte-frame offsets (see git history) shifted left by 1 to account for
    the stripped header byte:
      data[0:6]   CrcMessage header (id, segments, len, crc16) — unused here
      data[6]     node = 0x01
      data[7]     ctx
      data[8]     proc
      data[9]     arg_len
      data[10:]   args
    """
    ctx     = data[7]
    proc    = data[8]
    arg_len = data[9]
    args    = bytes(data[10:10 + arg_len]) if arg_len else b""
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


def _build_progress_frame(progress: int) -> bytes:
    """Spontaneous firmware-update progress notification (A5, ctx=0x40 flash
    window) — Phase 9b. Distinct message id=6 (not proc-response id=5), body
    = [0x03, 0x00, 0x00, progress:u32-LE, 0x0c] (8 bytes). Decoded from the
    real capture (memory/mera-firmware-update-ble-protocol.md): trailing bytes
    beyond the declared 8-byte body length carry real-device buffer-reuse
    garbage (confirmed non-zero, non-constant across otherwise-identical
    frames) — outside the declared length, so irrelevant to any correct
    parser; this always zero-pads there instead of replicating that garbage."""
    body = bytes([0x03, 0x00, 0x00]) + struct.pack('<I', progress) + bytes([0x0c])
    msg = CrcMessage.create(6, 0x00, body)
    serialized = bytes(msg.serialize())
    return bytes([0x11]) + serialized[:19]


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
        mock._gatt_service = self  # back-ref so async tasks (e.g. Phase 9b's
        # _fw_update_run) can push spontaneous notifications outside the
        # request/response path, without threading `service` through _dispatch.
        self._fw_bulk_bytes: dict = {"A1": 0, "A2": 0, "A3": 0, "A4": 0}  # Phase 9b bulk-transfer observability
        self._notify_value = bytes(20)
        self._notify_iface = None         # wired after register() via wire_notify()
        self._notify_a6_iface = None      # wired after register() via wire_notify_a6()
        self._notify_a7_iface = None      # wired after register() via wire_notify_a7()
        self._notify_a8_iface = None      # wired after register() via wire_notify_a8()
        self._last_a5_frames: list = []   # last response frames; used for FlowControl retransmit
        self._last_a5_proc: int = 0       # proc code of last multi-frame response (for progress log)
        self._retransmit_count: int = 0   # retransmits for current transaction; reset on new proc
        # Reassembles multi-frame WRITE requests (FIRST+CONS) — reused from the
        # bridge's own aquaclean_core.Frames.FrameCollector rather than
        # reimplementing it. The bridge only ever uses this to reassemble
        # multi-frame NOTIFY *responses* (it's a client, never a request
        # receiver); the mock is the peripheral side of the exact same wire
        # format, receiving multi-frame *requests* — same primitive, opposite
        # direction. Previously missing entirely: the mock dispatched on the
        # FIRST frame alone and silently dropped CONS continuations (see
        # memory/mera-firmware-update-request-truncation.md), which is why
        # GetFirmwareVersionList's 12-component query (13 args bytes, more
        # than the 9 that fit in one frame) always came back short.
        self._frame_collector = _FrameCollector()
        self._frame_collector.TransactionCompleteFC += self._on_request_reassembled
        # Ack bitmap for the request currently being reassembled — set/sent
        # per-frame in _handle_request (see _send_request_ack). NOT the same
        # as FrameCollector's own SendControlFrame event: that one only fires
        # every 4 frames or at completion (fine for the *response* side, where
        # the app acks a whole batch), but a real capture (onboarding-real-
        # mera.md, 14:11:09.414-.504) shows the real device acking every
        # single frame of an incoming request — after the FIRST frame alone
        # (of a 2-frame request) and again after the CONS frame — and the app
        # does not send CONS at all without that first per-frame ack.
        self._request_ack_bitmap = bytearray(8)
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

    def _log_write(self, idx: int, raw: bytes) -> None:
        """Log an incoming write on A1-A4 (idx 0-3, matching write_0..write_3) —
        Phase 9b: during the firmware-update flash window the app also writes
        ~290KB of raw (non-proc-framed) binary on these same characteristics
        (see .claude/rules/ble-protocol.md "Firmware update procedures"). A
        per-frame hex dump of that is both meaningless (opaque binary) and
        would flood the log (~14,500 lines for the real transfer size) — log a
        running byte-count summary instead, only while an update is actually
        in progress."""
        if self._mock._fw_update_state == "started":
            channel = f"A{idx + 1}"
            self._fw_bulk_bytes[channel] += len(raw)
            total = self._fw_bulk_bytes[channel]
            if total // _FW_BULK_LOG_THRESHOLD != (total - len(raw)) // _FW_BULK_LOG_THRESHOLD:
                self._mock._log("·", f"  {channel}: {total} bulk bytes received (firmware update in progress)")
            return
        self._mock._log("←", f"WRITE_{idx} ({len(raw)}B): {raw.hex()}")

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
                # SubFrameCountOrIndex packs two different meanings depending on
                # IsSubFrameCount (bit 0): on a FIRST/SINGLE frame it's n_cons
                # (how many CONS frames follow, 0 for a plain single-frame
                # request); on a CONS frame it's that frame's own index.
                # Mirrors FrameService.process_data's SINGLE-frame branch in
                # the bridge's aquaclean_core.Frames — same collector, same
                # start_transaction(n+1)/add_frame(i, payload) pattern, just
                # reused here for the opposite (request-receiving) direction.
                sub_frame_count_or_index = (hdr >> 1) & 3
                if hdr & 0x01:
                    # FIRST frame (n_cons may be 0, i.e. a complete single-frame request)
                    self._request_ack_bitmap = bytearray(8)
                    self._request_ack_bitmap[0] |= 1
                    await self._send_request_ack()
                    await self._frame_collector.start_transaction(sub_frame_count_or_index + 1)
                    await self._frame_collector.add_frame(0, raw[1:20])
                else:
                    # CONS continuation frame
                    frame_index = sub_frame_count_or_index
                    self._request_ack_bitmap[frame_index // 8] |= (1 << (frame_index % 8))
                    await self._send_request_ack()
                    await self._frame_collector.add_frame(frame_index, raw[1:20])

    async def _on_request_reassembled(self, sender, data: bytes) -> None:
        """FrameCollector.TransactionCompleteFC handler — fires once every
        expected frame (FIRST + all CONS) of one request has arrived. `data`
        is the concatenation of each frame's Payload in order (see
        _parse_reassembled_request); for a single-frame request this fires
        immediately with just that one frame's payload."""
        mock = self._mock
        ctx, proc, args = _parse_reassembled_request(bytes(data))
        # Wait for any in-progress A6 burst to finish; prevents ATT congestion
        # that causes iOS to drop A5 frames and send a partial FlowControl ACK.
        try:
            await asyncio.wait_for(self._a6_burst_done.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            mock._log("·", "A6 burst wait timed out — sending A5 response anyway")
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

    async def _send_request_ack(self) -> None:
        """Acks the frame just received (via self._request_ack_bitmap) with a
        CONTROL frame on A5 — sent immediately per-frame, BEFORE feeding the
        frame into self._frame_collector (which may synchronously trigger the
        full dispatch+response chain once the last expected frame arrives).
        Ordering confirmed from a real capture (onboarding-real-mera.md,
        14:11:09.414-.564): ack-FIRST, then CONS, then ack-CONS, and only
        *then* the response — the ack always precedes any response, even for
        the frame that completes the transaction. Required, not optional:
        without the ack after FIRST, the real app resends FIRST a few times
        and gives up rather than ever sending CONS."""
        control = _FrameFactory.BuildControlFrame(bytes(self._request_ack_bitmap)).serialize()
        self._mock._log("→", f"CTRL ack (bitmap=0x{self._request_ack_bitmap[0]:02x})")
        await self._push_method(0)(bytes(control))

    @characteristic(_WRITE_0_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_0(self, options):
        return bytes(20)

    @write_0.setter
    def write_0(self, value, options):
        raw = bytes(value)
        self._log_write(0, raw)
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_1_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_1(self, options):
        return bytes(20)

    @write_1.setter
    def write_1(self, value, options):
        raw = bytes(value)
        self._log_write(1, raw)
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_2_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_2(self, options):
        return bytes(20)

    @write_2.setter
    def write_2(self, value, options):
        raw = bytes(value)
        self._log_write(2, raw)
        asyncio.ensure_future(self._handle_request(raw))

    @characteristic(_WRITE_3_UUID, CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_3(self, options):
        return bytes(20)

    @write_3.setter
    def write_3(self, value, options):
        raw = bytes(value)
        self._log_write(3, raw)
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
    """Remote Control pairing service (UUID 0xC526).

    RC does FIND_BY_TYPE_VALUE UUID=0xC526 and verifies the service exists
    before initiating BLE pairing (LL_ENC_REQ). `rc_stub` (0xC527) was this
    service's only characteristic until 2026-07-21 — an unconfirmed guess, never
    observed in any real capture. The five characteristics below ARE confirmed,
    from a real RC<->real-toilet capture watching SMP pairing live (the sniffer
    caught the LTK and decrypted everything after — see docs/developer/
    mock-geberit-mera.md §"Button-press/release timing", "Major breakthrough,
    2026-07-21" for the full capture analysis this implements):

      0x25dcdfd2-...  NOTIFY  (CCCD enabled first, before either WRITE below)
      0x867710fb-...  WRITE   real payload: UTF-16BE text "      Pairing ok      "
      0x0e069b0a-...  WRITE   real payload: all zero bytes except one 0x7B at
                               offset 2 — meaning not understood, mock accepts
                               and no-ops it like the rest
      0x7152f4a9-...  role never observed in either capture — read-only stub
      0x464ead99-...  role never observed in either capture — read-only stub

    then, in the real captures, a NOTIFY on 0x5a4d406b-... (CCCD enabled
    earlier, before either WRITE) with payload `03 02` — sent here once the
    0x25dcdfd2 CCCD is confirmed enabled, mirroring the real device's observed
    order (see _maybe_send_ack below). Untested whether this makes the RC
    progress any further than the stub did — the real device's actual trigger
    condition for this reply is not confirmed, only its observed position in
    the sequence.
    """

    def __init__(self, mock: "MeraMock"):
        super().__init__("0000c526-0000-1000-8000-00805f9b34fb", True)
        self._mock = mock
        self._notify_1a_iface = None   # wired after register() — CCCD gate for the ack below
        self._notify_26_iface = None
        self._ack_sent = False

    def wire_notify_1a(self, iface) -> None:
        self._notify_1a_iface = iface

    def wire_notify_26(self, iface) -> None:
        self._notify_26_iface = iface

    @characteristic("0000c527-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def rc_stub(self, options):
        return b""

    @characteristic("25dcdfd2-8867-48da-b1d6-1b5985c4f259", CharFlags.NOTIFY)
    def notify_1a(self, options):
        return bytes(20)

    @characteristic("5a4d406b-b210-47ba-b7e6-db6b9f2e9997", CharFlags.NOTIFY)
    def notify_26(self, options):
        return bytes(20)

    @characteristic("867710fb-5e31-49ba-84e0-a10d5d832ad7", CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_pairing_status_text(self, options):
        return bytes(20)

    @write_pairing_status_text.setter
    def write_pairing_status_text(self, value, options):
        raw = bytes(value)
        try:
            text = raw.decode("utf-16-be", errors="replace")
        except Exception:
            text = raw.hex()
        self._log_write_rc("0x867710fb (status text)", raw, text)

    @characteristic("0e069b0a-967c-4002-91ac-1e51906a84b2", CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_pairing_status_code(self, options):
        return bytes(20)

    @write_pairing_status_code.setter
    def write_pairing_status_code(self, value, options):
        raw = bytes(value)
        self._log_write_rc("0x0e069b0a (status code)", raw, None)

    @characteristic("7152f4a9-6523-4517-80a2-96d8b9273538", CharFlags.READ)
    def rc_stub_1f(self, options):
        return b""

    @characteristic("464ead99-ec2c-49d4-a186-af6ff8979a96", CharFlags.READ)
    def rc_stub_21(self, options):
        return b""

    def _log_write_rc(self, label: str, raw: bytes, text) -> None:
        if text:
            self._mock._log("·", f"RC write {label}: {raw.hex()}  (decoded: {text!r})")
        else:
            self._mock._log("·", f"RC write {label}: {raw.hex()}")
        asyncio.ensure_future(self._maybe_send_ack())

    async def _maybe_send_ack(self) -> None:
        """Send the confirmed real-device ack (NOTIF 03 02 on 0x5a4d406b) once
        the 0x25dcdfd2 CCCD is enabled — matching the real captures' observed
        order (CCCD-0x5a4d406b enabled first, then both WRITEs, then
        CCCD-0x25dcdfd2 enabled, then this notify). Called from both write
        setters, right after the SECOND write in the real sequence — but the
        real 0x25dcdfd2 CCCD-enable comes from the client AFTER both writes,
        not before, so this polls briefly for it (same pattern as the A6-CCCD
        wait in _send_info_frame_burst) rather than checking once and giving
        up. Fires at most once per connection; _mock clears _ack_sent on
        disconnect."""
        if self._ack_sent:
            return
        if self._notify_26_iface is None or not getattr(self._notify_26_iface, "_notify", False):
            return   # real order: 0x5a4d406b CCCD is enabled before either write reaches here
        for _ in range(30):          # max 3 s, matches the A6-CCCD wait
            if self._ack_sent:
                return               # a concurrent call already sent it
            if self._notify_1a_iface is not None and getattr(self._notify_1a_iface, "_notify", False):
                break
            await asyncio.sleep(0.1)
        else:
            self._mock._log("·", "RC pairing ack: 0x25dcdfd2 CCCD not enabled within 3s — not sending ack")
            return
        self._ack_sent = True
        frame = bytes.fromhex("0302")
        self._mock._log("→", f"RC pairing ack: NOTIFY 0x5a4d406b ({frame.hex()})")
        try:
            if hasattr(self._notify_26_iface, "changed"):
                self._notify_26_iface.changed(frame)
            else:
                self._notify_26_iface.emit_properties_changed({"Value": Variant("ay", list(frame))})
        except Exception as e:
            self._mock._log("·", f"WARNING: RC pairing ack notify failed: {e}")


class _RCAncillaryService8A30(Service):
    """Service UUID 0x8A30 — RC does FIND_BY_TYPE_VALUE for this before 0xC526's
    contents matter (confirmed real capture, 2026-07-21). No characteristic UUID
    was ever observed under it in either capture — deliberately zero characteristics
    (matches the real device's own apparent structure better than a fabricated
    stub would; bluez_peripheral.gatt.service.Service tolerates an empty
    _characteristics list). The FIND_BY_TYPE_VALUE existence check only needs the
    service declaration itself, not any characteristic under it."""

    def __init__(self):
        super().__init__("00008a30-0000-1000-8000-00805f9b34fb", True)


class _RCAncillaryServiceE0DB(Service):
    """Service UUID 0xE0DB — RC does FIND_BY_TYPE_VALUE for this alongside 0x8A30
    (confirmed real capture, 2026-07-21). One characteristic UUID falls within its
    declared handle range (ends 0x0018): 0x1db512c1-..., role never observed in
    either capture — read-only stub."""

    def __init__(self):
        super().__init__("0000e0db-0000-1000-8000-00805f9b34fb", True)

    @characteristic("1db512c1-2aa1-45d7-894e-1e9441bc8389", CharFlags.READ)
    def stub(self, options):
        return b""


# ---- Advertisement ----
class _MeraAdvertisement(Advertisement):
    """Advertisement matching the real Mera Comfort BLE payload (11-byte total).

    Reverted 2026-07-18 (see docs/developer/nrf-ble-analyze-completeness-audit.md and
    memory for the full history) — a same-day attempt to split this into two separate
    company-ID-keyed Manufacturer Specific Data entries (matching how the real device
    splits them across ADV_IND/SCAN_RSP) was reverted the same day. The real device's
    ADV_IND carries exactly ONE manufacturer entry (confirmed via `tools/nrf-ble-
    analyze.py --adv`); the second entry (RS firmware tail) lives in its SCAN_RSP.
    bluez_peripheral/BlueZ gives no control over which PDU a manufacturerData dict's
    entries land in — handing it two company-ID keys resulted in BOTH landing in
    ADV_IND, a packet shape neither the real device nor this mock had ever sent
    before, and onboarding failed completely (zero LE Connection Complete events)
    immediately after shipping it. Reverted to the single-entry structure below
    rather than half-matching the real device in a new, untested way.

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

    IsEmergencyConnectPermitted (company ID low byte -> 0xAA) — re-added 2026-07-20,
    isolated from the entry-split this time: the STRUCTURE stays single-entry, exactly as
    it's been since the 2026-07-18 revert; only the dict's KEY is now conditional on
    state_b, the same way the payload already is. This is a deliberately minimal, isolated
    change — nothing about _update_advert()'s unregister/re-register mechanism, the
    single-entry shape, or the name/service-UUID fields changes. Confirmed from real
    captures (docs/developer/ble-advertising-button-press-confirmation.md) that on real
    hardware the company-ID flip lags the payload flip by ~0.1-5s rather than being
    simultaneous; tying them 1:1 here is a first test of whether BlueZ/iOS even tolerates
    the company-ID value changing at all, not a claim that this matches real timing yet.

    AquaCleanProduct.cs UpdateAdvertisingData():
      IsButtonPressed             = (full_data[2] == 1)    <- payload[0] = state_b

    The iOS 15-second scan loop selects a device only when IsButtonPressed=True.
    _update_advert(1) sets state_b=0x01 -> full_data[2]=0x01 -> triggers Connection 1.

    `article` used to be a module-level constant; now a constructor arg so each
    MeraMock instance can (eventually) advertise its own identity. `rs_prefix` mirrors
    whichever firmware profile is currently active (self._FW_COMPONENT_VERSIONS[1])
    instead of a hardcoded "30" — kept from the same-day change since it's orthogonal
    to the entry-count revert (doesn't change entry count).
    """

    def __init__(self, article: str, state_b: int = 0, rs_prefix: str = "30"):
        company_id = 0x01AA if state_b else 0x0100
        super().__init__(
            "Geberit AC PRO",                            # name -> SCAN_RSP (BlueZ splits automatically)
            ["00003ea0-0000-1000-8000-00805f9b34fb"],    # service_uuids -> ADV_IND
            appearance=0,
            timeout=0,
            manufacturerData={
                company_id: bytes([state_b]) + article.encode("ascii") + bytes([0x00]) + rs_prefix.encode("ascii")
            },
        )


# ---- Web UI ----
_HTML = """\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Mera Mock {version}</title>
  <link rel="stylesheet" href="/static/mock-controls.css">
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
    #press-btn.pressed {{ background: #ffdd00; color: #1a1a2e; box-shadow: 0 0 8px #ffdd00; }}
    #press-btn.pressed:hover {{ background: #ffe64d; }}
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
    BLE: <span class="badge {conn_cls}" id="conn-badge">{conn_txt}</span>
    &nbsp;
    Button: <span class="badge {btn_cls}" id="btn-badge">{btn_txt}</span>
    &nbsp;
    <span class="mc-hint" id="btn-times">{btn_times}</span>
  </p>
  <h2>Controls</h2>
  <form method="post" action="/button">
    <button type="submit" class="{btn_press_cls}" id="press-btn">Press Button (confirm pairing)</button>
  </form>
  <form method="post" action="/clear-log" style="display:inline">
    <button type="submit" class="danger">Clear log</button>
  </form>
  <h2>Settings</h2>
  <div id="mc-root"></div>
  <h2>Session log</h2>
  <div class="log" id="log-div">{log_html}</div>
  <script src="/static/mock-controls.js"></script>
  <script>
    mcRenderSettingsTable(document.getElementById('mc-root'), {settings_json});
    mcConnectSSE('/events', function (data) {{
      if (data.settings) mcRenderSettingsTable(document.getElementById('mc-root'), data.settings);
      if (data.log_html != null) document.getElementById('log-div').innerHTML = data.log_html;
      if (data.connected != null) {{
        var cb = document.getElementById('conn-badge');
        cb.className = 'badge ' + (data.connected ? 'ok' : 'idle');
        cb.textContent = data.connected ? 'Connected' : 'Idle';
      }}
      if (data.button_pressed != null) {{
        var bb = document.getElementById('btn-badge');
        bb.className = 'badge ' + (data.button_pressed ? 'ok' : 'warn');
        bb.textContent = data.button_pressed ? 'Pressed' : 'Waiting';
        var pb = document.getElementById('press-btn');
        pb.className = data.button_pressed ? 'pressed' : '';
      }}
      if (data.button_pressed_at !== undefined) {{
        var bt = document.getElementById('btn-times');
        var text = '';
        if (data.button_pressed_at) {{
          text = 'pressed ' + data.button_pressed_at;
          if (data.button_released_at) text += ' · released (auto) ' + data.button_released_at;
        }}
        bt.textContent = text;
      }}
    }});
  </script>
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
            mock_logging.set_log_dir(state_dir)

        # ---- identity (was module-level constants; instance now so a future
        # variant/model registry can override per instance without touching
        # this class again). All writable through the webui + persisted —
        # see _IDENTITY_ATTR_MAP / _FACTORY_IDENTITY. ----
        for _field, _attr in _IDENTITY_ATTR_MAP.items():
            setattr(self, _attr, _FACTORY_IDENTITY[_field])

        # SPL values for indices that need non-zero defaults (none currently needed).
        self._SPL_MERA_VALUES: dict = {}

        # Values from real Mera Comfort onboarding capture (onboarding-real-mera_timing.md).
        # Hardcoded real-device defaults — overridden below by anything already
        # persisted for this device (startup never overwrites an existing store,
        # requirements doc §5). ACTIVE_PROFILE_SETTINGS is the write-target for
        # proc 0x08 (SetActiveProfileSetting) — session-only, never persisted,
        # same as ACTIVE_COMMON_SETTINGS below; it has no confirmed getter of its
        # own so it's never read back, only written.
        self._ACTIVE_PROFILE_SETTINGS  = dict(_DEFAULT_PROFILE_SETTINGS)
        self._STORED_PROFILE_SETTINGS  = dict(_DEFAULT_PROFILE_SETTINGS)
        self._STORED_COMMON_SETTINGS   = dict(_DEFAULT_COMMON_SETTINGS)
        self._PER_NODE_PROFILE_SETTINGS = {
            0x00: 1, 0x01: 1, 0x02: 2, 0x03: 1, 0x04: 2,
            0x05: 1, 0x06: 4, 0x07: 0, 0x08: 3, 0x09: 1, 0x0d: 1,
        }
        # Per-instance overlay of the module-level fallback defaults — lets a
        # future firmware-update simulation durably change what GetFirmwareVersionList
        # reports, and keeps two MeraMock instances (different adapters) independent.
        self._FW_COMPONENT_VERSIONS = dict(_FW_COMPONENT_VERSIONS)

        # Persisted values win over the hardcoded defaults above — this is what
        # makes settings survive a mock restart (requirements doc §0/§5).
        persisted = mock_persistence.load_all("mera", self._adapter_tag)
        for key, value in persisted.items():
            namespace, _, idx_str = key.partition(":")
            if namespace == "identity":
                attr = _IDENTITY_ATTR_MAP.get(idx_str)
                if attr:
                    setattr(self, attr, value)
                continue
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            if namespace == "common_setting":
                self._STORED_COMMON_SETTINGS[idx] = value
            elif namespace == "profile_setting":
                self._STORED_PROFILE_SETTINGS[idx] = value
            elif namespace == "fw":
                self._FW_COMPONENT_VERSIONS[idx] = tuple(value)

        # Active CommonSetting store (proc 0x0A/0x0B) — session-scoped, seeded
        # from Stored (including any persisted overrides just applied above) at
        # mock startup, held only in memory thereafter, never persisted. Mirrors
        # the real device: a power-cycle re-derives Active from Stored NVM.
        # Simplification: seeded once here, not re-seeded per BLE session.
        self._ACTIVE_COMMON_SETTINGS = dict(self._STORED_COMMON_SETTINGS)

        # ---- mutable session/connection state (was module-level globals) ----
        self._session_log: list = []
        self._sse_queues: list = []  # webui /events subscribers (docs/developer/mock-service-requirements.md §6)
        self._button_pressed = False
        self._button_pressed_at = None   # "%H:%M:%S" — when the webui button was clicked
        self._button_released_at = None  # "%H:%M:%S" — when the mock auto-released it (see _send_info_frame_burst)
        self._registration_level: int = 0   # 0=Not registered, 1=Private, 2=Public — real device returns 0 during onboarding
        self._connected = False
        self._connection_gen = 0     # incremented on each new connection; guards stale burst tasks
        self._current_device_path = None  # D-Bus path of the currently connected device
        self._fw_update_state = "idle"  # idle | started | done | rebooting — Phase 9b ctx=0x40 simulation
        self._gatt_service = None  # wired by MeraService.__init__ — lets async tasks push spontaneous notifications
        self._advert = None          # current _MeraAdvertisement instance
        self._advert_bus = None      # D-Bus connection stored for advert updates
        self._advert_adapter = None  # Adapter stored for advert updates
        self._advert_lock: asyncio.Lock | None = None  # created in run(), needs a running loop
        self._bus = None             # system D-Bus connection; set in run()
        self._adapter_path = None    # set in run(); reused by _apply_device_name_to_adapter()

        # ---- per-instance logger — console + per-device file + combined file,
        # device tag at a fixed position in every line (docs/developer/
        # mock-service-requirements.md §7); shared with AlbaMock via mock_logging.py ----
        self.logger = mock_logging.get_device_logger("mera", self.adapter)

    def _hci_index(self) -> str:
        """BlueZ node name (e.g. "hci1") -> HCI index string ("1") for btmgmt/sysfs
        calls that address an adapter by index rather than by D-Bus path. Defaults
        to "0" when no specific adapter was requested, matching the original
        script's unconditional hci0 (which only ever ran a single instance)."""
        if self.adapter and self.adapter.startswith("hci") and self.adapter[3:].isdigit():
            return self.adapter[3:]
        return "0"

    def _set_pairable_on_verified(self) -> None:
        """Set the adapter pairable=on and verify it actually took effect —
        added 2026-07-19 after a report of a clean Home App onboarding being read as
        proof pairable=on succeeded, which it isn't: Home App onboarding never attempts
        BLE pairing regardless of adapter state (see run()'s comment above this call),
        so a smooth onboarding is not evidence either way. Checks the command's own
        return code, then reads back `btmgmt info` to confirm "bondable" — the mgmt
        setting name `pairable` is an alias for — is actually listed under "current
        settings", rather than trusting the command silently."""
        idx = self._hci_index()
        result = subprocess.run(
            ["btmgmt", "-i", idx, "pairable", "on"], capture_output=True, text=True,
        )
        if result.returncode != 0:
            self.logger.warning(
                "btmgmt pairable on FAILED (exit %s): %s",
                result.returncode, (result.stderr or result.stdout).strip(),
            )
            return

        info = subprocess.run(
            ["btmgmt", "-i", idx, "info"], capture_output=True, text=True,
        )
        settings_line = next(
            (line for line in info.stdout.splitlines() if "current settings" in line.lower()),
            None,
        )
        if settings_line is None:
            self.logger.warning(
                "Could not verify pairable state — `btmgmt info` output had no "
                "'current settings' line (exit %s): %s",
                info.returncode, (info.stderr or info.stdout).strip(),
            )
        # `btmgmt info` reports this setting as "bondable" — "pairable" is only the
        # btmgmt *command* name (an alias, confirmed via `btmgmt --help`: both
        # "bondable" and "pairable" commands say "Toggle bondable state"); the
        # settings list itself never contains the literal word "pairable".
        elif "bondable" in settings_line.lower():
            self.logger.info("Adapter confirmed pairable=on (verified via btmgmt info)")
        else:
            self.logger.warning(
                "btmgmt pairable on reported success but adapter is NOT pairable "
                "per readback — settings line: %s", settings_line.strip(),
            )

    def _log(self, direction: str, msg: str) -> None:
        entry = (time.strftime("%H:%M:%S"), direction, msg)
        self._session_log.append(entry)
        if len(self._session_log) > 200:
            self._session_log.pop(0)
        self.logger.info("  %s %s", direction, msg)
        self._broadcast_state_nowait()

    def _broadcast_state_nowait(self) -> None:
        """Push current state to every connected webui SSE client (/events).
        Called from _log() — nearly every state-mutating path (settings
        writes, button press, general BLE activity) already logs through it,
        so this covers them for free instead of scattering broadcast calls
        everywhere. Sync (put_nowait, not await) since _log() is sync and
        called from many non-async contexts; safe because these are unbounded
        asyncio.Queue instances (put_nowait never blocks/raises on them)."""
        if not self._sse_queues:
            return
        data = {
            "type": "state",
            "settings": self._settings_table_data(),
            "connected": self._connected,
            "button_pressed": self._button_pressed,
            "button_pressed_at": self._button_pressed_at,
            "button_released_at": self._button_released_at,
            "log_html": self._render_log(),
        }
        for q in list(self._sse_queues):
            q.put_nowait(data)

    def _button_times_text(self) -> str:
        """Human-readable press/release history for the webui (2026-07-18 ask) —
        distinguishes "I clicked press" from "the mock auto-released it" (see
        _send_info_frame_burst — the mock has no physical button, so release is
        triggered by BLE-connection progress, not a user action)."""
        if not self._button_pressed_at:
            return ""
        text = f"pressed {self._button_pressed_at}"
        if self._button_released_at:
            text += f" · released (auto) {self._button_released_at}"
        return text

    def _rs_fw_prefix(self) -> str:
        """2-char RS firmware major-version prefix for the advertisement's second
        Manufacturer Specific Data entry — mirrors whichever component-1 firmware
        version is currently active instead of a hardcoded constant."""
        v1, v2, _build = self._FW_COMPONENT_VERSIONS.get(1, (0x33, 0x30, 0))
        return chr(v1) + chr(v2)

    async def _apply_device_name_to_adapter(self) -> None:
        """Push self._DEVICE_NAME to the adapter's Alias property (BlueZ serves this
        as GATT 0x2a00, Device Name). Called at startup and best-effort from the
        webui identity write / factory-reset handlers — a live BlueZ connection is
        required (self._bus/self._adapter_path), so this is a no-op before run()
        has initialised them."""
        if not self._adapter_path or self._bus is None:
            return
        try:
            ai = await self._bus.introspect("org.bluez", self._adapter_path)
            ap = self._bus.get_proxy_object("org.bluez", self._adapter_path, ai)
            props = ap.get_interface("org.freedesktop.DBus.Properties")
            await props.call_set("org.bluez.Adapter1", "Alias", Variant("s", self._DEVICE_NAME))
            self.logger.info("Adapter alias set to %r  (GATT 0x2a00 Device Name)", self._DEVICE_NAME)
        except Exception as e:
            self.logger.warning("could not set adapter alias: %s", e)

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
            self._advert = _MeraAdvertisement(self._ARTICLE, state_b, rs_prefix=self._rs_fw_prefix())
            try:
                await self._advert.register(self._advert_bus, self._advert_adapter)
                company_id = 0x01AA if state_b else 0x0100
                self._log("·", f"Advertisement updated: byte[2]=0x{state_b:02X}  IsButtonPressed={bool(state_b)}"
                                f"  company=0x{company_id:04X}  IsEmergencyConnectPermitted={bool(state_b)}")
            except Exception as e:
                self.logger.error("advert re-register failed: %s", e)

    # ---- Procedure dispatch ----

    def _set_registration_level(self, level: int) -> None:
        self._registration_level = level

    def _dispatch(self, ctx: int, proc: int, args: bytes) -> list:
        """Return list of 20-byte frames for the response to proc."""
        self._log("←", f"proc=0x{proc:02X} ctx={ctx} args={args.hex() if args else '(none)'}")

        response_node_id = 0x01

        # ctx=0x40 (plus its ctx=0x00/proc=0x01 companion frame) is the
        # firmware-update sequence — proc 0x52/0x53 collide numerically with
        # SetStoredCommonSetting/GetStoredProfileSetting below, which only
        # apply under the default ctx. Must be checked first.
        if ctx == 0x40 or (ctx == 0x00 and proc == 0x01):
            result = self._proc_fw_update(ctx, proc, args)
        elif proc == 0x82:              # GetDeviceIdentification
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
          SapNumber[12] + SerialNumber[20] + ProductionDate[10] + Description[40]

        Field names corrected 2026-07-18: offset 0 is SapNumber (dotted format, e.g.
        "146.21x.xx.1"), not "ArticleNumber" as this method and mock-geberit-mera.md
        previously labeled it — confirmed by the app's own DeviceIdentification log
        line and docs/mqtt.md's dotted-format Identification/SapNumber topic.
        """
        def _pad(s: str, n: int) -> bytes:
            b = s.encode("ascii")[:n]
            return b + bytes(n - len(b))
        return (
            _pad(self._SAP_NUMBER, 12)        # SapNumber      offset  0
            + _pad(self._SERIAL_NUMBER, 20)   # SerialNumber   offset 12
            + _pad(self._PRODUCTION_DATE, 10) # ProductionDate offset 32
            + _pad(self._DESCRIPTION, 40)     # Description    offset 42
        )                                     # total = 82 bytes

    def _proc_05(self) -> bytes:
        """GetNodeInventory: count(1) + node IDs + zero-pad to 129 bytes total."""
        payload = bytes([len(_NODE_IDS)]) + _NODE_IDS
        return payload + bytes(129 - len(payload))

    def _proc_81(self) -> bytes:
        """GetSOCApplicationVersions: major_str(2) + minor_byte + null = 4 bytes.
        Writable via webui/persistence (self._SOC_VERSION, "MM.mm" text) — real
        device sends "10.18" as b"10" + 0x12 + 0x00.
        """
        return _encode_soc_version(self._SOC_VERSION)

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
        served = []
        for cid in comp_ids:
            v1, v2, build = self._FW_COMPONENT_VERSIONS.get(cid, (0x30, 0x30, 0))
            records += bytes([cid, v1, v2, build, 0])
            served.append(f"{cid}={_format_fw_version(v1, v2, build)}")
        self._log("→", f"GetFirmwareVersionList served: {', '.join(served)}")
        return records + bytes(max(0, 61 - len(records)))  # always pad to 61 bytes

    def _set_fw_version(self, component_id: int, v1: int, v2: int, build: int) -> None:
        """Update one component's reported firmware version and persist it —
        the write hook a firmware-update-process simulation calls after a
        simulated OTA completes (mirrors the real RS28.0->RS30.0 capture in
        memory/mera-firmware-update-ble-protocol.md). Not called anywhere yet
        (docs/developer/mock-service-requirements.md Phase 9b)."""
        self._FW_COMPONENT_VERSIONS[component_id] = (v1, v2, build)
        mock_persistence.save("mera", self._adapter_tag, f"fw:{component_id}", [v1, v2, build])
        self._log("·", f"SetFirmwareVersion component={component_id} -> ({v1},{v2},{build}) — persisted")

    def _current_firmware_profile(self) -> str:
        """Which canonical profile (if any) the live firmware versions match —
        derived from data already held, no separate "current profile" flag to
        keep in sync. Compares component 1 only (unique per profile) with a
        fallback for anything else (e.g. individual per-component writes from a
        future Phase 9b OTA simulation that don't exactly match either snapshot)."""
        current_c1 = self._FW_COMPONENT_VERSIONS.get(1)
        for name, profile in _FW_PROFILES.items():
            if profile[1] == current_c1:
                return name
        return "custom"

    def _apply_firmware_profile(self, profile: str) -> None:
        """Webui firmware-profile selector write path (docs/developer/
        mock-service-requirements.md §6) — applies every component in the
        chosen canonical snapshot via the existing per-component write hook,
        so persistence/logging stay identical to a real Phase 9b OTA simulation
        writing one component at a time."""
        for component_id, (v1, v2, build) in _FW_PROFILES[profile].items():
            self._set_fw_version(component_id, v1, v2, build)

    def _proc_fw_update(self, ctx: int, proc: int, args: bytes) -> bytes:
        """Firmware-update proc sequence (Phase 9b) — see .claude/rules/
        ble-protocol.md "Firmware update procedures (ctx=0x40)". State
        machine (idle -> started -> done -> rebooting -> idle) driven by
        timers, plus spontaneous progress-notify frames on A5 during the busy
        window (_fw_update_run) and byte-count observability logging for the
        bulk firmware-binary writes the app also sends on A1-A4 during that
        same window (MeraService._log_write) — the generic frame parser
        tolerates that binary without inspecting/validating its content."""
        if ctx == 0x00 and proc == 0x01:
            return b""  # companion heartbeat frame — ACK-only
        if proc == 0x00:                # background keepalive, unrelated to update state
            return _FW_UPDATE_KEEPALIVE
        if proc == 0x52:                # StartFirmwareUpdate
            if self._fw_update_state == "idle":
                self._fw_update_state = "started"
                if self._gatt_service is not None:
                    self._gatt_service._fw_bulk_bytes = {"A1": 0, "A2": 0, "A3": 0, "A4": 0}
                self._log("·", f"Firmware update started (simulated, {_FW_UPDATE_BUSY_SECONDS}s)")
                asyncio.ensure_future(self._fw_update_run(auto_finalize=False))
            return b""
        if proc == 0x53:                # poll: 05=busy, 06=done
            return bytes([0x06 if self._fw_update_state == "done" else 0x05])
        if proc == 0x04:                # benign ping; finalize trigger once done
            if self._fw_update_state == "done":
                self._fw_update_state = "rebooting"  # guard against double-scheduling on repeat pings
                asyncio.ensure_future(self._fw_update_finalize())
            return b""
        self._log("·", f"  unknown ctx=0x40 proc 0x{proc:02X} — returning empty OK")
        return b""

    async def _fw_update_run(self, auto_finalize: bool = False) -> None:
        """auto_finalize=True for the webui manual trigger — that flow has no
        real app cooperating to send the ctx=0x40/proc=0x04 finalize ping, so
        it would otherwise sit at "done" forever. The real proc=0x52 path
        keeps auto_finalize=False, matching real protocol fidelity (finalize
        only on the app's own follow-up ping)."""
        interval = _FW_UPDATE_BUSY_SECONDS / _FW_UPDATE_PROGRESS_TICKS
        progress = 0
        for _ in range(_FW_UPDATE_PROGRESS_TICKS):
            await asyncio.sleep(interval)
            progress += _FW_UPDATE_PROGRESS_STEP
            if self._gatt_service is not None:
                await self._gatt_service.push_notify(_build_progress_frame(progress))
        self._fw_update_state = "done"
        self._log("·", "Firmware update flash window complete (simulated) — waiting for finalize")
        if auto_finalize:
            self._fw_update_state = "rebooting"  # guard against a real proc=0x04 double-scheduling finalize
            asyncio.ensure_future(self._fw_update_finalize())

    async def _fw_update_finalize(self) -> None:
        self._log("·", "Firmware update finalize — simulating device reboot")
        self._apply_firmware_profile("rs30")
        device_path = self._current_device_path
        if device_path and self._bus:
            try:
                dev_intro = await self._bus.introspect("org.bluez", device_path)
                dev_proxy = self._bus.get_proxy_object("org.bluez", device_path, dev_intro)
                await dev_proxy.get_interface("org.bluez.Device1").call_disconnect()
            except Exception as exc:
                self.logger.warning("simulated-reboot disconnect failed: %s", exc)
        await asyncio.sleep(_FW_UPDATE_REBOOT_SECONDS)
        self._fw_update_state = "idle"
        self._log("·", "Simulated reboot complete — device reachable again")

    def _proc_86(self) -> bytes:
        """GetDeviceInitialOperationDate: UTF-8 date string, no null terminator.
        Writable via webui/persistence (self._INITIAL_OPERATION_DATE)."""
        return self._INITIAL_OPERATION_DATE.encode("ascii")

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

    def _write_stored_common_setting(self, setting_id: int, value: int) -> None:
        """Shared by proc 0x52 (BLE) and the webui's /settings/common/{id} route
        — one write path, so both surfaces stay consistent (docs/developer/
        mock-service-requirements.md §6)."""
        self._STORED_COMMON_SETTINGS[setting_id] = value
        mock_persistence.save("mera", self._adapter_tag, f"common_setting:{setting_id}", value)
        self._log("·", f"SetStoredCommonSetting id={setting_id} value={value} — persisted")

    def _proc_52(self, args: bytes) -> bytes:
        """SetStoredCommonSetting — persisted immediately via mock_persistence.py.
        Requires a power-cycle to take effect on a real device; the mock applies
        it to _STORED_COMMON_SETTINGS right away since there is no separate
        "pending write" state to model here."""
        setting_id, value = self._parse_set_setting_args(args)
        if setting_id is not None:
            self._write_stored_common_setting(setting_id, value)
        return b""

    def _proc_53(self, args: bytes) -> bytes:
        """GetStoredProfileSetting: args[0] = setting ID, returns 16-bit LE value."""
        setting_id = args[0] if args else 0
        value = self._STORED_PROFILE_SETTINGS.get(setting_id, 0)
        return bytes([value & 0xFF, (value >> 8) & 0xFF])

    def _write_stored_profile_setting(self, setting_id: int, value: int) -> None:
        """Shared by proc 0x54 (BLE) and the webui's /settings/profile/{id}
        route — one write path, so both surfaces stay consistent."""
        self._STORED_PROFILE_SETTINGS[setting_id] = value
        mock_persistence.save("mera", self._adapter_tag, f"profile_setting:{setting_id}", value)
        self._log("·", f"SetStoredProfileSetting id={setting_id} value={value} — persisted")

    def _proc_54(self, args: bytes) -> bytes:
        """SetStoredProfileSetting — persisted immediately via mock_persistence.py."""
        setting_id, value = self._parse_set_setting_args(args)
        if setting_id is not None:
            self._write_stored_profile_setting(setting_id, value)
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
        # A connection-interval-shortening request used to be attempted here (via
        # org.bluez.Device1.UpdateConnectionParameters) to get multi-frame proc
        # responses delivered within iOS's ~54ms FlowControl ACK window at the
        # default ~30ms CI. Removed 2026-07-17: that D-Bus method has never
        # existed on Device1 (confirmed against BlueZ's documented API), so this
        # silently failed on every single connection since it was written — see
        # docs/developer/mock-geberit-mera.md § "Connection-interval request was
        # always dead code" for the full investigation and why it wasn't the
        # actual cause of the periodic-disconnect mystery.
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
            self._button_released_at = time.strftime("%H:%M:%S")
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

    def _settings_table_data(self) -> dict:
        """Build the metadata+value JSON mock-controls.js needs to render the
        settings table — docs/developer/mock-service-requirements.md §6."""
        def _rows(meta, values, url_prefix):
            rows = []
            for setting_id, (name, kind, mn, mx, options) in meta.items():
                row = {
                    "id": setting_id, "name": name, "kind": kind,
                    "value": values.get(setting_id, 0), "min": mn, "max": mx,
                    "writeUrl": f"{url_prefix}/{setting_id}",
                }
                if kind == "select":
                    row["options"] = [{"value": v, "label": lbl} for v, lbl in options]
                elif kind == "swatch":
                    row["options"] = [{"value": v, "label": lbl, "color": color} for v, lbl, color in options]
                rows.append(row)
            return rows

        profile_row = {
            "id": "profile",
            "name": "Firmware Profile",
            "kind": "select",
            "value": self._current_firmware_profile(),
            "writeUrl": "/settings/firmware-profile",
            "options": [
                {"value": "rs30", "label": "RS30.0 TS206 (current)"},
                {"value": "rs28", "label": "RS28.0 TS199 (needs update)"},
            ],
        }
        trigger_row = {
            "id": "trigger-update",
            "name": "Manual Trigger",
            "kind": "button",
            "label": "Trigger Update" if self._fw_update_state == "idle" else f"Update: {self._fw_update_state}",
            "value": None,
            "writeUrl": "/settings/trigger-firmware-update",
        }
        fw_rows = [profile_row, trigger_row] + [
            {
                "id": cid,
                "name": _FW_COMPONENT_NAMES.get(cid, f"Component {cid}"),
                "kind": "text",
                "value": _format_fw_version(v1, v2, build),
                "writeUrl": f"/settings/fw-component/{cid}",
            }
            for cid, (v1, v2, build) in sorted(self._FW_COMPONENT_VERSIONS.items())
        ]

        identity_rows = [
            {
                "id": key,
                "name": label,
                "kind": "text",
                "value": getattr(self, _IDENTITY_ATTR_MAP[key]),
                "max": max_len,
                "hint": f"real: {_IDENTITY_REAL_REFERENCE[key]}",
                "writeUrl": f"/settings/identity/{key}",
            }
            for key, label, max_len in _IDENTITY_FIELD_META
        ]
        identity_rows.append({
            "id": "variant",
            "name": "Variant (model byte)",
            "kind": "text",
            "value": f"0x{self._VARIANT:02X}",
            "hint": f"real: 0x{_IDENTITY_REAL_REFERENCE['variant']:02X}",
            "writeUrl": "/settings/identity/variant",
        })

        reset_row = {
            "id": "factory-reset",
            "name": "Factory Reset",
            "kind": "button",
            "label": "Reset to Factory Settings",
            "value": None,
            "writeUrl": "/settings/factory-reset",
            "danger": True,
        }

        return {"sections": [
            {"title": "Device Identity", "rows": identity_rows},
            {"title": "Profile Settings", "rows": _rows(_PROFILE_SETTING_META, self._STORED_PROFILE_SETTINGS, "/settings/profile")},
            {"title": "Common Settings", "rows": _rows(_COMMON_SETTING_META, self._STORED_COMMON_SETTINGS, "/settings/common")},
            {"title": "Firmware Versions", "rows": fw_rows},
            {"title": "Danger Zone", "rows": [reset_row]},
        ]}

    async def _handle_root(self, request):
        from aiohttp import web
        html = _HTML.format(
            version=_MOCK_VERSION,
            conn_cls="ok" if self._connected else "idle",
            conn_txt="Connected" if self._connected else "Idle",
            btn_cls="ok" if self._button_pressed else "warn",
            btn_txt="Pressed" if self._button_pressed else "Waiting",
            btn_press_cls="pressed" if self._button_pressed else "",
            btn_times=self._button_times_text(),
            log_html=self._render_log(),
            settings_json=json.dumps(self._settings_table_data()),
        )
        return web.Response(content_type="text/html", text=html)

    async def _handle_write_common_setting(self, request):
        from aiohttp import web
        setting_id = int(request.match_info["setting_id"])
        body = await request.json()
        self._write_stored_common_setting(setting_id, int(body["value"]))
        return web.json_response({"ok": True})

    async def _handle_write_profile_setting(self, request):
        from aiohttp import web
        setting_id = int(request.match_info["setting_id"])
        body = await request.json()
        self._write_stored_profile_setting(setting_id, int(body["value"]))
        return web.json_response({"ok": True})

    async def _handle_set_firmware_profile(self, request):
        from aiohttp import web
        body = await request.json()
        profile = body.get("value")
        if profile not in _FW_PROFILES:
            return web.json_response({"error": f"unknown profile {profile!r}"}, status=400)
        self._apply_firmware_profile(profile)
        return web.json_response({"ok": True})

    async def _handle_write_fw_component(self, request):
        """Free-text per-component firmware-version edit (webui) — e.g. "RS28.0
        TS199" — same _set_fw_version write path as the profile selector and
        the real Phase 9b OTA finalize, so single-variable experiments no
        longer require a code change/redeploy (2026-07-18 ask)."""
        from aiohttp import web
        try:
            component_id = int(request.match_info["component_id"])
        except ValueError:
            return web.json_response({"error": "invalid component id"}, status=400)
        if component_id not in self._FW_COMPONENT_VERSIONS:
            return web.json_response({"error": f"unknown component {component_id}"}, status=400)
        body = await request.json()
        try:
            v1, v2, build = _parse_fw_version(str(body.get("value", "")))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        self._set_fw_version(component_id, v1, v2, build)
        return web.json_response({"ok": True})

    async def _handle_write_identity(self, request):
        """Free-text/hex edit for one device-identity field (Article, SAP,
        Serial, ProductionDate, Description, Variant, InitialOperationDate,
        SOC Version) — 2026-07-18 ask: no more hardcoded-only identity data."""
        from aiohttp import web
        field = request.match_info["field"]
        attr = _IDENTITY_ATTR_MAP.get(field)
        if attr is None:
            return web.json_response({"error": f"unknown identity field {field!r}"}, status=400)
        body = await request.json()
        raw = body.get("value")
        if field == "variant":
            try:
                value = int(raw, 0) if isinstance(raw, str) else int(raw)
            except (TypeError, ValueError):
                return web.json_response({"error": f"invalid variant {raw!r} — expected 0-255 or 0xNN"}, status=400)
            if not (0 <= value <= 0xFF):
                return web.json_response({"error": "variant must be 0-255"}, status=400)
        else:
            value = str(raw)
            max_len = next((m for k, _, m in _IDENTITY_FIELD_META if k == field), None)
            if max_len and len(value.encode("ascii", errors="replace")) > max_len:
                return web.json_response({"error": f"{field} exceeds {max_len} bytes (proc 0x82 fixed-width field)"}, status=400)
        setattr(self, attr, value)
        mock_persistence.save("mera", self._adapter_tag, f"identity:{field}", value)
        self._log("·", f"SetIdentity field={field} value={value!r} — persisted")
        if field == "device_name":
            asyncio.ensure_future(self._apply_device_name_to_adapter())
        return web.json_response({"ok": True})

    async def _handle_factory_reset(self, request):
        """Reset ALL mutable/persisted mock state (identity, firmware versions,
        profile/common settings) back to known-good defaults — the "in case I
        mess it up completely" recovery button (2026-07-18 ask). Takes effect
        immediately, no restart needed. Firmware/profile/common defaults match
        v1.88.0b1; device_name is the 2026-07-18-corrected real value, not
        v1.88.0b1's literal (wrong) "ro" — see memory/mera-device-name-ro-is-wrong.md."""
        from aiohttp import web
        mock_persistence.reset("mera", self._adapter_tag)
        self._STORED_PROFILE_SETTINGS = dict(_DEFAULT_PROFILE_SETTINGS)
        self._STORED_COMMON_SETTINGS = dict(_DEFAULT_COMMON_SETTINGS)
        self._ACTIVE_COMMON_SETTINGS = dict(self._STORED_COMMON_SETTINGS)
        for component_id, (v1, v2, build) in _FW_COMPONENT_VERSIONS_FACTORY.items():
            self._set_fw_version(component_id, v1, v2, build)
        for field, value in _FACTORY_IDENTITY.items():
            setattr(self, _IDENTITY_ATTR_MAP[field], value)
            mock_persistence.save("mera", self._adapter_tag, f"identity:{field}", value)
        asyncio.ensure_future(self._apply_device_name_to_adapter())
        self._log("·", "Factory reset — all settings restored to known-good defaults")
        return web.json_response({"ok": True})

    async def _handle_trigger_fw_update(self, request):
        """Webui-only manual trigger for the Phase 9b firmware-update state
        machine — same entry point as ctx=0x40/proc=0x52 (StartFirmwareUpdate),
        for testing the progress-notify/poll/finalize flow without depending
        on the real app ever sending that write."""
        from aiohttp import web
        if self._fw_update_state != "idle":
            return web.json_response(
                {"error": f"update already in progress (state={self._fw_update_state})"},
                status=409,
            )
        self._fw_update_state = "started"
        if self._gatt_service is not None:
            self._gatt_service._fw_bulk_bytes = {"A1": 0, "A2": 0, "A3": 0, "A4": 0}
        self._log("·", f"Firmware update started (webui-triggered, simulated, {_FW_UPDATE_BUSY_SECONDS}s)")
        asyncio.ensure_future(self._fw_update_run(auto_finalize=True))
        return web.json_response({"ok": True})

    async def _handle_events(self, request):
        """SSE endpoint (mirrors aquaclean_console_app/RestApiService.py's
        /events — same asyncio.Queue-per-client + 30s heartbeat pattern),
        so the webui updates in place instead of the full-page reload it
        used before (docs/developer/mock-service-requirements.md §6)."""
        from aiohttp import web
        response = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )
        await response.prepare(request)
        queue: asyncio.Queue = asyncio.Queue()
        self._sse_queues.append(queue)
        try:
            queue.put_nowait({
                "type": "state",
                "settings": self._settings_table_data(),
                "connected": self._connected,
                "button_pressed": self._button_pressed,
                "button_pressed_at": self._button_pressed_at,
                "button_released_at": self._button_released_at,
                "log_html": self._render_log(),
            })
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    await response.write(f"data: {json.dumps(data)}\n\n".encode())
                except asyncio.TimeoutError:
                    await response.write(b": heartbeat\n\n")
        except ConnectionResetError:
            pass
        # CancelledError deliberately NOT caught here — swallowing it would make
        # this task look like it completed normally instead of being cancelled,
        # which can block asyncio.run()'s shutdown from seeing this task as
        # actually cancelled. finally still cleans up either way.
        finally:
            if queue in self._sse_queues:
                self._sse_queues.remove(queue)
        return response

    async def _handle_button(self, request, service: MeraService):
        from aiohttp import web
        if self._button_pressed:
            raise web.HTTPFound("/")
        self._button_pressed = True
        self._button_pressed_at = time.strftime("%H:%M:%S")
        self._button_released_at = None
        self._log("·", "Button pressed via web UI — advertisement byte[2]=0x01 (IsButtonPressed=True)")
        # No pairable toggle here — pairable is now set once at startup in run(), not per
        # button-press (v1.101.0b1). See run()'s comment for why.
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
                "button_pressed_at": self._button_pressed_at,
                "button_released_at": self._button_released_at,
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

        # Clear any bond records without restarting the daemon.
        # btmgmt unpair removes the device (including stored IRK) from BlueZ memory and
        # disk — iOS's RPA cannot resolve to a bonded identity, preventing auth
        # enforcement on CCCDs.  Skipping the daemon restart preserves the battery
        # plugin's per-session device cache (see test-infrastructure.md).
        #
        # btmgmt unpair alone is NOT enough to force a fresh GATT re-discovery: BlueZ
        # persists a SEPARATE per-device GATT attribute cache (Robust Caching / "Service
        # Changed" database hash) on disk, independent of the LTK/IRK/CSRK bonding keys
        # unpair removes. Confirmed 2026-07-21: a real RC bonded across many mock-version
        # changes on this same adapter kept getting served STALE cached service UUIDs and
        # characteristic labels from an much earlier GATT structure — visible in btmon as
        # "Vendor specific" UUIDs whose last 2 bytes matched the current intended aliases
        # but whose rest didn't, and standard-characteristic labels (Model/Serial/Firmware
        # Revision String) for handles this mock doesn't even define — while the RC itself,
        # trusting that stale structure, never discovered any of the actually-current
        # characteristics. rm -rf'ing the whole per-device directory (not just unpair)
        # removes both the bonding keys and that cache, forcing a genuine fresh discovery
        # on the next connection. See docs/developer/mock-geberit-mera.md §"Button-press/
        # release timing" for the full capture analysis that found this.
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
                        try:
                            shutil.rmtree(e)
                            self.logger.info(
                                "Unpaired and removed cached GATT database for: %s", e.name)
                        except OSError as exc:
                            self.logger.warning(
                                "Unpaired %s but could not remove cached GATT database: %s",
                                e.name, exc)

        # Re-enabled 2026-07-19 (v1.101.0b1) — RE-TEST BEFORE RELYING ON THIS.
        # pairable=on was reverted twice (v1.31.0, then again 2026-07-16 after commit
        # 2b565b0) because it caused BlueZ to send an unsolicited SMP Security Request to
        # iOS, surfaced as a system pairing dialog interrupting Home App onboarding. Both
        # times the actual mechanism was BlueZ's built-in Battery plugin: it reads Battery
        # Level from the connected iOS device on every connect; iOS refuses the
        # unauthenticated read; BlueZ escalates with the Security Request. This is a
        # Linux-BlueZ-host artifact, not a real-hardware behavior — confirmed separately:
        # onboarding-real-mera.pcapng (a full real Home App session) has zero LL_ENC_REQ
        # frames, so the Home App never attempts BLE pairing on real hardware either.
        # One day after the second revert, this Battery-plugin mechanism was independently
        # fixed at the systemd level on anneubuntu-studio (`--noplugin=battery` drop-in
        # override, memory/mera-mock-battery-plugin-fix.md) — but pairable=on was never
        # re-tried in the mock's own code against that fix until now. If the iOS pairing
        # dialog reappears during Home App onboarding testing, the systemd override is
        # probably missing on this host (check: `systemctl show bluetooth.service -p
        # ExecStart` should include `--noplugin=battery`) — do not immediately re-revert to
        # pairable=off without checking that first. See
        # docs/developer/mock-service-requirements.md REQ-052 for the full history.
        self._set_pairable_on_verified()

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

        # Set Device Name (GATT 0x2a00) via the adapter's Alias property.
        # Corrected 2026-07-18 — this used to hardcode "ro" with a comment claiming it
        # "matches real Mera Comfort"; that was false (confirmed via a real SILLY log:
        # BlueZ's Device1.Name reads back "Geberit AquaClean pro" from GATT 0x2a00 after
        # connecting — see memory/mera-device-name-ro-is-wrong.md). Now uses
        # self._DEVICE_NAME (webui-writable, see _IDENTITY_ATTR_MAP), not a literal.
        # button_state_read (UUID 0x3A2B, handle 0x0020) separately returns b"ro" — that
        # one IS a confirmed real gating-state sentinel, unrelated to this.
        self._adapter_path = adapter_path
        if adapter_path:
            await self._apply_device_name_to_adapter()

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
            # rc_pairing/rc_8a30/rc_e0db used to be three separate GATT "applications"
            # (three separate GattManager1.RegisterApplication calls, v1.105.0b1-1.106.0b1).
            # Confirmed 2026-07-21 against a real RC: with 6 separate apps registered total
            # (mera/battery/dis + these 3), the RC's generic service discovery found only 4
            # groups instead of 6, with the missing/extra ones showing garbled "Vendor
            # specific" UUIDs and one open-ended (0x0024-0xffff) boundary — confirmed via two
            # independent capture paths (nRF sniffer + the host's own btmon) that this is a
            # real wire-level defect, not a display bug in either tool, and confirmed via a
            # direct Python check that bluez_peripheral's own UUID storage is correct — so the
            # corruption happens between bluez_peripheral and the served ATT bytes, most
            # plausibly a handle/boundary bookkeeping limit when too many separate
            # applications are registered (mera/battery/dis alone, 3 apps, never showed this).
            # bluez_peripheral.gatt.service.ServiceCollection exists specifically to bundle
            # multiple Service objects under ONE application/path/RegisterApplication call —
            # Service.register()'s own docstring warns "using this multiple times will cause
            # path conflicts". Bundling these 3 back under one app returns the total app count
            # to 4 (matching the known-working mera/battery/dis/rc_pairing-stub baseline).
            "rc": f"/org/bluez/example/mera_rc_{self._adapter_tag}",
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
        rc_pairing_service = _RCPairingService(self)
        rc_8a30_service = _RCAncillaryService8A30()
        rc_e0db_service = _RCAncillaryServiceE0DB()
        rc_collection = ServiceCollection([rc_pairing_service, rc_8a30_service, rc_e0db_service])
        try:
            try:
                await service.register(bus, app_paths["mera"], adapter_wrapper)
                await battery_service.register(bus, app_paths["battery"], adapter_wrapper)
                await dis_service.register(bus, app_paths["dis"], adapter_wrapper)
                await rc_collection.register(bus, app_paths["rc"], adapter_wrapper)
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

        # Wire the two RC-pairing NOTIFY characteristics so _RCPairingService can
        # gate its ack notify on both CCCDs being enabled (see its docstring).
        for uuid_target, wire_fn, label in [
            ("25dcdfd2-8867-48da-b1d6-1b5985c4f259", rc_pairing_service.wire_notify_1a, "RC 0x25dcdfd2"),
            ("5a4d406b-b210-47ba-b7e6-db6b9f2e9997", rc_pairing_service.wire_notify_26, "RC 0x5a4d406b"),
        ]:
            found = None
            for attr in ("_characteristics", "_chars"):
                chars = getattr(rc_pairing_service, attr, None)
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
                self.logger.warning("%s notify characteristic not found — RC pairing ack disabled", label)

        # Advertise via D-Bus LEAdvertisingManager1 (same path as mock-geberit-alba).
        # BlueZ encodes UUID 0x3EA0 and manufacturer data into the ADV_IND payload;
        # the local name is placed in SCAN_RSP automatically.
        # Store bus/adapter on the instance so _update_advert() can unregister/re-register
        # on button press.
        self._advert_bus = bus
        self._advert_adapter = adapter_wrapper
        self._advert = _MeraAdvertisement(self._ARTICLE, rs_prefix=self._rs_fw_prefix())
        await self._advert.register(bus, adapter_wrapper)
        self.logger.info(
            "Advertising: UUID=0x3EA0  company=0x0100  byte[2]=0x00 (IsButtonPressed=False)"
            "  article=%s  name='Geberit AC PRO'", self._ARTICLE
        )

        # Register a no-IO pairing agent so BlueZ has something to answer SMP requests
        # from real devices (the Remote Control) with. Without any registered
        # org.bluez.Agent1, bluetoothd has no way to service a pairing request beyond
        # "just works" and logs "No agent available for request type N" instead —
        # see docs/developer/mock-geberit-mera.md §"Button-press/release timing".
        # An embedded toilet has no display/keypad, so NO_INPUT_NO_OUTPUT matches the
        # mock's actual capability; NoIoAgent accepts every pairing request unconditionally.
        self._agent = NoIoAgent()
        await self._agent.register(bus, default=True)
        self.logger.info("BlueZ pairing agent registered: NoIoAgent (default)")

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
                    await gm.call_register_application(app_paths["rc"], {})
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
                rc_pairing_service._ack_sent = False   # allow the ack again next connection
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
        app.router.add_post("/settings/common/{setting_id}", self._handle_write_common_setting)
        app.router.add_post("/settings/profile/{setting_id}", self._handle_write_profile_setting)
        app.router.add_post("/settings/firmware-profile", self._handle_set_firmware_profile)
        app.router.add_post("/settings/fw-component/{component_id}", self._handle_write_fw_component)
        app.router.add_post("/settings/identity/{field}", self._handle_write_identity)
        app.router.add_post("/settings/factory-reset", self._handle_factory_reset)
        app.router.add_post("/settings/trigger-firmware-update", self._handle_trigger_fw_update)
        app.router.add_get("/events", self._handle_events)
        app.router.add_static("/static/", path=str(Path(__file__).parent / "static"))

        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.web_port).start()

        self.logger.info("")
        self.logger.info("--- Mera Comfort Mock Active ---")
        self.logger.info("    mock: %s  script: %s  bridge: %s", _MOCK_VERSION, _SCRIPT_HASH, _BRIDGE_VERSION)
        self.logger.info("    adapter: %s", self.adapter or "(first found)")
        self.logger.info("    SAP: %s  article: %s", self._SAP_NUMBER, self._ARTICLE)
        self.logger.info("    Device Name (0x2a00): %r", self._DEVICE_NAME)
        self.logger.info("    Web UI: http://0.0.0.0:%d/", self.web_port)
        self.logger.info("    Log file: %s", self.logger.device_log_path.name)
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
