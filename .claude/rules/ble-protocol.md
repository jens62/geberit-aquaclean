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

**Bridge current list**: `[0,1,2,3,4,5,6,7,12,13]` — 12 = UnpostedShowerCycles, 13 = DaysUntilNextDescale.
⚠️ **Mislabeling bug**: the bridge currently labels these as `LidOffsetPosition`/`ShowerArmOffsetPosition` — that is wrong.
See `docs/roadmap.md` → "Fix: SPL parameter mislabeling" for the fix TODO.
**iOS app sends**: `[0,1,2,3,4,5,6,7,8,9,10,11]` — confirmed from nRF52840 capture 2026-06-26
(HB2304EU298413, RS146.21 fw). Indices 8–11 return 0 on Mera Comfort; no `GetFilterStatus`
corruption observed. Earlier OTA capture (2026-06-01) showing `[13,12,0..7]` conflicts with this
and was likely misread or truncated — trust the nRF result.
Indices 12/13 are NOT in the iOS SPL list; iOS gets UnpostedShowerCycles/DaysUntilNextDescale
via `GetStatisticsDescale` instead.
The real LidOffset/ShowerArmOffset are at SPL indices 104/105 — queryability unconfirmed.

**Indices 8, 9, 10 are device-variant specific** (return 0 on Mera Comfort) but querying them
is safe — the earlier "permanently corrupts GetFilterStatus until power-cycle" claim is unverified
and contradicted by the 2026-06-26 nRF capture. Do not rely on that warning.

### SPL parameter index definitions

Indices 0–22 confirmed from iOS app v2.14.1 DpId.cs (formula: DpId = 65596 + index).
Firmware SPL dispatcher in node 0x01 handles switch cases 0–21 (0x00–0x15).
Indices 100+ follow the same DpId offset formula; queryability via GetSystemParameterList
unconfirmed for those — check v2.14.1 OTA capture.

| Index | Name | Notes |
|-------|------|-------|
| 0 | StateUserPresent | all |
| 1 | StateShowerAnal | all |
| 2 | StateShowerLady | all |
| 3 | StateDryer | all |
| 4 | StateDescaling | all |
| 5 | DurationDescaling | all |
| 6 | LastError | all |
| 7 | StateService | all |
| 8 | StateSprayCalibration | ⚠️ not for Mera Comfort — corrupts GetFilterStatus |
| 9 | StateOrientationLight | ⚠️ AcSela only — not for Mera Comfort |
| 10 | StateDraining | ⚠️ AcCama/AcCamaTestset only — not for Mera Comfort |
| 11 | EndiannessCheck | all (DpId 65607) |
| 12 | UnpostedShowerCycles | all (DpId 65608) — also via GetStatisticsDescale |
| 13 | DaysUntilNextDescale | all (DpId 65609) — also via GetStatisticsDescale |
| 14 | DaysUntilShowerRestricted | all (DpId 65610) — also via GetStatisticsDescale |
| 15 | ShowerCyclesUntilConfirmation | all (DpId 65611) — also via GetStatisticsDescale |
| 16 | TimestampAtLastDescale | all (DpId 65612) |
| 17 | TimestampAtLastDescalePrompt | all (DpId 65613) |
| 18 | NumberOfDescaleCycles | all (DpId 65614) — also via GetStatisticsDescale |
| 19 | DaysUntilNextFilterChange | all (DpId 65615) — also via GetFilterStatus |
| 20 | TimestampAtLastFilterChange | all (DpId 65616) |
| 21 | TimestampAtLastFilterChangePrompt | all (DpId 65617) |
| 22 | NumberOfFilterChanges | all (DpId 65618) — also via GetFilterStatus |
| 23 | LocalAppTime | write: app clock sync (DpId 65619) |
| 24–27 | LightDailyBlock1/2 Start/Stop | orientation light schedule; written by app (DpIds 65620–65623) |
| 28 | TimestampAtLastPowerdown | all (DpId 65624) |
| 31 | RealtimeClockUtcTime | all (DpId 65627) |
| 32–46 | ActiveProfileSettings 0–14 | mirror of proc 0x0A read values (DpIds 65628–65642) |
| 47–60 | ActiveCommonSettings 0–13 | mirror of proc 0x0A read values (DpIds 65643–65656) |
| 100 | ConnectedSsmDevices | AcSela fw≥4; AcMeraComfort fw≥23 (DpId 65696). Bitmask: bit0=FlushTrigger, bit1=OdourExtraction, bit2=OrientationLight |
| 104 | LidOffsetPosition | AcMeraComfort (DpId 65700) — queryability unconfirmed; check v2.14.1 OTA |
| 105 | ShowerArmOffsetPosition | AcMeraComfort (DpId 65701) — queryability unconfirmed |
| 106 | DryerArmOffsetPosition | AcMeraComfort (DpId 65702) — queryability unconfirmed |

