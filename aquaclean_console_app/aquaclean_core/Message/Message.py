import logging

from aquaclean_core.Message.CrcMessage     import CrcMessage      

logger = logging.getLogger(__name__)

class Message:
    BLEMSG_ID_CRC_NTF = 6
    BLEMSG_ID_CRC_REQ = 4
    BLEMSG_ID_CRC_RSP = 5
    BLEMSG_ID_PLAIN_NTF = 3
    BLEMSG_ID_PLAIN_REQ = 1
    BLEMSG_ID_PLAIN_RSP = 2

    def __init__(self):
        self.ID = 0
        self.Segments = 0

    @staticmethod
    def create_from_stream(data):
        logger.trace("create_from_stream")

        if data[0] in (Message.BLEMSG_ID_PLAIN_RSP, Message.BLEMSG_ID_PLAIN_NTF):
            raise Exception("Generation of 'PlainMessage' not supported yet")
        elif data[0] in (Message.BLEMSG_ID_CRC_RSP, Message.BLEMSG_ID_CRC_NTF):
            crcMsg = CrcMessage()
            return crcMsg.create_from_bytes(data)
        elif data[0] == Message.BLEMSG_ID_CRC_REQ:
            return None

    def serialize(self):
        raise NotImplementedError("Subclasses should implement this method")

