

from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos                  import DeviceIdentification   
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute   
from aquaclean_console_app.aquaclean_core.Common.Deserializer                   import Deserializer   

import logging
logger = logging.getLogger(__name__)

class GetDeviceInitialOperationDate:
        
    # /geberit-aquaclean/aquaclean-core/Api/CallClasses/GetDeviceInitialOperationDate.cs
    #     [ApiCall(Context = 0x00, Procedure = 0x86, Node = 0x01)]
        
    api_call_attribute = ApiCallAttribute(0x00, 0x86, 0x01)

    def __init__(self):
        logger.trace("GetDeviceInitialOperationDate: __init__")
        self.api_call_attribute = ApiCallAttribute(0x00, 0x86, 0x01)

    def get_api_call_attribute(self) -> ApiCallAttribute: # type: ignore
        logger.trace("GetDeviceInitialOperationDate: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        return bytearray()

    def result(self, data):
        return data.decode('utf-8').replace('\0', '').strip()
    
