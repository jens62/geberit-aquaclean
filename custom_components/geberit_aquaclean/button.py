"""Buttons — device commands."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    get_feature_sets, FS_ALL, FS_AQUACLEAN_OLD,
    FS_WITH_LADY_SHOWER, FS_WITH_DRYER, FS_WITH_ODOUR_EXTRACTION,
    FS_MERA_COMFORT_ONLY, FS_SELA_ONLY, FS_ALBA_ONLY,
)
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity, AquaCleanProxyEntity

# (command, friendly_name, icon, feature_set, wired)
# wired=False: entity disabled by default — feature exists on device but not yet exposed by bridge.
BUTTONS: list[tuple] = [
    # Present on all models including Alba
    ("toggle_anal_shower",            "Toggle Anal Shower",            "geberit:analshower",             FS_ALL,                   True),
    # AquacleanOld protocol models only (proc 0x09 SetCommand)
    ("toggle_lid",                    "Toggle Lid",                    "geberit:lid",                    FS_AQUACLEAN_OLD,         True),
    ("stop",                          "Stop",                          "mdi:stop-circle-outline",        FS_AQUACLEAN_OLD,         True),
    ("trigger_flush_manually",        "Trigger Flush Manually",        "geberit:flush",                  FS_AQUACLEAN_OLD,         True),
    ("prepare_descaling",             "Prepare Descaling",             "mdi:chemical-weapon",            FS_AQUACLEAN_OLD,         True),
    ("confirm_descaling",             "Confirm Descaling",             "mdi:check-circle-outline",       FS_AQUACLEAN_OLD,         True),
    ("cancel_descaling",              "Cancel Descaling",              "mdi:close-circle-outline",       FS_AQUACLEAN_OLD,         True),
    ("postpone_descaling",            "Postpone Descaling",            "mdi:clock-outline",              FS_AQUACLEAN_OLD,         True),
    ("start_cleaning_device",         "Start Cleaning Device",         "mdi:spray-bottle",               FS_AQUACLEAN_OLD,         True),
    ("execute_next_cleaning_step",    "Execute Next Cleaning Step",    "mdi:skip-next-circle-outline",   FS_AQUACLEAN_OLD,         True),
    ("reset_filter_counter",          "Reset Filter Counter",          "mdi:air-purifier",               FS_AQUACLEAN_OLD,         True),
    # Models with lady shower
    ("toggle_lady_shower",            "Toggle Lady Shower",            "geberit:ladywash",               FS_WITH_LADY_SHOWER,      True),
    # Models with air dryer
    ("toggle_dryer",                  "Toggle Dryer",                  "mdi:hair-dryer",                 FS_WITH_DRYER,            True),
    # Models with odour extraction
    ("toggle_odour_extraction",       "Toggle Odour Extraction",       "geberit:odourextraction",        FS_WITH_ODOUR_EXTRACTION, True),
    ("odour_extraction_run_on",       "Odour Extraction Run-On",       "geberit:odourextraction",        FS_WITH_ODOUR_EXTRACTION, True),
    # Mera Comfort only — orientation light via proc 0x0B; lid calibration (motorized)
    ("orientation_light_off",         "Orientation Light Off",         "geberit:light",                  FS_MERA_COMFORT_ONLY,     True),
    ("orientation_light_on",          "Orientation Light On",          "geberit:light",                  FS_MERA_COMFORT_ONLY,     True),
    ("orientation_light_when_approached", "Orientation Light When Approached", "geberit:light",          FS_MERA_COMFORT_ONLY,     True),
    ("start_lid_position_calibration","Start Lid Position Calibration","mdi:tune",                       FS_MERA_COMFORT_ONLY,     True),
    ("lid_position_offset_save",      "Lid Position Offset Save",      "mdi:content-save-outline",       FS_MERA_COMFORT_ONLY,     True),
    ("lid_position_offset_increment", "Lid Position Offset Increment", "mdi:plus-circle-outline",        FS_MERA_COMFORT_ONLY,     True),
    ("lid_position_offset_decrement", "Lid Position Offset Decrement", "mdi:minus-circle-outline",       FS_MERA_COMFORT_ONLY,     True),
    # Sela only — SetCommand 20 confirmed AcSela only
    ("toggle_orientation_light",      "Toggle Orientation Light",      "geberit:light",                  FS_SELA_ONLY,             True),
    # Alba only
    ("sync_rtc",                      "Sync RTC",                      "mdi:clock-check-outline",        FS_ALBA_ONLY,             True),  # DpId 270 (write-only)
    ("restart_alba_device",           "Restart Alba Device",           "mdi:restart",                    FS_ALBA_ONLY,             True),  # DpId 153 (write-only)
]

# Commands that only work while a user is seated — entity becomes unavailable otherwise.
_SITTING_REQUIRED = {"toggle_anal_shower", "toggle_lady_shower", "toggle_dryer"}

# Fallback guards for the unknown-model case (model not detected from BLE advertisement).
# When model IS known, these entities are never created for incompatible models.
_ALBA_INCOMPATIBLE = {
    cmd for cmd, _name, _icon, fs, _wired in BUTTONS
    if fs != FS_ALL and fs != FS_ALBA_ONLY
}
_ALBA_ONLY_CMDS = {cmd for cmd, _name, _icon, fs, _wired in BUTTONS if fs == FS_ALBA_ONLY}

# Alba-specific commands that take a value parameter: (command, value, friendly_name, icon)
# Trailing "# DpId N" comments are machine-readable: run tools/generate-hacs-entity-docs.py after any change.
ALBA_COMMAND_BUTTONS: list[tuple[str, int, str, str]] = [
    ("start_stop_spray_arm_cleaning", 1, "Start Spray Arm Cleaning", "mdi:spray-bottle"),     # DpId 566 (write-only)
    ("start_stop_spray_arm_cleaning", 0, "Stop Spray Arm Cleaning",  "mdi:spray-bottle-off"), # DpId 566 (write-only)
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    feature_sets = get_feature_sets(coordinator._device_model)
    entities: list = [
        AquaCleanButton(coordinator, entry, command, name, icon, wired)
        for command, name, icon, fs, wired in BUTTONS
        if fs in feature_sets
    ]
    # Alba-specific command buttons (require a value parameter)
    if FS_ALBA_ONLY in feature_sets:
        entities += [
            AlbaCommandButton(coordinator, entry, command, value, name, icon)
            for command, value, name, icon in ALBA_COMMAND_BUTTONS
        ]
    if coordinator._esphome_host:
        entities.append(Esp32RestartButton(coordinator, entry))
    async_add_entities(entities)


class AquaCleanButton(AquaCleanEntity, ButtonEntity):
    def __init__(self, coordinator, entry, command, name, icon, wired: bool = True) -> None:
        super().__init__(coordinator, entry)
        self._command = command
        self._wired = wired
        self._attr_unique_id = f"{entry.entry_id}_{command}"
        self._attr_name = name
        self._attr_icon = icon
        if not wired:
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        device_type = data.get("device_type")
        # Fallback guards when model is unknown (entities created for all models)
        if self._command in _ALBA_INCOMPATIBLE and device_type == "alba":
            return False
        if self._command in _ALBA_ONLY_CMDS and device_type != "alba":
            return False
        if self._command in _SITTING_REQUIRED and not data.get("is_user_sitting"):
            return False
        return super().available

    async def async_press(self) -> None:
        if not self._wired:
            raise HomeAssistantError("Not yet implemented in bridge")
        await self.coordinator.async_execute_command(self._command)


class AlbaCommandButton(AquaCleanEntity, ButtonEntity):
    """Button for Alba-specific commands that require an integer value parameter."""

    def __init__(self, coordinator, entry, command: str, value: int, name: str, icon: str) -> None:
        super().__init__(coordinator, entry)
        self._command = command
        self._value = value
        self._attr_unique_id = f"{entry.entry_id}_alba_{command}_{value}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        return (self.coordinator.data or {}).get("device_type") == "alba" and super().available

    async def async_press(self) -> None:
        await self.coordinator.async_execute_alba_command(self._command, self._value)


class Esp32RestartButton(AquaCleanProxyEntity, ButtonEntity):
    """Button to soft-reboot the ESPHome BLE proxy."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_esp32_restart"
        self._attr_name = "Restart AquaClean Proxy"
        self._attr_icon = "mdi:restart"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self.coordinator.async_restart_esp32()
