import asyncio
from typing import Callable, Any
from dataclasses import dataclass
import logging

from aquaclean_core.IAquaCleanClient                           import IAquaCleanClient   
from aquaclean_core.IAquaCleanClient                           import DeviceStateChangedEventArgs   
from aquaclean_core.Clients                                    import AquaCleanBaseClient
from aquaclean_core.Clients.Commands                           import Commands
from aquaclean_core.Clients.ProfileSettings                    import ProfileSettings
from aquaclean_core.AquaCleanClientFactory                     import AquaCleanClientFactory 
from aquaclean_core.Api.CallClasses.Dtos.DeviceIdentification  import DeviceIdentification   
from aquaclean_core.Message.MessageService                     import MessageService         
from aquaclean_core.IBluetoothLeConnector                      import IBluetoothLeConnector  
from aquaclean_utils                                           import utils   
from myEvent                                                   import myEvent   


import time    
epoch_time = int(time.time())
import datetime

logger = logging.getLogger(__name__)


class AquaCleanClient(IAquaCleanClient):
    def __init__(self, bluetooth_connector: IBluetoothLeConnector):  # type: ignore
        self.SOCApplicationVersions = myEvent.EventHandler()
        self.DeviceIdentification = myEvent.EventHandler()
        self.DeviceInitialOperationDate = myEvent.EventHandler()
        self.DeviceStateChanged = myEvent.EventHandler()
        self.base_client = AquaCleanBaseClient.AquaCleanBaseClient(bluetooth_connector)
        self.last_device_state_changed_event_args = None

        self.SapNumber: str = ""
        self.SerialNumber: str = ""
        self.ProductionDate: str = ""
        self.Description: str = ""
        self.InitialOperationDate: str = ""

    def _on_connection_status_changed(self, sender, *args):
        logger.trace("_on_connection_status_changed")
        if self.ConnectionStatusChanged:
            logger.trace("self.ConnectionStatusChanged is not None")
            self.ConnectionStatusChanged(self, args)
        else:
            logger.trace("self.ConnectionStatusChanged is None")


    async def connect(self, device_id: str):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        await self.base_client.connect_async(device_id)

        logger.debug(f"\n\n\nget_soc_application_versions_async\n\n")

        self.soc_application_versions = await self.base_client.get_soc_application_versions_async()
        logger.debug(f"soc_application_versions: {self.soc_application_versions}")
        await asyncio.sleep(0.01)
        await self.SOCApplicationVersions.invoke_async(self, self.soc_application_versions)
        await asyncio.sleep(0.01)


        logger.debug(f"\n\n\nget_device_identification_async\n\n")

        device_identification = await self.base_client.get_device_identification_async(0)
        self.SapNumber   = device_identification.sap_number
        self.SerialNumber   = device_identification.serial_number
        self.ProductionDate = device_identification.production_date
        self.Description    = device_identification.description

        logger.debug(f"SerialNumber: {self.SerialNumber}")
        logger.debug(f"SapNumber: {self.SapNumber}")
        logger.debug(f"ProductionDate: {self.ProductionDate}")
        logger.debug(f"Description: {self.Description}")
        # SerialNumber: 146.21x.xx.1
        # SapNumber: HB2304EU298413
        # ProductionDate: 11.04.2023
        # Description: AquaClean Mera Comfort

        await asyncio.sleep(0.01)

        await self.DeviceIdentification.invoke_async(self, device_identification)
        # for handler in self.DeviceIdentification.get_handlers():
        #     logger.trace("DeviceIdentification is not None, I.e. handlers subscribed for the event.")
        #     await handler(self, device_identification)
        await asyncio.sleep(0.01)

        # """ 

        logger.debug(f"\n\n\nget_device_initial_operation_date\n\n")

        self.InitialOperationDate = await self.base_client.get_device_initial_operation_date()
        logger.debug(f"self.InitialOperationDate: {self.InitialOperationDate}")
        await asyncio.sleep(0.01)

        await self.DeviceInitialOperationDate.invoke_async(self, self.InitialOperationDate)

        # """       
        await asyncio.sleep(0.01)

        turn = 0
        max_turns = 10
        # while True and turn < max_turns:
        while True:
            turn += 1
            current_time = datetime.datetime.now()
            logger.trace(f"{current_time}")
            start = datetime.datetime.now()

            await self._state_changed_timer_elapsed()
            end = datetime.datetime.now()
            delta = end - start
            millis = int(delta.total_seconds() * 1000) # milliseconds
            logger.trace(f"getting the device changes took {millis} milliseconds")

            await asyncio.sleep(2.5)

    async def disconnect(self):
        await self.base_client.disconnect()


    async def _state_changed_timer_elapsed(self):

        logger.trace("in _state_changed_timer_elapse")

        """
        0 userIsSitting, 
        1 analShowerIsRunning,
        2 ladyShowerIsRunning,
        3 dryerIsRunning,
        4 descalingState, 
        5 descalingDurationInMinutes,
        6 lastErrorCode,
        9 orientationLightState
        """

        logger.debug(f"\n\n\nget_system_parameter_list_async\n\n")

        result = await self.base_client.get_system_parameter_list_async([0, 1, 2, 3, 4, 5, 7, 9])
        ius= result.data_array[0] != 0
        device_state_changed_event_args = DeviceStateChangedEventArgs(
            IsUserSitting=ius,
            IsAnalShowerRunning=result.data_array[1] != 0,
            IsLadyShowerRunning=result.data_array[2] != 0,
            IsDryerRunning=result.data_array[3] != 0
        )

        logger.trace(f"device_state_changed_event_args: {device_state_changed_event_args}")


        if self.last_device_state_changed_event_args is None:
            logger.trace(f"len(self.DeviceStateChanged.get_handlers()): {len(self.DeviceStateChanged.get_handlers())}")
            await self.DeviceStateChanged.invoke_async(self, device_state_changed_event_args)

        elif device_state_changed_event_args != self.last_device_state_changed_event_args:
            # Only invoke event if something changed
            dsc = device_state_changed_event_args
            ldsc = self.last_device_state_changed_event_args
            await self.DeviceStateChanged.invoke_async(self, 
                    DeviceStateChangedEventArgs(
                    IsUserSitting=dsc.IsUserSitting if dsc.IsUserSitting != ldsc.IsUserSitting else None,
                    IsAnalShowerRunning=dsc.IsAnalShowerRunning if dsc.IsAnalShowerRunning != ldsc.IsAnalShowerRunning else None,
                    IsLadyShowerRunning=dsc.IsLadyShowerRunning if dsc.IsLadyShowerRunning != ldsc.IsLadyShowerRunning else None,
                    IsDryerRunning=dsc.IsDryerRunning if dsc.IsDryerRunning != ldsc.IsDryerRunning else None
                ))

        self.last_device_state_changed_event_args = device_state_changed_event_args
        await asyncio.sleep(0.1)


    async def toggle_anal_shower(self):
        await self.base_client.SetCommandAsync(Commands.Commands.ToggleAnalShower)

    async def toggle_lady_shower(self):
        await self.base_client.SetCommandAsync(Commands.Commands.ToggleLadyShower)

    async def toggle_lid_position(self):
        logger.trace("toggle_lid_position(self)")
        await self.base_client.SetCommandAsync(Commands.ToggleLidPosition)
        logger.trace("after await self.base_client.SetCommandAsync(Commands.ToggleLidPosition)")
        await asyncio.sleep(0.01)


    async def get_anal_shower_position(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.AnalShowerPosition)

    async def get_anal_shower_pressure(self):
        return await self.base_client.GetStoredProfileSettingAsync(ProfileSettings.ProfileSettings.AnalShowerPressure)

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
