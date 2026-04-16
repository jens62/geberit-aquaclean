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
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetFilterStatus                import GetFilterStatus
from aquaclean_console_app.aquaclean_core.Api.CallClasses.SubscribeNotifications         import SubscribeNotifications
from aquaclean_console_app.aquaclean_core.Api.CallClasses.SetStoredProfileSetting        import SetStoredProfileSetting
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetStoredCommonSetting         import GetStoredCommonSetting
from aquaclean_console_app.aquaclean_core.Api.CallClasses.GetNodeList                    import GetNodeList
from aquaclean_console_app.aquaclean_core.Api.CallClasses.SetStoredCommonSetting         import SetStoredCommonSetting

from aquaclean_console_app.aquaclean_utils                                               import utils


class _GetStoredProfileCall53:
    """Read a stored profile setting using proc 0x53 (C# GetStoredProfileSetting).

    Payload: [profile_id=0, setting_id] (2 bytes).
    Returns: 2-byte little-endian integer — the actual user preference stored
    on the device.  This is the value the Geberit iPhone app reads and writes.
    Confirmed from BLE sniff: proc 0x54 SetStoredProfileSetting (C# wire) writes
    here; the values match the in-app sliders.  Proc 0x0A reads a different area.
    """
    _attr = ApiCallAttribute(0x01, 0x53, 0x01)

    def __init__(self, setting_id: int, profile_id: int = 0):
        self._payload = bytes([profile_id, setting_id])

    def get_api_call_attribute(self) -> ApiCallAttribute:
        return self._attr

    def get_payload(self) -> bytes:
        return self._payload

    def result(self, data: bytearray):
        return bytes(data)


