
class FrameValidation:
    def tl_isValidAckBitmap(self, tlMsgOutCtl, bitmask):
        var5 = 0

        for byte in bitmask:
            var7 = 1
            var8 = 0
            for _ in range(8):
                var8 = var7
                if byte & var7 == var7:
                    if tlMsgOutCtl.vTxBackLogCtr[var5] == 0:
                        return False
                    var8 = var7 << 1
                var5 += 1
                if var5 == tlMsgOutCtl.nTxFrameCnt:
                    return True
                var7 = var8

        return True

    def MarkTransactionOkPackets(self, tlMsgOutCtl):
        var4 = -1
        var2 = 1

        for i in range(tlMsgOutCtl.nTxFrameCnt):
            if i % 8 == 0:
                var2 = 1
                var4 += 1
            else:
                var2 = var2 << 1

            if tlMsgOutCtl.vTxAckdFrameBitmask[var4] & var2 == var2:
                tlMsgOutCtl.vTxBackLogCtr[i] = 255

    # Not used
    def tl_setFrameBit(self, bitmap, destination):
        var3 = destination // 8
        bitmap[var3] |= 1 << (destination % 8)

    # Not used
    def tl_getHighestOkFrameNo(self, var1, var2):
        var3 = 0
        var6 = 1

        var5 = 0
        while var5 < var2 and (var1[var5] & 255) == 255:
            var3 += 8
            var5 += 1

        var4 = var3
        if var5 < var2:
            var2 = 0

            while True:
                var4 = var3
                if var2 >= 8:
                    break
                var4 = var3
                if (var1[var5] & var6) != var6:
                    break
                var3 += 1
                var6 = var6 << 1
                var2 += 1

        return var4

