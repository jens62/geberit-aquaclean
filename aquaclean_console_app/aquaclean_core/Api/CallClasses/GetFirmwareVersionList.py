import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


# All "get list" procedures (GetSystemParameterList, GetFirmwareVersionList,
# GetFilterStatus) use a fixed 13-byte payload: 1 count byte + up to 12 ID bytes,
# zero-padded to 13.  The device rejects shorter payloads with error 0xF7.
# We request 8 of the 12 component IDs the iOS app requests; component 1 is the
# main firmware version used for the "RS28.0 TS199" display string.
_FIRMWARE_PAYLOAD = bytes([
    0x08,                                           # count = 8
    0x01, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09,  # component IDs 1, 3–9
    0x00, 0x00, 0x00, 0x00,                         # zero-pad to 13 bytes total
])


class GetFirmwareVersionList:

    # Context=0x01, Procedure=0x0E, Node=0x01
    api_call_attribute = ApiCallAttribute(0x01, 0x0E, 0x01)

    def __init__(self, payload: bytes = _FIRMWARE_PAYLOAD):
        logger.trace("GetFirmwareVersionList: __init__")
        self._payload = bytearray(payload)

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("GetFirmwareVersionList: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace(f"GetFirmwareVersionList: get_payload -> {self._payload.hex() or '(empty)'}")
        return self._payload

    def result(self, data: bytes) -> dict:
        """
        Parse the GetFirmwareVersionList response.

        Response layout (5-byte records):
            byte 0        : count (number of records)
            per record:
              [comp_id][v1][v2][build][reserved]

        Returns:
            {
              "components": {
                  comp_id (int): {"version": str, "build": int},
                  ...
              },
              "main": str | None   # iOS-format main firmware: "RS28.0 TS199"
            }
        """
        logger.trace("GetFirmwareVersionList: result")
        components = {}
        if data and len(data) >= 1:
            count = data[0]
            pos = 1
            while pos + 4 <= len(data) and len(components) < count:
                comp_id = data[pos]
                v1 = data[pos + 1]
                v2 = data[pos + 2]
                build = data[pos + 3]
                v1c = chr(v1) if 0x20 <= v1 <= 0x7E else f"{v1:02X}"
                v2c = chr(v2) if 0x20 <= v2 <= 0x7E else f"{v2:02X}"
                components[comp_id] = {"version": v1c + v2c, "build": build}
                pos += 5

        # iOS display format for main firmware (component ID 1): "RS{version}.0 TS{build}"
        # Example: component 1 = version "28", build 199 → "RS28.0 TS199"
        main = None
        if 1 in components:
            c = components[1]
            main = f"RS{c['version']}.0 TS{c['build']}"

        logger.debug(f"GetFirmwareVersionList: {len(components)} components, main={main}")
        return {"components": components, "main": main}
