# from geberit_aquaclean_core.common import Deserializer


import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos                  import SOCApplicationVersion   
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute   
from aquaclean_console_app.aquaclean_core.Common.Deserializer                   import Deserializer   
from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute   


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
        logger.trace("GetSOCApplicationVersions: result")
        # Response layout (4 bytes): [RsHi][RsLo][TsLo][TsHi]
        # RsHi/RsLo are ASCII chars forming the RS version string (e.g. 0x31,0x30 → "10").
        # TsLo+TsHi form the TS build number as uint16 LE (e.g. 0x12,0x00 → 18).
        # Confirmed from InfoFrame fields in thomas-bingel C# log and live probe:
        #   GetSOCApplicationVersions → 31 30 12 00 → "RS10.0 TS18"
        if data and len(data) >= 3:
            rs = ''.join(chr(b) if 0x20 <= b <= 0x7E else f"{b:02X}" for b in data[0:2])
            ts = data[2] + (data[3] << 8 if len(data) >= 4 else 0)
            version = f"RS{rs}.0 TS{ts}"
            logger.debug(f"GetSOCApplicationVersions: {version}")
            return version
        return data.hex() if data else ""

    
