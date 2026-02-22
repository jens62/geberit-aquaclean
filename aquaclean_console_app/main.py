import json
import asyncio
import logging
import os
import configparser
import argparse
import traceback
import sys
import time
from datetime import datetime
from queue  import Queue, Empty
from aiorun import run, shutdown_waits_for
from haggis import logs

from bleak import BleakScanner
from bleak.exc import BleakError
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanClient                   import AquaCleanClient
from aquaclean_console_app.aquaclean_core.Clients.AquaCleanBaseClient               import BLEPeripheralTimeoutError
from aquaclean_console_app.aquaclean_core.IAquaCleanClient                          import IAquaCleanClient
from aquaclean_console_app.aquaclean_core.AquaCleanClientFactory                    import AquaCleanClientFactory
from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos.DeviceIdentification import DeviceIdentification
from aquaclean_console_app.aquaclean_core.Message.MessageService                    import MessageService
from aquaclean_console_app.aquaclean_core.IBluetoothLeConnector                     import IBluetoothLeConnector
from aquaclean_console_app.bluetooth_le.LE.BluetoothLeConnector                     import BluetoothLeConnector, ESPHomeConnectionError, ESPHomeDeviceNotFoundError
from aquaclean_console_app.MqttService                                              import MqttService as Mqtt
from aquaclean_console_app.RestApiService                                           import RestApiService
from aquaclean_console_app.myEvent                                                  import myEvent
from aquaclean_console_app.aquaclean_utils                                          import utils
from aquaclean_console_app.ErrorCodes                                               import (
    ErrorManager, E0000, E0001, E0002, E0003, E1001, E1002,
    E2001, E2002, E2003, E2004, E2005,
    E3002, E3003, E4001, E4002, E4003, E7002, E7004
)

# --- Configuration & Logging Setup ---
__location__ = os.path.dirname(os.path.abspath(__file__))
iniFile = os.path.join(__location__, 'config.ini')
config = configparser.ConfigParser(allow_no_value=False, inline_comment_prefixes=('#',))
config.read(iniFile)

logs.add_logging_level('TRACE', logging.DEBUG - 5)
logs.add_logging_level('SILLY', logging.DEBUG - 7)

log_level              = config.get("LOGGING",  "log_level",  fallback="DEBUG")
esphome_host           = config.get("ESPHOME",  "host",       fallback=None) or None
esphome_port           = int(config.get("ESPHOME", "port",    fallback="6053"))
esphome_noise_psk      = config.get("ESPHOME",  "noise_psk",  fallback=None) or None
esphome_log_streaming  = config.getboolean("ESPHOME", "log_streaming", fallback=False)
esphome_log_level      = config.get("ESPHOME", "log_level", fallback="INFO")
esphome_api_connection = config.get("ESPHOME", "esphome_api_connection", fallback="on-demand")
logging.basicConfig(level=log_level, format="%(asctime)-15s %(name)-8s %(lineno)d %(levelname)s: %(message)s")

# Suppress verbose external library logging (but not when explicitly debugging at TRACE/SILLY)
if log_level not in ('TRACE', 'SILLY'):
    logging.getLogger("aioesphomeapi.connection").setLevel(logging.INFO)
    logging.getLogger("aioesphomeapi._frame_helper.base").setLevel(logging.INFO)

logger = logging.getLogger(__name__)


def _log_startup_config():
    """Log all config.ini values at INFO level for post-hoc debugging."""
    _REDACTED = {'noise_psk', 'password'}
    lines = [f"Configuration ({iniFile}):"]
    for section in config.sections():
        for key, value in config.items(section):
            display = '***' if key in _REDACTED else value
            lines.append(f"  [{section}] {key} = {display}")
    if not config.sections():
        lines.append("  (no sections found — using defaults)")
    logger.info('\n'.join(lines))


