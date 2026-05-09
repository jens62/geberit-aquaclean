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
    DEFAULT_ESPHOME_PORT,
    DEFAULT_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Circuit breaker constants — match standalone bridge behaviour
_CIRCUIT_OPEN_THRESHOLD = 5    # consecutive failures before opening circuit
_CIRCUIT_OPEN_PROBE_SLEEP = 60  # extra seconds before each probe when circuit is open
_ESP32_RESTART_SLEEP = 30       # seconds to wait after sending ESP32 restart command


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
        poll_interval: int = conf.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._esphome_name_cache: str | None = None
        self._ble_name_cache: str | None = None
        self.ble_connected_at: datetime | None = None
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
        self._unsupported_device = False

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
        """Create a fresh BluetoothLeConnector for the local BLE path."""
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
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
        """Full disconnect of the persistent ESPHome connector on integration unload."""
        if self._esphome_connector is not None:
            _LOGGER.debug("Closing persistent ESPHome connector on unload")
            try:
                async with asyncio.timeout(5.0):
                    await self._esphome_connector.disconnect()
            except Exception:
                pass
            self._esphome_connector = None
            self._esphome_client = None

    # ------------------------------------------------------------------
    # DataUpdateCoordinator protocol
    # ------------------------------------------------------------------

    def _set_error(self, ec) -> None:
        self.last_error_code = ec.code
        self.last_error_hint = ec.hint

    def _clear_error(self) -> None:
        self.last_error_code = None
        self.last_error_hint = None

    async def _async_update_data(self) -> dict:
        """Circuit-breaker wrapper: tracks consecutive failures, triggers ESP32 restart."""
        if self._unsupported_device:
            from aquaclean_console_app.ErrorCodes import E0010
            raise UpdateFailed(f"{E0010.code} — {E0010.message}")

        if self._circuit_open and self._esphome_host:
            _LOGGER.debug(
                "Circuit open (%d consecutive failures) — sleeping %d s before probe",
                self._consecutive_failures,
                _CIRCUIT_OPEN_PROBE_SLEEP,
            )
            await asyncio.sleep(_CIRCUIT_OPEN_PROBE_SLEEP)

        async with self._ble_lock:
            try:
                result = await self._do_poll()
            except UpdateFailed:
                self._consecutive_failures += 1

                if self._consecutive_failures == _CIRCUIT_OPEN_THRESHOLD and self._esphome_host:
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
                elif self._consecutive_failures == _CIRCUIT_OPEN_THRESHOLD and not self._esphome_host:
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

        if self._esphome_host:
            connector = self._get_esphome_connector()
        else:
            connector = self._make_connector()

        try:
            # ── Phase 1: connect ──────────────────────────────────────────────
            t_connect = time.perf_counter()
            try:
                if self._device_type == "alba":
                    # Alba: connect_ble_only = connect_async + post_connect (DataPointInventory)
                    if self._esphome_host:
                        client = self._ensure_esphome_client(connector)
                    else:
                        from aquaclean_console_app.aquaclean_core.Clients.AlbaClient import AlbaClient
                        client = AlbaClient(connector)
                    await client.connect_ble_only(self._device_id)

                elif self._device_type == "mera":
                    if self._esphome_host:
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
                        if self._esphome_host:
                            self._esphome_client = client
                        await client.post_connect()  # DataPointInventory — mandatory first step
                        _LOGGER.info("Detected AquaClean Alba (Ble20) device — using Alba protocol")

                    elif not connector.is_variant_a:
                        # AquaClean Mera Comfort (original protocol)
                        self._device_type = "mera"
                        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory
                        client = AquaCleanClientFactory(connector).create_client()
                        if self._esphome_host:
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

            if connector.esphome_proxy_name:
                self._esphome_name_cache = connector.esphome_proxy_name
            if connector.device_name and connector.device_name != "Unknown":
                self._ble_name_cache = connector.device_name
            ble_rssi = connector.rssi
            esphome_wifi_rssi = connector.esphome_wifi_rssi
            esphome_free_heap = connector.esphome_free_heap
            esphome_max_free_block = connector.esphome_max_free_block

            if self._esphome_host:
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
            }

        except UpdateFailed:
            raise
        except Exception as exc:
            from aquaclean_console_app.ErrorCodes import E7002
            self._set_error(E7002)
            raise UpdateFailed(f"{E7002.code} — {E7002.message}: {exc}") from exc
        finally:
            if self._esphome_host:
                try:
                    async with asyncio.timeout(5.0):
                        await connector.disconnect_ble_only()
                except Exception:
                    _LOGGER.debug("disconnect_ble_only failed; resetting persistent ESPHome connector")
                    self._reset_esphome_connector()
            else:
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
            [0, 1, 2, 3, 4, 5, 6, 7, 9]
        )
        stats = await client.base_client.get_statistics_descale_async()
        soc_versions = await client.base_client.get_soc_application_versions_async()
        firmware_versions = await client.base_client.get_firmware_version_list_async()
        filter_status = await client.base_client.get_filter_status_async()
        profile_settings = await client.base_client.get_stored_profile_settings_async()
        common_settings = await client.base_client.get_stored_common_settings_async()

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
        """Phase 2 data fetch for AquaClean Alba (Ble20) devices."""
        ident = await client.base_client.get_device_identification_async(0)
        state = await client.base_client.get_system_parameter_list_async([0, 1, 2, 3])
        misc  = await client.base_client.get_misc_state_async()
        profile_settings = await client.base_client.get_stored_profile_settings_async()

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
            "firmware_version": None,
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
            if self._esphome_host:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
                await client.connect_ble_only(self._device_id)
                await client.set_stored_profile_setting(setting_id, value)
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host:
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
            if self._esphome_host:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
                await client.connect_ble_only(self._device_id)
                await client.set_stored_common_setting(setting_id, value)
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host:
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
            if self._esphome_host:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
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
                else:
                    _LOGGER.warning("Unknown command: %s", command)
            except ESPHomeConnectionError:
                self._reset_esphome_connector()
                raise
            finally:
                if self._esphome_host:
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
            if self._esphome_host:
                connector = self._get_esphome_connector()
                client = self._ensure_esphome_client(connector)
            else:
                connector = self._make_connector()
                client = self._make_local_client(connector)

            try:
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
                if self._esphome_host:
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
