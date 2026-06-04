from enum import Enum

class Commands(Enum):
    StartCleaningDevice = 4
    ExecuteNextCleaningStep = 5
    ToggleAnalShower = 0
    ToggleLadyShower = 1
    ToggleDryer = 2
    ToggleOrientationLight = 20   # AcSela only — use SetActiveCommonSetting(3, ...) for Mera Comfort
    OdourExtraction = 12           # toggle odour extraction on/off (live, while user is sitting)
    OdourExtractionRunOn = 13      # trigger odour extraction run-on
    TriggerFlushManually = 37
    ResetFilterCounter = 47
    PostponeDescaling = 9
    PrepareDescaling = 6
    ConfirmDescaling = 7
    CancelDescaling = 8
    StartLidPositionCalibration = 33
    LidPositionOffsetIncrement = 35
    LidPositionOffsetDecrement = 36
    LidPositionOffsetSave = 34
    ToggleLidPosition = 10

