"""Geberit Alba Ble20 application-layer client.

Runs inside the Arendi Security encrypted channel delivered by
BluetoothLeConnector.  Call inventory() once after the handshake
completes, then use read() / write() / enable_notification() for
data exchange.

Usage:
    client = Ble20Client(connector)
    await connector.connect_async(device_address)
    inv = await client.inventory()
    raw = await client.read(DpId.DP_ANAL_SHOWER_STATUS)
"""

import asyncio
import logging
import struct
from dataclasses import dataclass
from typing import Optional

from .command_id import CommandId
from .dp_type import DpType
from .transmission_status import TransmissionStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Device identification result
# ---------------------------------------------------------------------------

@dataclass
class Ble20DeviceIdentification:
    """Device identification data read from Ble20 DpIds.

    Field-to-DpId mapping mirrors DeviceIdentification in the Geberit vendor
    application source.  Fields are None when the device did not return data
    for that DpId.
    """
    name:                Optional[str] = None   # DpId 16  DP_NAME
    device_series:       Optional[int] = None   # DpId 0   DP_DEVICE_SERIES
    device_variant:      Optional[int] = None   # DpId 1   DP_DEVICE_VARIANT
    device_boot_variant: Optional[int] = None   # DpId 337 DP_BOOTLOADER_VARIANT
    device_model:        Optional[int] = None   # DpId 304 DP_DEVICE_MODEL
    device_number:       Optional[int] = None   # DpId 2   DP_DEVICE_NUMBER
    device_unique_id:    Optional[int] = None   # DpId 236 DP_UNIQUE_DEVICE_NUMBER
    fw_rs_version:       Optional[str] = None   # DpId 8   DP_FW_RS_VERSION
    fw_ts_version:       Optional[int] = None   # DpId 9   DP_FW_TS_VERSION
    device_production_date:     Optional[int] = None   # DpId 3   DP_DEVICE_PRODUCTION_DATE (TimeStampUtc)
    device_sap_number:          Optional[str] = None   # DpId 4   DP_DEVICE_SAP_NUMBER
    sales_product_sap_number:   Optional[str] = None   # DpId 371 DP_SALES_PRODUCT_SAP_NUMBER
    sales_product_serial_number: Optional[str] = None  # DpId 369 DP_SALES_PRODUCT_SERIAL_NUMBER


# ---------------------------------------------------------------------------
# Wire-address helpers (used by client, mock, and probe)
# ---------------------------------------------------------------------------

def encode_address(dp_id: int, instance: Optional[int] = None) -> bytes:
    """Encode a DpId (and optional instance) as a Ble20 wire address."""
    lo = dp_id & 0xFF
    hi = (dp_id >> 8) & 0x7F
    if instance is not None:
        return bytes([lo, hi | 0x80, instance])
    return bytes([lo, hi])


