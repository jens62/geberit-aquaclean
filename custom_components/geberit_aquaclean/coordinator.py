"""DataUpdateCoordinator — polls the AquaClean device on-demand over BLE."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import BLEPeripheralTimeoutError

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

_LOGGER = logging.getLogger(__name__)

# Circuit breaker constants — match standalone bridge behaviour
_CIRCUIT_OPEN_THRESHOLD = 5    # consecutive failures before opening circuit
_CIRCUIT_OPEN_PROBE_SLEEP = 60  # extra seconds before each probe when circuit is open
_ESP32_RESTART_SLEEP = 30       # seconds to wait after sending ESP32 restart command

# Alba fast/slow poll split.
# Fast polls (every cycle) read 13 DpIds (~4.6 s incl. connect) — BLE occupied ~15 % of 30 s.
# Slow polls (every Nth) read all 99 DpIds (~22 s) for diagnostic/static data.
_ALBA_SLOW_POLL_EVERY = 10


class AquaCleanCoordinator(DataUpdateCoordinator):
    """Polls the AquaClean device on a fixed interval using an on-demand BLE connection.

    Supports Geberit AquaClean Mera Comfort (original protocol) and AquaClean Alba
    (Ble20 protocol).  Device type is detected automatically on first successful poll.

    ESPHome proxy path — persistent TCP:
      The ESP32 API TCP connection is kept alive between polls.  Only the BLE GATT
      link to the Geberit is opened and closed each cycle.

    Local BLE path:
      A fresh BluetoothLeConnector is created per poll (unchanged from before).

    Circuit breaker:
      After _CIRCUIT_OPEN_THRESHOLD consecutive failures the circuit opens.
      If an ESPHome host is configured, an ESP32 restart is attempted immediately.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # options (from options flow) take precedence over data (from initial config flow)
        conf = {**entry.data, **entry.options}
        self._device_id: str = conf[CONF_DEVICE_ID]
        self._esphome_host: str | None = conf.get(CONF_ESPHOME_HOST) or None
        self._esphome_port: int = conf.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT)
        self._noise_psk: str | None = conf.get(CONF_NOISE_PSK) or None
        self._use_ha_bluetooth: bool = conf.get(CONF_USE_HA_BLUETOOTH, False)
        poll_interval: int = conf.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._esphome_name_cache: str | None = None
        self._ble_name_cache: str | None = None
        self.ble_connected_at: datetime | None = None
        self.ble_state: str = "disconnected"
        self._ble_connected_sensor = None  # AquaCleanBleConnectedSensor; set in async_added_to_hass
        # Detected device type: "mera" | "alba" | None (unknown, detected on first poll)
        self._device_type: str | None = None
        # Performance statistics
        self._last_connect_ms: int | None = None
        self._last_poll_ms: int | None = None
        self._stat_count: int = 0
        self._connect_total_ms: float = 0.0
        self._poll_total_ms: float = 0.0
        self._connect_min_ms: float | None = None
        self._connect_max_ms: float | None = None
        self._poll_min_ms: float | None = None
        self._poll_max_ms: float | None = None
        # RSSI statistics
        self._ble_rssi_count: int = 0
        self._ble_rssi_total: float = 0.0
        self._ble_rssi_min: float | None = None
        self._ble_rssi_max: float | None = None
        self._wifi_rssi_count: int = 0
        self._wifi_rssi_total: float = 0.0
        self._wifi_rssi_min: float | None = None
        self._wifi_rssi_max: float | None = None
        # Transport type: "bleak" | "esp32-wifi" | "esp32-eth"
        self._transport: str | None = None
        # Last error
        self.last_error_code: str | None = None
        self.last_error_hint: str | None = None
        # Circuit breaker state
        self._consecutive_failures: int = 0
        self._circuit_open: bool = False
        self._ble_lock = asyncio.Lock()
        # Persistent ESPHome connector and client (reused across polls).
        # _esphome_client is either AquaCleanClient (mera) or AlbaClient (alba).
        self._esphome_connector = None
        self._esphome_client = None
        # habluetooth path: connector created per poll (local var in _do_poll).
        # Stored here so async_close() can disconnect it if the integration is
        # disabled while a poll is in progress.
        self._habluetooth_connector = None
        self._unsupported_device = False
        # DataPointInventory cache for Alba devices.  The DpId inventory is static
        # (device hardware property) — fetched once on first poll and reused to
        # avoid the ~12 s BLE exchange on every subsequent connect.
        self._alba_inventory: dict = {}
        # Slow-poll data cache: device identification, full misc state, instanced stats,
        # profile settings.  Refreshed every _ALBA_SLOW_POLL_EVERY polls; fast polls
        # read only 9 live-changing DpIds and merge with this cache.
        self._alba_slow_cache: dict = {}
        self._alba_poll_num: int = 0
        # Stored-settings cache for Mera devices. Fetched once per device boot; cleared
        # when a write is issued via async_set_profile_setting / async_set_common_setting.
        # Both setting types change rarely (only on explicit user action) — re-fetching
        # every poll wastes ~3.6 s of BLE time (11 profile + 7 common queries).
        self._mera_profile_settings_cache: dict | None = None
        self._mera_common_settings_cache: dict | None = None
        # Firmware cloud check: result cached here; re-checked hourly inside _do_poll.
        self._firmware_update_result: dict | None = None
        self._last_firmware_check_at: datetime | None = None
        self._identification_logged: bool = False
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
        """Create a fresh BluetoothLeConnector for the local BLE path.

        When use_ha_bluetooth is True (Option B), bypasses the ESPHome path entirely
        and uses HA's bluetooth domain even if esphome_host is configured.
        """
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
        if self._use_ha_bluetooth:
            return BluetoothLeConnector(None, self._esphome_port, self._noise_psk, hass=self.hass)
        ha = self.hass if not self._esphome_host else None
        return BluetoothLeConnector(self._esphome_host, self._esphome_port, self._noise_psk, hass=ha)

    def _get_esphome_connector(self):
        """Return the persistent ESPHome connector, creating it if needed."""
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
        if self._esphome_connector is None:
            self._esphome_connector = BluetoothLeConnector(
                self._esphome_host, self._esphome_port, self._noise_psk, hass=None
            )
            _LOGGER.debug("Created persistent ESPHome connector")
        return self._esphome_connector

    def _ensure_esphome_client(self, connector):
        """Return the persistent ESPHome client, creating it if needed.

        Creates an AlbaClient for alba devices, AquaCleanClient for mera (default).
        The client registers its data handler on the connector exactly once.
        """
        if self._esphome_client is not None:
            return self._esphome_client
        if self._device_type == "alba":
            from aquaclean_console_app.aquaclean_core.Clients.AlbaClient import AlbaClient
            self._esphome_client = AlbaClient(connector)
            _LOGGER.debug("Created persistent AlbaClient for ESPHome connector")
        else:
            from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory
            self._esphome_client = AquaCleanClientFactory(connector).create_client()
            _LOGGER.debug("Created persistent AquaCleanClient for ESPHome connector")
        return self._esphome_client

    def _make_local_client(self, connector):
        """Create a fresh local BLE client of the appropriate type."""
        if self._device_type == "alba":
            from aquaclean_console_app.aquaclean_core.Clients.AlbaClient import AlbaClient
            return AlbaClient(connector)
        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory
        return AquaCleanClientFactory(connector).create_client()

    def _reset_esphome_connector(self) -> None:
        """Discard the persistent ESPHome connector+client so the next poll starts fresh."""
        if self._esphome_connector is not None:
            _LOGGER.debug("Resetting persistent ESPHome connector")
        self._esphome_connector = None
        self._esphome_client = None

    async def async_close(self) -> None:
        """Disconnect all active BLE connections on integration unload."""
        if self._esphome_connector is not None:
            _LOGGER.debug("Closing persistent ESPHome connector on unload")
            try:
                async with asyncio.timeout(5.0):
                    await self._esphome_connector.disconnect()
            except Exception:
                pass
            self._esphome_connector = None
            self._esphome_client = None
        if self._habluetooth_connector is not None:
            _LOGGER.debug("Closing in-progress habluetooth connector on unload")
            try:
                async with asyncio.timeout(5.0):
                    await self._habluetooth_connector.disconnect()
            except Exception:
                pass
            self._habluetooth_connector = None

    # ------------------------------------------------------------------
    # DataUpdateCoordinator protocol
    # ------------------------------------------------------------------

    def _set_error(self, ec) -> None:
        self.last_error_code = ec.code
        self.last_error_hint = ec.hint

    def _clear_error(self) -> None:
        self.last_error_code = None
        self.last_error_hint = None

    def _push_ble_state(self, state: str) -> None:
        self.ble_state = state
        if self._ble_connected_sensor is not None:
            self._ble_connected_sensor.async_write_ha_state()

    async def _async_update_data(self) -> dict:
        """Circuit-breaker wrapper: tracks consecutive failures, triggers ESP32 restart.

        Onboarding fast-restart: if this is the very first poll attempt (_device_type is
        None) and the ESPHome scanner returns E0002, the ESP32 is restarted immediately
        and the poll is retried once silently.  If the retry succeeds the user sees no
        "Failed setup" message — only a ~60-second "Setup in progress" delay in the HA UI.
        """
        if self._unsupported_device:
            from aquaclean_console_app.ErrorCodes import E0010
            raise UpdateFailed(f"{E0010.code} — {E0010.message}")

        # Detect onboarding phase: no successful poll yet, ESPHome path active.
        # consecutive_failures == 0 ensures the fast-restart fires at most once.
        _onboarding = (
            self._device_type is None
            and self._esphome_host is not None
            and not self._use_ha_bluetooth
            and self._consecutive_failures == 0
        )

        if self._circuit_open and self._esphome_host and not self._use_ha_bluetooth:
            _LOGGER.debug(
                "Circuit open (%d consecutive failures) — sleeping %d s before probe",
                self._consecutive_failures,
                _CIRCUIT_OPEN_PROBE_SLEEP,
            )
            await asyncio.sleep(_CIRCUIT_OPEN_PROBE_SLEEP)

        async with self._ble_lock:
            try:
                result = await self._do_poll()
            except UpdateFailed as exc:
                # ── Onboarding fast-restart ───────────────────────────────────────
                # The config flow test session leaves the ESP32 BLE scanner stuck.
                # Restart immediately and retry once silently so the user never sees
                # "Failed setup" — just a ~60-second "Setup in progress" spinner.
                if _onboarding and "E0002" in str(exc):
                    _LOGGER.warning(
                        "AquaClean: ESPHome proxy scanner unavailable during initial setup — "
                        "restarting ESP32 and retrying. "
                        "Initial setup will complete in approximately 60 seconds."
                    )
                    try:
                        await self.async_restart_esp32()
                        _LOGGER.info(
                            "ESP32 reboot command sent; waiting %d s", _ESP32_RESTART_SLEEP
                        )
                        await asyncio.sleep(_ESP32_RESTART_SLEEP)
                        result = await self._do_poll()
                        # Retry succeeded — no error surfaced to HA
                        self._consecutive_failures = 0
                        self._circuit_open = False
                        return result
                    except UpdateFailed:
                        # Restart + retry still failed — calm message, no internal detail
                        self._consecutive_failures += 1
                        raise UpdateFailed(
                            "ESPHome proxy restarted but AquaClean device still not found — "
                            "ensure the device is powered on and within BLE range of the ESP32 proxy"
                        )
                    except Exception as restart_exc:
                        _LOGGER.warning(
                            "ESP32 restart failed: %s — HA will retry via normal path", restart_exc
                        )
                        self._consecutive_failures += 1
                        raise exc  # re-raise original UpdateFailed

                # ── Normal circuit-breaker path ───────────────────────────────────
                self._consecutive_failures += 1

                if self._consecutive_failures == _CIRCUIT_OPEN_THRESHOLD and self._esphome_host and not self._use_ha_bluetooth:
                    self._circuit_open = True
                    _LOGGER.warning(
                        "Circuit breaker open: %d consecutive poll failures — triggering ESP32 restart",
                        self._consecutive_failures,
                    )
                    try:
                        await self.async_restart_esp32()
                        _LOGGER.info(
                            "ESP32 restart command sent; waiting %d s for reboot",
                            _ESP32_RESTART_SLEEP,
                        )
                        await asyncio.sleep(_ESP32_RESTART_SLEEP)
                    except Exception as restart_exc:
                        _LOGGER.warning(
                            "Failed to send ESP32 restart command: %s", restart_exc,
                        )
                elif self._consecutive_failures == _CIRCUIT_OPEN_THRESHOLD and (not self._esphome_host or self._use_ha_bluetooth):
                    self._circuit_open = True
                    _LOGGER.warning(
                        "Circuit breaker open: %d consecutive poll failures",
                        self._consecutive_failures,
                    )
                elif self._consecutive_failures > _CIRCUIT_OPEN_THRESHOLD:
                    _LOGGER.debug(
                        "Circuit open — consecutive failure %d", self._consecutive_failures
                    )
                raise
            else:
                if self._consecutive_failures > 0:
                    _LOGGER.info(
                        "Poll recovered after %d consecutive failure(s)", self._consecutive_failures
                    )
                self._consecutive_failures = 0
                self._circuit_open = False
                return result

    async def _do_poll(self) -> dict:
        """Connect, detect device type (if first poll), fetch data, disconnect."""
        from bleak import BleakError
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            ESPHomeConnectionError,
            ESPHomeDeviceNotFoundError,
        )
        from aquaclean_console_app.ErrorCodes import E0002, E0003, E0004, E0010, E1002, E7002

        poll_start = datetime.now(timezone.utc)

        if self._esphome_host and not self._use_ha_bluetooth:
            connector = self._get_esphome_connector()
        else:
            connector = self._make_connector()
            self._habluetooth_connector = connector

        try:
            # ── Phase 1: connect ──────────────────────────────────────────────
            self._push_ble_state("connecting")
            t_connect = time.perf_counter()
            try:
                if self._device_type == "alba":
                    # Alba: connect_ble_only = connect_async + post_connect (DataPointInventory)
                    if self._esphome_host and not self._use_ha_bluetooth:
                        client = self._ensure_esphome_client(connector)
                    else:
                        from aquaclean_console_app.aquaclean_core.Clients.AlbaClient import AlbaClient
                        client = AlbaClient(connector)
                    await client.connect_ble_only(
                        self._device_id,
                        inventory=self._alba_inventory or None,
                    )

                elif self._device_type == "mera":
                    if self._esphome_host and not self._use_ha_bluetooth:
                        client = self._ensure_esphome_client(connector)
                    else:
                        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory
                        client = AquaCleanClientFactory(connector).create_client()
                    await client.connect_ble_only(self._device_id)

                else:
                    # Unknown device type — connect via connector directly, then detect.
                    # We avoid creating any client before detection to prevent registering
                    # the wrong data handler (Mera handler + Ble20 handler would both fire).
                    await connector.connect_async(self._device_id)

                    if connector.is_variant_a and connector.arendi_handshake_done:
                        # AquaClean Alba (Ble20 protocol)
                        self._device_type = "alba"
                        from aquaclean_console_app.aquaclean_core.Clients.AlbaClient import AlbaClient
                        client = AlbaClient(connector)
                        if self._esphome_host and not self._use_ha_bluetooth:
                            self._esphome_client = client
                        await client.post_connect()  # DataPointInventory — mandatory first step
                        self._alba_inventory = client._inventory  # cache for subsequent polls
                        _LOGGER.info("Detected AquaClean Alba (Ble20) device — using Alba protocol")

                    elif not connector.is_variant_a:
                        # AquaClean Mera Comfort (original protocol)
                        self._device_type = "mera"
                        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory
                        client = AquaCleanClientFactory(connector).create_client()
                        if self._esphome_host and not self._use_ha_bluetooth:
                            self._esphome_client = client
                        _LOGGER.info("Detected AquaClean Mera Comfort device — using Mera protocol")

                    else:
                        # is_variant_a but no arendi handshake → truly unsupported variant
                        client = None  # handled by check below

            except ESPHomeConnectionError as exc:
                self._reset_esphome_connector()
                self._set_error(E1002)
                raise UpdateFailed(f"{E1002.code} — {E1002.message}: {exc}") from exc
            except ESPHomeDeviceNotFoundError as exc:
                self._set_error(E0002)
                raise UpdateFailed(f"{E0002.code} — {E0002.message}: {exc}") from exc
            except BleakError as exc:
                self._set_error(E0003)
                raise UpdateFailed(f"{E0003.code} — {E0003.message}: {exc}") from exc
            except asyncio.TimeoutError as exc:
                self._set_error(E0003)
                raise UpdateFailed(f"{E0003.code} — {E0003.message}") from exc
            except Exception as exc:
                self._set_error(E0003)
                raise UpdateFailed(f"{E0003.code} — {E0003.message}: {exc}") from exc

            # Unsupported variant-A device (non-standard GATT, no Arendi handshake)
            if connector.is_variant_a and not connector.arendi_handshake_done:
                model  = (connector.ble_dis_info or {}).get("model_number", "—")
                serial = (connector.ble_dis_info or {}).get("serial_number", "—")
                try:
                    import json as _json, pathlib as _pathlib
                    _manifest = _json.loads(
                        (_pathlib.Path(__file__).parent / "manifest.json").read_text()
                    )
                    _ver = _manifest.get("version", "unknown")
                except Exception:
                    _ver = "unknown"
                async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"unsupported_device_{self._device_id.replace(':', '').lower()}",
                    is_fixable=False,
                    severity=IssueSeverity.WARNING,
                    translation_key="unsupported_device",
                    translation_placeholders={
                        "mac": self._device_id,
                        "svc_uuid": str(connector.SERVICE_UUID),
                        "write_uuids": str(connector.BULK_CHAR_BULK_WRITE_0_UUID),
                        "notify_uuids": str(connector.BULK_CHAR_BULK_READ_0_UUID),
                        "device_model": model,
                        "device_serial": serial,
                        "version": _ver,
                    },
                )
                self._unsupported_device = True
                self._set_error(E0010)
                raise UpdateFailed(
                    f"{E0010.code} — {E0010.message}: {model} ({serial})"
                )

            connect_ms = int((time.perf_counter() - t_connect) * 1000)
            self._last_connect_ms = connect_ms
            self.ble_connected_at = datetime.now(timezone.utc)
            self._push_ble_state("connected")

            if connector.esphome_proxy_name:
                self._esphome_name_cache = connector.esphome_proxy_name
            if connector.device_name and connector.device_name != "Unknown":
                self._ble_name_cache = connector.device_name
            ble_rssi = connector.rssi
            esphome_wifi_rssi = connector.esphome_wifi_rssi
            esphome_free_heap = connector.esphome_free_heap
            esphome_max_free_block = connector.esphome_max_free_block

            if self._esphome_host and not self._use_ha_bluetooth:
                self._transport = "esp32-wifi" if esphome_wifi_rssi is not None else "esp32-eth"
            else:
                self._transport = "bleak"

            if ble_rssi is not None:
                self._ble_rssi_count += 1
                self._ble_rssi_total += ble_rssi
                if self._ble_rssi_min is None or ble_rssi < self._ble_rssi_min:
                    self._ble_rssi_min = ble_rssi
                if self._ble_rssi_max is None or ble_rssi > self._ble_rssi_max:
                    self._ble_rssi_max = ble_rssi
            if esphome_wifi_rssi is not None:
                self._wifi_rssi_count += 1
                self._wifi_rssi_total += esphome_wifi_rssi
                if self._wifi_rssi_min is None or esphome_wifi_rssi < self._wifi_rssi_min:
                    self._wifi_rssi_min = esphome_wifi_rssi
                if self._wifi_rssi_max is None or esphome_wifi_rssi > self._wifi_rssi_max:
                    self._wifi_rssi_max = esphome_wifi_rssi

            # ── Phase 2: device-specific data fetch ───────────────────────────
            _GATT_TIMEOUT = 60
            t_poll = time.perf_counter()
            try:
                async with asyncio.timeout(_GATT_TIMEOUT):
                    if self._device_type == "alba":
                        result_data = await self._build_alba_result(client)
                    else:
                        result_data = await self._build_mera_result(client)
            except (BleakError, asyncio.TimeoutError, BLEPeripheralTimeoutError) as exc:
                self._set_error(E0003)
                raise UpdateFailed(f"{E0003.code} — {E0003.message}: {exc}") from exc
            except Exception as exc:
                ec = E0004 if "service" in str(exc).lower() or "gatt" in str(exc).lower() else E7002
                self._set_error(ec)
                raise UpdateFailed(f"{ec.code} — {ec.message}: {exc}") from exc

            # First successful poll: log consolidated identification.
            if not self._identification_logged and result_data.get("sap_number"):
                self._identification_logged = True
                soc = result_data.get("soc_versions") or "n/a"
                _LOGGER.info(
                    "%s — SAP=%s Serial=%s fw=%s SOC=%s initial_op=%s",
                    result_data.get("description", "?"),
                    result_data.get("sap_number", "?"),
                    result_data.get("serial_number", "?"),
                    result_data.get("firmware_version") or "?", soc,
                    result_data.get("initial_operation_date", "?"),
                )

            # Firmware cloud check: inline on first poll (result is None) and every 3600 s.
            fw = result_data.get("firmware_version")
            if fw:
                now_utc = datetime.now(timezone.utc)
                should_check = (
                    self._firmware_update_result is None
                    or self._last_firmware_check_at is None
                    or (now_utc - self._last_firmware_check_at).total_seconds() >= 3600
                )
                if should_check:
                    try:
                        from aquaclean_console_app.FirmwareUpdateService import check_firmware_update
                        self._firmware_update_result = await check_firmware_update(fw)
                        self._last_firmware_check_at = now_utc
                        _fw_result = self._firmware_update_result
                        if _fw_result.get("error"):
                            _LOGGER.warning("Firmware cloud check failed: %s", _fw_result["error"])
                        else:
                            _LOGGER.info(
                                "Firmware cloud check: device=%s cloud=%s series=%s update_available=%s",
                                _fw_result.get("device_version"), _fw_result.get("cloud_version"),
                                _fw_result.get("series"), _fw_result.get("update_available"),
                            )
                    except Exception as exc:
                        _LOGGER.warning("Firmware update check error: %s", exc)

            poll_ms = int((time.perf_counter() - t_poll) * 1000)
            self._last_poll_ms = poll_ms

            self._stat_count += 1
            self._connect_total_ms += connect_ms
            self._poll_total_ms += poll_ms
            if self._connect_min_ms is None or connect_ms < self._connect_min_ms:
                self._connect_min_ms = connect_ms
            if self._connect_max_ms is None or connect_ms > self._connect_max_ms:
                self._connect_max_ms = connect_ms
            if self._poll_min_ms is None or poll_ms < self._poll_min_ms:
                self._poll_min_ms = poll_ms
            if self._poll_max_ms is None or poll_ms > self._poll_max_ms:
                self._poll_max_ms = poll_ms

            self._clear_error()

            return {
                "device_type": self._device_type,
                **result_data,
                # Poll timing
                "poll_epoch": poll_start,
                "poll_interval": self.update_interval.total_seconds(),
                "next_poll": datetime.now(timezone.utc) + self.update_interval,
                # ESPHome / BLE connection info
                "esphome_name": self._esphome_name_cache,
                "ble_name": self._ble_name_cache,
                "ble_rssi": ble_rssi,
                "esphome_wifi_rssi": esphome_wifi_rssi,
                "esphome_free_heap": esphome_free_heap,
                "esphome_max_free_block": esphome_max_free_block,
                # Performance statistics
                "last_connect_ms": connect_ms,
                "last_poll_ms": poll_ms,
                "avg_connect_ms": round(self._connect_total_ms / self._stat_count, 1),
                "avg_poll_ms": round(self._poll_total_ms / self._stat_count, 1),
                "min_connect_ms": self._connect_min_ms,
                "max_connect_ms": self._connect_max_ms,
                "min_poll_ms": self._poll_min_ms,
                "max_poll_ms": self._poll_max_ms,
                "stat_count": self._stat_count,
                # RSSI statistics
                "avg_ble_rssi": round(self._ble_rssi_total / self._ble_rssi_count, 1) if self._ble_rssi_count else None,
                "min_ble_rssi": self._ble_rssi_min,
                "max_ble_rssi": self._ble_rssi_max,
                "avg_wifi_rssi": round(self._wifi_rssi_total / self._wifi_rssi_count, 1) if self._wifi_rssi_count else None,
                "min_wifi_rssi": self._wifi_rssi_min,
                "max_wifi_rssi": self._wifi_rssi_max,
                "transport": self._transport,
                "ble_dis_info": connector.ble_dis_info,
                # Firmware cloud check (populated asynchronously after first poll)
                "firmware_update_available": (self._firmware_update_result or {}).get("update_available"),
                "firmware_version_date": (self._firmware_update_result or {}).get("device_firmware_date"),
                "cloud_firmware_version": (self._firmware_update_result or {}).get("cloud_version"),
                "cloud_firmware_date": (self._firmware_update_result or {}).get("cloud_firmware_date"),
            }

        except UpdateFailed:
            raise
        except Exception as exc:
            from aquaclean_console_app.ErrorCodes import E7002
            self._set_error(E7002)
            raise UpdateFailed(f"{E7002.code} — {E7002.message}: {exc}") from exc
        finally:
            self._push_ble_state("disconnected")
            if self._esphome_host and not self._use_ha_bluetooth:
                try:
                    async with asyncio.timeout(5.0):
                        if connector.client is not None:
                            await connector.disconnect_ble_only()
                        else:
                            # Scan timed out — no BLE client; disconnect_ble_only() would be a
                            # no-op leaving the TCP open.  Close it explicitly so the ESP32 doesn't
                            # later process a queued subscription on this connection once
                            # api_connection_ is freed by the config-flow BLE teardown.
                            await connector.disconnect()
                            self._reset_esphome_connector()
                except Exception:
                    _LOGGER.debug("disconnect failed; resetting persistent ESPHome connector")
                    self._reset_esphome_connector()
            else:
                self._habluetooth_connector = None
                try:
                    async with asyncio.timeout(5.0):
                        await connector.disconnect()
                except Exception:
                    pass

    async def _build_mera_result(self, client) -> dict:
        """Phase 2 data fetch for Mera Comfort devices."""
        from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient import BLEPeripheralTimeoutError

        ident = await client.base_client.get_device_identification_async(0)
        initial_op_date = await client.base_client.get_device_initial_operation_date()
        state = await client.base_client.get_system_parameter_list_async(
            [0, 1, 2, 3, 4, 5, 6, 7, 12, 13]  # 12=LidOffset, 13=ShowerArmOffset; data_array[8],[9]
        )
        stats = await client.base_client.get_statistics_descale_async()
        soc_versions = await client.base_client.get_soc_application_versions_async()
        firmware_versions = await client.base_client.get_firmware_version_list_async()
        filter_status = await client.base_client.get_filter_status_async()
        if self._mera_profile_settings_cache is None:
            self._mera_profile_settings_cache = await client.base_client.get_stored_profile_settings_async()
        profile_settings = self._mera_profile_settings_cache
        if self._mera_common_settings_cache is None:
            self._mera_common_settings_cache = await client.base_client.get_stored_common_settings_async()
        common_settings = self._mera_common_settings_cache

        return {
            # Device identification
            "sap_number": ident.sap_number,
            "serial_number": ident.serial_number,
            "production_date": ident.production_date,
            "description": ident.description,
            "initial_operation_date": initial_op_date,
            # Live state
            "is_user_sitting": state.data_array[0] != 0,
            "is_anal_shower_running": state.data_array[3] != 0,
            "is_lady_shower_running": state.data_array[2] != 0,
            "is_dryer_running": state.data_array[1] != 0,
            "last_error_code": state.data_array[6],
            "lid_offset_position": state.data_array[8],        # SPL index 12, position 8
            "shower_arm_offset_position": state.data_array[9], # SPL index 13, position 9
            "descaling_state": state.data_array[4],            # SPL index 4: 0=idle 1=preparing 2=waiting 3=running
            "descaling_duration_min": state.data_array[5],     # SPL index 5: countdown minutes
            # Descale statistics
            "days_until_next_descale": stats.days_until_next_descale,
            "days_until_shower_restricted": stats.days_until_shower_restricted,
            "shower_cycles_until_confirmation": stats.shower_cycles_until_confirmation,
            "number_of_descale_cycles": stats.number_of_descale_cycles,
            "date_time_at_last_descale": (
                datetime.fromtimestamp(stats.date_time_at_last_descale).strftime("%d.%m.%Y")
                if stats.date_time_at_last_descale and stats.date_time_at_last_descale > 0
                else "Never"
            ),
            "unposted_shower_cycles": stats.unposted_shower_cycles,
            "soc_versions": str(soc_versions) if soc_versions else None,
            "firmware_version": (firmware_versions or {}).get("main"),
            # Filter status
            "filter_days_remaining": (filter_status or {}).get("days_until_filter_change"),
            "filter_last_reset": (
                datetime.fromtimestamp(ts, tz=timezone.utc)
                if (ts := (filter_status or {}).get("last_filter_reset")) and ts > 0
                else None
            ),
            "filter_reset_count": (filter_status or {}).get("filter_reset_count"),
            "filter_next_change": (
                datetime.fromtimestamp(ts, tz=timezone.utc)
                if (ts := (filter_status or {}).get("next_filter_change")) and ts > 0
                else None
            ),
            # User profile settings
            "ps_odour_extraction":     (profile_settings or {}).get(0),
            "ps_oscillator_state":     (profile_settings or {}).get(1),
            "ps_anal_shower_pressure": (profile_settings or {}).get(2),
            "ps_lady_shower_pressure": (profile_settings or {}).get(3),
            "ps_anal_shower_position": (profile_settings or {}).get(4),
            "ps_lady_shower_position": (profile_settings or {}).get(5),
            "ps_water_temperature":    (profile_settings or {}).get(6),
            "ps_wc_seat_heat":         (profile_settings or {}).get(7),
            "ps_dryer_temperature":    (profile_settings or {}).get(8),
            "ps_dryer_state":          (profile_settings or {}).get(9),
            "ps_dryer_spray_intensity": (profile_settings or {}).get(13),
            # Common settings
            "cs_odour_extraction_run_on":      (common_settings or {}).get(0),
            "cs_orientation_light_brightness": (common_settings or {}).get(1),
            "cs_orientation_light_activation": (common_settings or {}).get(3),
            "cs_orientation_light_color":      (common_settings or {}).get(2),
            "cs_wc_lid_sensor_sensitivity":    (common_settings or {}).get(4),
            "cs_wc_lid_open_automatically":    (common_settings or {}).get(6),
            "cs_wc_lid_close_automatically":   (common_settings or {}).get(7),
        }

    async def _build_alba_result(self, client) -> dict:
        """Phase 2 data fetch for AquaClean Alba (Ble20) devices.

        Fast polls (every cycle): poll_state (4 reads) + fast misc (9 reads) = 13 reads,
        ~4.6 s total — BLE occupied ~15 % of 30 s interval.
        Slow polls (every _ALBA_SLOW_POLL_EVERY cycles): full 99-DpId read, ~22 s.
        """
        self._alba_poll_num += 1
        do_slow = not self._alba_slow_cache or (self._alba_poll_num % _ALBA_SLOW_POLL_EVERY == 1)

        # poll_state (4 reads) — always read, it contains the live is_sitting / shower state
        state = await client.base_client.get_system_parameter_list_async([0, 1, 2, 3])

        if do_slow:
            ident            = await client.base_client.get_device_identification_async(0)
            misc             = await client.base_client.get_misc_state_async()
            instanced        = await client.base_client.get_instanced_stats_async()
            profile_settings = await client.base_client.get_stored_profile_settings_async()
            self._alba_slow_cache = {
                "ident": ident, "misc": misc,
                "instanced": instanced, "profile_settings": profile_settings,
            }
        else:
            ident            = self._alba_slow_cache["ident"]
            instanced        = self._alba_slow_cache["instanced"]
            profile_settings = self._alba_slow_cache["profile_settings"]
            # 9 live-changing reads; merge with cached slow fields so all keys are present
            fast_misc = await client.base_client.get_misc_state_fast_async()
            misc = {**self._alba_slow_cache["misc"], **fast_misc}

        return {
            # Device identification
            "sap_number": ident.sap_number,
            "serial_number": ident.serial_number,
            "production_date": ident.production_date,
            "description": ident.description,
            "initial_operation_date": "",
            # Live state (shared keys with Mera for binary sensors)
            "is_user_sitting": state.data_array[0] != 0,
            "is_anal_shower_running": state.data_array[3] != 0,
            "is_lady_shower_running": False,
            "is_dryer_running": False,
            "last_error_code": 0,
            # Mera-only keys set to None so Mera sensors show "Unknown" rather than crash
            "days_until_next_descale": None,
            "days_until_shower_restricted": None,
            "shower_cycles_until_confirmation": None,
            "number_of_descale_cycles": None,
            "date_time_at_last_descale": None,
            "unposted_shower_cycles": None,
            "soc_versions": None,
            "firmware_version": (client.firmware_versions or {}).get("main"),
            "filter_days_remaining": None,
            "filter_last_reset": None,
            "filter_reset_count": None,
            "filter_next_change": None,
            # Profile settings — IDs 1,2,4,6 overlap with Mera and work on Alba
            "ps_oscillator_state":     (profile_settings or {}).get(1),
            "ps_anal_shower_pressure": (profile_settings or {}).get(2),
            "ps_anal_shower_position": (profile_settings or {}).get(4),
            "ps_water_temperature":    (profile_settings or {}).get(6),
            # Mera-only profile settings → absent for Alba (entity returns None / unavailable)
            # Alba misc state — prefixed with alba_
            "alba_active_intensity":   misc.get("active_intensity"),
            "alba_active_position":    misc.get("active_position"),
            "alba_active_temperature": misc.get("active_temperature"),
            "alba_active_oscillation": misc.get("active_oscillation"),
            "alba_spray_arm_cleaning_status_raw": misc.get("spray_arm_cleaning_status_raw"),
            "alba_spray_arm_cleaning_status":     misc.get("spray_arm_cleaning_status"),
            "alba_descaling_status_raw":           misc.get("descaling_status_raw"),
            "alba_descaling_status":               misc.get("descaling_status"),
            "alba_days_until_next_descaling":      misc.get("days_until_next_descaling"),
            "alba_descaling_cycles":               misc.get("descaling_cycles"),
            "alba_credits_until_next_descaling":   misc.get("credits_until_next_descaling"),
            "alba_descaling_device_lock_remaining_days":     misc.get("descaling_device_lock_remaining_days"),
            "alba_descaling_device_relock_remaining_cycles": misc.get("descaling_device_relock_remaining_cycles"),
            "alba_descaling_device_lock_status_raw": misc.get("descaling_device_lock_status_raw"),
            "alba_descaling_device_lock_status":     misc.get("descaling_device_lock_status"),
            "alba_unaccounted_shower_cycles":        misc.get("unaccounted_shower_cycles"),
            "alba_timestamp_last_descaling":         misc.get("timestamp_last_descaling"),
            "alba_timestamp_last_descaling_request": misc.get("timestamp_last_descaling_request"),
            "alba_user_detection_status":   misc.get("user_detection_status"),
            "alba_rtc_time":                misc.get("rtc_time"),
            "alba_operation_time_total_s":        misc.get("operation_time_total_s"),
            "alba_operation_time_since_power_up_s": misc.get("operation_time_since_power_up_s"),
            "alba_error_power_supply":        misc.get("error_power_supply"),
            "alba_error_water_heater":        misc.get("error_water_heater"),
            "alba_error_level_control":       misc.get("error_level_control"),
            "alba_error_user_detection":      misc.get("error_user_detection"),
            "alba_error_water_pump":          misc.get("error_water_pump"),
            "alba_error_spray_arm_drive":     misc.get("error_spray_arm_drive"),
            "alba_error_maintenance_request": misc.get("error_maintenance_request"),
            "alba_error_descaling":           misc.get("error_descaling"),
            "alba_demo_mode":     misc.get("demo_mode"),
            "alba_showroom_mode": misc.get("showroom_mode"),
            "alba_dry_run_mode":  misc.get("dry_run_mode"),
            "alba_product_registration_level_raw": misc.get("product_registration_level_raw"),
            "alba_product_registration_level":     misc.get("product_registration_level"),
            # Firmware / hardware versions (diagnostic)
            "alba_fw_rs_version": misc.get("fw_rs_version"),
            "alba_fw_ts_version": misc.get("fw_ts_version"),
            "alba_hw_rs_version": misc.get("hw_rs_version"),
            "alba_mcu_version":   misc.get("mcu_version"),
            # Pairing secret (diagnostic, not exposed as a sensor entity)
            "alba_pairing_secret_hex": misc.get("pairing_secret_hex"),
            # Version strings (instanced DpIds 785–787)
            "alba_fus_version":            instanced.get("fus_version"),
            "alba_geberit_loader_version": instanced.get("geberit_loader_version"),
            "alba_wireless_stack_version": instanced.get("wireless_stack_version"),
            # Progress percentages (live during operation; None when not running)
            "alba_anal_shower_progress_pct":        (instanced.get("anal_shower_progress") or {}).get("pct"),
            "alba_descaling_progress_pct":          (instanced.get("descaling_progress") or {}).get("pct"),
            "alba_spray_arm_cleaning_progress_pct": (instanced.get("spray_arm_cleaning_progress") or {}).get("pct"),
            # Lifetime statistics counters (instanced DpId 689)
            "alba_stats_total_usages":              (instanced.get("stats_total") or {}).get("aquaclean_usages"),
            "alba_stats_total_anal_showers":        (instanced.get("stats_total") or {}).get("aquaclean_anal_showers"),
            "alba_stats_total_lady_showers":        (instanced.get("stats_total") or {}).get("aquaclean_lady_showers"),
            "alba_stats_total_dryings":             (instanced.get("stats_total") or {}).get("aquaclean_dryings"),
            "alba_stats_total_descalings":          (instanced.get("stats_total") or {}).get("aquaclean_descalings"),
            "alba_stats_total_spray_arm_cleanings": (instanced.get("stats_total") or {}).get("aquaclean_spray_arm_cleanings"),
        }

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    async def async_restart_esp32(self) -> None:
        """Trigger a software reboot of the ESP32 proxy."""
        if not self._esphome_host:
            raise ValueError("No ESPHome host configured")
        connector = self._make_connector()
        await connector.restart_esp32_async()

    async def async_set_profile_setting(self, setting_id: int, value: int) -> None:
        """Write a user profile setting via BLE, serialised with the poll loop."""
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import ESPHomeConnectionError

        async with self._ble_lock:
            if self._esphome_host and not self._use_ha_bluetooth:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
                if self._device_type == "alba":
                    await client.connect_ble_only(self._device_id, inventory=self._alba_inventory or None)
                else:
                    await client.connect_ble_only(self._device_id)
                await client.set_stored_profile_setting(setting_id, value)
                # Invalidate caches so the next poll re-reads the updated values.
                self._alba_slow_cache = {}
                self._mera_profile_settings_cache = None
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host and not self._use_ha_bluetooth:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect_ble_only()
                    except Exception:
                        _LOGGER.debug("disconnect_ble_only failed after set_profile_setting; resetting connector")
                        self._reset_esphome_connector()
                else:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect()
                    except Exception:
                        pass

    async def async_set_common_setting(self, setting_id: int, value: int) -> None:
        """Write a common (device-wide) setting via BLE. Only supported on Mera."""
        if self._device_type == "alba":
            _LOGGER.warning("Common settings not supported on Alba — ignoring set_common_setting(%d)", setting_id)
            return

        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import ESPHomeConnectionError

        async with self._ble_lock:
            if self._esphome_host and not self._use_ha_bluetooth:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
                await client.connect_ble_only(self._device_id)
                await client.set_stored_common_setting(setting_id, value)
                self._mera_common_settings_cache = None
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host and not self._use_ha_bluetooth:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect_ble_only()
                    except Exception:
                        _LOGGER.debug("disconnect_ble_only failed after set_common_setting; resetting connector")
                        self._reset_esphome_connector()
                else:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect()
                    except Exception:
                        pass

    async def async_execute_command(self, command: str) -> None:
        """Execute a device command via BLE, serialised with the poll loop."""
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import ESPHomeConnectionError

        async with self._ble_lock:
            if self._esphome_host and not self._use_ha_bluetooth:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
                if self._device_type == "alba":
                    await client.connect_ble_only(self._device_id, inventory=self._alba_inventory or None)
                else:
                    await client.connect_ble_only(self._device_id)
                if command == "toggle_lid":
                    await client.toggle_lid_position()
                elif command == "toggle_anal_shower":
                    await client.toggle_anal_shower()
                elif command == "toggle_lady_shower":
                    await client.toggle_lady_shower()
                elif command == "toggle_dryer":
                    await client.toggle_dryer()
                elif command == "toggle_orientation_light":
                    await client.toggle_orientation_light()
                elif command == "stop":
                    await client.stop()
                elif command == "orientation_light_off":
                    await client.set_orientation_light_mode(0)
                elif command == "orientation_light_on":
                    await client.set_orientation_light_mode(1)
                elif command == "orientation_light_when_approached":
                    await client.set_orientation_light_mode(2)
                elif command == "toggle_odour_extraction":
                    await client.toggle_odour_extraction()
                elif command == "odour_extraction_run_on":
                    await client.odour_extraction_run_on()
                elif command == "trigger_flush_manually":
                    await client.trigger_flush_manually()
                elif command == "prepare_descaling":
                    await client.prepare_descaling()
                elif command == "confirm_descaling":
                    await client.confirm_descaling()
                elif command == "cancel_descaling":
                    await client.cancel_descaling()
                elif command == "postpone_descaling":
                    await client.postpone_descaling()
                elif command == "start_cleaning_device":
                    await client.start_cleaning_device()
                elif command == "execute_next_cleaning_step":
                    await client.execute_next_cleaning_step()
                elif command == "start_lid_position_calibration":
                    await client.start_lid_position_calibration()
                elif command == "lid_position_offset_save":
                    await client.lid_position_offset_save()
                elif command == "lid_position_offset_increment":
                    await client.lid_position_offset_increment()
                elif command == "lid_position_offset_decrement":
                    await client.lid_position_offset_decrement()
                elif command == "reset_filter_counter":
                    await client.reset_filter_counter()
                elif command == "sync_rtc":
                    await client.sync_rtc()
                elif command == "restart_alba_device":
                    await client.restart_device()
                else:
                    _LOGGER.warning("Unknown command: %s", command)
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host and not self._use_ha_bluetooth:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect_ble_only()
                    except Exception:
                        _LOGGER.debug("disconnect_ble_only failed after command; resetting connector")
                        self._reset_esphome_connector()
                else:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect()
                    except Exception:
                        pass

    async def async_execute_alba_command(self, command: str, value: int = 0) -> None:
        """Execute an Alba-specific command that requires a value parameter."""
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import ESPHomeConnectionError

        async with self._ble_lock:
            if self._esphome_host and not self._use_ha_bluetooth:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
                if self._device_type == "alba":
                    await client.connect_ble_only(self._device_id, inventory=self._alba_inventory or None)
                else:
                    await client.connect_ble_only(self._device_id)
                if command == "start_stop_spray_arm_cleaning":
                    await client.start_stop_spray_arm_cleaning(value)
                elif command == "set_active_intensity":
                    await client.set_active_intensity(value)
                elif command == "set_active_position":
                    await client.set_active_position(value)
                elif command == "set_active_temperature":
                    await client.set_active_temperature(value)
                elif command == "set_active_oscillation":
                    await client.set_active_oscillation(value)
                else:
                    _LOGGER.warning("Unknown Alba command: %s", command)
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host and not self._use_ha_bluetooth:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect_ble_only()
                    except Exception:
                        _LOGGER.debug("disconnect_ble_only failed after alba command; resetting connector")
                        self._reset_esphome_connector()
                else:
                    try:
                        async with asyncio.timeout(5.0):
                            await connector.disconnect()
                    except Exception:
                        pass
