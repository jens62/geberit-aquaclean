from enum import Enum

class ProfileSettings(Enum):
    OdourExtraction = 0  # bool
    OscillatorState = 1  # bool
    AnalShowerPressure = 2
    LadyShowerPressure = 3
    AnalShowerPosition = 4
    LadyShowerPosition = 5
    WaterTemperature = 6
    WcSeatHeat = 7
    DryerTemperature = 8
    DryerState = 9  # bool
    SystemFlush = 10  # bool