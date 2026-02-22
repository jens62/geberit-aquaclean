

from aquaclean_console_app.aquaclean_utils import utils   

import logging
logger = logging.getLogger(__name__)

class CrcMessage:
    def __init__(self):
        self._crc16_hi = 0
        self._crc16_lo = 0
        self.len_hi = 0
        self.len_lo = 0
        self.body = bytearray(256)

    @property
    def crc16_hi(self):
        return self._crc16_hi

    @property
    def crc16_lo(self):
        return self._crc16_lo

    @staticmethod
    def create_from_bytes(data):
        message = CrcMessage()
        message.id = data[0]
        message.segments = data[1]
        message.len_hi = data[2]
        message.len_lo = data[3]
        message._crc16_hi = data[4]
        message._crc16_lo = data[5]

        length = min(message.len_hi * 256 + message.len_lo, 256)
        message.body[:length] = data[6:6 + length]
        return message

    @staticmethod
    def create(message_id, message_segment, data):
        crc_message = CrcMessage()
        crc_message.id = message_id
        crc_message.segments = message_segment
        crc_message.body[:len(data)] = data
        crc_message.len_hi = len(data) // 256
        crc_message.len_lo = len(data) % 256

        var7 = crc_message.crc16_calculation(crc_message.body, len(data))
        crc_message._crc16_hi = var7 // 256
        crc_message._crc16_lo = var7 % 256

        return crc_message

    @staticmethod
    def size_of_header():
        return 6

    def serialize(self):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        var3 = bytearray(262)
        var3[0] = self.id
        var3[1] = self.segments
        var3[2] = self.len_hi
        var3[3] = self.len_lo
        var3[4] = self._crc16_hi
        var3[5] = self._crc16_lo
        var3[6:262] = self.body
        return var3

    @property
    def is_valid(self):
        length = (self.len_hi << 8) + self.len_lo
        i2 = self.crc16_calculation(self.body, length)
        return i2 == (self._crc16_hi << 8) + self._crc16_lo


    def crc16_calculation(self, data, length):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()}")
        logger.silly(f"data: {data}, length: {length}")

        i2 = 4660
        for i3 in range(length):
            i2 = (((i2 << 8) & 0xFF00) | ((i2 >> 8) & 0x00FF)) ^ (data[i3] & 0xFF)
            i2 = (i2 ^ ((i2 & 0xFF) >> 4)) & 0xFFFF
            i2 = (i2 ^ ((i2 << 8) << 4)) & 0xFFFF
            i2 = (i2 ^ (((i2 & 0xFF) << 4) << 1)) & 0xFFFF

        logger.silly(f"i2: {i2}")        
        return i2


    def __str__(self):
        return f"ID='{self.id}', Segments='{self.segments}', LenHi='{self.len_hi}', LenLo='{self.len_lo}', Crc16Hi='{self._crc16_hi}', Crc16Low='{self._crc16_lo}'"

