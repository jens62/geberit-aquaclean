from enum import Enum

from aquaclean_core.Api.Attributes.ApiCallAttribute      import ApiCallAttribute                                   
from aquaclean_core.Clients.Commands                     import Commands
from aquaclean_utils                                     import utils   


import logging

logger = logging.getLogger(__name__)

class SetCommand:

    api_call_attribute = ApiCallAttribute(0x01, 0x09, 0x00)

    def __init__(self, command: Commands):  # type: ignore
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"command: {command}")
        self.command = command

    def get_api_call_attribute(self) -> ApiCallAttribute: # type: ignore
        logger.info("SetCommand: get_api_call_attribute")
        return self.api_call_attribute
    
    def get_payload(self):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        payload = bytes([self.command.value])
        logger.trace(f"utils.bytes_to_hex_string(payload): {utils.bytes_to_hex_string(payload)}")
        return payload


