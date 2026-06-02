# Protocol Gap Analysis — Bridge vs. Geberit Home App

Identifies what the official Geberit Home app implements that the bridge does not yet
implement.  Sources: nRF52840 OTA capture (2026-06-01, device HB2304EU298413, firmware
RS146.21) and Geberit Home v2.13.2 app analysis.

---

## Procedures seen in capture — bridge status

The table below covers every distinct procedure observed in the Mera Comfort nRF52840
capture (`Mera Comfort sniff with nRF52840.pcapng`).

| Proc | Name | Bridge status | TODO? |
|------|------|--------------|-------|
| 0x82 | GetDeviceIdentification | ✅ implemented | — |
| 0x86 | GetDeviceInitialOperationDate | ✅ implemented | — |
| 0x81 | GetSOCApplicationVersions | ✅ implemented | — |
| 0x05 | GetNodeList | ✅ implemented | — |
| 0x0E | GetFirmwareVersionList | ✅ CallClass exists | — |
| 0x11 | SubscribeNotif_0x11 | ✅ implemented (unlock) | — |
| 0x13 | SubscribeNotif_0x13 | ✅ implemented (unlock) | — |
| 0x0D | GetSystemParameterList | ✅ implemented | add params 12/13 (TODO) |
| 0x59 | GetFilterStatus | ✅ implemented | — |
| 0x53 | GetStoredProfileSetting | ✅ implemented (IDs 0–10) | add IDs 11–14 (TODO) |
| 0x54 | SetStoredProfileSetting | ✅ CallClass exists, not exposed | wire interfaces (TODO) |
| 0x51 | GetStoredCommonSetting | ✅ reads IDs 0–3 only | add IDs 4–12 + fix ID 0 label (TODO) |
| 0x52 | SetStoredCommonSetting | ✅ CallClass exists | — |
| 0x09 | SetCommand — ToggleLidPosition (10) | ✅ implemented | — |
| 0x09 | SetCommand — code 3 (Stop) | ❌ not in Commands.py | TODO |
| 0x08 | SetActiveProfileSetting | ❌ not implemented | TODO |
| 0x07 | GetStoredProfileSetting (per-node) | ❌ not implemented | TODO |
| 0x0A | GetActiveCommonSetting | ❌ not called | TODO |
| 0x0B | SetActiveCommonSetting | ❌ not called | TODO |
| 0x55 | UnknownProc | ❌ not called | TODO |

---

## Complete CommonSetting ID map (proc 0x51 / 0x52)

Confirmed from app analysis (2026-06-02).  Bridge currently reads IDs 0–3 only; ID 0 label
is also wrong.

| ID | Correct name | Bridge label | Device restriction |
|----|-------------|--------------|-------------------|
| 0 | `WaterHardness` | ✅ fixed | all |
| 1 | `OrientationLightBrightness` | Brightness | all |
| 2 | `OrientationLightColour` | Color | all |
| 3 | `OrientationLightMode` | Activation | all |
| 4 | `LidSensorRange` | — | Mera Comfort |
| 5 | `OdourExtractionRunOn` | — | all |
| 6 | `LidAutoOpen` | — | Mera Comfort |
| 7 | `LidAutoClose` | — | Mera Comfort |
| 8 | `AutoFlush` | — | all |
| 9 | `DemoMode` | — | all |
| 10 | `LightSensorSensitivity` | — | AcSela only |
| 11 | `CareMode` | — | Mera Floorstanding |
| 12 | `Language` | — | all |

---

## Complete ProfileSetting ID map (proc 0x53 / 0x54 stored, 0x07 / 0x08 active)

| ID | Name | Bridge status | Notes |
|----|------|--------------|-------|
| 0 | `OdourExtraction` | ✅ | |
| 1 | `OscillatorState` | ✅ | |
| 2 | `AnalShowerPressure` | ✅ | |
| 3 | `LadyShowerPressure` | ✅ | |
| 4 | `AnalShowerPosition` | ✅ | |
| 5 | `LadyShowerPosition` | ✅ | |
| 6 | `WaterTemperature` | ✅ | |
| 7 | `WcSeatHeat` | ✅ | |
| 8 | `DryerTemperature` | ✅ | |
| 9 | `DryerState` | ✅ | |
| 10 | `SystemFlush` | ✅ | |
| 11 | `SeatHeating` | ❌ | Tuma Comfort only |
| 12 | `WaterHeating` | ❌ | Mera Comfort, Tuma Comfort |
| 13 | `DryerFanPower` | ❌ | confirmed in capture at t=108.3s (value=1) |
| 14 | `LadyOscillation` | ❌ | stored profile only |

