"""Binary sensors — live device state and connection status."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    get_feature_sets, FS_ALL, FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_ALBA_ONLY,
)
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity, AquaCleanProxyEntity

# (data_key, friendly_name, device_class, icon_on, icon_off, feature_set)
BINARY_SENSORS: list[tuple] = [
    ("is_user_sitting",           "User Sitting",              BinarySensorDeviceClass.OCCUPANCY, "geberit:is_user_sitting-on", "geberit:is_user_sitting-off", FS_ALL),
    ("is_anal_shower_running",    "Anal Shower Running",       None,                              "geberit:analshower",         "geberit:analshower",          FS_ALL),
    ("firmware_update_available", "Firmware Update Available", BinarySensorDeviceClass.UPDATE,    "mdi:update",                 "mdi:check-circle",            FS_ALL),
    ("is_lady_shower_running",    "Lady Shower Running",       None,                              "geberit:ladywash",           "geberit:ladywash",            FS_WITH_LADY_SHOWER),
    ("is_dryer_running",          "Dryer Running",             None,                              "geberit:dryer-on",           "geberit:dryer-off",           FS_WITH_DRYER),
]

# (data_key, friendly_name, device_class, icon)
# Binary sensors only available on AquaClean Alba devices.
# Trailing "# DpId N" comments are machine-readable: run tools/generate-hacs-entity-docs.py after any change.
ALBA_BINARY_SENSORS: list[tuple] = [
    # Error indicators — True means the fault is present
    ("alba_error_power_supply",        "Power Supply Error",    BinarySensorDeviceClass.PROBLEM, "mdi:power-plug-off"),    # DpId 93
    ("alba_error_water_heater",        "Water Heater Error",    BinarySensorDeviceClass.PROBLEM, "mdi:water-boiler-off"),  # DpId 764
    ("alba_error_level_control",       "Level Control Error",   BinarySensorDeviceClass.PROBLEM, "mdi:gauge-low"),         # DpId 765
    ("alba_error_user_detection",      "User Detection Error",  BinarySensorDeviceClass.PROBLEM, "mdi:account-alert"),     # DpId 766
    ("alba_error_water_pump",          "Water Pump Error",      BinarySensorDeviceClass.PROBLEM, "mdi:water-pump-off"),    # DpId 789
    ("alba_error_spray_arm_drive",     "Spray Arm Drive Error", BinarySensorDeviceClass.PROBLEM, "mdi:cog-off"),           # DpId 790
    ("alba_error_maintenance_request", "Maintenance Request",   BinarySensorDeviceClass.PROBLEM, "mdi:wrench-clock"),      # DpId 820
    ("alba_error_descaling",           "Descaling Error",       BinarySensorDeviceClass.PROBLEM, "mdi:water-remove"),      # DpId 982
    # Operating modes
    ("alba_demo_mode",                 "Demo Mode",             None,                            "mdi:monitor-eye"),       # DpId 795
    ("alba_showroom_mode",             "Showroom Mode",         None,                            "mdi:store-outline"),     # DpId 803
    ("alba_dry_run_mode",              "Dry Run Mode",          None,                            "mdi:water-off-outline"), # DpId 810
    # Active oscillation (also writable as number entity)
    ("alba_active_oscillation",        "Spray Arm Oscillation", None,                            "mdi:rotate-360"),        # r:DpId 577  w:DpId 576
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    feature_sets = get_feature_sets(coordinator._device_model)
    entities: list = [
        AquaCleanBinarySensor(coordinator, entry, key, name, device_class, icon_on, icon_off, fs)
        for key, name, device_class, icon_on, icon_off, fs in BINARY_SENSORS
        if fs in feature_sets
    ]
    entities.append(AquaCleanBleConnectedSensor(coordinator, entry))
    if coordinator._esphome_host:
        entities.append(AquaCleanEspHomeConnectedSensor(coordinator, entry))
    # Alba-specific binary sensors — only created when device model supports Alba features
    if FS_ALBA_ONLY in feature_sets:
        entities += [
            AquaCleanAlbaBinarySensor(coordinator, entry, key, name, device_class, icon)
            for key, name, device_class, icon in ALBA_BINARY_SENSORS
        ]
    async_add_entities(entities)


class AquaCleanBinarySensor(AquaCleanEntity, BinarySensorEntity):
    def __init__(
        self, coordinator, entry, key, name, device_class, icon_on, icon_off, feature_set: str = FS_ALL
    ) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._icon_on = icon_on
        self._icon_off = icon_off
        # Fallback guard: when model is unknown (all entities created), treat non-FS_ALL
        # entities as unavailable for Alba so state doesn't show as Unknown/True.
        self._mera_only = feature_set != FS_ALL

    @property
    def available(self) -> bool:
        if self._mera_only and (self.coordinator.data or {}).get("device_type") == "alba":
            return False
        return super().available

    @property
    def icon(self) -> str:
        return self._icon_on if self.is_on else self._icon_off

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.get(self._key))


class AquaCleanAlbaBinarySensor(AquaCleanEntity, BinarySensorEntity):
    """Binary sensor only available on AquaClean Alba devices."""

    def __init__(self, coordinator, entry, key, name, device_class, icon) -> None:
        super().__init__(coordinator, entry)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return (self.coordinator.data or {}).get("device_type") == "alba" and super().available

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        val = self.coordinator.data.get(self._key)
        return bool(val) if val is not None else None


class AquaCleanBleConnectedSensor(AquaCleanEntity, BinarySensorEntity):
    """Binary sensor: True when the last poll successfully reached the Geberit via BLE."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "BLE Connected"

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_ble_connected"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator._ble_connected_sensor = self

    async def async_will_remove_from_hass(self) -> None:
        self.coordinator._ble_connected_sensor = None

    @property
    def available(self) -> bool:
        return True  # always show Connected/Disconnected, never Unavailable

    @property
    def icon(self) -> str:
        return "mdi:bluetooth-connect" if self.is_on else "mdi:bluetooth-off"

    @property
    def is_on(self) -> bool:
        return self.coordinator.ble_state == "connected"

    @property
    def extra_state_attributes(self) -> dict:
        connected_at = self.coordinator.ble_connected_at
        attrs: dict = {"connected_at": connected_at.isoformat() if connected_at else None}
        if self.coordinator.last_error_code:
            attrs["error_code"] = self.coordinator.last_error_code
            attrs["error_hint"] = self.coordinator.last_error_hint
        return attrs


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

    @property
    def extra_state_attributes(self) -> dict:
        attrs: dict = {}
        if self.coordinator.last_error_code:
            attrs["error_code"] = self.coordinator.last_error_code
            attrs["error_hint"] = self.coordinator.last_error_hint
        return attrs
