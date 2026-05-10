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
else:
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant

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
        (16,  None,  0,  8, 0,         6,          4, b'AcAlba'),                     # DP_NAME
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
        self._tx_seq  = 0
        self._rx_ack  = 0
        self._tx_cipher: _AesCtrState | None = None
        self._rx_cipher: _AesCtrState | None = None
        self.handshake_done = False

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
            # S-frames: discard (no state needed for this simple server)

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
            if ft == 'U' and ctrl == expected_ctrl:
                return

    async def _await_i(self, expected_type: int, timeout: float = 5.0) -> bytes:
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"[MockServer] timeout waiting for I type=0x{expected_type:02X}")
            ft, ctrl, payload = await asyncio.wait_for(self._rx_queue.get(), timeout=remaining)
            if ft == 'I' and payload and payload[0] == expected_type:
                return payload

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
        print("[MockServer] waiting for SABM...")

        # 1. SABM → UA
        await self._await_u(self._u_ctrl(_HDLC_SABM_TYPE), timeout=60.0)
        print("[MockServer] ← SABM")
        await send_fn(self._att_u(_HDLC_UA_TYPE))
        print("[MockServer] → UA")

        # 2. VERSION_REQ → VERSION_RESP
        await self._await_i(_SEC_VERSION_REQ)
        print("[MockServer] ← VERSION_REQ")
        # 7 bytes: [type=0x01][0x00 × 5][proto_ver_minus_1=0x01] → proto v2
        await send_fn(self._att_i(bytes([_SEC_VERSION_RESP, 0, 0, 0, 0, 0, 1])))
        print("[MockServer] → VERSION_RESP (proto v2)")

        # 3. EP_REQ → EP_RESP
        await self._await_i(_SEC_EP_REQ)
        print("[MockServer] ← EP_REQ")
        nonce1 = os.urandom(16)
        nonce2 = os.urandom(16)
        ep_resp = bytes([_SEC_EP_RESP]) + nonce1 + nonce2 + bytes([0x01])
        await send_fn(self._att_i(ep_resp))
        print(f"[MockServer] → EP_RESP  nonce1={nonce1.hex()}  nonce2={nonce2.hex()}")

        # 4. KE_REQ → verify client CMAC, generate server keypair, KE_RESP
        ke = await self._await_i(_SEC_KE_REQ)
        print("[MockServer] ← KE_REQ")
        if len(ke) < 49:
            raise ValueError(f"[MockServer] KE_REQ too short ({len(ke)} bytes)")
        client_public_bytes = ke[1:33]
        client_cmac_bytes   = ke[33:49]

        auth_key = _hkdf(ikm=aquacleanBridgeId, salt=nonce1, length=16)
        expected_cmac = _aes_cmac(auth_key, client_public_bytes)
        if client_cmac_bytes != expected_cmac:
            raise ValueError("[MockServer] client CMAC verification FAILED — wrong aquacleanBridgeId?")
        print("[MockServer] client CMAC verified ✓")

        server_priv        = X25519PrivateKey.generate()
        server_public_bytes = server_priv.public_key().public_bytes_raw()
        server_cmac        = _aes_cmac(auth_key, server_public_bytes)

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
        while True:
            try:
                ft, ctrl, payload = await asyncio.wait_for(
                    self._rx_queue.get(), timeout=60.0
                )
            except asyncio.TimeoutError:
                print("[MockServer] no more frames after 60 s — exiting frame loop")
                return

            if ft != 'I':
                continue  # S-frame ACK or stray U-frame
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
                    await send_fn(self._att_i(bytes([_SEC_ENCRYPTED]) + encrypted_resp))
                    if send_delay_sec > 0:
                        await asyncio.sleep(send_delay_sec)
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
                await send_fn(self._att_i(bytes([_SEC_ENCRYPTED]) + encrypted_resp))
                print("[MockServer] → fake GetDeviceIdentification response (encrypted)")


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
    async def read_char(self, options):
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

    adapter_wrapper = await Adapter.get_first(bus)
    if adapter_wrapper:
        print("Adapter wrapper obtained from bluez_peripheral.")
    else:
        print("No Bluetooth adapter wrapper found via bluez_peripheral.Adapter.get_first()")

    objmgr = None
    try:
        adapter_path, adapter_address, objmgr = await find_first_adapter_path_and_address(bus)
        print("Adapter DBus path:", adapter_path)
        print("Adapter BLE address:", adapter_address)
    except Exception as e:
        print("Could not read adapter path/address via ObjectManager:", e)
        adapter_path = None
        adapter_address = None

    if not adapter_wrapper:
        print("No adapter wrapper available; cannot register GATT services/advertisement.")
        await bus.disconnect()
        return

    if objmgr is not None:
        def on_device_connected(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                addr = interfaces['org.bluez.Device1'].get('Address')
                if isinstance(addr, Variant):
                    addr = addr.value
                print(f"[Mock] BLE client connected:    {addr or path}")

        def on_device_disconnected(path, interfaces):
            if 'org.bluez.Device1' in interfaces:
                print(f"[Mock] BLE client disconnected: {path}")

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

    adv = Advertisement(
        "Geberit-Alba-Mock",
        [
            "559eb100-2390-11e8-b467-0ed5f89f718b",
            "0000fd48-0000-1000-8000-00805f9b34fb"
        ],
        appearance=0,
        timeout=0
    )

    adv_registered = False
    try:
        await adv.register(bus, adapter_wrapper)
        adv_registered = True
    except Exception as e:
        print("Advertisement registration failed:", e)

    print(f"--- Mock Device Active (mode={mode}) ---")
    print(f"    script: {_SCRIPT_HASH}  bridge: {_BRIDGE_VERSION}")
    print("Advertising as: Geberit-Alba-Mock")

    async def _handshake_loop():
        nonlocal adv, adv_registered
        app_handler = _Ble20AppLayer().dispatch if mode == "ble20" else None
        while True:
            sig_service._arendi = _AriendiServerSide()
            if mode == "ble20":
                app_handler = _Ble20AppLayer().dispatch  # fresh store per session
            try:
                await sig_service._arendi.run(sig_service.send_notify, app_handler=app_handler, send_delay_sec=send_delay_sec)
                print("[MockServer] session complete — waiting for next client (Ctrl-C to quit)")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                msg = str(exc)
                if msg:
                    print(f"[MockServer] ERROR: {msg}")
                else:
                    print("[MockServer] session timed out — waiting for next client (Ctrl-C to quit)")
            # After each session BlueZ stops advertising — create a fresh Advertisement
            # object and register it. Re-using the same instance fails because its DBus
            # interface (/com/spacecheese/bluez_peripheral/advert0) is still exported
            # after unregister() — only a new object gets a clean DBus slot.
            await safe_call(adv, "unregister", bus, adapter_wrapper)
            await safe_call(adv, "unregister", adapter_wrapper)
            await safe_call(adv, "unregister", bus)
            await safe_call(adv, "unregister")
            adv_registered = False
            adv = Advertisement(
                "Geberit-Alba-Mock",
                [
                    "559eb100-2390-11e8-b467-0ed5f89f718b",
                    "0000fd48-0000-1000-8000-00805f9b34fb"
                ],
                appearance=0,
                timeout=0
            )
            try:
                await adv.register(bus, adapter_wrapper)
                adv_registered = True
                print("[Mock] Advertising resumed — ready for next client")
            except Exception as e:
                print(f"[Mock] Advertisement re-registration failed: {e}")

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
    args = parser.parse_args()

    try:
        asyncio.run(main(args.mode, send_delay_sec=args.send_delay / 1000.0))
    except KeyboardInterrupt:
        print("\nInterrupted by user. Exiting.")
