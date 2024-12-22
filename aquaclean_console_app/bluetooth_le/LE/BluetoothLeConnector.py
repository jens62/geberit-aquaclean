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

    def __init__(self):
        self.client = None
        self.read_characteristics = {}
        self.data_received_handlers = myEvent.EventHandler()
        self.data_received = None
        self.connection_status_changed_handlers = myEvent.EventHandler()


    async def connect_async(self, device_id):
        logger.trace("BluetoothLeConnector: connect")
        device = await BleakScanner.find_device_by_address(device_id)
        if device is None:
            raise BleakError(f"AquaClean device with address {device_id} not found.")
        
        logger.trace(f"################### device ####################### : {device}")
        logger.trace(f"device.address: {device.address}, device.name: {device.name}")

        self.client = BleakClient(address_or_ble_device = device, disconnected_callback = self._on_disconnected)
        await self.client.connect()

        self.read_characteristics = {
            self.BULK_CHAR_BULK_READ_0_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_1_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_2_UUID: self.data_received,
            self.BULK_CHAR_BULK_READ_3_UUID: self.data_received
        }

        logger.trace(f"self.read_characteristics: {self.read_characteristics}")

        await self._list_services()

        self.connection_status_changed_handlers(self, True, device.address, device.name)


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

