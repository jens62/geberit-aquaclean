"""Number entities — writable user profile settings and common (device-wide) settings."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity

# Profile setting IDs that only exist on Mera Comfort — unavailable on Alba.
_MERA_ONLY_PROFILE_IDS: frozenset[int] = frozenset({0, 3, 5, 7, 8, 9, 13})

# (data_key, setting_id, friendly_name, min_value, max_value, icon)
# Ranges confirmed from Profile-Settings.xlsx.
PROFILE_NUMBERS: list[tuple] = [
    ("ps_anal_shower_pressure", 2, "Anal Shower Pressure", 0, 4, "mdi:water-boiler"),
    ("ps_lady_shower_pressure", 3, "Lady Shower Pressure", 0, 4, "mdi:water-boiler"),
    ("ps_anal_shower_position", 4, "Anal Shower Position",  0, 4, "mdi:arrow-left-right"),
    ("ps_lady_shower_position", 5, "Lady Shower Position",  0, 4, "mdi:arrow-left-right"),
    ("ps_water_temperature",    6, "Water Temperature",     0, 5, "mdi:thermometer-water"),
    ("ps_wc_seat_heat",         7, "WC Seat Heat",          0, 5, "mdi:heat-wave"),
    ("ps_dryer_temperature",      8,  "Dryer Temperature",     0, 5, "mdi:hair-dryer"),
    ("ps_dryer_state",            9,  "Dryer State",           0, 1, "mdi:hair-dryer"),
    ("ps_dryer_spray_intensity",  13, "Dryer Spray Intensity", 0, 4, "mdi:hair-dryer"),
    ("ps_odour_extraction",     0, "Odour Extraction",      0, 1, "mdi:air-filter"),
    ("ps_oscillator_state",     1, "Oscillator State",      0, 1, "mdi:rotate-360"),
]

# (data_key, command, friendly_name, min_value, max_value, icon)
# Live (active) setting sliders — only available on Alba; write takes effect immediately.
# Trailing "# DpId N" comments are machine-readable: run tools/generate-alba-entity-docs.py after any change.
ALBA_ACTIVE_NUMBERS: list[tuple] = [
    ("alba_active_intensity",   "set_active_intensity",   "Active Spray Intensity",   0, 4, "mdi:water-boiler"),     # r:DpId 571  w:DpId 570
    ("alba_active_position",    "set_active_position",    "Active Spray Position",    0, 4, "mdi:arrow-left-right"), # r:DpId 573  w:DpId 572
    ("alba_active_temperature", "set_active_temperature", "Active Water Temperature", 0, 5, "mdi:thermometer-water"),# r:DpId 575  w:DpId 574
    ("alba_active_oscillation", "set_active_oscillation", "Spray Arm Oscillation",    0, 1, "mdi:rotate-360"),       # r:DpId 577  w:DpId 576
]

# (data_key, setting_id, friendly_name, min_value, max_value, icon)
# Ranges confirmed from BLE log analysis (proc 0x51/0x52).
COMMON_NUMBERS: list[tuple] = [
    ("cs_orientation_light_brightness", 1, "Orientation Light Brightness", 0, 4, "mdi:brightness-6"),
    ("cs_orientation_light_activation", 3, "Orientation Light Activation", 0, 2, "mdi:motion-sensor"),
    ("cs_orientation_light_color",      2, "Orientation Light Color",      0, 6, "mdi:palette"),
    ("cs_odour_extraction_run_on",      0, "Odour Extraction Run-On",      0, 1, "mdi:air-purifier"),
    ("cs_wc_lid_sensor_sensitivity",    4, "WC Lid Sensor Sensitivity",    0, 4, "mdi:motion-sensor"),
    ("cs_wc_lid_open_automatically",    6, "WC Lid Open Automatically",    0, 1, "mdi:door-open"),
    ("cs_wc_lid_close_automatically",   7, "WC Lid Close Automatically",   0, 1, "mdi:door-closed"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        AquaCleanProfileNumber(coordinator, entry, data_key, setting_id, name, min_val, max_val, icon)
        for data_key, setting_id, name, min_val, max_val, icon in PROFILE_NUMBERS
    ]
    entities += [
        AquaCleanCommonNumber(coordinator, entry, data_key, setting_id, name, min_val, max_val, icon)
        for data_key, setting_id, name, min_val, max_val, icon in COMMON_NUMBERS
    ]
    # Alba live-setting sliders — only available when device_type == "alba"
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
    ) -> None:
        super().__init__(coordinator, entry)
        self._data_key = data_key
        self._setting_id = setting_id
        self._attr_unique_id = f"{entry.entry_id}_{data_key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = float(min_value)
        self._attr_native_max_value = float(max_value)

    @property
    def available(self) -> bool:
        if self._setting_id in _MERA_ONLY_PROFILE_IDS:
            if (self.coordinator.data or {}).get("device_type") == "alba":
                return False
        return super().available

    @property
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get(self._data_key)
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
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
    ) -> None:
        super().__init__(coordinator, entry)
        self._data_key = data_key
        self._setting_id = setting_id
        self._attr_unique_id = f"{entry.entry_id}_{data_key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_native_min_value = float(min_value)
        self._attr_native_max_value = float(max_value)

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
