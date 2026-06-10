#!/usr/bin/env python3
"""
Mock BLE peripheral using bluez_peripheral on Linux (BlueZ).

Three modes:
  --mode unsupported  (default)
      Advertises the Alba GATT profile but never responds to any frame.
      Use this to test the unsupported-device detection in the HACS config flow.

  --mode handshake
      Implements the full server-side Arendi Security handshake + one encrypted
      frame exchange.  Use this to test that AriendiSecurity.py (bridge side)
      can complete the handshake against a live BLE peer and exchange encrypted
      Geberit frames.

  --mode ble20
      Like handshake but the mock dispatches real Ble20 application-layer frames
      (Inventory / Read / Write) after the handshake.  Use this to develop and
      test Ble20Client.py against live BLE without real Alba hardware.

Requirements:
  - Linux with BlueZ (Experimental=true may be required)
  - Python packages: dbus-next, bluez_peripheral
  - Sufficient D-Bus privileges (run as root or with appropriate group membership)
  - BlueZ experimental features may be required (`Experimental=true` in
    /etc/bluetooth/main.conf)

Quick start:
  sudo /home/jens/venv/bin/python ./mock-geberit-alba.py --mode handshake
"""

import argparse
import asyncio
import builtins
import hashlib
import inspect
import os
import pathlib
import struct
import sys
from datetime import datetime, timezone

_builtin_print = builtins.print
def print(*args, **kwargs):  # noqa: A001
    now = datetime.now(tz=timezone.utc).astimezone().strftime('%H:%M:%S.%f')[:-3]
    _builtin_print(now, *args, **kwargs)

_SCRIPT_HASH = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:16]
_MOCK_VERSION = "1.2.0"   # bump this on every functional change — user-visible at startup
_VERBOSE = False  # set by --verbose; enables raw ATT hex per-write logging
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
from bluez_peripheral.util import Adapter
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


class _Advertisement(Advertisement):
    """Advertisement subclass that attempts to set a fast advertising interval.

    BlueZ defaults to 1280 ms when MinInterval/MaxInterval are absent from the
    LEAdvertisement1 D-Bus interface.  At 1280 ms the nRF52840 sniffer's device
    list rarely populates because the sniffer hops faster than the ad interval.

    NOTE: The @dbus_property override below does NOT work with the current version
    of dbus_next/bluez_peripheral.  btmon confirms BlueZ receives MinInterval=0x0000,
    MaxInterval=0x0000 via MGMT 0x0054 and falls back to the 1280 ms default.
    The subclass properties are parsed by the dbus_next metaclass but never read by
    BlueZ because bluez_peripheral's ServiceInterface registration path does not
    expose subclass-added properties to the MGMT interface interrogation layer.
    The class is kept so adding a working fix later requires minimal changes.
    """
    @dbus_property(access=PropertyAccess.READ)
    def MinInterval(self) -> 'u':
        return 200

    @dbus_property(access=PropertyAccess.READ)
    def MaxInterval(self) -> 'u':
        return 200


_AGENT_DBUS_PATH = "/mock/geberit/pairingagent"


class _NoIOPairingAgent(ServiceInterface):
    """BlueZ pairing agent with NoInputNoOutput capability (Just Works).

    Without a registered agent, BlueZ responds "Pairing Not Supported" when
    iOS attempts to pair (triggered by bluetoothd reading the iPhone's Battery
    Level characteristic → ATT "Insufficient Authentication" → SMP Security
    Request → iPhone sends Pairing Request → BlueZ has no agent → Pairing
    Failed).  iOS immediately surfaces "cannot connect" to the app.

    Registering this agent with NoInputNoOutput causes BlueZ to accept the iOS
    pairing attempt silently via Just Works, eliminating the SMP failure.
    Root cause confirmed from pcapng frames 5875-5879 (SMP Pairing Failed:
    Pairing Not Supported) captured during Geberit Home App 2.14.1 testing.
    """

    def __init__(self):
        super().__init__("org.bluez.Agent1")

    @dbus_method()
    def Release(self) -> None:
        pass

    @dbus_method()
    def RequestPinCode(self, device: 'o') -> 's':
        return "0000"

    @dbus_method()
    def DisplayPinCode(self, device: 'o', pincode: 's') -> None:
        pass

    @dbus_method()
    def RequestPasskey(self, device: 'o') -> 'u':
        return 0

    @dbus_method()
    def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q') -> None:
        pass

    @dbus_method()
    def RequestConfirmation(self, device: 'o', passkey: 'u') -> None:
        pass  # auto-accept for Just Works

    @dbus_method()
    def RequestAuthorization(self, device: 'o') -> None:
        pass  # auto-accept

    @dbus_method()
    def AuthorizeService(self, device: 'o', uuid: 's') -> None:
        pass  # auto-accept

    @dbus_method()
    def Cancel(self) -> None:
        pass


