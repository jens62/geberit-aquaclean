import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class SubscribeNotifications:
    """Proc(0x01, 0x13) — register notification subscriptions with the device.

    Must be sent (all 4 payloads, in order) after every BLE connect, before
    the first GetSystemParameterList call.

    Without this, the device ignores GetSystemParameterList when a previous
    BLE session (e.g. iPhone Geberit Home App) ended without properly
    unsubscribing. The device holds the old session's subscription open and
    does not deliver responses to the new client.

    The 4 payloads cover all known notification IDs, including 0x0D
    (GetSystemParameterList) in the final call.

    Confirmed by probe testing (2026-04-13): all 4 calls are required;
    sending only the last one (02,0f,0d,00,00) is not sufficient.
    """

    # Full subscription payload set observed from iPhone (exact wire bytes).
    # Format: [count, id0, id1, ...] — device echoes back [count, id0, 0...]
    PAYLOADS = [
        bytes([0x04, 0x01, 0x03, 0x04, 0x05]),
        bytes([0x04, 0x06, 0x07, 0x08, 0x09]),
        bytes([0x04, 0x0a, 0x0b, 0x0c, 0x0e]),
        bytes([0x02, 0x0f, 0x0d, 0x00, 0x00]),  # includes 0x0d = GetSystemParameterList
    ]

    def __init__(self, payload: bytes):
        self.api_call_attribute = ApiCallAttribute(0x01, 0x13, 0x01)
        self._payload = payload

    def get_api_call_attribute(self) -> ApiCallAttribute:
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        return self._payload

    def result(self, data: bytearray):
        return bytes(data)
