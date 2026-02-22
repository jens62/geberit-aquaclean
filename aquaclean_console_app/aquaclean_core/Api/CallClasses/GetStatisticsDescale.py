
import logging
logger = logging.getLogger(__name__)

from aquaclean_console_app.aquaclean_core.Api.Attributes.ApiCallAttribute       import ApiCallAttribute
from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos.StatisticsDescale import StatisticsDescale


class GetStatisticsDescale:

    # geberit-aquaclean/aquaclean-core/Api/CallClasses/tmp.txt
    # [Api(Context = 1, Procedure = 0x45)]
    # StatisticsDescale GetStatisticsDescale();

    api_call_attribute = ApiCallAttribute(0x01, 0x45, 0x01)

    def __init__(self):
        logger.trace("GetStatisticsDescale: __init__")

    def get_api_call_attribute(self) -> ApiCallAttribute:  # type: ignore
        logger.trace("GetStatisticsDescale: get_api_call_attribute")
        return self.api_call_attribute

    def get_payload(self):
        logger.trace("GetStatisticsDescale: get_payload")
        return bytearray()

    def result(self, data: bytearray) -> StatisticsDescale:
        logger.trace("GetStatisticsDescale: result, len=%d", len(data))
        sd = StatisticsDescale()
        pos = 0
        sd.unposted_shower_cycles          = int.from_bytes(data[pos:pos+1], 'little'); pos += 1
        sd.days_until_next_descale         = int.from_bytes(data[pos:pos+2], 'little'); pos += 2
        sd.days_until_shower_restricted    = int.from_bytes(data[pos:pos+2], 'little'); pos += 2
        sd.shower_cycles_until_confirmation = int.from_bytes(data[pos:pos+1], 'little'); pos += 1
        sd.date_time_at_last_descale       = int.from_bytes(data[pos:pos+4], 'little'); pos += 4
        sd.date_time_at_last_descale_prompt = int.from_bytes(data[pos:pos+4], 'little'); pos += 4
        sd.number_of_descale_cycles        = int.from_bytes(data[pos:pos+2], 'little'); pos += 2
        logger.debug("GetStatisticsDescale: result: %s", sd)
        return sd
