"""DataUpdateCoordinator — polls the AquaClean device on-demand over BLE."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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

    ESPHome proxy path — persistent TCP:
      The ESP32 API TCP connection is kept alive between polls (same behaviour as the
      standalone bridge's esphome_api_connection=persistent mode).  Only the BLE GATT
      link to the Geberit is opened and closed each cycle.  This keeps the ESP32's BLE
      scanner warm, so the next poll finds the device almost immediately even if it
      briefly stopped advertising after a command (e.g. ToggleLid).

    Local BLE path:
      A fresh BluetoothLeConnector is created per poll (unchanged from before).

    Commands (toggle_lid, …) reuse the same persistent connector as polls via
    async_execute_command(), which is serialised with _ble_lock to prevent two
    concurrent bluetooth_device_connect() calls from crashing the ESP32.

    Circuit breaker:
      After _CIRCUIT_OPEN_THRESHOLD consecutive failures the circuit opens.
      If an ESPHome host is configured, an ESP32 restart is attempted immediately.
      While open, each poll waits an extra _CIRCUIT_OPEN_PROBE_SLEEP seconds before
      attempting to connect, reducing hammering of an unresponsive ESP32.
      The circuit closes automatically on the next successful poll.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # options (from options flow) take precedence over data (from initial config flow)
        conf = {**entry.data, **entry.options}
        self._device_id: str = conf[CONF_DEVICE_ID]
        self._esphome_host: str | None = conf.get(CONF_ESPHOME_HOST) or None
        self._esphome_port: int = conf.get(CONF_ESPHOME_PORT, DEFAULT_ESPHOME_PORT)
        self._noise_psk: str | None = conf.get(CONF_NOISE_PSK) or None
        poll_interval: int = conf.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        self._esphome_name_cache: str | None = None  # last known ESPHome device name
        self._ble_name_cache: str | None = None      # last known BLE device name
        self.ble_connected_at: datetime | None = None  # timestamp of last successful BLE connect
        # Performance statistics (HACS-only; standalone bridge uses PollStats class)
        self._last_connect_ms: int | None = None
        self._last_poll_ms: int | None = None
        self._stat_count: int = 0
        self._connect_total_ms: float = 0.0
        self._poll_total_ms: float = 0.0
        self._connect_min_ms: float | None = None
        self._connect_max_ms: float | None = None
        self._poll_min_ms: float | None = None
        self._poll_max_ms: float | None = None
        # RSSI statistics (min/avg per session)
        self._ble_rssi_count: int = 0
        self._ble_rssi_total: float = 0.0
        self._ble_rssi_min: float | None = None
        self._ble_rssi_max: float | None = None
        self._wifi_rssi_count: int = 0
        self._wifi_rssi_total: float = 0.0
        self._wifi_rssi_min: float | None = None
        self._wifi_rssi_max: float | None = None
        # Transport type: "bleak" | "esp32-wifi" | "esp32-eth" — set on first successful poll
        self._transport: str | None = None
        # Last error — cleared on success, set on failure; exposed via binary sensor attributes
        self.last_error_code: str | None = None
        self.last_error_hint: str | None = None
        # Circuit breaker state
        self._consecutive_failures: int = 0
        self._circuit_open: bool = False
        # Serialises all BLE operations so poll and button-press never run concurrently.
        # With bleak, BlueZ transparently shares a single physical BLE connection across
        # concurrent BleakClient instances.  The ESPHome proxy cannot do this — each
        # bluetooth_device_connect() creates a new physical GATT connection, so two
        # simultaneous requests overwhelm the ESP32.
        self._ble_lock = asyncio.Lock()
        # Persistent ESPHome connector — reused across polls and commands to keep the
        # ESP32 API TCP connection alive (warm BLE scanner, fast reconnect after commands).
        # None when the local BLE path is used or after a TCP-level failure.
        self._esphome_connector = None   # BluetoothLeConnector
        self._esphome_client = None      # AquaCleanClient paired with _esphome_connector

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )

        try:
            from importlib.metadata import version as _pkg_version
            _LOGGER.info("geberit-aquaclean package version: %s", _pkg_version("geberit-aquaclean"))
        except Exception:
            _LOGGER.info("geberit-aquaclean package version: unknown")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_connector(self):
        """Create a fresh BluetoothLeConnector for the local BLE path."""
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            BluetoothLeConnector,
        )
        # Pass hass only for the local BLE path so BluetoothLeConnector uses HA's
        # bluetooth stack (habluetooth / bleak_retry_connector) instead of raw bleak.
        # The ESPHome path never calls _connect_local(), so hass is irrelevant there.
        ha = self.hass if not self._esphome_host else None
        return BluetoothLeConnector(self._esphome_host, self._esphome_port, self._noise_psk, hass=ha)

    def _get_esphome_connector(self):
        """Return the persistent ESPHome connector+client, creating them if needed.

        The connector is reused across polls so its internal aioesphomeapi TCP connection
        stays alive between cycles.  _ensure_esphome_api_connected() (inside the connector)
        handles reconnection if the TCP link has gone stale (ping timeout, etc.).
        """
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import BluetoothLeConnector
        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import AquaCleanClientFactory

        if self._esphome_connector is None:
            self._esphome_connector = BluetoothLeConnector(
                self._esphome_host, self._esphome_port, self._noise_psk, hass=None
            )
            self._esphome_client = AquaCleanClientFactory(self._esphome_connector).create_client()
            _LOGGER.debug("Created persistent ESPHome connector")
        return self._esphome_connector, self._esphome_client

    def _reset_esphome_connector(self) -> None:
        """Discard the persistent ESPHome connector so the next poll starts fresh.

        Called after unrecoverable TCP-level failures (ESPHomeConnectionError) so the
        next poll does not try to reuse a dead TCP connection.
        """
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
        # When circuit is open: extra sleep before probing to reduce ESP32 hammering.
        # Sleep happens OUTSIDE the lock so a pending command can still run during backoff.
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
                    # Circuit just opened — attempt ESP32 restart
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
                            "Failed to send ESP32 restart command (ESP32 may already be rebooting): %s",
                            restart_exc,
                        )
                elif self._consecutive_failures == _CIRCUIT_OPEN_THRESHOLD and not self._esphome_host:
                    # No ESP32 — open circuit for backoff only, no restart possible
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
                # Success — close circuit if it was open
                if self._consecutive_failures > 0:
                    _LOGGER.info(
                        "Poll recovered after %d consecutive failure(s)", self._consecutive_failures
                    )
                self._consecutive_failures = 0
                self._circuit_open = False
                return result

    async def _do_poll(self) -> dict:
        """Connect, fetch all device data, disconnect. Raises UpdateFailed on any error."""
        from bleak import BleakError

        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import (
            AquaCleanClientFactory,
        )
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            ESPHomeConnectionError,
            ESPHomeDeviceNotFoundError,
        )
        from aquaclean_console_app.ErrorCodes import E0002, E0003, E0004, E1002, E7002

        poll_start = datetime.now(timezone.utc)

        # ESPHome path: reuse persistent connector (keeps ESP32 TCP alive between polls).
        # Local BLE path: fresh connector per poll (unchanged).
        if self._esphome_host:
            connector, client = self._get_esphome_connector()
        else:
            connector = self._make_connector()
            client = AquaCleanClientFactory(connector).create_client()

        try:
            # ── Phase 1: connect (ESP32 TCP reuse or fresh + BLE scan + BLE GATT) ──
            t_connect = time.perf_counter()
            try:
                await client.connect_ble_only(self._device_id)
            except ESPHomeConnectionError as exc:
                # TCP-level failure — discard persistent connector so next poll reconnects.
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
                # Covers aioesphomeapi exceptions not subclassing the types above
                # (e.g. GATT notify setup timeout: "Timeout waiting for
                #  BluetoothGATTNotifyResponse … after 10.0s" raised by
                #  aioesphomeapi during bluetooth_gatt_start_notify).
                # All Phase-1 exceptions represent a connection-level failure → E0003.
                self._set_error(E0003)
                raise UpdateFailed(f"{E0003.code} — {E0003.message}: {exc}") from exc

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

            # Transport detection: bleak / esp32-wifi / esp32-eth
            if self._esphome_host:
                self._transport = "esp32-wifi" if esphome_wifi_rssi is not None else "esp32-eth"
            else:
                self._transport = "bleak"

            # Accumulate RSSI stats
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

            # ── Phase 2: GATT data fetch ─────────────────────────────────────
            # 30 s hard timeout: the underlying event-based calls have no
            # built-in timeout and will hang forever if the device ACKs the
            # request but never sends the response (e.g. rapid reconnect after
            # config-flow test).  asyncio.TimeoutError is already in the except.
            _GATT_TIMEOUT = 30
            t_poll = time.perf_counter()
            try:
                async with asyncio.timeout(_GATT_TIMEOUT):
                    ident = await client.base_client.get_device_identification_async(0)
                    initial_op_date = await client.base_client.get_device_initial_operation_date()
                    state = await client.base_client.get_system_parameter_list_async(
                        [0, 1, 2, 3, 4, 5, 7, 9]
                    )
                    stats = await client.base_client.get_statistics_descale_async()
                    soc_versions = await client.base_client.get_soc_application_versions_async()
                    firmware_versions = await client.base_client.get_firmware_version_list_async()
                    filter_status = await client.base_client.get_filter_status_async()
            except (BleakError, asyncio.TimeoutError, BLEPeripheralTimeoutError) as exc:
                self._set_error(E0003)
                raise UpdateFailed(f"{E0003.code} — {E0003.message}: {exc}") from exc
            except Exception as exc:
                # Unexpected error during GATT fetch (service not found, decode error, etc.)
                ec = E0004 if "service" in str(exc).lower() or "gatt" in str(exc).lower() else E7002
                self._set_error(ec)
                raise UpdateFailed(f"{ec.code} — {ec.message}: {exc}") from exc

            poll_ms = int((time.perf_counter() - t_poll) * 1000)
            self._last_poll_ms = poll_ms

            # Accumulate rolling performance stats
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

            # Poll succeeded — clear any previous error
            self._clear_error()

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
                "date_time_at_last_descale": (
                    datetime.fromtimestamp(stats.date_time_at_last_descale).strftime("%d.%m.%Y")
                    if stats.date_time_at_last_descale and stats.date_time_at_last_descale > 0
                    else "Never"
                ),
                "unposted_shower_cycles": stats.unposted_shower_cycles,
                "soc_versions": str(soc_versions) if soc_versions else None,
                # Firmware version
                "firmware_version": (firmware_versions or {}).get("main"),
                # Filter / honeycomb status
                "filter_days_remaining": (filter_status or {}).get("days_until_filter_change"),
                "filter_last_reset": (
                    datetime.fromtimestamp(ts, tz=timezone.utc)
                    if (ts := (filter_status or {}).get("last_filter_reset")) and ts > 0
                    else None
                ),
                "filter_reset_count": (filter_status or {}).get("filter_reset_count"),
                # Poll timing (for countdown visualization)
                "poll_epoch": poll_start,
                "poll_interval": self.update_interval.total_seconds(),
                "next_poll": datetime.now(timezone.utc) + self.update_interval,
                # ESPHome proxy info (for connection status sensors)
                "esphome_name": self._esphome_name_cache,
                # BLE device info (for connection status sensors)
                "ble_name": self._ble_name_cache,
                # Signal strength
                "ble_rssi": ble_rssi,
                "esphome_wifi_rssi": esphome_wifi_rssi,
                # ESP32 memory diagnostics
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
                # Transport type
                "transport": self._transport,
            }

        except UpdateFailed:
            raise  # already classified above
        except Exception as exc:
            # Safety net for anything not caught by the phase handlers
            from aquaclean_console_app.ErrorCodes import E7002
            self._set_error(E7002)
            raise UpdateFailed(f"{E7002.code} — {E7002.message}: {exc}") from exc
        finally:
            if self._esphome_host:
                # Keep ESP32 TCP alive — only tear down the BLE GATT link.
                try:
                    async with asyncio.timeout(5.0):
                        await connector.disconnect_ble_only()
                except Exception:
                    # disconnect_ble_only failed; TCP is likely dead — reset for next poll.
                    _LOGGER.debug("disconnect_ble_only failed; resetting persistent ESPHome connector")
                    self._reset_esphome_connector()
            else:
                # Local BLE: full disconnect (no persistent state to preserve).
                try:
                    async with asyncio.timeout(5.0):
                        await connector.disconnect()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Command execution (buttons)
    # ------------------------------------------------------------------

    async def async_restart_esp32(self) -> None:
        """Trigger a software reboot of the ESP32 proxy by pressing its restart button entity."""
        if not self._esphome_host:
            raise ValueError("No ESPHome host configured")
        connector = self._make_connector()
        await connector.restart_esp32_async()

    async def async_execute_command(self, command: str) -> None:
        """Execute a device command via BLE, serialised with the poll loop via _ble_lock.

        ESPHome path: reuses the persistent connector so TCP stays warm.
        Local BLE path: fresh connector per command.
        """
        from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory import (
            AquaCleanClientFactory,
        )
        from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector import (
            ESPHomeConnectionError,
        )

        async with self._ble_lock:
            if self._esphome_host:
                connector, client = self._get_esphome_connector()
            else:
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
