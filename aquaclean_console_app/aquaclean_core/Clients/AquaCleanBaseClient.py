import asyncio
import threading

import inspect

from binascii import hexlify

import logging

from aquaclean_console_app.aquaclean_core.Message.MessageService                         import MessageService         
from aquaclean_console_app.aquaclean_core.IBluetoothLeConnector                          import IBluetoothLeConnector  
from aquaclean_console_app.aquaclean_core.Frames.FrameService                            import FrameService                           
from aquaclean_console_app.aquaclean_core.Frames.FrameFactory                            import FrameFactory                           
from aquaclean_console_app.aquaclean_core.Frames.FrameValidation                         import FrameValidation as frame_validation                          
from aquaclean_console_app.aquaclean_core.Frames.FrameCollector                          import FrameCollector as frame_collector                                                   
from aquaclean_console_app.aquaclean_core.Message.MessageService                         import MessageService                            
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute                import ApiCallAttribute                                   
from aquaclean_console_app.aquaclean_core.Api.IApiCall                                   import IApiCall                             
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetSystemParameterList         import GetSystemParameterList                    
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetDeviceIdentification        import GetDeviceIdentification                                        
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetSOCApplicationVersions      import GetSOCApplicationVersions                    
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetDeviceInitialOperationDate  import GetDeviceInitialOperationDate                    
from aquaclean_console_app.aquaclean_core.Api.CallClasses.SetCommand                     import SetCommand
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetStatisticsDescale           import GetStatisticsDescale
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetFirmwareVersionList         import GetFirmwareVersionList

from aquaclean_console_app.aquaclean_utils                                               import utils   

from threading import Lock
import re
import pprint


logger = logging.getLogger(__name__)


class BLEPeripheralTimeoutError(Exception):
    """Raised when the BLE peripheral stops responding to requests."""
    pass


