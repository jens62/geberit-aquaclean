# BLE Protocol Reference

## Two-layer protocol

The code uses documented C# enum codes, NOT the DpIds from `BLE_COMMAND_REFERENCE.md`.
DpIds (e.g. 563 = anal shower) are Geberit device-level data point IDs — conceptual
reference only, not directly callable from the code.

---

## Layer 1 — `SetCommandAsync(Commands.X)` (`aquaclean_core/Clients/Commands.py`)

Sends Procedure=0x09 with a 1-byte command code. All are toggles/triggers:

| Command | Code | Wired? |
|---|---|---|
| `ToggleAnalShower` | 0 | ✅ all interfaces |
| `ToggleLadyShower` | 1 | ✅ all interfaces |
| `ToggleDryer` | 2 | ✅ all interfaces |
| `Stop` | 3 | ❌ not yet wired (confirmed AC_CMD_STOP from OTA capture 2026-06-01) |
| `StartCleaningDevice` | 4 | ❌ |
| `ExecuteNextCleaningStep` | 5 | ❌ |
| `PrepareDescaling` | 6 | ✅ all interfaces |
| `ConfirmDescaling` | 7 | ✅ all interfaces |
| `CancelDescaling` | 8 | ✅ all interfaces |
| `PostponeDescaling` | 9 | ✅ all interfaces |
| `ToggleLidPosition` | 10 | ✅ all interfaces |
| `OdourExtraction` | 12 | ❌ |
| `OdourExtractionRunOn` | 13 | ❌ |
| `ToggleOrientationLight` | 20 | ⚠️ REST only (no MQTT, no HACS, not in CLI) |
| `StartLidPositionCalibration` | 33 | ❌ |
| `LidPositionOffsetSave` | 34 | ❌ |
| `LidPositionOffsetIncrement` | 35 | ❌ |
| `LidPositionOffsetDecrement` | 36 | ❌ |
| `TriggerFlushManually` | 37 | ✅ all interfaces |
| `ResetFilterCounter` | 47 | ✅ all interfaces |
| `ShowerArmOffsetSave` | 47? | ⚠️ code 47 conflict with ResetFilterCounter — verify before adding |
| `ShowerArmOffsetStart` | 46 | ❌ |
| `ShowerArmOffsetIncrement` | 48 | ❌ |
| `ShowerArmOffsetDecrement` | 49 | ❌ |
| `DryerArmOffsetStart` | 50 | ❌ |
| `DryerArmOffsetSave` | 51 | ❌ |
| `DryerArmOffsetIncrement` | 52 | ❌ |
| `DryerArmOffsetDecrement` | 53 | ❌ |
| `Draining` | 54 | ❌ AcCama only |
| `ResetStatistics` | 78 | ❌ |

**New finding (2026-04-17):** `ToggleAnalShower` and `ToggleLadyShower` work correctly
only when `userSitting == True`. The device only accepts shower commands while someone is seated.

**Quick-win commands**: all unexposed Commands enum entries just need REST endpoints + web UI
wiring — `SetCommandAsync(Commands.X)` already handles all of them, zero new protocol code.

---

## Layer 2 — `GetStoredProfileSettingAsync` / `SetStoredProfileSettingAsync`

(`aquaclean_core/Clients/ProfileSettings.py`) Reads/writes stored user settings by index:

| Setting | Index | Getter | Setter |
|---|---|---|---|
| `OdourExtraction` | 0 | ✅ | ✅ |
| `OscillatorState` | 1 | ✅ | ❌ |
| `AnalShowerPressure` | 2 | ✅ | ❌ |
| `LadyShowerPressure` | 3 | ❌ | ❌ |
| `AnalShowerPosition` | 4 | ✅ | ❌ |
| `LadyShowerPosition` | 5 | ✅ | ❌ |
| `WaterTemperature` | 6 | ✅ | ❌ |
| `WcSeatHeat` | 7 | ✅ | ❌ |
| `DryerTemperature` | 8 | ✅ | ❌ |
| `DryerState` | 9 | ✅ | ❌ |
| `SystemFlush` | 10 | ✅ | ❌ |
| `SeatHeating` | 11 | ❌ | ❌ | Tuma Comfort only |
| `WaterHeating` | 12 | ❌ | ❌ | Mera/Tuma Comfort |
| `DryerFanPower` | 13 | ❌ | ❌ | 5 levels; confirmed nRF52840 capture t=108.3s, value=1 |
| `LadyOscillation` | 14 | ❌ | ❌ | Stored profile only |

