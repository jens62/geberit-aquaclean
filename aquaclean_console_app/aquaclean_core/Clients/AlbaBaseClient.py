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
# Mera Comfort profile setting ID → Alba stored DpId
# (from docs/developer/mera-comfort-alba-mapping.md)
_PROFILE_SETTING_DPID: dict[int, DpId] = {
    1: DpId.DP_STORED_ANAL_SPRAY_ARM_OSCILLATION,  # range 0–1
    2: DpId.DP_STORED_ANAL_SPRAY_INTENSITY,         # range 0–4
    4: DpId.DP_STORED_ANAL_SPRAY_ARM_POSITION,      # range 0–4
    6: DpId.DP_STORED_SHOWER_WATER_TEMPERATURE,     # range 0–5
}

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

        all_data = [0] * 12
        for idx, dp_id in enumerate(_SPL_DPID):
            all_data[idx] = _u32(dp_id)
        # Ble20 shower status enums: 0=Error, 1=Disabled, 2=Ready, >=3=active
        # Normalize to 0/1 so callers' != 0 checks work correctly.
        all_data[2] = 1 if all_data[2] >= 3 else 0  # LADY_SHOWER_STATUS
        all_data[3] = 1 if all_data[3] >= 3 else 0  # ANAL_SHOWER_STATUS
        # Mirror Mera Comfort behaviour: when a short params list is given,
        # data_array[i] = value at params[i].  Full-range calls (e.g. [0..7])
        # are a no-op since params[i] == i for every index.
        if params and len(params) < len(all_data):
            data = [all_data[p] if p < len(all_data) else 0 for p in params]
            data += [0] * (12 - len(data))
        else:
            data = all_data
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
        """Read firmware version from Ble20 DpIds DP_FW_RS_VERSION and DP_FW_TS_VERSION."""
        try:
            raw_rs = await self._ble20.read(int(DpId.DP_FW_RS_VERSION))
            raw_ts = await self._ble20.read(int(DpId.DP_FW_TS_VERSION))
            rs = struct.unpack_from('<I', raw_rs)[0] if len(raw_rs) >= 4 else (raw_rs[0] if raw_rs else 0)
            ts = struct.unpack_from('<I', raw_ts)[0] if len(raw_ts) >= 4 else (raw_ts[0] if raw_ts else 0)
            return {"main": f"RS{rs}.0 TS{ts}", "components": {}}
        except Exception:
            return None

    async def get_stored_profile_settings_async(self) -> dict:
        ps = {}
        for sid, dp_id in _PROFILE_SETTING_DPID.items():
            try:
                raw = await self._ble20.read(int(dp_id))
                ps[sid] = struct.unpack_from('<I', raw)[0] if len(raw) >= 4 else (raw[0] if raw else 0)
            except Exception:
                ps[sid] = 0
        return ps

    async def set_stored_profile_setting_async(self, setting_id: int, value: int) -> None:
        dp_id = _PROFILE_SETTING_DPID.get(setting_id)
        if dp_id is None:
            raise BLEPeripheralTimeoutError(f"Profile setting ID {setting_id} not supported on Alba")
        await self._ble20.write(int(dp_id), bytes([value & 0xFF]))

    async def get_stored_common_settings_async(self) -> dict:
        return {}

    async def get_node_list_async(self):
        return None

    async def get_misc_state_async(self) -> dict:
        """Read all 'misc' DpIds in one BLE session; return as a plain dict."""
        import datetime as _dt

        async def _u32(dp_id: DpId) -> Optional[int]:
            try:
                raw = await self._ble20.read(int(dp_id))
                if not raw:
                    return None
                if len(raw) >= 4:
                    return struct.unpack_from('<I', raw)[0]
                return raw[0]
            except Exception:
                return None

        async def _bool(dp_id: DpId) -> Optional[bool]:
            v = await _u32(dp_id)
            return None if v is None else bool(v)

        async def _ts(dp_id: DpId) -> Optional[str]:
            v = await _u32(dp_id)
            if v is None or v == 0:
                return None
            try:
                return _dt.datetime.fromtimestamp(v, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                return str(v)

        _SPRAY_ARM_CLEANING_STATUS_LABELS = {
            0: "Error", 1: "Disabled", 2: "Ready",
            3: "Arm Extending", 4: "Cleaning", 5: "Arm Retracting",
        }
        _DESCALING_STATUS_LABELS = {
            0: "Idle", 1: "Preparing", 2: "Waiting for descaler",
            3: "Running", 4: "Done",
        }
        _PRODUCT_REGISTRATION_LABELS = {0: "None", 1: "Basic", 2: "Full"}
        _SPRAY_ARM_CLEANING_STATUS_RAW = await _u32(DpId.DP_SPRAY_ARM_CLEANING_STATUS)

        result: dict = {}
        # Active shower parameters
        result["active_intensity"]    = await _u32(DpId.DP_ACTIVE_ANAL_SPRAY_INTENSITY_STATUS)
        result["active_position"]     = await _u32(DpId.DP_ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS)
        result["active_temperature"]  = await _u32(DpId.DP_ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS)
        result["active_oscillation"]  = await _bool(DpId.DP_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS)
        # Spray arm
        sac = _SPRAY_ARM_CLEANING_STATUS_RAW
        result["spray_arm_cleaning_status_raw"] = sac
        result["spray_arm_cleaning_status"]     = _SPRAY_ARM_CLEANING_STATUS_LABELS.get(sac, str(sac)) if sac is not None else None
        # Descaling
        ds_raw = await _u32(DpId.DP_DESCALING_STATUS)
        result["descaling_status_raw"]    = ds_raw
        result["descaling_status"]        = _DESCALING_STATUS_LABELS.get(ds_raw, str(ds_raw)) if ds_raw is not None else None
        result["days_until_next_descaling"]       = await _u32(DpId.DP_DAYS_UNTIL_NEXT_DESCALING)
        result["descaling_cycles"]                = await _u32(DpId.DP_DESCALING_CYCLES)
        result["credits_until_next_descaling"]    = await _u32(DpId.DP_CREDITS_UNTIL_NEXT_DESCALING)
        result["descaling_device_lock_remaining_days"]   = await _u32(DpId.DP_DESCALING_DEVICE_LOCK_REMAINING_DAYS)
        result["descaling_device_relock_remaining_cycles"] = await _u32(DpId.DP_DESCALING_DEVICE_RELOCK_REMAINING_CYCLES)
        desc_lock_raw = await _u32(DpId.DP_DESCALING_DEVICE_LOCK_STATUS)
        result["descaling_device_lock_status_raw"] = desc_lock_raw
        result["descaling_device_lock_status"]     = {0: "Unlocked", 1: "Pre-locked", 2: "Locked"}.get(desc_lock_raw, str(desc_lock_raw)) if desc_lock_raw is not None else None
        result["unaccounted_shower_cycles"]       = await _u32(DpId.DP_UNACCOUNTED_SHOWER_CYCLES)
        result["timestamp_last_descaling"]        = await _ts(DpId.DP_TIMESTAMP_OF_LAST_DESCALING)
        result["timestamp_last_descaling_request"] = await _ts(DpId.DP_TIMESTAMP_OF_LAST_DESCALING_REQUEST)
        # User presence
        result["user_detection_status"] = await _bool(DpId.DP_USER_DETECTION_STATUS)
        # Time / uptime
        rtc_raw = await _u32(DpId.DP_RTC_TIME)
        if rtc_raw and rtc_raw > 0:
            try:
                result["rtc_time"] = _dt.datetime.fromtimestamp(rtc_raw, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                result["rtc_time"] = str(rtc_raw)
        else:
            result["rtc_time"] = None
        op_total = await _u32(DpId.DP_OPERATION_TIME_TOTAL)
        op_up    = await _u32(DpId.DP_OPERATION_TIME_SINCE_POWER_UP)
        result["operation_time_total_s"]        = op_total
        result["operation_time_since_power_up_s"] = op_up
        # Errors (0 = OK)
        result["error_power_supply"]       = await _bool(DpId.DP_POWER_SUPPLY_ERROR_STATUS)
        result["error_water_heater"]       = await _bool(DpId.DP_WATER_HEATER_ERROR_STATUS)
        result["error_level_control"]      = await _bool(DpId.DP_LEVEL_CONTROL_ERROR_STATUS)
        result["error_user_detection"]     = await _bool(DpId.DP_USER_DETECTION_ERROR_STATUS)
        result["error_water_pump"]         = await _bool(DpId.DP_WATER_PUMP_ERROR_STATUS)
        result["error_spray_arm_drive"]    = await _bool(DpId.DP_SPRAY_ARM_DRIVE_ERROR_STATUS)
        result["error_maintenance_request"] = await _bool(DpId.DP_MAINTENANCE_REQUEST_STATUS)
        result["error_descaling"]          = await _bool(DpId.DP_DESCALING_ERROR_STATUS)
        # Modes
        result["demo_mode"]     = await _bool(DpId.DP_DEMO_MODE)
        result["showroom_mode"] = await _bool(DpId.DP_SHOWROOM_MODE)
        result["dry_run_mode"]  = await _bool(DpId.DP_DRY_RUN_MODE)
        prod_reg_raw = await _u32(DpId.DP_PRODUCT_REGISTRATION_LEVEL)
        result["product_registration_level_raw"] = prod_reg_raw
        result["product_registration_level"]     = _PRODUCT_REGISTRATION_LABELS.get(prod_reg_raw, str(prod_reg_raw)) if prod_reg_raw is not None else None
        # Firmware / hardware versions
        result["fw_rs_version"] = await _u32(DpId.DP_FW_RS_VERSION)
        result["fw_ts_version"] = await _u32(DpId.DP_FW_TS_VERSION)
        result["hw_rs_version"] = await _u32(DpId.DP_HW_RS_VERSION)
        result["mcu_version"]   = await _u32(DpId.DP_MCU_VERSION)
        # Pairing secret (diagnostic — hex-encoded bytes)
        try:
            raw_secret = await self._ble20.read(int(DpId.DP_PAIRING_SECRET))
            result["pairing_secret_hex"] = raw_secret.hex() if raw_secret else None
        except Exception:
            result["pairing_secret_hex"] = None
        return result

    async def get_misc_state_fast_async(self) -> dict:
        """Read only the 9 fast-changing misc DpIds (~1.8 s, 9 BLE reads).

        Returns a subset of get_misc_state_async() keys.  Caller must merge
        this result with a cached full misc dict so all expected keys are present.
        Fields covered: active shower params, spray arm status, descaling status,
        days until descaling, unaccounted shower cycles, user detection status.
        """
        async def _u32(dp_id: DpId) -> Optional[int]:
            try:
                raw = await self._ble20.read(int(dp_id))
                if not raw:
                    return None
                return struct.unpack_from('<I', raw)[0] if len(raw) >= 4 else raw[0]
            except Exception:
                return None

        _SPRAY_LABELS = {0: "Error", 1: "Disabled", 2: "Ready",
                         3: "Arm Extending", 4: "Cleaning", 5: "Arm Retracting"}
        _DESCALING_LABELS = {0: "Idle", 1: "Preparing", 2: "Waiting for descaler",
                             3: "Running", 4: "Done"}
        result: dict = {}
        result["active_intensity"]   = await _u32(DpId.DP_ACTIVE_ANAL_SPRAY_INTENSITY_STATUS)
        result["active_position"]    = await _u32(DpId.DP_ACTIVE_ANAL_SPRAY_ARM_POSITION_STATUS)
        result["active_temperature"] = await _u32(DpId.DP_ACTIVE_SHOWER_WATER_TEMPERATURE_STATUS)
        osc = await _u32(DpId.DP_ACTIVE_ANAL_SPRAY_ARM_OSCILLATION_STATUS)
        result["active_oscillation"] = None if osc is None else bool(osc)
        sac = await _u32(DpId.DP_SPRAY_ARM_CLEANING_STATUS)
        result["spray_arm_cleaning_status_raw"] = sac
        result["spray_arm_cleaning_status"] = _SPRAY_LABELS.get(sac, str(sac)) if sac is not None else None
        ds = await _u32(DpId.DP_DESCALING_STATUS)
        result["descaling_status_raw"] = ds
        result["descaling_status"] = _DESCALING_LABELS.get(ds, str(ds)) if ds is not None else None
        result["days_until_next_descaling"] = await _u32(DpId.DP_DAYS_UNTIL_NEXT_DESCALING)
        result["unaccounted_shower_cycles"] = await _u32(DpId.DP_UNACCOUNTED_SHOWER_CYCLES)
        ud = await _u32(DpId.DP_USER_DETECTION_STATUS)
        result["user_detection_status"] = None if ud is None else bool(ud)
        return result

    async def get_instanced_stats_async(self) -> dict:
        """Read all instanced DpIds: progress indicators, version strings, statistics counters."""

        async def _u32i(dp_id: DpId, instance: int) -> Optional[int]:
            try:
                raw = await self._ble20.read(int(dp_id), instance)
                if not raw:
                    return None
                if len(raw) >= 4:
                    return struct.unpack_from('<I', raw)[0]
                return raw[0]
            except Exception:
                return None

        result: dict = {}

        # Progress DpIds — 4 instances: 0=MaxTotal, 1=ElapsedTotal, 2=MaxStep, 3=ElapsedStep
        for dp_id, key in [
            (DpId.DP_ANAL_SHOWER_PROGRESS,        "anal_shower_progress"),
            (DpId.DP_SPRAY_ARM_CLEANING_PROGRESS, "spray_arm_cleaning_progress"),
            (DpId.DP_DESCALING_PROGRESS,          "descaling_progress"),
        ]:
            max_total     = await _u32i(dp_id, 0)
            elapsed_total = await _u32i(dp_id, 1)
            max_step      = await _u32i(dp_id, 2)
            elapsed_step  = await _u32i(dp_id, 3)
            pct = round(elapsed_total / max_total * 100, 1) if max_total else None
            result[key] = {
                "max_total":     max_total,
                "elapsed_total": elapsed_total,
                "max_step":      max_step,
                "elapsed_step":  elapsed_step,
                "pct":           pct,
            }

        # Version DpIds
        async def _version(dp_id: DpId, n_instances: int) -> Optional[str]:
            parts = [await _u32i(dp_id, i) for i in range(n_instances)]
            if all(p is None for p in parts):
                return None
            return ".".join(str(p or 0) for p in parts)

        result["fus_version"]            = await _version(DpId.DP_FUS_VERSION, 3)
        result["geberit_loader_version"] = await _version(DpId.DP_GEBERIT_LOADER_VERSION, 2)
        result["wireless_stack_version"] = await _version(DpId.DP_WIRELESS_STACK_VERSION, 3)

        # Statistics counters — instances: 2=UseWithFlush, 31-36=AquaClean-specific
        _STAT_INSTANCES: dict[int, str] = {
            2:  "use_with_flush",
            31: "aquaclean_usages",
            32: "aquaclean_anal_showers",
            33: "aquaclean_lady_showers",
            34: "aquaclean_dryings",
            35: "aquaclean_descalings",
            36: "aquaclean_spray_arm_cleanings",
        }
        for dp_id, key in [
            (DpId.DP_STATISTIC_COUNTER_SINCE_POWER_UP, "stats_since_power_up"),
            (DpId.DP_STATISTIC_COUNTER_SINCE_RESET,    "stats_since_reset"),
            (DpId.DP_STATISTIC_COUNTER_TOTAL,          "stats_total"),
        ]:
            result[key] = {name: await _u32i(dp_id, inst) for inst, name in _STAT_INSTANCES.items()}

        return result

    async def write_dp_async(self, dp_id: int, value: int) -> None:
        """Write a value to a DpId.
        Uses 4-byte little-endian uint32 for large values (e.g. Unix timestamps).
        Uses 1-byte for enum/boolean values (0-255), which is what the device expects
        for all control DpIds. The 4-byte path is only needed for DP_SET_RTC_TIME.
        """
        data = struct.pack('<I', value) if value > 0xFFFF else bytes([value & 0xFF])
        await self._ble20.write(dp_id, data)

    # ── Not implemented on Alba — raise so callers fall back gracefully ───────

    async def get_filter_status_async(self):
        raise BLEPeripheralTimeoutError("GetFilterStatus not supported on Alba")

    async def get_statistics_descale_async(self):
        raise BLEPeripheralTimeoutError("GetStatisticsDescale not supported on Alba")

    async def SetCommandAsync(self, command):
        raise BLEPeripheralTimeoutError(f"SetCommand (code {command}) not supported on Alba")
