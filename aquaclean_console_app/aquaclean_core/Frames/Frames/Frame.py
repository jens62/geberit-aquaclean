from enum import Enum

from aquaclean_console_app.aquaclean_core.Frames.Frames.FrameType          import FrameType

class FrameType(Enum):
    pass  # Assuming FrameType is an enum, define its values here

class Frame:
    BLE_PAYLOADLEN = 20

    def __init__(self):
        self.HasMessageTypeByte_b4 = False
        self.SubFrameCountOrIndex = 0
        self.IsSubFrameCount = False
        self.FrameType = None

    def serialize_hdr(self):
        data = bytearray(self.BLE_PAYLOADLEN)
        data[0] = 0  # Info Header ???
        # data[0] |= (self.frame_type.value << 5) if self.frame_type else 0
        data[0] |= (self.FrameType.value << 5) if self.FrameType else 0

        # if self.has_message_type_byte_b4:
        if self.HasMessageTypeByte_b4:
            data[0] |= 16

        # data[0] |= (self.sub_frame_count_or_index << 1)
        data[0] |= (self.SubFrameCountOrIndex << 1)
        # if self.is_sub_frame_count:
        if self.IsSubFrameCount:
            data[0] |= 1

        return data

    def serialize(self):
        raise NotImplementedError("Subclasses must implement serialize method")

