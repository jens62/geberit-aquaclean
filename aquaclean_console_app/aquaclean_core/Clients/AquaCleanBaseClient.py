import asyncio
import threading
import time

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

from aquaclean_console_app.aquaclean_utils                                               import utils   

from threading import Lock
from queue import Queue, Empty
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

        self.event_wait_queue = Queue()

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

        logger.trace('self.event_wait_queue.put("Transaction finished")')
        self.event_wait_queue.put("Transaction finished")
        logger.trace(f"###################### Transaction: set finished...")  


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
        await self.frame_service.send_frame_async(frame)

        await asyncio.sleep(0.01)

        logger.trace(f"self._threads(): \n{pprint.pformat(self._threads())}\n")

        # We need to wait for data from on_transaction_complete
        # The request is sent to the devive over bluetooth: "in function BluetoothLeConnector.send_message called by AquaCleanBaseClient.send_data_async"
        # The response is received over bluetooth: "BluetoothLeConnector: _on_data_received" at any time
        # When the data for a transaction is complete ()"in function AquaCleanBaseClient.on_transaction_completeForBaseClient called by EventHandler.__call__") "self.event_wait_queue" is filled
        # and we can continue.
        #
        # Typical sequence:
        #
        # 2024-12-16 11:50:36,704 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 224 TRACE: in function AquaCleanBaseClient.send_request called by AquaCleanBaseClient.get_system_parameter_list_async
        # 2024-12-16 11:50:36,709 geberit-aquaclean.aquaclean-core.Message.CrcMessage 59 TRACE: in function CrcMessage.serialize called by AquaCleanBaseClient.send_request
        # 2024-12-16 11:50:36,709 geberit-aquaclean.aquaclean-core.Message.CrcMessage 59 TRACE: in function CrcMessage.serialize called by AquaCleanBaseClient.send_request
        # 2024-12-16 11:50:36,711 geberit-aquaclean.aquaclean-core.Frames.FrameService 148 TRACE: in function FrameService.send_frame_async called by AquaCleanBaseClient.send_request
        # 2024-12-16 11:50:36,712 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 109 TRACE: in function AquaCleanBaseClient.send_data_async called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,713 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 116 TRACE: in function BluetoothLeConnector.send_message called by AquaCleanBaseClient.send_data_async
        # 2024-12-16 11:50:36,713 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 118 TRACE: Sending data to characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 data: 1104FF00116EE101010D0D080001020304050609
        # 2024-12-16 11:50:36,774 bleak.backends.bluezdbus.client 885 DEBUG: Write Characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 | /org/bluez/hci0/dev_38_AB_41_2A_0D_67/service0001/char0002: bytearray(b'\x11\x04\xff\x00\x11n\xe1\x01\x01\r\r\x08\x00\x01\x02\x03\x04\x05\x06\t')
        # 2024-12-16 11:50:36,774 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 122 TRACE: result: None
        # 2024-12-16 11:50:36,774 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 112 TRACE:  send_data_async after sleep
        # 2024-12-16 11:50:36,776 bleak.backends.bluezdbus.manager 872 DEBUG: received D-Bus signal: org.freedesktop.DBus.Properties.PropertiesChanged (/org/bluez/hci0/dev_38_AB_41_2A_0D_67/service0001/char000e): ['org.bluez.GattCharacteristic1', {'Value': <dbus_fast.signature.Variant ('ay', bytearray(b"p\x00\x0c\'\x01\x00\x00\x00\x00\x00\x00\x00\x00\xb7\t\x01\x00\x00\r\xf3"))>}, []]
        # 2024-12-16 11:50:36,777 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 104 TRACE: BluetoothLeConnector: _on_data_received
        # 2024-12-16 11:50:36,778 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 105 TRACE: Received data from characteristic 3334429d-90f3-4c41-a02d-5cb3a53e0000 data: 70000C27010000000000000000B7090100000DF3
        # 2024-12-16 11:50:36,778 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:36,778 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:36,778 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:36,778 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:36,779 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:36,779 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:36,779 geberit-aquaclean.aquaclean-core.Frames.FrameService 66 TRACE: in function FrameService.process_data called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,780 geberit-aquaclean.aquaclean-core.Frames.FrameService 157 TRACE: in function FrameService._handle_control_frame called by FrameService.process_data
        # 2024-12-16 11:50:36,781 geberit-aquaclean.aquaclean-core.Frames.FrameService 157 TRACE: in function FrameService._handle_control_frame called by FrameService.process_data
        # 2024-12-16 11:50:36,900 geberit-aquaclean.aquaclean-core.Frames.FrameService 66 TRACE: in function FrameService.process_data called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,901 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 30 TRACE: in function FrameCollector.start_transaction called by FrameService.process_data
        # 2024-12-16 11:50:36,902 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 50 TRACE: in function FrameCollector.add_frame called by FrameService.process_data
        # 2024-12-16 11:50:36,902 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 51 TRACE: frame_index: 0, Payload: 0500004210690001010D3D0700000000000100
        # 2024-12-16 11:50:36,902 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 61 TRACE: Received frame 1 of 4: Payload=0500004210690001010D3D0700000000000100
        # 2024-12-16 11:50:36,902 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 46 TRACE: Controlling bitmap changed with frameNumber 0 => 00000001 Bitmap: 0100000000000000
        # 2024-12-16 11:50:36,903 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 74 TRACE: len(self.frame_data): 1, self.expected_frames: 4
        # 2024-12-16 11:50:36,903 geberit-aquaclean.aquaclean-core.Frames.FrameService 89 TRACE: nach self.frame_collector.add_frame
        # 2024-12-16 11:50:36,903 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 104 TRACE: BluetoothLeConnector: _on_data_received
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 105 TRACE: Received data from characteristic 3334429d-90f3-4c41-a02d-5cb3a63e0000 data: 1200000002000000000300000000040000000005
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:36,904 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameService 66 TRACE: in function FrameService.process_data called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameService 67 TRACE: process_data, data: b'1200000002000000000300000000040000000005'
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 68 TRACE: in CreateFrameFromBytes
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 70 TRACE: frameType: 0
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 75 TRACE: SINGLE Frame
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameService 76 DEBUG: Processing new Frame: SingleFrame: <class 'geberit-aquaclean.aquaclean-core.Frames.Frames.SingleFrame.SingleFrame'>
        # 2024-12-16 11:50:36,905 geberit-aquaclean.aquaclean-core.Frames.FrameService 77 TRACE: frame.FrameType: 0
        # 2024-12-16 11:50:36,906 geberit-aquaclean.aquaclean-core.Frames.FrameService 80 TRACE: Handling frame type SINGLE
        # 2024-12-16 11:50:36,906 geberit-aquaclean.aquaclean-core.Frames.FrameService 83 TRACE: hexlify(single_frame.Payload): b'00000002000000000300000000040000000005'
        # 2024-12-16 11:50:36,906 geberit-aquaclean.aquaclean-core.Frames.FrameService 91 TRACE: not is single_frame.IsSubFrameCount:
        # 2024-12-16 11:50:36,906 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 50 TRACE: in function FrameCollector.add_frame called by FrameService.process_data
        # 2024-12-16 11:50:36,906 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 51 TRACE: frame_index: 1, Payload: 00000002000000000300000000040000000005
        # 2024-12-16 11:50:36,907 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 61 TRACE: Received frame 2 of 4: Payload=00000002000000000300000000040000000005
        # 2024-12-16 11:50:36,907 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 46 TRACE: Controlling bitmap changed with frameNumber 1 => 00000011 Bitmap: 0300000000000000
        # 2024-12-16 11:50:36,907 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 74 TRACE: len(self.frame_data): 2, self.expected_frames: 4
        # 2024-12-16 11:50:36,907 geberit-aquaclean.aquaclean-core.Frames.FrameService 93 TRACE: nach self.frame_collector.add_frame
        # 2024-12-16 11:50:36,908 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 104 TRACE: BluetoothLeConnector: _on_data_received
        # 2024-12-16 11:50:36,908 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 105 TRACE: Received data from characteristic 3334429d-90f3-4c41-a02d-5cb3a73e0000 data: 1400000000060000000000000000000000000000
        # 2024-12-16 11:50:36,908 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:36,908 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:36,908 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:36,908 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-core.Frames.FrameService 66 TRACE: in function FrameService.process_data called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-core.Frames.FrameService 67 TRACE: process_data, data: b'1400000000060000000000000000000000000000'
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 68 TRACE: in CreateFrameFromBytes
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 70 TRACE: frameType: 0
        # 2024-12-16 11:50:36,909 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 75 TRACE: SINGLE Frame
        # 2024-12-16 11:50:36,910 geberit-aquaclean.aquaclean-core.Frames.FrameService 76 DEBUG: Processing new Frame: SingleFrame: <class 'geberit-aquaclean.aquaclean-core.Frames.Frames.SingleFrame.SingleFrame'>
        # 2024-12-16 11:50:36,910 geberit-aquaclean.aquaclean-core.Frames.FrameService 77 TRACE: frame.FrameType: 0
        # 2024-12-16 11:50:36,910 geberit-aquaclean.aquaclean-core.Frames.FrameService 80 TRACE: Handling frame type SINGLE
        # 2024-12-16 11:50:36,910 geberit-aquaclean.aquaclean-core.Frames.FrameService 83 TRACE: hexlify(single_frame.Payload): b'00000000060000000000000000000000000000'
        # 2024-12-16 11:50:36,910 geberit-aquaclean.aquaclean-core.Frames.FrameService 91 TRACE: not is single_frame.IsSubFrameCount:
        # 2024-12-16 11:50:36,910 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 50 TRACE: in function FrameCollector.add_frame called by FrameService.process_data
        # 2024-12-16 11:50:36,911 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 51 TRACE: frame_index: 2, Payload: 00000000060000000000000000000000000000
        # 2024-12-16 11:50:36,911 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 61 TRACE: Received frame 3 of 4: Payload=00000000060000000000000000000000000000
        # 2024-12-16 11:50:36,911 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 46 TRACE: Controlling bitmap changed with frameNumber 2 => 00000111 Bitmap: 0700000000000000
        # 2024-12-16 11:50:36,911 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 74 TRACE: len(self.frame_data): 3, self.expected_frames: 4
        # 2024-12-16 11:50:36,911 geberit-aquaclean.aquaclean-core.Frames.FrameService 93 TRACE: nach self.frame_collector.add_frame
        # 2024-12-16 11:50:36,912 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 104 TRACE: BluetoothLeConnector: _on_data_received
        # 2024-12-16 11:50:36,912 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 105 TRACE: Received data from characteristic 3334429d-90f3-4c41-a02d-5cb3a83e0000 data: 160000000000000000000000000000006F000000
        # 2024-12-16 11:50:36,912 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:36,912 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:36,912 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-core.Frames.FrameService 66 TRACE: in function FrameService.process_data called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-core.Frames.FrameService 67 TRACE: process_data, data: b'160000000000000000000000000000006f000000'
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 68 TRACE: in CreateFrameFromBytes
        # 2024-12-16 11:50:36,913 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 70 TRACE: frameType: 0
        # 2024-12-16 11:50:36,914 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 75 TRACE: SINGLE Frame
        # 2024-12-16 11:50:36,914 geberit-aquaclean.aquaclean-core.Frames.FrameService 76 DEBUG: Processing new Frame: SingleFrame: <class 'geberit-aquaclean.aquaclean-core.Frames.Frames.SingleFrame.SingleFrame'>
        # 2024-12-16 11:50:36,914 geberit-aquaclean.aquaclean-core.Frames.FrameService 77 TRACE: frame.FrameType: 0
        # 2024-12-16 11:50:36,914 geberit-aquaclean.aquaclean-core.Frames.FrameService 80 TRACE: Handling frame type SINGLE
        # 2024-12-16 11:50:36,914 geberit-aquaclean.aquaclean-core.Frames.FrameService 83 TRACE: hexlify(single_frame.Payload): b'0000000000000000000000000000006f000000'
        # 2024-12-16 11:50:36,914 geberit-aquaclean.aquaclean-core.Frames.FrameService 91 TRACE: not is single_frame.IsSubFrameCount:
        # 2024-12-16 11:50:36,915 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 50 TRACE: in function FrameCollector.add_frame called by FrameService.process_data
        # 2024-12-16 11:50:36,915 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 51 TRACE: frame_index: 3, Payload: 0000000000000000000000000000006F000000
        # 2024-12-16 11:50:36,915 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 61 TRACE: Received frame 4 of 4: Payload=0000000000000000000000000000006F000000
        # 2024-12-16 11:50:36,915 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 46 TRACE: Controlling bitmap changed with frameNumber 3 => 00001111 Bitmap: 0F00000000000000
        # 2024-12-16 11:50:36,915 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 69 TRACE: len(self.frame_data): 4, len(self.frame_data): 4, self.expected_frames: 4
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 70 TRACE: Raising SendControlFrame with data 0F00000000000000
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:36,916 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:36,917 geberit-aquaclean.aquaclean-core.Frames.FrameService 58 TRACE: in function FrameService.on_send_control_frame called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,917 geberit-aquaclean.aquaclean-core.Frames.FrameService 59 DEBUG: Send control frame: b'\x0f\x00\x00\x00\x00\x00\x00\x00'
        # 2024-12-16 11:50:36,917 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 102 TRACE: BuildControlFrame: b'0f00000000000000'
        # 2024-12-16 11:50:36,917 geberit-aquaclean.aquaclean-core.Frames.FrameFactory 110 DEBUG: Bitmask: 0f00000000000000
        # 2024-12-16 11:50:36,917 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:36,917 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:36,918 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:36,918 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:36,918 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:36,918 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:36,918 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 109 TRACE: in function AquaCleanBaseClient.send_data_async called by EventHandler.invoke_async
        # 2024-12-16 11:50:36,918 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 110 TRACE: in send_data_async, sender: <geberit-aquaclean.aquaclean-core.Frames.FrameService.FrameService object at 0x7f86ca7670>, data: bytearray(b'p\x00\x08\x00\x0f\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        # 2024-12-16 11:50:36,919 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 116 TRACE: in function BluetoothLeConnector.send_message called by AquaCleanBaseClient.send_data_async
        # 2024-12-16 11:50:36,919 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 118 TRACE: Sending data to characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 data: 700008000F000000000000000000000000000000
        # 2024-12-16 11:50:36,993 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 277 TRACE: self.event_wait_queue.get()...
        # 2024-12-16 11:50:37,093 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 282 TRACE: event_wait_queue_get_result - timeout
        # 2024-12-16 11:50:37,093 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 283 TRACE: event_wait_queue_get_result: No value yet
        # 2024-12-16 11:50:37,095 asyncio  1987 WARNING: Executing <Task pending name='Task-2' coro=<shutdown_waits_for.<locals>.coro_proxy() running at /home/kali/homeautomation/geberit-py_bleak/aiorun.py:64> wait_for=<Future pending cb=[Task.task_wakeup()] created at /usr/lib/python3.12/asyncio/base_events.py:448> created at /home/kali/homeautomation/geberit-py_bleak/aiorun.py:75> took 0.102 seconds
        # 2024-12-16 11:50:37,096 bleak.backends.bluezdbus.client 885 DEBUG: Write Characteristic 3334429d-90f3-4c41-a02d-5cb3a13e0000 | /org/bluez/hci0/dev_38_AB_41_2A_0D_67/service0001/char0002: bytearray(b'p\x00\x08\x00\x0f\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')
        # 2024-12-16 11:50:37,096 geberit-aquaclean.aquaclean-uwp-bluetooth-le.LE.BluetoothLeConnector 122 TRACE: result: None
        # 2024-12-16 11:50:37,096 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 112 TRACE:  send_data_async after sleep
        # 2024-12-16 11:50:37,096 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 74 TRACE: len(self.frame_data): 4, self.expected_frames: 4
        # 2024-12-16 11:50:37,097 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 83 TRACE: receive complete
        # 2024-12-16 11:50:37,097 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 84 TRACE: receive complete: bytes(data)=0500004210690001010D3D070000000000010000000002000000000300000000040000000005000000000600000000000000000000000000000000000000000000000000000000006F000000
        # 2024-12-16 11:50:37,098 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 89 TRACE: in function FrameCollector.add_frame called by FrameService.process_data
        # 2024-12-16 11:50:37,098 geberit-aquaclean.aquaclean-core.Frames.FrameCollector 90 TRACE: len(self.TransactionCompleteFC.get_handlers(): 1 for on_transaction_complete
        # 2024-12-16 11:50:37,098 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 37 TRACE: in invoke_async
        # 2024-12-16 11:50:37,098 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 39 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:37,098 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 40 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:37,099 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 41 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:37,099 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 42 TRACE: inspect.iscoroutinefunction(handler): True
        # 2024-12-16 11:50:37,099 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 43 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:37,099 geberit-aquaclean.aquaclean-core.Frames.FrameService 51 TRACE: in function FrameService.on_transaction_complete called by EventHandler.invoke_async
        # 2024-12-16 11:50:37,099 geberit-aquaclean.aquaclean-core.Frames.FrameService 52 TRACE: len(self.TransactionCompleteFS.get_handlers(): 1 for on_transaction_complete
        # 2024-12-16 11:50:37,099 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 22 TRACE: inspect.isawaitable(handler): False
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 23 TRACE: inspect.iscoroutine(handler): False
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 24 TRACE: inspect.isfunction(handler): False
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 25 TRACE: inspect.iscoroutinefunction(handler): False
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 26 TRACE: inspect.ismethod(handler): True
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 116 TRACE: in function AquaCleanBaseClient.on_transaction_completeForBaseClient called by EventHandler.__call__
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 117 TRACE: on_transaction_completeForBaseClient, sender: <geberit-aquaclean.aquaclean-core.Frames.FrameCollector.FrameCollector object at 0x7f871dc0b0>, data: b'\x05\x00\x00B\x10i\x00\x01\x01\r=\x07\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x02\x00\x00\x00\x00\x03\x00\x00\x00\x00\x04\x00\x00\x00\x00\x05\x00\x00\x00\x00\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00o\x00\x00\x00'
        # 2024-12-16 11:50:37,100 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 118 TRACE: self.event_wait_handle.clear()...
        # 2024-12-16 11:50:37,101 geberit-aquaclean.aquaclean-core.Message.MessageService 17 TRACE: in function MessageService.parse_message1 called by AquaCleanBaseClient.on_transaction_completeForBaseClient
        # 2024-12-16 11:50:37,101 geberit-aquaclean.aquaclean-core.Message.Message 22 TRACE: create_from_stream
        # 2024-12-16 11:50:37,101 geberit-aquaclean.aquaclean-core.Message.MessageService 22 DEBUG: Parsing data to message with ID=5 Data=0500004210690001010d3d070000000000010000000002000000000300000000040000000005000000000600000000000000000000000000000000000000000000000000000000006f000000
        # 2024-12-16 11:50:37,101 geberit-aquaclean.aquaclean-core.Message.CrcMessage 79 TRACE: in function CrcMessage.crc16_calculation
        # 2024-12-16 11:50:37,103 geberit-aquaclean.aquaclean-core.Message.MessageService 44 DEBUG: Parsing message with Context=01, Procedure=0D, Data=07000000000001000000000200000000030000000004000000000500000000060000000000000000000000000000000000000000000000000000000000
        # 2024-12-16 11:50:37,103 geberit-aquaclean.aquaclean-core.Message.MessageService 46 TRACE: context 01
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Message.MessageService 47 TRACE: procedure 0d
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Message.MessageService 48 TRACE: data.hex() 07000000000001000000000200000000030000000004000000000500000000060000000000000000000000000000000000000000000000000000000000
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Message.MessageService 55 TRACE: msgCtx.context 01
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Message.MessageService 56 TRACE: msgCtx.procedure 0d
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Message.MessageService 57 TRACE: msgCtx.result_bytes 07000000000001000000000200000000030000000004000000000500000000060000000000000000000000000000000000000000000000000000000000
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 125 TRACE: self.context_lookup not found
        # 2024-12-16 11:50:37,104 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 127 TRACE: self.event_wait_queue.put("Transaction finished")
        # 2024-12-16 11:50:37,105 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 129 TRACE: ###################### Transaction: set finished...
        # 2024-12-16 11:50:37,105 geberit-aquaclean.aquaclean-core.Frames.FrameService 93 TRACE: nach self.frame_collector.add_frame
        # 2024-12-16 11:50:37,196 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 277 TRACE: self.event_wait_queue.get()...
        # 2024-12-16 11:50:37,196 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 279 TRACE: event_wait_queue_get_result: Transaction finished
        # 2024-12-16 11:50:37,196 geberit-aquaclean.aquaclean-core.Clients.AquaCleanBaseClient 286 TRACE: nach await self.frame_service.send_frame_async(frame)

        logger.trace(f"self.event_wait_queue.qsize(): {self.event_wait_queue.qsize()}")
        event_wait_queue_get_result = "No value yet"
        # Normal request/response cycle is ~600 ms; 5 s is a generous safety margin.
        timeout_seconds = 5.0
        start_time = time.time()
        while True:
            try:
                logger.trace(f"self.event_wait_queue.get()...")
                event_wait_queue_get_result = self.event_wait_queue.get(timeout=0.1)
                logger.trace(f"event_wait_queue_get_result: {event_wait_queue_get_result}")
                break
            except Empty:
                logger.trace(f"event_wait_queue_get_result - timeout")
                logger.trace(f"event_wait_queue_get_result: {event_wait_queue_get_result}")
                if time.time() - start_time > timeout_seconds:
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
            await asyncio.sleep(0.1)

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


