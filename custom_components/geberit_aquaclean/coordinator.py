"""DataUpdateCoordinator — polls the AquaClean device on-demand over BLE."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import time

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
        self._esphome_name_cache: str | None = None  # last known ESPHome device name
        self._ble_name_cache: str | None = None      # last known BLE device name
        self.ble_connected_at: datetime | None = None  # timestamp of last successful BLE connect
        # Performance statistics (HACS-only; standalone bridge uses PollStats class)
        self._last_connect_ms: int | None = None
        self._last_poll_ms: int | None = None
        self._stat_count: int = 0
        self._connect_total_ms: float = 0.0
        self._poll_total_ms: float = 0.0
        # RSSI statistics (min/avg per session)
        self._ble_rssi_count: int = 0
        self._ble_rssi_total: float = 0.0
        self._ble_rssi_min: float | None = None
        self._wifi_rssi_count: int = 0
        self._wifi_rssi_total: float = 0.0
        self._wifi_rssi_min: float | None = None
        # Transport type: "bleak" | "esp32-wifi" | "esp32-eth" — set on first successful poll
        self._transport: str | None = None

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

        poll_start = datetime.now(timezone.utc)
        connector = self._make_connector()
        client = AquaCleanClientFactory(connector).create_client()

        try:
            t_connect = time.perf_counter()
            await client.connect_ble_only(self._device_id)
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
            if esphome_wifi_rssi is not None:
                self._wifi_rssi_count += 1
                self._wifi_rssi_total += esphome_wifi_rssi
                if self._wifi_rssi_min is None or esphome_wifi_rssi < self._wifi_rssi_min:
                    self._wifi_rssi_min = esphome_wifi_rssi

            t_poll = time.perf_counter()
            ident = await client.base_client.get_device_identification_async(0)
            initial_op_date = await client.base_client.get_device_initial_operation_date()
            state = await client.base_client.get_system_parameter_list_async(
                [0, 1, 2, 3, 4, 5, 7, 9]
            )
            stats = await client.base_client.get_statistics_descale_async()
            soc_versions = await client.base_client.get_soc_application_versions_async()
            poll_ms = int((time.perf_counter() - t_poll) * 1000)
            self._last_poll_ms = poll_ms

            # Accumulate rolling performance stats
            self._stat_count += 1
            self._connect_total_ms += connect_ms
            self._poll_total_ms += poll_ms

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
                "stat_count": self._stat_count,
                # RSSI statistics
                "avg_ble_rssi": round(self._ble_rssi_total / self._ble_rssi_count, 1) if self._ble_rssi_count else None,
                "min_ble_rssi": self._ble_rssi_min,
                "avg_wifi_rssi": round(self._wifi_rssi_total / self._wifi_rssi_count, 1) if self._wifi_rssi_count else None,
                "min_wifi_rssi": self._wifi_rssi_min,
                # Transport type
                "transport": self._transport,
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

    async def async_restart_esp32(self) -> None:
        """Trigger a software reboot of the ESP32 proxy by pressing its restart button entity."""
        if not self._esphome_host:
            raise ValueError("No ESPHome host configured")
        connector = self._make_connector()
        await connector.restart_esp32_async()

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