def _check_config_errors() -> list[str]:
    """Return a list of configuration error strings. Empty list means config is valid."""
    import re
    errors = []

    # [BLE] device_id — required, MAC address format
    try:
        device_id = config.get("BLE", "device_id")
        if not re.match(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$', device_id):
            errors.append(
                f"[BLE] device_id={device_id!r} — expected MAC address XX:XX:XX:XX:XX:XX"
            )
    except Exception:
        errors.append("[BLE] device_id is missing (required)")

    # [SERVICE] ble_connection — enum
    ble_connection = config.get("SERVICE", "ble_connection", fallback="persistent")
    if ble_connection not in ("persistent", "on-demand"):
        errors.append(
            f"[SERVICE] ble_connection={ble_connection!r} — must be 'persistent' or 'on-demand'"
        )

    # [ESPHOME] esphome_api_connection — enum
    esphome_api_conn = config.get("ESPHOME", "esphome_api_connection", fallback="on-demand")
    if esphome_api_conn not in ("persistent", "on-demand"):
        errors.append(
            f"[ESPHOME] esphome_api_connection={esphome_api_conn!r} — must be 'persistent' or 'on-demand'"
        )

    # [ESPHOME] port — integer
    try:
        port = int(config.get("ESPHOME", "port", fallback="6053"))
        if not (1 <= port <= 65535):
            errors.append(f"[ESPHOME] port={port} — must be 1–65535")
    except ValueError:
        errors.append(f"[ESPHOME] port={config.get('ESPHOME', 'port', fallback='')!r} — must be an integer")

    # [API] port — integer
    try:
        api_port = int(config.get("API", "port", fallback="8080"))
        if not (1 <= api_port <= 65535):
            errors.append(f"[API] port={api_port} — must be 1–65535")
    except ValueError:
        errors.append(f"[API] port={config.get('API', 'port', fallback='')!r} — must be an integer")

    # [POLL] interval — non-negative float
    try:
        interval = float(config.get("POLL", "interval", fallback="0"))
        if interval < 0:
            errors.append(f"[POLL] interval={interval} — must be >= 0")
    except ValueError:
        errors.append(f"[POLL] interval={config.get('POLL', 'interval', fallback='')!r} — must be a number")

    # [LOGGING] log_level — known level
    valid_levels = {"SILLY", "TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    log_level = config.get("LOGGING", "log_level", fallback="DEBUG").upper()
    if log_level not in valid_levels:
        errors.append(
            f"[LOGGING] log_level={log_level!r} — must be one of {sorted(valid_levels)}"
        )

    # [ESPHOME] log_level — known level
    esphome_log_level = config.get("ESPHOME", "log_level", fallback="INFO").upper()
    if esphome_log_level not in valid_levels:
        errors.append(
            f"[ESPHOME] log_level={esphome_log_level!r} — must be one of {sorted(valid_levels)}"
        )

    return errors


def get_full_class_name(obj):
    module = obj.__class__.__module__
    if module is None or module == str.__class__.__module__:
        return obj.__class__.__name__
    return module + '.' + obj.__class__.__name__


class ManualReconnectRequested(Exception):
    """Raised to trigger a clean BLE reconnect without the timeout recovery protocol."""
    pass


class NullMqttService:
    """Drop-in replacement for MqttService when MQTT is disabled."""
    def __init__(self):
        self.ToggleLidPosition = myEvent.EventHandler()
        self.Connect           = myEvent.EventHandler()
        self.ToggleAnal        = myEvent.EventHandler()
        self.SetBleConnection  = myEvent.EventHandler()
        self.SetPollInterval   = myEvent.EventHandler()
        self.Disconnect        = myEvent.EventHandler()
        self.ConnectESP32      = myEvent.EventHandler()
        self.DisconnectESP32         = myEvent.EventHandler()

    async def start_async(self, loop, queue):
        queue.put("initialized")

    async def send_data_async(self, topic, value):
        pass

    def stop(self):
        pass


class ServiceMode:
    def __init__(self, mqtt_enabled=True, shutdown_event: asyncio.Event | None = None):
        self.client = None
        self.mqtt_initialized_wait_queue = Queue()
        self.device_state = {
            "is_user_sitting": None,
            "is_anal_shower_running": None,
            "is_lady_shower_running": None,
            "is_dryer_running": None,
            "ble_status": "disconnected",   # connecting | connected | disconnected | error
            "ble_connected_at": None,        # ISO timestamp string
            "ble_device_name": None,         # from client.Description
            "ble_device_address": None,      # BLE address from config
            "ble_error": None,               # error message when ble_status == "error"
            "ble_error_code": None,          # error code (E0001-E7999) when ble_status == "error"
            "ble_error_hint": None,          # user-facing resolution hint when ble_status == "error"
            "last_connect_ms": None,         # duration of last BLE connect in ms (total)
            "last_esphome_api_ms": None,     # portion: ESP32 API TCP connect (None = local BLE, 0 = reused)
            "last_ble_ms": None,             # portion: BLE scan + handshake to toilet
            "last_poll_ms": None,            # duration of last GetSystemParameterList in ms
            # Device identification — populated on first on-demand poll, cached for /info endpoint
            "sap_number": None,
            "serial_number": None,
            "production_date": None,
            "description": None,
            "initial_operation_date": None,
        }
        self.esphome_proxy_state = {
            "enabled": esphome_host is not None,
            "connected": False,
            "name": "",
            "host": esphome_host or "",
            "port": esphome_port if esphome_host else "",
            "error": "No error",
            "error_code": "E0000",
            "error_hint": "",
        }
        self._reconnect_requested = asyncio.Event()
        self._poll_interval_event = asyncio.Event()  # set by set_poll_interval() in persistent mode
        self._connection_allowed = asyncio.Event()
        self._connection_allowed.set()  # auto-connect on startup
        self._shutdown_event = shutdown_event or asyncio.Event()
        self.on_state_updated = None  # Optional async callback(state_dict)
        self._esphome_log_api = None  # Persistent API connection for log streaming
        self._esphome_log_unsub = None  # Log unsubscribe function

        if mqtt_enabled:
            self.mqttConfig = dict(config.items('MQTT'))
            self.mqtt_service = Mqtt(self.mqttConfig)
        else:
            self.mqttConfig = {"topic": config.get("MQTT", "topic", fallback="Geberit/AquaClean")}
            self.mqtt_service = NullMqttService()

    async def run(self):
        # 1. Initialize MQTT with wait queue (once)
        await self.mqtt_service.start_async(asyncio.get_running_loop(), self.mqtt_initialized_wait_queue)
        count = 50
        while count > 0:
            try:
                self.mqtt_initialized_wait_queue.get(timeout=0.1)
                break
            except Empty:
                pass
            count -= 1
            await asyncio.sleep(0.1)

        device_id = config.get("BLE", "device_id")
        try:
            interval = float(config.get("POLL", "interval"))
        except Exception:
            interval = 2.5
        self.device_state["poll_interval"] = interval

        # Subscribe MQTT handlers once — handlers reference self.client
        # which is updated each iteration of the recovery loop below
        self.mqtt_service.ToggleLidPosition += self.on_toggle_lid_message
        self.mqtt_service.Connect += self.request_reconnect

        # Publish initial ESPHome proxy status and Home Assistant discovery
        await self._publish_esphome_proxy_status()
        await self._publish_esphome_proxy_discovery()
        logger.debug(f"ESPHome proxy mode: enabled={self.esphome_proxy_state['enabled']}, host={self.esphome_proxy_state['host']}")

        # Start ESPHome log streaming if enabled
        await self._start_esphome_log_streaming()

        # --- Main Recovery Loop ---
        while not self._shutdown_event.is_set():
            # If disconnect was requested, wait here until reconnect is allowed.
            if not self._connection_allowed.is_set():
                allowed_task = asyncio.create_task(self._connection_allowed.wait())
                shutdown_task = asyncio.create_task(self._shutdown_event.wait())
                await asyncio.wait(
                    [allowed_task, shutdown_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                allowed_task.cancel()
                shutdown_task.cancel()
                for t in (allowed_task, shutdown_task):
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass
                if self._shutdown_event.is_set():
                    break

            bluetooth_connector = BluetoothLeConnector(esphome_host, esphome_port, esphome_noise_psk)
            factory = AquaCleanClientFactory(bluetooth_connector)
            self.client = factory.create_client()

            self.client.DeviceStateChanged += self.on_device_state_changed
            self.client.SOCApplicationVersions += self.soc_application_versions
            self.client.DeviceInitialOperationDate += self.device_initial_operation_date
            self.client.DeviceIdentification += self.on_device_identification
            bluetooth_connector.connection_status_changed_handlers += self.on_connection_status_changed

            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", ErrorManager.clear_error())
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", f"Connecting to {device_id} ...")
            await self._set_ble_status("connecting", device_address=device_id)

            try:
                t0 = time.perf_counter()
                await self.client.connect(device_id)
                self.device_state["last_connect_ms"] = int((time.perf_counter() - t0) * 1000)
                self.device_state["last_esphome_api_ms"] = bluetooth_connector.last_esphome_api_ms
                self.device_state["last_ble_ms"] = bluetooth_connector.last_ble_ms
                await self._set_ble_status(
                    "connected",
                    device_name=self.client.Description,
                    device_address=device_id,
                )
                await self.mqtt_service.send_data_async(
                    f"{self.mqttConfig['topic']}/centralDevice/timings",
                    json.dumps({
                        "connect_ms": self.device_state["last_connect_ms"],
                        "esphome_api_ms": bluetooth_connector.last_esphome_api_ms,
                        "ble_ms": bluetooth_connector.last_ble_ms,
                    })
                )

                # Update ESPHome proxy status if connected via ESP32
                if bluetooth_connector.esphome_proxy_connected:
                    await self._update_esphome_proxy_state(
                        connected=True,
                        name=bluetooth_connector.esphome_proxy_name,
                        error="No error"
                    )

                # Record when polling starts so clients can compute a
                # deterministic countdown regardless of when they connect.
                self.device_state["poll_epoch"] = time.time()

                # Inner polling loop — stays within this BLE connection.
                # Reacts to poll-interval changes (set_poll_interval) without
                # disconnecting; only reconnect/shutdown break out via exception.
                shutdown_requested = False
                while True:
                    current_poll_interval = self.device_state.get("poll_interval", interval)

                    reconnect_task    = asyncio.create_task(self._reconnect_requested.wait())
                    shutdown_task     = asyncio.create_task(self._shutdown_event.wait())
                    poll_change_task  = asyncio.create_task(self._poll_interval_event.wait())

                    if current_poll_interval > 0:
                        polling_task = asyncio.create_task(
                            self.client.start_polling(current_poll_interval, on_poll_done=self._on_poll_done))
                        tasks = [polling_task, reconnect_task, shutdown_task, poll_change_task]
                    else:
                        polling_task = None
                        tasks = [reconnect_task, shutdown_task, poll_change_task]

                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                        try:
                            await t
                        except asyncio.CancelledError:
                            pass

                    if shutdown_task in done:
                        shutdown_requested = True
                        break  # exit inner loop; outer break below exits recovery loop

                    if reconnect_task in done:
                        self._reconnect_requested.clear()
                        raise ManualReconnectRequested()

                    if poll_change_task in done:
                        self._poll_interval_event.clear()
                        new_interval = self.device_state.get("poll_interval", interval)
                        if new_interval > 0:
                            # Reset countdown epoch when re-enabling polling
                            self.device_state["poll_epoch"] = time.time()
                        continue  # restart inner loop with new interval

                    # polling_task finished — re-raise its exception if any
                    if polling_task is not None:
                        exc = polling_task.exception() if not polling_task.cancelled() else None
                        if exc:
                            raise exc

                if shutdown_requested:
                    break  # exit recovery loop

            except ManualReconnectRequested:
                logger.info("Manual reconnect requested — reconnecting...")
                await self.mqtt_service.send_data_async(
                    f"{self.mqttConfig['topic']}/centralDevice/connected", "Reconnecting..."
                )
            except BLEPeripheralTimeoutError as e:
                logger.warning("BLE Timeout — initiating recovery protocol.")
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", ErrorManager.to_json(E0003, str(e)))
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                await self.wait_for_device_restart(device_id, bluetooth_connector)
            except ESPHomeConnectionError as e:
                # ESP32 TCP connection failed — Geberit was never reached.
                error_code_obj = E1001 if e.timeout else E1002
                msg = f"{e} — Check that the ESP32 is reachable at {esphome_host}:{esphome_port}"
                logger.warning(msg)
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
                await self._set_ble_status("error", error_msg=msg, error_code=error_code_obj.code, error_hint=error_code_obj.hint)
                await self._update_esphome_proxy_state(connected=False, error=str(e), error_code=error_code_obj.code, error_hint=error_code_obj.hint)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
            except ESPHomeDeviceNotFoundError as e:
                # ESP32 TCP connected fine, but Geberit not visible via BLE proxy.
                msg = (
                    f"{e} — "
                    "Try in order: "
                    "1) Power cycle the Geberit. "
                    "2) Move the ESP32 closer to the Geberit. "
                    "3) Restart the ESP32."
                )
                logger.warning(msg)
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", ErrorManager.to_json(E0002, msg))
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
                await self._set_ble_status("error", error_msg=msg, error_code=E0002.code, error_hint=E0002.hint)
                await self._update_esphome_proxy_state(connected=False, error=str(e), error_code=E0002.code, error_hint=E0002.hint)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
            except BleakError as e:
                # Generic local BLE error (no ESPHome involved).
                msg = (
                    f"{e} — "
                    "Try in order: "
                    "1) Power cycle the Geberit. "
                    "2) Restart the Bluetooth service on the host machine. "
                    "3) Restart the host machine."
                )
                logger.warning(msg)
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", ErrorManager.to_json(E0003, msg))
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
                await self._set_ble_status("error", error_msg=msg, error_code=E0003.code, error_hint=E0003.hint)
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
            except Exception as e:
                await self.handle_exception(e)
            finally:
                # On shutdown, publish disconnected status to MQTT BEFORE the
                # slow BLE disconnect (which may be cancelled by ApiMode).
                if self._shutdown_event.is_set():
                    await self.mqtt_service.send_data_async(
                        f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                await self._set_ble_status("disconnected")
                # Update ESP32 proxy disconnected state
                if esphome_host:
                    await self._update_esphome_proxy_state(connected=False, error="No error", error_code="E0000")

        # Recovery loop exited — stop log streaming and MQTT background thread
        await self._stop_esphome_log_streaming()
        self.mqtt_service.stop()

    async def _set_ble_status(self, status: str, device_name=None, device_address=None, error_msg=None, error_code=None, error_hint=None):
        self.device_state["ble_status"] = status
        if status == "connected":
            self.device_state["ble_connected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.device_state["ble_device_name"] = device_name
            self.device_state["ble_device_address"] = device_address
            self.device_state["ble_error"] = None
            self.device_state["ble_error_code"] = None
            self.device_state["ble_error_hint"] = None
        elif status == "error":
            self.device_state["ble_connected_at"] = None
            self.device_state["poll_epoch"] = None
            self.device_state["last_connect_ms"] = None
            self.device_state["last_esphome_api_ms"] = None
            self.device_state["last_ble_ms"] = None
            self.device_state["last_poll_ms"] = None
            self.device_state["ble_error"] = error_msg
            self.device_state["ble_error_code"] = error_code
            self.device_state["ble_error_hint"] = error_hint
            # Clear device info to prevent stale data display
            self.device_state["ble_device_name"] = None
            self.device_state["ble_device_address"] = None
        elif status == "connecting":
            self.device_state["ble_connected_at"] = None
            # Reset timing so the webapp shows fresh values once connected,
            # not stale values from the previous operation.
            self.device_state["last_connect_ms"] = None
            self.device_state["last_esphome_api_ms"] = None
            self.device_state["last_ble_ms"] = None
            self.device_state["last_poll_ms"] = None
            self.device_state["ble_error"] = None
            self.device_state["ble_error_code"] = None
            self.device_state["ble_error_hint"] = None
            self.device_state["ble_device_name"] = None
            self.device_state["ble_device_address"] = None
        elif status == "disconnected":
            self.device_state["ble_connected_at"] = None
            self.device_state["ble_error"] = None
            self.device_state["ble_error_code"] = None
            self.device_state["ble_error_hint"] = None
            self.device_state["ble_device_name"] = None
            self.device_state["ble_device_address"] = None
            # Do NOT clear timing or poll_epoch here — the last completed
            # operation's values should remain visible in the webapp until
            # the next operation starts (clearing on "connecting" handles that).
        if self.on_state_updated:
            await self.on_state_updated(self.device_state.copy())

    async def _on_poll_done(self, millis: int):
        self.device_state["last_poll_ms"] = millis
        # Persistent mode reuses the BLE connection — no reconnect cost per poll.
        self.device_state["last_connect_ms"] = 0
        self.device_state["last_esphome_api_ms"] = 0 if esphome_host else None
        self.device_state["last_ble_ms"] = 0 if esphome_host else None
        if self.on_state_updated:
            await self.on_state_updated(self.device_state.copy())

    async def _update_esphome_proxy_state(self, connected=None, name=None, error=None, error_code=None, error_hint=None):
        """Update ESPHome proxy state and publish to MQTT."""
        if connected is not None:
            self.esphome_proxy_state["connected"] = connected
        if name is not None:
            self.esphome_proxy_state["name"] = name
        if error is not None:
            self.esphome_proxy_state["error"] = error
        if error_code is not None:
            self.esphome_proxy_state["error_code"] = error_code
            if error_code == "E0000":
                self.esphome_proxy_state["error_hint"] = ""  # clear stale hint on success
        if error_hint is not None:
            self.esphome_proxy_state["error_hint"] = error_hint
        await self._publish_esphome_proxy_status()
        # Broadcast state change to SSE clients (webapp)
        if self.on_state_updated:
            state = dict(self.device_state)
            state.update({
                "esphome_proxy_enabled": self.esphome_proxy_state["enabled"],
                "esphome_proxy_connected": self.esphome_proxy_state["connected"],
                "esphome_proxy_name": self.esphome_proxy_state["name"],
                "esphome_proxy_host": self.esphome_proxy_state["host"],
                "esphome_proxy_port": self.esphome_proxy_state["port"],
                "esphome_proxy_error": self.esphome_proxy_state["error"],
                "esphome_proxy_error_code": self.esphome_proxy_state["error_code"],
                "esphome_proxy_error_hint": self.esphome_proxy_state.get("error_hint", ""),
            })
            await self.on_state_updated(state)

    async def _publish_esphome_proxy_status(self):
        """Publish ESPHome proxy status to MQTT."""
        topic = self.mqttConfig['topic']

        # Publish enabled status
        await self.mqtt_service.send_data_async(
            f"{topic}/esphomeProxy/enabled",
            str(self.esphome_proxy_state["enabled"]).lower()
        )

        # Publish connected status
        if self.esphome_proxy_state["enabled"]:
            if self.esphome_proxy_state["connected"] and self.esphome_proxy_state["name"]:
                conn_str = f"{self.esphome_proxy_state['name']} ({self.esphome_proxy_state['host']}:{self.esphome_proxy_state['port']})"
            else:
                conn_str = "false"
            await self.mqtt_service.send_data_async(
                f"{topic}/esphomeProxy/connected",
                conn_str
            )
        else:
            await self.mqtt_service.send_data_async(
                f"{topic}/esphomeProxy/connected",
                "false"
            )

        # Publish error status (JSON format matching centralDevice/error)
        error_code = self.esphome_proxy_state["error_code"]
        error_msg = self.esphome_proxy_state["error"]
        error_hint = self.esphome_proxy_state.get("error_hint", "")
        if error_code == "E0000":
            error_json = ErrorManager.clear_error()
        else:
            # Create temporary ErrorCode for JSON formatting
            from aquaclean_console_app.ErrorCodes import ErrorCode
            temp_error = ErrorCode(error_code, error_msg, "ESP32", "ERROR", error_hint)
            error_json = ErrorManager.to_json(temp_error, include_timestamp=True)
        await self.mqtt_service.send_data_async(
            f"{topic}/esphomeProxy/error",
            error_json
        )

    async def _publish_esphome_proxy_discovery(self):
        """Publish Home Assistant MQTT discovery for ESPHome proxy entities."""
        import json
        topic = self.mqttConfig['topic']
        device_id = config.get("BLE", "device_id").replace(":", "").lower()

        # Device information shared across all entities
        device_config = {
            "identifiers": [f"aquaclean_{device_id}"],
            "name": "Geberit AquaClean",
            "manufacturer": "Geberit",
            "model": "AquaClean Console"
        }

        # Binary sensor: ESPHome proxy enabled
        enabled_config = {
            "name": "ESPHome Proxy Enabled",
            "unique_id": f"aquaclean_{device_id}_esphome_proxy_enabled",
            "state_topic": f"{topic}/esphomeProxy/enabled",
            "payload_on": "true",
            "payload_off": "false",
            "device_class": "connectivity",
            "entity_category": "diagnostic",
            "device": device_config
        }
        await self.mqtt_service.send_data_async(
            f"homeassistant/binary_sensor/aquaclean_{device_id}/esphome_proxy_enabled/config",
            json.dumps(enabled_config)
        )

        # Binary sensor: ESPHome proxy connected
        connected_config = {
            "name": "ESPHome Proxy Connected",
            "unique_id": f"aquaclean_{device_id}_esphome_proxy_connected",
            "state_topic": f"{topic}/esphomeProxy/connected",
            "value_template": "{{ 'ON' if value != 'false' else 'OFF' }}",
            "device_class": "connectivity",
            "entity_category": "diagnostic",
            "device": device_config
        }
        await self.mqtt_service.send_data_async(
            f"homeassistant/binary_sensor/aquaclean_{device_id}/esphome_proxy_connected/config",
            json.dumps(connected_config)
        )

        # Sensor: ESPHome proxy connection string
        connection_config = {
            "name": "ESPHome Proxy Connection",
            "unique_id": f"aquaclean_{device_id}_esphome_proxy_connection",
            "state_topic": f"{topic}/esphomeProxy/connected",
            "entity_category": "diagnostic",
            "icon": "mdi:bluetooth-connect",
            "device": device_config
        }
        await self.mqtt_service.send_data_async(
            f"homeassistant/sensor/aquaclean_{device_id}/esphome_proxy_connection/config",
            json.dumps(connection_config)
        )

        # Sensor: ESPHome proxy error
        error_config = {
            "name": "ESPHome Proxy Error",
            "unique_id": f"aquaclean_{device_id}_esphome_proxy_error",
            "state_topic": f"{topic}/esphomeProxy/error",
            "entity_category": "diagnostic",
            "icon": "mdi:alert-circle",
            "device": device_config
        }
        await self.mqtt_service.send_data_async(
            f"homeassistant/sensor/aquaclean_{device_id}/esphome_proxy_error/config",
            json.dumps(error_config)
        )

    async def _start_esphome_log_streaming(self):
        """Subscribe to ESPHome device logs if log streaming is enabled."""
        logger.debug(f"ESPHome log streaming config: enabled={esphome_log_streaming}, host={esphome_host!r}, level={esphome_log_level!r}")

        if not esphome_log_streaming:
            logger.debug("ESPHome log streaming disabled in config (log_streaming = false or not set)")
            return

        if not esphome_host:
            logger.debug("ESPHome log streaming skipped: no host configured ([ESPHOME] host not set)")
            return

        from aioesphomeapi import APIClient, LogLevel

        # Map config log level string to aioesphomeapi LogLevel
        level_map = {
            "ERROR": LogLevel.LOG_LEVEL_ERROR,
            "WARN": LogLevel.LOG_LEVEL_WARN,
            "WARNING": LogLevel.LOG_LEVEL_WARN,
            "INFO": LogLevel.LOG_LEVEL_INFO,
            "DEBUG": LogLevel.LOG_LEVEL_DEBUG,
            "VERBOSE": LogLevel.LOG_LEVEL_VERBOSE,
        }
        log_level = level_map.get(esphome_log_level.upper(), LogLevel.LOG_LEVEL_INFO)

        try:
            logger.info(f"Starting ESPHome log streaming from {esphome_host}:{esphome_port} (level={esphome_log_level})")

            # Create persistent API connection for log streaming
            self._esphome_log_api = APIClient(
                address=esphome_host,
                port=esphome_port,
                password="",
                noise_psk=esphome_noise_psk
            )
            await asyncio.wait_for(self._esphome_log_api.connect(login=True), timeout=10.0)

            # Subscribe to logs (subscribe_logs returns a callable, not awaitable)
            self._esphome_log_unsub = self._esphome_log_api.subscribe_logs(
                self._on_esphome_log_message,
                log_level=log_level
            )
            logger.info("ESPHome log streaming started successfully")

        except Exception as e:
            logger.warning(f"Failed to start ESPHome log streaming: {e}")
            self._esphome_log_api = None
            self._esphome_log_unsub = None

    def _on_esphome_log_message(self, log_entry):
        """Handle incoming log messages from ESPHome device."""
        import re
        from aioesphomeapi import LogLevel

        # log_entry is a structured object with .level and .message attributes
        # message format: b"\033[0;36m[D][component:line]: message\033[0m" (bytes!)
        try:
            # Extract message and level from log_entry object
            if hasattr(log_entry, 'message'):
                raw_message = log_entry.message
            else:
                raw_message = str(log_entry)

            # Decode bytes to string if needed
            if isinstance(raw_message, bytes):
                raw_message = raw_message.decode('utf-8', errors='replace')

            # Strip ANSI escape codes (color codes like \x1b[0;36m or \033[0;36m)
            ansi_escape = re.compile(r'(?:\x1b|\033)\[[0-9;]*m')
            clean = ansi_escape.sub('', raw_message)

            # Parse ESPHome log format: [LEVEL][component:line]: message
            # Example: [D][esp32_ble_tracker:141]: connecting: 0, discovered: 1
            match = re.match(r'^\[([DEWIVT])\]\[([^\]]+?)(?::\d+)?\]:\s*(.+)$', clean)
            if not match:
                # Fallback: log as-is if format doesn't match
                logger.debug(f"[ESP32:raw] {clean}")
                return

            level_char, component, message = match.groups()

            # Map ESPHome log level characters to Python log levels
            level_map = {
                'E': 'error',      # Error
                'W': 'warning',    # Warning
                'I': 'info',       # Info
                'D': 'debug',      # Debug
                'V': 'trace',      # Verbose
                'T': 'trace',      # Trace (very verbose)
            }
            log_method = getattr(logger, level_map.get(level_char, 'debug'))

            # Log with clean component tag
            prefix = f"[ESP32:{component}]"
            log_method(f"{prefix} {message}")

        except Exception as e:
            logger.debug(f"Error parsing ESPHome log entry: {e}, entry={log_entry!r}")

    async def _stop_esphome_log_streaming(self):
        """Unsubscribe from ESPHome device logs."""
        if self._esphome_log_unsub:
            try:
                self._esphome_log_unsub()
                logger.debug("Unsubscribed from ESPHome log streaming")
            except Exception as e:
                logger.debug(f"Error unsubscribing from logs: {e}")
            self._esphome_log_unsub = None

        if self._esphome_log_api:
            try:
                await self._esphome_log_api.disconnect()
                logger.debug("Disconnected ESPHome log streaming API")
            except Exception as e:
                logger.debug(f"Error disconnecting log API: {e}")
            self._esphome_log_api = None

    async def request_reconnect(self):
        """Trigger a clean BLE reconnect (callable from MQTT or REST API)."""
        logger.info("Reconnect requested.")
        self._connection_allowed.set()
        self._reconnect_requested.set()

    async def request_disconnect(self):
        """Disconnect and stay disconnected until reconnect is requested."""
        logger.info("Disconnect requested.")
        self._connection_allowed.clear()
        self._reconnect_requested.set()

    async def wait_for_device_restart(self, device_id, bluetooth_connector=None):
        """Passively scans until the device drops off BLE, then waits for it to reappear."""
        topic = self.mqttConfig['topic']

        if esphome_host:
            await self._wait_for_device_restart_via_esphome(device_id, topic, bluetooth_connector)
        else:
            await self._wait_for_device_restart_local(device_id, topic)

    async def _wait_for_device_restart_via_esphome(self, device_id, topic, bluetooth_connector=None):
        """Wait for device restart using ESP32 proxy scanning.

        Reuses the persistent ESP32 API connection from bluetooth_connector if it is
        still alive — avoids a redundant TCP handshake during recovery.  A fresh
        APIClient is only created when no live connection is available.  If even that
        fails, falls back to local BLE scanning and reports E2005 to MQTT and webapp.
        """
        logger.info(f"Using ESP32 proxy at {esphome_host}:{esphome_port} for recovery protocol")

        # Try to reuse the existing persistent ESP32 API connection.
        api = None
        own_api = False  # True when we created the connection and must close it afterwards.
        if bluetooth_connector is not None and bluetooth_connector._esphome_api is not None:
            try:
                conn = bluetooth_connector._esphome_api._connection
                if conn and conn.is_connected:
                    api = bluetooth_connector._esphome_api
                    proxy_name = bluetooth_connector.esphome_proxy_name or "unknown"
                    logger.info("Reusing existing ESP32 API connection for recovery scanning")
                    await self._update_esphome_proxy_state(connected=True, name=proxy_name, error="No error", error_code="E0000")
            except Exception:
                pass  # Connection check failed; fall through to create a fresh one.

        if api is None:
            from aioesphomeapi import APIClient
            own_api = True
            api = APIClient(address=esphome_host, port=esphome_port, password="", noise_psk=esphome_noise_psk)
            try:
                await asyncio.wait_for(api.connect(login=True), timeout=10.0)
                device_info = await asyncio.wait_for(api.device_info(), timeout=10.0)
                proxy_name = getattr(device_info, "name", "unknown")
                logger.debug(f"Connected to ESP32 proxy {proxy_name} for recovery scanning")
                await self._update_esphome_proxy_state(connected=True, name=proxy_name, error="No error", error_code="E0000")
            except Exception as e:
                logger.error(f"Failed to connect to ESP32 proxy for recovery: {e}")
                logger.warning("Falling back to local BLE scanning")
                await self._update_esphome_proxy_state(connected=False, error=f"Recovery connection failed: {e}", error_code=E2005.code, error_hint=E2005.hint)
                await self.mqtt_service.send_data_async(f"{topic}/centralDevice/error", ErrorManager.to_json(E2005, str(e)))
                await self._set_ble_status(
                    "error",
                    error_msg=f"ESP32 proxy unavailable during recovery — using local BLE scan: {e}",
                    error_code=E2005.code,
                    error_hint=E2005.hint,
                )
                await self._wait_for_device_restart_local(device_id, topic)
                return

        try:
            mac_int = int(device_id.replace(":", ""), 16)

            # Phase 1: wait for device to disappear (max 2 minutes)
            await self.mqtt_service.send_data_async(f"{topic}/centralDevice/connected", "Peripheral not responding. Please power cycle the device.")
            logger.info(f"Waiting for device {device_id} to drop off ESP32 proxy scanner...")

            timeout = time.time() + 120  # 2 minutes
            while not self._shutdown_event.is_set() and time.time() < timeout:
                found = await self._check_device_via_esphome(api, mac_int)
                if not found:
                    logger.info("Device shut down confirmed (via ESP32 proxy).")
                    await self.mqtt_service.send_data_async(f"{topic}/centralDevice/connected", "Device offline. Waiting for it to power back on...")
                    break
                await asyncio.sleep(2)
            else:
                if time.time() >= timeout:
                    logger.warning("Timeout waiting for device to disappear from ESP32 scanner. Device may still be advertising. Skipping to reconnection phase...")
                    await self.mqtt_service.send_data_async(f"{topic}/centralDevice/error", ErrorManager.to_json(E2001, "Device still advertising after 2 minutes"))

            # Phase 2: wait for device to reappear (max 2 minutes)
            logger.info(f"Waiting for device {device_id} to reappear on ESP32 proxy...")
            timeout = time.time() + 120  # 2 minutes
            while not self._shutdown_event.is_set() and time.time() < timeout:
                found = await self._check_device_via_esphome(api, mac_int)
                if found:
                    logger.info("Device back online (via ESP32 proxy).")
                    await self.mqtt_service.send_data_async(f"{topic}/centralDevice/connected", f"Device detected ({device_id}). Reconnecting...")
                    await asyncio.sleep(2)
                    break
                await asyncio.sleep(2)
            else:
                if time.time() >= timeout:
                    logger.error("Timeout waiting for device to reappear on ESP32 scanner. Giving up on recovery. Please check device power and BLE advertising.")
                    await self.mqtt_service.send_data_async(f"{topic}/centralDevice/error", ErrorManager.to_json(E2002, "Device not detected after 2 minutes"))
        finally:
            if own_api:
                try:
                    await api.disconnect()
                    logger.debug("Disconnected from ESP32 proxy after recovery scanning")
                except Exception:
                    pass

    async def _check_device_via_esphome(self, api, mac_int) -> bool:
        """Check if device is visible via ESP32 proxy. Returns True if found."""
        found_event = asyncio.Event()

        def on_raw_advertisements(resp):
            for adv in resp.advertisements:
                if adv.address == mac_int:
                    found_event.set()

        unsub = api.subscribe_bluetooth_le_raw_advertisements(on_raw_advertisements)
        try:
            await asyncio.wait_for(found_event.wait(), timeout=3.0)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            unsub()

    async def _wait_for_device_restart_local(self, device_id, topic):
        """Wait for device restart using local BLE scanning."""
        logger.info("Using local BLE for recovery protocol")

        # Phase 1: wait for the user to power-cycle the device (max 2 minutes)
        await self.mqtt_service.send_data_async(f"{topic}/centralDevice/connected", "Peripheral not responding. Please power cycle the device.")
        logger.info(f"Waiting for device {device_id} to drop off BLE scanner...")
        timeout = time.time() + 120  # 2 minutes
        while not self._shutdown_event.is_set() and time.time() < timeout:
            device = await BleakScanner.find_device_by_address(device_id, timeout=3.0)
            if device is None:
                logger.info("Device shut down confirmed.")
                await self.mqtt_service.send_data_async(f"{topic}/centralDevice/connected", "Device offline. Waiting for it to power back on...")
                break
            await asyncio.sleep(2)
        else:
            if time.time() >= timeout:
                logger.warning("Timeout waiting for device to disappear from BLE scanner. Device may still be advertising. Skipping to reconnection phase...")
                await self.mqtt_service.send_data_async(f"{topic}/centralDevice/error", ErrorManager.to_json(E2003, "Device still advertising after 2 minutes"))

        # Phase 2: wait for it to boot back up (max 2 minutes)
        logger.info(f"Waiting for device {device_id} to reappear...")
        timeout = time.time() + 120  # 2 minutes
        while not self._shutdown_event.is_set() and time.time() < timeout:
            device = await BleakScanner.find_device_by_address(device_id, timeout=3.0)
            if device is not None:
                logger.info("Device back online.")
                await self.mqtt_service.send_data_async(f"{topic}/centralDevice/connected", f"Device detected ({device.name} / {device.address}). Reconnecting...")
                await asyncio.sleep(2)
                break
            await asyncio.sleep(2)
        else:
            if time.time() >= timeout:
                logger.error("Timeout waiting for device to reappear on BLE scanner. Giving up on recovery. Please check device power and BLE advertising.")
                await self.mqtt_service.send_data_async(f"{topic}/centralDevice/error", ErrorManager.to_json(E2004, "Device not detected after 2 minutes"))

    # --- Event Handlers ---
    async def on_device_state_changed(self, sender, args):
        topic = self.mqttConfig['topic']
        if "IsUserSitting" in args.__dict__ and args.IsUserSitting is not None:
            self.device_state["is_user_sitting"] = args.IsUserSitting
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isUserSitting", str(args.IsUserSitting))
        if "IsAnalShowerRunning" in args.__dict__ and args.IsAnalShowerRunning is not None:
            self.device_state["is_anal_shower_running"] = args.IsAnalShowerRunning
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isAnalShowerRunning", str(args.IsAnalShowerRunning))
        if "IsLadyShowerRunning" in args.__dict__ and args.IsLadyShowerRunning is not None:
            self.device_state["is_lady_shower_running"] = args.IsLadyShowerRunning
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isLadyShowerRunning", str(args.IsLadyShowerRunning))
        if "IsDryerRunning" in args.__dict__ and args.IsDryerRunning is not None:
            self.device_state["is_dryer_running"] = args.IsDryerRunning
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isDryerRunning", str(args.IsDryerRunning))
        if self.on_state_updated:
            await self.on_state_updated(self.device_state.copy())

    async def on_device_identification(self, sender, args):
        topic = self.mqttConfig['topic']
        await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/SapNumber", str(args.sap_number))
        await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/SerialNumber", str(args.serial_number))
        await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/ProductionDate", str(args.production_date))
        await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/Description", str(args.description))

    async def device_initial_operation_date(self, sender, args):
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/initialOperationDate", str(args))

    async def soc_application_versions(self, sender, args):
        pass

    async def on_toggle_lid_message(self):
        await self.client.toggle_lid_position()

    def on_connection_status_changed(self, sender, *args):
        values = ", ".join(str(arg) for arg in args)
        asyncio.create_task(self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", values))

    async def handle_exception(self, e):
        exc_name = get_full_class_name(e)
        logger.error(f'{exc_name}: {e}')
        if exc_name == "bleak.exc.BleakError" and "Service Discovery" in str(e):
            logger.error("OK on shutdown")
        else:
            print(traceback.format_exc())
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", ErrorManager.to_json(E7004, f'{exc_name}: {e}'))
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
            sys.exit(1)


class ApiMode:
    """REST API mode: persistent BLE + polling loop, or on-demand per-request connections."""

    def __init__(self):
        mqtt_enabled = config.getboolean("SERVICE", "mqtt_enabled", fallback=True)
        self.ble_connection = config.get("SERVICE", "ble_connection", fallback="persistent")
        api_host = config.get("API", "host", fallback="0.0.0.0")
        api_port = int(config.get("API", "port", fallback="8080"))
        try:
            self._poll_interval = float(config.get("POLL", "interval"))
        except Exception:
            self._poll_interval = 0.0

        self._shutdown_event = asyncio.Event()
        self._on_demand_lock = asyncio.Lock()
        self._poll_wakeup    = asyncio.Event()
        self._esphome_connector: "BluetoothLeConnector | None" = None  # Persistent connector (esphome_api_connection=persistent)
        self._esphome_client = None  # Paired client — created once so data_received_handlers don't accumulate
        self.esphome_api_connection = esphome_api_connection  # runtime-mutable: "persistent" | "on-demand"
        self.rest_api = RestApiService(api_host, api_port)
        self.rest_api.set_api_mode(self)

        # Always create ServiceMode so ble_connection can be toggled at runtime.
        self.service = ServiceMode(mqtt_enabled=mqtt_enabled, shutdown_event=self._shutdown_event)
        self.service.device_state["ble_connection"] = self.ble_connection
        self.service.device_state["esphome_api_connection"] = self.esphome_api_connection
        self.service.device_state["poll_interval"]  = self._poll_interval
        if self.ble_connection != "persistent":
            # Start in standby — loop waits on _connection_allowed until switched
            self.service._connection_allowed.clear()

        logger.info(f"API mode: ble_connection={self.ble_connection}, esphome_api_connection={self.esphome_api_connection if esphome_host else 'N/A'}, mqtt_enabled={mqtt_enabled}, {api_host}:{api_port}")

    @staticmethod
    def _http_error(status_code: int, error_code, details: str = None):
        """
        Raise HTTPException with structured error response.

        Args:
            status_code: HTTP status code (400, 503, etc.)
            error_code: ErrorCode instance (E4001, E4003, etc.)
            details: Optional additional error details

        Returns:
            HTTPException with detail as structured dict:
            {
                "status": "error",
                "error": {
                    "code": "E4001",
                    "message": "Invalid BLE connection mode"
                }
            }
        """
        from fastapi import HTTPException
        error_dict = ErrorManager.to_dict(error_code, details)
        raise HTTPException(
            status_code=status_code,
            detail={
                "status": "error",
                "error": error_dict
            }
        )

    async def run(self):
        self.service.on_state_updated = self.rest_api.broadcast_state
        # Wire MQTT inbound control topics → ApiMode handlers
        self.service.mqtt_service.ToggleAnal       += self._on_mqtt_toggle_anal
        self.service.mqtt_service.SetBleConnection       += self._on_mqtt_set_ble_connection
        self.service.mqtt_service.SetEsphomeApiConnection += self._on_mqtt_set_esphome_api_connection
        self.service.mqtt_service.SetPollInterval         += self._on_mqtt_set_poll_interval
        self.service.mqtt_service.Disconnect       += self._on_mqtt_disconnect
        self.service.mqtt_service.ConnectESP32          += self._on_mqtt_esp32_connect
        self.service.mqtt_service.DisconnectESP32        += self._on_mqtt_esp32_disconnect
        service_task = asyncio.create_task(self.service.run())
        poll_task = asyncio.create_task(self._polling_loop())
        try:
            await self.rest_api.start(self._shutdown_event)
        finally:
            # Ensure the BLE loop also sees the shutdown event
            self._shutdown_event.set()
            # Let the service exit gracefully via the shutdown event —
            # it needs to publish MQTT status before BLE disconnect.
            # Only cancel as a last resort if it doesn't finish in time.
            tasks = {service_task, poll_task}
            done, pending = await asyncio.wait(tasks, timeout=5.0)
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    # --- Config endpoints ---

    def get_current_state(self) -> dict:
        """In-memory state snapshot — sync, no BLE connection (safe for SSE initial push)."""
        state = dict(self.service.device_state)
        # Include ESPHome proxy state
        state.update({
            "esphome_proxy_enabled": self.service.esphome_proxy_state["enabled"],
            "esphome_proxy_connected": self.service.esphome_proxy_state["connected"],
            "esphome_proxy_name": self.service.esphome_proxy_state["name"],
            "esphome_proxy_host": self.service.esphome_proxy_state["host"],
            "esphome_proxy_port": self.service.esphome_proxy_state["port"],
            "esphome_proxy_error": self.service.esphome_proxy_state["error"],
        })
        return state

    def get_config(self) -> dict:
        return {
            "ble_connection": self.ble_connection,
            "esphome_api_connection": self.esphome_api_connection,
            "poll_interval": self._poll_interval,
        }

    async def set_ble_connection(self, value: str) -> dict:
        if value not in ("persistent", "on-demand"):
            self._http_error(400, E4001, f"Invalid value {value!r}. Use 'persistent' or 'on-demand'.")
        self.ble_connection = value
        self.service.device_state["ble_connection"] = value
        if value == "persistent":
            await self.service.request_reconnect()
        else:
            await self.service.request_disconnect()
        await self.rest_api.broadcast_state(self.service.device_state.copy())
        return {"status": "success", "ble_connection": value}

    async def set_esphome_api_connection(self, value: str) -> dict:
        if value not in ("persistent", "on-demand"):
            self._http_error(400, E4001, f"Invalid value {value!r}. Use 'persistent' or 'on-demand'.")
        self.esphome_api_connection = value
        self.service.device_state["esphome_api_connection"] = value
        # When switching to on-demand, tear down the shared connector so the
        # next request gets a fresh connection rather than reusing a stale one.
        if value == "on-demand" and self._esphome_connector is not None:
            try:
                await self._esphome_connector.disconnect()
            except Exception:
                pass
            self._esphome_connector = None
            self._esphome_client = None
            await self.service._update_esphome_proxy_state(
                connected=False, error="No error", error_code="E0000"
            )
        await self.rest_api.broadcast_state(self.service.device_state.copy())
        return {"status": "success", "esphome_api_connection": value}

    async def set_poll_interval(self, value: float) -> dict:
        if value < 0:
            self._http_error(400, E4002, f"Value {value} is invalid. Must be >= 0 (0 = disabled)")
        old_interval = self._poll_interval
        self._poll_interval = value
        self.service.device_state["poll_interval"] = value
        if old_interval == 0 and value > 0:
            # Reset the countdown epoch so the webapp starts the countdown immediately
            # rather than resuming from a stale epoch set when polling was previously active.
            self.service.device_state["poll_epoch"] = time.time()
        self._poll_wakeup.set()                    # wake on-demand _polling_loop
        self.service._poll_interval_event.set()    # wake persistent-mode inner loop
        await self.rest_api.broadcast_state(self.service.device_state.copy())
        return {"status": "success", "poll_interval": value}

    # --- MQTT inbound handlers ---

    async def _on_mqtt_toggle_anal(self):
        try:
            await self.run_command("toggle-anal")
        except Exception as e:
            logger.warning(f"MQTT toggle-anal failed: {e}")

    async def _on_mqtt_set_ble_connection(self, value: str):
        try:
            await self.set_ble_connection(value)
        except Exception as e:
            logger.warning(f"MQTT set_ble_connection({value!r}) failed: {e}")

    async def _on_mqtt_set_esphome_api_connection(self, value: str):
        try:
            await self.set_esphome_api_connection(value)
        except Exception as e:
            logger.warning(f"MQTT set_esphome_api_connection({value!r}) failed: {e}")

    async def _on_mqtt_set_poll_interval(self, value: float):
        try:
            await self.set_poll_interval(value)
        except Exception as e:
            logger.warning(f"MQTT set_poll_interval({value}) failed: {e}")

    async def _on_mqtt_disconnect(self):
        try:
            await self.do_disconnect()
        except Exception as e:
            logger.warning(f"MQTT disconnect failed: {e}")

    # --- REST endpoint implementations ---

    async def get_status(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            result = self.service.device_state
        else:
            result = await self._on_demand(lambda client: self._fetch_state(client))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isUserSitting",       str(result.get("is_user_sitting")))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isAnalShowerRunning", str(result.get("is_anal_shower_running")))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isLadyShowerRunning", str(result.get("is_lady_shower_running")))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isDryerRunning",      str(result.get("is_dryer_running")))
        return result

    async def get_info(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            c = self.service.client
            result = {
                "sap_number": c.SapNumber,
                "serial_number": c.SerialNumber,
                "production_date": c.ProductionDate,
                "description": c.Description,
                "initial_operation_date": c.InitialOperationDate,
            }
        else:
            if self.service.device_state.get("sap_number") is not None:
                result = {k: self.service.device_state[k] for k in
                          ("sap_number", "serial_number", "production_date",
                           "description", "initial_operation_date")}
            else:
                result = await self._on_demand(lambda client: self._fetch_info(client))
        return result

    async def run_command(self, command: str):
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            return await self._persistent_query(lambda client: self._execute_command(client, command))
        else:
            return await self._on_demand(lambda client: self._execute_command(client, command))

    async def do_connect(self):
        if self.ble_connection == "persistent":
            await self.service.request_reconnect()
            return {"status": "success", "action": "reconnect requested"}
        else:
            return await self._on_demand(lambda client: self._fetch_info(client))

    async def do_disconnect(self):
        if self.ble_connection == "persistent":
            await self.service.request_disconnect()
            return {"status": "success", "action": "disconnect requested"}
        else:
            return {"status": "success", "action": "no persistent connection to disconnect"}

    async def esp32_connect(self) -> dict:
        """Not applicable in on-demand ESP API mode — each BLE request creates its own connection."""
        return {"status": "success", "action": "not_applicable", "note": "ESP32 API is on-demand; connection is created per BLE request"}

    async def esp32_disconnect(self) -> dict:
        """Not applicable in on-demand ESP API mode — each BLE request tears down its own connection."""
        return {"status": "success", "action": "not_applicable", "note": "ESP32 API is on-demand; no persistent connection to disconnect"}

    async def _on_mqtt_esp32_connect(self):
        try:
            await self.esp32_connect()
        except Exception as e:
            logger.warning(f"MQTT esp32_connect failed: {e}")

    async def _on_mqtt_esp32_disconnect(self):
        try:
            await self.esp32_disconnect()
        except Exception as e:
            logger.warning(f"MQTT esp32_disconnect failed: {e}")

    # --- Data query endpoints ---

    async def get_system_parameters(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            # fires DeviceStateChanged → on_device_state_changed → publishes to MQTT
            await self.service.client._state_changed_timer_elapsed()
            return self.service.device_state
        else:
            result = await self._on_demand(lambda client: self._fetch_state(client))
            await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isUserSitting",       str(result["is_user_sitting"]))
            await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isAnalShowerRunning", str(result["is_anal_shower_running"]))
            await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isLadyShowerRunning", str(result["is_lady_shower_running"]))
            await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isDryerRunning",      str(result["is_dryer_running"]))
            return result

    async def get_soc_versions(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            result = {"soc_versions": str(self.service.client.soc_application_versions or "")}
        else:
            result = await self._on_demand(self._fetch_soc_versions)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/SocVersions", result["soc_versions"])
        return result

    async def get_statistics_descale(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            sd = await self.service.client.base_client.get_statistics_descale_async()
            result = self._statistics_descale_to_dict(sd)
        else:
            result = await self._on_demand(self._fetch_statistics_descale)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/unpostedShowerCycles",          str(result["unposted_shower_cycles"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/daysUntilNextDescale",          str(result["days_until_next_descale"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/daysUntilShowerRestricted",     str(result["days_until_shower_restricted"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/showerCyclesUntilConfirmation", str(result["shower_cycles_until_confirmation"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/dateTimeAtLastDescale",         str(result["date_time_at_last_descale"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/dateTimeAtLastDescalePrompt",   str(result["date_time_at_last_descale_prompt"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/descaleStatistics/numberOfDescaleCycles",         str(result["number_of_descale_cycles"]))
        return result

    async def get_initial_operation_date(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            result = {"initial_operation_date": str(self.service.client.InitialOperationDate)}
        else:
            if self.service.device_state.get("initial_operation_date") is not None:
                result = {
                    "initial_operation_date": self.service.device_state["initial_operation_date"],
                    "_connect_ms": 0,
                    "_esphome_api_ms": 0 if esphome_host else None,
                    "_ble_ms": 0 if esphome_host else None,
                    "_query_ms": 0,
                }
            else:
                result = await self._on_demand(self._fetch_initial_op_date)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/initialOperationDate", result["initial_operation_date"])
        return result

    async def get_identification(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            c = self.service.client
            result = {
                "sap_number": c.SapNumber,
                "serial_number": c.SerialNumber,
                "production_date": c.ProductionDate,
                "description": c.Description,
            }
        else:
            if self.service.device_state.get("sap_number") is not None:
                result = {k: self.service.device_state[k] for k in
                          ("sap_number", "serial_number", "production_date", "description")}
                result.update({
                    "_connect_ms": 0,
                    "_esphome_api_ms": 0 if esphome_host else None,
                    "_ble_ms": 0 if esphome_host else None,
                    "_query_ms": 0,
                })
            else:
                result = await self._on_demand(self._fetch_identification)
        return result

    async def get_anal_shower_state(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            result = await self._persistent_query(self._fetch_anal_shower_state)
        else:
            result = await self._on_demand(self._fetch_anal_shower_state)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isAnalShowerRunning", str(result["is_anal_shower_running"]))
        return result

    async def get_user_sitting_state(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            result = await self._persistent_query(self._fetch_user_sitting_state)
        else:
            result = await self._on_demand(self._fetch_user_sitting_state)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isUserSitting", str(result["is_user_sitting"]))
        return result

    async def get_lady_shower_state(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            result = await self._persistent_query(self._fetch_lady_shower_state)
        else:
            result = await self._on_demand(self._fetch_lady_shower_state)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isLadyShowerRunning", str(result["is_lady_shower_running"]))
        return result

    async def get_dryer_state(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                self._http_error(503, E4003)
            result = await self._persistent_query(self._fetch_dryer_state)
        else:
            result = await self._on_demand(self._fetch_dryer_state)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isDryerRunning", str(result["is_dryer_running"]))
        return result

    # --- Helpers ---

    async def _fetch_anal_shower_state(self, client):
        result = await client.base_client.get_system_parameter_list_async([1])
        val = result.data_array[1] != 0
        # Update device_state here — before _on_demand's finally block fires —
        # so the "disconnected" SSE broadcast carries the fresh value, not null.
        self.service.device_state["is_anal_shower_running"] = val
        return {"is_anal_shower_running": val}

    async def _fetch_user_sitting_state(self, client):
        result = await client.base_client.get_system_parameter_list_async([0])
        val = result.data_array[0] != 0
        self.service.device_state["is_user_sitting"] = val
        return {"is_user_sitting": val}

    async def _fetch_lady_shower_state(self, client):
        result = await client.base_client.get_system_parameter_list_async([2])
        val = result.data_array[2] != 0
        self.service.device_state["is_lady_shower_running"] = val
        return {"is_lady_shower_running": val}

    async def _fetch_dryer_state(self, client):
        result = await client.base_client.get_system_parameter_list_async([3])
        val = result.data_array[3] != 0
        self.service.device_state["is_dryer_running"] = val
        return {"is_dryer_running": val}

    async def _fetch_soc_versions(self, client):
        versions = await client.base_client.get_soc_application_versions_async()
        return {"soc_versions": str(versions)}

    async def _fetch_statistics_descale(self, client):
        sd = await client.base_client.get_statistics_descale_async()
        return self._statistics_descale_to_dict(sd)

    @staticmethod
    def _statistics_descale_to_dict(sd) -> dict:
        return {
            "unposted_shower_cycles":           sd.unposted_shower_cycles,
            "days_until_next_descale":          sd.days_until_next_descale,
            "days_until_shower_restricted":     sd.days_until_shower_restricted,
            "shower_cycles_until_confirmation": sd.shower_cycles_until_confirmation,
            "date_time_at_last_descale":        sd.date_time_at_last_descale,
            "date_time_at_last_descale_prompt": sd.date_time_at_last_descale_prompt,
            "number_of_descale_cycles":         sd.number_of_descale_cycles,
        }

    async def _fetch_initial_op_date(self, client):
        date = await client.base_client.get_device_initial_operation_date()
        return {"initial_operation_date": str(date)}

    async def _fetch_identification(self, client):
        ident = await client.base_client.get_device_identification_async(0)
        return {
            "sap_number": ident.sap_number,
            "serial_number": ident.serial_number,
            "production_date": ident.production_date,
            "description": ident.description,
        }

    async def _persistent_query(self, action):
        """Execute a BLE action on the persistent client and return timing metadata.
        Connect costs are 0 — the connection is already live."""
        t = time.perf_counter()
        result = await action(self.service.client)
        query_ms = int((time.perf_counter() - t) * 1000)
        timing = {
            "_connect_ms": 0,
            "_esphome_api_ms": 0 if esphome_host else None,
            "_ble_ms": 0 if esphome_host else None,
            "_query_ms": query_ms,
        }
        if isinstance(result, dict):
            return {**result, **timing}
        return timing

    def _get_esphome_connector(self) -> BluetoothLeConnector:
        """Return the cached persistent BluetoothLeConnector for persistent_api mode.

        Created on first call and reused across requests. The ESP32 API TCP connection
        inside the connector is kept alive between BLE cycles via disconnect_ble_only().
        """
        if self._esphome_connector is None:
            self._esphome_connector = BluetoothLeConnector(esphome_host, esphome_port, esphome_noise_psk)
            self._esphome_connector.connection_status_changed_handlers += self.service.on_connection_status_changed
            factory = AquaCleanClientFactory(self._esphome_connector)
            self._esphome_client = factory.create_client()
        return self._esphome_connector

    async def _on_demand(self, action):
        """Connect, execute action, disconnect — for on-demand connection mode.
        Publishes connecting/connected/disconnected to MQTT and SSE, mirroring
        the persistent-mode behaviour."""
        async with self._on_demand_lock:
            return await self._on_demand_inner(action)

    async def _on_demand_inner(self, action):
        device_id = config.get("BLE", "device_id")
        topic = self.service.mqttConfig['topic']

        use_persistent = bool(esphome_host and self.esphome_api_connection == "persistent")

        if use_persistent:
            connector = self._get_esphome_connector()
            client = self._esphome_client
        else:
            connector = BluetoothLeConnector(esphome_host, esphome_port, esphome_noise_psk)
            connector.connection_status_changed_handlers += self.service.on_connection_status_changed
            factory = AquaCleanClientFactory(connector)
            client = factory.create_client()
        _exc = None
        _ec = None
        try:
            await self.service.mqtt_service.send_data_async(
                f"{topic}/centralDevice/connected", f"Connecting to {device_id} ...")
            await self.service._set_ble_status("connecting", device_address=device_id)
            t0 = time.perf_counter()
            await client.connect_ble_only(device_id)
            connect_ms = int((time.perf_counter() - t0) * 1000)
            self.service.device_state["last_connect_ms"] = connect_ms
            self.service.device_state["last_esphome_api_ms"] = connector.last_esphome_api_ms
            self.service.device_state["last_ble_ms"] = connector.last_ble_ms
            await self.service._set_ble_status("connected", device_name=connector.device_name, device_address=device_id)
            if esphome_host and connector.esphome_proxy_connected:
                await self.service._update_esphome_proxy_state(
                    connected=True,
                    name=connector.esphome_proxy_name,
                    error="No error",
                    error_code="E0000",
                )
            await self.service.mqtt_service.send_data_async(
                f"{topic}/centralDevice/timings",
                json.dumps({
                    "connect_ms": connect_ms,
                    "esphome_api_ms": connector.last_esphome_api_ms,
                    "ble_ms": connector.last_ble_ms,
                })
            )
            t1 = time.perf_counter()
            result = action(client)
            result = await result if asyncio.iscoroutine(result) else result
            query_ms = int((time.perf_counter() - t1) * 1000)
            self.service.device_state["last_poll_ms"] = query_ms
            timing = {
                "_connect_ms": connect_ms,
                "_esphome_api_ms": connector.last_esphome_api_ms,
                "_ble_ms": connector.last_ble_ms,
                "_query_ms": query_ms,
            }
            if isinstance(result, dict):
                result = {**result, **timing}
            else:
                result = timing
            return result
        except Exception as e:
            _exc = e
        finally:
            try:
                if use_persistent:
                    await connector.disconnect_ble_only()  # Keep ESP32 API TCP alive for next request
                else:
                    await connector.disconnect()           # Full teardown (original behavior)
            except Exception:
                pass
            if _exc is not None:
                # Map exception to error code so webapp shows the right status.
                if isinstance(_exc, BLEPeripheralTimeoutError):
                    _ec = E0003
                elif isinstance(_exc, ESPHomeConnectionError):
                    _ec = E1001 if _exc.timeout else E1002
                elif isinstance(_exc, ESPHomeDeviceNotFoundError):
                    _ec = E0002
                elif isinstance(_exc, BleakError):
                    _ec = E0003
                else:
                    _ec = E7002
                await self.service._set_ble_status("error", error_msg=str(_exc), error_code=_ec.code, error_hint=_ec.hint)
            else:
                await self.service._set_ble_status("disconnected")
                if esphome_host:
                    if use_persistent:
                        # TCP stays alive — proxy remains connected between BLE cycles
                        await self.service._update_esphome_proxy_state(
                            connected=True, error="No error", error_code="E0000"
                        )
                    else:
                        await self.service._update_esphome_proxy_state(
                            connected=False, error="No error", error_code="E0000"
                        )
        if _exc is not None:
            ApiMode._http_error(503, _ec, str(_exc))

    async def _publish_identification_to_mqtt(self, info: dict):
        """Publish device identification fields to their MQTT topics.
        Mirrors what ServiceMode.on_device_identification / device_initial_operation_date
        do in persistent mode via event handlers."""
        topic = self.service.mqttConfig['topic']
        await self.service.mqtt_service.send_data_async(
            f"{topic}/peripheralDevice/information/Identification/SapNumber",    str(info.get("sap_number", "")))
        await self.service.mqtt_service.send_data_async(
            f"{topic}/peripheralDevice/information/Identification/SerialNumber",  str(info.get("serial_number", "")))
        await self.service.mqtt_service.send_data_async(
            f"{topic}/peripheralDevice/information/Identification/ProductionDate", str(info.get("production_date", "")))
        await self.service.mqtt_service.send_data_async(
            f"{topic}/peripheralDevice/information/Identification/Description",   str(info.get("description", "")))
        await self.service.mqtt_service.send_data_async(
            f"{topic}/peripheralDevice/information/initialOperationDate",         str(info.get("initial_operation_date", "")))

    async def _polling_loop(self):
        """Background poll: query GetSystemParameterList every _poll_interval seconds
        when running in on-demand mode. Skips silently in persistent mode.
        interval=0 pauses polling. _poll_wakeup lets the loop react immediately
        when the interval is changed at runtime via set_poll_interval()."""
        logger.info(f"Poll loop started (interval={self._poll_interval}s)")
        topic = self.service.mqttConfig['topic']
        _identification_fetched = False  # fetch identification on the first poll, then state-only
        _consecutive_poll_failures = 0
        _CIRCUIT_OPEN_THRESHOLD = 5    # failures before circuit opens
        _CIRCUIT_OPEN_SLEEP     = 60   # seconds between probe attempts when open

        while True:
            # Sleep for the current interval; _poll_wakeup interrupts early on change.
            try:
                if self._poll_interval > 0:
                    await asyncio.wait_for(self._poll_wakeup.wait(), timeout=self._poll_interval)
                else:
                    await self._poll_wakeup.wait()   # interval=0: wait until re-enabled
                # Woken by set_poll_interval — restart sleep with the new value.
                self._poll_wakeup.clear()
                continue
            except asyncio.TimeoutError:
                pass   # normal path: interval elapsed
            except asyncio.CancelledError:
                return

            if self._shutdown_event.is_set():
                return
            if self.ble_connection != "on-demand":
                continue  # persistent mode handles its own polling

            # Circuit breaker: after threshold failures, probe at a longer interval.
            if _consecutive_poll_failures >= _CIRCUIT_OPEN_THRESHOLD:
                await asyncio.sleep(_CIRCUIT_OPEN_SLEEP)

            try:
                if not _identification_fetched:
                    result = await self._on_demand(self._fetch_state_and_info)
                    _identification_fetched = True
                    # Cache identification in device_state for SSE and /info endpoint.
                    for k in ("sap_number", "serial_number", "production_date",
                              "description", "initial_operation_date"):
                        self.service.device_state[k] = result.get(k)
                    await self._publish_identification_to_mqtt(result)
                else:
                    result = await self._on_demand(self._fetch_state)
                # Success — close circuit.
                if _consecutive_poll_failures > 0:
                    logger.info(f"Poll recovered after {_consecutive_poll_failures} consecutive failure(s)")
                    _identification_fetched = False  # re-fetch in case device was power-cycled
                _consecutive_poll_failures = 0
                # _set_ble_status("disconnected") cleared timing and poll_epoch;
                # restore them so the webapp gets accurate values.
                self.service.device_state["last_connect_ms"]    = result.get("_connect_ms")
                self.service.device_state["last_esphome_api_ms"] = result.get("_esphome_api_ms")
                self.service.device_state["last_ble_ms"]         = result.get("_ble_ms")
                self.service.device_state["last_poll_ms"]        = result.get("_query_ms")
                self.service.device_state["poll_epoch"]      = time.time()
                await self.rest_api.broadcast_state(self.service.device_state.copy())
                await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isUserSitting",       str(result.get("is_user_sitting")))
                await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isAnalShowerRunning", str(result.get("is_anal_shower_running")))
                await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isLadyShowerRunning", str(result.get("is_lady_shower_running")))
                await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isDryerRunning",      str(result.get("is_dryer_running")))
            except ESPHomeConnectionError as e:
                error_code_obj = E1001 if e.timeout else E1002
                _consecutive_poll_failures += 1
                logger.warning(f"On-demand poll: ESP32 TCP error (failure #{_consecutive_poll_failures}): {e}")
                await self.service.mqtt_service.send_data_async(
                    f"{topic}/esphomeProxy/error", ErrorManager.to_json(error_code_obj, str(e)))
                await self.service._update_esphome_proxy_state(
                    connected=False, error=str(e), error_code=error_code_obj.code, error_hint=error_code_obj.hint)
                if _consecutive_poll_failures == _CIRCUIT_OPEN_THRESHOLD:
                    logger.warning(f"Circuit open after {_consecutive_poll_failures} failures — probing every {_CIRCUIT_OPEN_SLEEP}s")
            except ESPHomeDeviceNotFoundError as e:
                _consecutive_poll_failures += 1
                logger.warning(f"On-demand poll: Geberit not found via ESP32 (failure #{_consecutive_poll_failures}): {e}")
                await self.service.mqtt_service.send_data_async(
                    f"{topic}/centralDevice/error", ErrorManager.to_json(E0002, str(e)))
                if _consecutive_poll_failures == _CIRCUIT_OPEN_THRESHOLD:
                    logger.warning(f"Circuit open after {_consecutive_poll_failures} failures — probing every {_CIRCUIT_OPEN_SLEEP}s")
            except BleakError as e:
                _consecutive_poll_failures += 1
                logger.warning(f"On-demand poll: BLE error (failure #{_consecutive_poll_failures}): {e}")
                await self.service.mqtt_service.send_data_async(
                    f"{topic}/centralDevice/error", ErrorManager.to_json(E0003, str(e)))
                if _consecutive_poll_failures == _CIRCUIT_OPEN_THRESHOLD:
                    logger.warning(f"Circuit open after {_consecutive_poll_failures} failures — probing every {_CIRCUIT_OPEN_SLEEP}s")
            except Exception as e:
                _consecutive_poll_failures += 1
                logger.warning(f"On-demand poll failed (failure #{_consecutive_poll_failures}): {e}")
                await self.service.mqtt_service.send_data_async(f"{topic}/centralDevice/error", ErrorManager.to_json(E7002, str(e)))
                if _consecutive_poll_failures == _CIRCUIT_OPEN_THRESHOLD:
                    logger.warning(f"Circuit open after {_consecutive_poll_failures} failures — probing every {_CIRCUIT_OPEN_SLEEP}s")

    async def _fetch_state(self, client):
        from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetSystemParameterList import GetSystemParameterList
        result = await client.base_client.get_system_parameter_list_async([0, 1, 2, 3, 4, 5, 7, 9])
        # Update device_state before _on_demand's finally fires so the
        # "disconnected" SSE broadcast carries fresh values.
        self.service.device_state["is_user_sitting"]        = result.data_array[0] != 0
        self.service.device_state["is_anal_shower_running"] = result.data_array[1] != 0
        self.service.device_state["is_lady_shower_running"] = result.data_array[2] != 0
        self.service.device_state["is_dryer_running"]       = result.data_array[3] != 0
        return {
            "is_user_sitting":        self.service.device_state["is_user_sitting"],
            "is_anal_shower_running": self.service.device_state["is_anal_shower_running"],
            "is_lady_shower_running": self.service.device_state["is_lady_shower_running"],
            "is_dryer_running":       self.service.device_state["is_dryer_running"],
        }

    async def _fetch_state_and_info(self, client):
        """Used for the first on-demand poll only: fetch state + identification in one BLE session."""
        state = await self._fetch_state(client)
        info  = await self._fetch_info(client)
        return {**state, **info}

    async def _fetch_info(self, client):
        ident = await client.base_client.get_device_identification_async(0)
        initial_op_date = await client.base_client.get_device_initial_operation_date()
        return {
            "sap_number": ident.sap_number,
            "serial_number": ident.serial_number,
            "production_date": ident.production_date,
            "description": ident.description,
            "initial_operation_date": str(initial_op_date),
        }

    async def _execute_command(self, client, command: str):
        if command == "toggle-lid":
            await client.toggle_lid_position()
        elif command == "toggle-anal":
            await client.toggle_anal_shower()
        else:
            self._http_error(400, E3002, f"Command '{command}' not recognized")


def get_ha_discovery_configs(topic_prefix: str) -> list:
    """
    Return Home Assistant MQTT Discovery configurations for all AquaClean entities.

    The topic_prefix comes from config.ini [MQTT] topic, so it always matches the
    topics the running application actually publishes.

    HOW TO KEEP THIS IN SYNC: when you add a new send_data_async() call elsewhere
    in this file, add the corresponding HA entity here.  Both live in main.py, so
    they're easy to find and update together.
    """
    DEVICE = {
        "identifiers": ["geberit_aquaclean"],
        "name": "Geberit AquaClean",
        "model": "Mera Comfort",
        "manufacturer": "Geberit",
    }
    HA = "homeassistant"
    t = topic_prefix

    return [
        # --- Binary Sensors: monitor state (ServiceMode.on_device_state_changed) ---
        {
            "topic": f"{HA}/binary_sensor/geberit_aquaclean/user_sitting/config",
            "payload": {
                "name": "User Sitting",
                "unique_id": "geberit_aquaclean_user_sitting",
                "state_topic": f"{t}/peripheralDevice/monitor/isUserSitting",
                "payload_on": "True", "payload_off": "False",
                "icon": "mdi:seat",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/binary_sensor/geberit_aquaclean/anal_shower_running/config",
            "payload": {
                "name": "Anal Shower Running",
                "unique_id": "geberit_aquaclean_anal_shower_running",
                "state_topic": f"{t}/peripheralDevice/monitor/isAnalShowerRunning",
                "payload_on": "True", "payload_off": "False",
                "icon": "mdi:shower",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/binary_sensor/geberit_aquaclean/lady_shower_running/config",
            "payload": {
                "name": "Lady Shower Running",
                "unique_id": "geberit_aquaclean_lady_shower_running",
                "state_topic": f"{t}/peripheralDevice/monitor/isLadyShowerRunning",
                "payload_on": "True", "payload_off": "False",
                "icon": "mdi:shower",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/binary_sensor/geberit_aquaclean/dryer_running/config",
            "payload": {
                "name": "Dryer Running",
                "unique_id": "geberit_aquaclean_dryer_running",
                "state_topic": f"{t}/peripheralDevice/monitor/isDryerRunning",
                "payload_on": "True", "payload_off": "False",
                "icon": "mdi:air-filter",
                "device": DEVICE,
            },
        },
        # --- Sensors: device identification (ServiceMode.on_device_identification) ---
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/sap_number/config",
            "payload": {
                "name": "SAP Number",
                "unique_id": "geberit_aquaclean_sap_number",
                "state_topic": f"{t}/peripheralDevice/information/Identification/SapNumber",
                "icon": "mdi:identifier",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/serial_number/config",
            "payload": {
                "name": "Serial Number",
                "unique_id": "geberit_aquaclean_serial_number",
                "state_topic": f"{t}/peripheralDevice/information/Identification/SerialNumber",
                "icon": "mdi:barcode",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/production_date/config",
            "payload": {
                "name": "Production Date",
                "unique_id": "geberit_aquaclean_production_date",
                "state_topic": f"{t}/peripheralDevice/information/Identification/ProductionDate",
                "icon": "mdi:calendar",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/description/config",
            "payload": {
                "name": "Description",
                "unique_id": "geberit_aquaclean_description",
                "state_topic": f"{t}/peripheralDevice/information/Identification/Description",
                "icon": "mdi:information",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        # --- Sensor: initial operation date (ServiceMode.device_initial_operation_date) ---
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/initial_operation_date/config",
            "payload": {
                "name": "Initial Operation Date",
                "unique_id": "geberit_aquaclean_initial_operation_date",
                "state_topic": f"{t}/peripheralDevice/information/initialOperationDate",
                "icon": "mdi:calendar-clock",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        # --- Sensors: descale statistics (ApiMode.get_statistics_descale) ---
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/days_until_next_descale/config",
            "payload": {
                "name": "Days Until Next Descale",
                "unique_id": "geberit_aquaclean_days_until_next_descale",
                "state_topic": f"{t}/peripheralDevice/information/descaleStatistics/daysUntilNextDescale",
                "icon": "mdi:calendar-refresh",
                "unit_of_measurement": "days",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/days_until_shower_restricted/config",
            "payload": {
                "name": "Days Until Shower Restricted",
                "unique_id": "geberit_aquaclean_days_until_shower_restricted",
                "state_topic": f"{t}/peripheralDevice/information/descaleStatistics/daysUntilShowerRestricted",
                "icon": "mdi:calendar-alert",
                "unit_of_measurement": "days",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/shower_cycles_until_confirmation/config",
            "payload": {
                "name": "Shower Cycles Until Confirmation",
                "unique_id": "geberit_aquaclean_shower_cycles_until_confirmation",
                "state_topic": f"{t}/peripheralDevice/information/descaleStatistics/showerCyclesUntilConfirmation",
                "icon": "mdi:counter",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/number_of_descale_cycles/config",
            "payload": {
                "name": "Number of Descale Cycles",
                "unique_id": "geberit_aquaclean_number_of_descale_cycles",
                "state_topic": f"{t}/peripheralDevice/information/descaleStatistics/numberOfDescaleCycles",
                "icon": "mdi:history",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/last_descale/config",
            "payload": {
                "name": "Last Descale",
                "unique_id": "geberit_aquaclean_last_descale",
                "state_topic": f"{t}/peripheralDevice/information/descaleStatistics/dateTimeAtLastDescale",
                "value_template": "{% if value | int(0) > 0 %}{{ value | int | timestamp_local }}{% else %}Never{% endif %}",
                "icon": "mdi:calendar-check",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/unposted_shower_cycles/config",
            "payload": {
                "name": "Unposted Shower Cycles",
                "unique_id": "geberit_aquaclean_unposted_shower_cycles",
                "state_topic": f"{t}/peripheralDevice/information/descaleStatistics/unpostedShowerCycles",
                "icon": "mdi:sync",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        # --- Sensors: connection status (ServiceMode.run / on_connection_status_changed) ---
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/connected/config",
            "payload": {
                "name": "Connected",
                "unique_id": "geberit_aquaclean_connected",
                "state_topic": f"{t}/centralDevice/connected",
                "icon": "mdi:bluetooth-connect",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/sensor/geberit_aquaclean/error/config",
            "payload": {
                "name": "Error",
                "unique_id": "geberit_aquaclean_error",
                "state_topic": f"{t}/centralDevice/error",
                "icon": "mdi:alert-circle",
                "entity_category": "diagnostic",
                "device": DEVICE,
            },
        },
        # --- Switches: device control (MqttService subscriptions) ---
        {
            "topic": f"{HA}/switch/geberit_aquaclean/toggle_lid/config",
            "payload": {
                "name": "Toggle Lid",
                "unique_id": "geberit_aquaclean_toggle_lid",
                "command_topic": f"{t}/peripheralDevice/control/toggleLidPosition",
                "payload_on": "true", "payload_off": "false",
                "icon": "mdi:toilet",
                "optimistic": True,
                "retain": False,
                "device": DEVICE,
            },
        },
        {
            "topic": f"{HA}/switch/geberit_aquaclean/toggle_anal/config",
            "payload": {
                "name": "Toggle Anal Shower",
                "unique_id": "geberit_aquaclean_toggle_anal",
                "command_topic": f"{t}/peripheralDevice/control/toggleAnal",
                "payload_on": "true", "payload_off": "false",
                "icon": "mdi:shower-head",
                "optimistic": True,
                "retain": False,
                "device": DEVICE,
            },
        },
    ]


def run_ha_discovery(remove: bool = False) -> dict:
    """
    Publish or remove Home Assistant MQTT discovery messages.
    Reads broker connection settings from config.ini — no BLE connection needed.
    Returns a dict with 'published' and 'failed' lists.
    """
    import paho.mqtt.client as mqtt

    mqtt_cfg = dict(config.items('MQTT'))
    topic_prefix = mqtt_cfg.get('topic', 'Geberit/AquaClean')
    host = mqtt_cfg.get('server', 'localhost')
    port = int(mqtt_cfg.get('port', 1883))
    username = mqtt_cfg.get('username') or None
    password = mqtt_cfg.get('password') or None

    try:
        client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
    except AttributeError:
        client = mqtt.Client()

    if username:
        client.username_pw_set(username, password)

    client.connect(host, port, 60)

    configs = get_ha_discovery_configs(topic_prefix)
    published = []
    failed = []

    for cfg in configs:
        topic = cfg["topic"]
        if remove:
            res = client.publish(topic, payload=None, retain=True)
            label = topic.split("/")[-2]
        else:
            res = client.publish(topic, payload=json.dumps(cfg["payload"]), retain=True)
            label = cfg["payload"]["name"]

        if res.rc == mqtt.MQTT_ERR_SUCCESS:
            published.append(label)
        else:
            failed.append(topic)

    client.disconnect()
    return {"topic_prefix": topic_prefix, "broker": f"{host}:{port}", "published": published, "failed": failed}


async def run_cli(args):
    """Executes the CLI logic and ensures JSON is always printed."""
    result = {
        "status": "error",
        "command": getattr(args, 'command', None),
        "device": None,
        "serial_number": None,
        "data": {},
        "error_code": None,
        "message": "Unknown error"
    }

    if not args.command:
        result["message"] = "CLI mode requires --command"
        print(json.dumps(result, indent=2))
        return

    # --- Commands that don't need a BLE connection ---
    if args.command == 'check-config':
        errors = _check_config_errors()
        if errors:
            result["status"] = "error"
            result["message"] = f"{len(errors)} configuration error(s) found"
            result["data"] = {"errors": errors}
        else:
            result["status"] = "success"
            result["message"] = "Configuration is valid"
            result["data"] = {"errors": []}
        print(json.dumps(result, indent=2))
        return

    if args.command == 'get-config':
        result["data"] = {
            "ble_connection":  config.get("SERVICE", "ble_connection", fallback="persistent"),
            "poll_interval":   float(config.get("POLL", "interval", fallback="0")),
            "mqtt_enabled":    config.getboolean("SERVICE", "mqtt_enabled", fallback=True),
            "device_id":       config.get("BLE", "device_id"),
            "api_host":        config.get("API", "host", fallback="0.0.0.0"),
            "api_port":        int(config.get("API", "port", fallback="8080")),
        }
        result["status"] = "success"
        result["message"] = "Config read from config.ini"
        print(json.dumps(result, indent=2))
        return

    if args.command in ('publish-ha-discovery', 'remove-ha-discovery'):
        remove = args.command == 'remove-ha-discovery'
        try:
            data = run_ha_discovery(remove=remove)
            result["data"] = data
            if data["failed"]:
                result["status"] = "error"
                result["message"] = f"{len(data['failed'])} message(s) failed to publish"
            else:
                result["status"] = "success"
                result["message"] = (
                    f"Removed {len(data['published'])} HA discovery entities"
                    if remove else
                    f"Published {len(data['published'])} HA discovery entities to {data['broker']}"
                )
        except Exception as e:
            result["status"] = "error"
            result["message"] = str(e)
        print(json.dumps(result, indent=2))
        return

    if args.command == 'esp32-connect':
        if not esphome_host:
            result["message"] = "No ESPHome host configured in [ESPHOME] host"
            print(json.dumps(result, indent=2))
            return
        connector = BluetoothLeConnector(esphome_host, esphome_port, esphome_noise_psk)
        try:
            await connector._ensure_esphome_api_connected()
            result["status"] = "success"
            result["message"] = "ESP32 API connected"
            result["data"] = {
                "esphome_proxy_name": connector.esphome_proxy_name,
                "esphome_api_ms": connector.last_esphome_api_ms,
            }
        except Exception as e:
            result["message"] = str(e)
        finally:
            try:
                if connector._esphome_api:
                    await connector._esphome_api.disconnect()
            except Exception:
                pass
        print(json.dumps(result, indent=2))
        return

    if args.command == 'esp32-disconnect':
        result["status"] = "success"
        result["message"] = "No persistent ESP32 connection in CLI mode (one-shot)"
        print(json.dumps(result, indent=2))
        return

    # --- Commands that require a BLE connection ---
    client = None
    try:
        device_id = args.address or config.get("BLE", "device_id")
        connector = BluetoothLeConnector(esphome_host, esphome_port, esphome_noise_psk)
        factory = AquaCleanClientFactory(connector)
        client = factory.create_client()

        logger.info(f"Connecting to {device_id}...")
        await client.connect(device_id)

        result["device"]        = client.Description
        result["serial_number"] = client.SerialNumber
        result["timing"] = {
            "esphome_api_ms": connector.last_esphome_api_ms,
            "ble_ms": connector.last_ble_ms,
        }

        if args.command in ('status', 'system-parameters'):
            r = await client.base_client.get_system_parameter_list_async([0, 1, 2, 3])
            result["data"] = {
                "is_user_sitting":        r.data_array[0] != 0,
                "is_anal_shower_running": r.data_array[1] != 0,
                "is_lady_shower_running": r.data_array[2] != 0,
                "is_dryer_running":       r.data_array[3] != 0,
            }
        elif args.command == 'info':
            ident           = await client.base_client.get_device_identification_async(0)
            initial_op_date = await client.base_client.get_device_initial_operation_date()
            result["data"] = {
                "sap_number":             ident.sap_number,
                "serial_number":          ident.serial_number,
                "production_date":        ident.production_date,
                "description":            ident.description,
                "initial_operation_date": str(initial_op_date),
            }
        elif args.command == 'user-sitting-state':
            r = await client.base_client.get_system_parameter_list_async([0])
            result["data"] = {"is_user_sitting": r.data_array[0] != 0}
        elif args.command == 'anal-shower-state':
            r = await client.base_client.get_system_parameter_list_async([1])
            result["data"] = {"is_anal_shower_running": r.data_array[1] != 0}
        elif args.command == 'lady-shower-state':
            r = await client.base_client.get_system_parameter_list_async([2])
            result["data"] = {"is_lady_shower_running": r.data_array[2] != 0}
        elif args.command == 'dryer-state':
            r = await client.base_client.get_system_parameter_list_async([3])
            result["data"] = {"is_dryer_running": r.data_array[3] != 0}
        elif args.command == 'identification':
            ident = await client.base_client.get_device_identification_async(0)
            result["data"] = {
                "sap_number":      ident.sap_number,
                "serial_number":   ident.serial_number,
                "production_date": ident.production_date,
                "description":     ident.description,
            }
        elif args.command == 'initial-operation-date':
            date = await client.base_client.get_device_initial_operation_date()
            result["data"] = {"initial_operation_date": str(date)}
        elif args.command == 'soc-versions':
            versions = await client.base_client.get_soc_application_versions_async()
            result["data"] = {"soc_versions": str(versions)}
        elif args.command == 'statistics-descale':
            sd = await client.base_client.get_statistics_descale_async()
            result["data"] = ApiMode._statistics_descale_to_dict(sd)
        elif args.command == 'toggle-lid':
            await client.toggle_lid_position()
            result["data"] = {"action": "lid_toggled"}
        elif args.command == 'toggle-anal':
            await client.toggle_anal_shower()
            result["data"] = {"action": "anal_shower_toggled"}

        result["status"]  = "success"
        result["message"] = f"Command {args.command} completed"

    except BLEPeripheralTimeoutError as e:
        # BLE connection timeout
        result["status"] = "error"
        result["error_code"] = E0003.code
        result["message"] = E0003.message + f": {str(e)}"
        logger.error(ErrorManager.to_cli(E0003, str(e)))
    except ESPHomeConnectionError as e:
        error_code = E1001 if e.timeout else E1002
        result["status"] = "error"
        result["error_code"] = error_code.code
        result["message"] = error_code.message + f": {e}"
        logger.error(ErrorManager.to_cli(error_code, str(e)))
    except ESPHomeDeviceNotFoundError as e:
        result["status"] = "error"
        result["error_code"] = E0002.code
        result["message"] = E0002.message + f": {e}"
        logger.error(ErrorManager.to_cli(E0002, str(e)))
    except BleakError as e:
        result["status"] = "error"
        result["error_code"] = E0003.code
        result["message"] = E0003.message + f": {e}"
        logger.error(ErrorManager.to_cli(E0003, str(e)))
    except Exception as e:
        # Generic errors
        result["status"] = "error"
        result["error_code"] = E7004.code if "command" not in str(e).lower() else E3003.code
        result["message"] = str(e)
        logger.error(ErrorManager.to_cli(E7004, str(e)))
    finally:
        if client:
            await client.disconnect()
        # The ONLY thing sent to stdout
        print(json.dumps(result, indent=2))


async def main(args):
    if args.mode in ('service', 'api'):
        errors = _check_config_errors()
        if errors:
            for e in errors:
                logging.error(f"Invalid configuration: {e}")
            sys.exit(1)
        _log_startup_config()
    if args.mode == 'service':
        service = ServiceMode()
        await shutdown_waits_for(service.run())
    elif args.mode == 'api':
        api = ApiMode()
        await shutdown_waits_for(api.run())
        # Our signal handler replaced aiorun's, so aiorun won't stop the
        # loop on its own.  Stopping it here lets aiorun enter its normal
        # shutdown phase (cancel remaining tasks like bleak D-Bus, etc.).
        asyncio.get_running_loop().stop()
    else:
        await run_cli(args)
        loop = asyncio.get_running_loop()
        loop.stop()


class JsonArgumentParser(argparse.ArgumentParser):
    """Custom parser that outputs argument errors as JSON; help uses standard text."""

    def error(self, message):
        """Called on invalid choices, missing arguments, or bad types."""
        result = {
            "status": "error",
            "command": "invalid",
            "message": f"Argument Error: {message}",
            "data": {}
        }
        print(json.dumps(result, indent=2))
        sys.exit(0)

    def exit(self, status=0, message=None):
        if message:
            self.error(message)
        sys.exit(status)


if __name__ == "__main__":
    parser = JsonArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description="Geberit AquaClean Controller",
        epilog=(
            "device state queries (require BLE):\n"
            "  %(prog)s --mode cli --command status\n"
            "  %(prog)s --mode cli --command system-parameters\n"
            "  %(prog)s --mode cli --command user-sitting-state\n"
            "  %(prog)s --mode cli --command anal-shower-state\n"
            "  %(prog)s --mode cli --command lady-shower-state\n"
            "  %(prog)s --mode cli --command dryer-state\n"
            "\n"
            "device info queries (require BLE):\n"
            "  %(prog)s --mode cli --command info\n"
            "  %(prog)s --mode cli --command identification\n"
            "  %(prog)s --mode cli --command initial-operation-date\n"
            "  %(prog)s --mode cli --command soc-versions\n"
            "  %(prog)s --mode cli --command statistics-descale\n"
            "\n"
            "device commands (require BLE):\n"
            "  %(prog)s --mode cli --command toggle-lid\n"
            "  %(prog)s --mode cli --command toggle-anal\n"
            "\n"
            "app config / home assistant (no BLE required):\n"
            "  %(prog)s --mode cli --command check-config\n"
            "  %(prog)s --mode cli --command get-config\n"
            "  %(prog)s --mode cli --command publish-ha-discovery\n"
            "  %(prog)s --mode cli --command remove-ha-discovery\n"
            "\n"
            "ESPHome proxy (no BLE required):\n"
            "  %(prog)s --mode cli --command esp32-connect\n"
            "  %(prog)s --mode cli --command esp32-disconnect\n"
            "\n"
            "options:\n"
            "  --address 38:AB:XX:XX:ZZ:67   override BLE device address from config.ini\n"
            "\n"
            "CLI results and errors are written to stdout as JSON.\n"
            "Log output goes to stderr (redirect with 2>logfile)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--mode', choices=['service', 'cli', 'api'], default='service')
    parser.add_argument('--command', choices=[
        # device state queries
        'status', 'system-parameters',
        'user-sitting-state', 'anal-shower-state', 'lady-shower-state', 'dryer-state',
        # device info queries
        'info', 'identification', 'initial-operation-date', 'soc-versions', 'statistics-descale',
        # device commands
        'toggle-lid', 'toggle-anal',
        # app config / home assistant (no BLE required)
        'check-config', 'get-config', 'publish-ha-discovery', 'remove-ha-discovery',
        # ESPHome proxy (no BLE required)
        'esp32-connect', 'esp32-disconnect',
    ])
    parser.add_argument('--address')

    args = parser.parse_args()
    run(main(args))
