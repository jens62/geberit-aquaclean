# AquaClean Model Feature Matrix

Sources: iOS app v2.14.1 — `AqCDeviceSettingsViewModel`, `AqCPersonalSettingsViewModel`,
`GeberitDeviceExtensions`, `AquacleanOldVariant`; `ble-protocol.md`; `protocol-gap-analysis.md`.

All models in this document use the **AquaClean protocol** (proc 0x82, 0x0D, 0x09, 0x53,
0x51, …) over the Standard GATT profile (`3334429d-…`). They are identified as variant
codes of the **AquacleanOld** device series in proc 0x82 `GetDeviceIdentification`.

---

## Proc 0x82 variant byte → device type

| Variant byte | App device type | Product name |
|---|---|---|
| 1 | `AcMeraFloorstanding` | Mera Floorstanding |
| 2 | `AcMeraClassic` | **Mera Classic** |
| 3 | `AcMeraComfort` | Mera Comfort |
| 4 | `AcTumaClassic` | Tuma Classic |
| 5 | `AcTumaComfort` | Tuma Comfort |
| 6 | `AcSela` | **Sela** |
| 7 | `AcCamaTestset` | Cama Testset |
| 8 | `AcCama` | Cama |

Source: `AquacleanOldVariant.cs` (enum ordinals) cross-referenced with the switch in `-.cs`.

---

## Feature matrix

| Feature | Mera Comfort | **Mera Classic** | **Sela** | Mera Floorstanding | Tuma Comfort | Tuma Classic |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|
| Anal shower | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Lady shower | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| Air dryer | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| Odour extraction | ✅ | ✅ | ❌ | ✅ | ✅ | ❌ |
| Orientation light | ✅ (proc 0x0B) | ❌ | ✅ (SetCommand 20) | ❌ | ❌ | ❌ |
| Lid motor (toggle) | ✅ | ✅ | ✅? | ✅? | ✅? | ❌ |
| Lid approach sensor (auto open/close) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Seat heater | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Water heater | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |

Source: app settings menus (`AqCDeviceSettingsViewModel._E003–_E006`) and personal settings
tabs (`AqCPersonalSettingsViewModel._E002–_E007`).

---

## SPL parameter lists (proc 0x0D `GetSystemParameterList`)

### Indices 8–11 — device-variant specific (DANGER claim revised 2026-06-26)

**Revised finding (2026-06-26, nRF52840 capture of iOS app against HB2304EU298413 RS146.21 fw):**
The iOS app sends `[0,1,2,3,4,5,6,7,8,9,10,11]` to a real Mera Comfort. The device returns
zeros for indices 8–11 without error and without any observable `GetFilterStatus` corruption.
The earlier "DANGER — permanently corrupts until power-cycle" claim is unverified for Mera Comfort
with current firmware. Indices 8–11 appear safe to query; the device returns 0 for any index
it does not support rather than erroring or corrupting state.

Indices 8–11 are still device-variant specific in meaning:

| Index | Name | Applicable models |
|-------|------|------------------|
| 8 | `StateSprayCalibration` | non-Mera Comfort variants |
| 9 | `StateOrientationLight` | AcSela |
| 10 | `StateDraining` | AcCama / AcCamaTestset |
| 11 | `EndiannessCheck` | all |

| Model | iOS app indices | Notes |
|-------|----------------|-------|
| Mera Comfort | `[0,1,2,3,4,5,6,7,8,9,10,11]` | confirmed from nRF52840 capture 2026-06-26; 8–11 return 0 |
| **Mera Classic** | `[0,1,2,3,4,5,6,7,12,13]` | from OTA capture; unverified with nRF sniffer |
| **Sela** | `[0,1,2,3,4,5,6,7,9,12,13]` | adds index 9 (`StateOrientationLight`) |
| Cama | `[0,1,2,3,4,5,6,7,10,12,13]` | adds index 10 (`StateDraining`) |

