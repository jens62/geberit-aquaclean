"""Number entities — writable user profile settings."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity

# (data_key, setting_id, friendly_name, min_value, max_value, icon)
PROFILE_NUMBERS: list[tuple] = [
    ("ps_anal_shower_pressure", 2, "Anal Shower Pressure", 0, 10, "mdi:water-boiler"),
    ("ps_lady_shower_pressure", 3, "Lady Shower Pressure", 0, 10, "mdi:water-boiler"),
    ("ps_anal_shower_position", 4, "Anal Shower Position",  0, 10, "mdi:arrow-left-right"),
    ("ps_lady_shower_position", 5, "Lady Shower Position",  0, 10, "mdi:arrow-left-right"),
    ("ps_water_temperature",    6, "Water Temperature",     0, 10, "mdi:thermometer-water"),
    ("ps_wc_seat_heat",         7, "WC Seat Heat",          0, 10, "mdi:heat-wave"),
    ("ps_dryer_temperature",    8, "Dryer Temperature",     0, 10, "mdi:hair-dryer"),
    ("ps_dryer_state",          9, "Dryer State",           0, 10, "mdi:hair-dryer"),
    ("ps_odour_extraction",     0, "Odour Extraction",      0,  1, "mdi:air-filter"),
    ("ps_oscillator_state",     1, "Oscillator State",      0,  1, "mdi:rotate-360"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AquaCleanProfileNumber(coordinator, entry, data_key, setting_id, name, min_val, max_val, icon)
        for data_key, setting_id, name, min_val, max_val, icon in PROFILE_NUMBERS
    )


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
    def native_value(self) -> float | None:
        val = (self.coordinator.data or {}).get(self._data_key)
        return float(val) if val is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_profile_setting(self._setting_id, int(value))
        await self.coordinator.async_request_refresh()
