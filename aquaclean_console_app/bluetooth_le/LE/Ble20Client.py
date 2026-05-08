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
from typing import Optional

from .command_id import CommandId
from .transmission_status import TransmissionStatus

logger = logging.getLogger(__name__)


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

    RECV_TIMEOUT = 15.0

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
          DP_SENSOR_DISTANCE_STATUS (60)  — user present
          DP_START_STOP_ANAL_SHOWER (563)
          DP_ANAL_SHOWER_STATUS     (564)
          DP_LID_LIFTER_POSITION    (1008)
        """
        from .dp_ids import DpId
        _POLL_IDS = [
            DpId.DP_SENSOR_DISTANCE_STATUS,
            DpId.DP_START_STOP_ANAL_SHOWER,
            DpId.DP_ANAL_SHOWER_STATUS,
            DpId.DP_LID_LIFTER_POSITION,
        ]
        result: dict[int, bytes] = {}
        for dp_id in _POLL_IDS:
            try:
                result[int(dp_id)] = await self.read(int(dp_id))
            except Exception:
                logger.debug(f"Ble20: poll_state DpId={dp_id} unavailable", exc_info=True)
        return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tx_name(status: int) -> str:
    try:
        return TransmissionStatus(status).name
    except ValueError:
        return f"0x{status:02X}"
