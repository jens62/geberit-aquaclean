import array


from aquaclean_console_app.aquaclean_core.Frames.Frames.Frame              import Frame   

class FirstConsFrame(Frame):
    PAYLOAD_LENGTH = 18

    def __init__(self):
        self.payload = bytearray(self.PAYLOAD_LENGTH)
        self.frame_count_or_number = 0
        self.is_sub_frame_count = False
        self.sub_frame_count_or_index = 0
        self.has_message_type_byte_b4 = False

    @staticmethod
    def create_first_cons_frame(data):
        # TODO: Will be overwritten from factory
        frame = FirstConsFrame()
        frame.is_sub_frame_count = (data[0] & 0x80) > 0
        frame.sub_frame_count_or_index = (data[0] >> 5) & 3  # 0000 0011
        frame.has_message_type_byte_b4 = (data[0] & 8) > 0
        # TODO: frame.frame_id_b765 = data[0] & 7
        frame.frame_count_or_number = data[1]

        frame.payload = bytearray(data[2:2+FirstConsFrame.PAYLOAD_LENGTH])
        return frame

    def serialize(self):
        raise NotImplementedError()

    def __str__(self):
        return f"FirstConsFrame: IsSubFrameCount={self.is_sub_frame_count}, SubFrameCountOrIndex={self.sub_frame_count_or_index}, HasMessageTypeByte_b4={self.has_message_type_byte_b4}, FrameCountOrNuber={self.frame_count_or_number}"
    
