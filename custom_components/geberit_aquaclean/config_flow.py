"""Config flow and options flow for Geberit AquaClean."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_ESPHOME_HOST,
    CONF_ESPHOME_PORT,
    CONF_NOISE_PSK,
    CONF_POLL_INTERVAL,
    CONF_USE_HA_BLUETOOTH,
    DEFAULT_ESPHOME_PORT,
    DEFAULT_POLL_INTERVAL,
)


# Known AquaClean Alba (Variant A / Ble20) GATT characteristic UUIDs.
# If a non-standard GATT profile has these UUIDs but the Arendi handshake failed
# (e.g. due to a stale NimBLE NVS cache on the ESP32), the device is still a
# supported Alba — report "cannot_connect" so the user can retry after clearing
# the ESP32 BT cache, rather than permanently flagging it as unsupported.
_ALBA_WRITE_UUID  = "559eb001-2390-11e8-b467-0ed5f89f718b"
_ALBA_NOTIFY_UUID = "559eb002-2390-11e8-b467-0ed5f89f718b"


def _is_known_alba_profile(profile) -> bool:
    """Return True when the GATT profile matches the known Alba Variant A UUIDs."""
    return (
        _ALBA_WRITE_UUID  in (profile.write_uuids  or [])
        and _ALBA_NOTIFY_UUID in (profile.notify_uuids or [])
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
            vol.Optional(CONF_USE_HA_BLUETOOTH, default=defaults.get(CONF_USE_HA_BLUETOOTH, False)): cv.boolean,
        }
    )


async def _has_local_bluetooth() -> bool:
    """Return True if a local Bluetooth adapter is accessible within a short timeout."""
    try:
        from bleak import BleakScanner
        scanner = BleakScanner()
        await asyncio.wait_for(scanner.start(), timeout=5.0)
        await scanner.stop()
        return True
    except Exception:
        return False


async def _test_connection(
    device_id: str,
    esphome_host: str | None,
    esphome_port: int,
    noise_psk: str | None,
    hass=None,
):
    """Attempt a BLE connect, probe GATT profile, and return the result.

    Returns a GattProfile on success.  Raises on connection failure.
    The GATT profile is used to detect unsupported device variants (e.g. Alba)
    so they receive a clear error with UUID details instead of "Cannot connect".
    """
    _LOGGER.info(
        "[AquaClean] Config flow: testing connection — device=%s esphome_host=%s port=%s",
        device_id, esphome_host, esphome_port,
    )
    from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
    from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory

    connector = BluetoothLeConnector(esphome_host, esphome_port, noise_psk, hass=hass)
    client = AquaCleanClientFactory(connector).create_client()
    try:
        await client.connect_ble_only(device_id)
        _LOGGER.info("[AquaClean] Config flow: connection test succeeded")
        profile = connector.get_gatt_profile()
        profile.dis_info = connector.ble_dis_info
        profile.arendi_handshake_done = connector.arendi_handshake_done
        _LOGGER.info(
            "[AquaClean] Config flow: GATT profile — is_standard=%s arendi_ok=%s svc_uuid=%s dis=%s",
            profile.is_standard, profile.arendi_handshake_done, profile.svc_uuid, profile.dis_info,
        )
        return profile
    except Exception:
        # connect_ble_only() may have partially succeeded: BLE connected but
        # subscribe_notifications_async() failed because the device uses a
        # non-standard GATT profile (e.g. AquaClean Alba).  In that case
        # connector.client is still set and we can read the GATT services to
        # return a useful unsupported_device abort instead of cannot_connect.
        profile = connector.get_gatt_profile()
        profile.dis_info = connector.ble_dis_info
        if not profile.is_standard:
            _LOGGER.info(
                "[AquaClean] Config flow: BLE connected but non-standard GATT profile "
                "detected after init failure — svc=%s write=%s notify=%s dis=%s",
                profile.svc_uuid, profile.write_uuids, profile.notify_uuids, profile.dis_info,
            )
            return profile
        raise
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
            use_ha_bt = data.get(CONF_USE_HA_BLUETOOTH, False)
            test_esphome_host = None if use_ha_bt else data[CONF_ESPHOME_HOST]
            if not test_esphome_host and not use_ha_bt and not await _has_local_bluetooth():
                errors["base"] = "no_bluetooth"
            else:
                # Both paths perform a live BLE connection test.
                # Local BLE / Option B: BluetoothLeConnector uses HA's bluetooth stack
                #   (habluetooth + bleak_retry_connector) via the hass= parameter —
                #   no raw BleakScanner conflict, no event loop hang.
                # ESPHome (Option A): connects over TCP as before.
                hass = self.hass if (not data[CONF_ESPHOME_HOST] or use_ha_bt) else None
                try:
                    profile = await _test_connection(
                        data[CONF_DEVICE_ID],
                        test_esphome_host,
                        data.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT),
                        data[CONF_NOISE_PSK],
                        hass=hass,
                    )
                except Exception:
                    _LOGGER.exception("[AquaClean] Config flow: connection test failed")
                    errors["base"] = "cannot_connect"
                else:
                    if profile is not None and not profile.is_standard and not profile.arendi_handshake_done:
                        if _is_known_alba_profile(profile):
                            _LOGGER.warning(
                                "[AquaClean] Config flow: known Alba GATT profile but handshake "
                                "failed (svc=%s) — ESP32 NimBLE cache may be stale; "
                                "press 'Clear Bluetooth Cache' on the proxy and retry",
                                profile.svc_uuid,
                            )
                            errors["base"] = "cannot_connect"
                        else:
                            dis = profile.dis_info or {}
                            _LOGGER.warning(
                                "[AquaClean] Config flow: unsupported GATT profile — "
                                "svc=%s write=%s notify=%s model=%s serial=%s",
                                profile.svc_uuid, profile.write_uuids, profile.notify_uuids,
                                dis.get("model_number", ""), dis.get("serial_number", ""),
                            )
                            _placeholders = {
                                "mac": data[CONF_DEVICE_ID],
                                "svc_uuid": profile.svc_uuid,
                                "write_uuids": ", ".join(profile.write_uuids) or "—",
                                "notify_uuids": ", ".join(profile.notify_uuids) or "—",
                                "device_model": dis.get("model_number") or "—",
                                "device_serial": dis.get("serial_number") or "—",
                                "version": version,
                            }
                            async_create_issue(
                                self.hass,
                                DOMAIN,
                                f"unsupported_device_{data[CONF_DEVICE_ID].replace(':', '').lower()}",
                                is_fixable=False,
                                severity=IssueSeverity.WARNING,
                                translation_key="unsupported_device",
                                translation_placeholders=_placeholders,
                            )
                            return self.async_abort(
                                reason="unsupported_device",
                                description_placeholders=_placeholders,
                            )
                    if not errors:
                        await self.async_set_unique_id(data[CONF_DEVICE_ID])
                        self._abort_if_unique_id_configured()
                        await asyncio.sleep(3.0)  # let BLE teardown propagate before coordinator first poll
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

            # Skip the live BLE connection test when only non-connection settings
            # changed (e.g. poll_interval).  Running a test in that case races with
            # the coordinator's persistent ESP32 subscription and always fails with
            # ESPHomeDeviceNotFoundError (0 packets — slot already in use).
            _connection_keys = (
                CONF_DEVICE_ID, CONF_ESPHOME_HOST, CONF_ESPHOME_PORT, CONF_NOISE_PSK,
                CONF_USE_HA_BLUETOOTH,
            )
            connection_unchanged = all(
                data.get(k) == current.get(k) for k in _connection_keys
            )
            if connection_unchanged:
                _LOGGER.debug("[AquaClean] Options flow: connection params unchanged — skipping BLE test")
                return self.async_create_entry(title="", data=data)

            use_ha_bt = data.get(CONF_USE_HA_BLUETOOTH, False)
            test_esphome_host = None if use_ha_bt else data[CONF_ESPHOME_HOST]
            if not test_esphome_host and not use_ha_bt and not await _has_local_bluetooth():
                errors["base"] = "no_bluetooth"
            else:
                hass = self.hass if (not data[CONF_ESPHOME_HOST] or use_ha_bt) else None
                try:
                    profile = await _test_connection(
                        data[CONF_DEVICE_ID],
                        test_esphome_host,
                        data.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT),
                        data[CONF_NOISE_PSK],
                        hass=hass,
                    )
                except Exception:
                    _LOGGER.exception("[AquaClean] Options flow: connection test failed")
                    errors["base"] = "cannot_connect"
                else:
                    if profile is not None and not profile.is_standard and not profile.arendi_handshake_done:
                        if _is_known_alba_profile(profile):
                            _LOGGER.warning(
                                "[AquaClean] Options flow: known Alba GATT profile but handshake "
                                "failed (svc=%s) — ESP32 NimBLE cache may be stale; "
                                "press 'Clear Bluetooth Cache' on the proxy and retry",
                                profile.svc_uuid,
                            )
                            errors["base"] = "cannot_connect"
                        else:
                            dis = profile.dis_info or {}
                            _LOGGER.warning(
                                "[AquaClean] Options flow: unsupported GATT profile — "
                                "svc=%s write=%s notify=%s model=%s serial=%s",
                                profile.svc_uuid, profile.write_uuids, profile.notify_uuids,
                                dis.get("model_number", ""), dis.get("serial_number", ""),
                            )
                            _placeholders = {
                                "mac": data[CONF_DEVICE_ID],
                                "svc_uuid": profile.svc_uuid,
                                "write_uuids": ", ".join(profile.write_uuids) or "—",
                                "notify_uuids": ", ".join(profile.notify_uuids) or "—",
                                "device_model": dis.get("model_number") or "—",
                                "device_serial": dis.get("serial_number") or "—",
                                "version": version,
                            }
                            async_create_issue(
                                self.hass,
                                DOMAIN,
                                f"unsupported_device_{data[CONF_DEVICE_ID].replace(':', '').lower()}",
                                is_fixable=False,
                                severity=IssueSeverity.WARNING,
                                translation_key="unsupported_device",
                                translation_placeholders=_placeholders,
                            )
                            return self.async_abort(
                                reason="unsupported_device",
                                description_placeholders=_placeholders,
                            )
                    if not errors:
                        await asyncio.sleep(3.0)  # let BLE teardown propagate before coordinator first poll
                        return self.async_create_entry(title="", data=data)

        return self.async_show_form(
            step_id="init",
            data_schema=_build_schema(current),
            errors=errors,
            description_placeholders={"version": version},
        )
