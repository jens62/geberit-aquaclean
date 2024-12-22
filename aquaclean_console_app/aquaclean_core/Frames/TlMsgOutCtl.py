import array

class TlMsgOutCtl:
    def __init__(self):
        self.nTxState = 0
        self.vTxAckdFrameBitmask = bytearray(255)
        self.nTxFrameCnt = 0
        self.nTxLatencyMs = 0
        self.nTxUnackdFrameLimit = 0
        self.vTxBackLogCtr = bytearray(255)
        self.bTxHasMsgTypeByte = False
        self.nDataLen = 0