Note: an earlier OTA capture (2026-06-01, `ble-protocol.md`) showed the iPhone sending
`[13,12,0,1,2,3,4,5,6,7]` to the same device. This conflicts with the nRF capture finding.
The nRF52840 capture is lower-level and more reliable; the OTA capture may have been misread
or truncated. Trust the nRF result.

### Bridge current state

The bridge uses one list for all AquaClean protocol devices: `[0,1,2,3,4,5,6,7,12,13]`.
This differs from the iOS app's `[0..11]` in two ways:
- Missing indices 8, 9, 10, 11 (device returns 0 for these on Mera Comfort — safe gap)
- Includes indices 12, 13 which the iOS app does not poll via SPL (it uses `GetStatisticsDescale` instead)

**For Sela, index 9 (`StateOrientationLight`) is missing** — the bridge does not read or
expose the live orientation light state for Sela devices.

---

## SetCommand codes (proc 0x09) — per model

| Code | Name | Mera Comfort | **Mera Classic** | **Sela** |
|------|------|:---:|:---:|:---:|
| 0 | ToggleAnalShower | ✅ | ✅ | ✅ |
| 1 | ToggleLadyShower | ✅ | ✅ | ✅ |
| 2 | ToggleDryer | ✅ | ✅ | ❌ no dryer |
| 3 | Stop | ✅ | ✅ | ✅ |
| 6 | PrepareDescaling | ✅ | ✅ | ✅ |
| 7 | ConfirmDescaling | ✅ | ✅ | ✅ |
| 8 | CancelDescaling | ✅ | ✅ | ✅ |
| 9 | PostponeDescaling | ✅ | ✅ | ✅ |
| 10 | ToggleLidPosition | ✅ | ✅? | ✅? |
| 12 | OdourExtraction | ✅ | ✅ | ❌ no odour extraction |
| 13 | OdourExtractionRunOn | ✅ | ✅ | ❌ |
| 20 | ToggleOrientationLight | ❌ use proc 0x0B | ❌ no hardware | ✅ **Sela only** |
| 37 | TriggerFlushManually | ✅ | ✅ | ✅ |
| 47 | ResetFilterCounter | ✅ | ✅ | ✅ |

`ToggleOrientationLight` (code 20): confirmed `SetIncludedDeviceTypes([AcSela])` in app
factory — does NOT work on any other model.

---

## Profile settings (proc 0x53 / 0x54 stored; proc 0x07 / 0x08 active)

| ID | Name | Mera Comfort | **Mera Classic** | **Sela** |
|----|------|:---:|:---:|:---:|
| 0 | OdourExtraction | ✅ | ✅ | ❌ no OE hardware |
| 1 | OscillatorState | ✅ | ✅ | ✅ |
| 2 | AnalShowerPressure | ✅ | ✅ | ✅ |
| 3 | LadyShowerPressure | ✅ | ✅ | ✅ |
| 4 | AnalShowerPosition | ✅ | ✅ | ✅ |
| 5 | LadyShowerPosition | ✅ | ✅ | ✅ |
| 6 | WaterTemperature | ✅ | ✅ | ✅ |
| 7 | WcSeatHeat | ✅ | ❌ no hardware | ❌ no hardware |
| 8 | DryerTemperature | ✅ | ✅ | ❌ no dryer |
| 9 | DryerState | ✅ | ✅ | ❌ no dryer |
| 10 | SystemFlush | ✅ | ✅ | ✅ |
| 11 | SeatHeating | ❌ Tuma Comfort only | ❌ | ❌ |
| 12 | WaterHeating | ✅ | ❌ no heater | ❌ |
| 13 | DryerFanPower | ✅ (fw ≥20) | ✅ (fw ≥20) | ❌ no dryer |
| 14 | LadyOscillation | ✅ | ✅ | ✅ |

---

## Common settings (proc 0x51 / 0x52 stored; proc 0x0A / 0x0B active)

