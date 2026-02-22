
import struct
import sys
from binascii import hexlify

from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos import SystemParameterList   
from aquaclean_console_app.aquaclean_core.Api.CallClasses.Dtos import DeviceIdentification   
from aquaclean_console_app.aquaclean_utils                     import utils   

import logging
logger = logging.getLogger(__name__)


class Deserializer():

    @staticmethod
    def deserialize_to_int(data, position, length):
        if struct.unpack('<I', b'\x01\x00\x00\x00')[0] == 1:  # Check for little-endian
            data[position:position + length] = reversed(data[position:position + length])

        var0 = 0
        for i in range(position, position + length):
            var0 = (var0 << 8) | data[i]

        return var0
    
    
    @staticmethod
    def deserialize(cls, data):
        logger.trace(f"in function deserialize")
        logger.trace(f"cls: {cls}, hexlify(data): {hexlify(data)}, len(data): {len(data)}")

        result = cls()

        start_pos = 0
        data_length = len(data)

        for prop in cls.__dataclass_fields__:
            logger.trace(f"prop:={prop}")
            logger.trace(f"cls.__dataclass_fields__[prop]:={cls.__dataclass_fields__[prop]}")
            logger.trace(f"cls.__dataclass_fields__[prop].metadata:={cls.__dataclass_fields__[prop].metadata}")

            info = cls.__dataclass_fields__[prop].metadata.get('de_serialize', None)
            property_type = cls.__dataclass_fields__[prop].type
            byte_length = sys.getsizeof(property_type)
            value = None

            logger.trace(f"info:={info}")
            logger.trace(f"str(property_type):={str(property_type)}")
            logger.trace(f"type(property_type):={type(property_type)}")
            logger.trace(f"byte_length:={byte_length}")

            logger.trace(f"sys.getsizeof(property_type):={sys.getsizeof(property_type)}")

            if str(property_type) == "<class 'int'>": # TODO use isinstance ??
                logger.trace(f"property_type := int")
                byte_length = 1  # TODO hardcoded!!

            if str(property_type) == "<class 'str'>":
                logger.trace(f"property_type := str")
                logger.trace(f"len(result.prop):={len(getattr(result,prop))}")
                byte_length = len(getattr(result,prop))
                #  byte_length = 4

            if str(property_type) == "typing.List[int]":
                logger.trace(f"property_type := List[int]")
                # byte_length = 4 


            if str(property_type) == "<class 'int'>" and byte_length == 1:  # 1 Byte
                value = data[start_pos]
                logger.debug(f"Arg: {value}")
            elif property_type == int and byte_length in (2, 4):  # 2 Bytes => Short or 4 Bytes => Integer
                value = Deserializer.deserialize_to_int(data, start_pos, byte_length)
                logger.debug(f"Arg: {value}")
            elif str(property_type) == "<class 'str'>":
                value = data[start_pos:start_pos + byte_length].decode('utf-8').replace('\0', '').strip()
                logger.debug(f"Arg: {value}")
            elif property_type == bytes:
                value = data[start_pos:start_pos + byte_length]
                logger.debug(f"Arg: {value.hex()}")
            elif str(property_type) == "typing.List[int]":
                byte_length = 60 # TODO hardcoded!!
                byte_length = data_length - start_pos # TODO hardcoded!!is that better than byte_length = 60 ??
                logger.trace(f"byte_length:={byte_length}")
                divisor = 5
                length = 4
                array_size = byte_length // divisor
                value = []
                logger.trace(f"range(array_size): {range(array_size)}")
                logger.trace(f"len(data): {len(data)}")

                for i in range(array_size):
                    logger.trace(f"i * {divisor} + 1: {i * divisor + 1}")
                    logger.trace(f"start_pos: {start_pos}")

                    number = Deserializer.deserialize_to_int(data, i * divisor + 1 + start_pos, length)

                    value.append(number)
                logger.debug(f"Arg: {value}")

            setattr(result, prop, value)
            start_pos += byte_length

        return result
    

