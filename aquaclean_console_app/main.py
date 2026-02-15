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

from aquaclean_core.Clients.AquaCleanClient                   import AquaCleanClient
from aquaclean_core.IAquaCleanClient                          import IAquaCleanClient       
from aquaclean_core.AquaCleanClientFactory                    import AquaCleanClientFactory 
from aquaclean_core.Api.CallClasses.Dtos.DeviceIdentification import DeviceIdentification   
from aquaclean_core.Message.MessageService                    import MessageService         
from aquaclean_core.IBluetoothLeConnector                     import IBluetoothLeConnector  
from bluetooth_le.LE.BluetoothLeConnector                     import BluetoothLeConnector
from MqttService                                              import MqttService as Mqtt
from myEvent                                                  import myEvent   
from aquaclean_utils                                          import utils
from MqttService                                              import MqttService as Mqtt

# --- Configuration & Logging Setup ---
__location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
iniFile = os.path.join(__location__, 'config.ini')
config = configparser.ConfigParser(allow_no_value=False)
config.read(['config.ini', os.path.expanduser(iniFile)])

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

class ServiceMode:
    """Restores the full logic of your original MainPage class."""
    def __init__(self):
        self.mqttConfig = dict(config.items('MQTT'))
        self.mqtt_service = Mqtt(self.mqttConfig)
        self.client = None
        self.mqtt_initialized_wait_queue = Queue()

    async def run(self):
        # 1. Initialize MQTT with wait queue (Original Logic)
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

        # 2. Setup BLE Client
        device_id = config.get("BLE", "device_id")
        try:
            interval = float(config.get("POLL", "interval"))
        except Exception:
            interval = 2.5

        bluetooth_connector = BluetoothLeConnector()
        factory = AquaCleanClientFactory(bluetooth_connector)
        self.client = factory.create_client()

        # 3. Subscribe all original handlers
        self.client.DeviceStateChanged += self.on_device_state_changed
        self.client.SOCApplicationVersions += self.soc_application_versions
        self.client.DeviceInitialOperationDate += self.device_initial_operation_date
        self.client.DeviceIdentification += self.on_device_identification
        bluetooth_connector.connection_status_changed_handlers += self.on_connection_status_changed
        self.mqtt_service.ToggleLidPosition += self.on_toggle_lid_message

        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", str(None))
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", f"Connecting to {device_id} ...")

        try:
            await self.client.connect(device_id)
            await self.client.start_polling(interval)
        except Exception as e:
            await self.handle_exception(e)
        finally:
            await self.client.disconnect()

    # --- Restored Original Handlers (Verbatim) ---
    async def on_device_state_changed(self, sender, args):
        topic = self.mqttConfig['topic']
        if "IsUserSitting" in args.__dict__ and args.IsUserSitting is not None:
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isUserSitting", str(args.IsUserSitting))
        if "IsAnalShowerRunning" in args.__dict__ and args.IsAnalShowerRunning is not None:
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isAnalShowerRunning", str(args.IsAnalShowerRunning))
        if "IsLadyShowerRunning" in args.__dict__ and args.IsLadyShowerRunning is not None:
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isLadyShowerRunning", str(args.IsLadyShowerRunning))
        if "IsDryerRunning" in args.__dict__ and args.IsDryerRunning is not None:
            await self.mqtt_service.send_data_async(f"{topic}/peripheralDevice/monitor/isDryerRunning", str(args.IsDryerRunning))

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
            # Add more status data here if available
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
        # Service mode uses shutdown_waits_for to run indefinitely
        await shutdown_waits_for(service.run())
    else:            
        # Execute the command
        await run_cli(args)
        
        loop = asyncio.get_running_loop()
        loop.stop()

class JsonArgumentParser(argparse.ArgumentParser):
    """Custom parser that forces all output to JSON format."""
    
    def _print_message(self, message, file=None):
        # Prevent any raw text from leaking to stdout/stderr
        pass

    def error(self, message):
        """Called on invalid choices, missing arguments, or bad types."""
        result = {
            "status": "error",
            "command": "invalid",
            "message": f"Argument Error: {message}",
            "data": {}
        }
        # Print JSON to stdout for machine parsing
        print(json.dumps(result, indent=2))
        sys.exit(0) 

    def exit(self, status=0, message=None):
        # Override exit to prevent standard text printing
        if message:
            self.error(message)
        sys.exit(status)

if __name__ == "__main__":
    parser = JsonArgumentParser(description="Geberit AquaClean Controller", add_help=False)

    # Add --help manually if you want it to return JSON too
    parser.add_argument('-h', '--help', action='store_true')
    parser.add_argument('--mode', choices=['service', 'cli'], default='service')
    parser.add_argument('--command', choices=['toggle-lid', 'toggle-anal', 'status'])
    parser.add_argument('--address')

    args = parser.parse_args()

    if getattr(args, 'help', False):
        # Dynamically extract options and choices from the parser
        help_data = {
            "status": "help",
            "description": parser.description,
            "options": [],
            "commands": []
        }
        
        for action in parser._actions:
            # Get the flags (e.g., --mode, --command)
            opts = action.option_strings
            help_data["options"].extend(opts)
            
            # If the option has 'choices', identify them as our commands
            if action.dest == 'command' and action.choices:
                help_data["commands"] = list(action.choices)

        print(json.dumps(help_data, indent=2))
        sys.exit(0)

    # 3. Pass the parsed args into the async loop
    run(main(args))