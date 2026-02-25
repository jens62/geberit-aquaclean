"""Config flow and options flow for Geberit AquaClean."""
from __future__ import annotations

import logging

import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from homeassistant.loader import async_get_integration

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


def _build_schema(defaults: dict) -> vol.Schema:
    """Build the shared schema for both config and options flow, pre-filled with defaults."""
    return vol.Schema(
        {
            vol.Required(CONF_DEVICE_ID, default=defaults.get(CONF_DEVICE_ID, "")): cv.string,
            vol.Optional(CONF_ESPHOME_HOST, default=defaults.get(CONF_ESPHOME_HOST) or ""): cv.string,
            vol.Optional(CONF_ESPHOME_PORT, default=defaults.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT)): cv.port,
            vol.Optional(CONF_NOISE_PSK, default=defaults.get(CONF_NOISE_PSK) or ""): cv.string,
            vol.Optional(CONF_POLL_INTERVAL, default=defaults.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)): vol.All(
                int, vol.Range(min=5, max=3600)
            ),
        }
    )


async def _has_local_bluetooth() -> bool:
    """Return True if a local Bluetooth adapter is accessible."""
    try:
        from bleak import BleakScanner
        scanner = BleakScanner()
        await scanner.start()
        await scanner.stop()
        return True
    except Exception:
        return False


async def _test_connection(
    device_id: str,
    esphome_host: str | None,
    esphome_port: int,
    noise_psk: str | None,
) -> None:
    """Attempt a BLE connect to validate the supplied settings."""
    _LOGGER.info(
        "[AquaClean] Config flow: testing connection — device=%s esphome_host=%s port=%s",
        device_id, esphome_host, esphome_port,
    )
    from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
    from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory

    connector = BluetoothLeConnector(esphome_host, esphome_port, noise_psk)
    client = AquaCleanClientFactory(connector).create_client()
    try:
        await client.connect_ble_only(device_id)
        _LOGGER.info("[AquaClean] Config flow: connection test succeeded")
    finally:
        try:
            await connector.disconnect()
        except Exception:
            pass


def _normalise(user_input: dict) -> dict:
    """Strip whitespace and convert empty strings to None for optional fields."""
    return {
        **user_input,
        CONF_DEVICE_ID: user_input[CONF_DEVICE_ID].strip().upper(),
        CONF_ESPHOME_HOST: user_input.get(CONF_ESPHOME_HOST, "").strip() or None,
        CONF_NOISE_PSK: user_input.get(CONF_NOISE_PSK, "").strip() or None,
    }


class AquaCleanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Initial setup flow — runs once when the integration is first added."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> AquaCleanOptionsFlow:
        return AquaCleanOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        integration = await async_get_integration(self.hass, DOMAIN)
        version = integration.manifest.get("version", "unknown")

        if user_input is not None:
            data = _normalise(user_input)
            if not data[CONF_ESPHOME_HOST] and not await _has_local_bluetooth():
                errors["base"] = "no_bluetooth"
            else:
                try:
                    await _test_connection(
                        data[CONF_DEVICE_ID],
                        data[CONF_ESPHOME_HOST],
                        data.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT),
                        data[CONF_NOISE_PSK],
                    )
                except Exception:
                    _LOGGER.exception("[AquaClean] Config flow: connection test failed")
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(data[CONF_DEVICE_ID])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"AquaClean {data[CONF_DEVICE_ID]}",
                        data=data,
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=_build_schema(user_input or {}),
            errors=errors,
            description_placeholders={"version": version},
        )


class AquaCleanOptionsFlow(config_entries.OptionsFlow):
    """Options flow — accessible via the Configure button after initial setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        # Current effective values: options override data
        current = {**self._config_entry.data, **self._config_entry.options}

        integration = await async_get_integration(self.hass, DOMAIN)
        version = integration.manifest.get("version", "unknown")

        if user_input is not None:
            data = _normalise(user_input)
            if not data[CONF_ESPHOME_HOST] and not await _has_local_bluetooth():
                errors["base"] = "no_bluetooth"
            else:
                try:
                    await _test_connection(
                        data[CONF_DEVICE_ID],
                        data[CONF_ESPHOME_HOST],
                        data.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT),
                        data[CONF_NOISE_PSK],
                    )
                except Exception:
                    _LOGGER.exception("[AquaClean] Options flow: connection test failed")
                    errors["base"] = "cannot_connect"
                else:
                    return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(current),
            errors=errors,
            description_placeholders={"version": version},
        )
