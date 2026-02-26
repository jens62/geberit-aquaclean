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

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity

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
    async_add_entities(
        AquaCleanSensor(coordinator, entry, key, name, unit, device_class, state_class, icon)
        for key, name, unit, device_class, state_class, icon in SENSORS
    )


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
