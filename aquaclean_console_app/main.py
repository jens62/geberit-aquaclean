
import asyncio
import logging

from aquaclean_core.Clients                                    import AquaCleanClient
from aquaclean_core.IAquaCleanClient                           import IAquaCleanClient       
from aquaclean_core.AquaCleanClientFactory                     import AquaCleanClientFactory 
from aquaclean_core.Api.CallClasses.Dtos.DeviceIdentification  import DeviceIdentification   
from aquaclean_core.Message.MessageService                     import MessageService         
from aquaclean_core.IBluetoothLeConnector                      import IBluetoothLeConnector  
from bluetooth_le.LE.BluetoothLeConnector                      import BluetoothLeConnector
from MqttService                                               import MqttService as Mqtt
from myEvent                                                   import myEvent   
from aquaclean_utils                                           import utils   

import os
import configparser

from aiorun import run, shutdown_waits_for
from signal import SIGINT, SIGTERM

from bleak import BleakError
import traceback

__location__ = os.path.realpath(
    os.path.join(os.getcwd(), os.path.dirname(__file__)))

iniFile = os.path.join(__location__, 'config.ini')
config = configparser.ConfigParser(allow_no_value=False)
config.read(['config.ini', os.path.expanduser( iniFile)])

# Setup logging
from haggis import logs

logs.add_logging_level('TRACE', logging.DEBUG - 5)
logs.add_logging_level('SILLY', logging.DEBUG - 7)

log_level = config.get("LOGGING", "log_level")
logging.basicConfig(
    level=log_level,
    format="%(asctime)-15s %(name)-8s %(lineno)d %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


class MainPage:
    def __init__(self):
        self.mqttConfig = dict(config.items('MQTT'))
        self.mqtt_service = Mqtt(self.mqttConfig)
        self.client = None        

    async def initialize(self):
        await self.mqtt_service.start_async( asyncio.get_running_loop())
        await self.test3()

    async def test3(self):
        device_id = config.get("BLE", "device_id")

        bluetooth_connector = BluetoothLeConnector()
        factory = AquaCleanClientFactory(bluetooth_connector)

        # for some reason self.mqtt_service.ToggleLidPosition was not available without wait??
        # TODO sync (with means of a queue, similar to on_transaction_completeForBaseClient in AquaCleanBaseClient),
        # because mqtt runs in an other thread)
        await asyncio.sleep(3)
        self.mqtt_service.ToggleLidPosition += self.on_toggleLidMessage
    
        self.client = factory.create_client()

        self.client.DeviceStateChanged += self.on_device_state_changed

        
        self.client.SOCApplicationVersions += self.soc_application_versions
        self.client.DeviceInitialOperationDate += self.device_initial_operation_date
        self.client.DeviceIdentification += self.on_device_identification
        bluetooth_connector.connection_status_changed_handlers += self.on_connection_status_changed

        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", str(None))
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", f"Connecting to {device_id} ...")

        try:
            await self.client.connect(device_id)
        except Exception as e:
            exception_class_name = get_full_class_name(e)
            logger.trace(f'{exception_class_name}: {e}')
            if exception_class_name == "bleak.exc.BleakError" and (str(e) == "Service Discovery has not been performed yet"):
                logger.error(f'this exception is ok on shutdown')
            else:
                print ('{exception_class_name}: {e}')
                print(traceback.format_exc())
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/error", f'{exception_class_name}: {e}, see log for details')
                await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(False))
                if exception_class_name == "bleak.exc.BleakError":
                    if hasattr(e, 'args'):
                        for arg in e.args:
                            logger.error(f"arg: {arg}")
                    logger.error(f'Check address or restart peripheral device (Geberit AquaClean) and wait a little while.')
                else:
                    logger.trace(f'only e: {e}')
                    logger.trace(f'only e.args: {e.args}')
                    logger.trace(f'only e.__dict__: {e.__dict__}')
                    logger.trace(f'only dir(e): {dir(e)}')
                    logger.trace(f'only vars(e): {vars(e)}')

                    if hasattr(e, 'args'):
                        for arg in e.args:
                            logger.error(f"arg: {arg}")

                    logger.error(f'Restart central (machine the script is running on) and peripheral device (Geberit AquaClean) and wait a little while.')

                    # ...
                    # bleak.backends.bluezdbus.client 211 DEBUG: Connecting to BlueZ path /org/bluez/hci0/dev_38_AB_41_2A_0D_67
                    # bleak.backends.bluezdbus.manager 872 DEBUG: received D-Bus signal: org.freedesktop.DBus.Properties.PropertiesChanged (/org/bluez/hci0/dev_38_AB_41_2A_0D_67): ['org.bluez.Device1', {'Connected': <dbus_fast.signature.Variant ('b', True)>}, []]
                    # bleak.backends.bluezdbus.client 235 DEBUG: retry due to le-connection-abort-by-local
                    # ...
                    # __main__ 91 ERROR: TimeoutError:
                    print(e)
            exit(1)
        finally:
            logger.trace(f'finally...')
            await self.client.disconnect()
 
      
    async def dummy(self):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        await asyncio.sleep(1)
        logger.trace(f"end of {utils.currentClassName()}.{utils.currentFuncName()}")


    async def on_toggleLidMessage(self):
        logger.trace(f"on_toggleLidMessage")
        # await self.dummy()
        await asyncio.sleep(0.01)
        await self.client.toggle_lid_position()
        await asyncio.sleep(0.01)


    async def on_device_identification(self, sender, args):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"on_deviceIdentification, sender: {sender}, args: {args}")
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/Identification/SapNumber", str(args.sap_number))
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/Identification/SerialNumber", str(args.serial_number))
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/Identification/ProductionDate", str(args.production_date))
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/Identification/Description", str(args.description))


    async def soc_application_versions(self, sender, args):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"soc_application_versions, sender: {sender}, args: {args}")
        # not meaningful for me
        # await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/SOCApplicationVersions", str(args))


    async def device_initial_operation_date(self, sender, args):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"device_initial_operation_date, sender: {sender}, args: {args}")
        await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/information/initialOperationDate", str(args))


    async def on_device_state_changed(self, sender, args):
        logger.trace(f"on_device_state_changed, sender: {sender}, args: {args}")

        if "IsUserSitting" in args.__dict__ and not (args.IsUserSitting == None):
            logger.debug(f"IsUserSitting={args.IsUserSitting}")

            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/monitor/isUserSitting", str(args.IsUserSitting))

        if "IsAnalShowerRunning" in args.__dict__ and not (args.IsAnalShowerRunning == None):
            logger.debug(f"IsAnalShowerRunning={args.IsAnalShowerRunning}")

            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/monitor/isAnalShowerRunning", str(args.IsAnalShowerRunning))

        if "IsLadyShowerRunning" in args.__dict__ and not (args.IsLadyShowerRunning == None):
            logger.debug(f"IsLadyShowerRunning={args.IsLadyShowerRunning}")
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/monitor/isLadyShowerRunning", str(args.IsLadyShowerRunning))
    
        if "IsDryerRunning" in args.__dict__ and not (args.IsDryerRunning == None):
            logger.debug(f"IsLadyShowerRunning={args.IsDryerRunning}")
            await self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/peripheralDevice/monitor/isDryerRunning", str(args.IsDryerRunning))        
   

    def on_connection_status_changed(self, sender, *args):
        logger.trace(f"IsConnected={args}")
        first = True
        for arg in args:
            if first:
                values = str(arg)
                first = False
            else:
                values += ", " + str(arg)

        # asyncio.create_task(self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", str(args)))
        asyncio.create_task(self.mqtt_service.send_data_async(f"{self.mqttConfig['topic']}/centralDevice/connected", values))


