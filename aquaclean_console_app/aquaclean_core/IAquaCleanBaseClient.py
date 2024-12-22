from abc import ABC, abstractmethod
import asyncio

class ConnectionStatusChangedEventArgs(asyncio.Event):
    def __init__(self, is_connected: bool):
        self.is_connected = is_connected

class IAquaCleanBaseClient(ABC):
    @abstractmethod
    async def connect_async(self, device_id: str):
        pass

    @abstractmethod
    async def set_command_async(self, command):
        pass

    @abstractmethod
    async def get_system_parameter_list_async(self, parameter_list: bytes):
        pass

    @abstractmethod
    async def get_device_identification_async(self, node: int):
        pass

    @abstractmethod
    async def get_firmware_version_list(self, arg1, arg2):
        pass

    @abstractmethod
    async def set_stored_profile_setting_async(self, profile_setting, setting_value: int):
        pass

    @abstractmethod
    async def get_stored_profile_setting_async(self, profile_setting):
        pass

    @abstractmethod
    async def get_device_initial_operation_date(self):
        pass
