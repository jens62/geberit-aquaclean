import logging
import struct

logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute import ApiCallAttribute


# All "get list" procedures use a fixed 13-byte payload: 1 count byte + up to 12
# ID bytes, zero-padded to 13.  The device rejects shorter payloads with 0xF7.
# WARNING: the iPhone sends 12 IDs [0,1,2,3,4,5,6,7,4(dup),8,9,10] but the device
# times out on that payload (confirmed: aquaclean-f2a96f1-TRACE-no-filter-data.log).
# Use 8 IDs only — confirmed working in aquaclean-latest-TRACE-filter-works.log.
_FILTER_PAYLOAD = bytes([
    0x08,                                                        # count = 8
    0x00, 0x01, 0x02, 0x03, 0x07, 0x08, 0x09, 0x0a,           # IDs 0,1,2,3,7,8,9,10
    0x00, 0x00, 0x00, 0x00,                                      # zero-pad to 13 bytes
])


class GetFilterStatus:
    """
    Procedure 0x59 (context 0x01) — filter maintenance status.

    Response: N records of 5 bytes each: [ID (1 byte)][value uint32 LE (4 bytes)]

    Key record IDs
    --------------
    7  days_until_filter_change   uint32   0 = exchange now; 365 = just reset
    8  last_filter_reset          uint32   Unix timestamp (seconds)
    9  next_filter_change         uint32   Unix timestamp; 0 if not scheduled
    10 filter_reset_count         uint32   Total number of filter resets

    The iOS app triggers a reset via SetCommand(ResetFilterCounter = 0x2F),
    which atomically: sets ID 7 → 365, updates ID 8 → now, clears ID 9 → 0,
    increments ID 10 by 1.
    """

    # Context=0x01, Procedure=0x59, Node=0x01
    api_call_attribute = ApiCallAttribute(0x01, 0x59, 0x01)

    def __init__(self):
        logger.trace("GetFilterStatus: __init__")

    def get_api_call_attribute(self) -> ApiCallAttribute:
        logger.trace("GetFilterStatus: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self) -> bytes:
        logger.trace("GetFilterStatus: get_payload")
        return bytearray(_FILTER_PAYLOAD)

    def result(self, data: bytes) -> dict:
        """
        Parse GetFilterStatus response.

        Returns a dict with all record values plus convenience fields:
            days_until_filter_change  int   (record ID 7)
            last_filter_reset         int   Unix timestamp (record ID 8)
            next_filter_change        int   Unix timestamp, 0 = none (record ID 9)
            filter_reset_count        int   (record ID 10)
            raw_records               dict  {id: value} for all returned records
        """
        logger.trace("GetFilterStatus: result, len=%d", len(data) if data else 0)
        records = {}
        if data and len(data) >= 1:
            count = data[0]
            pos = 1
            while pos + 4 < len(data) and len(records) < count:
                rec_id = data[pos]
                value = struct.unpack_from('<I', data, pos + 1)[0]
                records[rec_id] = value
                pos += 5

        result = {
            "days_until_filter_change": records.get(7, None),
            "last_filter_reset":        records.get(8, None),
            "next_filter_change":       records.get(9, None),
            "filter_reset_count":       records.get(10, None),
            "raw_records":              records,
        }
        logger.debug("GetFilterStatus: days_remaining=%s resets=%s",
                     result["days_until_filter_change"], result["filter_reset_count"])
        return result