# --- Import Arendi Security crypto from the bridge package -------------------
# Adds the repo root to sys.path so we can import from aquaclean_console_app.
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
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


# ---------------------------------------------------------------------------
# Inner COBS encode helper (mirrors CobsFraming.Transmit in C# — wraps app data
# before it is passed to the Security layer for encryption)
# ---------------------------------------------------------------------------

def _inner_cobs_encode(data: bytes) -> bytes:
    """Wrap data in inner COBS frame: [0x00] + COBS(data + CRC16_LE) + [0x00]."""
    crc = _crc16_kermit(data)
    return b'\x00' + _cobs_encode(data + bytes([crc & 0xFF, (crc >> 8) & 0xFF])) + b'\x00'


# ---------------------------------------------------------------------------
# Ble20 in-memory device mock (--mode ble20)
# ---------------------------------------------------------------------------

class _Ble20AppLayer:
    """
    Server-side Ble20 application layer.

    Maintains a small in-memory DpId store and dispatches decrypted Ble20
    frames after the Arendi Security handshake completes.

    Handles:
      Inventory   (0x00) → InventoryCount + N × InventoryData
      ReadCmd     (0x10) → ReadAns or ReadError
      WriteCmd    (0x20) → WriteAck or WriteError (+ NotifyData if subscribed)
      NotifyEnable  (0x30) → NotifyAck or NotifyError
      NotifyDisable (0x31) → NotifyAck
    """

    # (dp_id, instance, version, datatype, min_s, max_s, behavior, init_bytes)
    # datatype: 0=Unused 1=Binary 2=MilliSeconds 3=Seconds 8=String 9=Counter
    #           10=Enum 11=OffOn 13=TimeStampUtc
    # behavior: 0=Info  1=Status  2=Command  3=Nvm  4=Protected
    #
    # 78 DpIds mirroring a real kstr AquaClean Alba (AC250) inventory.
    # Values from kstr readall 2026-05-08 with obfuscation applied:
    #   Timestamps / Seconds: real value − 457751 (= 5d 7h 9m 11s)
    #   Counters (serial/device IDs): real value − 57911
    #   Strings: last identifying segment replaced with X/zeros
    _DEFAULT_STORE = [
        # ── System / device identification ────────────────────────────────
        (0,   None,  0,  9, 0,         255,        0, struct.pack('<I', 250)),        # DEVICE_SERIES = 250 (Aquaclean)
        (1,   None,  0,  9, 0,         255,        0, struct.pack('<I', 0)),          # DEVICE_VARIANT = 0 (Alba)
        (2,   None,  0,  9, 0,         9999999,    4, struct.pack('<I', 35225)),      # DEVICE_NUMBER (obf)
        (3,   None,  0, 13, 0,         0,          4, struct.pack('<I', 1757175271)), # DEVICE_PRODUCTION_DATE (obf)
        (4,   None,  0,  8, 0,         12,         4, b'828.860.00.X'),               # DEVICE_SAP_NUMBER (obf)
        (8,   None,  0,  8, 2,         2,          0, b'03'),                         # FW_RS_VERSION → RS03TS89
        (9,   None,  0,  9, 0,         65535,      0, struct.pack('<I', 89)),         # FW_TS_VERSION = 89
        (10,  None,  0,  8, 2,         2,          4, b'00'),                         # HW_RS_VERSION
        (12,  None,  0,  8, 0,         4,          4, b'0000'),                       # PAIRING_SECRET (obf)
        (13,  None,  0,  8, 0,         6,          3, b''),                           # ACCESS_CODE (empty)
        (14,  None,  0,  9, 0,         0,          3, struct.pack('<I', 0)),          # ACCESS_REVOCATION = 0
        (15,  None,  0, 13, 0,         0,          1, struct.pack('<I', 947286443)),  # RTC_TIME (obf; never set — equals 2000-01-01 epoch + OPERATION_TIME_TOTAL)
        (16,  None,  0,  8, 0,         4,          4, b'AcAl'),                      # DP_NAME (truncated: 6-byte "AcAlba" exceeds BLE PDU on CONWISE/CSR adapters)
        (62,  None,  1, 10, 0,         4,          2, b'\x00'),                       # RESET (Command, write-only)
        (83,  None,  1, 10, 0,         1,          2, b'\x00'),                       # START_BOOTLOADER (Command, write-only)
        (93,  None,  1,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # POWER_SUPPLY_ERROR_STATUS = 0
        (148, None,  0,  3, 0,         0,          1, struct.pack('<I', 601643)),     # OPERATION_TIME_TOTAL (obf; = RTC − 2000-01-01 epoch)
        (149, None,  0,  3, 0,         0,          1, struct.pack('<I', 492999)),     # OPERATION_TIME_SINCE_POWER_UP (obf)
        (153, None,  0,  0, 0,         0,          2, b''),                           # RESTART (Command, write-only)
        (236, None,  0,  9, 0,         0,          0, struct.pack('<I', 34761370)),   # UNIQUE_DEVICE_NUMBER (obf)
        (270, None,  0, 13, 946684800, -192608896, 2, struct.pack('<I', 947286443)), # SET_RTC_TIME (Command, write-only)
        (313, None,  0,  8, 0,         20,         4, b'245.832.00.X'),               # SALES_SAP_NUMBER (obf)
        (337, None,  0,  9, 0,         255,        0, struct.pack('<I', 0)),          # BOOTLOADER_VARIANT = 0
        (369, None,  0,  8, 0,         20,         4, b'SB0000EU000000'),             # SALES_PRODUCT_SERIAL_NUMBER (obf)
        (370, None,  0, 13, 0,         0,          4, struct.pack('<I', 1774187093)), # SALES_PRODUCT_PRODUCTION_DATE (obf)
        (371, None,  0,  8, 0,         12,         4, b'146.350.01.x'),               # SALES_PRODUCT_SAP_NUMBER (confirms Alba 250 toilet)
        (431, None,  0,  3, 0,         0,          4, struct.pack('<I', 0)),          # OPERATION_TIME_OFFSET = 0
        # ── Anal shower ───────────────────────────────────────────────────
        (563, None,  0, 10, 0,         1,          2, b'\x00'),                       # START_STOP_ANAL_SHOWER (Command, write-only)
        (564, None,  0, 10, 0,         7,          1, b'\x01'),                       # ANAL_SHOWER_STATUS = 1 (Disabled at rest)
        (566, None,  0, 10, 0,         1,          2, b'\x00'),                       # START_STOP_SPRAY_ARM_CLEANING (Command, write-only)
        (567, None,  0, 10, 0,         5,          1, b'\x02'),                       # SPRAY_ARM_CLEANING_STATUS = 2 (Ready)
        (569, None,  0, 10, 0,         0,          2, b'\x00'),                       # LOAD_PROFILE (Command, write-only)
        (570, None,  0, 10, 0,         4,          2, b'\x00'),                       # SET_ACTIVE_ANAL_SPRAY_INTENSITY (Command, write-only)
        (571, None,  0, 10, 0,         4,          1, b'\x04'),                       # ACTIVE_ANAL_SPRAY_INTENSITY_STATUS = 4 (Level 5)
        (572, None,  0, 10, 0,         4,          2, b'\x00'),                       # SET_ACTIVE_ANAL_SPRAY_ARM_POSITION (Command, write-only)
        (573, None,  0, 10, 0,         4,          1, b'\x04'),                       # ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS = 4 (Position 5)
        (574, None,  0, 10, 0,         5,          2, b'\x00'),                       # SET_ACTIVE_SHOWER_WATER_TEMPERATURE (Command, write-only)
        (575, None,  0, 10, 0,         5,          1, b'\x05'),                       # ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS = 5 (Level 5)
        (576, None,  0, 11, 0,         1,          2, b'\x00'),                       # SET_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION (Command, write-only)
        (577, None,  0, 11, 0,         1,          1, b'\x00'),                       # ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS = Off
        (580, None,  0, 10, 0,         4,          3, b'\x04'),                       # STORED_ANAL_SPRAY_INTENSITY = 4 (Level 5)
        (581, None,  0, 10, 0,         4,          3, b'\x04'),                       # STORED_ANAL_SPRAY_ARM_POSITION = 4 (Position 5)
        (582, None,  0, 10, 0,         5,          3, b'\x05'),                       # STORED_SHOWER_WATER_TEMPERATURE = 5 (Level 5)
        (583, None,  0, 11, 0,         1,          3, b'\x00'),                       # STORED_ANAL_SPRAY_ARM_OSCILLATION = Off
        (584, None,  0, 10, 0,         1,          2, b'\x00'),                       # START_STOP_DESCALING (Command, write-only)
        (585, None,  0, 10, 0,         4,          1, b'\x02'),                       # DESCALING_STATUS = 2 (Ready)
        (588, None,  0,  9, 0,         0,          3, struct.pack('<I', 1)),          # UNACCOUNTED_SHOWER_CYCLES = 1
        (589, None,  0,  9, 0,         0,          1, struct.pack('<I', 168)),        # DAYS_UNTIL_NEXT_DESCALING = 168
        (590, None,  0, 13, 0,         0,          3, struct.pack('<I', 0)),          # TIMESTAMP_OF_LAST_DESCALING = 0 (never descaled)
        (591, None,  0, 13, 0,         0,          3, struct.pack('<I', 0)),          # TIMESTAMP_OF_LAST_DESCALING_REQUEST = 0 (never)
        (592, None,  0,  9, 0,         0,          3, struct.pack('<I', 0)),          # DESCALING_CYCLES = 0
        (607, None,  0, 10, 0,         1,          1, b'\x00'),                       # USER_DETECTION_STATUS = 0 (User absent)
        (711, None,  0,  9, 0,         0,          1, struct.pack('<I', 340)),        # STATISTIC_COUNTER_SINCE_POWER_UP_SUM = 340
        (764, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # WATER_HEATER_ERROR_STATUS = 0
        (765, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # LEVEL_CONTROL_ERROR_STATUS = 0
        (766, None,  0,  1, 4,         4,          1, b'\x00\x00\x00\x00'),          # USER_DETECTION_ERROR_STATUS = 0
        (781, None,  0,  9, 0,         0,          3, struct.pack('<I', 33600)),      # CREDITS_UNTIL_NEXT_DESCALING = 33600 (= 168 days × 200)
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
        # ── Instanced DpIds — inventory descriptors (instance = n_instances) ────
        (785,  3,    0,  9, 0,       255,  0, struct.pack('<I', 3)),          # FUS_VERSION: 3 instances (0–2)
        (786,  2,    0,  9, 0,       255,  0, struct.pack('<I', 2)),          # GEBERIT_LOADER_VERSION: 2 instances (0–1)
        (787,  3,    0,  9, 0,       255,  0, struct.pack('<I', 3)),          # WIRELESS_STACK_VERSION: 3 instances (0–2)
        (565,  4,    0,  2, 0,         0,  1, b'\x00\x00\x00\x00'),          # ANAL_SHOWER_PROGRESS: 4 instances (0–3)
        (568,  4,    0,  2, 0,         0,  1, b'\x00\x00\x00\x00'),          # SPRAY_ARM_CLEANING_PROGRESS: 4 instances
        (586,  4,    0,  2, 0,         0,  1, b'\x00\x00\x00\x00'),          # DESCALING_PROGRESS: 4 instances
        # ── Version DpId per-instance values (FUS 1.2.0, LOADER 5.6, WIRELESS 1.14.1) ─
        (785,  0,    0,  9, 0,       255,  1, struct.pack('<I', 1)),          # FUS_VERSION Major = 1
        (785,  1,    0,  9, 0,       255,  1, struct.pack('<I', 2)),          # FUS_VERSION Minor = 2
        (785,  2,    0,  9, 0,       255,  1, struct.pack('<I', 0)),          # FUS_VERSION Bugfix = 0
        (786,  0,    0,  9, 0,       255,  1, struct.pack('<I', 5)),          # GEBERIT_LOADER_VERSION Major = 5
        (786,  1,    0,  9, 0,       255,  1, struct.pack('<I', 6)),          # GEBERIT_LOADER_VERSION Minor = 6
        (787,  0,    0,  9, 0,       255,  1, struct.pack('<I', 1)),          # WIRELESS_STACK_VERSION Major = 1
        (787,  1,    0,  9, 0,       255,  1, struct.pack('<I', 14)),         # WIRELESS_STACK_VERSION Minor = 14
        (787,  2,    0,  9, 0,       255,  1, struct.pack('<I', 1)),          # WIRELESS_STACK_VERSION Bugfix = 1
        # ── Progress DpId per-instance values (all zero at rest) ──────────────
        (565,  0,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # ANAL_SHOWER_PROGRESS MaxTotal
        (565,  1,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # ANAL_SHOWER_PROGRESS ElapsedTotal
        (565,  2,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # ANAL_SHOWER_PROGRESS MaxStep
        (565,  3,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # ANAL_SHOWER_PROGRESS ElapsedStep
        (568,  0,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # SPRAY_ARM_CLEANING_PROGRESS MaxTotal
        (568,  1,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # SPRAY_ARM_CLEANING_PROGRESS ElapsedTotal
        (568,  2,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # SPRAY_ARM_CLEANING_PROGRESS MaxStep
        (568,  3,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # SPRAY_ARM_CLEANING_PROGRESS ElapsedStep
        (586,  0,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # DESCALING_PROGRESS MaxTotal
        (586,  1,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # DESCALING_PROGRESS ElapsedTotal
        (586,  2,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # DESCALING_PROGRESS MaxStep
        (586,  3,    0,  3, 0,         0,  1, struct.pack('<I', 0)),          # DESCALING_PROGRESS ElapsedStep
        # ── Statistics counter per-instance values (instances 2, 31–36) ───────
        # STATISTIC_COUNTER_SINCE_POWER_UP (405)
        (405,  2,    1,  9, 0, 999999999,  1, struct.pack('<I', 12)),         # UseWithFlush = 12
        (405, 31,    1,  9, 0, 999999999,  1, struct.pack('<I', 5)),          # AquacleanUsages = 5
        (405, 32,    1,  9, 0, 999999999,  1, struct.pack('<I', 3)),          # AnalShowers = 3
        (405, 33,    1,  9, 0, 999999999,  1, struct.pack('<I', 0)),          # LadyShowers = 0
        (405, 34,    1,  9, 0, 999999999,  1, struct.pack('<I', 2)),          # Dryings = 2
        (405, 35,    1,  9, 0, 999999999,  1, struct.pack('<I', 0)),          # Descalings = 0
        (405, 36,    1,  9, 0, 999999999,  1, struct.pack('<I', 1)),          # SprayArmCleanings = 1
        # STATISTIC_COUNTER_SINCE_RESET (688)
        (688,  2,    1,  9, 0, 999999999,  1, struct.pack('<I', 120)),        # UseWithFlush = 120
        (688, 31,    1,  9, 0, 999999999,  1, struct.pack('<I', 47)),         # AquacleanUsages = 47
        (688, 32,    1,  9, 0, 999999999,  1, struct.pack('<I', 28)),         # AnalShowers = 28
        (688, 33,    1,  9, 0, 999999999,  1, struct.pack('<I', 5)),          # LadyShowers = 5
        (688, 34,    1,  9, 0, 999999999,  1, struct.pack('<I', 18)),         # Dryings = 18
        (688, 35,    1,  9, 0, 999999999,  1, struct.pack('<I', 3)),          # Descalings = 3
        (688, 36,    1,  9, 0, 999999999,  1, struct.pack('<I', 9)),          # SprayArmCleanings = 9
        # STATISTIC_COUNTER_TOTAL (689)
        (689,  2,    1,  9, 0, 999999999,  1, struct.pack('<I', 843)),        # UseWithFlush = 843
        (689, 31,    1,  9, 0, 999999999,  1, struct.pack('<I', 312)),        # AquacleanUsages = 312
        (689, 32,    1,  9, 0, 999999999,  1, struct.pack('<I', 187)),        # AnalShowers = 187
        (689, 33,    1,  9, 0, 999999999,  1, struct.pack('<I', 35)),         # LadyShowers = 35
        (689, 34,    1,  9, 0, 999999999,  1, struct.pack('<I', 112)),        # Dryings = 112
        (689, 35,    1,  9, 0, 999999999,  1, struct.pack('<I', 18)),         # Descalings = 18
        (689, 36,    1,  9, 0, 999999999,  1, struct.pack('<I', 64)),         # SprayArmCleanings = 64
    ]

    def __init__(self):
        self._store: dict = {}
        self._notify_subscribed: set = set()
        for dp_id, inst, ver, dt, mn, mx, beh, val in self._DEFAULT_STORE:
            self._store[(dp_id, inst)] = {
                'version': ver, 'datatype': dt,
                'min_s': mn,    'max_s': mx,
                'behavior': beh,
                'value': bytearray(val),
            }

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
        print(f"[MockBle20] ← WRITE DpId={dp_id} value={value.hex()} → ACK")
        responses = [bytes([CommandId.WriteAck]) + addr]
        if dp_id in self._notify_subscribed:
            responses.append(bytes([CommandId.NotifyData]) + encode_address(dp_id) + value)
            print(f"[MockBle20] → NOTIFY_DATA DpId={dp_id} value={value.hex()}")
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
                self._rx_ack = (peer_ns + 1) % 8
                self._rx_queue.put_nowait(('I', ctrl, payload))
            elif (ctrl & 0x03) == 0x03:   # U-frame
                self._rx_queue.put_nowait(('U', ctrl, payload))
            elif (ctrl & 0x03) == 0x01:  # S-frame (RR/RNR) — signal flow control
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

    async def run(self, send_fn, app_handler=None, send_delay_sec: float = 0.0) -> None:
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
        # need_sabm=True  → wait for SABM before sending UA (first session).
        # need_sabm=False → SABM already received in frame loop; go straight to UA.
        need_sabm = True
        while True:
            if need_sabm:
                print("[MockServer] waiting for SABM...")
                try:
                    await self._await_u(self._u_ctrl(_HDLC_SABM_TYPE), timeout=60.0)
                except asyncio.TimeoutError:
                    print("[MockServer] session timed out — no client connected within 60 s")
                    print("[Mock] If the bridge/HACS was running, the ESP32 BLE scanner may be stuck.")
                    print("[Mock]   → Restart it via ESPHome web UI (Restart button) — no other action needed.")
                    return False
                print("[MockServer] ← SABM")

            # 1. UA
            await send_fn(self._att_u(_HDLC_UA_TYPE))
            print("[MockServer] → UA")

            # 2. VERSION_REQ → VERSION_RESP
            try:
                await self._await_i(_SEC_VERSION_REQ)
            except asyncio.TimeoutError:
                print("[MockServer] handshake timeout waiting for VERSION_REQ")
                return
            print("[MockServer] ← VERSION_REQ")
            await send_fn(self._att_i(bytes([_SEC_VERSION_RESP, 0, 0, 0, 0, 0, 1])))
            print("[MockServer] → VERSION_RESP (proto v2)")

            # 3. EP_REQ → EP_RESP
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

            # 4. KE_REQ → verify client CMAC, generate server keypair, KE_RESP
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
            #                         after KE, observed 450–750 ms on iPhone.
            #   active session (False) — 30 s: inter-command gap once session is running.
            #                           During init, gaps are ~2.96 s (CapabilitiesCmd after
            #                           inventory; ReadCmd after DpId=8 ReadAns).
            #                           After Initialize() returns, the iOS app may take
            #                           10–20 s before sending NotifyEnable frames (async
            #                           UI rendering, possible cloud-call timeout).
            _first_frame = True
            while True:
                _timeout = 5.0 if _first_frame else 30.0
                print(f"[MockServer] ⏳ waiting for next frame (timeout={_timeout:.0f}s, tx_seq={self._tx_seq})")
                try:
                    ft, ctrl, payload = await asyncio.wait_for(
                        self._rx_queue.get(), timeout=_timeout
                    )
                except asyncio.TimeoutError:
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
        return b"Geberit-Mock"


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
        """Called by main() after register() to wire up the notification sender."""
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

    @characteristic("559eb001-2390-11e8-b467-0ed5f89f718b", CharFlags.WRITE)
    def sig_write(self, options):
        pass

    @sig_write.setter
    def sig_write(self, value, options):
        data = bytes(value)
        head = data[:4].hex() if len(data) >= 4 else data.hex()
        print(f"[BLE←] {len(data)} B  head={head}{'...' if len(data) > 4 else ''}")
        if _VERBOSE:
            print(f"[Write→sig_write] {data.hex()}")
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


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def find_first_adapter_path_and_address(bus):
    """Use ObjectManager to find the first Adapter1 path and Address."""
    introspection = await bus.introspect('org.bluez', '/')
    proxy = bus.get_proxy_object('org.bluez', '/', introspection)
    objmgr = proxy.get_interface('org.freedesktop.DBus.ObjectManager')
    managed = await objmgr.call_get_managed_objects()
    for path, ifaces in managed.items():
        if 'org.bluez.Adapter1' in ifaces:
            adapter_props = ifaces['org.bluez.Adapter1']
            addr = adapter_props.get('Address')
            if isinstance(addr, Variant):
                addr = addr.value
            return path, addr, objmgr
    return None, None, objmgr


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

async def main(mode: str, send_delay_sec: float = 0.0):
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

    # Register a NoInputNoOutput BLE pairing agent so iOS "Just Works" pairing
    # succeeds silently instead of BlueZ replying "Pairing Not Supported".
    # Without this, bluetoothd reads the iPhone's Battery Level characteristic
    # → ATT "Insufficient Authentication" → SMP Security Request → iPhone pairs
    # → "Pairing Not Supported" → iOS surfaces "cannot connect" to the app.
    _agent = _NoIOPairingAgent()
    bus.export_object(_AGENT_DBUS_PATH, _agent)
    _agent_mgr = None
    try:
        _intr = await bus.introspect('org.bluez', '/org/bluez')
        _proxy = bus.get_proxy_object('org.bluez', '/org/bluez', _intr)
        _agent_mgr = _proxy.get_interface('org.bluez.AgentManager1')
        await _agent_mgr.call_register_agent(_AGENT_DBUS_PATH, 'NoInputNoOutput')
        await _agent_mgr.call_request_default_agent(_AGENT_DBUS_PATH)
        print("Pairing agent registered (NoInputNoOutput — Just Works auto-accept)")
    except Exception as _e:
        print(f"WARNING: pairing agent registration failed: {_e}")
        print("         iOS SMP pairing may fail with 'Pairing Not Supported'")
        _agent_mgr = None

    adapter_wrapper = await Adapter.get_first(bus)
    if adapter_wrapper:
        print("Adapter wrapper obtained from bluez_peripheral.")
    else:
        print("No Bluetooth adapter wrapper found via bluez_peripheral.Adapter.get_first()")

    objmgr = None
    try:
        adapter_path, adapter_address, objmgr = await find_first_adapter_path_and_address(bus)
        print("Adapter DBus path:", adapter_path)
        print("Adapter BLE address:", adapter_address,
              "(controller identity — BlueZ may advertise with a random/rotating address on-air; run 'sudo btmon' to see the actual transmitted address)")
    except Exception as e:
        print("Could not read adapter path/address via ObjectManager:", e)
        adapter_path = None
        adapter_address = None

    if not adapter_wrapper:
        print("No adapter wrapper available; cannot register GATT services/advertisement.")
        await bus.disconnect()
        return

    # Track the BlueZ object path and address of the currently connected BLE client.
    # Used to force-disconnect when a session ends by timeout (not by peer).
    _connected_device_path = None
    _connected_device_addr = None

    if objmgr is not None:
        def on_device_connected(path, interfaces):
            nonlocal _connected_device_path, _connected_device_addr
            if 'org.bluez.Device1' in interfaces:
                addr = interfaces['org.bluez.Device1'].get('Address')
                if isinstance(addr, Variant):
                    addr = addr.value
                _connected_device_addr = addr
                _connected_device_path = path
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
    sig_service = BtSigDataService(mode=mode)
    dis_service = DeviceInformationService()

    try:
        await geb_service.register(bus, "/org/bluez/example/geberit", adapter_wrapper)
        await sig_service.register(bus, "/org/bluez/example/sigdata", adapter_wrapper)
        await dis_service.register(bus, "/org/bluez/example/dis", adapter_wrapper)
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
        manufacturerData={0x0602: bytes([0x02, 0xFA] + [0x00] * 13)},
    )

    adv_registered = False
    try:
        await adv.register(bus, adapter_wrapper)
        adv_registered = True
    except Exception as e:
        print("Advertisement registration failed:", e)

    print(f"--- Mock Device Active (mode={mode}) ---")
    print(f"    mock: {_MOCK_VERSION}  script: {_SCRIPT_HASH}  bridge: {_BRIDGE_VERSION}")
    print("Advertising: fd48 + Geberit mfr data (company 0x0602)")

    async def _handshake_loop():
        nonlocal _connected_device_path
        app_handler = _Ble20AppLayer().dispatch if mode == "ble20" else None
        _user_sitting = False
        _session_num = 0
        while True:
            _session_num += 1
            print(f"\n[Mock] ===== SESSION {_session_num} — waiting for client =====")
            sig_service._arendi = _AriendiServerSide()
            if mode == "ble20":
                _ble20_app = _Ble20AppLayer()  # fresh store per session
                if _user_sitting:
                    _ble20_app._store[(607, None)]['value'] = bytearray(b'\x01')
                app_handler = _ble20_app.dispatch
            # Reset notify subscription state so the next BLE client can subscribe.
            # bluez_peripheral sets _notifying=True in StartNotify() and never resets
            # it on an abrupt BLE disconnect (no StopNotify() is called).  On the
            # second connection, StartNotify() raises "Already notifying" → BlueZ
            # returns ATT error 0x01 to the ESP32 → "Invalid handle" → poll failure.
            if notify_char is not None and hasattr(notify_char, '_notifying'):
                notify_char._notifying = False
            _completed = None
            _session_completed = False
            try:
                _completed = await sig_service._arendi.run(sig_service.send_notify, app_handler=app_handler, send_delay_sec=send_delay_sec)
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
            if _session_completed and _completed is not False:
                _user_sitting = not _user_sitting
                print(f"[Mock] Next session USER_DETECTION_STATUS → {'1 (sitting)' if _user_sitting else '0 (absent)'}")

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
            arendi_just_ran = sig_service._arendi
            if _connected_device_path is not None:
                _path_to_disconnect = _connected_device_path
                _connected_device_path = None  # clear before attempt — not needed after
                by_peer = getattr(arendi_just_ran, 'disconnected_by_peer', False)
                print(f"[Mock] Forcing BlueZ disconnect to resume advertising{' (already disconnected by peer — call may fail)' if by_peer else ''}: {_path_to_disconnect}")
                try:
                    introspect = await bus.introspect('org.bluez', _path_to_disconnect)
                    proxy = bus.get_proxy_object('org.bluez', _path_to_disconnect, introspect)
                    dev_iface = proxy.get_interface('org.bluez.Device1')
                    await dev_iface.call_disconnect()
                    print("[Mock] Force-disconnect sent; waiting for BlueZ to confirm...")
                    await asyncio.sleep(0.5)
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
            await asyncio.sleep(0.3)
            print("[Mock] Ready for next client")

    server_task = None
    if mode in ("handshake", "ble20"):
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
    parser = argparse.ArgumentParser(
        description="Mock Geberit AquaClean Alba BLE peripheral (Linux/BlueZ).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Modes
-----
  --mode unsupported  (default)
      Advertises the Alba GATT profile but never responds to any write.
      Use this to test the HACS unsupported-device detection screen
      (HA shows the device UUID details instead of a generic error).

  --mode handshake
      Implements the full server-side Arendi Security handshake:
        SABM / UA / VERSION_REQ-RESP / EP_REQ-RESP / KE_REQ-RESP
      then loops on incoming encrypted frames and sends back a fake
      GetDeviceIdentification response.

      Use this to verify that AriendiSecurity.py (bridge side) can:
        1. Complete the handshake against a live BLE peer
        2. Encrypt outgoing Geberit frames correctly
        3. Decrypt incoming encrypted frames correctly

  --mode ble20
      Like --mode handshake but after the handshake the mock dispatches real
      Ble20 application-layer frames:
        Inventory (0x00) → InventoryCount + N × InventoryData
        Read      (0x10) → ReadAns or ReadError
        Write     (0x20) → WriteAck or WriteError

      In-memory DpId store (fresh per session):
        DpId  60  USER_PRESENT        (OffOn,   init=0x00)
        DpId 563  START_STOP_SHOWER   (OffOn,   init=0x00)
        DpId 564  ANAL_SHOWER_STATUS  (Enum,    init=0x00)
        DpId 1008 LID_LIFTER_POSITION (Signed,  init=0x00000000)
        DpId 1009 TRIGGER_LID_LIFTING (OffOn,   init=0x00)

      Use this to test Ble20Client.py (bridge side) end-to-end without
      real Alba hardware.

      Expected output on inventory:
        [MockServer] ← encrypted frame DECRYPTED: 0000
        [MockBle20] → INVENTORY_DATA DpId=60
        [MockBle20] → INVENTORY_DATA DpId=563
        ...
        [MockServer] ← encrypted frame DECRYPTED: 10<addr>
        [MockBle20] ← READ DpId=60 → 00

Unsupported-device detection test (--mode unsupported)
------------------------------------------------------
Prerequisites (free the ESP32 BLE subscription slot):
  1. In HA: Settings → Integrations → aquaclean-proxy → Disable
  2. In HA: Geberit AquaClean → Configure → set Poll Interval to 300 s

Test sequence:
  3. sudo python mock-geberit-alba.py
     Wait for '--- Mock Device Active ---'
  4. HA: Settings → Integrations → 'Add entry' → Geberit AquaClean
     → MAC: <adapter address from step 3>
     → ESPHome host: <ESP32 IP>
     Expected: unsupported-device abort screen with GATT UUIDs
""",
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
        help="milliseconds to sleep between consecutive BLE notifications (default: 0). "
             "Use --send-delay 20 when testing over an ESPHome BLE proxy to prevent "
             "ATT notification drops caused by BLE link congestion.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        default=False,
        help="Print raw ATT hex for every BLE write (default: suppressed). "
             "Useful for HDLC parsing debugging; normally too noisy.",
    )
    args = parser.parse_args()

    _VERBOSE = args.verbose

    try:
        asyncio.run(main(args.mode, send_delay_sec=args.send_delay / 1000.0))
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
