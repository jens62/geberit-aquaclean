"""Geberit Ble20 data-point type codes.

All constants derived from the Geberit vendor application source.
"""
from enum import IntEnum


class DpType(IntEnum):
    Unused         = 0
    Binary         = 1
    MilliSeconds   = 2
    Seconds        = 3
    Minutes        = 4
    Hours          = 5
    Permill        = 6
    Percent        = 7
    String         = 8
    Counter        = 9
    Enum           = 10
    OffOn          = 11
    OffOnAuto      = 12
    TimeStampUtc   = 13
    TimeStampLocal = 14
    Signed         = 15