### AC_ DpId namespace — mapping to SPL indices

The app uses two DpId namespaces: **`AC_`** for all AquaClean models (Mera Comfort, AcSela,
AcCama, …) and **`DP_`** for Alba/Ble20 devices. They do not overlap.

`AC_STATUS_*` DpIds follow `DpId = 65596 + SPL_index` for the contiguous block indices 0–22
(DpIds 65596–65618). At higher offsets the same formula gives SPL indices 100+ (e.g.
`AC_STATUS_CONNECTED_SSM_DEVICES = 65696` = index 100; `AC_STATUS_LID_OFFSET_POSITION = 65700`
= index 104). Whether those high indices are queryable via GetSystemParameterList is unconfirmed
— check v2.14.1 OTA capture. **`AC_` does not mean Mera Comfort** — it is the general
AquaClean protocol namespace.

**No motion detection in SPL namespace.** The Mera Comfort has a proximity sensor node
(hardware confirmed in RS30 firmware, node 0x0B). It drives lid mechanics via internal bus only.
No SPL parameter, GATT notification, or BLE advertisement bit for motion/approach state exists
on Mera Comfort. `AC_STATUS_USER_PRESENT` (SPL index 0) = seat sensor only.

**`AC_STATUS_ORIENTATION_LIGHT` (= 65605, SPL index 9):**
- AcSela only. Index 9 always returns 0 on HB2304EU298413 — orientation light state is
  invisible over BLE on Mera Comfort (confirmed from BLE log analysis).
- Not in `SPL_PARAMS_MERA_COMFORT` — intentionally excluded.
- **DO NOT probe index 9 on Mera Comfort** — same danger as indices 8 and 10:
  permanently corrupts `GetFilterStatus` state until power-cycle.

