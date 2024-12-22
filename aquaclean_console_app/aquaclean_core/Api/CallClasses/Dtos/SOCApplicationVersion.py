from dataclasses import dataclass

@dataclass
class SOCApplicationVersion:
    def __init__(self, A: bytes = [None] * 2, B: bytes = None):
        self.A = A
        self.B = B

    def __str__(self):
        return f"SOCApplicationVersion: A={self.A}, B={self.B}"