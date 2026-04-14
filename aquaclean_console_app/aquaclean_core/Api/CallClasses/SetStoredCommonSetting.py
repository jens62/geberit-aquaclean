import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class SetStoredCommonSetting:

    # [ApiCall(Context = 0x01, Procedure = 0x52, Node = 0x01)]
    # Writes a single stored common (device-wide) setting by ID.
    # Payload:  [setting_id, value_lo, value_hi]  (3 bytes)
    # Response: OK (no data)
    #
    # Known IDs (confirmed from BLE log of iPhone orientation-light session):
    #   0: Odour extraction run-on time  (bool 0/1)
    #   1: Orientation light brightness  (0-4)
    #   2: Orientation light activation  (0=On, 1=Off, 2=when approached)
    #   3: Orientation light color       (0-6; confirmed: 1=Blue, 2=Magenta)

    api_call_attribute = ApiCallAttribute(0x01, 0x52, 0x01)

    def __init__(self, setting_id: int, value: int):
        logger.trace("SetStoredCommonSetting: __init__")
        self.setting_id = setting_id
        self.value = value

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("SetStoredCommonSetting: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        logger.trace("SetStoredCommonSetting: get_payload")
        return bytes([self.setting_id, self.value & 0xFF, (self.value >> 8) & 0xFF])
