"""Base entity classes shared by all AquaClean platforms."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, DOMAIN
from .coordinator import AquaCleanCoordinator


class AquaCleanEntity(CoordinatorEntity[AquaCleanCoordinator]):
    """Base class for entities belonging to the Geberit AquaClean toilet device.

    _attr_has_entity_name = True tells HA to prefix entity IDs with a slugified
    version of the device name.  The device name is fixed as "Geberit AquaClean"
    so entity IDs are stable and predictable:
        binary_sensor.geberit_aquaclean_user_sitting
        sensor.geberit_aquaclean_days_until_next_descale
        button.geberit_aquaclean_toggle_lid
        …

    The actual product model (e.g. "AquaClean Mera Comfort") is stored in the
    device registry under `model`, visible in the device detail page.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        data = self.coordinator.data or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._entry.data[CONF_DEVICE_ID])},
            name="Geberit AquaClean",        # stable — drives entity ID prefix
            manufacturer="Geberit",
            model=data.get("description"),   # e.g. "AquaClean Mera Comfort"
            serial_number=data.get("serial_number"),
            hw_version=data.get("sap_number"),
        )


class AquaCleanProxyEntity(CoordinatorEntity[AquaCleanCoordinator]):
    """Base class for entities belonging to the ESPHome BLE proxy device.

    Represents the ESP32 running ESPHome as a separate HA device, linked to
    the main toilet device via via_device.  Only instantiated when an ESPHome
    host is configured.

    Entity IDs will be prefixed with the proxy device name:
        button.aquaclean_proxy_restart_aquaclean_proxy
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: AquaCleanCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_proxy")},
            name="AquaClean Proxy",
            manufacturer="Espressif",
            model="ESP32 (ESPHome Bluetooth Proxy)",
            via_device=(DOMAIN, self._entry.data[CONF_DEVICE_ID]),
        )
