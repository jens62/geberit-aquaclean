import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class GetFirmwareVersionList:
    def __init__(self):
        logger.trace("GetFirmwareVersionList: __init__")
        # Context=0x01, Procedure=0x0E, Node=0x01
        # C# signature: GetFirmwareVersionList(object arg1, object arg2) — arg types unknown;
        # starting with empty payload to probe the device response.
        self.api_call_attribute = ApiCallAttribute(0x01, 0x0E, 0x01)

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("GetFirmwareVersionList: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("GetFirmwareVersionList: get_payload")
        # arg1/arg2 types unknown — empty payload as initial probe
        return bytearray()

    def result(self, data):
        logger.trace("GetFirmwareVersionList: result")
        # Return both raw hex and attempted ASCII decode for analysis
        raw_hex = ''.join(f'{b:02X}' for b in data)
        try:
            ascii_str = data.decode('ascii', errors='replace')
        except Exception:
            ascii_str = ''
        logger.debug(f"GetFirmwareVersionList raw: {raw_hex}")
        logger.debug(f"GetFirmwareVersionList ascii: {ascii_str!r}")
        return {"raw_hex": raw_hex, "ascii": ascii_str}
