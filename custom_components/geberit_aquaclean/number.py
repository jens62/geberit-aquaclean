"""Number entities — writable user profile settings and common (device-wide) settings."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    get_feature_sets, FS_AQUACLEAN_OLD, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_DRYER_FAN,
    FS_WITH_ODOUR_EXTRACTION, FS_WITH_SEAT_HEATER, FS_WITH_WATER_HEATER,
    FS_MERA_COMFORT_ONLY, FS_SELA_ONLY, FS_ALBA_ONLY,
)
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity

# (data_key, setting_id, friendly_name, min_value, max_value, icon, feature_set, wired)
# wired=False: entity disabled by default — feature exists on device but not yet exposed by bridge.
# Ranges confirmed from Profile-Settings.xlsx.
PROFILE_NUMBERS: list[tuple] = [
    # All AquacleanOld models — proc 0x53/0x54 stored profile settings
    ("ps_anal_shower_pressure", 2, "Anal Shower Pressure", 0, 4, "mdi:water-boiler",      FS_AQUACLEAN_OLD,   True),
    ("ps_anal_shower_position", 4, "Anal Shower Position", 0, 4, "mdi:arrow-left-right",  FS_AQUACLEAN_OLD,   True),
    ("ps_water_temperature",    6, "Water Temperature",    0, 5, "mdi:thermometer-water", FS_AQUACLEAN_OLD,   True),
    ("ps_oscillator_state",     1, "Oscillator State",     0, 1, "mdi:rotate-360",        FS_AQUACLEAN_OLD,   True),
    # Models with lady shower
    ("ps_lady_shower_pressure", 3, "Lady Shower Pressure", 0, 4, "mdi:water-boiler",      FS_WITH_LADY_SHOWER, True),
    ("ps_lady_shower_position", 5, "Lady Shower Position", 0, 4, "mdi:arrow-left-right",  FS_WITH_LADY_SHOWER, True),
    # Models with seat heater (Mera Comfort, Tuma Comfort)
    ("ps_wc_seat_heat",         7, "WC Seat Heat",         0, 5, "mdi:heat-wave",         FS_WITH_SEAT_HEATER, True),
    # Models with air dryer
    ("ps_dryer_temperature",    8,  "Dryer Temperature",     0, 5, "mdi:hair-dryer",      FS_WITH_DRYER,       True),
    ("ps_dryer_state",          9,  "Dryer State",           0, 1, "mdi:hair-dryer",      FS_WITH_DRYER,       True),
    # Models with dryer fan speed setting (Mera Comfort fw≥20, Mera Classic fw≥20)
    ("ps_dryer_spray_intensity", 13, "Dryer Spray Intensity", 0, 4, "mdi:hair-dryer",    FS_WITH_DRYER_FAN,   True),
    # Models with odour extraction
    ("ps_odour_extraction",     0, "Odour Extraction",      0, 1, "mdi:air-filter",       FS_WITH_ODOUR_EXTRACTION, True),
    # Not yet wired — disabled by default, visible to users as "not yet in bridge"
    ("ps_lady_oscillation",     14, "Lady Oscillation",     0, 1, "mdi:rotate-360",       FS_WITH_LADY_SHOWER,  False),
    ("ps_water_heating",        12, "Water Heating",        0, 1, "mdi:water-thermometer", FS_WITH_WATER_HEATER, False),
]

# (data_key, command, friendly_name, min_value, max_value, icon)
# Live (active) setting sliders — only available on Alba; write takes effect immediately.
# Trailing "# DpId N" comments are machine-readable: run tools/generate-hacs-entity-docs.py after any change.
ALBA_ACTIVE_NUMBERS: list[tuple] = [
    ("alba_active_intensity",   "set_active_intensity",   "Active Spray Intensity",   0, 4, "mdi:water-boiler"),     # r:DpId 571  w:DpId 570
    ("alba_active_position",    "set_active_position",    "Active Spray Position",    0, 4, "mdi:arrow-left-right"), # r:DpId 573  w:DpId 572
    ("alba_active_temperature", "set_active_temperature", "Active Water Temperature", 0, 5, "mdi:thermometer-water"),# r:DpId 575  w:DpId 574
    ("alba_active_oscillation", "set_active_oscillation", "Spray Arm Oscillation",    0, 1, "mdi:rotate-360"),       # r:DpId 577  w:DpId 576
]

# (data_key, setting_id, friendly_name, min_value, max_value, icon, feature_set, wired)
# wired=False: entity disabled by default — feature exists on device but not yet exposed by bridge.
# Ranges confirmed from BLE log analysis (proc 0x51/0x52).
COMMON_NUMBERS: list[tuple] = [
    # Mera Comfort only — orientation light via proc 0x0B (confirmed working)
    ("cs_orientation_light_brightness", 1, "Orientation Light Brightness", 0, 4, "mdi:brightness-6",   FS_MERA_COMFORT_ONLY,     True),
    ("cs_orientation_light_activation", 3, "Orientation Light Activation", 0, 2, "mdi:motion-sensor",  FS_MERA_COMFORT_ONLY,     True),
    ("cs_orientation_light_color",      2, "Orientation Light Color",      0, 6, "mdi:palette",        FS_MERA_COMFORT_ONLY,     True),
    # Models with odour extraction
    ("cs_odour_extraction_run_on",      0, "Odour Extraction Run-On",      0, 1, "mdi:air-purifier",   FS_WITH_ODOUR_EXTRACTION, True),
    # Mera Comfort only — lid approach sensor settings
    ("cs_wc_lid_sensor_sensitivity",    4, "WC Lid Sensor Sensitivity",    0, 4, "mdi:motion-sensor",  FS_MERA_COMFORT_ONLY,     True),
    ("cs_wc_lid_open_automatically",    6, "WC Lid Open Automatically",    0, 1, "mdi:door-open",      FS_MERA_COMFORT_ONLY,     True),
    ("cs_wc_lid_close_automatically",   7, "WC Lid Close Automatically",   0, 1, "mdi:door-closed",    FS_MERA_COMFORT_ONLY,     True),
    # Not yet wired — disabled by default, visible to users as "not yet in bridge"
    ("cs_auto_flush",                8, "Auto Flush",                0, 1, "mdi:toilet",          FS_AQUACLEAN_OLD, False),
    ("cs_demo_mode",                 9, "Demo Mode",                 0, 1, "mdi:presentation",    FS_AQUACLEAN_OLD, False),
    ("cs_language",                 12, "Language",                  0, 5, "mdi:translate",       FS_AQUACLEAN_OLD, False),
    ("cs_light_sensor_sensitivity", 10, "Light Sensor Sensitivity",  0, 4, "mdi:brightness-auto", FS_SELA_ONLY,     False),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    feature_sets = get_feature_sets(coordinator._device_model)
    entities = [
        AquaCleanProfileNumber(coordinator, entry, data_key, setting_id, name, min_val, max_val, icon, wired)
        for data_key, setting_id, name, min_val, max_val, icon, fs, wired in PROFILE_NUMBERS
        if fs in feature_sets
    ]
    entities += [
        AquaCleanCommonNumber(coordinator, entry, data_key, setting_id, name, min_val, max_val, icon, wired)
        for data_key, setting_id, name, min_val, max_val, icon, fs, wired in COMMON_NUMBERS
        if fs in feature_sets
    ]
    # Alba live-setting sliders
    if FS_ALBA_ONLY in feature_sets:
        entities += [
            AlbaActiveNumber(coordinator, entry, data_key, command, name, min_val, max_val, icon)
            for data_key, command, name, min_val, max_val, icon in ALBA_ACTIVE_NUMBERS
        ]
    async_add_entities(entities)


class AquaCleanProfileNumber(AquaCleanEntity, NumberEntity):
    """A writable user profile setting."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: AquaCleanCoordinator,
        entry: ConfigEntry,
        data_key: str,
        setting_id: int,
        name: str,
        min_value: float,
        max_value: float,
        icon: str,
        wired: bool = True,
    ) -> None:
        super().__init__(coordinator, entry)
        self._data_key = data_key
        self._setting_id = setting_id
        self._wired = wired
        self._attr_unique_id = f"{entry.entry_id}_{data_key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = float(min_value)
        self._attr_native_max_value = float(max_value)
        if not wired:
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Fallback guard for unknown-model installs: no profile settings on Alba.
        if (self.coordinator.data or {}).get("device_type") == "alba":
            return False
        return super().available

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get(self._data_key)
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        if not self._wired:
            raise HomeAssistantError("Not yet implemented in bridge")
        await self.coordinator.async_set_profile_setting(self._setting_id, int(value))
        await self.coordinator.async_request_refresh()