---

## Complete SetCommand code map (proc 0x09)

Confirmed from app analysis (2026-06-02).  ✅ = wired on all interfaces.  ⚠️ = partial.

| Code | Name | Bridge status |
|------|------|--------------|
| 0 | `ToggleAnalShower` | ✅ |
| 1 | `ToggleLadyShower` | ✅ |
| 2 | `ToggleDryer` | ✅ |
| 3 | `Stop` | ❌ (mislabelled OpenLid in earlier analysis) |
| 4 | `StartCleaningDevice` | ❌ |
| 5 | `ExecuteNextCleaningStep` | ❌ |
| 6 | `PrepareDescaling` | ✅ |
| 7 | `ConfirmDescaling` | ✅ |
| 8 | `CancelDescaling` | ✅ |
| 9 | `PostponeDescaling` | ✅ |
| 10 | `ToggleLidPosition` | ✅ |
| 11 | `StartLidPositionCalibration` | ❌ |
| 12 | `OdourExtraction` | ❌ |
| 13 | `OdourExtractionRunOn` | ❌ |
| 20 | `ToggleOrientationLight` | ⚠️ REST only |
| 33 | `StartLidPositionCalibration` | ❌ |
| 34 | `LidPositionOffsetSave` | ❌ |
| 35 | `LidPositionOffsetIncrement` | ❌ |
| 36 | `LidPositionOffsetDecrement` | ❌ |
| 37 | `TriggerFlushManually` | ✅ |
| 39 | `SprayCalibration` | ❌ |
| 46 | `ShowerArmOffsetStart` | ❌ |
| 47 | `ShowerArmOffsetSave` | ❌ (conflicts with `ResetFilterCounter=47` — verify) |
| 48 | `ShowerArmOffsetIncrement` | ❌ |
| 49 | `ShowerArmOffsetDecrement` | ❌ |
| 50 | `DryerArmOffsetStart` | ❌ |
| 51 | `DryerArmOffsetSave` | ❌ |
| 52 | `DryerArmOffsetIncrement` | ❌ |
| 53 | `DryerArmOffsetDecrement` | ❌ |
| 54 | `Draining` | ❌ AcCama only |
| 47 | `ResetFilterCounter` | ✅ (code 47 — see conflict note above) |
| 78 | `ResetStatistics` | ❌ |

---

## Additional procedures confirmed from app analysis

| Proc | Name | Bridge status | Notes |
|------|------|--------------|-------|
| 0x06 | `GetActualOutletTemperature` | ❌ | Live water outlet temp during shower; not seen in capture yet |
| 0x45 | `GetStatisticsDescale` | ✅ | |
| 0x56 | `SetDeviceRegistrationLevel` | ❌ | Value 257 observed; purpose unclear |

---

## SPL parameter map (proc 0x0D) — for reference

| Index | Name | Bridge status |
|-------|------|--------------|
| 0 | `user_sitting` | ✅ |
| 1 | `anal_shower` | ✅ |
| 2 | `lady_shower` | ✅ |
| 3 | `dryer` | ✅ |
| 4 | `descaling_state` | ⚠️ polled, not exposed in SSE/MQTT (TODO) |
| 5 | `descaling_min` | ⚠️ polled, not exposed in SSE/MQTT (TODO) |
| 6 | `last_error` | ✅ |
| 7 | `service_state` | ✅ (labelled `unknown7` in bridge) |
| 8 | `SprayCalibration` | ❌ device-variant specific, not for Mera Comfort |
| 9 | `OrientationLight` | ❌ AcSela only |
| 10 | `Draining` | ❌ AcCama only |
| 11 | `ConnectedSsmDevices` | ❌ AcSela only |
| 12 | `LidOffsetPosition` | ❌ Mera Comfort fw ≥ RS25 — **TODO (confirmed safe)** |
| 13 | `ShowerArmOffsetPosition` | ❌ Mera Comfort — **TODO (confirmed safe)** |
| 14 | `DryerArmOffsetPosition` | ❌ not tested |
