"""Application-layer BLE client for Geberit Alba (Ble20) devices.

Exposes the same interface as AquaCleanClient so that main.py uses it
without device-specific branches.  Wraps the low-level Ble20Client
(bluetooth_le/LE/Ble20Client.py) which handles wire-protocol framing.

Typical usage (dispatch path in main.py):
    # connector.connect_async() already called; Arendi handshake done
    client = AlbaClient(connector)
    await client.post_connect()    # mandatory: DataPointInventory
"""
import asyncio
import datetime
import logging

from aquaclean_console_app.aquaclean_core.IAquaCleanClient import (
    IAquaCleanClient, DeviceStateChangedEventArgs,
)
from aquaclean_console_app.aquaclean_core.Clients.AlbaBaseClient import AlbaBaseClient
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import BLEPeripheralTimeoutError
from aquaclean_console_app.bluetooth_le.LE.Ble20Client import Ble20Client
from aquaclean_console_app.bluetooth_le.LE.dp_ids import DpId
from aquaclean_console_app.myEvent import myEvent

logger = logging.getLogger(__name__)


class AlbaClient(IAquaCleanClient):
    """Application-layer client for Geberit Alba (Ble20) devices.

    The connector must already be connected (connector.connect_async() called)
    and the Arendi handshake must be complete before AlbaClient is instantiated.
    Call post_connect() immediately after construction to run DataPointInventory.
    """

    def __init__(self, connector):
        self._connector = connector
        self._ble20 = Ble20Client(connector)
        self._inventory: dict = {}

        self.base_client = AlbaBaseClient(connector, self._ble20)

        # Events — same names and semantics as AquaCleanClient
        self.SOCApplicationVersions = myEvent.EventHandler()
        self.DeviceIdentification = myEvent.EventHandler()
        self.DeviceInitialOperationDate = myEvent.EventHandler()
        self.DeviceStateChanged = myEvent.EventHandler()

        # Data attributes — same names as AquaCleanClient
        self.SapNumber = ""
        self.SerialNumber = ""
        self.ProductionDate = ""
        self.Description = ""
        self.InitialOperationDate = ""
        self.soc_application_versions = None
        self.firmware_versions = None

        self.last_device_state_changed_event_args = None

    async def post_connect(self) -> None:
        """Run DataPointInventory — mandatory first step in Ble20 protocol."""
        self._inventory = await self._ble20.inventory()
        self.base_client._inv = self._inventory

    async def connect(self, device_id: str) -> None:
        """Full connect: BLE + Arendi handshake + inventory + identification fetch."""
        await self._connector.connect_async(device_id)
        if not self._connector.arendi_handshake_done:
            return
        await self.post_connect()
        di = await self.base_client.get_device_identification_async(0)
        self.SapNumber = di.sap_number
        self.SerialNumber = di.serial_number
        self.Description = di.description
        await self.DeviceIdentification.invoke_async(self, di)
        await self.DeviceInitialOperationDate.invoke_async(self, "")

    async def connect_ble_only(self, device_id: str) -> None:
        """BLE-only connect (used when AlbaClient is constructed directly)."""
        await self._connector.connect_async(device_id)
        if self._connector.arendi_handshake_done:
            await self.post_connect()

    async def disconnect(self) -> None:
        await self._connector.disconnect()

    async def start_polling(self, interval: float, on_poll_done=None) -> None:
        logger.info(f"AlbaClient: polling loop started (interval={interval}s)")
        while True:
            start = datetime.datetime.now()
            await self._state_changed_timer_elapsed()
            millis = int((datetime.datetime.now() - start).total_seconds() * 1000)
            logger.trace(f"AlbaClient: poll took {millis} ms")
            if on_poll_done:
                await on_poll_done(millis)
            await asyncio.sleep(interval)

    async def _state_changed_timer_elapsed(self) -> None:
        state = await self._ble20.poll_state()

        def _nonzero(dp_id: DpId) -> bool:
            raw = state.get(int(dp_id))
            return bool(raw and raw[0] != 0)

        args = DeviceStateChangedEventArgs(
            IsUserSitting      = _nonzero(DpId.DP_SENSOR_DISTANCE_STATUS),
            IsAnalShowerRunning= _nonzero(DpId.DP_ANAL_SHOWER_STATUS),
            IsLadyShowerRunning= _nonzero(DpId.DP_LADY_SHOWER_STATUS),
            IsDryerRunning     = False,
        )
        if self.last_device_state_changed_event_args is None:
            await self.DeviceStateChanged.invoke_async(self, args)
        elif args != self.last_device_state_changed_event_args:
            prev = self.last_device_state_changed_event_args
            await self.DeviceStateChanged.invoke_async(self, DeviceStateChangedEventArgs(
                IsUserSitting      = args.IsUserSitting       if args.IsUserSitting       != prev.IsUserSitting       else None,
                IsAnalShowerRunning= args.IsAnalShowerRunning if args.IsAnalShowerRunning != prev.IsAnalShowerRunning else None,
                IsLadyShowerRunning= args.IsLadyShowerRunning if args.IsLadyShowerRunning != prev.IsLadyShowerRunning else None,
                IsDryerRunning     = None,
            ))
        self.last_device_state_changed_event_args = args

    # ── Toggle commands via Ble20 write ──────────────────────────────────────

    async def toggle_anal_shower(self) -> None:
        try:
            raw = await self._ble20.read(DpId.DP_START_STOP_ANAL_SHOWER)
            current = raw[0] if raw else 0
        except Exception:
            current = 0
        await self._ble20.write(DpId.DP_START_STOP_ANAL_SHOWER, bytes([0 if current else 1]))

    async def toggle_lady_shower(self) -> None:
        try:
            raw = await self._ble20.read(DpId.DP_START_STOP_LADY_SHOWER)
            current = raw[0] if raw else 0
        except Exception:
            current = 0
        await self._ble20.write(DpId.DP_START_STOP_LADY_SHOWER, bytes([0 if current else 1]))

    async def toggle_lid_position(self) -> None:
        await self._ble20.write(DpId.DP_TRIGGER_LID_LIFTING, bytes([0x01]))
        await asyncio.sleep(0.01)

    # ── Unsupported on Alba — raise BLEPeripheralTimeoutError ─────────────────

    def _unsupported(self, name: str):
        raise BLEPeripheralTimeoutError(f"{name} not supported on Alba")

    async def toggle_dryer(self):                          self._unsupported("toggle_dryer")
    async def toggle_orientation_light(self):             self._unsupported("toggle_orientation_light")
    async def reset_filter_counter(self):                 self._unsupported("reset_filter_counter")
    async def trigger_flush_manually(self):               self._unsupported("trigger_flush_manually")
    async def prepare_descaling(self):
        await self._ble20.write(DpId.DP_START_STOP_DESCALING, bytes([0x01]))

    async def confirm_descaling(self):
        # Same DpId as prepare; device state (2=waiting) determines effect (2→3).
        await self._ble20.write(DpId.DP_START_STOP_DESCALING, bytes([0x01]))

    async def cancel_descaling(self):
        await self._ble20.write(DpId.DP_START_STOP_DESCALING, bytes([0x00]))

    async def postpone_descaling(self):
        await self._ble20.write(DpId.DP_DESCALING_UNLOCK_DEVICE, bytes([0x01]))
    async def start_cleaning_device(self):                self._unsupported("start_cleaning_device")
    async def execute_next_cleaning_step(self):           self._unsupported("execute_next_cleaning_step")
    async def start_lid_position_calibration(self):       self._unsupported("start_lid_position_calibration")
    async def lid_position_offset_save(self):             self._unsupported("lid_position_offset_save")
    async def lid_position_offset_increment(self):        self._unsupported("lid_position_offset_increment")
    async def lid_position_offset_decrement(self):        self._unsupported("lid_position_offset_decrement")
    async def set_stored_profile_setting(self, sid, val): await self.base_client.set_stored_profile_setting_async(sid, val)
    async def set_stored_common_setting(self, sid, val):  self._unsupported("set_stored_common_setting")
    async def get_anal_shower_position(self):             self._unsupported("get_anal_shower_position")
    async def get_anal_shower_pressure(self):             self._unsupported("get_anal_shower_pressure")
    async def get_water_temperature(self):                self._unsupported("get_water_temperature")
    async def get_lady_shower_position(self):             self._unsupported("get_lady_shower_position")
    async def get_lady_shower_pressure(self):             self._unsupported("get_lady_shower_pressure")
    async def get_dryer_state(self):                      self._unsupported("get_dryer_state")
    async def get_oscilation_state(self):                 self._unsupported("get_oscilation_state")
    async def get_odour_extraction_state(self):           self._unsupported("get_odour_extraction_state")
    async def set_odour_extraction_state(self, state):    self._unsupported("set_odour_extraction_state")
    async def get_dryer_temperature(self):                self._unsupported("get_dryer_temperature")
    async def get_dryer_spray_intensity(self):            self._unsupported("get_dryer_spray_intensity")
    async def get_wc_seat_heat(self):                     self._unsupported("get_wc_seat_heat")
    async def get_system_flush_state(self):               self._unsupported("get_system_flush_state")
