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
]

# Commands that only work while a user is seated — entity becomes unavailable otherwise.
_SITTING_REQUIRED = {"toggle_anal_shower", "toggle_lady_shower", "toggle_dryer"}


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
        if self._command in _SITTING_REQUIRED:
            data = self.coordinator.data or {}
            if not data.get("is_user_sitting"):
                return False
        return super().available

    async def async_press(self) -> None:
        await self.coordinator.async_execute_command(self._command)


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
