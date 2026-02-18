import asyncio
from bleak import BleakClient, BleakScanner, BleakError
from bleak.backends.scanner import AdvertisementData
from bleak.backends.device import BLEDevice


from uuid import UUID

from binascii import hexlify
import logging

from aquaclean_utils                                     import utils   
from myEvent                                             import myEvent   

from typing import Dict, Callable


logger = logging.getLogger(__name__)

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
        self._esphome_api      = None  # Persistent ESP32 API connection
        self._esphome_feature_flags = None  # Cached ESP32 feature flags
        self._esphome_unsub_adv = None  # Deferred advertisement unsubscription


    async def connect_async(self, device_id):
        logger.trace("BluetoothLeConnector: connect")
        if self.esphome_host:
            await self._connect_via_esphome(device_id)
        else:
            await self._connect_local(device_id)


    async def _connect_local(self, device_id):
        device = await BleakScanner.find_device_by_address(device_id)
        if device is None:
            raise BleakError(f"AquaClean device with address {device_id} not found.")

        self.device_address = device.address
        self.device_name = device.name
        logger.trace(f"device.address: {device.address}, device.name: {device.name}")

        self.client = BleakClient(address_or_ble_device=device, disconnected_callback=self._on_disconnected)
        await self.client.connect()
        await self._post_connect()


    async def _ensure_esphome_api_connected(self):
        """Ensure ESP32 API connection is established and reuse it."""
        from aioesphomeapi import APIClient

        if self._esphome_api is not None:
            # Check if connection is still alive
            try:
                if self._esphome_api._connection and self._esphome_api._connection.is_connected:
                    logger.trace("Reusing existing ESP32 API connection")
                    return self._esphome_api
            except Exception:
                pass
            # Connection dead, will reconnect below
            logger.debug("ESP32 API connection lost, reconnecting")
            self._esphome_api = None

        # Create new connection
        logger.debug(f"Establishing persistent ESP32 API connection to {self.esphome_host}:{self.esphome_port}")
        api = APIClient(
            address=self.esphome_host,
            port=self.esphome_port,
            password="",
            noise_psk=self.esphome_noise_psk
        )

        try:
            await asyncio.wait_for(api.connect(login=True), timeout=10.0)
            logger.debug("ESP32 API connection established")

            # Fetch device info to get bluetooth_proxy_feature_flags (once per API connection)
            try:
                device_info = await asyncio.wait_for(api.device_info(), timeout=10.0)
                self._esphome_feature_flags = getattr(device_info, "bluetooth_proxy_feature_flags", 0)
                logger.debug(f"ESP32 bluetooth_proxy_feature_flags: {self._esphome_feature_flags}")
            except Exception as e:
                logger.warning(f"Failed to get device info, using default feature_flags=0: {e}")
                self._esphome_feature_flags = 0

            self._esphome_api = api
            return api
        except asyncio.TimeoutError:
            raise BleakError(f"Timeout connecting to ESPHome proxy at {self.esphome_host}:{self.esphome_port}")
        except Exception as e:
            raise BleakError(f"Failed to connect to ESPHome proxy at {self.esphome_host}: {e}")


    async def _connect_via_esphome(self, device_id):
        from bluetooth_le.LE.ESPHomeAPIClient import ESPHomeAPIClient
        from aioesphomeapi import APIClient

        logger.debug(f"BluetoothLeConnector: connecting to BLE device via ESPHome proxy")

        # TEMPORARY: Create fresh API connection like probe script (don't reuse)
        logger.debug(f"Creating fresh ESP32 API connection to {self.esphome_host}:{self.esphome_port}")
        api = APIClient(
            address=self.esphome_host,
            port=self.esphome_port,
            password="",
            noise_psk=self.esphome_noise_psk
        )
        await asyncio.wait_for(api.connect(login=True), timeout=10.0)
        device_info = await asyncio.wait_for(api.device_info(), timeout=10.0)
        feature_flags = getattr(device_info, "bluetooth_proxy_feature_flags", 0)
        logger.debug(f"Fresh API connection established, feature_flags={feature_flags}")
        self._esphome_feature_flags = feature_flags

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

        logger.trace(f"Scanning for BLE device {device_id} (mac_int={mac_int})")
        unsub_adv = api.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
        try:
            await asyncio.wait_for(found_event.wait(), timeout=30.0)
            logger.debug(f"Found BLE device {device_id} with name: {device_name or 'Unknown'}, address_type: {address_type}")
        except asyncio.TimeoutError:
            unsub_adv()
            raise BleakError(f"AquaClean device {device_id} not found via ESPHome proxy at {self.esphome_host}")
        # NOTE: Do NOT unsubscribe from advertisements here!
        # Unsubscribing sends UnsubscribeBluetoothLEAdvertisementsRequest which
        # clears api_connection_ on the ESP32. The ESP32 loop() then disconnects
        # ALL active BLE connections when api_connection_ is nullptr.
        # We defer unsubscription until after BLE connection is established.

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

                # Ensure previous attempt is fully cleaned up before starting new one
                if self.client is not None:
                    logger.trace(f"Cleaning up previous connection attempt before retry")
                    try:
                        await self.client.disconnect()
                    except Exception as cleanup_error:
                        logger.trace(f"Cleanup error (expected): {cleanup_error}")
                    self.client = None
                    # Give ESP32 a moment to fully process the disconnect
                    await asyncio.sleep(0.5)

                self.client = ESPHomeAPIClient(api, device_id, self._on_disconnected, addr_type, self._esphome_feature_flags)
                await self.client.connect()
                logger.info(f"BLE connection successful with address_type={addr_type}")
                # Do NOT unsubscribe from advertisements — unsubscribing clears
                # api_connection_ on the ESP32 which causes loop() to disconnect
                # ALL active BLE connections. Keep subscription alive; the ESP32
                # stops scanning automatically while a BLE connection is active.
                self._esphome_unsub_adv = unsub_adv
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
        logger.trace(f"self.read_characteristics: {self.read_characteristics}")
        await self._list_services()
        self.connection_status_changed_handlers(self, True, self.device_address, self.device_name)


    async def _list_services(self):
        logger.trace("BluetoothLeConnector: _list_services")

        if not self.client.is_connected:
            logger.trace('1. Error. Client not connected.')
            await self.client.connect()
        else:
            logger.trace('1. in subscribe 1: connected.')

        for service in self.client.services:
            if service.uuid == str(self.SERVICE_UUID):
                for characteristic in service.characteristics:
                    logger.trace(f"got characteristic.uuid {characteristic.uuid}")
                    if characteristic.uuid in str(self.read_characteristics):
                        logger.trace(f"Registering characteristic {characteristic.uuid} for notification.")
                        await self.client.start_notify(characteristic, self._on_data_received)


    async def _on_data_received(self, sender, data):
        logger.trace("BluetoothLeConnector: _on_data_received")
        logger.debug(f"Received data from characteristic {sender.uuid} data: {''.join(f'{b:02X}' for b in data)}")

        await self.data_received_handlers.invoke_async(data)


    def _on_disconnected(self, client):
        logger.trace("BluetoothLeConnector: _on_disconnected")
        self.connection_status_changed_handlers(self, False)


    async def send_message(self, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        # 13:03:18:989	18.11.2024 13:03:18: Sending data to characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 data: 700008000F000000000000000000000000000000
        logger.debug(f"Sending data to characteristic {self.BULK_CHAR_BULK_WRITE_0_UUID} data: {''.join(f'{b:02X}' for b in data)}")
        # result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_0_UUID, data)
        result = await self.client.write_gatt_char(self.BULK_CHAR_BULK_WRITE_0_UUID, data)

        logger.trace(f"result: {result}")


    async def disconnect(self):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        if self.client:
            logger.trace(f"before asyncio.create_task(self.client.disconnect())")
            await self.client.disconnect()
        else:
            logger.trace(f"not self.client, no need to disconnect.")
        # Now safe to unsubscribe from advertisements after BLE is disconnected
        if self._esphome_unsub_adv:
            try:
                self._esphome_unsub_adv()
                logger.trace("Unsubscribed from advertisements after BLE disconnect")
            except Exception:
                pass
            self._esphome_unsub_adv = None

