from typing import List
import struct
from binascii import hexlify

from aquaclean_core.Api.CallClasses.Dtos import SystemParameterList   
from aquaclean_core.Api.Attributes       import ApiCallAttribute   
from aquaclean_core.Common.Deserializer  import Deserializer   

import logging

logger = logging.getLogger(__name__)


class GetSystemParameterList:

    """
    0 userIsSitting, 
    1 analShowerIsRunning,
    2 ladyShowerIsRunning,
    3 dryerIsRunning,
    4 descalingState, 
    5 descalingDurationInMinutes,
    6 lastErrorCode,
    9 orientationLightState
    """

            
    # geberit-aquaclean/aquaclean-core/Api/CallClasses/GetSystemParameterList.cs
    # [ApiCall(Context = 0x01, Procedure = 0x0D, Node = 0x01)]

    api_call_attribute = ApiCallAttribute.ApiCallAttribute(0x01, 0x0D, 0x01)

    def __init__(self, parameter_list: List[int]):
        self.parameter_list = parameter_list

    def get_api_call_attribute(self) -> ApiCallAttribute.ApiCallAttribute: # type: ignore
        logger.trace("GetSystemParameterList: get_api_call_attribute")
        return self.api_call_attribute
    
    def get_payload(self) -> bytearray:
        arg_count = min(len(self.parameter_list), 12)
        data = bytearray(13)
        data[0] = arg_count

        for i in range(arg_count):
            data[i + 1] = self.parameter_list[i]
        return data

    def result(self, data: bytearray):
        logger.trace("in method result: %s", hexlify(data))

        ds = Deserializer()

        ds_result = ds.deserialize( SystemParameterList.SystemParameterList, data)

        logger.trace("ds_result.a: %s", ds_result.a)
        logger.trace("ds_resul.data_array: %s", ds_result.data_array)

        ds_int = ds.deserialize_to_int(data, 0, 4)
        logger.trace("ds_int: %d", ds_int)

        return ds_result