CallClasses `0x53` / `0x54` are already migrated but not yet wired into any interface.

---

## Layer 3 — `GetSystemParameterList` (SPL)

Reads live device state. **NOT** DpIds — separate index space.
`SPL_PARAMS_MERA_COMFORT` in `AquaCleanClient.py` defines the list sent.

**Current list**: `[0,1,2,3,4,5,6,7]` — safe for all standard variants.
**iPhone sends**: `[13, 12, 0, 1, 2, 3, 4, 5, 6, 7]` (confirmed OTA capture 2026-06-01, HB2304EU298413 fw RS146.21).
Params 12 and 13 are safe on Mera Comfort firmware ≥ RS25 (LidOffsetPosition, ShowerArmOffsetPosition).

**DANGER**: indices 8, 9, 10 are device-variant specific — sending them to a Mera Comfort
permanently corrupts `GetFilterStatus` state until power-cycle. Do NOT add 8/9/10 to Mera Comfort list.

### SPL parameter index definitions

| Index | Name | Device restriction |
|-------|------|--------------------|
| 0 | StateUserPresent | all |
| 1 | StateShowerAnal | all |
| 2 | StateShowerLady | all |
| 3 | StateDryer | all |
| 4 | StateDescaling | all |
| 5 | DurationDescaling | all |
| 6 | LastError | all |
| 7 | StateService | all |
| 8 | StateSprayCalibration | restricted — not for Mera Comfort |
| 9 | StateOrientationLight | AcSela only |
| 10 | StateDraining | AcCama/AcCamaTestset only |
| 11 | ConnectedSsmDevices | AcSela only |
| 12 | LidOffsetPosition | AcMeraComfort, firmware ≥ RS25 — safe |
| 13 | ShowerArmOffsetPosition | AcMeraComfort — safe |
| 14 | DryerArmOffsetPosition | — |
| 255 | EndiannessCheck | — |

### SPL parameter semantics — label corrections

Some code comment labels are misleading:
- **Index 0** — `StateUserPresent` — seat sensor ✅ correct
- **Index 1** — labelled `analShowerIsRunning` in code — actually tracks **user sitting**
- **Index 3** — labelled `dryerIsRunning` — actually tracks **anal shower running**
- Dryer state: not visible in any captured SPL change — possibly different index or polled differently

### SPL anal-shower packed field (Index 1)

The anal-shower SPL parameter is a packed uint32, not a simple boolean:
| Value | Hex | Condition |
|-------|-----|-----------|
| 0 | `0x00000000` | shower not running |
| 1281 | `0x00000501` | shower running, temperature = 1 |
| 1280 | `0x00000500` | shower running, temperature = 0 |

Hypothesis (little-endian): byte 0 = water temperature (0–5), byte 1 = pressure (~0x05 while running).
The current `!= 0` check for "shower running" is correct, but temperature/pressure/position bytes are discarded.

---

## Confirmed procedure codes

From `aquaclean-SILLY.log`:
- `0x06` — GetActualOutletTemperature (not yet implemented in bridge)
- `0x07` — per-node profile setting query (1-byte arg = node_id)
- `0x08` — SetActiveProfileSetting: format `[arg_count=3, setting_id, value]` (confirmed OTA 2026-06-01)
- `0x09` — SetCommand (toggle/trigger)
- `0x0A` / `0x0B` — `GetActiveCommonSetting` / `SetActiveCommonSetting` — confirmed from factory (RpcNumberGet=10, RpcNumberSet=11). Same setting ID space as 0x51/0x52. **Key difference: 0x0B applies immediately, no power cycle required.** iPhone uses these at init to restore orientation light settings (colour, brightness, mode). Bridge should use 0x0B to control orientation light at runtime.
- `0x0D` — GetSystemParameterList (batched state poll)
- `0x51` — GetStoredCommonSetting(id) → 2-byte int
- `0x53` / `0x54` — GetStoredProfileSetting / SetStoredProfileSetting
- `0x55` — `GetDeviceRegistrationLevel` (RpcNumberGet=85 in AcDataPointDefinitionFactory); response = 0/1/2 ("Not registered" / "Registered as private device" / "Registered as public device"). App reads this at init to customise UI — **not used by the toilet device itself**. Bridge does NOT need to call it.
- `0x56` — `SetDeviceRegistrationLevel` (RpcNumberSet=86); valid range 0–2 (the "value 257" in earlier notes was a misreading)
- `0x59` — GetFilterStatus
- `0x81` — GetSOCApplicationVersions
- `0x82` — GetDeviceIdentification
- `0x86` — GetDeviceInitialOperationDate

