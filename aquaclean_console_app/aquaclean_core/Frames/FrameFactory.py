from typing import List, Optional, Any
from binascii import hexlify


from aquaclean_core.Frames.Frames.InfoFrame          import InfoFrame        as info_frame
from aquaclean_core.Frames.Frames.FrameType          import FrameType        as frame_type
from aquaclean_core.Frames.Frames.FlowControlFrame   import FlowControlFrame as flow_control_frame
from aquaclean_core.Frames.Frames.SingleFrame        import SingleFrame      as single_frame
from aquaclean_core.Frames.Frames.FirstConsFrame     import FirstConsFrame   as first_cons_frame
from aquaclean_core.Frames.Frames.Frame              import Frame            as frame

import logging

logger = logging.getLogger(__name__)

from typing import *

# Assuming FrameType is an enum, we'll define it here
# class FrameType(enum.Enum):
#     SINGLE = 0
#     FIRST = 1
#     CONS = 2
#     CONTROL = 3
#     INFO = 4

class SingleFrame(frame):
    @classmethod
    def CreateSingleFrame(cls, data: bytes) -> 'SingleFrame':
        # Implementation not provided, so we'll leave it as a pass
        pass

class FirstConsFrame(frame):
    @classmethod
    def CreateFirstConsFrame(cls, data: bytes) -> 'FirstConsFrame':
        # Implementation not provided, so we'll leave it as a pass
        pass

class FlowControlFrame(frame):
    def __init__(self):
        super().__init__()
        self.UnackdFrameLimit: int = 0
        self.TransactionLatency: int = 0
        self.AckdFrameBitmask: bytearray = bytearray(8)

    @classmethod
    def CreateFlowControlFrame(cls, data: bytes) -> 'FlowControlFrame':
        # Implementation not provided, so we'll leave it as a pass
        pass

class InfoFrame(frame):
    @classmethod
    def CreateInfoFrame(cls, data: bytes) -> 'InfoFrame':
        # Implementation not provided, so we'll leave it as a pass
        pass

class FrameFactory:
    BLE_PAYLOAD_LEN = 20

    @staticmethod
    def getFrameTypeFromHeaderByte(headerByte: int) -> frame_type: # type: ignore
        return frame_type((headerByte >> 5) & 7)
    
    @staticmethod
    def CreateFrameFromBytes(data: bytes) -> Optional[frame]: # type: ignore
        logger.trace("in CreateFrameFromBytes")
        frameType = FrameFactory.getFrameTypeFromHeaderByte(data[0])
        logger.trace(f"frameType: {frameType}") 

        tlFrame = None

        if frameType == frame_type.SINGLE:
            logger.trace(f"SINGLE Frame")
            singleframe = single_frame()
            tlFrame = singleframe.create_single_frame(data)
        elif frameType in [frame_type.FIRST, frame_type.CONS]:
            logger.trace(f"FIRST or CONS Frame")
            firstconsframe = first_cons_frame()
            tlFrame = firstconsframe.create_first_cons_frame(data)
        elif frameType == frame_type.CONTROL:
            logger.trace(f"CONTROL Frame")
            flowcontrolframe = flow_control_frame()
            tlFrame = flowcontrolframe.create_flow_control_frame(data)
        elif frameType == frame_type.INFO:
            logger.trace(f"Info Frame")
            infoframe = info_frame()
            # tlFrame = infoframe.create_info_frame( InfoFrame, data)
            tlFrame = infoframe.create_info_frame( data)

        if tlFrame is not None:
            tlFrame.FrameType = frameType
            tlFrame.HasMessageTypeByte_b4 = (data[0] & 16) > 0
            tlFrame.SubFrameCountOrIndex = (data[0] >> 1) & 3
            tlFrame.IsSubFrameCount = (data[0] & 1) > 0

        return tlFrame

    @staticmethod
    def BuildControlFrame(bitmap: bytes) -> FlowControlFrame:
        logger.trace(f"BuildControlFrame: {hexlify(bitmap)}")

        flowControlFrame = flow_control_frame()
        flowControlFrame.HasMessageTypeByte_b4 = True
        flowControlFrame.FrameType = frame_type.CONTROL
        flowControlFrame.UnackdFrameLimit = 8
        flowControlFrame.TransactionLatency = 0
        flowControlFrame.AckdFrameBitmask[:8] = bitmap[:8]
        logger.debug(f"Bitmask: {flowControlFrame.AckdFrameBitmask.hex()}")
        return flowControlFrame

    @staticmethod
    def BuildSingleFrame(data: bytes) -> SingleFrame:
        logger.trace(f"BuildSingleFrame: {hexlify(data)}")
        singleFrm = single_frame()
        singleFrm.FrameType = frame_type.SINGLE
        singleFrm.HasMessageTypeByte_b4 = True
        singleFrm.IsSubFrameCount = True
        singleFrm.SubFrameCountOrIndex = 0
        singleFrm.Payload = bytearray(19)
        singleFrm.Payload[:19] = data[:19]
        return singleFrm