| ID | Name | Mera Comfort | **Mera Classic** | **Sela** |
|----|------|:---:|:---:|:---:|
| 0 | WaterHardness | ✅ | ✅ | ✅ |
| 1 | OrientationLightBrightness | ✅ | ⚠️ register exists, no hardware | ✅ |
| 2 | OrientationLightColour | ✅ | ⚠️ register exists, no hardware | ✅ |
| 3 | OrientationLightMode | ✅ | ⚠️ register exists, no hardware | ✅ |
| 4 | LidSensorRange | ✅ | ❌ no sensor | ❌ no sensor |
| 5 | OdourExtractionRunOn | ✅ | ✅ | ❌ no OE hardware |
| 6 | LidAutoOpen | ✅ | ❌ no sensor | ❌ no sensor |
| 7 | LidAutoClose | ✅ | ❌ no sensor | ❌ no sensor |
| 8 | AutoFlush | ✅ | ✅ | ✅ |
| 9 | DemoMode | ✅ | ✅ | ✅ |
| 10 | LightSensorSensitivity | ❌ | ❌ | ✅ **AcSela only** |
| 11 | CareMode | ❌ Mera Floorstanding | ❌ | ❌ |
| 12 | Language | ✅ | ✅ | ✅ |

⚠️ Mera Classic: the firmware register for orientation light settings (IDs 1–3) exists in
the protocol spec ("all"), but the hardware is absent. The app does not expose these
settings for Mera Classic. Writing to them is harmless but has no physical effect.

---

## Filter status (proc 0x59 `GetFilterStatus`)

`GetFilterStatus` is available on all models. The ceramic honeycomb filter is present on
all AquaClean shower toilet models.

The bridge reads filter status identically on all models: IDs 0–6 (UsageShowers,
UsageDays, MaxShowers, MaxDays, ChangeRequired, ShowersSinceChange, DaysSinceChange).
IDs 7–10 are **device-specific** — do not probe them without model identification.

---

## Model identification via proc 0x82

The `description` field from `GetDeviceIdentification` (proc 0x82) contains the product
name string, e.g. `"AcMeraClassic"` or `"AcSela"`. This is the authoritative source for
model detection at runtime.

The bridge currently does not branch on device model — it sends the same SPL list and
exposes the same commands regardless of device type. For multi-model correctness, detect
the model from proc 0x82 at connect time and select the appropriate SPL list and feature
set.

---

## Bridge gaps for Mera Classic

The bridge works correctly out of the box for Mera Classic. No SPL changes needed.
Known limitations compared to Mera Comfort:

| Gap | Impact | Fix needed |
|-----|--------|------------|
| Bridge sends ToggleDryer command regardless of model | Dryer command sent to Mera Classic is harmless; Mera Classic has a dryer | None — dryer IS present |
| Bridge exposes WcSeatHeat in profile settings | Mera Classic returns 0; app hides this tab | Low priority — cosmetic only |
| Bridge exposes WaterHeating (ID 12) profile setting | Mera Classic has no water heater; value will be 0 | Low priority |
| Bridge exposes LidSensorRange/LidAutoOpen/Close common settings | Mera Classic has no lid sensor; reads return 0 | Low priority |

---

## Bridge gaps for Sela

Sela works with the bridge but several features are not exposed:

| Gap | Impact | Fix needed |
|-----|--------|------------|
| **SPL index 9 missing** | Orientation light live state not polled or broadcast | Add 9 to Sela SPL list |
| **ToggleOrientationLight not wired for Sela** | Bridge sends proc 0x0B (Mera Comfort path) instead of SetCommand 20 | Add model-specific dispatch |
| CommonSetting ID 10 (LightSensorSensitivity) not read | Proximity sensor range not exposed | Add to common settings read |
| Bridge exposes ToggleDryer command | Sela has no dryer — command will be ignored by device | Cosmetic; low priority |
| Bridge exposes OdourExtraction command | Sela has no OE — command ignored | Cosmetic; low priority |
| Bridge reads DryerTemperature/DryerState profile settings | Returns 0 on Sela | Low priority |
| Bridge reads OdourExtraction profile setting (ID 0) | Returns 0 on Sela | Low priority |
