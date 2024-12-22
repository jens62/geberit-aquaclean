from dataclasses import dataclass
from typing import Optional, Callable, Awaitable
import asyncio

class IAquaCleanClient:
    DeviceStateChanged: Callable[[Optional['DeviceStateChangedEventArgs']], None]
    ConnectionStatusChanged: Callable[[Optional['ConnectionStatusChangedEventArgs']], None]

    async def connect(self, device_id: str) -> Awaitable[None]:
        pass

    async def toggle_lid_position(self) -> Awaitable[None]:
        pass

    async def toggle_lady_shower(self) -> Awaitable[None]:
        pass

    async def toggle_anal_shower(self) -> Awaitable[None]:
        pass

    async def get_anal_shower_position(self) -> Awaitable[int]:
        pass

    async def get_anal_shower_pressure(self) -> Awaitable[int]:
        pass

    async def get_water_temperature(self) -> Awaitable[int]:
        pass

    async def get_lady_shower_position(self) -> Awaitable[int]:
        pass

    async def get_lady_shower_pressure(self) -> Awaitable[int]:
        pass

    async def get_dryer_state(self) -> Awaitable[bool]:
        pass

    async def get_oscillation_state(self) -> Awaitable[bool]:
        pass

    async def get_odour_extraction_state(self) -> Awaitable[bool]:
        pass

    async def set_odour_extraction_state(self, state: bool) -> Awaitable[None]:
        pass

    async def get_dryer_temperature(self) -> Awaitable[int]:
        pass

    async def get_wc_seat_heat(self) -> Awaitable[int]:
        pass

    async def get_system_flush_state(self) -> Awaitable[bool]:
        pass


class Subscriptable:
    def __class_getitem__(cls, item):
        return cls._get_child_dict()[item]

    @classmethod
    def _get_child_dict(cls):
        return {k: v for k, v in cls.__dict__.items() if not k.startswith('_')}



@dataclass
class DeviceStateChangedEventArgs(Subscriptable):
    IsUserSitting: bool = None
    IsAnalShowerRunning: bool = None
    IsLadyShowerRunning: bool = None
    IsDryerRunning: bool = None

    def __eq__(self, other):
        if not isinstance(other, DeviceStateChangedEventArgs):
            return False
        return (self.IsUserSitting == other.IsUserSitting and
                self.IsAnalShowerRunning == other.IsAnalShowerRunning and
                self.IsLadyShowerRunning == other.IsLadyShowerRunning and
                self.IsDryerRunning == other.IsDryerRunning)
    
    def __str__(self):
        return f'IsUserSitting: {self.IsUserSitting}, IsAnalShowerRunning: {self.IsAnalShowerRunning}, IsLadyShowerRunning: {self.IsLadyShowerRunning}, IsDryerRunning: {self.IsDryerRunning}'
    
 