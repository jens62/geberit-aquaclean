"""Binary sensors — live device state and connection status."""
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
from .entity import AquaCleanEntity, AquaCleanProxyEntity

# (data_key, friendly_name, device_class, icon_on, icon_off)
BINARY_SENSORS: list[tuple[str, str, BinarySensorDeviceClass | None, str, str]] = [
    ("is_user_sitting",        "User Sitting",        BinarySensorDeviceClass.OCCUPANCY, "geberit:is_user_sitting-on",  "geberit:is_user_sitting-off"),
    ("is_anal_shower_running", "Anal Shower Running", None,                               "geberit:analshower",           "geberit:analshower"),
    ("is_lady_shower_running", "Lady Shower Running", None,                               "geberit:ladywash",             "geberit:ladywash"),
    ("is_dryer_running",       "Dryer Running",       None,                               "geberit:dryer-on",             "geberit:dryer-off"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [
        AquaCleanBinarySensor(coordinator, entry, key, name, device_class, icon_on, icon_off)
        for key, name, device_class, icon_on, icon_off in BINARY_SENSORS
    ]
    entities.append(AquaCleanBleConnectedSensor(coordinator, entry))
    if coordinator._esphome_host:
        entities.append(AquaCleanEspHomeConnectedSensor(coordinator, entry))
    async_add_entities(entities)


class AquaCleanBinarySensor(AquaCleanEntity, BinarySensorEntity):
    def __init__(self, coordinator, entry, key, name, device_class, icon_on, icon_off) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._icon_on = icon_on
        self._icon_off = icon_off

    @property
    def icon(self) -> str:
        return self._icon_on if self.is_on else self._icon_off

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get(self._key))


class AquaCleanBleConnectedSensor(AquaCleanEntity, BinarySensorEntity):
    """Binary sensor: True when the last poll successfully reached the Geberit via BLE."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "BLE Connected"

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ble_connected"

    @property
    def available(self) -> bool:
        return True  # always show Connected/Disconnected, never Unavailable

    @property
    def icon(self) -> str:
        return "mdi:bluetooth-connect" if self.is_on else "mdi:bluetooth-off"

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        connected_at = self.coordinator.ble_connected_at
        return {"connected_at": connected_at.isoformat() if connected_at else None}


class AquaCleanEspHomeConnectedSensor(AquaCleanProxyEntity, BinarySensorEntity):
    """Binary sensor: True when the ESPHome proxy was reachable on the last poll."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Connected"

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_esphome_connected"

    @property
    def available(self) -> bool:
        return True  # always show Connected/Disconnected, never Unavailable

    @property
    def icon(self) -> str:
        return "mdi:lan-connect" if self.is_on else "mdi:lan-disconnect"

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
