
import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute  import ApiCallAttribute
from aquaclean_console_app.aquaclean_core.Clients.ProfileSettings          import ProfileSettings


class SetStoredProfileSetting:

    # geberit-aquaclean/aquaclean-core/Api/CallClasses/SetStoredProfileSetting.cs
    # [ApiCall(Context = 0x01, Procedure = 0x54, Node = 0x01)]

    api_call_attribute = ApiCallAttribute(0x01, 0x54, 0x01)

    def __init__(self, profile_setting: ProfileSettings, value: int):
        logger.trace("SetStoredProfileSetting: __init__")
        self.profile_setting = profile_setting
        self.value = value

    def get_api_call_attribute(self) -> ApiCallAttribute:  # type: ignore
        logger.trace("SetStoredProfileSetting: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("SetStoredProfileSetting: get_payload")
        profile_id = 0
        data = bytearray(4)
        data[0] = profile_id
        data[1] = self.profile_setting.value
        data[2] = self.value >> 0 & 0xFF
        data[3] = self.value >> 8 & 0xFF
        return bytes(data)
