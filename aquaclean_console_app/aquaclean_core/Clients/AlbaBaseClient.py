"""Adapter: maps AquaCleanBaseClient-style API calls to Ble20 wire operations.

Provides the same method signatures as AquaCleanBaseClient so that action
functions in main.py (_fetch_state, _fetch_identification, …) work unchanged
against an Alba device.  Methods that have no Alba equivalent raise
BLEPeripheralTimeoutError so the caller's existing try/except handles them.
"""
import datetime
import struct
import logging
from typing import Optional

from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos.SystemParameterList import SystemParameterList
from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos.DeviceIdentification import DeviceIdentification
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import BLEPeripheralTimeoutError
from aquaclean_console_app.aquaclean_core.DeviceNameUtil import get_full_name
from aquaclean_console_app.bluetooth_le.LE.dp_ids import DpId

logger = logging.getLogger(__name__)

# Ble20 DpId → SPL data_array index mapping.
# Mirrors the index expectations in _fetch_state / _state_changed_timer_elapsed:
#   index 0 = IsUserSitting       (DP_SENSOR_DISTANCE_STATUS = 60)
#   index 1 = IsDryerRunning      (no dryer on Alba → 0)
#   index 2 = IsLadyShowerRunning (DP_LADY_SHOWER_STATUS = 872)
#   index 3 = IsAnalShowerRunning (DP_ANAL_SHOWER_STATUS  = 564)
#   index 4 = descaling state     (no equivalent → 0)
#   index 5 = descaling min       (no equivalent → 0)
#   index 6 = last error code     (no equivalent → 0)
#   index 7 = service state       (no equivalent → 0)
_SPL_DPID: list[Optional[DpId]] = [
    DpId.DP_SENSOR_DISTANCE_STATUS,   # 0
    None,                              # 1  dryer — no equivalent
    DpId.DP_LADY_SHOWER_STATUS,        # 2
    DpId.DP_ANAL_SHOWER_STATUS,        # 3
    None,                              # 4  descaling state
    None,                              # 5  descaling min
    None,                              # 6  last error
    None,                              # 7  service state
]


class AlbaBaseClient:
    """AquaCleanBaseClient-compatible adapter for Ble20/Alba devices.

    Instantiated by AlbaClient.  Not used directly.
    """

    def __init__(self, connector, ble20):
        self.bluetooth_le_connector = connector
        self._ble20 = ble20
        self._inv: dict = {}   # set by AlbaClient.post_connect() after inventory

    async def disconnect(self):
        await self.bluetooth_le_connector.disconnect()

    async def get_system_parameter_list_async(self, params: list) -> SystemParameterList:
        """Poll Ble20 state DpIds; return as a SystemParameterList."""
        state = await self._ble20.poll_state()

        def _u32(dp_id: Optional[DpId]) -> int:
            if dp_id is None:
                return 0
            raw = state.get(int(dp_id))
            if not raw:
                return 0
            if len(raw) >= 4:
                return struct.unpack_from('<I', raw)[0]
            return raw[0]

        data = [0] * 12
        for idx, dp_id in enumerate(_SPL_DPID):
            data[idx] = _u32(dp_id)
        # Ble20 shower status enums: 0=Error, 1=Disabled, 2=Ready, >=3=active
        # (Prerinsing/ArmExtending/Shower/ArmRetracting/Postrinsing)
        # Normalize to 0/1 so callers' != 0 checks work correctly.
        data[2] = 1 if data[2] >= 3 else 0  # LADY_SHOWER_STATUS
        data[3] = 1 if data[3] >= 3 else 0  # ANAL_SHOWER_STATUS
        return SystemParameterList(a=0, data_array=data)

    async def get_device_identification_async(self, profile_id: int = 0) -> DeviceIdentification:
        di = await self._ble20.get_device_identification(self._inv)
        sap    = di.sales_product_sap_number    or ""
        serial = di.sales_product_serial_number or ""
        if di.device_series is not None and di.device_variant is not None:
            description = get_full_name(di.device_series, di.device_variant)
        else:
            description = di.name or "Geberit AquaClean Alba"
        ts = di.device_production_date
        prod_date = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d") if ts else ""
        return DeviceIdentification(
            sap_number      = sap,
            serial_number   = serial,
            production_date = prod_date,
            description     = description,
        )

    async def get_device_initial_operation_date(self) -> str:
        return ""

    async def get_soc_application_versions_async(self):
        return None

    async def get_firmware_version_list_async(self, payload=None):
        return None

    async def get_stored_profile_settings_async(self) -> dict:
        return {}

    async def get_stored_common_settings_async(self) -> dict:
        return {}

    async def get_node_list_async(self):
        return None

    # ── Not implemented on Alba — raise so callers fall back gracefully ───────

    async def get_filter_status_async(self):
        raise BLEPeripheralTimeoutError("GetFilterStatus not supported on Alba")

    async def get_statistics_descale_async(self):
        raise BLEPeripheralTimeoutError("GetStatisticsDescale not supported on Alba")

    async def SetCommandAsync(self, command):
        raise BLEPeripheralTimeoutError(f"SetCommand (code {command}) not supported on Alba")
