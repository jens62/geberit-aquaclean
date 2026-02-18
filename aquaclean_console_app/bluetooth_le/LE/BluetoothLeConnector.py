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


    async def _connect_via_esphome(self, device_id):
        from aioesphomeapi import APIClient
        from bluetooth_le.LE.ESPHomeAPIClient import ESPHomeAPIClient

        logger.debug(f"BluetoothLeConnector: connecting to ESPHome proxy at {self.esphome_host}:{self.esphome_port}")

        # Connect to ESP32 proxy
        api = APIClient(
            address=self.esphome_host,
            port=self.esphome_port,
            password="",
            noise_psk=self.esphome_noise_psk
        )

        try:
            logger.trace(f"Connecting to ESP32 proxy at {self.esphome_host}:{self.esphome_port}")
            await asyncio.wait_for(api.connect(login=True), timeout=10.0)
            logger.debug("Connected to ESP32 proxy")
        except asyncio.TimeoutError:
            raise BleakError(f"Timeout connecting to ESPHome proxy at {self.esphome_host}:{self.esphome_port}")
        except Exception as e:
            raise BleakError(f"Failed to connect to ESPHome proxy at {self.esphome_host}: {e}")

        # Scan for BLE device using raw advertisements
        mac_int = int(device_id.replace(":", ""), 16)
        found_event = asyncio.Event()
        device_name = ""

        def on_raw_advertisements(resp):
            nonlocal device_name
            for adv in resp.advertisements:
                if adv.address == mac_int:
                    device_name = self._parse_local_name(bytes(adv.data))
                    found_event.set()

        logger.trace(f"Scanning for BLE device {device_id} (mac_int={mac_int})")
        unsub = api.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
        try:
            await asyncio.wait_for(found_event.wait(), timeout=30.0)
            logger.debug(f"Found BLE device {device_id} with name: {device_name or 'Unknown'}")
        except asyncio.TimeoutError:
            raise BleakError(f"AquaClean device {device_id} not found via ESPHome proxy at {self.esphome_host}")
        finally:
            unsub()

        # Create wrapper client and connect to BLE device
        self.device_address = device_id
        self.device_name = device_name or "Unknown"
        logger.debug(f"Creating ESPHomeAPIClient for {device_id}")

        self.client = ESPHomeAPIClient(api, device_id, self._on_disconnected)
        await self.client.connect()

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