# iPhone order from BLE log: AnalShowerPressure=2, OscillatorState=1, LadyShowerPressure=3,
# AnalShowerPosition=4, WaterTemperature=6, WcSeatHeat=7, LadyShowerPosition=5,
# DryerTemperature=8, OdourExtraction=0, DryerState=9, DryerSprayIntensity=13
_IPHONE_PROFILE_SETTING_IDS = [2, 1, 3, 4, 6, 7, 5, 8, 0, 9, 13]

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
        self.profile_settings: dict = {}  # populated by subscribe_notifications_async()

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


    async def subscribe_notifications_async(self):
        """Send the BLE device init sequence before GetSystemParameterList.

        Sequence (confirmed working — matches v2.4.63 baseline):
          4 × Proc(0x01,0x11)  — pre-subscription
          4 × Proc(0x01,0x13)  — subscription registration (IDs 1–15, compact range only)

        Required on every BLE connect. Without this, the device may ignore
        GetSystemParameterList if a previous iPhone or bridge session left it
        in a stuck/subscription-only state.

        NOTE: Do NOT add proc codes like 0x59 to Proc_0x13 PAYLOADS.  The
        subscription ID space is compact (1–15); out-of-range IDs corrupt the
        device's subscription table and block the affected proc from responding.

        NOTE: Do NOT add Proc_0x0A or Proc_0x53 calls here.  Adding 10×Proc_0x0A
        pushes GetFilterStatus from position #13 to #23, causing it to time out.
        Adding 10×Proc_0x53 after that brings the total to 28 rapid BLE calls,
        causing GetSystemParameterList itself to time out.  Profile settings are
        fetched via get_stored_profile_settings_async() in _fetch_state instead.

        NOTE: The iPhone also sends 3×Proc(0x0B) writes immediately after 0x13
        (AnalShowerPressure=2, OscillatorState=2, LadyShowerPressure=2 — always
        value=2 regardless of user settings). These were tested as a session-claim
        hypothesis (commit 0bce5a2, 2026-04-16): DISPROVEN. E0003 persisted in both
        bleak and ESP32 proxy test runs with 0x0B writes present. Not implemented.
        See docs/developer/unknown-procedures.md § "Proc 0x0B session-claim hypothesis".
        """
        logger.debug("iPhone init sequence: 4×Proc_0x11 + 4×Proc_0x13")
        for payload in SubscribeNotifications.PRE_PAYLOADS:
            api_call = SubscribeNotifications(payload, proc=0x11)
            await self.send_request(api_call)
        for payload in SubscribeNotifications.PAYLOADS:
            api_call = SubscribeNotifications(payload, proc=0x13)
            await self.send_request(api_call)

    async def get_stored_profile_settings_async(self) -> dict:
        """Read all user profile settings via proc 0x53 (C# GetStoredProfileSetting).

        Returns a dict mapping setting_id → value (little-endian 2-byte int).
        These match the Geberit iPhone app slider values — confirmed by BLE sniff
        of SetStoredProfileSetting_C# (proc 0x54) writing to the same storage area.
        Proc 0x0A (init sequence) reads a *different* storage area and returns
        incorrect values; always use proc 0x53 for the actual user preferences.

        Called from _fetch_state (every poll) so profile settings stay current.
        Not called in subscribe_notifications_async — adding 10 extra calls there
        caused GetSystemParameterList to time out after the 18-call init sequence.
        """
        ps = {}
        for sid in _IPHONE_PROFILE_SETTING_IDS:
            await self.send_request(_GetStoredProfileCall53(sid))
            ps[sid] = int.from_bytes(bytes(self.message_context.result_bytes[:2]), 'little')
        self.profile_settings = ps
        return ps


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
        logger.info(f"SetCommandAsync: {command.name} (code={command.value})")
        await self.send_request(SetCommand(command))
        # No sleep here: send_request() already waits for the ACK from the device,
        # confirming the command was received and processed.  Sleeping after the ACK
        # gives the Geberit time to drop the BLE link, which causes bleak's disconnect()
        # to take the is_connected=False early-return path and NOT close its D-Bus
        # MessageBus — leaving BlueZ with a stale notification subscription that
        # triggers "Notify acquired" on the next session's start_notify call (E0003).


    # not yet implemented
    #
    # async def get_stored_profile_setting_async(self, profile_setting):
    #     api_call = GetStoredProfileSetting(0, profile_setting)
    #     return (await self.send_request(api_call)).result(self.message_context.ResultBytes)

    async def set_stored_profile_setting_async(self, profile_setting, setting_value: int):
        """Write a single user profile setting via proc 0x54.

        profile_setting: ProfileSettings enum member
        setting_value:   integer value (LE 2-byte, as written by the iPhone app)
        """
        api_call = SetStoredProfileSetting(profile_setting, setting_value)
        await self.send_request(api_call)

    async def get_stored_common_settings_async(self) -> dict:
        """Read common (device-wide) settings via proc 0x51.

        Returns a dict mapping setting_id → value.
        IDs confirmed from BLE log analysis:
          0: Odour extraction run-on time (bool)
          1: Orientation light brightness (0-4)
          2: Orientation light COLOR      (0=Blue,1=Turquoise,2=Magenta,3=Orange,4=Yellow,5=WarmWhite,6=ColdWhite)
          3: Orientation light ACTIVATION (0=Off, 1=On, 2=WhenApproached)
          4: WC Lid sensor sensitivity    (0-4)
          6: WC Lid open automatically   (0=off, 1=on)
          7: WC Lid close automatically  (0=off, 1=on)
        """
        cs = {}
        for sid in [2, 1, 3, 0, 4, 6, 7]:  # iPhone read order from BLE log (extended)
            api_call = GetStoredCommonSetting(sid)
            await self.send_request(api_call)
            cs[sid] = api_call.result(self.message_context.result_bytes)
        return cs

    async def set_stored_common_setting_async(self, setting_id: int, value: int):
        """Write a single common (device-wide) setting via proc 0x52."""
        api_call = SetStoredCommonSetting(setting_id, value)
        await self.send_request(api_call)

    async def get_statistics_descale_async(self):
        api_call = GetStatisticsDescale()
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"self.message_context.result_bytes: {self.message_context.result_bytes}")

        result = api_call.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")

        return result

    async def get_node_list_async(self) -> dict:
        api_call = GetNodeList()
        await self.send_request(api_call)
        return api_call.result(self.message_context.result_bytes)

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

    async def get_filter_status_async(self):
        api_call = GetFilterStatus()
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call)
        logger.debug(f"response: {response}")
        logger.debug(f"self.message_context.result_bytes: {self.message_context.result_bytes}")

        result = api_call.result(self.message_context.result_bytes)
        logger.debug(f"result: {result}")

        return result

    async def get_firmware_version_list_async(self, payload: bytes = None):
        api_call = GetFirmwareVersionList() if not payload else GetFirmwareVersionList(payload)
        logger.trace(f"api_call: {api_call}")

        response = await self.send_request(api_call, send_as_first_cons=True)
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
    

    async def send_request(self, api_call, send_as_first_cons=False):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.debug(f"Sending {api_call.__class__.__name__}{'as FIRST+CONS' if send_as_first_cons else ''}")

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
        if send_as_first_cons:
            # Signal to the device that a CONS frame follows (SubFrameCountOrIndex=1,
            # IsSubFrameCount=True) so it returns a multi-frame response instead of
            # a 5-byte error body.  Type byte: SINGLE|HasMsgType|SubCount=1|IsCount → 0x13.
            frame.SubFrameCountOrIndex = 1
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

        if send_as_first_cons:
            # CONS frame on WRITE_1: type byte 0x12 (SINGLE|HasMsgType|SubIndex=1),
            # followed by 19 zero bytes.  The full CrcMessage fits in the FIRST frame
            # so the CONS frame carries no payload — the device requires it for the
            # handshake regardless.
            cons_frame = bytes([0x12]) + bytes(19)
            logger.debug(f"Sending CONS frame: {cons_frame.hex()}")
            await self.bluetooth_le_connector.send_message_cons(cons_frame)

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
            _name = self.bluetooth_le_connector.device_name
            _addr = self.bluetooth_le_connector.device_address
            _label = (
                f"'{_name}' ({_addr})"
                if _name and _name not in ("Unknown", "None")
                else _addr
            )
            error_msg = (
                f"No response from BLE peripheral {_label}. "
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


