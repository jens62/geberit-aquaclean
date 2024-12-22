import array


from aquaclean_core.Frames.Frames.Frame              import Frame   

# c#
# internal class FlowControlFrame : Frame

class FlowControlFrame(Frame):
    BITMASK_LENGTH = 8

    def __init__(self):
        self.ErrorCode = 0
        self.TransactionLatency = 0  # In milliseconds
        self.UnackdFrameLimit = 0
        self.AckdFrameBitmask = bytearray(self.BITMASK_LENGTH)
        self.SubFrameCountOrIndex = 0
        self.IsSubFrameCount = False

    @staticmethod
    def create_flow_control_frame(data):
        frame = FlowControlFrame()
        frame.ErrorCode = data[1]
        frame.UnackdFrameLimit = data[2]
        frame.TransactionLatency = data[3]
        frame.AckdFrameBitmask = data[4:4+FlowControlFrame.BITMASK_LENGTH]
        return frame

    def serialize(self):
        # var1 = bytearray(super().serialize_hdr())
        var1 = self.serialize_hdr()
        var1[1] = self.ErrorCode
        var1[2] = self.UnackdFrameLimit
        var1[3] = self.TransactionLatency
        var1[4:4+self.BITMASK_LENGTH] = self.AckdFrameBitmask
        return var1

    def __str__(self):
        return f"FlowControlFrame: ErrorCode=0x{self.ErrorCode:02X}, UncheckedFrameLimit=0x{self.UnackdFrameLimit:02X}, TransactionLatency=0x{self.TransactionLatency:02X}, AckdFrameBitmask={self.AckdFrameBitmask.hex()}"
