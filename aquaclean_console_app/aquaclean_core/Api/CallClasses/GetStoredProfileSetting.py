
import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute  import ApiCallAttribute
from aquaclean_console_app.aquaclean_core.Clients.ProfileSettings          import ProfileSettings
from aquaclean_console_app.aquaclean_core.Common.Deserializer              import Deserializer


class GetStoredProfileSetting:

    # geberit-aquaclean/aquaclean-core/Api/CallClasses/GetStoredProfileSetting.cs
    # [ApiCall(Context = 0x01, Procedure = 0x53, Node = 0x01)]

    api_call_attribute = ApiCallAttribute(0x01, 0x53, 0x01)

    def __init__(self, profile_id: int, profile_setting: ProfileSettings):
        logger.trace("GetStoredProfileSetting: __init__")
        self.profile_id = profile_id
        self.profile_setting = profile_setting

    def get_api_call_attribute(self) -> ApiCallAttribute:  # type: ignore
        logger.trace("GetStoredProfileSetting: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("GetStoredProfileSetting: get_payload")
        return bytes([self.profile_id, self.profile_setting.value])

    def result(self, data: bytearray) -> int:
        logger.trace("GetStoredProfileSetting: result")
        return Deserializer.deserialize_to_int(data, 0, 2)
