
from aquaclean_console_app.aquaclean_core.Message.CrcMessage     import CrcMessage      
from aquaclean_console_app.aquaclean_core.Message.MessageContext import MessageContext
from aquaclean_console_app.aquaclean_core.Message.Message        import Message    
from aquaclean_console_app.aquaclean_utils                       import utils   

import logging

logger = logging.getLogger(__name__)

class MessageService:
    def parse_message1(self, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        ms = Message()
        message = ms.create_from_stream(data)

        logger.debug(f"Parsing data to message with ID={message.id} Data={data.hex()}")

        if message.id == 5:
            crc_message = message  # Assuming message is of type CrcMessage
            if not crc_message.is_valid:
                logger.debug("Message invalid")
            context = crc_message.body[2]
            procedure = crc_message.body[3]
            arg_byte_length = crc_message.body[4]

            # Copy argument bytes into a new array
            arg_bytes = crc_message.body[5:5 + arg_byte_length]

            # Build return arguments from bytes
            return self.parse_message2(context, procedure, arg_bytes)
        else:
            logger.debug(f"Unknown message ID={message.id}")

        return None
    

    def parse_message2(self, context, procedure, data):
        logger.debug(f"Parsing message with Context={context:02X}, Procedure={procedure:02X}, Data={data.hex()}")

        logger.trace("context %02x", context)
        logger.trace("procedure %02x", procedure)
        logger.trace("data.hex() %s", data.hex())

        msgCtx = MessageContext(
            context=context,
            procedure=procedure,
            result_bytes=data,
        )
        logger.trace("msgCtx.context %02x", msgCtx.context)
        logger.trace("msgCtx.procedure %02x", msgCtx.procedure)
        logger.trace("msgCtx.result_bytes %s", msgCtx.result_bytes.hex())

        return msgCtx
    

    def build_message(self, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()}")

        return self.build_message_segment_of_type(4, data, 0x00, 0x01)
    
    
    def int_to_signed_short(self, value):
        return -(value & 0x8000) | (value & 0x7fff)


    def signedToUnsigned(self, n, byte_count): 
        return int.from_bytes(n.to_bytes(byte_count, 'little', signed=True), 'little', signed=False)


    def build_message_segment_of_type(self, message_id, data, is_zero, is_one):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()}")
        logger.trace(f"message_id: {message_id}, data: {data}, is_zero: {is_zero}, is_one: {is_one}")

        message_segment = is_zero - 1 + ((is_one - 1) * 16)

        logger.trace(f"TODO: some additional gymnastics on message_segment. Neccessary??")
        if message_segment < 0:
            logger.trace(f"TODO: message_segment ({message_segment}) < 0")
            message_segment = 256 + message_segment
            logger.trace(f"TODO: message_segment changing to 256 + message_segment == {message_segment}")

        if message_id == 4:
            if CrcMessage.size_of_header() + len(data) > 256:
                return None
            crcMessage = CrcMessage.create(message_id, message_segment, data)
            logger.trace(f"crcMessage: {crcMessage}")
            return CrcMessage.create(message_id, message_segment, data)
        else:
            return None
        

