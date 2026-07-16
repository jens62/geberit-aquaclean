"""
AlbaMock — class-based BLE peripheral mock for Geberit AquaClean Alba.

Structural port of tools/mock-geberit-alba.py for mock_service.py's multi-device
orchestration (docs/developer/mock-service-requirements.md Phase 3).
tools/mock-geberit-alba.py is intentionally left untouched — its logic is
duplicated here for now, not shared, until a later phase decides the cutover
to a thin wrapper (requirements doc §2/§10 decision 2, same as Mera's Phase 2).

Scope of this port (2026-07-16):
  - Unlike Mera's mock, Alba's DpId store (_Ble20AppLayer) and Arendi crypto
    session (_AriendiServerSide) were ALREADY instance-scoped classes, not
    module globals — this port didn't need to invent that, only wrap the
    orchestration in main() into a class and fix the few real module-level
    touch points (mode/adapter_name/send_delay_sec/web_port -> self.*).
  - Adapter selection goes through the shared mock_bluez_adapter.select_adapter
    instead of this script's own identical inline copy.
  - D-Bus GATT app paths are now tagged with the adapter, same reasoning as
    MeraMock: two instances would otherwise collide on one D-Bus object path.
  - Persistence wiring is included in THIS phase (unlike Mera, which needed a
    separate Phase 2b) because _Ble20AppLayer._write() already does real
    mutation — nothing was stubbed. Every DpId row already carries a
    `behavior` field (0=Info 1=Status 2=Command 3=Nvm 4=Protected); only
    behavior==3 (Nvm) rows are genuinely durable settings, so the persist
    decision falls straight out of data already in _DEFAULT_STORE — no
    separate namespace/persist classification needed, unlike Mera's multiple
    overlapping index spaces.
  - _Ble20AppLayer is deliberately reconstructed fresh every BLE session
    (unchanged behavior) — each fresh construction reloads persisted Nvm
    values from mock_persistence.py, so a setting written in one session (or
    before a mock restart) is visible in the next session automatically.
  - Logging is NOT converted to a per-instance logger in this phase. Unlike
    Mera (one hardcoded `logging.getLogger`), Alba uses a module-level
    timestamped `print()` override across hundreds of call sites in
    _Ble20AppLayer, _AriendiServerSide, and the session loop. Converting all
    of those is exactly Phase 7's scope ("Logging polish") — doing it
    piecemeal here would leave a half-converted mix of self.logger and
    print(), which is worse than deferring it wholesale.

NOT tested against real BlueZ/D-Bus/hardware from this environment (no
bluez_peripheral/dbus_next available here). Verified by careful manual port +
syntax check + a scripted persistence round-trip on the mock VM — see the
requirements doc's Phase 3 verification section.
"""

import argparse
import asyncio
import builtins
import hashlib
import inspect
import json
import os
import pathlib
import random
import struct
import sys
from datetime import datetime, timezone

_builtin_print = builtins.print
def print(*args, **kwargs):  # noqa: A001
    now = datetime.now(tz=timezone.utc).astimezone().strftime('%H:%M:%S.%f')[:-3]
    _builtin_print(now, *args, **kwargs)

_SCRIPT_HASH = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:16]
_MOCK_VERSION = "2.21.0"  # bump this on every functional change — user-visible at startup
_VERBOSE = False  # set by --verbose; enables raw ATT hex per-write logging (unused today — ported for parity, same as the original script)
_ui_notify_state: dict = {"607": False, "564": 1}  # 607: bool; 564: int (1=disabled,2=ready,5=running)
try:
    from importlib.metadata import version as _pkg_ver
    _BRIDGE_VERSION = _pkg_ver("geberit-aquaclean")
except Exception:
    _BRIDGE_VERSION = "unknown"

# Import bluez_peripheral first so it loads its internal DBus library (dbus_next for
# 0.1.x, dbus_fast for 0.2.x).  Then mirror that choice for our own MessageBus /
# BusType / Variant imports — mixing libraries causes a ServiceInterface type error.
from bluez_peripheral.gatt.service import Service
from bluez_peripheral.gatt.characteristic import characteristic, CharacteristicFlags as CharFlags
from bluez_peripheral.advert import Advertisement
import sys as _sys
if "dbus_fast" in _sys.modules:
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType, Variant
    from dbus_fast.service import ServiceInterface, dbus_property, method as dbus_method
    from dbus_fast.constants import PropertyAccess
else:
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant
    from dbus_next.service import ServiceInterface, dbus_property, method as dbus_method
    from dbus_next.constants import PropertyAccess

# Adds the repo root to sys.path so `aquaclean_ble_relay`-relative imports below
# resolve even when this file is run as a standalone script (sys.path[0] is then
# this file's own directory, not the repo root) — must run before ANY import of
# `aquaclean_ble_relay.*`/`aquaclean_console_app.*`. mera_mock.py does this
# ordering correctly already; this file previously did it too late (after the
# aquaclean_ble_relay imports right below), which only broke standalone-script
# invocation outside a proper package layout — found 2026-07-16 testing Phase 6.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from aquaclean_ble_relay.mock_bluez_adapter import select_adapter  # noqa: E402
from aquaclean_ble_relay import mock_persistence  # noqa: E402


class _Advertisement(Advertisement):
    """Advertisement subclass — kept as a thin wrapper.

    The @dbus_property approach for MinInterval/MaxInterval does NOT work:
    bluez_peripheral passes an empty options dict to RegisterAdvertisement, so
    BlueZ receives MinInterval=0x0000 via MGMT and falls back to 1280 ms.
    BlueZ reads the interval from the RegisterAdvertisement OPTIONS DICT, not
    from the advertisement object's D-Bus properties.

    The fast-interval fix is applied after register() by unregistering and
    re-registering with {'MinInterval': 100, 'MaxInterval': 100} in the options
    dict — see the block below adv.register() in AlbaMock.run().
    """


# --- Import Arendi Security crypto from the bridge package -------------------
# (repo root already added to sys.path above, before the aquaclean_ble_relay imports)
from aquaclean_console_app.bluetooth_le.LE.command_id import CommandId
from aquaclean_console_app.bluetooth_le.LE.Ble20Client import encode_address, decode_address
from aquaclean_console_app.bluetooth_le.LE.AriendiSecurity import (
    _crc16_kermit, _cobs_encode, _cobs_decode, _inner_cobs_decode,
    _hkdf, _aes_cmac, _AesCtrState,
    aquacleanBridgeId,
    _SEC_VERSION_REQ, _SEC_VERSION_RESP,
    _SEC_EP_REQ,      _SEC_EP_RESP,
    _SEC_KE_REQ,      _SEC_KE_RESP,
    _SEC_ENCRYPTED,
    _HDLC_SABM_TYPE,  _HDLC_UA_TYPE,
)
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey, X25519PublicKey,
)
try:
    from fastapi import FastAPI as _FastAPI, Request as _Request
    from fastapi.responses import HTMLResponse as _HTMLResponse, RedirectResponse as _RedirectResponse
    from fastapi.staticfiles import StaticFiles as _StaticFiles
    import uvicorn as _uvicorn
    _WEB_AVAILABLE = True
except ImportError:
    _WEB_AVAILABLE = False


# ---------------------------------------------------------------------------
# Inner COBS encode helper (mirrors CobsFraming.Transmit in C# — wraps app data
# before it is passed to the Security layer for encryption)
# ---------------------------------------------------------------------------

def _inner_cobs_encode(data: bytes) -> bytes:
    """Wrap data in inner COBS frame: [0x00] + COBS(data + CRC16_LE) + [0x00]."""
    crc = _crc16_kermit(data)
    return b'\x00' + _cobs_encode(data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])) + b'\x00'


# ---------------------------------------------------------------------------
# DpId value codecs for the webui settings table (docs/developer/mock-service-
# requirements.md §6) — datatype legend per _DEFAULT_STORE's own comment
# (8=String, 9=Counter 4-byte LE, everything else here is a 1-byte enum/offon).
# ---------------------------------------------------------------------------

def _decode_dpid_value(datatype: int, raw: bytes):
    if datatype == 8:  # String
        return raw.decode('ascii', 'replace')
    if datatype == 9:  # Counter, 4-byte LE
        return struct.unpack('<I', raw.ljust(4, b'\x00')[:4])[0]
    return raw[0] if raw else 0


def _encode_dpid_value(datatype: int, value) -> bytes:
    if datatype == 8:  # String
        return str(value).encode('ascii')
    if datatype == 9:  # Counter, 4-byte LE
        return struct.pack('<I', int(value))
    return bytes([int(value) & 0xFF])


# ---------------------------------------------------------------------------
# Ble20 in-memory device mock (--mode ble20)
# ---------------------------------------------------------------------------

