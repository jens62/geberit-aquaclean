
from aquaclean_console_app.aquaclean_core.Frames.Frames.FrameType          import FrameType        as frame_type
from aquaclean_console_app.aquaclean_core.Frames.Frames.Frame              import Frame   

class InfoFrame(Frame):
    INFO_PROTOVERS_2_0 = 32
    INFO_PROTVERS_CAPABILITIES = 1
    INFO_RXCAP_TXNOWAIT_AFTER_FF = 1

    def __init__(self):
        self._info_frm_type = 0
        self._proto_version = 0
        self._max_packet_len = 0
        self._max_packet_count = 0
        self._capa_flags0 = 0
        self._capa_flags1 = 0
        self._mode_flags0 = 0
        self._mode_flags1 = 0
        self._ctrl_flags0 = 0
        self._ctrl_flags1 = 0
        self._cmd = 0
        self._rs_hi = 0
        self._rs_lo = 0
        self._ts_hi = 0
        self._ts_lo = 0

    @property
    def info_frm_type(self):
        return self._info_frm_type

    @property
    def proto_version(self):
        return self._proto_version

    @property
    def max_packet_len(self):
        return self._max_packet_len

    @property
    def max_packet_count(self):
        return self._max_packet_count

    @property
    def capa_flags0(self):
        return self._capa_flags0

    @property
    def capa_flags1(self):
        return self._capa_flags1

    @property
    def mode_flags0(self):
        return self._mode_flags0

    @property
    def mode_flags1(self):
        return self._mode_flags1

    @property
    def ctrl_flags0(self):
        return self._ctrl_flags0

    @property
    def ctrl_flags1(self):
        return self._ctrl_flags1

    @property
    def cmd(self):
        return self._cmd

    @property
    def rs_hi(self):
        return self._rs_hi

    @property
    def rs_lo(self):
        return self._rs_lo

    @property
    def ts_hi(self):
        return self._ts_hi

    @property
    def ts_lo(self):
        return self._ts_lo

    @classmethod
    def create_info_frame(cls, data):
        frame = cls()
        frame._info_frm_type = data[1]
        frame._proto_version = data[2]
        frame._max_packet_len = data[3]
        frame._max_packet_count = data[4]
        frame._capa_flags0 = data[5]
        frame._capa_flags1 = data[6]
        frame._mode_flags0 = data[7]
        frame._mode_flags1 = data[8]
        frame._ctrl_flags0 = data[9]
        frame._ctrl_flags1 = data[10]
        frame._cmd = data[11]
        frame._rs_hi = data[12]
        frame._rs_lo = data[13]
        frame._ts_hi = data[14]
        frame._ts_lo = data[15]
        return frame

    def serialize(self):
        var1 = super().serialize_hdr()
        var1[1] = self._info_frm_type
        var1[2] = self._proto_version
        var1[3] = self._max_packet_len
        var1[4] = self._max_packet_count
        var1[5] = self._capa_flags0
        var1[6] = self._capa_flags1
        var1[7] = self._mode_flags0
        var1[8] = self._mode_flags1
        var1[9] = self._ctrl_flags0
        var1[10] = self._ctrl_flags1
        var1[11] = self._cmd
        var1[12] = self._rs_hi
        var1[13] = self._rs_lo
        var1[14] = self._ts_hi
        var1[15] = self._ts_lo
        return var1

    def __str__(self):
        text = ("InfoFrmType = {0:02X}, "
                "ProtoVersion = {1:02X}, "
                "MaxPacketLen = {2:02X}, "
                "MaxPacketCount = {3:02X}, "
                "CapaFlags0 = {4:02X}, "
                "CapaFlags1 = {5:02X}, "
                "ModeFlags0 = {6:02X}, "
                "ModeFlags1 = {7:02X}, "
                "CtrlFlags0 = {8:02X}, "
                "CtrlFlags1 = {9:02X}, "
                "Cmd = {10:02X}, "
                "RsHi = {11:02X}, "
                "RsLo = {12:02X}, "
                "TsHi = {13:02X}, "
                "TsLo = {14:02X}")
        return text.format(
            self._info_frm_type,
            self._proto_version,
            self._max_packet_len,
            self._max_packet_count,
            self._capa_flags0,
            self._capa_flags1,
            self._mode_flags0,
            self._mode_flags1,
            self._ctrl_flags0,
            self._ctrl_flags1,
            self._cmd,
            self._rs_hi,
            self._rs_lo,
            self._ts_hi,
            self._ts_lo
        )