def decode_address(data: bytes, offset: int = 1) -> tuple[int, Optional[int], int]:
    """Decode a Ble20 wire address at *offset*.

    Returns (dp_id, instance_or_None, next_offset).
    Default offset=1 skips the command byte that precedes the address in
    every Ble20 frame.
    """
    lo = data[offset]
    hi = data[offset + 1]
    has_instance = bool(hi & 0x80)
    dp_id = ((hi & 0x7F) << 8) | lo
    if has_instance:
        return dp_id, data[offset + 2], offset + 3
    return dp_id, None, offset + 2


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class Ble20Client:
    """
    Ble20 application-layer client for Geberit Alba devices.

    Registers on connector.data_received_handlers to receive decrypted
    plaintext frames delivered by AriendiSecurity.  Uses
    connector.send_message() to send (the connector encrypts transparently).

    Handles:
      inventory()            — DataPointInventory → full DpId map
      read()                 — ReadCmd / ReadAns
      write()                — WriteCmd / WriteAck
      enable_notification()  — NotifyEnable / NotifyAck
      get_notification()     — await next NotifyData for a subscribed DpId
      poll_state()           — read the standard bridge state DpIds
    """

    RECV_TIMEOUT = 30.0

    def __init__(self, connector):
        """
        connector: BluetoothLeConnector with a completed Arendi handshake.
        data_received_handlers fires with decrypted plaintext frames.
        """
        self._connector = connector
        self._rx_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._notify_queues: dict[int, asyncio.Queue] = {}
        connector.data_received_handlers += self._on_data

    # ── Internal plumbing ────────────────────────────────────────────────────

    async def _on_data(self, data: bytes) -> None:
        if not data:
            return
        cmd = data[0]
        if cmd == CommandId.NotifyData and len(data) >= 3:
            dp_id, _, _ = decode_address(data, 1)
            q = self._notify_queues.get(dp_id)
            if q is not None:
                q.put_nowait(data)
                return
        self._rx_queue.put_nowait(data)

    async def _recv(self, timeout: float = RECV_TIMEOUT) -> bytes:
        return await asyncio.wait_for(self._rx_queue.get(), timeout=timeout)

    async def _send(self, payload: bytes) -> None:
        logger.debug(f"Ble20 → {payload.hex()}")
        await self._connector.send_message(payload)

    # ── Inventory ────────────────────────────────────────────────────────────

    async def inventory(self) -> dict[int, dict]:
        """Run DataPointInventory.  Returns {dp_id: entry_dict}.

        entry_dict keys:
          instance, version, datatype, min_s, max_s, min_u, max_u,
          is_internal, behavior
        """
        await self._send(bytes([CommandId.Inventory, 0x00]))

        while True:
            frame = await self._recv()
            logger.debug(f"Ble20 ← {frame.hex()}")
            if frame[0] == CommandId.InventoryCount:
                break
            logger.debug(f"Ble20: skipping pre-inventory frame cmd=0x{frame[0]:02X}")

        count = struct.unpack_from('<H', frame, 1)[0]
        logger.debug(f"Ble20: inventory count={count}")

        result: dict[int, dict] = {}
        received = 0
        while received < count:
            frame = await self._recv()
            logger.debug(f"Ble20 ← {frame.hex()}")
            if frame[0] != CommandId.InventoryData:
                logger.debug(f"Ble20: skipping non-inventory frame cmd=0x{frame[0]:02X}")
                continue
            dp_id, instance, payload_off = decode_address(frame, 1)
            payload = frame[payload_off:]
            if len(payload) < 11:
                logger.warning(f"Ble20: short InventoryData for DpId={dp_id}: {payload.hex()}")
                received += 1
                continue
            flags = payload[10]
            result[dp_id] = {
                'instance':    instance,
                'version':     payload[0],
                'datatype':    payload[1],
                'min_s':       struct.unpack_from('<i', payload, 2)[0],
                'max_s':       struct.unpack_from('<i', payload, 6)[0],
                'min_u':       struct.unpack_from('<I', payload, 2)[0],
                'max_u':       struct.unpack_from('<I', payload, 6)[0],
                'is_internal': bool(flags & 0x80),
                'behavior':    flags & 0x7F,
            }
            received += 1

        logger.info(f"Ble20: inventory complete — {len(result)} DpIds")
        return result

    # ── Read ─────────────────────────────────────────────────────────────────

    async def read(self, dp_id: int, instance: Optional[int] = None) -> bytes:
        """Read one DpId.  Returns raw value bytes.  Raises IOError on device error."""
        addr = encode_address(dp_id, instance)
        await self._send(bytes([CommandId.ReadCmd]) + addr)
        while True:
            frame = await self._recv()
            logger.debug(f"Ble20 ← {frame.hex()}")
            if frame[0] in (CommandId.ReadAns, CommandId.ReadError):
                break
            logger.debug(f"Ble20: skipping frame cmd=0x{frame[0]:02X} (awaiting ReadAns)")
        if frame[0] == CommandId.ReadError:
            _, _, off = decode_address(frame, 1)
            status = frame[off] if off < len(frame) else 0xFF
            raise IOError(f"ReadError dp_id={dp_id}: {_tx_name(status)}")
        _, _, off = decode_address(frame, 1)
        return frame[off:]

    # ── Write ─────────────────────────────────────────────────────────────────

    async def write(self, dp_id: int, value: bytes, instance: Optional[int] = None) -> None:
        """Write one DpId.  Raises IOError on device error."""
        addr = encode_address(dp_id, instance)
        await self._send(bytes([CommandId.WriteCmd]) + addr + value)
        while True:
            frame = await self._recv()
            logger.debug(f"Ble20 ← {frame.hex()}")
            if frame[0] in (CommandId.WriteAck, CommandId.WriteError):
                break
            logger.debug(f"Ble20: skipping frame cmd=0x{frame[0]:02X} (awaiting WriteAck)")
        if frame[0] == CommandId.WriteError:
            _, _, off = decode_address(frame, 1)
            status = frame[off] if off < len(frame) else 0xFF
            raise IOError(f"WriteError dp_id={dp_id}: {_tx_name(status)}")

    # ── Notifications ────────────────────────────────────────────────────────

    async def enable_notification(self, dp_ids: list[int]) -> None:
        """Subscribe to unsolicited value updates for the given DpIds."""
        for dp_id in dp_ids:
            addr = encode_address(dp_id)
            await self._send(bytes([CommandId.NotifyEnable]) + addr)
            frame = await self._recv()
            logger.debug(f"Ble20 ← {frame.hex()}")
            if frame[0] == CommandId.NotifyAck:
                if dp_id not in self._notify_queues:
                    self._notify_queues[dp_id] = asyncio.Queue()
                logger.debug(f"Ble20: notification enabled for DpId={dp_id}")
            else:
                _, _, off = decode_address(frame, 1)
                status = frame[off] if off < len(frame) else 0xFF
                logger.warning(f"Ble20: NotifyEnable DpId={dp_id} failed: {_tx_name(status)}")

    async def get_notification(self, dp_id: int, timeout: float = RECV_TIMEOUT) -> bytes:
        """Wait for a NotifyData on a subscribed DpId.  Returns raw value bytes."""
        q = self._notify_queues.get(dp_id)
        if q is None:
            raise ValueError(f"DpId={dp_id} not subscribed — call enable_notification first")
        frame = await asyncio.wait_for(q.get(), timeout=timeout)
        _, _, off = decode_address(frame, 1)
        return frame[off:]

    # ── Bridge state polling ──────────────────────────────────────────────────

    async def poll_state(self) -> dict[int, bytes]:
        """Read the standard bridge state DpIds.  Returns {dp_id: raw_bytes}.

        DpIds polled (all optional — silently skipped if device returns an error
        or the DpId is absent from this device's inventory):
          DP_USER_DETECTION_STATUS  (607) — user present/sitting
          DP_ANAL_SHOWER_STATUS     (564) — anal shower running state
        """
        from .dp_ids import DpId
        _POLL_IDS = [
            DpId.DP_USER_DETECTION_STATUS,
            DpId.DP_ANAL_SHOWER_STATUS,
        ]
        result: dict[int, bytes] = {}
        for dp_id in _POLL_IDS:
            try:
                result[int(dp_id)] = await self.read(int(dp_id))
            except Exception as e:
                logger.debug(f"Ble20: poll_state DpId={dp_id} unavailable: {e}")
        return result

    # ── Device identification ─────────────────────────────────────────────────

    async def get_device_identification(
        self,
        inv: Optional[dict[int, dict]] = None,
    ) -> Ble20DeviceIdentification:
        """Read device identification DpIds and return a Ble20DeviceIdentification.

        inv: inventory dict from inventory() — used for correct datatype decoding.
             When None, raw bytes are decoded by sensible defaults per field.

        DpIds absent from the device (ReadError) are silently skipped (field = None).
        """
        from .dp_ids import DpId

        async def _try(dp_id: int) -> Optional[bytes]:
            try:
                return await self.read(dp_id)
            except Exception:
                logger.debug(f"Ble20: get_device_identification DpId={dp_id} unavailable")
                return None

        def _datatype(dp_id: int) -> int:
            if inv and dp_id in inv:
                return inv[dp_id]['datatype']
            return -1

        def _str(raw: Optional[bytes]) -> Optional[str]:
            if not raw:
                return None
            s = raw.rstrip(b'\x00').decode('ascii', errors='replace')
            return s or None

        def _u8(raw: Optional[bytes]) -> Optional[int]:
            return raw[0] if raw else None

        def _i32(raw: Optional[bytes]) -> Optional[int]:
            if raw and len(raw) >= 4:
                return struct.unpack_from('<i', raw)[0]
            if raw and len(raw) >= 2:
                return struct.unpack_from('<h', raw)[0]
            return raw[0] if raw else None

        def _u32(raw: Optional[bytes]) -> Optional[int]:
            if raw and len(raw) >= 4:
                return struct.unpack_from('<I', raw)[0]
            if raw and len(raw) >= 2:
                return struct.unpack_from('<H', raw)[0]
            return raw[0] if raw else None

        def _auto(raw: Optional[bytes], dp_id: int) -> Optional[int]:
            dt = _datatype(dp_id)
            if dt == DpType.Signed:
                return _i32(raw)
            return _u32(raw)

        name_raw         = await _try(DpId.DP_NAME)
        series_raw       = await _try(DpId.DP_DEVICE_SERIES)
        variant_raw      = await _try(DpId.DP_DEVICE_VARIANT)
        prod_date_raw    = await _try(DpId.DP_DEVICE_PRODUCTION_DATE)
        number_raw       = await _try(DpId.DP_DEVICE_NUMBER)
        sap_raw          = await _try(DpId.DP_DEVICE_SAP_NUMBER)
        fw_rs_raw        = await _try(DpId.DP_FW_RS_VERSION)
        fw_ts_raw        = await _try(DpId.DP_FW_TS_VERSION)
        model_raw        = await _try(DpId.DP_DEVICE_MODEL)
        unique_raw       = await _try(DpId.DP_UNIQUE_DEVICE_NUMBER)
        boot_raw         = await _try(DpId.DP_BOOTLOADER_VARIANT)
        prod_sap_raw     = await _try(DpId.DP_SALES_PRODUCT_SAP_NUMBER)
        prod_serial_raw  = await _try(DpId.DP_SALES_PRODUCT_SERIAL_NUMBER)

        return Ble20DeviceIdentification(
            name                        = _str(name_raw),
            device_series               = _u8(series_raw),
            device_variant              = _u8(variant_raw),
            device_number               = _auto(number_raw, DpId.DP_DEVICE_NUMBER),
            device_production_date      = _u32(prod_date_raw),
            device_sap_number           = _str(sap_raw) if sap_raw and _datatype(DpId.DP_DEVICE_SAP_NUMBER) == DpType.String
                                          else (_u32(sap_raw) if sap_raw else None),
            fw_rs_version               = _str(fw_rs_raw),
            fw_ts_version               = _u32(fw_ts_raw),
            device_model                = _u8(model_raw),
            device_unique_id            = _u32(unique_raw),
            device_boot_variant         = _u8(boot_raw),
            sales_product_sap_number    = _str(prod_sap_raw),
            sales_product_serial_number = _str(prod_serial_raw),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tx_name(status: int) -> str:
    try:
        return TransmissionStatus(status).name
    except ValueError:
        return f"0x{status:02X}"