class _Ble20AppLayer:
    """
    Server-side Ble20 application layer.

    Maintains a small in-memory DpId store and dispatches decrypted Ble20
    frames after the Arendi Security handshake completes.

    Handles:
      Inventory   (0x00) -> InventoryCount + N x InventoryData
      ReadCmd     (0x10) -> ReadAns or ReadError
      WriteCmd    (0x20) -> WriteAck or WriteError (+ NotifyData if subscribed)
      NotifyEnable  (0x30) -> NotifyAck or NotifyError
      NotifyDisable (0x31) -> NotifyAck
    """

    # (dp_id, instance, version, datatype, min_s, max_s, behavior, init_bytes)
    # datatype: 0=Unused  1=Binary  2=MilliSeconds  3=Seconds  8=String  9=Counter  10=Enum  11=OffOn  13=TimeStampUtc
    # behavior: 0=Info  1=Status  2=Command  3=Nvm  4=Protected
    #
    # Values follow kstr (E4:85:01:CD:6B:04) from kstr-dpid-readall-2026-05-08.md,
    # with deliberate obfuscation of device-identifying fields (marked obf).
    # DpId 236 (UNIQUE_DEVICE_NUMBER) must match 559eb110 bytes 4-7 = 0x02134CD1.
    # PAIRING_SECRET set to "0000" instead of real kstr PIN for test convenience.
    _DEFAULT_STORE = [
        # ── System / device identification ────────────────────────────────
        (0,   None,  0,  9, 0,         255,        0, struct.pack('<I', 250)),        # DEVICE_SERIES = 250
        (1,   None,  0,  9, 0,         255,        0, struct.pack('<I', 0)),          # DEVICE_VARIANT = 0
        (2,   None,  0,  9, 0,         9999999,    4, struct.pack('<I', 35225)),      # DEVICE_NUMBER (obf)
        (3,   None,  0, 13, 0,         0,          4, struct.pack('<I', 1757175271)), # DEVICE_PRODUCTION_DATE (obf)
        (4,   None,  0,  8, 0,         12,         4, b'828.860.00.X'),               # DEVICE_SAP_NUMBER (obf)
        (8,   None,  0,  8, 2,         2,          0, struct.pack('<I', 3)),          # FW_RS_VERSION = 3 -> RS03.0 TS89
        (9,   None,  0,  9, 0,         65535,      0, struct.pack('<I', 89)),         # FW_TS_VERSION = 89
        (10,  None,  0,  8, 2,         2,          4, b'00'),                         # HW_RS_VERSION
        (12,  None,  0,  8, 0,         4,          4, b'0000'),                       # PAIRING_SECRET (real device has a non-zero PIN; 0000 for testing convenience)
        (13,  None,  0,  8, 0,         6,          3, b''),                           # ACCESS_CODE (empty)
        (14,  None,  0,  9, 0,         0,          3, struct.pack('<I', 0)),          # ACCESS_REVOCATION = 0
        (15,  None,  0, 13, 0,         0,          1, struct.pack('<I', 947286443)),  # RTC_TIME (obf)
        (16,  None,  0,  8, 0,         6,          4, b'AcAlba'),                     # DP_NAME
        (62,  None,  1, 10, 0,         4,          2, b'\x00'),                       # RESET (Command, write-only)
        (83,  None,  1, 10, 0,         1,          2, b'\x00'),                       # START_BOOTLOADER (Command, write-only)
        (93,  None,  1,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # POWER_SUPPLY_ERROR_STATUS = 0
        (148, None,  0,  3, 0,         0,          1, struct.pack('<I', 601643)),     # OPERATION_TIME_TOTAL (obf)
        (149, None,  0,  3, 0,         0,          1, struct.pack('<I', 492999)),     # OPERATION_TIME_SINCE_POWER_UP (obf)
        (153, None,  0,  0, 0,         0,          2, b''),                           # RESTART (Command, write-only)
        # DpId 236: must match 559eb110 bytes 4-7 (DeviceUniqueId LE = 0x02134CD1)
        (236, None,  0,  9, 0,         0,          0, struct.pack('<I', 0x02134CD1)), # UNIQUE_DEVICE_NUMBER
        (270, None,  0, 13, 946684800, -192608896, 2, struct.pack('<I', 947286443)), # SET_RTC_TIME (Command, write-only)
        (313, None,  0,  8, 0,         20,         4, b'245.832.00.X'),               # SALES_SAP_NUMBER (obf)
        (337, None,  0,  9, 0,         255,        0, struct.pack('<I', 0)),          # BOOTLOADER_VARIANT = 0
        (369, None,  0,  8, 0,         20,         4, b'SB0000EU000000'),             # SALES_PRODUCT_SERIAL_NUMBER (obf)
        (370, None,  0, 13, 0,         0,          4, struct.pack('<I', 1774187093)), # SALES_PRODUCT_PRODUCTION_DATE (obf)
        (371, None,  0,  8, 0,         12,         4, b'146.350.01.x'),               # SALES_PRODUCT_SAP_NUMBER
        (431, None,  0,  3, 0,         0,          4, struct.pack('<I', 0)),          # OPERATION_TIME_OFFSET = 0
        # ── Anal shower ───────────────────────────────────────────────────
        (563, None,  0, 10, 0,         1,          2, b'\x00'),                       # START_STOP_ANAL_SHOWER (Command)
        (564, None,  0, 10, 0,         7,          1, b'\x01'),                       # ANAL_SHOWER_STATUS = 1 (Disabled)
        (566, None,  0, 10, 0,         1,          2, b'\x00'),                       # START_STOP_SPRAY_ARM_CLEANING (Command)
        (567, None,  0, 10, 0,         5,          1, b'\x02'),                       # SPRAY_ARM_CLEANING_STATUS = 2 (Ready)
        (569, None,  0, 10, 0,         0,          2, b'\x00'),                       # LOAD_PROFILE (Command)
        (570, None,  0, 10, 0,         4,          2, b'\x00'),                       # SET_ACTIVE_ANAL_SPRAY_INTENSITY (Command)
        (571, None,  0, 10, 0,         4,          1, b'\x04'),                       # ACTIVE_ANAL_SPRAY_INTENSITY_STATUS = 4 (Level 5)
        (572, None,  0, 10, 0,         4,          2, b'\x00'),                       # SET_ACTIVE_ANAL_SPRAY_ARM_POSITION (Command)
        (573, None,  0, 10, 0,         4,          1, b'\x04'),                       # ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS = 4 (Position 5)
        (574, None,  0, 10, 0,         5,          2, b'\x00'),                       # SET_ACTIVE_SHOWER_WATER_TEMPERATURE (Command)
        (575, None,  0, 10, 0,         5,          1, b'\x05'),                       # ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS = 5 (Level 5)
        (576, None,  0, 11, 0,         1,          2, b'\x00'),                       # SET_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION (Command)
        (577, None,  0, 11, 0,         1,          1, b'\x00'),                       # ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS = Off
        (580, None,  0, 10, 0,         4,          3, b'\x04'),                       # STORED_ANAL_SPRAY_INTENSITY = 4 (Level 5)
        (581, None,  0, 10, 0,         4,          3, b'\x04'),                       # STORED_ANAL_SPRAY_ARM_POSITION = 4 (Position 5)
        (582, None,  0, 10, 0,         5,          3, b'\x05'),                       # STORED_SHOWER_WATER_TEMPERATURE = 5 (Level 5)
        (583, None,  0, 11, 0,         1,          3, b'\x00'),                       # STORED_ANAL_SPRAY_ARM_OSCILLATION = Off
        (584, None,  0, 10, 0,         1,          2, b'\x00'),                       # START_STOP_DESCALING (Command)
        (585, None,  0, 10, 0,         4,          1, b'\x02'),                       # DESCALING_STATUS = 2 (Ready)
        (588, None,  0,  9, 0,         0,          3, struct.pack('<I', 1)),          # UNACCOUNTED_SHOWER_CYCLES = 1
        (589, None,  0,  9, 0,         0,          1, struct.pack('<I', 168)),        # DAYS_UNTIL_NEXT_DESCALING = 168
        (590, None,  0, 13, 0,         0,          3, struct.pack('<I', 0)),          # TIMESTAMP_OF_LAST_DESCALING = 0 (never)
        (591, None,  0, 13, 0,         0,          3, struct.pack('<I', 0)),          # TIMESTAMP_OF_LAST_DESCALING_REQUEST = 0
        (592, None,  0,  9, 0,         0,          3, struct.pack('<I', 0)),          # DESCALING_CYCLES = 0
        # DpId=607: USER_DETECTION_STATUS — toggled per-session by _handshake_loop
        (607, None,  0, 10, 0,         1,          1, b'\x00'),                       # USER_DETECTION_STATUS = 0 (User absent)
        (711, None,  0,  9, 0,         0,          1, struct.pack('<I', 340)),        # STATISTIC_COUNTER_SINCE_POWER_UP_SUM = 340
        (764, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # WATER_HEATER_ERROR_STATUS = 0
        (765, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # LEVEL_CONTROL_ERROR_STATUS = 0
        (766, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # USER_DETECTION_ERROR_STATUS = 0
        (781, None,  0,  9, 0,         0,          3, struct.pack('<I', 33600)),      # CREDITS_UNTIL_NEXT_DESCALING = 33600 (168 d x 200)
        (789, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # WATER_PUMP_ERROR_STATUS = 0
        (790, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # SPRAY_ARM_DRIVE_ERROR_STATUS = 0
        (795, None,  0, 11, 0,         1,          3, b'\x00'),                       # DEMO_MODE = Off
        (796, None,  0, 10, 0,         2,          3, b'\x00'),                       # PRODUCT_REGISTRATION_LEVEL = 0 (Unregistered)
        (802, None,  0,  0, 0,         0,          2, b''),                           # START_USER_SESSION (Command, write-only)
        (803, None,  0, 11, 0,         1,          4, b'\x00'),                       # SHOWROOM_MODE = Off
        (810, None,  0, 11, 0,         1,          1, b'\x00'),                       # DRY_RUN_MODE = Off
        (820, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # MAINTENANCE_REQUEST_STATUS = 0
        (977, None,  0,  9, 0,         0,          3, struct.pack('<I', 14)),         # DESCALING_DEVICE_LOCK_REMAINING_DAYS = 14
        (978, None,  0,  0, 0,         0,          2, b''),                           # DESCALING_UNLOCK_DEVICE (Command, write-only)
        (979, None,  0,  9, 0,         0,          3, struct.pack('<I', 0)),          # DESCALING_DEVICE_RELOCK_REMAINING_CYCLES = 0
        (982, None,  0,  1, 4,         4,          0, b'\x00\x00\x00\x00'),          # DESCALING_ERROR_STATUS = 0
        (983, None,  0, 11, 0,         1,          1, b'\x00'),                       # DESCALING_DEVICE_LOCK_STATUS = Off
        # ── Instanced DpIds ───────────────────────────────────────────────
        (786,  2,    0,  9, 0,         0,          0, struct.pack('<I', 0)),          # GEBERIT_LOADER_VERSION inst=2
        (785,  3,    0,  9, 0,         0,          0, struct.pack('<I', 0)),          # FUS_VERSION inst=3
        (787,  3,    0,  9, 0,         0,          0, struct.pack('<I', 0)),          # WIRELESS_STACK_VERSION inst=3
        (565,  4,    0,  2, 0,         0,          1, b'\x00\x00\x00\x00'),          # ANAL_SHOWER_PROGRESS inst=4
        (568,  4,    0,  2, 0,         0,          1, b'\x00\x00\x00\x00'),          # SPRAY_ARM_CLEANING_PROGRESS inst=4
        (586,  4,    0,  2, 0,         0,          1, b'\x00\x00\x00\x00'),          # DESCALING_PROGRESS inst=4
        (405, 31,    1,  9, 0,  999999999,         1, struct.pack('<I', 0)),          # STATISTIC_COUNTER_SINCE_POWER_UP inst=31
        (688, 31,    1,  9, 0,  999999999,         1, struct.pack('<I', 0)),          # STATISTIC_COUNTER_SINCE_RESET inst=31
        (689, 31,    1,  9, 0,  999999999,         1, struct.pack('<I', 0)),          # STATISTIC_COUNTER_TOTAL inst=31
    ]

    # A real Alba's serial number and pairing PIN are unique per physical unit —
    # printed on its own sticker (see docs/developer/mock-service-requirements.md
    # §5) — but every _DEFAULT_STORE row above is one shared hardcoded default.
    # Two Alba mocks (e.g. two adapters, Phase 5) would otherwise show identical
    # "S/N" and PIN, which isn't what a real fleet of devices looks like. These
    # two DpIds are Protected (behavior=4, factory-set — never written over BLE,
    # so the Nvm write-through path below doesn't touch them) but still need a
    # stable per-device identity: generated once per device_key and persisted
    # forever after, exactly like a sticker never changes once printed.
    _IDENTITY_DPIDS = (12, 369)  # PAIRING_SECRET, SALES_PRODUCT_SERIAL_NUMBER

    # Firmware/component-version DpIds — behavior==0 (Info) or 4 (Protected), so
    # (like identity above) never touched by the Nvm (behavior==3) write-through
    # path. Persisted the same way so a future firmware-update simulation can
    # durably change what a real device's flash update would change, and so
    # multiple AlbaMock instances don't all silently share one mutable copy.
    # (dp_id, instance) pairs — three of these are instanced rows in _DEFAULT_STORE.
    _FIRMWARE_DPIDS = ((8, None), (9, None), (10, None), (786, 2), (785, 3), (787, 3))
    # FW_RS_VERSION, FW_TS_VERSION, HW_RS_VERSION, GEBERIT_LOADER_VERSION, FUS_VERSION, WIRELESS_STACK_VERSION

    # Friendly names for the webui's settings table (docs/developer/mock-service-
    # requirements.md §6) — writable Nvm DpIds plus the read-only identity/
    # firmware DpIds above. Deliberately scoped to these ~14, not all 79 DpIds
    # in _DEFAULT_STORE — the rest are live/session state, not "settings".
    _DPID_NAMES = {
        13: "Access Code", 580: "Stored Anal Spray Intensity",
        581: "Stored Anal Spray Arm Position", 582: "Stored Shower Water Temperature",
        583: "Stored Anal Spray Arm Oscillation", 795: "Demo Mode",
        12: "Pairing Secret (PIN)", 369: "Serial Number",
        8: "FW RS Version", 9: "FW TS Version", 10: "HW RS Version",
        785: "FUS Version", 786: "Geberit Loader Version", 787: "Wireless Stack Version",
    }
    _SETTINGS_DPIDS = tuple(_DPID_NAMES.keys())

    @staticmethod
    def _generate_identity_value(dp_id: int) -> bytes:
        if dp_id == 12:  # PAIRING_SECRET — 4-digit numeric PIN, same format as _DEFAULT_STORE's default
            return f"{random.randint(0, 9999):04d}".encode('ascii')
        if dp_id == 369:  # SALES_PRODUCT_SERIAL_NUMBER, e.g. real kstr-style "SB2603EU08023"
            return f"SB{random.randint(1000, 9999)}EU{random.randint(0, 999999):06d}".encode('ascii')
        raise ValueError(f"no identity generator for DpId {dp_id}")

    def __init__(self, notify_queue=None, device_key: str = "default"):
        self._device_key = device_key
        self._store: dict = {}
        self._notify_subscribed: set = set()
        self._notify_queue = notify_queue
        for dp_id, inst, ver, dt, mn, mx, beh, val in self._DEFAULT_STORE:
            self._store[(dp_id, inst)] = {
                'version': ver, 'datatype': dt,
                'min_s': mn,    'max_s': mx,
                'behavior': beh,
                'value': bytearray(val),
            }
        # Persisted Nvm (behavior==3) values win over the hardcoded defaults
        # above — this is what makes a setting survive a mock restart
        # (requirements doc §0/§5). Reloaded fresh every session (this class
        # is deliberately reconstructed per BLE session), which also means a
        # value written in one session is visible in the very next one.
        persisted = mock_persistence.load_all("alba", self._device_key)
        for key, value_hex in persisted.items():
            namespace, _, dp_id_str = key.partition(":")
            if namespace != "dpid":
                continue
            try:
                dp_id = int(dp_id_str)
            except ValueError:
                continue
            entry = self._store.get((dp_id, None))
            if entry is not None and entry['behavior'] == 3:
                entry['value'] = bytearray(bytes.fromhex(value_hex))

        # Per-device identity (serial number, pairing PIN) — generated once per
        # device_key, persisted immediately, and reused on every later session/
        # restart. Deliberately outside the Nvm-only loop above: these are
        # Protected fields never written over BLE, so nothing would ever
        # populate them via the normal write-through path.
        for dp_id in self._IDENTITY_DPIDS:
            key = f"dpid:{dp_id}"
            if key in persisted:
                # Apply directly — the loop above skips these (behavior==4,
                # Protected, not ==3 Nvm), so without this they'd silently stay
                # at the _DEFAULT_STORE default despite being "already generated".
                self._store[(dp_id, None)]['value'] = bytearray(bytes.fromhex(persisted[key]))
                continue
            value = self._generate_identity_value(dp_id)
            self._store[(dp_id, None)]['value'] = bytearray(value)
            mock_persistence.save("alba", self._device_key, key, value.hex())

        # Firmware/component-version DpIds — unlike identity, no random
        # generation: firmware versions aren't unique per physical unit, so the
        # first run for a device_key just persists today's hardcoded
        # _DEFAULT_STORE default, making it the stable, writable baseline a
        # future firmware-update simulation can change.
        for dp_id, instance in self._FIRMWARE_DPIDS:
            key = f"dpid:{dp_id}"
            if key in persisted:
                self._store[(dp_id, instance)]['value'] = bytearray(bytes.fromhex(persisted[key]))
                continue
            value = bytes(self._store[(dp_id, instance)]['value'])
            mock_persistence.save("alba", self._device_key, key, value.hex())

    def _set_firmware_version(self, dp_id: int, value: bytes) -> None:
        """Update a firmware/component-version DpId and persist it — the write
        hook a firmware-update-process simulation calls after a simulated OTA
        completes. Not called anywhere yet (docs/developer/mock-service-requirements.md
        Phase 9b)."""
        instance = dict(self._FIRMWARE_DPIDS)[dp_id]
        self._store[(dp_id, instance)]['value'] = bytearray(value)
        mock_persistence.save("alba", self._device_key, f"dpid:{dp_id}", bytes(value).hex())

    def _find_entry(self, dp_id: int):
        return next((e for (d, _i), e in self._store.items() if d == dp_id), None)

    def _settings_table_data(self) -> dict:
        """Build the metadata+value JSON mock-controls.js needs to render the
        settings table — docs/developer/mock-service-requirements.md §6."""
        settings_rows, info_rows = [], []
        for dp_id in self._SETTINGS_DPIDS:
            entry = self._find_entry(dp_id)
            if entry is None:
                continue
            value = _decode_dpid_value(entry['datatype'], bytes(entry['value']))
            row = {
                "id": dp_id, "name": self._DPID_NAMES[dp_id], "value": value,
                "min": entry['min_s'], "max": entry['max_s'],
            }
            if entry['behavior'] == 3:  # Nvm — the only writable class here
                row["writeUrl"] = f"/settings/dpid/{dp_id}"
                if entry['datatype'] == 8:
                    row["kind"] = "text"
                elif entry['datatype'] == 11:
                    row["kind"] = "toggle"
                else:
                    row["kind"] = "stepper"
                settings_rows.append(row)
            else:
                row["kind"] = "readonly"
                info_rows.append(row)
        return {"sections": [
            {"title": "Settings", "rows": settings_rows},
            {"title": "Identity & Firmware", "rows": info_rows},
        ]}

    def _write_dpid_setting(self, dp_id: int, raw_value) -> None:
        """Webui-only write path for /settings/dpid/{id}. Restricted to
        behavior==3 (Nvm) rows — mirrors the persistence _write() already does
        for a real BLE WriteCmd, but deliberately does NOT fan out a NOTIFY
        (that's a live-BLE-session concept; this is a settings-table edit that
        may happen with no BLE client connected at all)."""
        entry = self._find_entry(dp_id)
        if entry is None:
            raise KeyError(f"unknown DpId {dp_id}")
        if entry['behavior'] != 3:
            raise ValueError(f"DpId {dp_id} is not writable (behavior={entry['behavior']})")
        encoded = _encode_dpid_value(entry['datatype'], raw_value)
        entry['value'] = bytearray(encoded)
        mock_persistence.save("alba", self._device_key, f"dpid:{dp_id}", encoded.hex())

    async def _stop_sequence(self):
        """Simulate realistic shower wind-down: 5->6(Retracting)->7(Postrinsing)->2(Ready)."""
        for delay, state, label in [(0.5, 0x06, "Retracting"), (1.5, 0x07, "Postrinsing"), (3.0, 0x02, "Ready")]:
            await asyncio.sleep(delay)
            entry = self._store.get((564, None))
            if entry is not None:
                entry['value'] = bytearray([state])
            if 564 in self._notify_subscribed and self._notify_queue is not None:
                await self._notify_queue.put((564, bytes([state])))

    async def dispatch(self, plaintext: bytes) -> list:
        """Dispatch one decrypted Ble20 frame; return list of response payloads."""
        if not plaintext:
            return []
        cmd = plaintext[0]
        if cmd == 0xD0:  # TunnelDataExchange wrapper (GetEndProduct path)
            return self._tunnel_dispatch(plaintext)
        return self._dispatch_sync(plaintext)

    def _tunnel_dispatch(self, frame: bytes) -> list:
        """Unwrap a TunnelDataExchange (0xD0) frame, dispatch inner frames, rewrap responses.

        Wire format: [0xD0, series, variant, uid0-3, (len-1, inner_bytes)...]
        Response:    same header, one wrapped inner response per inner request frame.
        """
        if len(frame) < 9:
            print(f"[MockBle20] tunnel frame too short ({len(frame)} bytes) — ignored")
            return []
        series  = frame[1]
        variant = frame[2]
        uid     = frame[3:7]
        hdr     = bytes([0xD0, series, variant]) + uid

        responses = []
        pos = 7
        while pos + 1 < len(frame):
            inner_len = frame[pos] + 1
            pos += 1
            if pos + inner_len > len(frame):
                print(f"[MockBle20] tunnel inner frame truncated at pos={pos} — ignored")
                break
            inner = frame[pos:pos + inner_len]
            pos += inner_len
            print(f"[MockBle20] [tunnel] inner cmd=0x{inner[0]:02X}")
            for resp in self._dispatch_sync(inner):
                responses.append(hdr + bytes([len(resp) - 1]) + resp)
        return responses

    def _dispatch_sync(self, plaintext: bytes) -> list:
        """Dispatch one unwrapped frame synchronously (no 0xD0 recursion)."""
        if not plaintext:
            return []
        cmd = plaintext[0]
        if cmd == CommandId.Inventory:
            return self._inventory()
        if cmd == CommandId.ReadCmd:
            return self._read(plaintext)
        if cmd == CommandId.WriteCmd:
            return self._write(plaintext)
        if cmd == CommandId.NotifyEnable:
            return self._notify_enable(plaintext)
        if cmd == CommandId.NotifyDisable:
            return self._notify_disable(plaintext)
        if cmd == CommandId.CapabilitiesCmd:
            return self._capabilities()
        if cmd == CommandId.EventStorageInventory:
            return self._event_storage_inventory()
        if cmd == CommandId.ListInventoryCmd:
            return self._list_inventory()
        print(f"[MockBle20] unknown cmd=0x{cmd:02X} — ignored")
        return []

    def _inventory(self) -> list:
        count = len(self._store)
        frames = [struct.pack('<BH', CommandId.InventoryCount, count)]
        for (dp_id, inst), e in sorted(self._store.items()):
            addr = encode_address(dp_id, inst)
            payload = (bytes([e['version'], e['datatype']]) +
                       struct.pack('<ii', e['min_s'], e['max_s']) +
                       bytes([e['behavior'] & 0x7F]))
            frames.append(bytes([CommandId.InventoryData]) + addr + payload)
            print(f"[MockBle20] → INVENTORY_DATA DpId={dp_id}")
        return frames

    def _read(self, frame: bytes) -> list:
        dp_id, inst, _ = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        entry = self._store.get((dp_id, inst)) or self._store.get((dp_id, None))
        if entry is None:
            print(f"[MockBle20] ← READ DpId={dp_id} → InvalidId")
            return [bytes([CommandId.ReadError]) + addr + bytes([0x01])]
        val = bytes(entry['value'])
        print(f"[MockBle20] ← READ DpId={dp_id} → {val.hex()}")
        if dp_id == 8:
            print("[MockServer] *** Phase 1 complete — if app shows PIN dialog, enter: 0000 ***")
        return [bytes([CommandId.ReadAns]) + addr + val]

    def _write(self, frame: bytes) -> list:
        dp_id, inst, off = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        value = frame[off:]
        entry = self._store.get((dp_id, inst)) or self._store.get((dp_id, None))
        if entry is None:
            print(f"[MockBle20] ← WRITE DpId={dp_id} → InvalidId")
            return [bytes([CommandId.WriteError]) + addr + bytes([0x01])]
        entry['value'] = bytearray(value)
        if entry['behavior'] == 3:  # Nvm — persisted immediately (requirements doc §5)
            mock_persistence.save("alba", self._device_key, f"dpid:{dp_id}", value.hex())
            print(f"[MockBle20] ← WRITE DpId={dp_id} value={value.hex()} → ACK — persisted")
        else:
            print(f"[MockBle20] ← WRITE DpId={dp_id} value={value.hex()} → ACK")
        responses = [bytes([CommandId.WriteAck]) + addr]
        if dp_id in self._notify_subscribed:
            responses.append(bytes([CommandId.NotifyData]) + encode_address(dp_id) + value)
            print(f"[MockBle20] → NOTIFY_DATA DpId={dp_id} value={value.hex()}")
        # DpId 563 = START_STOP_ANAL_SHOWER: mirror into DpId 564 (ANAL_SHOWER_STATUS)
        # Start (01): immediately -> 5 (Shower running); enables STOP button in app.
        # Stop  (00): async wind-down 5->6(Retracting)->7(Postrinsing)->2(Ready).
        if dp_id == 563:
            if value[:1] == b'\x01':
                entry_564 = self._store.get((564, None))
                if entry_564 is not None:
                    entry_564['value'] = bytearray(b'\x05')
                if 564 in self._notify_subscribed:
                    responses.append(bytes([CommandId.NotifyData]) + encode_address(564) + b'\x05')
                    print(f"[MockBle20] → NOTIFY_DATA DpId=564 value=05 (Shower (running))")
            else:
                try:
                    asyncio.get_running_loop().create_task(self._stop_sequence())
                    print(f"[MockBle20] → scheduled stop sequence 5->6(0.5s)->7(1.5s)->2(3s)")
                except RuntimeError:
                    entry_564 = self._store.get((564, None))
                    if entry_564 is not None:
                        entry_564['value'] = bytearray(b'\x02')
                    if 564 in self._notify_subscribed:
                        responses.append(bytes([CommandId.NotifyData]) + encode_address(564) + b'\x02')
                        print(f"[MockBle20] → NOTIFY_DATA DpId=564 value=02 (Ready) [fallback]")
        return responses

    def _notify_enable(self, frame: bytes) -> list:
        dp_id, inst, _ = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        if (dp_id, inst) not in self._store and (dp_id, None) not in self._store:
            print(f"[MockBle20] ← NOTIFY_ENABLE DpId={dp_id} → InvalidId")
            return [bytes([CommandId.NotifyError]) + addr + bytes([0x01])]
        self._notify_subscribed.add(dp_id)
        print(f"[MockBle20] ← NOTIFY_ENABLE DpId={dp_id} → ACK")
        return [bytes([CommandId.NotifyAck]) + addr]

    def _notify_disable(self, frame: bytes) -> list:
        dp_id, inst, _ = decode_address(frame, 1)
        addr = encode_address(dp_id, inst)
        self._notify_subscribed.discard(dp_id)
        print(f"[MockBle20] ← NOTIFY_DISABLE DpId={dp_id} → ACK")
        return [bytes([CommandId.NotifyAck]) + addr]

    def _capabilities(self) -> list:
        # flags=0x00: no extended event storage, no other extensions
        print("[MockBle20] ← CAPABILITIES_CMD → ACK flags=0x00")
        return [bytes([CommandId.CapabilitiesAck, 0x00])]

    def _event_storage_inventory(self) -> list:
        # No stored events — respond with count=0
        print("[MockBle20] ← EVENT_STORAGE_INVENTORY → count=0")
        return [struct.pack('<BH', CommandId.EventStorageInventoryCount, 0)]

    def _list_inventory(self) -> list:
        # No lists — respond with count=0
        print("[MockBle20] ← LIST_INVENTORY_CMD → count=0")
        return [struct.pack('<BH', CommandId.ListInventoryCount, 0)]


# ---------------------------------------------------------------------------
# Device-side Arendi Security implementation
# ---------------------------------------------------------------------------

class _AriendiServerSide:
    """
    Server (device) role for the Arendi Security handshake + encrypted data.

    Call flow:
      1. feed(att_bytes)  — call from BLE write handler for every incoming ATT PDU
      2. await run(send_fn) — drives the handshake then loops on encrypted frames
         send_fn: async callable(att_bytes: bytes) — sends a BLE notification
    """

    def __init__(self):
        self._rx_buf  = bytearray()
        self._rx_queue: asyncio.Queue = asyncio.Queue()
        self._srr_queue: asyncio.Queue = asyncio.Queue()
        self._tx_seq  = 0
        self._rx_ack  = 0
        self._tx_cipher: _AesCtrState | None = None
        self._rx_cipher: _AesCtrState | None = None
        self.handshake_done = False
        # Set to True when the frame loop exits because the BLE peer closed the link.
        # False means the session ended by timeout (BLE link may still be alive).
        self.disconnected_by_peer = False
        # Set to True by Phase 2+ timeout: DataPointInventory complete but no Phase 3
        # SABM or GetEndProduct arrived — mock should disconnect so advertising resumes.
        self.should_disconnect_after = False

    # -----------------------------------------------------------------------
    # Incoming ATT byte feeding — same COBS/CRC/HDLC parser as AriendiSecurity
    # -----------------------------------------------------------------------

    def feed(self, data: bytes) -> None:
        self._rx_buf.extend(data)
        self._process_rx_buf()

    def _process_rx_buf(self) -> None:
        while True:
            buf = self._rx_buf
            if not buf or buf[0] != 0:
                idx = buf.find(b'\x00')
                if idx == -1:
                    self._rx_buf = bytearray()
                    return
                self._rx_buf = buf[idx:]
                buf = self._rx_buf
            end = buf.find(b'\x00', 1)
            if end == -1:
                return
            frame_bytes = bytes(buf[1:end])
            self._rx_buf = buf[end:]
            if not frame_bytes:
                continue
            try:
                decoded = _cobs_decode(frame_bytes)
            except ValueError:
                continue
            if len(decoded) < 3:
                continue
            crc_recv = decoded[-2] | (decoded[-1] << 8)
            crc_calc = _crc16_kermit(decoded[:-2])
            if crc_recv != crc_calc:
                print(f"[MockServer] CRC mismatch rx=0x{crc_recv:04X} calc=0x{crc_calc:04X} — frame dropped")
                continue
            ctrl    = decoded[0]
            payload = decoded[1:-2]
            if (ctrl & 0x01) == 0:        # I-frame
                peer_ns = (ctrl >> 1) & 0x07
                peer_nr = (ctrl >> 5) & 0x07
                self._rx_ack = (peer_ns + 1) % 8
                cmd_byte = f"0x{payload[1]:02X}" if len(payload) >= 2 and payload[0] == _SEC_ENCRYPTED else f"sec=0x{payload[0]:02X}" if payload else "empty"
                print(f"[HDLC←] I-frame N(S)={peer_ns} N(R)={peer_nr} payload={len(payload)}B cmd={cmd_byte}")
                self._rx_queue.put_nowait(('I', ctrl, payload))
            elif (ctrl & 0x03) == 0x03:   # U-frame
                print(f"[HDLC←] U-frame ctrl=0x{ctrl:02X}")
                self._rx_queue.put_nowait(('U', ctrl, payload))
            elif (ctrl & 0x03) == 0x01:  # S-frame (RR/RNR) — signal flow control
                peer_nr = (ctrl >> 5) & 0x07
                print(f"[HDLC←] S-RR N(R)={peer_nr}")
                self._srr_queue.put_nowait(ctrl)

    # -----------------------------------------------------------------------
    # Frame builders — mirrors of AriendiSecurity
    # -----------------------------------------------------------------------

    @staticmethod
    def _u_ctrl(type_code: int) -> int:
        return ((type_code << 3) & 0xE0) | ((type_code << 2) & 0x0C) | 0x03

    def _i_ctrl(self) -> int:
        return ((self._rx_ack << 5) & 0xE0) | ((self._tx_seq << 1) & 0x0E)

    def _build_att(self, ctrl: int, payload: bytes) -> bytes:
        raw = bytes([ctrl]) + payload
        crc = _crc16_kermit(raw)
        return b'\x00' + _cobs_encode(raw + bytes([crc & 0xFF, (crc >> 8) & 0xFF])) + b'\x00'

    def _att_u(self, hdlc_type: int) -> bytes:
        return self._build_att(self._u_ctrl(hdlc_type), b'')

    def _att_i(self, sec_payload: bytes) -> bytes:
        att = self._build_att(self._i_ctrl(), sec_payload)
        self._tx_seq = (self._tx_seq + 1) % 8
        return att

    # -----------------------------------------------------------------------
    # Await helpers
    # -----------------------------------------------------------------------

    async def _await_u(self, expected_ctrl: int, timeout: float = 5.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[MockServer] timeout waiting for U ctrl=0x{expected_ctrl:02X}")
            ft, ctrl, _ = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'DISCONNECT':
                raise TimeoutError("[MockServer] BLE disconnected during handshake")
            if ft == 'U' and ctrl == expected_ctrl:
                return

    async def _await_i(self, expected_type: int, timeout: float = 5.0) -> bytes:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[MockServer] timeout waiting for I type=0x{expected_type:02X}")
            ft, ctrl, payload = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'DISCONNECT':
                raise TimeoutError("[MockServer] BLE disconnected during handshake")
            if ft == 'I' and payload and payload[0] == expected_type:
                return payload

    async def _await_s_rr(self, expected_nr: int, timeout: float = 2.0) -> None:
        """Wait for the bridge's HDLC S-RR ACK for the last sent I-frame.

        expected_nr: the N(R) value the bridge should include in its S-RR.
            When mock sends an I-frame with N(S)=k, _att_i() advances _tx_seq to
            k+1.  The bridge's ACK has N(R)=k+1 = _tx_seq at call time.
            S-RRs with a different N(R) are stale (handshake leftovers or
            pair-delivery pre-queued items) and are discarded.

        Filtering by N(R) prevents stale S-RRs from being consumed as ACKs for
        later frames.  Without this check, stale S-RRs cause mock to consider a
        frame "delivered" before the bridge actually received it — BlueZ's
        notification queue fills up with unconfirmed frames, and a subsequent
        notification (e.g. DpId=16) arrives at the bridge 30 s late.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError()
            ctrl = await asyncio.wait_for(self._srr_queue.get(), timeout=remaining)
            if (ctrl >> 5) & 0x07 == expected_nr:
                return  # correct ACK — frame confirmed delivered

    # -----------------------------------------------------------------------
    # Handshake + encrypted data exchange
    # -----------------------------------------------------------------------

    async def run(self, send_fn, app_handler=None, send_delay_sec: float = 0.0, disconnect_fn=None) -> None:
        """
        Run the full server-side handshake, then loop on incoming encrypted frames.

        send_fn:        async callable(att_bytes: bytes) — BLE notification sender.
        app_handler:    async callable(plaintext: bytes) -> list[bytes] — Ble20 dispatch;
                        if None, sends back a fake Legacy GetDeviceIdentification response.
        send_delay_sec: seconds to sleep between consecutive notifications (0 = no delay).
                        Use ~0.020 when testing over an ESPHome BLE proxy to avoid
                        congestion-induced ATT notification drops.
        """
        # Outer loop: run a full handshake + frame session; restart when a new
        # SABM arrives mid-session (coordinator reconnects on the same BLE link).
        # need_sabm=True  -> wait for SABM before sending UA (first session).
        # need_sabm=False -> SABM already received in frame loop; go straight to UA.
        need_sabm = True
        _ble_session_phase = 0
        while True:
            _ble_session_phase += 1
            if need_sabm:
                print(f"[MockServer] [Phase {_ble_session_phase}] waiting for SABM...")
                try:
                    await self._await_u(self._u_ctrl(_HDLC_SABM_TYPE), timeout=60.0)
                except asyncio.TimeoutError:
                    print("[MockServer] session timed out — no client connected within 60 s")
                    print("[Mock] If the bridge/HACS was running, the ESP32 BLE scanner may be stuck.")
                    print("[Mock]   → Restart it via ESPHome web UI (Restart button) — no other action needed.")
                    return False
                print(f"[MockServer] [Phase {_ble_session_phase}] ← SABM")
            else:
                print(f"[MockServer] [Phase {_ble_session_phase}] SABM (mid-session restart) — fresh Arendi KE")

            # 1. UA
            await send_fn(self._att_u(_HDLC_UA_TYPE))
            print(f"[MockServer] [Phase {_ble_session_phase}] → UA")

            # 2. VERSION_REQ -> VERSION_RESP
            try:
                await self._await_i(_SEC_VERSION_REQ)
            except asyncio.TimeoutError:
                print("[MockServer] handshake timeout waiting for VERSION_REQ")
                return
            print("[MockServer] ← VERSION_REQ")
            await send_fn(self._att_i(bytes([_SEC_VERSION_RESP, 0, 0, 0, 0, 0, 1])))
            print("[MockServer] → VERSION_RESP (proto v2)")

            # 3. EP_REQ -> EP_RESP
            try:
                await self._await_i(_SEC_EP_REQ)
            except asyncio.TimeoutError:
                print("[MockServer] handshake timeout waiting for EP_REQ")
                return
            print("[MockServer] ← EP_REQ")
            nonce1 = os.urandom(16)
            nonce2 = os.urandom(16)
            ep_resp = bytes([_SEC_EP_RESP]) + nonce1 + nonce2 + bytes([0x01])
            await send_fn(self._att_i(ep_resp))
            print(f"[MockServer] → EP_RESP  nonce1={nonce1.hex()}  nonce2={nonce2.hex()}")

            # 4. KE_REQ -> verify client CMAC, generate server keypair, KE_RESP
            try:
                ke = await self._await_i(_SEC_KE_REQ)
            except asyncio.TimeoutError:
                print("[MockServer] handshake timeout waiting for KE_REQ")
                return
            print("[MockServer] ← KE_REQ")
            if len(ke) < 49:
                print(f"[MockServer] KE_REQ too short ({len(ke)} bytes) — aborting")
                return
            client_public_bytes = ke[1:33]
            client_cmac_bytes   = ke[33:49]

            auth_key = _hkdf(ikm=aquacleanBridgeId, salt=nonce1, length=16)
            expected_cmac = _aes_cmac(auth_key, client_public_bytes)
            if client_cmac_bytes != expected_cmac:
                print("[MockServer] client CMAC verification FAILED — wrong aquacleanBridgeId?")
                return
            print("[MockServer] client CMAC verified ✓")

            server_priv         = X25519PrivateKey.generate()
            server_public_bytes = server_priv.public_key().public_bytes_raw()
            server_cmac         = _aes_cmac(auth_key, server_public_bytes)

            client_pub_key = X25519PublicKey.from_public_bytes(client_public_bytes)
            shared_secret  = server_priv.exchange(client_pub_key)
            key_material   = _hkdf(ikm=shared_secret, salt=nonce1, length=32)

            # key_material[0:16]  = client rx key  = server tx key  (server encrypts outgoing)
            # key_material[16:32] = client tx key  = server rx key  (server decrypts incoming)
            self._tx_cipher = _AesCtrState(key_material[0:16],  nonce2)
            self._rx_cipher = _AesCtrState(key_material[16:32], nonce2)

            ke_resp = bytes([_SEC_KE_RESP]) + server_public_bytes + server_cmac
            await send_fn(self._att_i(ke_resp))
            print(f"[MockServer] → KE_RESP  server_pub={server_public_bytes.hex()[:16]}...")

            self.handshake_done = True
            print("[MockServer] *** HANDSHAKE COMPLETE — session keys established ***")

            # 5. Loop on incoming encrypted frames
            # The first item in the queue may be a S-RR ACK (ft='S') or the first data frame.
            new_sabm = False
            # Timeouts:
            #   _first_frame (True) — 5 s: app derives keys and encrypts first request
            #                         after KE, observed 450-750 ms on iPhone.
            #   active session (False) — 30 s: inter-command gap once session is running.
            #                           During init, gaps are ~2.96 s (CapabilitiesCmd after
            #                           inventory; ReadCmd after DpId=8 ReadAns).
            #                           After Initialize() returns, the iOS app may take
            #                           10-20 s before sending NotifyEnable frames (async
            #                           UI rendering, possible cloud-call timeout).
            #   _in_notify_mode (True) — 120 s: app has subscribed at least one DpId and
            #                            entered passive monitoring mode.  It sends no more
            #                            I-frames; it only responds with S-RR ACKs to our
            #                            NOTIFY_DATA pushes.  30 s fires long before the app
            #                            would naturally disconnect.
            _first_frame = True
            _in_notify_mode = False  # set True on first NotifyAck; extends receive timeout
            # Phase 2 protocol:
            #   1. Detect end of Phase 2: EVENT_STORAGE_INVENTORY + READ DpId=8.
            #   2. Mock sends HDLC DISC to signal "Phase 2 done".
            #   3. iOS responds UA (discarded by loop), then sends Phase 2.5 reads
            #      automatically on the same BLE link (~2.3 s total):
            #        READ DpId=589 (DAYS_UNTIL_NEXT_DESCALING)
            #        READ DpId=585 (DESCALING_STATUS)
            #        READ DpId=983 (DESCALING_DEVICE_LOCK_STATUS)
            #        WRITE DpId=802 (START_USER_SESSION) <- save screen appears
            #   4. User presses Save -> iOS sends Phase 3 SABM on the same BLE link.
            #      Loop handles it, restarting the handshake for Phase 3.
            #
            # BLE must stay alive after DISC — dropping it kills the save screen
            # (bz.IsConnected=false -> dialog closes).  No fast-disconnect here.
            _phase2_saw_storage = False
            _disc_sent = False
            while True:
                if _first_frame:
                    _timeout = 5.0
                elif _ble_session_phase >= 3:
                    # Phase 3: KE + Inventory + GetEndProduct.  Give plenty of time.
                    _timeout = 30.0
                elif _ble_session_phase >= 2 and _disc_sent:
                    # DISC sent; Phase 2.5 reads land in ~2.3 s automatically, then
                    # the save screen stays open until the user presses Save (Phase 3 SABM).
                    _timeout = 25.0
                elif _ble_session_phase >= 2:
                    # Before DISC: covers the ~2 s gap between Phase 2 sub-phase A
                    # (quick KE + READ) and sub-phase B (full inventory).
                    # Must be > 2 s or it fires mid-inventory.
                    _timeout = 3.0
                elif _in_notify_mode:
                    # App is in passive NOTIFY monitoring — no more I-frames expected until
                    # it disconnects or sends a NotifyDisable.  The session must stay alive
                    # so the app can receive server-initiated NOTIFY_DATA pushes.
                    _timeout = 120.0
                else:
                    # Phase 1: iOS shows the PIN dialog after this; the inter-session gap
                    # can be long if the user is slow.  Keep a generous timeout.
                    _timeout = 30.0
                print(f"[MockServer] ⏳ waiting for next frame (timeout={_timeout:.0f}s, tx_seq={self._tx_seq})")
                try:
                    ft, ctrl, payload = await asyncio.wait_for(
                        self._rx_queue.get(), timeout=_timeout
                    )
                except asyncio.TimeoutError:
                    if _ble_session_phase >= 2:
                        if _disc_sent:
                            print(f"[MockServer] save-screen timeout — user did not press Save within {_timeout:.0f} s of DISC")
                        else:
                            print(f"[MockServer] Phase {_ble_session_phase} fallback timeout — "
                                  f"no frames for {_timeout:.0f} s")
                        self.should_disconnect_after = True
                    else:
                        # Client went silent — session is over.  Return WITHOUT setting
                        # disconnected_by_peer so the caller can force a BlueZ-side
                        # disconnect, which makes advertising resume immediately rather
                        # than waiting ~30 s for the supervision timeout on the
                        # CONWISE/CSR USB adapter.
                        print(f"[MockServer] no frames for {_timeout:.0f} s — ending session to resume advertising")
                    return

                if ft == 'DISCONNECT':
                    print("[MockServer] BLE connection closed — exiting frame loop immediately")
                    self.disconnected_by_peer = True
                    return
                if ft == 'U' and ctrl == self._u_ctrl(_HDLC_SABM_TYPE):
                    print("[MockServer] SABM received in frame loop — sending UA and restarting handshake")
                    new_sabm = True
                    break
                if ft != 'I':
                    continue  # S-frame ACK or stray U-frame
                _first_frame = False  # session is active; switch to 1 s inter-frame timeout
                if not payload or payload[0] != _SEC_ENCRYPTED:
                    sec_str = f"0x{payload[0]:02X}" if payload else "0x??"
                    print(f"[MockServer] unexpected I-frame sec_type={sec_str} — ignored")
                    continue

                decrypted = self._rx_cipher.process(payload[1:])
                plaintext = _inner_cobs_decode(decrypted)
                if plaintext is None:
                    print(f"[MockServer] ← inner COBS decode failed: {decrypted.hex()}")
                    continue
                print(f"[MockServer] ← encrypted frame DECRYPTED: {plaintext.hex()}")

                if app_handler is not None:
                    responses = await app_handler(plaintext)
                    for resp in responses:
                        if resp and resp[0] == CommandId.NotifyAck:
                            _in_notify_mode = True
                        encrypted_resp = self._tx_cipher.process(_inner_cobs_encode(resp))
                        att_frame = self._att_i(bytes([_SEC_ENCRYPTED]) + encrypted_resp)
                        try:
                            await asyncio.wait_for(send_fn(att_frame), timeout=5.0)
                        except asyncio.TimeoutError:
                            print(f"[MockServer] ERROR: send_fn blocked >5 s for cmd=0x{resp[0]:02X}")
                            return False
                        print(f"[MockServer] → cmd=0x{resp[0]:02X} N(S)={self._tx_seq - 1 & 7}")
                        if send_delay_sec > 0:
                            await asyncio.sleep(send_delay_sec)
                    # Drain any S-RR ACKs the app sent — the real device fires all notifications
                    # without blocking on per-frame ACKs; the app's auto-S-RR just accumulates here.
                    _drained = 0
                    while not self._srr_queue.empty():
                        self._srr_queue.get_nowait()
                        _drained += 1
                    if _drained:
                        print(f"[MockServer] drained {_drained} S-RR(s) after burst")
                    # Real-time Phase 2 done detection: EVENT_STORAGE_INVENTORY + READ DpId=8.
                    # Send HDLC DISC to signal end of Phase 2 HDLC session.
                    # iOS responds UA (discarded), then sends Phase 3 SABM on same BLE link
                    # OR disconnects BLE and reconnects for Phase 3.  Keep frame loop running
                    # so the Phase 3 SABM or GetEndProduct (0xD0) arrives here.
                    if _ble_session_phase == 2 and not _disc_sent:
                        _cmd = plaintext[0] if plaintext else 0
                        if _cmd == CommandId.EventStorageInventory:
                            _phase2_saw_storage = True
                        elif _phase2_saw_storage and _cmd == CommandId.ReadCmd:
                            _disc_type = 8  # DISC U-frame ctrl = 0x43
                            await send_fn(self._att_u(_disc_type))
                            _disc_sent = True
                            print("[MockServer] Phase 2 complete — sent DISC; BLE stays alive for "
                                  "Phase 2.5 reads (DpId=589/585/983/802) then Phase 3 SABM")
                else:
                    # Fallback: fake Legacy GetDeviceIdentification response.
                    fake_resp = bytes([
                        0x24, 0x00, 0x00,           # SINGLE frame header, counter=0
                        0x00, 0x82, 0x00,           # ctx=0x00, proc=GetDeviceIdentification, OK
                        0x41, 0x63, 0x41, 0x6C,     # "AcAl"
                        0x62, 0x61, 0x00, 0x00,     # "ba\x00\x00"
                        0x00, 0x00, 0x00, 0x00,     # padding
                        0x00, 0x00,                 # padding (total = 20 bytes)
                    ])
                    encrypted_resp = self._tx_cipher.process(_inner_cobs_encode(fake_resp))
                    att_frame = self._att_i(bytes([_SEC_ENCRYPTED]) + encrypted_resp)
                    expected_nr = self._tx_seq
                    await send_fn(att_frame)
                    print("[MockServer] → fake GetDeviceIdentification response (encrypted)")
                    try:
                        await self._await_s_rr(expected_nr, timeout=2.0)
                    except asyncio.TimeoutError:
                        pass

            if not new_sabm:
                return
            # SABM received mid-session: loop back and re-do the handshake.
            # The coordinator is waiting for UA — we send it at the top of the loop.
            # Reset HDLC sequence counters: after SABM both sides start fresh at 0.
            # Without this, N(S) continues from Phase 1's final value; the app
            # expects N(S)=0 for the first Phase 2 frame and immediately rejects it,
            # causing "cannot connect" at ~7 s on iOS.
            self._tx_seq = 0
            self._rx_ack = 0
            # Drain any stale S-RR ACKs left over from Phase 1 so Phase 2's
            # _await_s_rr() calls don't match Phase 1 N(R) values.
            while not self._srr_queue.empty():
                self._srr_queue.get_nowait()
            self.handshake_done = False
            need_sabm = False  # SABM already consumed from queue


# ---------------------------------------------------------------------------
# GATT services
# ---------------------------------------------------------------------------

class GeberitServiceA(Service):
    def __init__(self):
        super().__init__("559eb100-2390-11e8-b467-0ed5f89f718b", True)

    @characteristic("559eb101-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE_WITHOUT_RESPONSE)
    def write_char(self, options):
        pass

    @write_char.setter
    def write_char(self, value, options):
        print(f"Geberit A [Write]: {bytes(value).hex()}")

    @characteristic("559eb110-2390-11e8-b467-0ed5f89f718b", CharFlags.READ)
    def read_char(self, options):
        # 16 bytes — deliberately NOT 14 or 18.
        # Ble20Product.Initialize() only parses OtaVersion from exactly 14- or 18-byte
        # responses; any other length leaves OtaVersion = null.
        # With OtaVersion null no HTTP call is made to mobileappsv1.services.geberit.com,
        # so Phase 2 (GetEndProduct) proceeds.  18 bytes would set OtaVersion -> HTTP call
        # -> server returns {"Data": null} -> exception -> Phase 2 blocked (confirmed via
        # Charles Proxy HAR 2026-06-13 and btsnoop analysis 2026-06-13).
        #
        # 16 bytes (deliberately NOT 14 or 18: OtaVersion stays null -> no HTTP gate).
        #
        # Byte layout (kstr GATT handle 0x0010, bytes 0-15):
        #   bytes 0-1:   LoaderVersion major/minor = 05 06 (kstr real)
        #   bytes 2-3:   DeviceSeries LE = 00 00  <- intentionally NOT 250 (FA 00)
        #   bytes 4-7:   DeviceUniqueId LE = D1 4C 13 02 (kstr real)
        #   bytes 8-15:  ChipId/ChipRevision/WirelessFw (kstr real)
        #   bytes 16-17: FusVersion omitted -> 16 bytes total
        #
        # Why bytes 2-3 = 00 00 (not FA 00 = 250):
        #   _E008() reads bytes 2-3 as DeviceSeries LE.  FA 00 = 250 is a recognised
        #   series -> _E008() = true -> _E004() called -> Device Name reads + HTTP
        #   GetDeviceApiMinVersions -> no cache on test device -> throws -> "cannot connect".
        #   00 00 = 0 is NOT recognised -> _E008() = false -> _E004() skipped -> Phase 2
        #   proceeds.  DpId=0=250 is still required (separate device-type check before
        #   _E008()); these two values do NOT need to match.
        #   Confirmed from mock 1.3.0: bytes 2-3 = 62 65 (unrecognised), DpId=0=250 -> worked.
        return bytes([
            0x05, 0x06,             # LoaderVersion = 5.6 (bytes 0-1, kstr real)
            0x00, 0x00,             # DeviceSeries=0 unrecognised -> _E008()=false (bytes 2-3)
            0xD1, 0x4C, 0x13, 0x02, # DeviceUniqueId LE (bytes 4-7, kstr real)
            0x95, 0x04,             # ChipId LE (bytes 8-9, kstr)
            0x03, 0x20,             # ChipRevision LE (bytes 10-11, kstr)
            0x01, 0x0E, 0x01, 0x01, # WirelessFirmwareVersion (bytes 12-15)
            # bytes 16-17 (FusVersion 02 00) intentionally omitted -> 16 bytes total
        ])


class BtSigDataService(Service):
    def __init__(self, mode: str = "unsupported"):
        super().__init__("0000fd48-0000-1000-8000-00805f9b34fb", True)
        self._mode = mode
        self._notify_value = bytes([0])
        self._arendi = _AriendiServerSide() if mode in ("handshake", "ble20") else None
        # Populated after register() to enable pushing notifications.
        # bluez_peripheral stores characteristic _Characteristic objects in
        # Service._chars in declaration order: [0]=sig_write, [1]=sig_notify.
        # Each _Characteristic IS a dbus-next ServiceInterface; calling
        # emit_properties_changed({'Value': Variant('ay', data)}) on it causes
        # BlueZ to send a BLE ATT Handle Value Notification to subscribed clients.
        self._notify_char_iface = None

    def set_notify_char_iface(self, iface):
        """Called by AlbaMock.run() after register() to wire up the notification sender."""
        self._notify_char_iface = iface

    async def send_notify(self, att_bytes: bytes) -> None:
        """Push ATT bytes to the connected BLE client via NOTIFY."""
        self._notify_value = att_bytes
        if self._notify_char_iface is not None:
            if hasattr(self._notify_char_iface, 'changed'):
                self._notify_char_iface.changed(att_bytes)
            else:
                self._notify_char_iface.emit_properties_changed(
                    {'Value': Variant('ay', list(att_bytes))}
                )
        else:
            print("[MockServer] WARNING: notify char interface not set — cannot send notification")

    @characteristic("559eb001-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE_WITHOUT_RESPONSE)
    def sig_write(self, options):
        pass

    @sig_write.setter
    def sig_write(self, value, options):
        data = bytes(value)
        print(f"[BLE←] {len(data)} B  {data.hex()}")
        if self._mode in ("handshake", "ble20") and self._arendi is not None:
            self._arendi.feed(data)
        else:
            print(f"Data Channel [Write]: {data.hex()}")

    @characteristic("559eb002-2390-11e8-b467-0ed5f89f718b", CharFlags.NOTIFY)
    def sig_notify(self, options):
        return self._notify_value


class DeviceInformationService(Service):
    """Standard BLE Device Information Service (0x180a) with real Alba values from kstr's device.

    Source: local-assets/Android-BLE-Logs/kstr/aquaclean2.log (E4:85:01:CD:6B:04)
    Note: model_number is the SAP article number, NOT the Geberit product name ("AcAlba").
    "AcAlba" is only available via proc 0x82 GetDeviceIdentification (application layer).
    """

    def __init__(self):
        super().__init__("0000180a-0000-1000-8000-00805f9b34fb", True)

    @characteristic("00002a29-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def manufacturer_name(self, options):
        return b"Geberit"

    @characteristic("00002a24-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def model_number(self, options):
        return b"828.860.00.A"

    @characteristic("00002a25-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def serial_number(self, options):
        return b"93136"

    @characteristic("00002a26-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def firmware_revision(self, options):
        return b"RS03TS89"

    @characteristic("00002a27-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def hardware_revision(self, options):
        return b"00"

    @characteristic("00002a28-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def software_revision(self, options):
        return b"1.14.1 1.2.0"


class BatteryService(Service):
    """Standard BLE Battery Service (0x180F).

    BlueZ auto-registers its own Battery Service which requires authentication
    (Insufficient Authentication, ATT error 0x05) for reads.  Registering our
    own service overrides it with an unauthenticated READ, silencing the
    spurious error from iOS without affecting Phase 1 or Phase 2.
    """

    def __init__(self):
        super().__init__("0000180f-0000-1000-8000-00805f9b34fb", True)

    @characteristic("00002a19-0000-1000-8000-00805f9b34fb", CharFlags.READ)
    def battery_level(self, options):
        return bytes([100])


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def safe_call(obj, method_name, *args, **kwargs):
    fn = getattr(obj, method_name, None)
    if not fn:
        return False
    try:
        if inspect.iscoroutinefunction(fn):
            await fn(*args, **kwargs)
        else:
            result = fn(*args, **kwargs)
            if inspect.isawaitable(result):
                await result
        return True
    except Exception as e:
        print(f"Cleanup: calling {method_name} raised: {e}")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

class AlbaMock:
    """One Alba BLE peripheral mock instance.

    `main(mode, send_delay_sec, web_port, adapter_name)` from the original
    script becomes `__init__` (stores config) + `run()` (the orchestration
    body, otherwise unchanged — its internal nested closures already operate
    on locals of this method, not module globals, so wrapping it in a class
    needed no further changes to them).

    `adapter`: BlueZ node name (e.g. "hci1") to bind to, or None for "first
    found". Threaded through to GATT/advertisement registration (via
    mock_bluez_adapter.select_adapter) and to D-Bus GATT application object
    paths (so two instances don't collide).
    """

    def __init__(self, adapter: str | None = None, mode: str = "unsupported",
                 send_delay_sec: float = 0.0, web_port: int = 8765, state_dir=None):
        self.adapter = adapter
        self.mode = mode
        self.send_delay_sec = send_delay_sec
        self.web_port = web_port
        self._adapter_tag = adapter or "default"
        # Dead flag today (unused even in the original script) — ported for parity.
        self._verbose = False

        if state_dir is not None:
            # Process-wide: see MeraMock.__init__ for the same note — all mock
            # instances share one DB file, isolated by (device_type, device_key).
            mock_persistence.set_state_dir(state_dir)

    async def run(self):
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

        # Suppress the noisy "does not have property TxPower" error from dbus_next.
        # BlueZ queries TxPower from our LEAdvertisement1 interface; when the property
        # is absent dbus_next logs an ERROR but falls back to no TxPower in the adv —
        # harmless.  Filter it from the root logger to keep output readable.
        import logging as _logging
        class _SuppressDbusPropertyNotFound(_logging.Filter):
            def filter(self, record):
                return "does not have property" not in record.getMessage()
        _logging.getLogger().addFilter(_SuppressDbusPropertyNotFound())

        # No pairing agent registered intentionally. Real Geberit firmware ignores SMP
        # entirely — the device never appears in iOS Bluetooth Settings (no link-layer
        # pairing). Registering an agent makes BlueZ run a full SC key exchange (~4s)
        # before rejecting User Confirmation, during which iOS CoreBluetooth pauses the
        # ATT write queue — DpId=9 never arrives in FrameHandler.m()'s 2000ms window.
        # Without an agent, "Pairing Not Supported" is returned in ~5ms and iOS
        # resumes unencrypted writes immediately. Both DpId=8 and DpId=9 arrive fine.
        _agent_mgr = None

        objmgr = None
        adapter_wrapper = None
        adapter_path = None
        adapter_address = None
        try:
            adapter_wrapper, adapter_path, adapter_address, objmgr = await select_adapter(bus, self.adapter)
            if adapter_wrapper:
                print(f"Adapter wrapper obtained from bluez_peripheral ({adapter_path}).")
            else:
                print("No Bluetooth adapter found via ObjectManager.")
            print("Adapter DBus path:", adapter_path)
            print("Adapter BLE address:", adapter_address,
                  "(controller identity — BlueZ may advertise with a random/rotating address on-air; run 'sudo btmon' to see the actual transmitted address)")
        except ValueError as e:
            print(f"Adapter selection failed: {e}")
            return
        except Exception as e:
            print("Could not read adapter path/address via ObjectManager:", e)

        # Set the BlueZ adapter alias so GATT 0x2a00 (GAP Device Name) returns "AC250"
        # instead of the system hostname.  iOS reads this after the Arendi protocol completes.
        # Real kstr (E4:85:01:CD:6B:04) has Name/Alias = "AC250" in BlueZ cache
        # (aquaclean2.log line 47).  "AcAlba" is the app-internal GeberitDeviceType class name,
        # not the BLE device identity.
        if adapter_path:
            try:
                _adap_intro = await bus.introspect('org.bluez', adapter_path)
                _adap_proxy = bus.get_proxy_object('org.bluez', adapter_path, _adap_intro)
                _adap_props = _adap_proxy.get_interface('org.freedesktop.DBus.Properties')
                await _adap_props.call_set('org.bluez.Adapter1', 'Alias', Variant('s', 'AC250'))
                print("Adapter alias set to 'AC250' (GATT 0x2a00 Device Name)")
            except Exception as e:
                print(f"Warning: could not set adapter alias: {e}")

        if not adapter_wrapper:
            print("No adapter wrapper available; cannot register GATT services/advertisement.")
            await bus.disconnect()
            return

        # D-Bus GATT application paths, tagged with the adapter so two instances
        # don't collide (the original script hardcoded these — fine for one
        # instance per process, not fine once mock_service.py runs several).
        # Prefixed with the model name, not just the adapter: "battery"/"dis" are generic
        # service names that MeraMock also uses — tagging by adapter alone would collide
        # if an Alba and a Mera mock ever share one adapter (Phase 5, mock_service.py).
        app_paths = {
            "geberit": f"/org/bluez/example/alba_geberit_{self._adapter_tag}",
            "sigdata": f"/org/bluez/example/alba_sigdata_{self._adapter_tag}",
            "dis": f"/org/bluez/example/alba_dis_{self._adapter_tag}",
            "battery": f"/org/bluez/example/alba_battery_{self._adapter_tag}",
        }

        # Track the BlueZ object path and address of the currently connected BLE client.
        # Used to force-disconnect when a session ends by timeout (not by peer).
        _connected_device_path = None
        _connected_device_addr = None
        # Pre-introspected Device1 proxy interface — avoids the 2+ s bus.introspect() round
        # trip at disconnect time.  Set when a device connects; cleared after disconnect.
        _connected_dev_iface = None

        async def _cache_dev_iface(path):
            nonlocal _connected_dev_iface
            try:
                intro = await bus.introspect('org.bluez', path)
                proxy = bus.get_proxy_object('org.bluez', path, intro)
                _connected_dev_iface = proxy.get_interface('org.bluez.Device1')
                print(f"[Mock] Device1 interface cached for fast disconnect: {path}")
            except Exception as e:
                print(f"[Mock] Pre-introspect failed (fast disconnect unavailable): {e}")

        # Sent once per mock session to force the BLE client (HAOS) to re-discover GATT handles.
        # HAOS BlueZ caches handles from prior sessions (via Database Hash); those cached handles
        # become stale when the Ubuntu VM's BlueZ assigns different handles (due to system GATT
        # services registered before the mock).  Unregister + re-register triggers Service Changed
        # on the client, which invalidates its cache.  Only fires once to avoid disrupting ongoing
        # Arendi handshakes on subsequent connections.
        _service_changed_sent = [False]

        async def _send_service_changed():
            if adapter_path is None:
                return
            try:
                _ai = await bus.introspect('org.bluez', adapter_path)
                _ap = bus.get_proxy_object('org.bluez', adapter_path, _ai)
                _gm = _ap.get_interface('org.bluez.GattManager1')
                _app = app_paths["sigdata"]
                await _gm.call_unregister_application(_app)
                await _gm.call_register_application(_app, {})
                print("[Mock] [GATT] Service Changed sent — client will re-discover GATT handles")
            except Exception as _e:
                print(f"[Mock] [GATT] Service Changed trigger failed (non-fatal): {_e}")

        if objmgr is not None:
            def on_device_connected(path, interfaces):
                nonlocal _connected_device_path, _connected_device_addr, _connected_dev_iface
                if 'org.bluez.Device1' in interfaces:
                    addr = interfaces['org.bluez.Device1'].get('Address')
                    if isinstance(addr, Variant):
                        addr = addr.value
                    _connected_device_addr = addr
                    _connected_device_path = path
                    _connected_dev_iface = None  # clear stale cache; new one coming
                    asyncio.ensure_future(_cache_dev_iface(path))
                    if not _service_changed_sent[0]:
                        _service_changed_sent[0] = True
                        asyncio.ensure_future(_send_service_changed())
                    print(f"[Mock] BLE client connected:    {addr or path}")

            def on_device_disconnected(path, interfaces):
                if 'org.bluez.Device1' in interfaces:
                    # Do NOT clear _connected_device_path here.  The handshake loop
                    # always calls force-disconnect after a session and clears the path
                    # there.  Clearing it here would prevent the force-disconnect when
                    # BlueZ fires the event before the session handler runs (which is
                    # the case when the connection is terminated cleanly by the peer).
                    print(f"[Mock] BLE client disconnected: {_connected_device_addr or path}")
                    # Signal the running arendi session to exit immediately so advertising
                    # resumes right away — without this the frame loop waits 60 s for more
                    # frames and the mock stays "occupied" blocking the next BLE client.
                    arendi = sig_service._arendi
                    if arendi is not None:
                        try:
                            arendi._rx_queue.put_nowait(('DISCONNECT', 0, b''))
                        except Exception:
                            pass

            objmgr.on_interfaces_added(on_device_connected)
            objmgr.on_interfaces_removed(on_device_disconnected)

        geb_service = GeberitServiceA()
        sig_service = BtSigDataService(mode=self.mode)
        dis_service = DeviceInformationService()
        bat_service = BatteryService()

        try:
            await geb_service.register(bus, app_paths["geberit"], adapter_wrapper)
            await sig_service.register(bus, app_paths["sigdata"], adapter_wrapper)
            await dis_service.register(bus, app_paths["dis"], adapter_wrapper)
            await bat_service.register(bus, app_paths["battery"], adapter_wrapper)
        except Exception as e:
            print("Service registration failed:", e)

        # Diagnostic: confirm CharFlags values and what D-Bus flags sig_write is advertising.
        print(f"[Diag] CharFlags: READ={CharFlags.READ.value} WRITE_WITHOUT_RESPONSE={CharFlags.WRITE_WITHOUT_RESPONSE.value} WRITE={CharFlags.WRITE.value} NOTIFY={CharFlags.NOTIFY.value}")
        for attr in ('_characteristics', '_chars'):
            chars = getattr(sig_service, attr, None)
            if not chars:
                continue
            for c in chars:
                uuid_str = str(getattr(c, '_uuid', getattr(c, 'uuid', '?')))
                flags_val = getattr(c, 'flags', getattr(c, '_flags', '?'))
                service_ref = getattr(c, '_service', 'NOT SET')
                setter_ref = getattr(c, '_setter_func', 'NOT SET')
                dbus_flags = None
                if hasattr(c, '_get_flags'):
                    try:
                        dbus_flags = c._get_flags()
                    except Exception as e:
                        dbus_flags = f"ERROR: {e}"
                flags_int = flags_val.value if hasattr(flags_val, 'value') else (flags_val if isinstance(flags_val, int) else '?')
                print(f"[Diag] char {uuid_str}: flags={flags_val}({flags_int}) dbus_flags={dbus_flags} _service={'set' if service_ref not in (None,'NOT SET') else service_ref} _setter={'set' if setter_ref not in (None,'NOT SET') else setter_ref}")
            break

        # Wire up the notify characteristic interface so send_notify() can push frames.
        # Search whichever attribute holds characteristics (_characteristics in newer
        # bluez_peripheral, _chars in older versions) and find the NOTIFY char by flags
        # rather than by index — the order differs across library versions.
        notify_char = None
        for attr in ('_characteristics', '_chars'):
            chars = getattr(sig_service, attr, None)
            if chars:
                for c in chars:
                    if hasattr(c, 'flags') and CharFlags.NOTIFY in c.flags:
                        notify_char = c
                        break
            if notify_char:
                break
        if notify_char is not None:
            sig_service.set_notify_char_iface(notify_char)
            print("Notify characteristic interface wired.")
        else:
            print("WARNING: could not find notify characteristic — notifications disabled")

        # Advertisement payload must fit within 31 bytes (BLE ADV_IND limit).
        # Real Alba advertises: fd48 16-bit UUID (4 B) + mfr data (19 B) + flags (3 B) = 26 B.
        # A long local name or 128-bit UUIDs push the total over the limit and BlueZ rejects
        # the registration with "Advertising data too long".
        #
        # Company ID 0x0602 = Geberit International AG — required by the Geberit Home App's
        # CheckDiscovered filter (BleProductManager.cs). fd48 alone satisfies the Stage 1
        # UUID scan filter (FD48 is listed explicitly). 559eb100-... is in the GATT service
        # and will be discovered after connection — it must NOT be in the advertisement.
        adv = _Advertisement(
            "",                                          # no local name — matches real device
            ["0000fd48-0000-1000-8000-00805f9b34fb"],   # fd48 only; 559eb100 stays in GATT
            appearance=0,
            timeout=0,
            manufacturerData={0x0602: bytes([0x02, 0xFA, 0x00] + list(b'AC250') + [0x00] * 7)},
        )

        # Intercept bus.export() during adv.register() to capture the advertisement D-Bus path.
        # We need the path to unregister and re-register with a 100 ms interval (see below).
        # Checking bus._path_exports AFTER register() fails because bluez_peripheral may
        # export-then-unexport the adv object — BlueZ caches properties at registration time
        # so the export is only needed transiently.  The interceptor fires during the call.
        _captured_adv_path = [None]
        _pre_existing_paths = set(getattr(bus, '_path_exports', {}).keys())
        _orig_bus_export = bus.export

        def _capturing_export(path, interface):
            _orig_bus_export(path, interface)
            if path not in _pre_existing_paths and _captured_adv_path[0] is None:
                _captured_adv_path[0] = path

        bus.export = _capturing_export
        adv_registered = False
        try:
            await adv.register(bus, adapter_wrapper)
            adv_registered = True
        except Exception as e:
            print("Advertisement registration failed:", e)
        finally:
            bus.export = _orig_bus_export

        # Fix: re-register with MinInterval/MaxInterval=100 ms so Phase 3 connects in <1 s.
        #
        # Root cause of iOS crash (2026-06-13, mock 2.5.0):
        #   After Phase 2 BLE disconnect the iOS app calls GetEndProduct() ~1.1 s later.
        #   GetEndProduct() -> b.c() -> FrameHandler.l() checks TunnelDataExchange.IsConnected
        #   (= bz.IsConnected).  bz.IsConnected is only restored to True when Phase 3
        #   connects and runs Ble20Product.Initialize() -> bz.InitializeDataExchange().
        #   With BlueZ default 1280 ms interval iOS takes ~15 s to find the mock for Phase 3
        #   — far longer than the ~1.1 s GetEndProduct() window -> SIGABRT.
        #   Real hardware advertises at ~100 ms so Phase 3 connects in <1 s.
        if adv_registered and adapter_path:
            _adv_path = _captured_adv_path[0]
            if _adv_path is None:
                # Interceptor got nothing — bluez_peripheral doesn't use bus.export() for adv.
                # Last-resort: check if the library stores the path as an attribute.
                _adv_path = getattr(adv, '_path', None)
                if _adv_path is None:
                    for _attr in dir(adv):
                        if 'path' in _attr.lower() and not _attr.startswith('__'):
                            _val = getattr(adv, _attr, None)
                            if isinstance(_val, str) and _val.startswith('/'):
                                _adv_path = _val
                                break
            if _adv_path:
                try:
                    _ai = await bus.introspect('org.bluez', adapter_path)
                    _ap = bus.get_proxy_object('org.bluez', adapter_path, _ai)
                    _am = _ap.get_interface('org.bluez.LEAdvertisingManager1')
                    await _am.call_unregister_advertisement(_adv_path)
                    # Re-export adv so BlueZ can read its properties when re-registering.
                    # bluez_peripheral may have unexported it after the initial registration.
                    if _adv_path not in getattr(bus, '_path_exports', {}):
                        bus.export(_adv_path, adv)
                    await _am.call_register_advertisement(_adv_path, {
                        'MinInterval': Variant('u', 100),
                        'MaxInterval': Variant('u', 100),
                    })
                    print(f"[Mock] Advertising interval set to 100 ms via {_adv_path} (Phase 3 connects in <1 s)")
                except Exception as _e:
                    print(f"[Mock] Warning: fast advertising interval not set ({_e})")
                    print("[Mock]   Needs BlueZ >= 5.58 and LEAdvertisingManager1 support")
            else:
                print("[Mock] Warning: adv path unknown — advertising at BlueZ default 1280 ms")
                print("[Mock]   Phase 3 may not connect within the iOS app's ~1 s window -> crash")

        print(f"--- Mock Device Active (mode={self.mode}) ---")
        print(f"    mock: {_MOCK_VERSION}  script: {_SCRIPT_HASH}  bridge: {_BRIDGE_VERSION}")
        print("Advertising: fd48 + Geberit mfr data (company 0x0602)")

        async def _handshake_loop():
            nonlocal _connected_device_path, _connected_dev_iface
            app_handler = _Ble20AppLayer(notify_queue=_notify_push_queue, device_key=self._adapter_tag).dispatch if self.mode == "ble20" else None

            async def _fast_disconnect():
                """Disconnect BLE using pre-cached or freshly enumerated Device1 interface.

                If InterfacesAdded never fired (unreliable on some BlueZ/adapter setups),
                falls back to ObjectManager enumeration — ~2 s total, still within the
                6+ s window before the user presses Save.
                """
                iface = _connected_dev_iface
                if iface is None and objmgr is not None:
                    print("[Mock] Fast-disconnect: no cached interface — enumerating ObjectManager")
                    try:
                        _managed = await objmgr.call_get_managed_objects()
                        for _p, _ifs in _managed.items():
                            if 'org.bluez.Device1' not in _ifs:
                                continue
                            _c = _ifs['org.bluez.Device1'].get('Connected')
                            if isinstance(_c, Variant):
                                _c = _c.value
                            if _c:
                                _intro = await bus.introspect('org.bluez', _p)
                                _proxy = bus.get_proxy_object('org.bluez', _p, _intro)
                                iface = _proxy.get_interface('org.bluez.Device1')
                                print(f"[Mock] Fast-disconnect: found device at {_p}")
                                break
                    except Exception as e:
                        print(f"[Mock] Fast-disconnect: ObjectManager enumeration failed: {e}")
                if iface is None:
                    print("[Mock] Fast-disconnect: device not found — end-of-session cleanup will disconnect")
                    return
                try:
                    await iface.call_disconnect()
                    print("[Mock] Fast-disconnect: BLE link terminated")
                except Exception as e:
                    print(f"[Mock] Fast-disconnect: {e}")
            _user_sitting = False
            _session_num = 0
            _session_pre_created = False  # True when cleanup pre-created the next session
            while True:
                _session_num += 1
                print(f"\n[Mock] ===== SESSION {_session_num} — waiting for client =====")
                if not _session_pre_created:
                    sig_service._arendi = _AriendiServerSide()
                    if self.mode == "ble20":
                        _ble20_app = _Ble20AppLayer(notify_queue=_notify_push_queue, device_key=self._adapter_tag)  # fresh store per session
                        if _user_sitting:
                            _ble20_app._store[(607, None)]['value'] = bytearray(b'\x01')
                            _ble20_app._store[(564, None)]['value'] = bytearray(b'\x02')  # Ready (app source: 0=Err,1=Disabled,2=Ready,3=Prerinsing,4=Extending,5=Shower,6=Retracting,7=Postrinsing)
                        app_handler = _ble20_app.dispatch
                        _ble20_app_ref[0] = _ble20_app
                        _ui_notify_state["607"] = _user_sitting
                        _ui_notify_state["564"] = 2 if _user_sitting else 1  # 2=ready, 1=disabled
                    # Reset notify subscription state so the next BLE client can subscribe.
                    # bluez_peripheral sets _notifying=True in StartNotify() and never resets
                    # it on an abrupt BLE disconnect (no StopNotify() is called).  On the
                    # second connection, StartNotify() raises "Already notifying" -> BlueZ
                    # returns ATT error 0x01 to the ESP32 -> "Invalid handle" -> poll failure.
                    if notify_char is not None and hasattr(notify_char, '_notifying'):
                        notify_char._notifying = False
                _session_pre_created = False  # consume the flag
                _completed = None
                _session_completed = False
                try:
                    _completed = await sig_service._arendi.run(sig_service.send_notify, app_handler=app_handler, send_delay_sec=self.send_delay_sec, disconnect_fn=None)
                    _session_completed = True   # run() returned — a client connected
                    if _completed is not False:
                        print("[MockServer] session complete — waiting for next client (Ctrl-C to quit)")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    msg = str(exc)
                    if msg:
                        print(f"[MockServer] ERROR: {msg}")
                    else:
                        print("[MockServer] session timed out — waiting for next client (Ctrl-C to quit)")

                # Only toggle when a client actually connected and completed a session.
                # Timeouts (no client within 60 s) raise an exception so _session_completed
                # stays False — the sitting state is preserved until the next real poll.
                by_peer = getattr(sig_service._arendi, 'disconnected_by_peer', False)
                # should_disconnect_after: Phase 2 timeout fired (no Phase 3 SABM/GetEndProduct).
                # Treat like by_peer so the next session is pre-created immediately and
                # advertising resumes quickly in case Phase 3 arrives as a new BLE session.
                should_disconnect = getattr(sig_service._arendi, 'should_disconnect_after', False)
                if _session_completed and _completed is not False:
                    _user_sitting = not _user_sitting
                    print(f"[Mock] Next session USER_DETECTION_STATUS -> {'1 (sitting)' if _user_sitting else '0 (absent)'}")

                # Pre-create next session immediately when:
                #   by_peer=True  — iOS disconnected the BLE link
                #   should_disconnect=True — Phase 2 short-timeout: mock is about to disconnect
                # Both cases: iOS connects Phase 3 within ~400 ms.  Pre-creating now ensures
                # the Phase 3 SABM lands in the correct session queue, not the defunct one.
                if by_peer or should_disconnect:
                    sig_service._arendi = _AriendiServerSide()
                    if self.mode == "ble20":
                        _ble20_app = _Ble20AppLayer(notify_queue=_notify_push_queue, device_key=self._adapter_tag)
                        if _user_sitting:
                            _ble20_app._store[(607, None)]['value'] = bytearray(b'\x01')
                            _ble20_app._store[(564, None)]['value'] = bytearray(b'\x02')  # Ready (app source: 0=Err,1=Disabled,2=Ready,3=Prerinsing,4=Extending,5=Shower,6=Retracting,7=Postrinsing)
                        app_handler = _ble20_app.dispatch
                        _ble20_app_ref[0] = _ble20_app
                        _ui_notify_state["607"] = _user_sitting
                        _ui_notify_state["564"] = 2 if _user_sitting else 1  # 2=ready, 1=disabled
                    if notify_char is not None and hasattr(notify_char, '_notifying'):
                        notify_char._notifying = False
                    _session_pre_created = True

                # Always force a BlueZ-side disconnect after every session so advertising
                # resumes immediately (< 100 ms) rather than waiting for the supervision
                # timeout (~30 s on the CONWISE/CSR USB adapter in UTM).
                #
                # When the peer disconnected cleanly, BlueZ may have fired InterfacesRemoved
                # already, in which case call_disconnect() returns NotConnected — caught and
                # ignored.  When the CONWISE adapter missed the LL_TERMINATE_IND (L2CAP bug),
                # the BLE link is still alive at the link layer; call_disconnect() terminates
                # it and advertising resumes within 100 ms.
                #
                # _connected_device_path is intentionally NOT cleared by on_device_disconnected
                # so it is always available here regardless of which path ended the session.
                # Fallback: if on_device_connected never fired (BlueZ did not emit
                # InterfacesAdded for the Device1 object), enumerate all BlueZ Device1
                # objects with Connected=True.  This guarantees a disconnect even when
                # the InterfacesAdded signal was missed.
                _path_to_disconnect = _connected_device_path
                _connected_device_path = None  # clear before attempt — not needed after
                if _path_to_disconnect is None and objmgr is not None:
                    try:
                        _managed = await objmgr.call_get_managed_objects()
                        for _p, _ifaces in _managed.items():
                            if 'org.bluez.Device1' not in _ifaces:
                                continue
                            _c = _ifaces['org.bluez.Device1'].get('Connected')
                            if isinstance(_c, Variant):
                                _c = _c.value
                            if _c:
                                _path_to_disconnect = _p
                                print(f"[Mock] Found connected device via ObjectManager: {_p}")
                                break
                    except Exception as _oe:
                        print(f"[Mock] ObjectManager enumeration failed: {_oe}")
                if _path_to_disconnect is not None:
                    print(f"[Mock] Forcing BlueZ disconnect to resume advertising{' (already disconnected by peer — call may fail)' if by_peer else ''}: {_path_to_disconnect}")
                    try:
                        introspect = await bus.introspect('org.bluez', _path_to_disconnect)
                        proxy = bus.get_proxy_object('org.bluez', _path_to_disconnect, introspect)
                        dev_iface = proxy.get_interface('org.bluez.Device1')
                        await dev_iface.call_disconnect()
                        print("[Mock] Force-disconnect sent; advertising resumes immediately")
                    except Exception as _e:
                        if by_peer:
                            print(f"[Mock] Force-disconnect on already-disconnected device (expected): {_e}")
                        else:
                            print(f"[Mock] Force-disconnect failed: {_e}")

                # BlueZ resumes advertising automatically after a BLE disconnect because the
                # advertisement registered at startup remains active.  Calling unregister/
                # re-register here would race with BlueZ's own resume and cause "Already
                # Exists" failures, leaving the mock dark for up to 60 s.  Just let BlueZ
                # do its job.
                # Skip the 0.3 s delay when Phase 3 is expected imminently — it may arrive
                # within 400 ms and the next session is already pre-created above.
                if not (by_peer or should_disconnect):
                    await asyncio.sleep(0.3)
                print("[Mock] Ready for next client")

        # Mutable ref to the current session's _Ble20AppLayer.  Set by _handshake_loop each
        # time a new session is created; read by the web-UI pusher to check subscriptions.
        _ble20_app_ref: list = [None]
        # Queue shared between _Ble20AppLayer (producer: stop-sequence) and web-UI pusher
        # (producer: manual toggles) and the pusher coroutine (consumer: encrypts + sends).
        # Always created so _Ble20AppLayer can use it even when web UI is disabled.
        _notify_push_queue: asyncio.Queue = asyncio.Queue()

        _web_task = None
        _pusher_task = None
        if self.web_port > 0 and self.mode == "ble20":
            if not _WEB_AVAILABLE:
                print("[MockWeb] WARNING: FastAPI/uvicorn not installed — --web-port ignored")
            else:

                _web_app = _FastAPI()
                _static_dir = pathlib.Path(__file__).parent / "static"
                _web_app.mount("/static", _StaticFiles(directory=str(_static_dir)), name="static")

                @_web_app.get("/", response_class=_HTMLResponse)
                async def _web_get_index():
                    s607 = _ui_notify_state["607"]
                    # Read DpId 564 from live store when available; fall back to ui state
                    _cur_app = _ble20_app_ref[0]
                    if _cur_app:
                        _v564 = bytes(_cur_app._store.get((564, None), {}).get('value', b'\x01'))
                        s564 = int.from_bytes(_v564, 'little') if _v564 else 1
                        _serial = bytes(_cur_app._store.get((369, None), {}).get('value', b'')).decode('ascii', 'replace') or "pending..."
                        _pin = bytes(_cur_app._store.get((12, None), {}).get('value', b'')).decode('ascii', 'replace') or "pending..."
                        _sap = bytes(_cur_app._store.get((371, None), {}).get('value', b'')).decode('ascii', 'replace') or "?"
                        _settings_json = json.dumps(_cur_app._settings_table_data())
                    else:
                        s564 = _ui_notify_state["564"]
                        _serial = _pin = "pending..."
                        _sap = "?"
                        _settings_json = json.dumps({"sections": []})
                    b607 = "ON (sitting)" if s607 else "OFF (absent)"
                    b564 = {1: "1 (disabled)", 2: "2 (ready)", 5: "5 (running)"}.get(s564, f"{s564} (?)")
                    c607 = "#2a9" if s607 else "#999"
                    c564 = {"1": "#999", "2": "#2a9", "5": "#e50"}.get(str(s564), "#666")
                    return (
                        "<!DOCTYPE html><html><head><title>Mock Alba NOTIFY</title>"
                        "<meta http-equiv='refresh' content='2'>"
                        "<link rel='stylesheet' href='/static/mock-controls.css'>"
                        "<style>"
                        "body{font-family:monospace;max-width:540px;margin:40px auto;padding:0 20px}"
                        ".row{display:flex;align-items:center;gap:16px;margin:16px 0}"
                        ".lbl{width:290px}.note{font-size:.85em;color:#666}"
                        "button{padding:6px 18px;border:1px solid #888;border-radius:4px;"
                        "cursor:pointer;color:#fff;font-family:monospace}"
                        ".sticker{background:#eee;color:#111;padding:10px 14px;border-radius:4px;"
                        "margin-bottom:20px;line-height:1.5}"
                        "</style></head><body>"
                        "<h2>Mock Alba — NOTIFY Control</h2>"
                        # Per-device identity, generated once and persisted (requirements doc
                        # §5) — mirrors the S/N + PIN printed on a real unit's sticker, so two
                        # mocked Albas (e.g. on hci0/hci1) show distinct values here.
                        "<div class='sticker'>"
                        f"AquaClean Alba {_sap}<br>"
                        f"S/N: {_serial}<br>"
                        f"PIN: {_pin}"
                        "</div>"
                        "<p>Pushes encrypted NOTIFY_DATA to the active BLE client.<br>"
                        "Discarded when no client is connected or the DpId is not subscribed.</p>"
                        f'<div class="row">'
                        f'<span class="lbl">DpId 607 — USER_DETECTION_STATUS'
                        f'<br><span class="note">0=absent &nbsp; 1=sitting'
                        f'<br>also pushes DpId 564: 2=ready/startable, 1=disabled</span></span>'
                        f'<form method="POST" action="/notify/607/toggle">'
                        f'<button style="background:{c607}">{b607} &rarr; Toggle</button></form></div>'
                        f'<div class="row">'
                        f'<span class="lbl">DpId 564 — ANAL_SHOWER_STATUS (live)'
                        f'<br><span class="note">1=disabled &nbsp; 2=ready &nbsp; 5=running'
                        f'<br>standalone toggle cycles 1→2→5→1</span></span>'
                        f'<form method="POST" action="/notify/564/toggle">'
                        f'<button style="background:{c564}">{b564} &rarr; Cycle</button></form></div>'
                        "<h3>Settings</h3>"
                        "<div id='mc-root'></div>"
                        "<script src='/static/mock-controls.js'></script>"
                        f"<script>mcRenderSettingsTable(document.getElementById('mc-root'), {_settings_json});</script>"
                        "</body></html>"
                    )

                @_web_app.post("/settings/dpid/{dp_id}")
                async def _web_write_dpid(dp_id: int, request: _Request):
                    cur_app = _ble20_app_ref[0]
                    if cur_app is None:
                        return _HTMLResponse("No active session", status_code=409)
                    body = await request.json()
                    try:
                        cur_app._write_dpid_setting(dp_id, body["value"])
                    except (KeyError, ValueError) as e:
                        return _HTMLResponse(str(e), status_code=400)
                    return {"ok": True}

                @_web_app.post("/notify/{dp_id}/toggle")
                async def _web_post_toggle(dp_id: str):
                    if dp_id not in ("607", "564"):
                        return _HTMLResponse("Unknown DpId", status_code=400)
                    if dp_id == "607":
                        new_607 = not _ui_notify_state["607"]
                        _ui_notify_state["607"] = new_607
                        await _notify_push_queue.put((607, b'\x01' if new_607 else b'\x00'))
                        # Real device changes ANAL_SHOWER_STATUS (564) atomically:
                        # sitting -> 2 (Ready/startable), absent -> 1 (Disabled).
                        shower_val = 2 if new_607 else 1
                        _ui_notify_state["564"] = shower_val
                        await _notify_push_queue.put((564, bytes([shower_val])))
                    else:
                        # Standalone DpId 564 cycle: 1->2->5->1
                        cur = _ui_notify_state["564"]
                        new_564 = {1: 2, 2: 5, 5: 1}.get(cur, 2)
                        _ui_notify_state["564"] = new_564
                        await _notify_push_queue.put((564, bytes([new_564])))
                    return _RedirectResponse("/", status_code=303)

                async def _pusher():
                    while True:
                        dp_id, value_bytes = await _notify_push_queue.get()
                        arendi = sig_service._arendi
                        ble20_app = _ble20_app_ref[0]
                        if arendi is None or not getattr(arendi, 'handshake_done', False):
                            print(f"[MockWeb] NOTIFY DpId={dp_id} discarded — no active session")
                            continue
                        if ble20_app is None or dp_id not in ble20_app._notify_subscribed:
                            subs = ble20_app._notify_subscribed if ble20_app else set()
                            print(f"[MockWeb] NOTIFY DpId={dp_id} discarded — not subscribed (subscribed: {subs})")
                            continue
                        entry = ble20_app._store.get((dp_id, None))
                        if entry is not None:
                            entry['value'] = bytearray(value_bytes)
                        payload = bytes([CommandId.NotifyData]) + encode_address(dp_id) + value_bytes
                        encrypted = arendi._tx_cipher.process(_inner_cobs_encode(payload))
                        att_frame = arendi._att_i(bytes([_SEC_ENCRYPTED]) + encrypted)
                        await sig_service.send_notify(att_frame)
                        print(f"[MockWeb] → NOTIFY_DATA DpId={dp_id} value={value_bytes.hex()}")

                _pusher_task = asyncio.create_task(_pusher())
                _web_cfg = _uvicorn.Config(
                    _web_app, host="0.0.0.0", port=self.web_port,
                    log_level="warning", loop="none", lifespan="off",
                )
                _web_srv = _uvicorn.Server(_web_cfg)
                _web_srv.install_signal_handlers = lambda: None
                _web_task = asyncio.create_task(_web_srv.serve())
                print(f"[MockWeb] Control UI: http://0.0.0.0:{self.web_port}/  (DpId 607 + 564)")

        server_task = None
        if self.mode in ("handshake", "ble20"):
            print("Waiting for bridge to connect and start handshake (60 s timeout)...")
            server_task = asyncio.create_task(_handshake_loop())

        stop_event = asyncio.Event()
        try:
            if server_task:
                await asyncio.wait(
                    [server_task, asyncio.ensure_future(stop_event.wait())],
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await stop_event.wait()
        except asyncio.CancelledError:
            pass
        except KeyboardInterrupt:
            pass
        finally:
            print("\nShutting down...")
            if _pusher_task and not _pusher_task.done():
                _pusher_task.cancel()
            if _web_task and not _web_task.done():
                _web_task.cancel()
            if server_task and not server_task.done():
                server_task.cancel()
            if _agent_mgr is not None:
                try:
                    await _agent_mgr.call_unregister_agent(_AGENT_DBUS_PATH)
                except Exception:
                    pass
            if adv_registered:
                await safe_call(adv, "unregister", bus, adapter_wrapper)
                await safe_call(adv, "unregister", adapter_wrapper)
                await safe_call(adv, "unregister", bus)
                await safe_call(adv, "unregister")
            await safe_call(geb_service, "unregister")
            await safe_call(sig_service, "unregister")
            await safe_call(dis_service, "unregister")
            try:
                result = bus.disconnect()
                if inspect.isawaitable(result):
                    await result
            except Exception as e:
                print("Error disconnecting bus:", e)
            print("Cleanup complete. Exiting.")


if __name__ == "__main__":
    # Minimal standalone entry point — not mock_service.py (Phase 4), just enough
    # to run this refactored class by hand on the mock VM and confirm Phase 3
    # didn't change behavior versus tools/mock-geberit-alba.py.
    parser = argparse.ArgumentParser(
        description="alba_mock.py — class-based BLE peripheral mock for Geberit AquaClean Alba",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode",
        choices=["unsupported", "handshake", "ble20"],
        default="unsupported",
        help="unsupported: no responses (HACS detection test); "
             "handshake: full Arendi Security server (decryption test); "
             "ble20: Arendi Security + Ble20 application layer (Ble20Client test)",
    )
    parser.add_argument(
        "--send-delay",
        type=int,
        default=0,
        metavar="MS",
        help="milliseconds to sleep between consecutive BLE notifications (default: 0).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8765,
        metavar="PORT",
        help="TCP port for the NOTIFY control web UI (default: 8765). "
             "Only active in --mode ble20. Use --web-port 0 to disable.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Dead flag today (unused even in the original script) — ported for parity.",
    )
    parser.add_argument(
        "--adapter",
        default=None,
        metavar="HCI_NAME",
        help="BlueZ adapter node name to bind to (e.g. 'hci1'). Default: first adapter found.",
    )
    parser.add_argument(
        "--state-dir", metavar="DIR", default=None,
        help="Directory for the shared persistence DB (default: alongside this module)",
    )
    args = parser.parse_args()

    _VERBOSE = args.verbose

    mock = AlbaMock(
        adapter=args.adapter, mode=args.mode,
        send_delay_sec=args.send_delay / 1000.0, web_port=args.web_port,
        state_dir=args.state_dir,
    )
    try:
        asyncio.run(mock.run())
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
