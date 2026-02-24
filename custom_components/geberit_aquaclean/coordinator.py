"""DataUpdateCoordinator — polls the AquaClean device on-demand over BLE."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_ESPHOME_HOST,
    CONF_ESPHOME_PORT,
    CONF_NOISE_PSK,
    CONF_POLL_INTERVAL,
    DEFAULT_ESPHOME_PORT,
    DEFAULT_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class AquaCleanCoordinator(DataUpdateCoordinator):
    """Polls the AquaClean device on a fixed interval using an on-demand BLE connection.

    Each update cycle:
      1. Opens a fresh BluetoothLeConnector (local BLE or ESPHome proxy)
      2. Calls connect_ble_only() — BLE handshake only, no eager data fetches
      3. Fetches identification, state, and descale statistics
      4. Disconnects

    Commands (toggle_lid, toggle_anal_shower, toggle_lady_shower) are executed via
    async_execute_command(), which follows the same connect/command/disconnect pattern.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # options (from options flow) take precedence over data (from initial config flow)
        conf = {**entry.data, **entry.options}
        self._device_id: str = conf[CONF_DEVICE_ID]
        self._esphome_host: str | None = conf.get(CONF_ESPHOME_HOST) or None
        self._esphome_port: int = conf.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT)
        self._noise_psk: str | None = conf.get(CONF_NOISE_PSK) or None
        poll_interval: int = conf.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_connector(self):
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            BluetoothLeConnector,
        )
        return BluetoothLeConnector(self._esphome_host, self._esphome_port, self._noise_psk)

    # ------------------------------------------------------------------
    # DataUpdateCoordinator protocol
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict:
        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import (
            AquaCleanClientFactory,
        )

        connector = self._make_connector()
        client = AquaCleanClientFactory(connector).create_client()
        try:
            await client.connect_ble_only(self._device_id)

            ident = await client.base_client.get_device_identification_async(0)
            initial_op_date = await client.base_client.get_device_initial_operation_date()
            state = await client.base_client.get_system_parameter_list_async(
                [0, 1, 2, 3, 4, 5, 7, 9]
            )
            stats = await client.base_client.get_statistics_descale_async()

            return {
                # Device identification
                "sap_number": ident.sap_number,
                "serial_number": ident.serial_number,
                "production_date": ident.production_date,
                "description": ident.description,
                "initial_operation_date": initial_op_date,
                # Live state
                "is_user_sitting": state.data_array[0] != 0,
                "is_anal_shower_running": state.data_array[1] != 0,
                "is_lady_shower_running": state.data_array[2] != 0,
                "is_dryer_running": state.data_array[3] != 0,
                # Descale statistics
                "days_until_next_descale": stats.days_until_next_descale,
                "days_until_shower_restricted": stats.days_until_shower_restricted,
                "shower_cycles_until_confirmation": stats.shower_cycles_until_confirmation,
                "number_of_descale_cycles": stats.number_of_descale_cycles,
                "date_time_at_last_descale": stats.date_time_at_last_descale,
                "unposted_shower_cycles": stats.unposted_shower_cycles,
            }
        except Exception as exc:
            raise UpdateFailed(f"AquaClean update failed: {exc}") from exc
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Command execution (buttons)
    # ------------------------------------------------------------------

    async def async_execute_command(self, command: str) -> None:
        """Execute a device command via a fresh on-demand BLE connection."""
        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import (
            AquaCleanClientFactory,
        )

        connector = self._make_connector()
        client = AquaCleanClientFactory(connector).create_client()
        try:
            await client.connect_ble_only(self._device_id)
            if command == "toggle_lid":
                await client.toggle_lid_position()
            elif command == "toggle_anal_shower":
                await client.toggle_anal_shower()
            elif command == "toggle_lady_shower":
                await client.toggle_lady_shower()
            else:
                _LOGGER.warning("Unknown command: %s", command)
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass
