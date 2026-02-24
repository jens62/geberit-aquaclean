"""Buttons — device commands."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import AquaCleanCoordinator
from .entity import AquaCleanEntity

# (command, friendly_name, icon)
BUTTONS: list[tuple[str, str, str]] = [
    ("toggle_lid",           "Toggle Lid",           "mdi:toilet"),
    ("toggle_anal_shower",   "Toggle Anal Shower",   "mdi:shower"),
    ("toggle_lady_shower",   "Toggle Lady Shower",   "mdi:shower"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: AquaCleanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AquaCleanButton(coordinator, entry, command, name, icon)
        for command, name, icon in BUTTONS
    )


class AquaCleanButton(AquaCleanEntity, ButtonEntity):
    def __init__(self, coordinator, entry, command, name, icon) -> None:
        super().__init__(coordinator, entry)
        self._command = command
        self._attr_unique_id = f"{entry.entry_id}_{command}"
        self._attr_name = name
        self._attr_icon = icon

    async def async_press(self) -> None:
        await self.coordinator.async_execute_command(self._command)
