from threading import Event, Lock
import inspect
import logging

logger = logging.getLogger(__name__)

class EventHandler ():

    def __init__(self):
        self.__handlers = []

    def __iadd__(self, handler):
        self.__handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.__handlers.remove(handler)
        return self

    def __call__(self, *args, **kwargs):
        for handler in self.__handlers:
            logger.trace(f"inspect.isawaitable(handler): {inspect.isawaitable(handler)}")
            logger.trace(f"inspect.iscoroutine(handler): {inspect.iscoroutine(handler)}")
            logger.trace(f"inspect.isfunction(handler): {inspect.isfunction(handler)}")
            logger.trace(f"inspect.iscoroutinefunction(handler): {inspect.iscoroutinefunction(handler)}")
            logger.trace(f"inspect.ismethod(handler): {inspect.ismethod(handler)}")
            # von FrameService 61: self.TransactionCompleteFS(sender, data)
            # 11 06:22:43,660 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 22 TRACE: inspect.isawaitable(handler): False
            # 2024-12-11 06:22:43,660 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 23 TRACE: inspect.iscoroutine(handler): False
            # 2024-12-11 06:22:43,661 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 24 TRACE: inspect.isfunction(handler): False
            # 2024-12-11 06:22:43,661 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 25 TRACE: inspect.iscoroutinefunction(handler): False
            # 2024-12-11 06:22:43,661 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 26 TRACE: inspect.ismethod(handler): True
            handler(*args, **kwargs)
        #self.__handlers.clear()

    async def invoke_async (self, *args, **kwargs):
        logger.trace(f"in invoke_async")
        for handler in self.__handlers:
            logger.trace(f"inspect.isawaitable(handler): {inspect.isawaitable(handler)}")
            logger.trace(f"inspect.iscoroutine(handler): {inspect.iscoroutine(handler)}")
            logger.trace(f"inspect.isfunction(handler): {inspect.isfunction(handler)}")
            logger.trace(f"inspect.iscoroutinefunction(handler): {inspect.iscoroutinefunction(handler)}")
            logger.trace(f"inspect.ismethod(handler): {inspect.ismethod(handler)}")
            # von FrameService 261:  await self.SendData.invoke_async(self, frame.serialize())
            # 2024-12-11 06:01:40,683 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 28 TRACE: inspect.isawaitable(handler): False
            # 2024-12-11 06:01:40,683 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 29 TRACE: inspect.iscoroutine(handler): False
            # 2024-12-11 06:01:40,683 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 30 TRACE: inspect.isfunction(handler): False
            # 2024-12-11 06:01:40,683 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 31 TRACE: inspect.iscoroutinefunction(handler): True
            # 2024-12-11 06:01:40,684 geberit-aquaclean.aquaclean-console-app.myEvent.myEvent 32 TRACE: inspect.ismethod(handler): True
            await handler(*args, **kwargs)

    def get_handlers(self):
        return self.__handlers
    
    # def remove(self, handler):
    #     # self.__isub__(handler)
    #     # self.__handlers = []
    #     self.__handlers.pop(0)