To probe on an **AcSela** (not Mera Comfort):
```bash
# GetSystemParameterList for index 9 only — AcSela only, DO NOT run on Mera Comfort
/Users/jens/venv/bin/python tools/geberit-ble-probe.py \
  --proc 0x0D \
  --args 01 09 00 00 00 00 00 00 00 00 00 00 00
```
Args format: `count(1 byte)` + `param indices` + `zero-pad to 13 bytes total`.
A non-zero response value = live orientation light state on AcSela.

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
- `0x07` — GetPerNodeProfileSetting (1-byte arg = node_id). During onboarding (Connection 1 button-detection cycle), the iOS app calls this for 10 nodes in order [04,02,05,03,09,01,00,0d,08,07]; device responds with InfoFrames on A5.
- `0x08` — SetActiveProfileSetting: format `[arg_count=3, setting_id, value]` (confirmed OTA 2026-06-01)
- `0x0E` — GetFirmwareVersionList: arg = list of component IDs (ints). iOS queries [1,3,4,5,6,7,8,9,10,11,12,14] then [15] during onboarding. Returns per-component version strings (RS/TS). Distinct from `0x0D` (GetSystemParameterList).
- `0x09` — SetCommand (toggle/trigger)
- `0x0A` / `0x0B` — `GetActiveCommonSetting` / `SetActiveCommonSetting` — confirmed from factory (RpcNumberGet=10, RpcNumberSet=11). Same setting ID space as 0x51/0x52. **Key difference: 0x0B applies immediately, no power cycle required.** iPhone uses these at init to restore orientation light settings (colour, brightness, mode). Bridge should use 0x0B to control orientation light at runtime.
- `0x0D` — GetSystemParameterList (batched state poll)
- `0x51` — GetStoredCommonSetting(id) → 2-byte int
- `0x53` / `0x54` — GetStoredProfileSetting / SetStoredProfileSetting
- `0x55` — `GetDeviceRegistrationLevel` (RpcNumberGet=85 in AcDataPointDefinitionFactory); response = 0/1/2 ("Not registered" / "Registered as private device" / "Registered as public device"). App reads this at init to customise UI — **not used by the toilet device itself**. Bridge does NOT need to call it.
- `0x56` — `SetDeviceRegistrationLevel` (RpcNumberSet=86); valid range 0–2 (the "value 257" in earlier notes was a misreading)
- `0x59` — GetFilterStatus. iOS onboarding queries this twice in sequence: first IDs [0–7] (returns empty — probe), then IDs [0–11] (returns days remaining, reset count, last reset date). Bridge uses IDs [0–7] only.
- `0x81` — GetSOCApplicationVersions
- `0x82` — GetDeviceIdentification
- `0x86` — GetDeviceInitialOperationDate

**Discrete DpId Procedure ID** (for BLE_COMMAND_REFERENCE.md DpIds directly): **UNKNOWN**.
Not observed in any log. To find: BLE-sniff the official Geberit Home app.

---

## Firmware update procedures (ctx=0x40)

Decoded from a genuine RS28.0→RS30.0 Mera Comfort update capture, 2026-07-14
(`local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/`,
full detail in `memory/mera-firmware-update-ble-protocol.md`). **Not a separate
proc namespace** — these proc codes collide numerically with the default-ctx
ones (0x52=SetStoredCommonSetting, 0x53=GetStoredProfileSetting under ctx=1);
`ctx` must be checked first, before dispatching on `proc` alone.

| ctx | proc | Meaning | Request→Response |
|-----|------|---------|-------------------|
| `0x40` | `0x00` | Background keepalive/telemetry, runs continuously (~2s interval) independent of update state | args=none → 12-byte payload, observed values fluctuate but don't appear to gate app progression |
| `0x00` | `0x01` | Companion heartbeat frame, always sent alongside `0x40/0x00` | args=none → empty (ACK-only) |
| `0x40` | `0x04` | Benign ping during the flash window; **the same code also finalizes the update** — after the device is in the "done" polling state, the next `0x40/0x04` is followed ~1s later by device silence (reboot) | args=none → empty (ACK-only); no distinct "finalize" proc code exists, behavior is state-dependent |
| `0x40` | `0x52` | `StartFirmwareUpdate` — sent once, when the user taps "Update Now" in the app (the `0x40/0x00`/`0x00/0x01` background poll runs for 30+ seconds beforehand without blocking this) | args=none → empty (ACK-only) |
| `0x40` | `0x53` | Poll update progress | args=none → 1 byte: `0x05`=busy, `0x06`=done. Polled every ~1.8s for the whole flash window (~164s in the real capture) |

