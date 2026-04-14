import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class GetStoredCommonSetting:

    # [ApiCall(Context = 0x01, Procedure = 0x51, Node = 0x01)]
    # Reads a single stored common (device-wide) setting by ID.
    # Payload:  [setting_id]  (1 byte)
    # Response: [value_lo, value_hi]  (2-byte little-endian int)
    #
    # Known IDs (confirmed from BLE log of iPhone orientation-light session):
    #   0: Odour extraction run-on time  (bool 0/1)
    #   1: Orientation light brightness  (0-4)
    #   2: Orientation light activation  (0=On, 1=Off, 2=when approached)
    #   3: Orientation light color       (0-6; confirmed: 1=Blue, 2=Magenta)

    api_call_attribute = ApiCallAttribute(0x01, 0x51, 0x01)

    def __init__(self, setting_id: int):
        logger.trace("GetStoredCommonSetting: __init__")
        self.setting_id = setting_id

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("GetStoredCommonSetting: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        logger.trace("GetStoredCommonSetting: get_payload")
        return bytes([self.setting_id])

    def result(self, data: bytearray) -> int:
        logger.trace("GetStoredCommonSetting: result")
        return int.from_bytes(bytes(data[:2]), 'little')
