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
    """Executes a single command without MQTT overhead."""
    device_id = args.address or config.get("BLE", "device_id")
    connector = BluetoothLeConnector()
    factory = AquaCleanClientFactory(connector)
    client = factory.create_client()
    try:
        await client.connect(device_id)
        if args.command == 'toggle-lid':
            await client.toggle_lid_position()
        elif args.command == 'toggle-anal':
            await client.toggle_anal_shower()
        elif args.command == 'status':
            print(f"Device: {client.Description} - SN: {client.SerialNumber}")
        print(f"Success: {args.command}")
    finally:
        await client.disconnect()

async def main():
    parser = argparse.ArgumentParser(description="Geberit AquaClean Controller")
    parser.add_argument('--mode', choices=['service', 'cli'], default='service', help="Operation mode")
    parser.add_argument('--command', choices=['toggle-lid', 'toggle-anal', 'status'], help="CLI Command")
    parser.add_argument('--address', help="Override BLE MAC address")
    
    args = parser.parse_args()

    if args.mode == 'service':
        service = ServiceMode()
        await shutdown_waits_for(service.run())
    else:
        if not args.command:
            print("Error: CLI mode requires --command")
            sys.exit(1)
        await run_cli(args)

if __name__ == "__main__":
    run(main())