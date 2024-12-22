from typing import List
from dataclasses import dataclass

@dataclass
class SystemParameterList:
    a: int
    data_array: List[int]
    def __init__(self, a: int = 0, data_array: List[int] = None):
        self.a = a
        self.data_array = data_array if data_array is not None else [0] * 60

