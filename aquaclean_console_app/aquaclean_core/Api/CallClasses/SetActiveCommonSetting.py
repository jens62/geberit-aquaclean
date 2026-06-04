import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class SetActiveCommonSetting:

    # [ApiCall(Context = 0x01, Procedure = 0x0B, Node = 0x01)]
    # Writes a single ACTIVE (live) common setting by ID.
    # Applies immediately — no power cycle required.
    # Payload:  [setting_id, value_lo, value_hi]  (3 bytes, same format as 0x52)
    # Response: OK (no data)
    #
    # Confirmed live on HB2304EU298413 (2026-06-04):
    #   3: Orientation light mode  (0=Off, 1=On, 2=WhenApproached)
    # Same ID space as proc 0x52 (SetStoredCommonSetting).

    api_call_attribute = ApiCallAttribute(0x01, 0x0B, 0x01)

    def __init__(self, setting_id: int, value: int):
        logger.trace("SetActiveCommonSetting: __init__")
        self.setting_id = setting_id
        self.value = value

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("SetActiveCommonSetting: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        logger.trace("SetActiveCommonSetting: get_payload")
        return bytes([self.setting_id, self.value & 0xFF, (self.value >> 8) & 0xFF])