**Discrete DpId Procedure ID** (for BLE_COMMAND_REFERENCE.md DpIds directly): **UNKNOWN**.
Not observed in any log. To find: BLE-sniff the official Geberit Home app.

---

## CommonSetting IDs (proc 0x51 / 0x52)

| ID | Correct name | Device restriction |
|----|-------------|-------------------|
| 0 | `WaterHardness` | all |
| 1 | `OrientationLightBrightness` | all |
| 2 | `OrientationLightColour` | all |
| 3 | `OrientationLightMode` | all |
| 4 | `LidSensorRange` | Mera Comfort |
| 5 | `OdourExtractionRunOn` | all |
| 6 | `LidAutoOpen` | Mera Comfort |
| 7 | `LidAutoClose` | Mera Comfort |
| 8 | `AutoFlush` | all |
| 9 | `DemoMode` | all |
| 10 | `LightSensorSensitivity` | AcSela only |
| 11 | `CareMode` | Mera Floorstanding |
| 12 | `Language` | all |

**Active vs Stored — two separate proc pairs:**
- **Stored** (proc 0x51/0x52): writes to NVM, requires power-cycle to take effect (confirmed Geberit Support case CAS1550064K3D1Z). Bridge currently uses these.
- **Active** (proc 0x0A/0x0B): applies immediately at runtime, no power cycle. iPhone uses 0x0B at every session init to restore orientation light settings.

**To turn the orientation light off immediately:** write proc 0x0B, ID=3 (OrientationLightMode), value=0 ("Off"). **CONFIRMED LIVE 2026-06-04** on HB2304EU298413 — light turns off within ~1s. value=1=On, value=2=WhenApproached. Write response=(none). Stored setting (proc 0x51) stays unchanged.

**ToggleOrientationLight (SetCommand code 20): AcSela ONLY.** Confirmed from factory `SetIncludedDeviceTypes([AcSela])`. Does NOT work on Mera Comfort.

**Color values** (CommonSetting ID 2, confirmed from factory v2.14.1 + BLE log):
`0=Blue  1=Turquoise  2=Magenta  3=Orange  4=Yellow  5=WarmWhite  6=ColdWhite`

**Mode values** (CommonSetting ID 3): `0=Off  1=On  2=WhenApproached`

---

## BLE advertising payload — SensorState

The device broadcasts BLE advertisements while idle. Manufacturer-specific payload:

| Offset | Content |
|--------|---------|
| 0 | State byte A — `0xAA` means `IsEmergencyConnectPermitted` |
| 1 | firmware version chars |
| 2 | State byte B — `0x01` means `IsButtonPressed` |
| 3–7 | Article number characters |
| 8–10 | RS firmware number chars (11-byte variant only) |

`SensorState` = 2 bits: `IsButtonPressed` (bit 0) + `IsEmergencyConnectPermitted` (bit 1).
**No proximity or approach detection bit.** Hardware nodes `0x0A`/`0x0B` drive lid opening
locally — no BLE event is emitted, no GATT characteristic changes.
`AC_STATUS_USER_PRESENT` (SPL index 0) = seat detection only, not approach detection.

---

## `BLE_COMMAND_REFERENCE.md`

Located at `operation_support/BLE_COMMAND_REFERENCE.md`. Verified against `DpId.cs` source.
Use to understand WHAT the device supports conceptually. Do NOT map its DpIds directly to
Commands enum codes — different numbering systems.
For 90% of useful functionality, Commands.py + ProfileSettings.py are sufficient.
For the remaining 10% (water hardness, error status, etc.), BLE sniffing is required.
