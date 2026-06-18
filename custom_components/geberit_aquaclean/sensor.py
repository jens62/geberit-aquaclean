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

from aquaclean_console_app.bluetooth_le.LE.dp_ids import dp_name

from .const import (
    DOMAIN, CONF_DEVICE_ID, CONF_ESPHOME_HOST, CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT,
    get_feature_sets, FS_ALL, FS_AQUACLEAN_OLD, FS_ALBA_ONLY, ALBA_WIRED_DPIDS,
)
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity, AquaCleanProxyEntity

# (data_key, friendly_name, unit, device_class, state_class, icon, feature_set)
# (data_key, friendly_name, unit, device_class, state_class, icon, feature_set, wired)
# wired=False: entity disabled by default — feature exists on device but not yet exposed by bridge.
SENSORS: list[tuple] = [
    # Identification — static, present on all models including Alba
    ("serial_number",           "Serial Number",              None,  None,                          None,                              "mdi:identifier",        FS_ALL,          True),
    ("sap_number",              "SAP Number",                 None,  None,                          None,                              "mdi:barcode",           FS_ALL,          True),
    ("description",             "Model",                      None,  None,                          None,                              "mdi:toilet",            FS_ALL,          True),
    ("production_date",         "Production Date",            None,  None,                          None,                              "mdi:calendar-badge",    FS_ALL,          True),
    ("initial_operation_date",  "Initial Operation Date",     None,  None,                          None,                              "mdi:calendar-check",    FS_ALL,          True),
    ("soc_versions",            "SOC Versions",               None,  None,                          None,                              "mdi:chip",              FS_ALL,          True),
    ("firmware_version",        "Firmware Version",           None,  None,                          None,                              "mdi:chip",              FS_ALL,          True),
    ("firmware_version_date",   "Firmware Release Date",      None,  None,                          None,                              "mdi:calendar-chip",     FS_ALL,          True),
    ("cloud_firmware_version",  "Cloud Firmware Version",     None,  None,                          None,                              "mdi:cloud-upload",      FS_ALL,          True),
    ("cloud_firmware_date",     "Cloud Firmware Release Date", None, None,                          None,                              "mdi:calendar-clock",    FS_ALL,          True),
    # Poll timing — all models
    ("poll_epoch",    "Last Poll",      None, SensorDeviceClass.TIMESTAMP, None,                              "mdi:clock-check",    FS_ALL,          True),
    ("poll_interval", "Poll Interval",  "s",  SensorDeviceClass.DURATION,  SensorStateClass.MEASUREMENT,      "mdi:timer-outline",  FS_ALL,          True),
    ("next_poll",     "Next Poll",      None, SensorDeviceClass.TIMESTAMP, None,                              "mdi:calendar-clock", FS_ALL,          True),
    # Signal strength — all models
    ("ble_rssi",      "BLE Signal",     "dBm", SensorDeviceClass.SIGNAL_STRENGTH, SensorStateClass.MEASUREMENT, "mdi:signal",         FS_ALL,          True),
    # Descale statistics — AquacleanOld protocol only (proc 0x59 / SPL params)
    ("days_until_next_descale",          "Days Until Next Descale",          "d",  SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT,       "mdi:water-remove",    FS_AQUACLEAN_OLD, True),
    ("days_until_shower_restricted",     "Days Until Shower Restricted",     "d",  SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT,       "mdi:water-alert",     FS_AQUACLEAN_OLD, True),
    ("shower_cycles_until_confirmation", "Shower Cycles Until Confirmation", None, None,                       SensorStateClass.MEASUREMENT,       "mdi:counter",         FS_AQUACLEAN_OLD, True),
    ("number_of_descale_cycles",         "Number of Descale Cycles",         None, None,                       SensorStateClass.TOTAL_INCREASING,  "mdi:counter",         FS_AQUACLEAN_OLD, True),
    ("unposted_shower_cycles",           "Unposted Shower Cycles",           None, None,                       SensorStateClass.MEASUREMENT,       "mdi:counter",         FS_AQUACLEAN_OLD, True),
    ("date_time_at_last_descale",        "Last Descale",                     None, None,                       None,                               "mdi:calendar-clock",  FS_AQUACLEAN_OLD, True),
    # Descaling live state (from SPL params 4 and 5) — AquacleanOld only
    ("descaling_state",        "Descaling State",        None, None, SensorStateClass.MEASUREMENT, "geberit:descaling",               FS_AQUACLEAN_OLD, True),
    ("descaling_duration_min", "Descaling Duration",     "min", SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, "mdi:timer-outline",  FS_AQUACLEAN_OLD, True),
    # Filter / honeycomb maintenance — AquacleanOld only
    ("filter_days_remaining", "Days Until Filter Change",       "d",  SensorDeviceClass.DURATION,  SensorStateClass.MEASUREMENT,       "mdi:water-check",     FS_AQUACLEAN_OLD, True),
    ("filter_last_reset",     "Last Filter Reset",              None, SensorDeviceClass.TIMESTAMP, None,                               "mdi:calendar-clock",  FS_AQUACLEAN_OLD, True),
    ("filter_reset_count",    "Filter Reset Count",             None, None,                        SensorStateClass.TOTAL_INCREASING,  "mdi:counter",         FS_AQUACLEAN_OLD, True),
    ("filter_next_change",    "Next Filter Change",             None, SensorDeviceClass.TIMESTAMP, None,                               "mdi:filter-plus",     FS_AQUACLEAN_OLD, True),
    # Calibration offsets (SPL indices 12/13) — AquacleanOld only
    ("lid_offset_position",        "Lid Offset Position",        None, None, SensorStateClass.MEASUREMENT, "geberit:adjustabletoiletseat",  FS_AQUACLEAN_OLD, True),
    ("shower_arm_offset_position", "Spray Arm Offset Position",  None, None, SensorStateClass.MEASUREMENT, "geberit:showerarm-forward",     FS_AQUACLEAN_OLD, True),
]

