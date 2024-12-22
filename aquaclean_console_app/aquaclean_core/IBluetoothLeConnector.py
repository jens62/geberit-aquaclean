import asyncio

class IBluetoothLeConnector:
    def __init__(self):
        self._data_received = []
        self._connection_status_changed = []

    @property
    def data_received(self):
        return self._data_received

    @property
    def connection_status_changed(self):
        return self._connection_status_changed

    def disconnect(self):
        pass

    async def connect_async(self, id):
        pass

    async def send_message(self, data):
        pass

