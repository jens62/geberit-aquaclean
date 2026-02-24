"""Config flow for Geberit AquaClean."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

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

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): cv.string,
        vol.Optional(CONF_ESPHOME_HOST, default=""): cv.string,
        vol.Optional(CONF_ESPHOME_PORT, default=DEFAULT_ESPHOME_PORT): cv.port,
        vol.Optional(CONF_NOISE_PSK, default=""): cv.string,
        vol.Optional(CONF_POLL_INTERVAL, default=DEFAULT_POLL_INTERVAL): vol.All(
            int, vol.Range(min=5, max=3600)
        ),
    }
)


class AquaCleanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the user-initiated config flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID].strip().upper()
            esphome_host = user_input.get(CONF_ESPHOME_HOST, "").strip() or None
            esphome_port = user_input.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT)
            noise_psk = user_input.get(CONF_NOISE_PSK, "").strip() or None

            try:
                await self._test_connection(device_id, esphome_host, esphome_port, noise_psk)
            except Exception:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"AquaClean {device_id}",
                    data={
                        **user_input,
                        CONF_DEVICE_ID: device_id,
                        CONF_ESPHOME_HOST: esphome_host,
                        CONF_NOISE_PSK: noise_psk,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _test_connection(
        self,
        device_id: str,
        esphome_host: str | None,
        esphome_port: int,
        noise_psk: str | None,
    ) -> None:
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            BluetoothLeConnector,
        )
        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import (
            AquaCleanClientFactory,
        )

        connector = BluetoothLeConnector(esphome_host, esphome_port, noise_psk)
        client = AquaCleanClientFactory(connector).create_client()
        try:
            await client.connect_ble_only(device_id)
        finally:
            try:
                await connector.disconnect()
            except Exception:
                pass
