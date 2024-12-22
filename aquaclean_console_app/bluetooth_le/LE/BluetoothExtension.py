from uuid import UUID
from bleak import BleakClient, BleakScanner, BleakError
import logging

logger = logging.getLogger(__name__)

class BluetoothExtensions:
    @staticmethod
    async def read_bytes(client: BleakClient, char_uuid: UUID):
        try:
            data = await client.read_gatt_char(char_uuid)
            return data
        except Exception as e:
            logger.error(f"Could not read from Characteristic UUID={char_uuid}: {str(e)}")
            raise

    @staticmethod
    async def get_gatt_service(client: BleakClient, service_uuid: UUID):
        services = await client.get_services()
        for service in services:
            if service.uuid == service_uuid:
                return service
        logger.error(f"Could not find GATT service with UUID={service_uuid}")
        raise BleakError(f"Could not find GATT service with UUID={service_uuid}")

    @staticmethod
    async def get_characteristics(service):
        characteristics = service.characteristics
        if not characteristics:
            logger.error("Could not get characteristics from service")
            raise BleakError("Could not get characteristics from service")
        return characteristics

    @staticmethod
    async def write_bytes(client: BleakClient, char_uuid: UUID, data: bytes):
        try:
            await client.write_gatt_char(char_uuid, data)
        except Exception as e:
            logger.error(f"Could not write data to Characteristic UUID={char_uuid}: {str(e)}")
            raise
