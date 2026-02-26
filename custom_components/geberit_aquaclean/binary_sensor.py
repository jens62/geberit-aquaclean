"""Binary sensors — live device state."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity

# (data_key, friendly_name, device_class, icon)
BINARY_SENSORS: list[tuple[str, str, BinarySensorDeviceClass | None, str]] = [
    ("is_user_sitting",        "User Sitting",        BinarySensorDeviceClass.OCCUPANCY, "geberit:user-sitting"),
    ("is_anal_shower_running", "Anal Shower Running", None,                               "geberit:analshower"),
    ("is_lady_shower_running", "Lady Shower Running", None,                               "geberit:ladywash"),
    ("is_dryer_running",       "Dryer Running",       None,                               "geberit:dryer"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AquaCleanBinarySensor(coordinator, entry, key, name, device_class, icon)
        for key, name, device_class, icon in BINARY_SENSORS
    )


class AquaCleanBinarySensor(AquaCleanEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, key, name, device_class, icon) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get(self._key))
