
import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos                  import DeviceIdentification   
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute   
from aquaclean_console_app.aquaclean_core.Common.Deserializer                   import Deserializer   

class GetDeviceIdentification:

    api_call_attribute = ApiCallAttribute(0x00, 0x82, 0x01)

    def __init__(self):
        logger.trace("GetDeviceIdentification: __init__")
        self.api_call_attribute = ApiCallAttribute(0x00, 0x82, 0x01)

    def get_api_call_attribute(self) -> ApiCallAttribute: # type: ignore
        logger.trace("GetDeviceIdentification: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("GetDeviceIdentification: get_payload")
        return bytearray()

    def result(self, data):
        logger.trace("GetDeviceIdentification: result")
        ds = Deserializer()
        di = ds.deserialize(DeviceIdentification.DeviceIdentification, data)
        return di

