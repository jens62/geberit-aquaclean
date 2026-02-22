from threading import Lock
from typing import Dict, List, Callable, Any
from binascii import hexlify
import logging

from aquaclean_console_app.aquaclean_utils                                     import utils   
from aquaclean_console_app.myEvent                                             import myEvent   

logger = logging.getLogger(__name__)

class FrameCollector:
    """
    Responsible to collect all frames which belong together
    """

    def __init__(self):
        self.sync_obj = Lock()
        self.frame_data: Dict[int, bytes] = {}
        self.bitmap: List[int] = []
        self.expected_frames: int = 0
        self.transaction_in_progress: bool = False
        self.temp_frame_data: Dict[int, bytes] = {}
        self.TransactionCompleteFC = myEvent.EventHandler()
        self.SendControlFrame = myEvent.EventHandler()


    async def start_transaction(self, expected_frames: int):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")

        with self.sync_obj:
            self.frame_data = {}
            self.expected_frames = expected_frames
            self.bitmap = [0] * 8
            self.transaction_in_progress = True

            for frame_index, payload in self.temp_frame_data.items():
                await self.add_frame(frame_index, payload)
            self.temp_frame_data.clear()

    def set_bitmap(self, frame_number: int):
        var3 = frame_number // 8
        mask = 1 << (frame_number % 8)
        self.bitmap[var3] |= mask
        logger.debug(f"Controlling bitmap changed with frameNumber {frame_number} => {bin(self.bitmap[var3])[2:].zfill(8)} Bitmap: {''.join(f'{b:02X}' for b in self.bitmap)}")


    async def add_frame(self, frame_index: int, payload: bytes):
        logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
        logger.trace(f"frame_index: {frame_index}, Payload: {''.join(f'{b:02X}' for b in payload)}")

        with self.sync_obj:
            if not self.transaction_in_progress:
                logger.trace("not self.transaction_in_progress")
                self.temp_frame_data[frame_index] = payload
                #self.sync_obj.release()
                #RuntimeError: release unlocked lock
                return

            logger.debug(f"Received frame {frame_index + 1} of {self.expected_frames}: Payload={''.join(f'{b:02X}' for b in payload)}")

            self.frame_data[frame_index] = payload
            self.set_bitmap(frame_index)
            if len(self.frame_data) % 4 == 0 or len(self.frame_data) == self.expected_frames:
                # bitmap_clone = self.bitmap.copy()
                bitmap_clone = self.bitmap[:]

                logger.trace(f"len(self.frame_data): {len(self.frame_data)}, len(self.frame_data): {len(self.frame_data)}, self.expected_frames: {self.expected_frames}")
                logger.debug(f"Raising SendControlFrame with data {''.join(f'{b:02X}' for b in bitmap_clone)}")

                await self.SendControlFrame.invoke_async(self, bytes(bitmap_clone))

            logger.trace(f"len(self.frame_data): {len(self.frame_data)}, self.expected_frames: {self.expected_frames}")
            if len(self.frame_data) != self.expected_frames:
                return

            # Build message
            data = bytearray()
            for i in range(self.expected_frames):
                data.extend(self.frame_data[i])

            logger.debug("receive complete")
            logger.trace(f"receive complete: bytes(data)={''.join(f'{b:02X}' for b in bytes(data))}")

            self.transaction_in_progress = False
            self.temp_frame_data.clear()      

            logger.trace(f"in function {utils.currentClassName()}.{utils.currentFuncName()} called by {utils.currentClassName(1)}.{utils.currentFuncName(1)}")
            logger.trace(f"len(self.TransactionCompleteFC.get_handlers(): {len(self.TransactionCompleteFC.get_handlers())} for on_transaction_complete")

            await self.TransactionCompleteFC.invoke_async(self, bytes(data))



