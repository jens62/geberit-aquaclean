class MessageContext:
    def __init__(self,context=0, procedure=0, result_bytes=bytearray()):
        self.result = None
        self.context = context
        self.procedure = procedure
        self.result_bytes = result_bytes

