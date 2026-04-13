import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


class SubscribeNotifications:
    """Proc(0x01, 0x11) + Proc(0x01, 0x13) — register notification subscriptions.

    The iPhone sends 4×Proc_0x11 followed by 4×Proc_0x13 on every BLE connect,
    before the first GetSystemParameterList call.

    Without this sequence, the device ignores GetSystemParameterList when a
    previous BLE session (e.g. iPhone Geberit Home App) ended without properly
    unsubscribing. The device holds the old session's subscription open and
    does not deliver responses to the new client.

    Confirmed by probe testing (2026-04-13):
    - All 4 × Proc_0x13 calls are required (sending only the last is insufficient)
    - Proc_0x11 × 4 precedes Proc_0x13 × 4 in the full iPhone init sequence
    - Sending only Proc_0x13 works for some stuck states but not all
    """

    # Proc_0x11 payloads — sent before Proc_0x13.  Note the last entry differs:
    # count=1, only ID 0x0f (vs Proc_0x13's count=2, IDs 0x0f + 0x0d).
    PRE_PAYLOADS = [
        bytes([0x04, 0x01, 0x03, 0x04, 0x05]),
        bytes([0x04, 0x06, 0x07, 0x08, 0x09]),
        bytes([0x04, 0x0a, 0x0b, 0x0c, 0x0e]),
        bytes([0x01, 0x0f, 0x00, 0x00, 0x00]),  # only 0x0f, no 0x0d here
    ]

    # Proc_0x13 payloads — sent after Proc_0x11.  Exact iPhone wire bytes.
    # Format: [count, id0, id1, ...] — device echoes back [count, id0, 0...]
    # IDs 1–15 are the valid compact subscription space; do NOT add proc codes
    # like 0x59 here — out-of-range IDs corrupt the subscription table and cause
    # the corresponding proc to stop responding (confirmed v2.4.68-pre regression).
    PAYLOADS = [
        bytes([0x04, 0x01, 0x03, 0x04, 0x05]),
        bytes([0x04, 0x06, 0x07, 0x08, 0x09]),
        bytes([0x04, 0x0a, 0x0b, 0x0c, 0x0e]),
        bytes([0x02, 0x0f, 0x0d, 0x00, 0x00]),  # includes 0x0d = GetSystemParameterList
    ]

    def __init__(self, payload: bytes, proc: int = 0x13):
        self.api_call_attribute = ApiCallAttribute(0x01, proc, 0x01)
        self._payload = payload

    def get_api_call_attribute(self) -> ApiCallAttribute:
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        return self._payload

    def result(self, data: bytearray):
        return bytes(data)
