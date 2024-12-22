class ApiCallAttribute:
    def __init__(self, context: int = 0, procedure: int = 0, node: int = 0):
        self.context = context
        self.procedure = procedure
        self.node = node

    def __str__(self):
        return f"ApiCallAttribute: context=0x{self.context:02x}, procedure=0x{self.procedure:02x}, node=0x{self.node:02x}"


