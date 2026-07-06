"""Config flow and options flow for Geberit AquaClean."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re

import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers import selector
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.loader import async_get_integration

from .const import (
    DOMAIN,
    ADV_DEVICE_TYPE_TO_MODEL,
    CONF_DEVICE_ID,
    CONF_DEVICE_TYPE,
    CONF_ESPHOME_HOST,
    CONF_ESPHOME_PORT,
    CONF_NOISE_PSK,
    CONF_POLL_INTERVAL,
    CONF_USE_HA_BLUETOOTH,
    DEFAULT_ESPHOME_PORT,
    DEFAULT_POLL_INTERVAL,
)
from .coordinator import _get_proxy_lock


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

    # Serialize against any coordinator already polling another device on the same
    # ESP32 proxy — the firmware only allows one BLE advertisement subscription at a
    # time (see CLAUDE.md trap 12). Without this, testing/adding a second device on a
    # shared proxy can race an existing coordinator's poll cycle.
    proxy_ctx = _get_proxy_lock(esphome_host) if esphome_host else contextlib.nullcontext()
    async with proxy_ctx:
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
                _LOGGER.warning(
                    "[AquaClean] Config flow: BLE connected but non-standard GATT profile "
                    "detected after init failure — svc=%s write=%s notify=%s dis=%s",
                    profile.svc_uuid, profile.write_uuids, profile.notify_uuids, profile.dis_info,
                    exc_info=True,
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
    """Multi-step setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._transport: str = "esphome_auto"
        self._found_proxies: list[dict] = []
        self._esphome_host: str | None = None
        self._esphome_port: int = DEFAULT_ESPHOME_PORT
        self._esphome_auto_selected: bool = False
        self._esphome_label: str = ""
        self._noise_psk: str | None = None
        self._found_devices: list[dict] = []
        self._mac: str | None = None
        self._adv_bytes: bytes = b""
        self._adv_device_type_str: str = ""
        self._device_label: str = ""
        self._version: str = "unknown"

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> AquaCleanOptionsFlow:
        return AquaCleanOptionsFlow(config_entry)

    async def _get_version(self) -> str:
        integration = await async_get_integration(self.hass, DOMAIN)
        return integration.manifest.get("version", "unknown")

    # ------------------------------------------------------------------
    # Step 1 — Transport selector
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None) -> FlowResult:
        if self._version == "unknown":
            self._version = await self._get_version()

        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema({
                    vol.Required("transport_type", default="esphome_auto"): selector.selector({
                        "select": {
                            "options": ["esphome_auto", "esphome_manual", "local_ble", "local_ble_ha"],
                            "translation_key": "transport_type",
                        }
                    }),
                }),
                description_placeholders={"version": self._version},
            )

        self._transport = user_input["transport_type"]

        if self._transport == "esphome_auto":
            try:
                from aquaclean_console_app.setup.discovery import async_discover_esphome
                self._found_proxies = await async_discover_esphome(timeout=12.0, hass=self.hass)
            except Exception:
                self._found_proxies = []

            # Always show the combined pick/manual step: proxy dropdown (if any found)
            # alongside always-visible manual IP fields so mDNS misses don't block setup.
            return await self.async_step_esphome_pick()

        elif self._transport == "esphome_manual":
            return await self.async_step_esphome_host()

        else:  # local_ble or local_ble_ha
            return await self.async_step_ble_scan()

    # ------------------------------------------------------------------
    # Step 2a — ESPHome proxy: discovered dropdown + always-visible manual fields
    # ------------------------------------------------------------------
    async def async_step_esphome_pick(self, user_input=None) -> FlowResult:
        """Combined proxy picker + manual IP entry.

        Proxy dropdown (when proxies were found) and manual host/port/psk fields
        are shown simultaneously.  If a proxy is selected from the dropdown it takes
        precedence over anything typed in the manual host field.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input.get("rescan"):
                try:
                    from aquaclean_console_app.setup.discovery import async_discover_esphome
                    self._found_proxies = await async_discover_esphome(timeout=12.0, hass=self.hass)
                except Exception:
                    self._found_proxies = []
                return await self.async_step_esphome_pick()

            chosen_proxy = (user_input.get("esphome_proxy") or "").strip()
            manual_host = (user_input.get("esphome_host") or "").strip()

            if chosen_proxy and chosen_proxy != "__none__":
                # Proxy selected from dropdown — use its IP:port.
                try:
                    host_part, port_part = chosen_proxy.rsplit(":", 1)
                    self._esphome_host = host_part
                    self._esphome_port = int(port_part)
                except Exception:
                    self._esphome_host = chosen_proxy
                    self._esphome_port = DEFAULT_ESPHOME_PORT
                _sel_proxy = next(
                    (p for p in self._found_proxies if f"{p['ip']}:{p['port']}" == chosen_proxy),
                    None,
                )
                if _sel_proxy:
                    self._esphome_auto_selected = True
                    self._esphome_label = (
                        f"{_sel_proxy['name']} "
                        f"({_sel_proxy['host'] or _sel_proxy['ip']}:{_sel_proxy['port']})"
                    )
            elif manual_host:
                self._esphome_host = manual_host
                self._esphome_port = int(user_input.get("esphome_port") or DEFAULT_ESPHOME_PORT)
                psk = (user_input.get("noise_psk") or "").strip()
                self._noise_psk = psk or None
            else:
                errors["esphome_host"] = "esphome_host_required"

            if not errors:
                return await self.async_step_ble_scan()

        # Build schema — proxy dropdown only present when proxies were discovered.
        schema_fields: dict = {}
        if self._found_proxies:
            _default_proxy = f"{self._found_proxies[0]['ip']}:{self._found_proxies[0]['port']}"
            options = [
                selector.SelectOptionDict(
                    # Use IP as value — aioesphomeapi would create a competing Zeroconf
                    # instance to resolve .local hostnames; passing an IP avoids that.
                    value=f"{p['ip']}:{p['port']}",
                    label=f"{p['name']} ({p['host'] or p['ip']}:{p['port']})",
                )
                for p in self._found_proxies
            ]
            options.append(selector.SelectOptionDict(value="__none__", label="Enter IP manually below"))
            schema_fields[vol.Optional("esphome_proxy", default=_default_proxy)] = selector.selector({
                "select": {"options": options}
            })

        schema_fields[vol.Optional("esphome_host", default=self._esphome_host or "")] = selector.selector(
            {"text": {}}
        )
        schema_fields[vol.Optional("esphome_port", default=self._esphome_port)] = selector.selector(
            {"number": {"min": 1, "max": 65535, "mode": "box"}}
        )
        schema_fields[vol.Optional("noise_psk", default="")] = selector.selector(
            {"text": {"type": "password"}}
        )
        schema_fields[vol.Optional("rescan", default=False)] = selector.selector(
            {"boolean": {}}
        )

        return self.async_show_form(
            step_id="esphome_pick",
            data_schema=vol.Schema(schema_fields),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 2b — Manual ESPHome host entry
    # ------------------------------------------------------------------
    async def async_step_esphome_host(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.get("esphome_host", "").strip()
            if not host:
                errors["esphome_host"] = "esphome_host_required"
            else:
                self._esphome_host = host
                self._esphome_port = int(user_input.get("esphome_port", DEFAULT_ESPHOME_PORT))
                psk = user_input.get("noise_psk", "").strip()
                self._noise_psk = psk or None
                return await self.async_step_ble_scan()

        return self.async_show_form(
            step_id="esphome_host",
            data_schema=vol.Schema({
                vol.Required("esphome_host", default=self._esphome_host or ""): selector.selector({
                    "text": {}
                }),
                vol.Optional("esphome_port", default=self._esphome_port): selector.selector({
                    "number": {"min": 1, "max": 65535, "mode": "box"}
                }),
                vol.Optional("noise_psk", default=""): selector.selector({
                    "text": {"type": "password"}
                }),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 3 — BLE scan
    # ------------------------------------------------------------------
    async def async_step_ble_scan(self, user_input=None) -> FlowResult:
        if user_input is None:
            try:
                if self._esphome_host:
                    from aquaclean_console_app.setup.discovery import async_scan_ble_via_esphome
                    self._found_devices = await async_scan_ble_via_esphome(
                        self._esphome_host, self._esphome_port, self._noise_psk, timeout=10.0
                    )
                elif self._transport == "local_ble_ha":
                    self._found_devices = []
                else:
                    from aquaclean_console_app.setup.discovery import async_scan_ble_local
                    self._found_devices = await async_scan_ble_local(timeout=10.0)
            except Exception as exc:
                _LOGGER.debug("BLE scan exception: %s", exc, exc_info=True)
                self._found_devices = []

            configured_macs = {
                entry.data.get(CONF_DEVICE_ID, "").upper()
                for entry in self._async_current_entries()
            }
            self._found_devices = [
                d for d in self._found_devices
                if d["mac"].upper() not in configured_macs
            ]

            if not self._found_devices:
                return await self.async_step_device_manual()

            from aquaclean_console_app.setup.discovery import parse_geberit_adv_info
            options = []
            for d in self._found_devices:
                adv_bytes = d.get("adv_bytes") or b""
                adv_info = parse_geberit_adv_info(adv_bytes)
                _LOGGER.debug(
                    "ble_scan label: mac=%s keys=%s adv_bytes=%dB hex=%s pre_article=%r pre_type=%r parsed=%s",
                    d["mac"], list(d.keys()), len(adv_bytes),
                    adv_bytes[:32].hex() if adv_bytes else "(none)",
                    d.get("article_number"), d.get("device_type"), adv_info,
                )
                article = d.get("article_number") or adv_info["article_number"] or d.get("adv_name") or ""
                device_type = d.get("device_type") or adv_info["device_type"] or ""
                label = d["mac"]
                if article:
                    label = f"{article}  {label}"
                if device_type:
                    label += f"  ({device_type})"
                label += f"  {d['rssi']:+d} dBm"
                _LOGGER.debug("ble_scan label result: %r", label)
                options.append(selector.SelectOptionDict(value=d["mac"], label=label))
            options.append(selector.SelectOptionDict(value="__manual__", label="Enter MAC manually…"))

            return self.async_show_form(
                step_id="ble_scan",
                data_schema=vol.Schema({
                    vol.Required("device_mac"): selector.selector({
                        "select": {"options": options}
                    }),
                }),
            )

        chosen = user_input["device_mac"]
        if chosen == "__manual__":
            return await self.async_step_device_manual()
        self._mac = chosen.upper()
        _sel = next((d for d in self._found_devices if d["mac"].upper() == self._mac), None)
        self._adv_bytes = (_sel or {}).get("adv_bytes") or b""
        if _sel:
            from aquaclean_console_app.setup.discovery import parse_geberit_adv_info
            _ai = parse_geberit_adv_info(self._adv_bytes)
            _a = _sel.get("article_number") or _ai["article_number"] or _sel.get("adv_name") or ""
            _dt = _sel.get("device_type") or _ai["device_type"] or ""
            self._adv_device_type_str = _dt
            _lbl = self._mac
            if _a:
                _lbl = f"{_a}  {_lbl}"
            if _dt:
                _lbl += f"  ({_dt})"
            self._device_label = _lbl
        else:
            self._device_label = self._mac or ""
        return await self.async_step_confirm()

    # ------------------------------------------------------------------
    # Step 4 — Manual MAC entry
    # ------------------------------------------------------------------
    async def async_step_device_manual(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            raw = user_input.get("device_id", "").strip().upper()
            if not re.match(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$", raw):
                errors["device_id"] = "invalid_mac"
            else:
                self._mac = raw
                self._device_label = raw
                return await self.async_step_confirm()

        return self.async_show_form(
            step_id="device_manual",
            data_schema=vol.Schema({
                vol.Required("device_id", default=self._mac or ""): selector.selector({
                    "text": {}
                }),
            }),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Step 5 — Confirm + connection test
    # ------------------------------------------------------------------
    async def async_step_confirm(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            poll_interval = int(user_input.get("poll_interval", DEFAULT_POLL_INTERVAL))
            use_ha_bt = self._transport == "local_ble_ha"
            test_esphome_host = None if use_ha_bt else self._esphome_host
            hass = self.hass if (not self._esphome_host or use_ha_bt) else None

            try:
                profile = await _test_connection(
                    self._mac,
                    test_esphome_host,
                    self._esphome_port,
                    self._noise_psk,
                    hass=hass,
                )
            except Exception:
                _LOGGER.exception("[AquaClean] Config flow wizard: connection test failed")
                errors["base"] = "cannot_connect"
            else:
                if profile is not None and not profile.is_standard and not profile.arendi_handshake_done:
                    if _is_known_alba_profile(profile):
                        # BLE connected and Alba GATT matched, but Arendi handshake timed out.
                        # Common on first connect: NimBLE does fresh GATT discovery (~13 s),
                        # exhausting a test session timer.  The cache is now warm; retry once
                        # automatically — second connect takes ~2 s and handshake succeeds.
                        _LOGGER.info(
                            "[AquaClean] Config flow: Alba handshake timed out — "
                            "auto-retrying once (NimBLE GATT cache now warm)"
                        )
                        await asyncio.sleep(2.0)
                        try:
                            profile = await _test_connection(
                                self._mac, test_esphome_host, self._esphome_port,
                                self._noise_psk, hass=hass,
                            )
                        except Exception:
                            _LOGGER.warning(
                                "[AquaClean] Config flow wizard: Alba handshake retry raised exception"
                            )
                            errors["base"] = "cannot_connect"
                        else:
                            if (
                                profile is not None
                                and not profile.is_standard
                                and not profile.arendi_handshake_done
                                and _is_known_alba_profile(profile)
                            ):
                                _LOGGER.warning(
                                    "[AquaClean] Config flow wizard: known Alba GATT profile but "
                                    "handshake failed after auto-retry (svc=%s) — possible causes: "
                                    "stale BlueZ GATT handle cache on the HA machine "
                                    "(fix: bluetoothctl remove %s), or stale NimBLE NVS cache on "
                                    "the ESP32 proxy (fix: clear BT cache in ESPHome UI)",
                                    profile.svc_uuid, self._mac,
                                )
                                errors["base"] = "alba_handshake_failed"
                            # else: retry succeeded — fall through to `if not errors:`
                    else:
                        dis = profile.dis_info or {}
                        _LOGGER.warning(
                            "[AquaClean] Config flow wizard: unsupported GATT profile — "
                            "svc=%s write=%s notify=%s model=%s serial=%s",
                            profile.svc_uuid, profile.write_uuids, profile.notify_uuids,
                            dis.get("model_number", ""), dis.get("serial_number", ""),
                        )
                        _placeholders = {
                            "mac": self._mac,
                            "svc_uuid": profile.svc_uuid,
                            "write_uuids": ", ".join(profile.write_uuids) or "—",
                            "notify_uuids": ", ".join(profile.notify_uuids) or "—",
                            "device_model": dis.get("model_number") or "—",
                            "device_serial": dis.get("serial_number") or "—",
                            "version": self._version,
                        }
                        async_create_issue(
                            self.hass,
                            DOMAIN,
                            f"unsupported_device_{self._mac.replace(':', '').lower()}",
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
                    await self.async_set_unique_id(self._mac)
                    self._abort_if_unique_id_configured()
                    await asyncio.sleep(3.0)  # let BLE teardown propagate before coordinator first poll
                    data = {
                        CONF_DEVICE_ID: self._mac,
                        CONF_DEVICE_TYPE: ADV_DEVICE_TYPE_TO_MODEL.get(self._adv_device_type_str),
                        CONF_ESPHOME_HOST: self._esphome_host,
                        CONF_ESPHOME_PORT: self._esphome_port,
                        CONF_NOISE_PSK: self._noise_psk,
                        CONF_POLL_INTERVAL: poll_interval,
                        CONF_USE_HA_BLUETOOTH: use_ha_bt,
                    }
                    return self.async_create_entry(
                        title=f"AquaClean {self._mac}",
                        data=data,
                    )

        if self._transport in ("esphome_auto", "esphome_manual"):
            if self._esphome_auto_selected and self._esphome_label:
                transport_label = f"ESPHome {self._esphome_label} (auto-discovered)"
            else:
                host_str = f"{self._esphome_host}:{self._esphome_port}" if self._esphome_host else "—"
                transport_label = f"ESPHome {host_str} (manual)"
        elif self._transport == "local_ble":
            transport_label = "Local Bluetooth"
        elif self._transport == "local_ble_ha":
            transport_label = "HA Bluetooth domain"
        else:
            transport_label = self._transport

        mac_label = self._device_label or self._mac or ""

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({
                vol.Optional("poll_interval", default=DEFAULT_POLL_INTERVAL): selector.selector({
                    "number": {"min": 5, "max": 3600, "step": 5, "mode": "slider", "unit_of_measurement": "s"}
                }),
            }),
            errors=errors,
            description_placeholders={
                "mac": mac_label,
                "transport": transport_label,
                "esphome_host": self._esphome_host or "—",
                "version": self._version,
            },
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