**Bulk firmware transfer**: during the flash window, the app also writes
~290KB of raw firmware binary (not proc-framed) directly on the same A1–A4
write characteristics used for normal proc requests, split across three of
the four channels. This is **not gated by any proc response** — the device
signals completion purely via the `0x40/0x53` poll flipping to `0x06`, not by
counting bytes. The write channels must tolerate this arbitrary binary data
without crashing; garbled `ctx`/`proc` bytes parsed from it are harmless noise
(spurious "unknown proc" responses the app isn't waiting on).

**Reboot**: last thing sent before the device goes silent is `0x40/0x04`.
Real device: ~13.3s fully silent (no advertising), then a few seconds of
corrupted advertising data before stabilizing and getting a fresh `CONNECT_IND`
(~19s total). No general-purpose restart command exists for Mera (unlike
Alba's `DP_RESTART`) — this finalize side effect is the only BLE-triggerable
reboot ever observed.

**Mock implementation** (`mera_mock.py`, Phase 9b): simplified state machine
(`idle → started → done → rebooting → idle`) driven by timers rather than by
inspecting the bulk transfer or emitting progress-notify frames on A5 — see
`docs/developer/mock-service-requirements.md` Phase 9b for the deferred
byte-exact items.

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

**Mock reverted 2026-07-18, same day as the correction below** — attempting to replicate the
two-packet split in `_MeraAdvertisement` (via a `manufacturerData` dict with two company-ID
keys) resulted in BlueZ putting both entries in ADV_IND instead of splitting them ADV_IND/
SCAN_RSP as the real device does — a packet shape never sent before, and onboarding failed
completely (zero BLE connections) immediately after. Reverted to one combined 11-byte entry.
The real-device facts below are still correct and confirmed — only the mock's ability to
replicate the split is unresolved (bluez_peripheral/BlueZ gives no control over which PDU an
entry lands in). See `docs/developer/nrf-ble-analyze-completeness-audit.md` for the audit that
led here, and `docs/developer/mera-home-app-onboarding.md` for the full revert note.

**Corrected 2026-07-18** (was a single "11-byte payload" model — wrong; see
`docs/developer/mera-home-app-onboarding.md` for the full byte-level evidence). The real
device splits this across TWO packets, not one AD structure, and `IsEmergencyConnectPermitted`
is encoded in the advertised COMPANY ID itself, not a separate payload byte:

**ADV_IND** — one `0xFF` Manufacturer-Specific-Data entry, company `0x0100` normally /
`0x01AA` when `IsEmergencyConnectPermitted`, 6-byte data payload:

| Payload offset | Content |
|--------|---------|
| 0 | State byte B — `0x01` means `IsButtonPressed` (iOS/Android key onboarding selection off this) |
| 1–5 | Article number characters (e.g. `"14621"`) |

Confirmed live (nRF Connect, 2026-07-18): idle = company `0x0100`, data `00 31 34 36 32 31`;
button pressed = company `0x01AA`, data `01 31 34 36 32 31` — both flags flip together on a
real button press.

**SCAN_RSP** — separate `0x09` Complete Local Name (`"Geberit AC PRO"`) plus a second, distinct
`0xFF` entry: 3 raw bytes `[0x00, rs_char1, rs_char2]` — the RS firmware major-version prefix
(e.g. `00 33 30` = "30" for RS30.0). A scanner that merges ADV_IND+SCAN_RSP into one device
view (nRF Connect, apparently the app too) shows this as if it were a second "Manufacturer
data" block on the same packet — it isn't; it's a separate PDU.

`SensorState` = 2 bits: `IsButtonPressed` (bit 0, ADV_IND payload) + `IsEmergencyConnectPermitted`
(bit 1, ADV_IND company-ID low byte). **No proximity or approach detection bit.** Hardware
nodes `0x0A`/`0x0B` drive lid opening locally — no BLE event is emitted, no GATT characteristic
changes.
`AC_STATUS_USER_PRESENT` (SPL index 0) = seat detection only, not approach detection.

---

## `BLE_COMMAND_REFERENCE.md`

Located at `operation_support/BLE_COMMAND_REFERENCE.md`. Verified against `DpId.cs` source.
Use to understand WHAT the device supports conceptually. Do NOT map its DpIds directly to
Commands enum codes — different numbering systems.
For 90% of useful functionality, Commands.py + ProfileSettings.py are sufficient.
For the remaining 10% (water hardness, error status, etc.), BLE sniffing is required.
