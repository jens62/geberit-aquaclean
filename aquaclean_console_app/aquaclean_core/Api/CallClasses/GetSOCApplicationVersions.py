# from geberit_aquaclean_core.common import Deserializer


import logging
logger = logging.getLogger(__name__)

from aquaclean_core.Api.CallClasses.Dtos                  import SOCApplicationVersion   
from aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute   
from aquaclean_core.Common.Deserializer                   import Deserializer   
from aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute   


class GetSOCApplicationVersions:
    def __init__(self):
        logger.trace("GetSOCApplicationVersions: __init__")
        self.api_call_attribute = ApiCallAttribute(0x01, 0x81, 0x01) # TODO: Determine the correct node value

    def get_api_call_attribute(self) -> ApiCallAttribute: # type: ignore
        logger.trace("GetSOCApplicationVersions: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("GetDeviceIdentification: get_payload")
        return bytearray()

    def result(self, data):
        logger.trace("GetDeviceIdentification: result")
        logger.info("Not yet fully implemented.")
        readable_data = ''.join(f'{b:02X}' for b in data)
        logger.trace(f"data: {readable_data}")
        # ds = Deserializer.Deserializer()
        # di = ds.deserialize(SOCApplicationVersion.SOCApplicationVersion, data)
        # return di
        return readable_data

    
