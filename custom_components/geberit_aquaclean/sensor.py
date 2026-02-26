"""Sensors — device identification and descale statistics."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_DEVICE_ID, CONF_ESPHOME_HOST, CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity, AquaCleanProxyEntity

# (data_key, friendly_name, unit, device_class, state_class, icon)
SENSORS: list[tuple] = [
    # Identification — static, changes only after factory reset / replacement
    ("serial_number",           "Serial Number",              None,  None,                          None,                              "mdi:identifier"),
    ("sap_number",              "SAP Number",                 None,  None,                          None,                              "mdi:barcode"),
    ("description",             "Model",                      None,  None,                          None,                              "mdi:toilet"),
    ("production_date",         "Production Date",            None,  None,                          None,                              "mdi:calendar-badge"),
    ("initial_operation_date",  "Initial Operation Date",     None,  None,                          None,                              "mdi:calendar-check"),
    ("soc_versions",            "SOC Versions",               None,  None,                          None,                              "mdi:chip"),
    # Descale statistics
    ("days_until_next_descale",          "Days Until Next Descale",          "d",  SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT,       "mdi:water-remove"),
    ("days_until_shower_restricted",     "Days Until Shower Restricted",     "d",  SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT,       "mdi:water-alert"),
    ("shower_cycles_until_confirmation", "Shower Cycles Until Confirmation", None, None,                       SensorStateClass.MEASUREMENT,       "mdi:counter"),
    ("number_of_descale_cycles",         "Number of Descale Cycles",         None, None,                       SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),
    ("unposted_shower_cycles",           "Unposted Shower Cycles",           None, None,                       SensorStateClass.MEASUREMENT,       "mdi:counter"),
    ("date_time_at_last_descale",        "Last Descale",                     None, None,                       None,                               "mdi:calendar-clock"),
    # Poll timing (for countdown visualization)
    ("poll_epoch",    "Last Poll",      None, SensorDeviceClass.TIMESTAMP, None,                              "mdi:clock-check"),
    ("poll_interval", "Poll Interval",  "s",  SensorDeviceClass.DURATION,  SensorStateClass.MEASUREMENT,      "mdi:timer-outline"),
    ("next_poll",     "Next Poll",      None, SensorDeviceClass.TIMESTAMP, None,                              "mdi:calendar-clock"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [
        AquaCleanSensor(coordinator, entry, key, name, unit, device_class, state_class, icon)
        for key, name, unit, device_class, state_class, icon in SENSORS
    ]
    entities.append(AquaCleanBleConnectionSensor(coordinator, entry))
    if coordinator._esphome_host:
        entities.append(AquaCleanEspHomeConnectionSensor(coordinator, entry))
    async_add_entities(entities)


class AquaCleanSensor(AquaCleanEntity, SensorEntity):
    def __init__(
        self, coordinator, entry, key, name, unit, device_class, state_class, icon
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_icon = icon

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)


class AquaCleanBleConnectionSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the Geberit BLE connection string: '{name} (mac)'."""

    _attr_name = "BLE Connection"
    _attr_icon = "mdi:bluetooth"

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ble_connection"
        self._mac: str = entry.data[CONF_DEVICE_ID]

    @property
    def available(self) -> bool:
        return True  # always show the connection string, never Unavailable

    @property
    def native_value(self) -> str:
        name = (self.coordinator.data or {}).get("ble_name") or self.coordinator._ble_name_cache
        if name:
            return f"{name} ({self._mac})"
        return self._mac


class AquaCleanEspHomeConnectionSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the ESPHome proxy connection string: '{name} (host:port)'."""

    _attr_name = "Connection"
    _attr_icon = "mdi:lan"

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_esphome_connection"
        conf = {**entry.data, **entry.options}
        self._host: str = conf.get(CONF_ESPHOME_HOST, "")
        self._port: int = conf.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT)

    @property
    def available(self) -> bool:
        return True  # always show the connection string, never Unavailable

    @property
    def native_value(self) -> str:
        name = (self.coordinator.data or {}).get("esphome_name") or self.coordinator._esphome_name_cache
        if name:
            return f"{name} ({self._host}:{self._port})"
        return f"{self._host}:{self._port}"
