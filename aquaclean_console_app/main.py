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
from aquaclean_core.Clients.AquaCleanClient                   import AquaCleanClient
from aquaclean_core.Clients.AquaCleanBaseClient               import BLEPeripheralTimeoutError
from aquaclean_core.IAquaCleanClient                          import IAquaCleanClient
from aquaclean_core.AquaCleanClientFactory                    import AquaCleanClientFactory
from aquaclean_core.Api.CallClasses.Dtos.DeviceIdentification import DeviceIdentification
from aquaclean_core.Message.MessageService                    import MessageService
from aquaclean_core.IBluetoothLeConnector                     import IBluetoothLeConnector
from bluetooth_le.LE.BluetoothLeConnector                     import BluetoothLeConnector
from MqttService                                              import MqttService as Mqtt
from RestApiService                                           import RestApiService
from myEvent                                                  import myEvent
from aquaclean_utils                                          import utils

# --- Configuration & Logging Setup ---
__location__ = os.path.dirname(os.path.abspath(__file__))
iniFile = os.path.join(__location__, 'config.ini')
config = configparser.ConfigParser(allow_no_value=False)
config.read(iniFile)

logs.add_logging_level('TRACE', logging.DEBUG - 5)
logs.add_logging_level('SILLY', logging.DEBUG - 7)

