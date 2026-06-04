"""Buttons — device commands."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity, AquaCleanProxyEntity

# (command, friendly_name, icon)
BUTTONS: list[tuple[str, str, str]] = [
    ("toggle_lid",                    "Toggle Lid",                    "geberit:lid"),
    ("toggle_anal_shower",            "Toggle Anal Shower",            "geberit:analshower"),
    ("toggle_lady_shower",            "Toggle Lady Shower",            "geberit:ladywash"),
    ("toggle_dryer",                  "Toggle Dryer",                  "mdi:hair-dryer"),
    ("toggle_orientation_light",      "Toggle Orientation Light",      "mdi:lightbulb-outline"),
    ("orientation_light_off",         "Orientation Light Off",         "mdi:lightbulb-off-outline"),
    ("orientation_light_on",          "Orientation Light On",          "mdi:lightbulb-on-outline"),
    ("orientation_light_when_approached", "Orientation Light When Approached", "mdi:motion-sensor"),
    ("toggle_odour_extraction",       "Toggle Odour Extraction",       "mdi:air-purifier"),
    ("odour_extraction_run_on",       "Odour Extraction Run-On",       "mdi:air-filter"),
    ("trigger_flush_manually",        "Trigger Flush Manually",        "mdi:toilet"),
    ("prepare_descaling",             "Prepare Descaling",             "mdi:chemical-weapon"),
    ("confirm_descaling",             "Confirm Descaling",             "mdi:check-circle-outline"),
    ("cancel_descaling",              "Cancel Descaling",              "mdi:close-circle-outline"),
    ("postpone_descaling",            "Postpone Descaling",            "mdi:clock-outline"),
    ("start_cleaning_device",         "Start Cleaning Device",         "mdi:spray-bottle"),
    ("execute_next_cleaning_step",    "Execute Next Cleaning Step",    "mdi:skip-next-circle-outline"),
    ("start_lid_position_calibration","Start Lid Position Calibration","mdi:tune"),
    ("lid_position_offset_save",      "Lid Position Offset Save",      "mdi:content-save-outline"),
    ("lid_position_offset_increment", "Lid Position Offset Increment", "mdi:plus-circle-outline"),
    ("lid_position_offset_decrement", "Lid Position Offset Decrement", "mdi:minus-circle-outline"),
    ("reset_filter_counter",          "Reset Filter Counter",          "mdi:air-purifier"),
    ("sync_rtc",                      "Sync RTC",                      "mdi:clock-check-outline"),
    ("restart_alba_device",           "Restart Alba Device",           "mdi:restart"),
]

# Commands that only work while a user is seated — entity becomes unavailable otherwise.
_SITTING_REQUIRED = {"toggle_anal_shower", "toggle_lady_shower", "toggle_dryer"}

# Commands not available on AquaClean Alba — entity becomes unavailable when device_type == "alba".
_MERA_ONLY = {
    "toggle_lid",           # DpIds 1008/1009 absent from Alba inventory — no motorized lid
    "toggle_lady_shower",   # DpIds 868/872 absent from Alba inventory — no lady shower arm
    "toggle_dryer",
    "toggle_orientation_light",
    "orientation_light_off",
    "orientation_light_on",
    "orientation_light_when_approached",
    "toggle_odour_extraction",
    "odour_extraction_run_on",
    "trigger_flush_manually",
    "start_cleaning_device",
    "execute_next_cleaning_step",
    "start_lid_position_calibration",
    "lid_position_offset_save",
    "lid_position_offset_increment",
    "lid_position_offset_decrement",
    "reset_filter_counter",
}

# Commands only available on AquaClean Alba — entity becomes unavailable when device_type != "alba".
_ALBA_ONLY = {"sync_rtc", "restart_alba_device"}

# Alba-specific commands that take a value parameter: (command, value, friendly_name, icon)
ALBA_COMMAND_BUTTONS: list[tuple[str, int, str, str]] = [
    ("start_stop_spray_arm_cleaning", 1, "Start Spray Arm Cleaning", "mdi:spray-bottle"),
    ("start_stop_spray_arm_cleaning", 0, "Stop Spray Arm Cleaning",  "mdi:spray-bottle-off"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list = [
        AquaCleanButton(coordinator, entry, command, name, icon)
        for command, name, icon in BUTTONS
    ]
    # Alba-specific command buttons (require a value parameter)
    entities += [
        AlbaCommandButton(coordinator, entry, command, value, name, icon)
        for command, value, name, icon in ALBA_COMMAND_BUTTONS
    ]
    if coordinator._esphome_host:
        entities.append(Esp32RestartButton(coordinator, entry))
    async_add_entities(entities)


class AquaCleanButton(AquaCleanEntity, ButtonEntity):
    def __init__(self, coordinator, entry, command, name, icon) -> None:
        super().__init__(coordinator, entry)
        self._command = command
        self._attr_unique_id = f"{entry.entry_id}_{command}"
        self._attr_name = name
        self._attr_icon = icon

    @property
    def available(self) -> bool:
        data = self.coordinator.data or {}
        device_type = data.get("device_type")
        if self._command in _MERA_ONLY and device_type == "alba":
            return False
        if self._command in _ALBA_ONLY and device_type != "alba":
            return False
        if self._command in _SITTING_REQUIRED and not data.get("is_user_sitting"):
            return False
        return super().available

    async def async_press(self) -> None:
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