def get_full_class_name(obj):
    module = obj.__class__.__module__
    if module is None or module == str.__class__.__module__:
        return obj.__class__.__name__
    return module + '.' + obj.__class__.__name__


def do_cleanup():
    logger.trace("do_cleanup() called (got asyncio.CancelledError)")
    main_page.on_connection_status_changed( None, False)

# Run the MainPage class
main_page = MainPage()
# asyncio.run(main_page.init_task)
#asyncio.run(main_page.initialize())


# https://stackoverflow.com/a/58840987
# async def main_coro():
#     try:
#         await main_page.initialize()
#     except asyncio.CancelledError:
#         do_cleanup()

# if __name__ == "__main__":
#     loop = asyncio.get_event_loop()
#     main_task = asyncio.ensure_future(main_coro())
#     for signal in [SIGINT, SIGTERM]:
#         loop.add_signal_handler(signal, main_task.cancel)
#     try:
#         loop.run_until_complete(main_task)
#     finally:
#         logger.trace("closing loop...")
#         loop.close()


def on_shutdown():
    print( 'on Shutdown)')


# main coroutine
async def main_corofn():
    main_page = MainPage()
    await main_page.initialize()

async def main():
    try:
        await shutdown_waits_for(main_corofn())
    except asyncio.CancelledError:
        # MainPage().on_connection_status_changed( None, False)
        # see https://github.com/hbldh/bleak/issues/875
        # BleakClientBlueZDBus._cleanup_all()
        # sys:1: ResourceWarning: unclosed <socket.socket [closed] fd=10, family=1, type=1, proto=0>
        print('You pressed Ctrl+C (signal.SIGINT)')

if __name__ == "__main__":
    # run the asyncio program
    run(main())


