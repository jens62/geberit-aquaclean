import asyncio

from aquaclean_core.Frames.FrameFactory                  import FrameFactory       
from aquaclean_core.Frames.Frames.FrameType              import FrameType   
from aquaclean_core.Frames.TlMsgOutCtl                   import TlMsgOutCtl   
from aquaclean_core.Frames.Frames.Frame                  import Frame   
from aquaclean_core.Frames.Frames.FlowControlFrame       import FlowControlFrame   
from aquaclean_core.Frames.FrameValidation                import FrameValidation as frame_validation
from aquaclean_core.Frames.FrameCollector                import FrameCollector

from bluetooth_le.LE.BluetoothLeConnector                import BluetoothLeConnector                   
from aquaclean_core.Frames.Frames.FlowControlFrame       import FlowControlFrame   

from aquaclean_utils                                     import utils   
from myEvent                                             import myEvent   

from binascii import hexlify

import logging
logger = logging.getLogger(__name__)


class FrameService:

    def __init__(self):
        self.frame_factory = FrameFactory()
        self.frame_collector = FrameCollector()
        self.frame_validator = frame_validation()
        self.tl_msg_out_ctl = TlMsgOutCtl()

        self.info_frame_count = 0
        self.InfoFrameReceived = myEvent.EventHandler()
        self.InfoFrameReceived += self.increment_info_frame_count

        self.TransactionCompleteFS = myEvent.EventHandler()
        self.frame_collector.TransactionCompleteFC += self.on_transaction_complete

        self.SendData = myEvent.EventHandler()
        
        self.frame_collector.SendControlFrame += self.on_send_control_frame


    def increment_info_frame_count(self, sender, arg):
        logger.trace(f"in increment_info_frame_count")
        self.info_frame_count += 1


    async def on_transaction_complete(self, sender, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"len(self.TransactionCompleteFS.get_handlers(): {len(self.TransactionCompleteFS.get_handlers())} for on_transaction_complete")

        self.TransactionCompleteFS(sender, data)


    async def on_send_control_frame(self, sender, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.debug(f"Send control frame: {''.join(f'{b:02X}' for b in data)}")
        control_frame_data = self.frame_factory.BuildControlFrame(data).serialize()

        await self.SendData.invoke_async(self, control_frame_data)


    async def process_data(self, data):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"process_data, data: {hexlify(data)}")
        if len(data) != FrameFactory.BLE_PAYLOAD_LEN:
            raise Exception("Payload length is not " + str(FrameFactory.BLE_PAYLOAD_LEN))

        frame = self.frame_factory.CreateFrameFromBytes(data)
        if frame is None:
            logger.debug("Frame type was not recognized")
            raise Exception("Frame type was not recognized")

        logger.debug(f"Processing new Frame: {frame}");
        logger.trace(f"frame.FrameType: {frame.FrameType}");

        if frame.FrameType == FrameType.SINGLE:
            logger.trace(f"Handling frame type SINGLE")
            
            single_frame = frame
            logger.trace(f"hexlify(single_frame.Payload): {hexlify(single_frame.Payload)}")

            if single_frame.IsSubFrameCount: 
                logger.trace(f"single_frame.IsSubFrameCount:")
                await self.frame_collector.start_transaction(single_frame.SubFrameCountOrIndex + 1)
                await self.frame_collector.add_frame(0, single_frame.Payload)
                logger.trace(f"nach self.frame_collector.add_frame")
            else:
                logger.trace(f"not is single_frame.IsSubFrameCount:")
                await self.frame_collector.add_frame(single_frame.SubFrameCountOrIndex, single_frame.Payload)
                logger.trace(f"nach self.frame_collector.add_frame")


        elif frame.FrameType == FrameType.FIRST:
            logger.trace(f"Handling frame type FIRST")
            first_frame = frame
            logger.trace(f"first_frame.frame_count_or_number: {first_frame.frame_count_or_number}")
            await self.frame_collector.start_transaction(first_frame.frame_count_or_number)
            await self.frame_collector.add_frame(0, first_frame.payload)

        elif frame.FrameType == FrameType.CONS:
            logger.trace(f"Handling frame type CONS")
            cons_frame = frame
            await self.frame_collector.add_frame(cons_frame.frame_count_or_number, cons_frame.payload)

        elif frame.FrameType == FrameType.CONTROL:
            logger.trace(f"Handling frame type CONTROL")
            if self._handle_control_frame(self.tl_msg_out_ctl, frame) > 0:
                logger.trace(f"self._handle_control_frame(self.tl_msg_out_ctl, frame) > 0, {self._handle_control_frame(self.tl_msg_out_ctl, frame)}")
                logger.trace(f"Message complete")
            else:
                logger.trace(f"self._handle_control_frame(self.tl_msg_out_ctl, frame) >= 0")
               
        elif frame.FrameType == FrameType.INFO:
            logger.trace(f"Handling frame type INFO")
            info_frame = frame
            if info_frame.info_frm_type == 1:
                logger.debug(f"rcv info frame, protocol={info_frame.proto_version}, "
                              f"rs={info_frame.rs_hi}{info_frame.rs_lo}, "
                              f"ts={info_frame.ts_hi * 256 + info_frame.ts_lo}")
                self.InfoFrameReceived(self, frame)


    async def wait_for_info_frames_async(self):
        logger.trace(f"in wait_for_info_frames_async")
        info_frame_count_did_not_change = 0
        last_info_frame_count = -1
        while self.info_frame_count < 10:
            if last_info_frame_count == self.info_frame_count:
                info_frame_count_did_not_change += 1
            
            logger.trace(f"self.info_frame_count: {self.info_frame_count}")
            logger.trace(f"last_info_frame_count: {last_info_frame_count}")
            logger.trace(f"info_frame_count_did_not_change: {info_frame_count_did_not_change}")

            if info_frame_count_did_not_change > 20:
                break

            last_info_frame_count = self.info_frame_count

            await asyncio.sleep(0.1)

        self.info_frame_count = 0

    async def send_frame_async(self, frame):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        serialized_frame = frame.serialize()
        logger.trace(f"hexlify(serialized_frame): {hexlify(serialized_frame)}")
        logger.trace("Going to send the data by means of eventHandler: await self.SendData.invoke_async(self, frame.serialize())")
        await self.SendData.invoke_async(self, frame.serialize())


    def _handle_control_frame(self, tl_msg_out_ctl: TlMsgOutCtl, frame: FlowControlFrame) -> int:  # type: ignore
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        if frame.ErrorCode != 0 or tl_msg_out_ctl.nTxState != 0:
            return 0

        tl_msg_out_ctl.vTxAckdFrameBitmask[:] = frame.AckdFrameBitmask[:]
        self.frame_validator.MarkTransactionOkPackets(tl_msg_out_ctl)

        highest_ok_frame_count = self._get_highest_ok_frame_cnt(tl_msg_out_ctl.nTxFrameCnt, tl_msg_out_ctl.vTxBackLogCtr)
        logger.debug(f"Highest OK Frame Count {highest_ok_frame_count}")
        if highest_ok_frame_count == tl_msg_out_ctl.nTxFrameCnt:
            tl_msg_out_ctl.n_tx_frame_cnt = 0
            tl_msg_out_ctl.n_tx_state = 0
            logger.debug(">>--------TX MSG SUCCESS----------<<")
            return 1
        else:
            tl_msg_out_ctl.n_tx_latency_ms = max(frame.TransactionLatency, 10)
            tl_msg_out_ctl.n_tx_unackd_frame_limit = frame.UnackdFrameLimit
            tl_msg_out_ctl.n_tx_state = 2
            logger.debug(f"Waiting for {highest_ok_frame_count} of {tl_msg_out_ctl.n_tx_frame_cnt}")
            return 0

    @staticmethod
    def _get_highest_ok_frame_cnt(txn_frame_cnt: int, txn_back_log_ctr) -> int:
        counter = 0
        for i in range(txn_frame_cnt):
            if txn_back_log_ctr[i] == 255:
                counter += 1
            else:
                break
        return counter            