class AquaCleanCommonNumber(AquaCleanEntity, NumberEntity):
    """A writable common (device-wide) setting."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: AquaCleanCoordinator,
        entry: ConfigEntry,
        data_key: str,
        setting_id: int,
        name: str,
        min_value: float,
        max_value: float,
        icon: str,
        wired: bool = True,
    ) -> None:
        super().__init__(coordinator, entry)
        self._data_key = data_key
        self._setting_id = setting_id
        self._wired = wired
        self._attr_unique_id = f"{entry.entry_id}_{data_key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = float(min_value)
        self._attr_native_max_value = float(max_value)
        if not wired:
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        if (self.coordinator.data or {}).get("device_type") == "alba":
            return False
        return super().available

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get(self._data_key)
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        if not self._wired:
            raise HomeAssistantError("Not yet implemented in bridge")
        await self.coordinator.async_set_common_setting(self._setting_id, int(value))
        await self.coordinator.async_request_refresh()


class AlbaActiveNumber(AquaCleanEntity, NumberEntity):
    """Writable live (active) setting for AquaClean Alba — takes effect during the current session."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0

    def __init__(
        self,
        coordinator: AquaCleanCoordinator,
        entry: ConfigEntry,
        data_key: str,
        command: str,
        name: str,
        min_value: float,
        max_value: float,
        icon: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._data_key = data_key
        self._command = command
        self._attr_unique_id = f"{entry.entry_id}_{data_key}_active"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = float(min_value)
        self._attr_native_max_value = float(max_value)

    @property
    def available(self) -> bool:
        return (self.coordinator.data or {}).get("device_type") == "alba" and super().available

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get(self._data_key)
        if val is None:
            return None
        return float(val)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_execute_alba_command(self._command, int(value))
        await self.coordinator.async_request_refresh()