# (data_key, friendly_name, unit, device_class, state_class, icon)
# Sensors only available on AquaClean Alba devices.
# Trailing "# DpId N" comments are machine-readable: run tools/generate-hacs-entity-docs.py after any change.
ALBA_SENSORS: list[tuple] = [
    ("alba_spray_arm_cleaning_status",               "Spray Arm Cleaning Status",         None, None,                          None,                              "mdi:spray-bottle"),            # DpId 567 (enum)
    ("alba_descaling_status",                        "Descaling Status",                  None, None,                          None,                              "mdi:water-remove"),            # DpId 585 (enum)
    ("alba_days_until_next_descaling",               "Days Until Next Descaling",         "d",  SensorDeviceClass.DURATION,    SensorStateClass.MEASUREMENT,      "mdi:water-remove"),            # DpId 589
    ("alba_descaling_cycles",                        "Descaling Cycles",                  None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 592
    ("alba_credits_until_next_descaling",            "Credits Until Next Descaling",      None, None,                          SensorStateClass.MEASUREMENT,      "mdi:counter"),                 # DpId 781
    ("alba_descaling_device_lock_remaining_days",    "Descaling Lock Remaining Days",     "d",  SensorDeviceClass.DURATION,    SensorStateClass.MEASUREMENT,      "mdi:lock-clock"),              # DpId 977
    ("alba_descaling_device_relock_remaining_cycles","Descaling Relock Remaining Cycles", None, None,                          SensorStateClass.MEASUREMENT,      "mdi:counter"),                 # DpId 979
    ("alba_descaling_device_lock_status",            "Descaling Device Lock",             None, None,                          None,                              "mdi:lock"),                    # DpId 983 (enum)
    ("alba_unaccounted_shower_cycles",               "Unaccounted Shower Cycles",         None, None,                          SensorStateClass.MEASUREMENT,      "mdi:counter"),                 # DpId 588
    ("alba_timestamp_last_descaling",                "Last Descaling",                    None, None,                          None,                              "mdi:calendar-clock"),          # DpId 590
    ("alba_timestamp_last_descaling_request",        "Last Descaling Request",            None, None,                          None,                              "mdi:calendar-clock"),          # DpId 591
    ("alba_rtc_time",                                "RTC Time",                          None, None,                          None,                              "mdi:clock-outline"),           # DpId 15
    ("alba_operation_time_total_s",                  "Operation Time Total",              "s",  SensorDeviceClass.DURATION,    SensorStateClass.TOTAL_INCREASING,  "mdi:timer"),                   # DpId 148
    ("alba_operation_time_since_power_up_s",         "Operation Time Since Power-Up",     "s",  SensorDeviceClass.DURATION,    SensorStateClass.MEASUREMENT,      "mdi:timer-outline"),           # DpId 149
    ("alba_product_registration_level",              "Product Registration Level",        None, None,                          None,                              "mdi:certificate-outline"),     # DpId 796 (enum)
    ("alba_active_intensity",                        "Active Spray Intensity",            None, None,                          SensorStateClass.MEASUREMENT,      "mdi:water-boiler"),            # r:DpId 571  w:DpId 570
    ("alba_active_position",                         "Active Spray Position",             None, None,                          SensorStateClass.MEASUREMENT,      "mdi:arrow-left-right"),        # r:DpId 573  w:DpId 572
    ("alba_active_temperature",                      "Active Water Temperature",          None, None,                          SensorStateClass.MEASUREMENT,      "mdi:thermometer-water"),       # r:DpId 575  w:DpId 574
    # Version strings from instanced DpIds 785–787
    ("alba_fus_version",                             "FUS Version",                       None, None,                          None,                              "mdi:chip"),                    # DpId 785 (3 instances)
    ("alba_geberit_loader_version",                  "Geberit Loader Version",            None, None,                          None,                              "mdi:chip"),                    # DpId 786 (2 instances)
    ("alba_wireless_stack_version",                  "Wireless Stack Version",            None, None,                          None,                              "mdi:chip"),                    # DpId 787 (3 instances)
    # Progress percentages (live during operation)
    ("alba_anal_shower_progress_pct",                "Anal Shower Progress",              "%",  None,                          SensorStateClass.MEASUREMENT,      "mdi:percent"),                 # DpId 565 (instanced, pct)
    ("alba_descaling_progress_pct",                  "Descaling Progress",                "%",  None,                          SensorStateClass.MEASUREMENT,      "mdi:percent"),                 # DpId 586 (instanced, pct)
    ("alba_spray_arm_cleaning_progress_pct",         "Spray Arm Cleaning Progress",       "%",  None,                          SensorStateClass.MEASUREMENT,      "mdi:percent"),                 # DpId 568 (instanced, pct)
    # Lifetime statistics counters from instanced DpId 689
    ("alba_stats_total_usages",                      "Total AquaClean Uses",              None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 689 inst=31
    ("alba_stats_total_anal_showers",                "Total Anal Shower Uses",            None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 689 inst=32
    ("alba_stats_total_lady_showers",                "Total Lady Shower Uses",            None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 689 inst=33
    ("alba_stats_total_dryings",                     "Total Dryer Uses",                  None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 689 inst=34
    ("alba_stats_total_descalings",                  "Total Descaling Cycles",            None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 689 inst=35
    ("alba_stats_total_spray_arm_cleanings",         "Total Spray Arm Cleanings",         None, None,                          SensorStateClass.TOTAL_INCREASING,  "mdi:counter"),                 # DpId 689 inst=36
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    feature_sets = get_feature_sets(coordinator._device_model)
    entities: list = [
        AquaCleanSensor(coordinator, entry, key, name, unit, device_class, state_class, icon, wired)
        for key, name, unit, device_class, state_class, icon, fs, wired in SENSORS
        if fs in feature_sets
    ]
    entities.append(AquaCleanBleConnectionSensor(coordinator, entry))

    # Alba-specific sensors — only created when device model supports Alba features
    if FS_ALBA_ONLY in feature_sets:
        entities += [
            AquaCleanAlbaSensor(coordinator, entry, key, name, unit, device_class, state_class, icon)
            for key, name, unit, device_class, state_class, icon in ALBA_SENSORS
        ]
        entities.append(AquaCleanAlbaDpIdCoverageSensor(coordinator, entry))

    # Performance/timing/BLE RSSI stats — always added regardless of transport.
    # Valid for both local BLE and ESP32 proxy modes; live on the main toilet device.
    entities.append(AquaCleanLastConnectSensor(coordinator, entry))
    entities.append(AquaCleanLastPollSensor(coordinator, entry))
    entities.append(AquaCleanAvgConnectSensor(coordinator, entry))
    entities.append(AquaCleanMinConnectSensor(coordinator, entry))
    entities.append(AquaCleanMaxConnectSensor(coordinator, entry))
    entities.append(AquaCleanAvgPollSensor(coordinator, entry))
    entities.append(AquaCleanMinPollSensor(coordinator, entry))
    entities.append(AquaCleanMaxPollSensor(coordinator, entry))
    entities.append(AquaCleanStatCountSensor(coordinator, entry))
    entities.append(AquaCleanTransportSensor(coordinator, entry))
    entities.append(AquaCleanAvgBleRssiSensor(coordinator, entry))
    entities.append(AquaCleanMinBleRssiSensor(coordinator, entry))
    entities.append(AquaCleanMaxBleRssiSensor(coordinator, entry))

    # ESP32 proxy-specific sensors — only when an ESPHome host is configured
    if coordinator._esphome_host:
        entities.append(AquaCleanEspHomeConnectionSensor(coordinator, entry))
        entities.append(AquaCleanProxyWifiSignalSensor(coordinator, entry))
        entities.append(AquaCleanProxyFreeHeapSensor(coordinator, entry))
        entities.append(AquaCleanProxyMaxFreeBlockSensor(coordinator, entry))
        entities.append(AquaCleanProxyAvgWifiRssiSensor(coordinator, entry))
        entities.append(AquaCleanProxyMinWifiRssiSensor(coordinator, entry))
        entities.append(AquaCleanProxyMaxWifiRssiSensor(coordinator, entry))
    async_add_entities(entities)


class AquaCleanSensor(AquaCleanEntity, SensorEntity):
    def __init__(
        self, coordinator, entry, key, name, unit, device_class, state_class, icon, wired: bool = True
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_icon = icon
        if not wired:
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._key)


class AquaCleanAlbaSensor(AquaCleanEntity, SensorEntity):
    """Sensor only available on AquaClean Alba devices."""

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
    def available(self) -> bool:
        return (self.coordinator.data or {}).get("device_type") == "alba" and super().available

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


# ──────────────────────────────────────────────────────────────────────────────
# Performance timing sensors — live on the main toilet device.
# Valid for both local BLE and ESP32 proxy transports; always added.
# ──────────────────────────────────────────────────────────────────────────────

class AquaCleanLastConnectSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the last BLE connect time in milliseconds."""

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


class AquaCleanLastPollSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the last GATT data fetch time in milliseconds."""

    _attr_name = "Last Poll ms"
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


class AquaCleanAvgConnectSensor(AquaCleanEntity, SensorEntity):
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


class AquaCleanMinConnectSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session minimum connect time in milliseconds."""

    _attr_name = "Min Connect"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_min_connect_ms"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("min_connect_ms")


class AquaCleanMaxConnectSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session maximum connect time in milliseconds."""

    _attr_name = "Max Connect"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_max_connect_ms"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("max_connect_ms")


class AquaCleanAvgPollSensor(AquaCleanEntity, SensorEntity):
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


class AquaCleanMinPollSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session minimum GATT fetch time in milliseconds."""

    _attr_name = "Min Poll"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_min_poll_ms"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("min_poll_ms")


class AquaCleanMaxPollSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session maximum GATT fetch time in milliseconds."""

    _attr_name = "Max Poll"
    _attr_native_unit_of_measurement = "ms"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:timer-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_max_poll_ms"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("max_poll_ms")


class AquaCleanStatCountSensor(AquaCleanEntity, SensorEntity):
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


class AquaCleanTransportSensor(AquaCleanEntity, SensorEntity):
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


# ──────────────────────────────────────────────────────────────────────────────
# BLE RSSI statistics — live on the main toilet device; always added.
# ──────────────────────────────────────────────────────────────────────────────

class AquaCleanAvgBleRssiSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session average BLE signal strength."""

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


class AquaCleanMinBleRssiSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session worst BLE signal strength."""

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


class AquaCleanMaxBleRssiSensor(AquaCleanEntity, SensorEntity):
    """Sensor showing the session best BLE signal strength."""

    _attr_name = "Max BLE RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:bluetooth"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_max_ble_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("max_ble_rssi")


# ──────────────────────────────────────────────────────────────────────────────
# WiFi RSSI statistics — ESP32 proxy only; live on the proxy device.
# ──────────────────────────────────────────────────────────────────────────────

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


class AquaCleanProxyMaxWifiRssiSensor(AquaCleanProxyEntity, SensorEntity):
    """Sensor showing the session best WiFi signal strength (ESP32 ↔ router)."""

    _attr_name = "Max WiFi RSSI"
    _attr_native_unit_of_measurement = "dBm"
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:wifi"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_max_wifi_rssi"

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get("max_wifi_rssi")


class AquaCleanAlbaDpIdCoverageSensor(AquaCleanEntity, SensorEntity):
    """Diagnostic sensor listing every DpId from the live inventory as wired or not.

    State: 'N / M wired' — updates after each poll as the inventory may grow
    with firmware updates.  Extra state attributes contain one entry per DpId.
    Disabled by default; visible in the Diagnostics download.
    """

    _attr_name = "DpId Coverage"
    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_alba_dpid_coverage"

    @property
    def native_value(self) -> str | None:
        inv = self.coordinator._alba_inventory
        if not inv:
            return None
        total = len(inv)
        wired = sum(1 for dpid in inv if dpid in ALBA_WIRED_DPIDS)
        return f"{wired} / {total} wired"

    @property
    def extra_state_attributes(self) -> dict:
        inv = self.coordinator._alba_inventory
        if not inv:
            return {}
        return {
            str(dpid): {
                "name": dp_name(dpid) or str(dpid),
                "wired": dpid in ALBA_WIRED_DPIDS,
            }
            for dpid in sorted(inv)
        }
