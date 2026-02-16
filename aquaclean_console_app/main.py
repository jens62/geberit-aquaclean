import json
import asyncio
import logging
import os
import configparser
import argparse
import traceback
import sys
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


class ServiceMode:
    def __init__(self, mqtt_enabled=True):
        self.client = None
        self.mqtt_initialized_wait_queue = Queue()
        self.device_state = {
            "is_user_sitting": None,
            "is_anal_shower_running": None,
            "is_lady_shower_running": None,
            "is_dryer_running": None,
        }
        self._reconnect_requested = asyncio.Event()
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

        # Subscribe MQTT handlers once — handlers reference self.client
        # which is updated each iteration of the recovery loop below
        self.mqtt_service.ToggleLidPosition += self.on_toggle_lid_message
        self.mqtt_service.Reconnect += self.request_reconnect

        # --- Main Recovery Loop ---
        while True:
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

            try:
                await self.client.connect(device_id)

                # Run polling and reconnect-request watcher concurrently;
                # whichever finishes first wins.
                polling_task = asyncio.create_task(self.client.start_polling(interval))
                reconnect_task = asyncio.create_task(self._reconnect_requested.wait())

                done, pending = await asyncio.wait(
                    [polling_task, reconnect_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for t in pending:
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

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
                await asyncio.sleep(30)
            except Exception as e:
                await self.handle_exception(e)
            finally:
                try:
                    await self.client.disconnect()
                except Exception:
                    pass

    async def request_reconnect(self):
        """Trigger a clean BLE reconnect (callable from MQTT or REST API)."""
        logger.info("Reconnect requested.")
        self._reconnect_requested.set()

    async def wait_for_device_restart(self, device_id):
        """Passively scans until the device drops off BLE, then waits for it to reappear."""
        topic = f"{self.mqttConfig['topic']}/centralDevice/connected"

        # Phase 1: wait for the user to power-cycle the device
        await self.mqtt_service.send_data_async(topic, "Peripheral not responding. Please power cycle the device.")
        logger.info(f"Waiting for device {device_id} to drop off BLE scanner...")
        while True:
            device = await BleakScanner.find_device_by_address(device_id, timeout=3.0)
            if device is None:
                logger.info("Device shut down confirmed.")
                await self.mqtt_service.send_data_async(topic, "Device offline. Waiting for it to power back on...")
                break
            await asyncio.sleep(2)

        # Phase 2: wait for it to boot back up
        logger.info(f"Waiting for device {device_id} to reappear...")
        while True:
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

        self.rest_api = RestApiService(api_host, api_port)
        self.rest_api.set_api_mode(self)

        if self.ble_connection == "persistent":
            self.service = ServiceMode(mqtt_enabled=mqtt_enabled)
        else:
            self.service = None

        logger.info(f"API mode: ble_connection={self.ble_connection}, mqtt_enabled={mqtt_enabled}, {api_host}:{api_port}")

    async def run(self):
        if self.ble_connection == "persistent":
            self.service.on_state_updated = self.rest_api.broadcast_state
            # Run the BLE service as a background task so that uvicorn (which
            # installs its own SIGINT handler) acts as the foreground process.
            # When uvicorn exits on Ctrl+C, the finally block cancels the service.
            service_task = asyncio.create_task(self.service.run())
            try:
                await self.rest_api.start()
            finally:
                service_task.cancel()
                # Wait at most 3 s for the service to clean up; if it takes
                # longer (e.g. a slow BLE disconnect) we move on and let the
                # event loop stop handle the rest.
                await asyncio.wait({service_task}, timeout=3.0)
        else:
            await self.rest_api.start()

    # --- REST endpoint implementations ---

    async def get_status(self):
        if self.ble_connection == "persistent":
            if self.service.client is None:
                from fastapi import HTTPException
                raise HTTPException(status_code=503, detail="BLE client not connected")
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
            await self._on_demand(lambda client: self._execute_command(client, command))

    async def do_connect(self):
        if self.ble_connection == "persistent":
            await self.service.request_reconnect()
            return {"status": "success", "action": "reconnect requested"}
        else:
            return await self._on_demand(lambda client: self._fetch_info(client))

    async def do_disconnect(self):
        if self.ble_connection == "persistent":
            await self.service.request_reconnect()
            return {"status": "success", "action": "reconnect requested"}
        else:
            return {"status": "success", "action": "no persistent connection to disconnect"}

    async def do_reconnect(self):
        if self.ble_connection == "persistent":
            await self.service.request_reconnect()
            return {"status": "success", "action": "reconnect requested"}
        else:
            return await self._on_demand(lambda client: self._fetch_info(client))

    # --- Helpers ---

    async def _on_demand(self, action):
        """Connect, execute action, disconnect — for on-demand connection mode."""
        device_id = config.get("BLE", "device_id")
        connector = BluetoothLeConnector()
        factory = AquaCleanClientFactory(connector)
        client = factory.create_client()
        try:
            await client.connect(device_id)
            return await action(client)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

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
        return {
            "sap_number": client.SapNumber,
            "serial_number": client.SerialNumber,
            "production_date": client.ProductionDate,
            "description": client.Description,
            "initial_operation_date": client.InitialOperationDate,
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
