import array

from aquaclean_core.Frames.Frames.Frame              import Frame   

import logging

logger = logging.getLogger(__name__)

class SingleFrame(Frame): # Frame.Frame definiert SingleFrame als subclass of Frame, damit kann "super()" aufgerufen werden.
    PAYLOAD_LENGTH = 19

    def __init__(self):
        self.Payload = bytearray(self.PAYLOAD_LENGTH)

    @staticmethod
    def create_single_frame(data):
        frame = SingleFrame()
        frame.Payload = data[1:1+SingleFrame.PAYLOAD_LENGTH]
        return frame

    def serialize(self):
        logger.trace(f"type(self): {type(self)}")
        var1 = self.serialize_hdr()
        var1[1:1+self.PAYLOAD_LENGTH] = self.Payload
        return var1
    
    def __str__(self):
        return f"SingleFrame: {type(self)}"

