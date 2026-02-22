from dataclasses import dataclass

# geberit-aquaclean/aquaclean-core/Api/CallClasses/Dtos/NodeList.cs

@dataclass
class NodeList:
    def __init__(self, A: int = 0, B: bytes = None):
        self.A = A
        self.B = B if B is not None else [None] * 128

    def __str__(self):
        return f"NodeList: A={self.A}, B={self.B}"
