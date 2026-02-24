"""Base entity class shared by all AquaClean platforms."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, DOMAIN
from .coordinator import AquaCleanCoordinator


class AquaCleanEntity(CoordinatorEntity[AquaCleanCoordinator]):
    """Base class that provides the shared DeviceInfo for all AquaClean entities."""

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_DEVICE_ID])},
            name=data.get("description") or "Geberit AquaClean",
            manufacturer="Geberit",
            model=data.get("description"),
            serial_number=data.get("serial_number"),
            hw_version=data.get("sap_number"),
        )
