"""Geberit Ble20 data-point behavior codes.

All constants derived from the Geberit vendor application source.
"""
from enum import IntEnum


class DpBehavior(IntEnum):
    Info          = 0
    Status        = 1
    Command       = 2
    Nvm           = 3
    Protected     = 4
    CommandLocked = 5