class AquaCleanBaseClient:
    def __init__(self, bluetooth_le_connector: IBluetoothLeConnector):  # type: ignore
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        self.bluetooth_le_connector = bluetooth_le_connector
        self.frame_service = FrameService()
        self.frame_factory = FrameFactory()
        self.frame_collector = frame_collector()
        self.message_service = MessageService()
        self.lock = Lock()

        self.context_lookup = {}

        self.build_context_lookup()
        logger.trace(f"self.context_lookup: {self.context_lookup}")

        self._transaction_event = asyncio.Event()

        self.message_context = None
        self.call_count = 0

        self._cleaner_task_str_re = re.compile(r"\S*site-packages/")

        # Process received data from Bluetooth
        self.bluetooth_le_connector.data_received_handlers += self.frame_service.process_data

        # Connection status has changed
        self.bluetooth_le_connector.connection_status_changed_handlers += self.connection_status_changed

        # Send Frame over Bluetooth
        self.frame_service.SendData += self.send_data_async

        # Process complete transaction
        self.frame_service.TransactionCompleteFS += self.on_transaction_completeForBaseClient


    def build_context_lookup(self):
        for name, obj in inspect.getmembers(self):
            if inspect.isclass(obj) and issubclass(obj, IApiCall):
                api_call_attr = getattr(obj, 'ApiCallAttribute', None)
                if api_call_attr:
                    self.context_lookup[api_call_attr] = obj


    def connection_status_changed(self, sender, *args):
        logger.trace(f"connection_status_changed, sender: {sender}, args: {args}")


    async def send_data_async(self, sender, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"in send_data_async, sender: {sender}, data: {data}")
        await self.bluetooth_le_connector.send_message(data)     
        logger.trace(f" send_data_async after sleep")  


    def on_transaction_completeForBaseClient(self, sender, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"on_transaction_completeForBaseClient, sender: {sender}, data: {data}")
        logger.trace(f"self.event_wait_handle.clear()...")

        self.message_context = self.message_service.parse_message1(data)
        context = ApiCallAttribute(context=self.message_context.context, procedure=self.message_context.procedure)
        if context in self.context_lookup:
            logger.trace(f"self.context_lookup[context]: {self.context_lookup[context]}")
        else:
            logger.trace(f"self.context_lookup not found")

        logger.trace("self._transaction_event.set()")
        self._transaction_event.set()
        logger.trace("###################### Transaction: set finished...")


    async def connect_async(self, device_id):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        await self.bluetooth_le_connector.connect_async(device_id)
        await self.frame_service.wait_for_info_frames_async()


    async def get_system_parameter_list_async(self, parameter_list):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        api_call = GetSystemParameterList(parameter_list)
        response = await self.send_request(api_call)
        logger.trace(f"response: {response}")
        result = response.result(self.message_context.result_bytes)
        return result


    async def get_device_initial_operation_date(self):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        api_call = GetDeviceInitialOperationDate()
        logger.debug(f"api_call: {api_call}")
        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"response.get_payload(): {response.get_payload()}")

        self.message_context.context= '\x00'
        self.message_context.procedure='\x86'
        context = ApiCallAttribute(context=self.message_context.context, procedure=self.message_context.procedure)
        if context in self.context_lookup:
            logger.trace(f"self.context_lookup[context]: {self.context_lookup[context]}")
        else:
            logger.trace(f"self.context_lookup not found")

        result= response.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")

        return result 


    async def get_device_identification_async(self, node):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"in get_device_identification_async, node: {node}")
        api_call = GetDeviceIdentification()
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"self.message_context: {self.message_context}")
        logger.debug(f"self.message_context.result_bytes: {self.message_context.result_bytes}")
        
        result = api_call.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")
        logger.debug(f"response.result: {response.result}")
        
        return result   


    async def disconnect(self):
        await self.bluetooth_le_connector.disconnect()

        
    async def SetCommandAsync(self, command):
        logger.debug(f"SetCommandAsync: SetCommand.SetCommand(command): {SetCommand(command)}")
        await self.send_request(SetCommand(command))
        await asyncio.sleep(1)


    # not yet implemented
    #
    # async def get_stored_profile_setting_async(self, profile_setting):
    #     api_call = GetStoredProfileSetting(0, profile_setting)
    #     return (await self.send_request(api_call)).result(self.message_context.ResultBytes)

    # async def set_stored_profile_setting_async(self, profile_setting, setting_value):
    #     api_call = SetStoredProfileSetting(profile_setting, setting_value)
    #     await self.send_request(api_call)

    async def get_statistics_descale_async(self):
        api_call = GetStatisticsDescale()
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"self.message_context.result_bytes: {self.message_context.result_bytes}")

        result = api_call.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")

        return result

    async def get_soc_application_versions_async(self):
        api_call = GetSOCApplicationVersions()
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"self.message_context: {self.message_context}")
        logger.debug(f"self.message_context.result_bytes: {self.message_context.result_bytes}")

        result = api_call.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")
        logger.debug(f"response.result: {response.result}")

        return result

    async def get_firmware_version_list_async(self, payload: bytes = b''):
        api_call = GetFirmwareVersionList(payload)
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"self.message_context.result_bytes: {self.message_context.result_bytes}")

        result = api_call.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")

        return result
    

    def _cleaner_task_str(self, task):
        s = str(task)
        return self._cleaner_task_str_re.sub("", s)


    # https://github.com/simonw/datasette/issues/1733
    def _threads(self):
        threads = list(threading.enumerate())
        d = {
            "num_threads": len(threads),
            "threads": [
                {"name": t.name, "ident": t.ident, "daemon": t.daemon} for t in threads
            ],
        }
        # Only available in Python 3.7+
        if hasattr(asyncio, "all_tasks"):
            tasks = asyncio.all_tasks()
            d.update(
                {
                    "num_tasks": len(tasks),
                    "tasks": [self._cleaner_task_str( t) for t in tasks],
                }
            )
        return d
    

    async def send_request(self, api_call):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.debug(f"Sending {api_call.__class__.__name__}")

        while self.call_count > 0:
            logger.trace(f"self.call_count: {self.call_count} > 0")
            await asyncio.sleep(0.1)
        
        if self.call_count <= 0:
            logger.trace(f"self.call_count: {self.call_count} <= 0")

        with self.lock:
            self.call_count += 1

        data = self.build_payload(api_call)
        logger.trace(f"After build_payload: data: {data.hex()}")

        message = self.message_service.build_message(data)
        logger.trace(f"message: {message}")
        logger.trace(f"message.serialize(): {message.serialize().hex()}")

        frame = self.frame_factory.BuildSingleFrame(message.serialize())
        logger.trace(f"frame: {frame}")
        logger.trace(f"type(frame): {type(frame)}")

        logger.trace(f"hexlify(frame.Payload): {hexlify(frame.Payload)}")

        serializedFrame = frame.serialize()
        logger.trace(f"serializedFrame.hex(): {serializedFrame.hex()}")
        logger.trace(f"frame.serialize().hex(): {frame.serialize().hex()}")

        logger.trace(f"vor await self.frame_service.send_frame_async(frame)")

        # Clear the event before sending so we don't pick up a stale signal from
        # a previous transaction that fired while the event loop was busy.
        self._transaction_event.clear()

        await self.frame_service.send_frame_async(frame)

        await asyncio.sleep(0.01)

        logger.trace(f"self._threads(): \n{pprint.pformat(self._threads())}\n")

        # Wait for on_transaction_completeForBaseClient to set _transaction_event.
        # Using asyncio.Event + asyncio.wait_for instead of threading.Queue.get():
        #   - does NOT block the event loop between iterations
        #   - BLE notification callbacks can fire immediately during the await
        #   - asyncio.timeout() in the caller can cancel cleanly at await points
        #
        # Normal request/response cycle is ~600 ms; 5 s is a generous safety margin.
        timeout_seconds = 5.0
        try:
            logger.trace(f"awaiting _transaction_event (timeout={timeout_seconds}s)...")
            await asyncio.wait_for(self._transaction_event.wait(), timeout=timeout_seconds)
            logger.trace(f"_transaction_event set — transaction complete")
        except asyncio.TimeoutError:
            with self.lock:
                self.call_count -= 1
            error_msg = (
                f"No response from BLE peripheral "
                f"'{self.bluetooth_le_connector.device_name}' "
                f"({self.bluetooth_le_connector.device_address}). "
                f"Usually a restart of the BLE peripheral is required."
            )
            logger.error(error_msg)
            raise BLEPeripheralTimeoutError(error_msg)

        logger.trace(f"nach await self.frame_service.send_frame_async(frame)")

        with self.lock:
            self.call_count -= 1

        return api_call
    

    def build_payload(self, api_call: IApiCall ) -> bytes: # type: ignore
        logger.trace(f"api_call: {api_call}")

        api_call_attribute = api_call.get_api_call_attribute()

        if api_call_attribute:
            logger.trace(f"In build_payload: api_call_attribute: {api_call_attribute}")
            # In build_payload: api_call_attribute: ApiCallAttribute: context=0x00, procedure=0x82, node=0x01
        else:
            logger.trace(f"In build_payload: api_call_attribute is None")

        if api_call_attribute is None:
            raise Exception("No ApiCallAttribute set on object")

        payload = api_call.get_payload()
        logger.trace(f"api_call.get_payload(): {payload.hex()}")

        data = bytearray(4 + len(payload))
        data[0] = api_call_attribute.node
        data[1] = api_call_attribute.context
        data[2] = api_call_attribute.procedure
        data[3] = len(payload)
        logger.trace(f"payload, payload length after data[3]: {data.hex()}")
        logger.trace(f"data after data[3]: {data.hex()}")

        data[4:] = payload
        logger.trace(f"After copying payload: {data.hex()}")
        return bytes(data)