log_level = config.get("LOGGING", "log_level")
logging.basicConfig(level=log_level, format="%(asctime)-15s %(name)-8s %(lineno)d %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


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
        self.Reconnect = myEvent.EventHandler()

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
            "last_connect_ms": None,         # duration of last BLE connect in ms
            "last_poll_ms": None,            # duration of last GetSystemParameterList in ms
        }
        self._reconnect_requested = asyncio.Event()
        self._connection_allowed = asyncio.Event()
        self._connection_allowed.set()  # auto-connect on startup
        self._shutdown_event = shutdown_event or asyncio.Event()
        self.on_state_updated = None  # Optional async callback(state_dict)

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
        self.mqtt_service.Reconnect += self.request_reconnect

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

            bluetooth_connector = BluetoothLeConnector()
            factory = AquaCleanClientFactory(bluetooth_connector)
            self.client = factory.create_client()

            self.client.DeviceStateChanged += self.on_device_state_changed
            self.client.SOCApplicationVersions += self.soc_application_versions
            self.client.DeviceInitialOperationDate += self.device_initial_operation_date
            self.client.DeviceIdentification += self.on_device_identification
            bluetooth_connector.connection_status_changed_handlers += self.on_connection_status_changed

            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", "No error")
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", f"Connecting to {device_id} ...")
            await self._set_ble_status("connecting", device_address=device_id)

            try:
                t0 = time.perf_counter()
                await self.client.connect(device_id)
                self.device_state["last_connect_ms"] = int((time.perf_counter() - t0) * 1000)
                await self._set_ble_status(
                    "connected",
                    device_name=self.client.Description,
                    device_address=device_id,
                )

                # Record when polling starts so clients can compute a
                # deterministic countdown regardless of when they connect.
                self.device_state["poll_epoch"] = time.time()

                # Run polling, reconnect-request watcher, and shutdown watcher
                # concurrently; whichever finishes first wins.
                polling_task   = asyncio.create_task(self.client.start_polling(interval, on_poll_done=self._on_poll_done))
                reconnect_task = asyncio.create_task(self._reconnect_requested.wait())
                shutdown_task  = asyncio.create_task(self._shutdown_event.wait())

                done, pending = await asyncio.wait(
                    [polling_task, reconnect_task, shutdown_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

                if shutdown_task in done:
                    break  # exit recovery loop

                if reconnect_task in done:
                    self._reconnect_requested.clear()
                    raise ManualReconnectRequested()

                # polling_task finished — re-raise its exception if any
                exc = polling_task.exception() if not polling_task.cancelled() else None
                if exc:
                    raise exc

            except ManualReconnectRequested:
                logger.info("Manual reconnect requested — reconnecting...")
                await self.mqtt_service.send_data_async(
                    f"{self.mqttConfig['topic']}/centralDevice/connected", "Reconnecting..."
                )
            except BLEPeripheralTimeoutError as e:
                logger.warning("BLE Timeout — initiating recovery protocol.")
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", str(e))
                try:
                    await self.client.disconnect()
                except Exception:
                    pass
                await self.wait_for_device_restart(device_id)
            except BleakError as e:
                msg = (
                    f"{e} — "
                    "Try in order: "
                    "1) Power cycle the Geberit. "
                    "2) Restart the Bluetooth service on the host machine. "
                    "3) Restart the host machine."
                )
                logger.warning(msg)
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", msg)
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
                await self._set_ble_status("error", error_msg=msg)
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

        # Recovery loop exited — stop the MQTT background thread
        self.mqtt_service.stop()

    async def _set_ble_status(self, status: str, device_name=None, device_address=None, error_msg=None):
        self.device_state["ble_status"] = status
        if status == "connected":
            self.device_state["ble_connected_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.device_state["ble_device_name"] = device_name
            self.device_state["ble_device_address"] = device_address
            self.device_state["ble_error"] = None
        elif status == "error":
            self.device_state["ble_connected_at"] = None
            self.device_state["poll_epoch"] = None
            self.device_state["last_connect_ms"] = None
            self.device_state["last_poll_ms"] = None
            self.device_state["ble_error"] = error_msg
        elif status in ("disconnected", "connecting"):
            self.device_state["ble_connected_at"] = None
            self.device_state["poll_epoch"] = None
            self.device_state["last_connect_ms"] = None
            self.device_state["last_poll_ms"] = None
            self.device_state["ble_error"] = None
        if self.on_state_updated:
            await self.on_state_updated(self.device_state.copy())

    async def _on_poll_done(self, millis: int):
        self.device_state["last_poll_ms"] = millis
        if self.on_state_updated:
            await self.on_state_updated(self.device_state.copy())

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

    async def wait_for_device_restart(self, device_id):
        """Passively scans until the device drops off BLE, then waits for it to reappear."""
        topic = f"{self.mqttConfig['topic']}/centralDevice/connected"

        # Phase 1: wait for the user to power-cycle the device
        await self.mqtt_service.send_data_async(topic, "Peripheral not responding. Please power cycle the device.")
        logger.info(f"Waiting for device {device_id} to drop off BLE scanner...")
        while not self._shutdown_event.is_set():
            device = await BleakScanner.find_device_by_address(device_id, timeout=3.0)
            if device is None:
                logger.info("Device shut down confirmed.")
                await self.mqtt_service.send_data_async(topic, "Device offline. Waiting for it to power back on...")
                break
            await asyncio.sleep(2)

        # Phase 2: wait for it to boot back up
        logger.info(f"Waiting for device {device_id} to reappear...")
        while not self._shutdown_event.is_set():
            device = await BleakScanner.find_device_by_address(device_id, timeout=3.0)
            if device is not None:
                logger.info("Device back online.")
                await self.mqtt_service.send_data_async(topic, f"Device detected ({device.name} / {device.address}). Reconnecting...")
                await asyncio.sleep(2)
                break
            await asyncio.sleep(2)

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
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", f'{exc_name}: {e}')
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
            sys.exit(1)


class ApiMode:
    """REST API mode: persistent BLE + polling loop, or on-demand per-request connections."""

    def __init__(self):
        mqtt_enabled = config.getboolean("SERVICE", "mqtt_enabled", fallback=True)
        self.ble_connection = config.get("SERVICE", "ble_connection", fallback="persistent")
        api_host = config.get("API", "host", fallback="0.0.0.0")
        api_port = int(config.get("API", "port", fallback="8080"))

        self._shutdown_event = asyncio.Event()

        self.rest_api = RestApiService(api_host, api_port)
        self.rest_api.set_api_mode(self)

        # Always create ServiceMode so ble_connection can be toggled at runtime.
        self.service = ServiceMode(mqtt_enabled=mqtt_enabled, shutdown_event=self._shutdown_event)
        self.service.device_state["ble_connection"] = self.ble_connection
        if self.ble_connection != "persistent":
            # Start in standby — loop waits on _connection_allowed until switched
            self.service._connection_allowed.clear()

        logger.info(f"API mode: ble_connection={self.ble_connection}, mqtt_enabled={mqtt_enabled}, {api_host}:{api_port}")

    async def run(self):
        self.service.on_state_updated = self.rest_api.broadcast_state
        service_task = asyncio.create_task(self.service.run())
        try:
            await self.rest_api.start(self._shutdown_event)
        finally:
            # Ensure the BLE loop also sees the shutdown event
            self._shutdown_event.set()
            # Let the service exit gracefully via the shutdown event —
            # it needs to publish MQTT status before BLE disconnect.
            # Only cancel as a last resort if it doesn't finish in time.
            done, pending = await asyncio.wait({service_task}, timeout=5.0)
            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

    # --- Config endpoints ---

    def get_current_state(self) -> dict:
        """In-memory state snapshot — sync, no BLE connection (safe for SSE initial push)."""
        return dict(self.service.device_state)

    def get_config(self) -> dict:
        return {"ble_connection": self.ble_connection}

    async def set_ble_connection(self, value: str) -> dict:
        if value not in ("persistent", "on-demand"):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Invalid value {value!r}. Use 'persistent' or 'on-demand'.")
        self.ble_connection = value
        self.service.device_state["ble_connection"] = value
        if value == "persistent":
            await self.service.request_reconnect()
        else:
            await self.service.request_disconnect()
        await self.rest_api.broadcast_state(self.service.device_state.copy())
        return {"status": "success", "ble_connection": value}

    # --- REST endpoint implementations ---

    async def get_status(self):
        if self.ble_connection == "persistent":
            return self.service.device_state
        else:
            return await self._on_demand(lambda client: self._fetch_state(client))

    async def get_info(self):
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
            c = self.service.client
            return {
                "sap_number": c.SapNumber,
                "serial_number": c.SerialNumber,
                "production_date": c.ProductionDate,
                "description": c.Description,
                "initial_operation_date": c.InitialOperationDate,
            }
        else:
            return await self._on_demand(lambda client: self._fetch_info(client))

    async def run_command(self, command: str):
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
            await self._execute_command(self.service.client, command)
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

    # --- Data query endpoints ---

    async def get_system_parameters(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
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
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
            result = {"soc_versions": str(self.service.client.soc_application_versions)}
        else:
            result = await self._on_demand(self._fetch_soc_versions)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/SocVersions", result["soc_versions"])
        return result

    async def get_initial_operation_date(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
            result = {"initial_operation_date": str(self.service.client.InitialOperationDate)}
        else:
            result = await self._on_demand(self._fetch_initial_op_date)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/initialOperationDate", result["initial_operation_date"])
        return result

    async def get_identification(self):
        topic = self.service.mqttConfig['topic']
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
            c = self.service.client
            result = {
                "sap_number": c.SapNumber,
                "serial_number": c.SerialNumber,
                "production_date": c.ProductionDate,
                "description": c.Description,
            }
        else:
            result = await self._on_demand(self._fetch_identification)
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/SapNumber",     str(result["sap_number"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/SerialNumber",   str(result["serial_number"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/ProductionDate", str(result["production_date"]))
        await self.service.mqtt_service.send_data_async(f"{topic}/peripheralDevice/information/Identification/Description",    str(result["description"]))
        return result

    async def get_anal_shower_state(self):
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
            return await self._fetch_anal_shower_state(self.service.client)
        else:
            return await self._on_demand(self._fetch_anal_shower_state)

    # --- Helpers ---

    async def _fetch_anal_shower_state(self, client):
        result = await client.base_client.get_system_parameter_list_async([1])
        return {"is_anal_shower_running": result.data_array[0] != 0}

    async def _fetch_soc_versions(self, client):
        versions = await client.base_client.get_soc_application_versions_async()
        return {"soc_versions": str(versions)}

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

    async def _on_demand(self, action):
        """Connect, execute action, disconnect — for on-demand connection mode.
        Publishes connecting/connected/disconnected to MQTT and SSE, mirroring
        the persistent-mode behaviour."""
        device_id = config.get("BLE", "device_id")
        topic = self.service.mqttConfig['topic']
        connector = BluetoothLeConnector()
        # Mirror persistent mode: let the connector publish "True, address, name"
        connector.connection_status_changed_handlers += self.service.on_connection_status_changed
        factory = AquaCleanClientFactory(connector)
        client = factory.create_client()
        try:
            await self.service.mqtt_service.send_data_async(
                f"{topic}/centralDevice/connected", f"Connecting to {device_id} ...")
            await self.service._set_ble_status("connecting", device_address=device_id)
            t0 = time.perf_counter()
            await client.connect_ble_only(device_id)
            connect_ms = int((time.perf_counter() - t0) * 1000)
            self.service.device_state["last_connect_ms"] = connect_ms
            t1 = time.perf_counter()
            result = action(client)
            result = await result if asyncio.iscoroutine(result) else result
            query_ms = int((time.perf_counter() - t1) * 1000)
            timing = {"_connect_ms": connect_ms, "_query_ms": query_ms}
            if isinstance(result, dict):
                result = {**result, **timing}
            else:
                result = timing
            return result
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            await self.service._set_ble_status("disconnected")

    async def _fetch_state(self, client):
        from aquaclean_core.Api.CallClasses.GetSystemParameterList import GetSystemParameterList
        result = await client.base_client.get_system_parameter_list_async([0, 1, 2, 3, 4, 5, 7, 9])
        return {
            "is_user_sitting": result.data_array[0] != 0,
            "is_anal_shower_running": result.data_array[1] != 0,
            "is_lady_shower_running": result.data_array[2] != 0,
            "is_dryer_running": result.data_array[3] != 0,
        }

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
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Unknown command: {command}")


async def run_cli(args):
    """Executes the CLI logic and ensures JSON is always printed."""
    result = {
        "status": "error",
        "command": getattr(args, 'command', None),
        "device": None,
        "serial_number": None,
        "data": {},
        "message": "Unknown error"
    }

    client = None
    try:
        # 1. Internal Validation
        if not args.command:
            raise ValueError("CLI mode requires --command (e.g., --command status)")

        # 2. Resource Initialization
        device_id = args.address or config.get("BLE", "device_id")
        connector = BluetoothLeConnector()
        factory = AquaCleanClientFactory(connector)
        client = factory.create_client()

        logger.info(f"Connecting to {device_id}...")
        await client.connect(device_id)

        # Populate metadata
        result["device"] = client.Description
        result["serial_number"] = client.SerialNumber

        # 3. Command Execution
        if args.command == 'status':
            result["data"]["connection"] = "active"
        elif args.command == 'toggle-lid':
            await client.toggle_lid_position()
            result["data"]["action"] = "lid_toggled"
        elif args.command == 'toggle-anal':
            await client.toggle_anal_shower()
            result["data"]["action"] = "anal_shower_toggled"

        result["status"] = "success"
        result["message"] = f"Command {args.command} completed"

    except Exception as e:
        result["status"] = "error"
        result["message"] = str(e)
        logger.error(f"CLI Error: {e}")
    finally:
        if client:
            await client.disconnect()
        # The ONLY thing sent to stdout
        print(json.dumps(result, indent=2))


async def main(args):
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
            "examples:\n"
            "  %(prog)s --mode cli --command toggle-lid 2>aquaclean_console_app_cli.log\n"
            "\n"
            "  output:\n"
            "  {\n"
            '    "status": "success",\n'
            '    "command": "toggle-lid",\n'
            '    "device": "AquaClean Mera Comfort",\n'
            '    "serial_number": "HB23XXEUXXXXXX",\n'
            '    "data": { "action": "lid_toggled" },\n'
            '    "message": "Command toggle-lid completed"\n'
            "  }\n"
            "\n"
            "CLI results and errors are written to stdout as JSON.\n"
            "Log output goes to stderr (redirect with 2>logfile)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--mode', choices=['service', 'cli', 'api'], default='service')
    parser.add_argument('--command', choices=['toggle-lid', 'toggle-anal', 'status'])
    parser.add_argument('--address')

    args = parser.parse_args()
    run(main(args))
