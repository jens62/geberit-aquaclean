"""Sensors — device identification and descale statistics."""
from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    # Signal strength
    ("ble_rssi",      "BLE Signal",     "dBm", SensorDeviceClass.SIGNAL_STRENGTH, SensorStateClass.MEASUREMENT, "mdi:signal"),
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
        entities.append(AquaCleanProxyWifiSignalSensor(coordinator, entry))
        entities.append(AquaCleanProxyFreeHeapSensor(coordinator, entry))
        entities.append(AquaCleanProxyMaxFreeBlockSensor(coordinator, entry))
        entities.append(AquaCleanProxyLastConnectSensor(coordinator, entry))
        entities.append(AquaCleanProxyLastPollSensor(coordinator, entry))
        entities.append(AquaCleanProxyAvgConnectSensor(coordinator, entry))
        entities.append(AquaCleanProxyAvgPollSensor(coordinator, entry))
        entities.append(AquaCleanProxyStatCountSensor(coordinator, entry))
        entities.append(AquaCleanProxyTransportSensor(coordinator, entry))
        entities.append(AquaCleanProxyAvgBleRssiSensor(coordinator, entry))
        entities.append(AquaCleanProxyMinBleRssiSensor(coordinator, entry))
        entities.append(AquaCleanProxyAvgWifiRssiSensor(coordinator, entry))
        entities.append(AquaCleanProxyMinWifiRssiSensor(coordinator, entry))
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


class AquaCleanProxyWifiSignalSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the ESP32 WiFi signal strength in dBm."""

    _attr_name = "WiFi Signal"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_esphome_wifi_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("esphome_wifi_rssi")


class AquaCleanProxyFreeHeapSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the ESP32 free heap memory in bytes."""

    _attr_name = "Free Heap"
    _attr_native_unit_of_measurement = "B"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:memory"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_esphome_free_heap"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("esphome_free_heap")


class AquaCleanProxyMaxFreeBlockSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the ESP32 largest contiguous free memory block in bytes."""

    _attr_name = "Max Free Block"
    _attr_native_unit_of_measurement = "B"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:memory"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_esphome_max_free_block"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("esphome_max_free_block")


class AquaCleanProxyLastConnectSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the last BLE+ESP32 connect time in milliseconds."""

    _attr_name = "Last Connect"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_connect_ms"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("last_connect_ms")


class AquaCleanProxyLastPollSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the last GATT data fetch time in milliseconds."""

    _attr_name = "Last Poll"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_poll_ms"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("last_poll_ms")


class AquaCleanProxyAvgConnectSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the rolling average connect time in milliseconds."""

    _attr_name = "Avg Connect"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bar"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_avg_connect_ms"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("avg_connect_ms")


class AquaCleanProxyAvgPollSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the rolling average poll time in milliseconds."""

    _attr_name = "Avg Poll"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:chart-bar"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_avg_poll_ms"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("avg_poll_ms")


class AquaCleanProxyStatCountSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the number of successful polls since HA started."""

    _attr_name = "Poll Samples"
    _attr_native_unit_of_measurement = "samples"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_stat_count"

    @property
    def native_value(self) -> int | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("stat_count")


class AquaCleanProxyTransportSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the transport type: bleak / esp32-wifi / esp32-eth."""

    _attr_name = "Transport"
    _attr_icon = "mdi:transit-connection-variant"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_transport"

    @property
    def native_value(self) -> str | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("transport")


class AquaCleanProxyAvgBleRssiSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the session average BLE signal strength (ESP32 ↔ toilet)."""

    _attr_name = "Avg BLE RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_avg_ble_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("avg_ble_rssi")


class AquaCleanProxyMinBleRssiSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the session worst BLE signal strength (ESP32 ↔ toilet)."""

    _attr_name = "Min BLE RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:bluetooth-off"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_min_ble_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("min_ble_rssi")


class AquaCleanProxyAvgWifiRssiSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the session average WiFi signal strength (ESP32 ↔ router)."""

    _attr_name = "Avg WiFi RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_avg_wifi_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("avg_wifi_rssi")


class AquaCleanProxyMinWifiRssiSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the session worst WiFi signal strength (ESP32 ↔ router)."""

    _attr_name = "Min WiFi RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi-off"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_min_wifi_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("min_wifi_rssi")
