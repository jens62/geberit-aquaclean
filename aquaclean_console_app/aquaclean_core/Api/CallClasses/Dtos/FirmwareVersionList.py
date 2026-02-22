from dataclasses import dataclass

# geberit-aquaclean/aquaclean-core/Api/CallClasses/Dtos/FirmwareVersionList.cs

@dataclass
class FirmwareVersionList:
    def __init__(self, A: int = 0, B: bytes = None):
        self.A = A
        self.B = B if B is not None else [None] * 60

    def __str__(self):
        return f"FirmwareVersionList: A={self.A}, B={self.B}"
