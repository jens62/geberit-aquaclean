import asyncio
import time
from bleak import BleakClient, BleakScanner, BleakError
from bleak.backends.scanner import AdvertisementData
from bleak.backends.device import BLEDevice


from uuid import UUID

from binascii import hexlify
import logging

from aquaclean_console_app.aquaclean_utils                                     import utils
from aquaclean_console_app.myEvent                                             import myEvent

from typing import Dict, Callable


logger = logging.getLogger(__name__)


class ESPHomeConnectionError(Exception):
    """Raised when the app cannot reach the ESP32 API (TCP port 6053).

    The ``timeout`` attribute distinguishes a connect timeout (E1001) from
    a general connection failure (E1002).
    """
    def __init__(self, message: str, timeout: bool = False):
        super().__init__(message)
        self.timeout = timeout


class ESPHomeDeviceNotFoundError(Exception):
    """Raised when the Geberit device is not found via the ESPHome BLE proxy (E0002)."""


class IBluetoothLeConnector:
    pass

class BluetoothLeConnector(IBluetoothLeConnector):
    SERVICE_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a03e0000")
    BULK_CHAR_BULK_WRITE_0_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a13e0000")
    BULK_CHAR_BULK_WRITE_1_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a23e0000")
    BULK_CHAR_BULK_WRITE_2_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a33e0000")
    BULK_CHAR_BULK_WRITE_3_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a43e0000")
    BULK_CHAR_BULK_READ_0_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a53e0000")
    BULK_CHAR_BULK_READ_1_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a63e0000")
    BULK_CHAR_BULK_READ_2_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a73e0000")
    BULK_CHAR_BULK_READ_3_UUID = UUID("3334429d-90f3-4c41-a02d-5cb3a83e0000")
    CCC_UUID = UUID("00002902-0000-1000-8000-00805f9b34fb")

    def __init__(self, esphome_host=None, esphome_port=6053, esphome_noise_psk=None):
        self.client = None
        self.read_characteristics = {}
        self.data_received_handlers = myEvent.EventHandler()
        self.data_received = None
        self.connection_status_changed_handlers = myEvent.EventHandler()
        self.device_address = 'Unknown'
        self.device_name = 'Unknown'
        self.esphome_host      = esphome_host
        self.esphome_port      = esphome_port
        self.esphome_noise_psk = esphome_noise_psk
        self.esphome_proxy_name = None  # ESP32 device name from device_info
        self.esphome_proxy_connected = False  # True when ESP32 API is connected
        self.last_esphome_api_ms: int | None = None  # Time to connect/verify ESP32 API (None = local BLE, 0 = reused)
        self._esphome_unsub_adv = None  # BLE advertisement unsubscribe callable; held until disconnect()
        self.last_ble_ms: int | None = None           # Time for BLE scan + handshake to toilet
        self._esphome_api = None          # Persistent ESP32 API TCP connection (reused when persistent_api=true)
        self._esphome_feature_flags = 0   # Cached bluetooth_proxy_feature_flags from device_info


    async def connect_async(self, device_id):
        logger.silly("BluetoothLeConnector: connect")
        if self.esphome_host:
            await self._connect_via_esphome(device_id)
        else:
            await self._connect_local(device_id)


    async def _connect_local(self, device_id):
        t0 = time.perf_counter()
        device = await BleakScanner.find_device_by_address(device_id)
        if device is None:
            raise BleakError(f"AquaClean device with address {device_id} not found.")

        self.device_address = device.address
        self.device_name = device.name
        logger.debug(f"device.address: {device.address}, device.name: {device.name}")

        self.client = BleakClient(address_or_ble_device=device, disconnected_callback=self._on_disconnected)
        await self.client.connect()
        self.last_esphome_api_ms = None  # No ESP32 proxy
        self.last_ble_ms = int((time.perf_counter() - t0) * 1000)
        await self._post_connect()


    async def _ensure_esphome_api_connected(self):
        """Return a connected ESP32 API client, reusing the existing TCP connection if alive.

        On first call (or after a full disconnect): opens a new TCP connection, fetches
        device_info, stores feature_flags and proxy name, sets last_esphome_api_ms to
        the actual connect time.

        On subsequent calls when the TCP connection is still alive: returns immediately
        with last_esphome_api_ms = 0 (no TCP handshake overhead).

        Raises ESPHomeConnectionError on connection failure.
        """
        from aioesphomeapi import APIClient

        if self._esphome_api is not None:
            # Check whether the underlying TCP connection is still alive.
            # aioesphomeapi sets _connection = None internally when the TCP link
            # drops (e.g. after the 90-second ping-response timeout).  Returning
            # a dead APIClient here would cause "Not connected" errors on every
            # subsequent poll with no chance of recovery.  Detect the dead state
            # and fall through to open a fresh connection instead.
            if getattr(self._esphome_api, '_connection', None) is not None:
                logger.debug("Reusing existing ESP32 API connection")
                self.last_esphome_api_ms = 0
                return self._esphome_api
            logger.warning(
                "ESP32 API connection lost (ping timeout?); "
                "clearing stale client and reconnecting"
            )
            self._esphome_api = None
            self.esphome_proxy_connected = False
            # Fall through to open a fresh connection

        t0 = time.perf_counter()
        api = APIClient(
            address=self.esphome_host,
            port=self.esphome_port,
            password="",
            noise_psk=self.esphome_noise_psk
        )
        try:
            await asyncio.wait_for(api.connect(login=True), timeout=10.0)
        except asyncio.TimeoutError:
            raise ESPHomeConnectionError(
                f"Timeout connecting to ESPHome proxy at {self.esphome_host}:{self.esphome_port}",
                timeout=True,
            )
        except Exception as e:
            raise ESPHomeConnectionError(
                f"Failed to connect to ESPHome proxy at {self.esphome_host}: {e}",
                timeout=False,
            )

        try:
            device_info = await asyncio.wait_for(api.device_info(), timeout=10.0)
            self._esphome_feature_flags = getattr(device_info, "bluetooth_proxy_feature_flags", 0)
            self.esphome_proxy_name = getattr(device_info, "name", "unknown")
        except Exception as e:
            logger.warning(f"Failed to get device info, using default feature_flags=0: {e}")
            self._esphome_feature_flags = 0
            self.esphome_proxy_name = "unknown"

        self._esphome_api = api
        self.esphome_proxy_connected = True
        self.last_esphome_api_ms = int((time.perf_counter() - t0) * 1000)
        logger.debug(f"ESP32 proxy connected: {self.esphome_proxy_name} ({self.last_esphome_api_ms} ms)")
        return api

    async def _connect_via_esphome(self, device_id):
        from aquaclean_console_app.bluetooth_le.LE.ESPHomeAPIClient import ESPHomeAPIClient

        logger.debug(f"BluetoothLeConnector: connecting to BLE device via ESPHome proxy")

        try:
            api = await self._ensure_esphome_api_connected()
        except ESPHomeConnectionError:
            self._esphome_api = None  # Force reconnect on next attempt
            raise

        t_ble = time.perf_counter()  # BLE timing starts after ESP32 API is ready

        # Scan for BLE device using raw advertisements
        mac_int = int(device_id.replace(":", ""), 16)
        found_event = asyncio.Event()
        device_name = ""
        address_type = 0  # Default to PUBLIC (0) if not specified

        def on_raw_advertisements(resp):
            nonlocal device_name, address_type
            for adv in resp.advertisements:
                if adv.address == mac_int:
                    device_name = self._parse_local_name(bytes(adv.data))
                    # Capture address_type from advertisement (0=PUBLIC, 1=RANDOM)
                    # If not present in advertisement, defaults to 0 (PUBLIC)
                    address_type = getattr(adv, 'address_type', 0)
                    found_event.set()

        logger.silly(f"Scanning for BLE device {device_id} (mac_int={mac_int})")
        unsub_adv = api.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
        try:
            await asyncio.wait_for(found_event.wait(), timeout=30.0)
            logger.debug(f"Found BLE device {device_id} with name: {device_name or 'Unknown'}, address_type: {address_type}")
        except asyncio.TimeoutError:
            unsub_adv()
            raise ESPHomeDeviceNotFoundError(
                f"AquaClean device {device_id} not found via ESPHome proxy at {self.esphome_host}"
            )
        # Advertisement subscription intentionally kept alive until BLE connect completes.
        # Calling unsub_adv() before bluetooth_device_connect() sends
        # UnsubscribeBluetoothLEAdvertisementsRequest which clears api_connection_ on the
        # ESP32, causing it to disconnect any BLE client in CONNECTING state immediately
        # → "Disconnect before connected, disconnect scheduled" (reason 0x16).
        # See CLAUDE.md trap 7.

        # Create wrapper client and connect to BLE device
        self.device_address = device_id
        self.device_name = device_name or "Unknown"
        logger.debug(f"Creating ESPHomeAPIClient for {device_id}")

        # Try connecting with PUBLIC first, fallback to RANDOM
        # AquaClean "Geberit AC PRO" uses PUBLIC (0) addressing
        # RANDOM (1) fails with error 256 for this device
        address_types_to_try = [0, 1]  # Try PUBLIC first (works for AquaClean), then RANDOM
        last_error = None

        for attempt, addr_type in enumerate(address_types_to_try, 1):
            try:
                logger.debug(f"BLE connection attempt {attempt}/2 with address_type={addr_type} ({'RANDOM' if addr_type == 1 else 'PUBLIC'})")

                # Ensure previous attempt is fully cleaned up before starting new one.
                # close_api=False: keep the TCP connection alive — we only retry the BLE link.
                if self.client is not None:
                    logger.silly(f"Cleaning up previous connection attempt before retry")
                    try:
                        await self.client.disconnect(close_api=False)
                    except Exception as cleanup_error:
                        logger.silly(f"Cleanup error (expected): {cleanup_error}")
                    self.client = None
                    # Give ESP32 a moment to fully process the disconnect
                    await asyncio.sleep(0.5)

                self.client = ESPHomeAPIClient(api, device_id, self._on_disconnected, addr_type, self._esphome_feature_flags)
                await self.client.connect()
                # Keep advertisement subscription alive for entire BLE connection lifetime.
                # Sending UnsubscribeBluetoothLEAdvertisementsRequest while BLE is active
                # causes the ESP32 to disconnect the BLE client (see CLAUDE.md trap 7).
                # unsub_adv() is stored and called in disconnect() after BLE is torn down.
                self._esphome_unsub_adv = unsub_adv
                logger.info(f"BLE connection successful with address_type={addr_type}")
                break
            except Exception as e:
                last_error = e
                logger.warning(f"BLE connection attempt {attempt}/2 failed with address_type={addr_type}: {e}")
                if attempt < len(address_types_to_try):
                    logger.debug(f"Retrying with alternate address_type")
                else:
                    logger.error(f"All BLE connection attempts failed")
                    unsub_adv()
                    raise last_error

        self.last_ble_ms = int((time.perf_counter() - t_ble) * 1000)
        logger.debug(f"BLE connect complete ({self.last_ble_ms} ms)")
        await self._post_connect()


    def _parse_local_name(self, data: bytes) -> str:
        """Extract device name from raw BLE advertisement AD structures."""
        i = 0
        name = ""
        while i < len(data):
            length = data[i]
            if length == 0 or i + length >= len(data):
                break
            ad_type = data[i + 1]
            value = data[i + 2 : i + 1 + length]
            if ad_type == 0x09:  # Complete Local Name
                return value.decode("utf-8", errors="replace")
            elif ad_type == 0x08:  # Shortened Local Name
                name = value.decode("utf-8", errors="replace")
            i += 1 + length
        return name


    async def _post_connect(self):
        self.read_characteristics = {
            self.BULK_CHAR_BULK_READ_0_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_1_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_2_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_3_UUID: self.data_received
        }
        logger.silly(f"self.read_characteristics: {self.read_characteristics}")
        await self._list_services()
        self.connection_status_changed_handlers(self, True, self.device_address, self.device_name)


    async def _list_services(self):
        logger.silly("BluetoothLeConnector: _list_services")

        if not self.client.is_connected:
            logger.silly('1. Error. Client not connected.')
            await self.client.connect()
        else:
            logger.silly('1. in subscribe 1: connected.')

        for service in self.client.services:
            if service.uuid == str(self.SERVICE_UUID):
                for characteristic in service.characteristics:
                    logger.silly(f"got characteristic.uuid {characteristic.uuid}")
                    if characteristic.uuid in str(self.read_characteristics):
                        logger.silly(f"Registering characteristic {characteristic.uuid} for notification.")
                        await self.client.start_notify(characteristic, self._on_data_received)


    async def _on_data_received(self, sender, data):
        logger.silly("BluetoothLeConnector: _on_data_received")
        logger.silly(f"Received data from characteristic {sender.uuid} data: {''.join(f'{b:02X}' for b in data)}")

        await self.data_received_handlers.invoke_async(data)


    def _on_disconnected(self, client):
        logger.silly("BluetoothLeConnector: _on_disconnected")
        self.connection_status_changed_handlers(self, False)


    async def send_message(self, data):
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        # 13:03:18:989	18.11.2024 13:03:18: Sending data to characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 data: 700008000F000000000000000000000000000000
        logger.silly(f"Sending data to characteristic {self.BULK_CHAR_BULK_WRITE_0_UUID} data: {''.join(f'{b:02X}' for b in data)}")
        # result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_0_UUID, data)
        result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_0_UUID, data)

        logger.silly(f"result: {result}")


    async def disconnect_ble_only(self):
        """BLE-only disconnect — ESP32 API TCP connection stays alive for reuse.

        Used in persistent_api mode: tears down the BLE link to the Geberit and
        releases the advertisement subscription (safe to do after BLE is down —
        see CLAUDE.md trap 7), but leaves self._esphome_api connected so the next
        call to _connect_via_esphome() reuses the TCP connection at 0 ms overhead.
        """
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        if self.client:
            await self.client.disconnect(close_api=False)
            self.client = None
        else:
            logger.silly("not self.client, no need to disconnect BLE.")

        # Unsubscribe from BLE advertisements — safe now that BLE is fully down (trap 7).
        if self._esphome_unsub_adv is not None:
            try:
                self._esphome_unsub_adv()
            except Exception:
                pass
            self._esphome_unsub_adv = None

        # Do NOT reset self._esphome_api or esphome_proxy_connected — TCP stays alive.

    async def disconnect(self):
        logger.silly(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        if self.client:
            logger.silly(f"before asyncio.create_task(self.client.disconnect())")
            await self.client.disconnect()
        else:
            logger.silly(f"not self.client, no need to disconnect.")

        # Unsubscribe from BLE advertisements now that BLE is fully torn down.
        # Must NOT be called while BLE is active — the UnsubscribeBluetoothLEAdvertisementsRequest
        # causes the ESP32 to disconnect any active BLE client (see CLAUDE.md trap 7).
        if self._esphome_unsub_adv is not None:
            try:
                self._esphome_unsub_adv()
            except Exception:
                pass
            self._esphome_unsub_adv = None

        # Reset ESP32 proxy connection state
        if self.esphome_host:
            self.esphome_proxy_connected = False
            self._esphome_api = None  # TCP connection is gone; force reconnect on next call

