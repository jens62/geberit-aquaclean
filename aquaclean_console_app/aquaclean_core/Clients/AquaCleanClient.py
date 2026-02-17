import asyncio
from typing import Callable, Any
import logging
import datetime
import time

from aquaclean_core.IAquaCleanClient import IAquaCleanClient, DeviceStateChangedEventArgs   
from aquaclean_core.Clients import AquaCleanBaseClient
from aquaclean_core.Clients.Commands import Commands
from aquaclean_core.Clients.ProfileSettings import ProfileSettings
from aquaclean_utils import utils   
from myEvent import myEvent   

logger = logging.getLogger(__name__)

class AquaCleanClient(IAquaCleanClient):
    def __init__(self, bluetooth_connector):
        self.SOCApplicationVersions = myEvent.EventHandler()
        self.DeviceIdentification = myEvent.EventHandler()
        self.DeviceInitialOperationDate = myEvent.EventHandler()
        self.DeviceStateChanged = myEvent.EventHandler()
        self.base_client = AquaCleanBaseClient.AquaCleanBaseClient(bluetooth_connector)
        self.last_device_state_changed_event_args = None

        self.SapNumber = ""
        self.SerialNumber = ""
        self.ProductionDate = ""
        self.Description = ""
        self.InitialOperationDate = ""

    async def connect(self, device_id: str):
        """Standard connection and info fetching. No infinite loop here."""
        logger.trace(f"Connecting to {device_id}...")
        await self.base_client.connect_async(device_id)

        # Fetch Identification Data
        self.soc_application_versions = await self.base_client.get_soc_application_versions_async()
        await self.SOCApplicationVersions.invoke_async(self, self.soc_application_versions)

        device_identification = await self.base_client.get_device_identification_async(0)
        self.SapNumber = device_identification.sap_number
        self.SerialNumber = device_identification.serial_number
        self.ProductionDate = device_identification.production_date
        self.Description = device_identification.description
        await self.DeviceIdentification.invoke_async(self, device_identification)

        self.InitialOperationDate = await self.base_client.get_device_initial_operation_date()
        await self.DeviceInitialOperationDate.invoke_async(self, self.InitialOperationDate)

    async def connect_ble_only(self, device_id: str):
        """Pure BLE handshake — no data fetching. For on-demand mode so that
        query_ms captures only the actual data request, not eager pre-fetches."""
        logger.trace(f"BLE-only connect to {device_id}...")
        await self.base_client.connect_async(device_id)

    async def start_polling(self, interval: float, on_poll_done=None):
        """The infinite loop logic from your original connect method.
        on_poll_done: optional async callable(millis: int) called after each poll."""
        logger.info(f"Starting status polling loop (interval: {interval}s)")
        while True:
            start = datetime.datetime.now()
            await self._state_changed_timer_elapsed()
            delta = datetime.datetime.now() - start
            millis = int(delta.total_seconds() * 1000)
            logger.trace(f"getting the device changes took {millis} milliseconds")
            if on_poll_done:
                await on_poll_done(millis)

            await asyncio.sleep(interval)

    async def _state_changed_timer_elapsed(self):
        """Original polling logic with specific parameter list [0, 1, 2, 3, 4, 5, 7, 9]"""
        result = await self.base_client.get_system_parameter_list_async([0, 1, 2, 3, 4, 5, 7, 9])
        device_state_changed_event_args = DeviceStateChangedEventArgs(
            IsUserSitting=result.data_array[0] != 0,
            IsAnalShowerRunning=result.data_array[1] != 0,
            IsLadyShowerRunning=result.data_array[2] != 0,
            IsDryerRunning=result.data_array[3] != 0
        )

        if self.last_device_state_changed_event_args is None:
            await self.DeviceStateChanged.invoke_async(self, device_state_changed_event_args)
        elif device_state_changed_event_args != self.last_device_state_changed_event_args:
            dsc = device_state_changed_event_args
            ldsc = self.last_device_state_changed_event_args
            # Restore your original partial-update logic
            await self.DeviceStateChanged.invoke_async(self, DeviceStateChangedEventArgs(
                IsUserSitting=dsc.IsUserSitting if dsc.IsUserSitting != ldsc.IsUserSitting else None,
                IsAnalShowerRunning=dsc.IsAnalShowerRunning if dsc.IsAnalShowerRunning != ldsc.IsAnalShowerRunning else None,
                IsLadyShowerRunning=dsc.IsLadyShowerRunning if dsc.IsLadyShowerRunning != ldsc.IsLadyShowerRunning else None,
                IsDryerRunning=dsc.IsDryerRunning if dsc.IsDryerRunning != ldsc.IsDryerRunning else None
            ))

        self.last_device_state_changed_event_args = device_state_changed_event_args

    async def disconnect(self):
        await self.base_client.disconnect()

    async def toggle_anal_shower(self):
        await self.base_client.SetCommandAsync(Commands.Commands.ToggleAnalShower)

    async def toggle_lady_shower(self):
        await self.base_client.SetCommandAsync(Commands.Commands.ToggleLadyShower)

    async def toggle_lid_position(self):
        await self.base_client.SetCommandAsync(Commands.ToggleLidPosition)
        await asyncio.sleep(0.01)

    # --- Restored Original Getter Methods ---
    async def get_anal_shower_position(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.AnalShowerPosition)

    async def get_water_temperature(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.WaterTemperature)
    
    async def get_lady_shower_position(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.LadyShowerPosition)

    async def get_lady_shower_pressure(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.LadyShowerPressure)

    async def get_dryer_state(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.DryerState) == 1

    async def get_oscilation_state(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.OscillatorState) == 1

    async def get_odour_extraction_state(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.OdourExtraction) == 1

    async def set_odour_extraction_state(self, state: bool):
        await self.base_client.SetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.OdourExtraction, 1 if state else 0)

    async def get_dryer_temperature(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.DryerState)

    async def get_wc_seat_heat(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.WcSeatHeat)

    async def get_system_flush_state(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.SystemFlush) == 1
