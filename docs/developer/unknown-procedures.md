# Geberit AquaClean — Unknown / Unresolved BLE Protocol Items

This document consolidates everything that has been observed in iPhone BLE traffic
logs but whose purpose or mapping is not yet fully understood.

Use this as a research backlog: each item has a suggested investigation approach.

Last updated: 2026-04-22

---

## 1. Unknown Procedure Codes

### Proc `0x05` (ctx=0x01) — identified as `GetNodeList`

| Field | Value |
|-------|-------|
| Label | `GetNodeList` |
| Direction | Request (no args), response = `NodeList` struct |
| Response format | 1-byte `A` (count) + 128-byte `B` (node ID array, first `A` bytes meaningful) |
| Seen in | BLE traffic logs; confirmed in thomas-bingel C# repo (`tmp.txt`) |
| Status | **Name confirmed. Response format confirmed (2026-04-17). Node semantics unknown.** |

**Source:** `aquaclean-core/Api/CallClasses/tmp.txt` — `GetNodeList()` returns `NodeList`.
`NodeList` struct (`aquaclean-core/Api/CallClasses/Dtos/NodeList.cs`):

```csharp
public struct NodeList {
    [DeSerialize(Length = 1)]   public int A { get; set; }
    [DeSerialize(Length = 128)] public byte[] B { get; set; }
}
```

**Confirmed result (2026-04-17, device HB2304EU298413, via `geberit-ble-probe.py --proc 0x05 --ctx 0x01`):**

```
A = 12
B (first 12 bytes) = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14, 15]
B (remaining 116 bytes) = all zero
```

- `A=12` → the device has 12 registered nodes
- `B` is a **node ID array**, not a bitmask — first `A` bytes are meaningful, rest are zero-padded
- Node IDs present: `0x03–0x0C, 0x0E, 0x0F`
- **Node `0x0D` (13) is absent** from the list, while all adjacent IDs are present
- Node IDs `0x01` and `0x02` are also absent (likely reserved)

**What the node IDs likely represent:** logical subsystems or firmware modules (e.g. main controller,
pump, lid mechanism, descaling unit, orientation light). Each has its own node ID for the internal
communication bus. Exact mapping of ID → component is unknown.

**Notable:** `0x0D` is also the procedure code for `GetSystemParameterList`. Whether the absent
node 13 is related to GetSPL behavior is unknown — the procedure code and node ID namespaces are
almost certainly separate.

**How to investigate further:** BLE-sniff a session with a different hardware variant to see whether
the node list changes (different hardware = different modules present).

---

### Proc `0x07` (ctx=0x01)

| Field | Value |
|-------|-------|
| Label | `UnknownProc_0x07` |
| Direction | unknown |
| Payload | Request args: `0x0a` (1 byte); response unknown |
| Seen in | Android pcapng `open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng` (2026-04-22) — single occurrence during init sequence |
| Status | **Completely unknown** |

**How to investigate:** Use `geberit-ble-probe.py --proc 0x07 --ctx 0x01 --args 0a` and inspect
the response. Cross-reference with the GetNodeList node IDs (0x03–0x0C, 0x0E, 0x0F) — arg `0x0a`
is node ID 10, possibly a per-node query.

---

### Proc `0x0E` (ctx=0x01) — unknown init-phase procedure

| Field | Value |
|-------|-------|
| Label | `UnknownProc_0x0E` |
| Direction | Request with param list, response unknown |
| Payload | SPL-style param ID list — called twice: `[1,3,4,5,6,7,8,9,10,11,12,14]` then `[15]` |
| Seen in | Android pcapng `open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng` (2026-04-21) |
| Calling context | Init sequence, after `GetSOCApplicationVersions`, before `GetFirmwareVersionList` |
| Status | **Completely unknown — first observation** |

**What is known:**
- Called exactly twice per session in the Android app's init sequence
- The param ID format mirrors `GetSystemParameterList` (0x0D): a list of integer IDs
- First call: 12 IDs — `[1,3,4,5,6,7,8,9,10,11,12,14]`
- Second call: 1 ID — `[15]`
- The IDs do NOT overlap with the SPL param space (which maxes out at 11 on this device);
  they may address a separate index space

**Hypothesis:** Could be `GetNodeParameterList` (some per-node status query), a firmware
module status request, or an extended hardware capability query. The param IDs resemble
node IDs (from GetNodeList: `[3,4,5,6,7,8,9,10,11,12,14,15]`) with minor offset.

**How to investigate:** Use `geberit-ble-probe.py --proc 0x0E --ctx 0x01` with the
same param IDs and inspect the response bytes. Cross-reference param values against
the GetNodeList node IDs.

---

### Proc `0x11` (ctx=0x01) — confirmed as `GetFirmwareVersionList`

| Field | Value |
|-------|-------|
| Label | `GetFirmwareVersionList` |
| Direction | Request (param list), response = firmware version strings |
| Payload | SPL-style param list; Android app calls with `[3,4,5,6,7,8,9,10,11,12,14,15]` (×4) |
| Seen in | Android pcapng `open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng` (2026-04-21) |
| Calling context | Init sequence (called 4 times), between `GetSOCApplicationVersions` and `GetStoredProfileSetting` |
| Status | **CONFIRMED — name and response content verified from ASCII strings in response** |

**Evidence from pcapng:** Response to the GetFirmwareVersionList calls contained readable
ASCII fragments including firmware component version strings. This confirms the procedure
identity beyond doubt.

**Already implemented in bridge:** `GetFirmwareVersionList` is implemented and callable.
The Android app calls it 4 times with 12 param IDs each (`[3,4,5,6,7,8,9,10,11,12,14,15]`).
The bridge uses fewer params; the exact parameter space is the 12 node IDs from `GetNodeList`.

**Notable:** The Android init sequence calls this before any `GetStoredProfileSetting` reads —
earlier in the init than previously assumed from iPhone log analysis.

---

### Proc `0x08` (ctx=0x01) — `SetActiveProfileSetting` (observed in Android)

| Field | Value |
|-------|-------|
| Label | `SetActiveProfileSetting` |
| Direction | Write |
| Args | `[setting_id, value_lo, value_hi]` — 3 bytes, same format as proc 0x0B |
| Seen in | Android pcapng `open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng` (2026-04-22); thomas-bingel C# repo (`tmp.txt`) |
| Status | **Observed and partially decoded. Same setting ID space as proc 0x53/0x54.** |

**Source:** `aquaclean-core/Api/CallClasses/tmp.txt` — listed as `SetActiveProfileSetting(profileSettingId, settingValue)`.

**Observed in Android pcapng:** Sent in 4 groups interleaved with GetSPL polls after the user
selected settings mid-session. Decoded args (format: `[setting_id, value_lo, value_hi]`):

| Args (hex) | Setting ID | Name | Value |
|------------|-----------|------|-------|
| `040200` | 4 | AnalShowerPosition | 2 |
| `020300` | 2 | AnalShowerPressure | 3 |
| `060300` | 6 | WaterTemperature | 3 |
| `050300` | 5 | LadyShowerPosition | 3 |
| `030000` | 3 | LadyShowerPressure | 0 |
| `090100` | 9 | DryerState | 1 |
| `010100` | 1 | OscillatorState | 1 |
| `000100` | 0 | OdourExtraction | 1 |
| `0d0000` | 13 | DryerSprayIntensity | 0 |
| `080300` | 8 | DryerTemperature | 3 |
| `070000` | 7 | WcSeatHeat | 0 |

**Confirmed:** Proc 0x08 uses the **same setting ID space** as proc 0x53/0x54 (GetStoredProfileSetting / SetStoredProfileSetting).

**What distinguishes 0x08 from 0x54:** 0x08 likely applies settings live (in-session, "active"),
while 0x54 persists them to device flash (stored profile). This is the "active" vs "stored"
distinction described in `tmp.txt`.

---

### Proc `0x55` (ctx=0x01) — partially understood

| Field | Value |
|-------|-------|
| Label | *(unnamed — decoded but purpose unknown)* |
| Direction | Request `[0x01]` (1 byte), response `0x00` |
| Seen in | `Connect-Toggle-Lid-shutdown-app.txt`, Stuhlgang log |
| Calling context | Sent **once per session**, at the **end of the init sequence** — after all `GetStoredCommonSetting` (0x51) reads, before first user action |
| Status | Purpose **unknown** — candidates: "session ready", "enable remote control" |

**What is NOT the case:**
- NOT approach-triggered (present in `Connect-Toggle-Lid` log where no approach occurred)
- NOT the reason the lid opened in the Stuhlgang log (that was the device's own proximity sensor)

**How to investigate:** BLE-sniff a session where proc 0x55 is deliberately skipped
(e.g. using the bridge, which does not send it). If the device still responds to
commands, it is not required. If commands stop working, it is likely a "remote control
enable" handshake.

---

### Proc `0x56` (ctx=0x01) — named but purpose unclear

| Field | Value |
|-------|-------|
| Label | `SetDeviceRegistrationLevel` (from thomas-bingel C# repo, `tmp.txt`) |
| Direction | Write |
| Payload | value `257` (= `0x01 0x01` little-endian) mentioned in `tmp.txt` |
| Seen in | C# source reference only — not yet observed in any iPhone BLE log |
| Status | Name known, purpose **unknown** |

**How to investigate:** Sniff an iPhone session and search for proc 0x56 in the
decoded output. Check whether it appears during device pairing/first-setup or only
in specific scenarios.

---

### Proc `0x0A` / `0x0B` — init sequence, different storage area

| Field | Value |
|-------|-------|
| `0x0A` | `GetStoredProfileSetting` (iPhone init variant) — payload `[setting_id]` 1 byte |
| `0x0B` | `SetStoredProfileSetting` (iPhone init variant) — payload `[setting_id, val_lo, val_hi]` 3 bytes |
| Seen in | Every iPhone session, in the init sequence immediately after Proc_0x13 |
| Status | Observed and decoded, but the **storage area** they address is different from proc 0x53/0x54 |

**What is known:** These are NOT the procs to use for reading/writing user preferences.
Proc 0x0A always returns `0` for LadyShowerPosition even when the app shows a non-zero
value. See `memory/ble-procedure-investigation-method.md` for the full 0x0A vs 0x53 lesson.

**Remaining question:** What storage area do 0x0A/0x0B address, and what are those
values used for? They form the init handshake but the semantic meaning of the data
is unknown.

#### Proc 0x0B session-claim hypothesis (2026-04-16) — **DISPROVEN**

**Observation:** BLE log analysis of 4 capture sessions (`1_Neuanmeldung nach blocking
state 1.txt` through `4_dritte anmedlung.txt`) shows the iPhone sends exactly these
three 0x0B writes on every connect, immediately after the 4×0x13 subscription sequence:

```
args=020200  →  AnalShowerPressure = 2  (setting_id=2, value=2)
args=010200  →  OscillatorState    = 2  (setting_id=1, value=2)
args=030200  →  LadyShowerPressure = 2  (setting_id=3, value=2)
```

The value is always `2` for all three regardless of the user's actual shower settings.
This is not preference restoration — the real settings arrive later via proc 0x53/0x54.

**Hypothesis:** These writes "claim" the application session — telling the device that
a new client has taken over — and may clear stale state left by a previous client. If
correct, adding them to the bridge's init sequence could allow recovery from E0003
(device visible but no response) without a power cycle.

**Validation (2026-04-16, commit 0bce5a2):**
1. Baseline confirmed (commit 4691631): 4×0x11 + 4×0x13 alone does NOT recover E0003
2. 3×0x0B writes implemented and tested in two modes:
   - **bleak (local BLE):** E0003 persists — GetSystemParameterList timed out (failure #1)
   - **ESP32 proxy:** E0003 persists — GetSystemParameterList timed out (failures #1 and #2)
3. **Result: DISPROVEN.** The 3×0x0B writes have no effect on E0003 recovery.

**Not implemented:** Reverted after disproof. E0003 root cause remains unknown.
The device requires a power cycle to recover from this state.

---

### `SetCommand` payload code `3` (proc `0x09`, args=`[0x03]`)

| Field | Value |
|-------|-------|
| Label | Unknown |
| Mechanism | Proc `0x09` (SetCommand), single-byte payload `0x03` |
| Seen in | Android pcapng `open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng` (2026-04-22) |
| Status | **Not in `Commands` enum — unknown command code** |

**Observation:** In a session titled "open lid remotely, walk to toilet, sit, stand up", the Android
app sends `SetCommand(0x03)` immediately before `SetCommand(ToggleLidPosition)`. Code 3 is a gap in
our `Commands` enum between `ToggleDryer (2)` and `StartCleaningDevice (4)`.

**Current `Commands` enum codes for reference:**
`0=ToggleAnalShower, 1=ToggleLadyShower, 2=ToggleDryer, [3=?], 4=StartCleaningDevice,
5=ExecuteNextCleaningStep, 6=PrepareDescaling, 7=ConfirmDescaling, 8=CancelDescaling,
9=PostponeDescaling, 10=ToggleLidPosition, 20=ToggleOrientationLight, 33–36=LidCalibration,
37=TriggerFlushManually, 47=ResetFilterCounter`

**Hypothesis:** Code 3 may be a dedicated "OpenLid" command (as opposed to the toggle in code 10).
In the capture, the app opens the lid remotely, waits ~3.5 s, then sends ToggleLidPosition — possibly
to close it after the user sat down.

**How to investigate:** Use `geberit-ble-probe.py --proc 0x09 --ctx 0x01 --args 03` and observe
whether the device responds with a lid action. Alternatively, sniff the app while tapping "open lid"
and "close lid" separately (if those are distinct UI buttons) and compare which SetCommand code is sent.

---

## 2. Unknown Common Setting IDs (proc 0x51/0x52)

The iPhone reads IDs `[2, 1, 3, 0]` on every connect. IDs 4, 6, 7 were confirmed
from the WC Lid BLE log (2026-04-15).

**Update 2026-04-21 (Android pcapng):** The Android app reads **all IDs 0–9** in
a sequential scan during every init. This confirms that IDs 0–9 all exist on this
device. Semantics of IDs 4–9 remain unknown (responses not yet decoded from pcapng).

| ID | Candidate | Range | Status |
|----|-----------|-------|--------|
| 0 | OdourRunOn | 0–1 | ✓ confirmed |
| 1 | Brightness | 0–5 | ✓ confirmed |
| 2 | Activation mode | 0=On / 1=Off / 2=When Approached | ✓ confirmed |
| 3 | Color | 1=Blue / 2=Magenta / … | ✓ confirmed (partial color mapping) |
| 4 | Unknown | — | **Observed** (Android app reads it, value unknown) |
| 5 | Maximum Lid Position | float/int | **Observed** (Android reads it; "Maximaldeckelposition" in `Profile-Settings.xlsx`) |
| 6 | Unknown | — | **Observed** (Android app reads it; also seen in WC Lid log) |
| 7 | Unknown | — | **Observed** (Android app reads it; also seen in WC Lid log) |
| 8 | Unknown | — | **Observed** (Android app reads it; value `0` in pcapng 2026-04-22) |
| 9 | Unknown | — | **Observed** (Android app reads it; value `0` in pcapng 2026-04-22) |

**How to investigate:**
- **ID 5 (Max Lid Position):** Sniff iPhone while using "Maximaldeckelposition" calibration
  in the app's WC lid settings.
- **IDs 4, 8–9:** Decode the full GetStoredCommonSetting response sequence from the Android
  pcapng (`open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng`) using
  `tools/android-ble-analyze.py --geberit --verbose`. The raw response bytes for each ID
  are in the capture.

---

## 3. Unknown Profile Setting IDs (proc 0x53/0x54)

Confirmed IDs: 0–9 and 13. IDs **10, 11, 12** fall between `DryerState`(9) and
`DryerSprayIntensity`(13) and have never been observed in any BLE log.

| ID | Candidate | Status |
|----|-----------|--------|
| 10 | `SystemFlush` (from AquaCleanClient source) | Getter exists in code, meaning unclear |
| 11 | Unknown | Never observed |
| 12 | Unknown | Never observed |

**How to investigate:** Sniff iPhone while exploring all dryer and flush-related settings.

---

## 4. GetSystemParameterList — Confirmed and Unknown Indices

The bridge/spl-monitor polls the iPhone's 12-param list `[0, 1, 2, 3, 4, 5, 6, 7, 4, 8, 9, 10]`
(single call; index 4 duplicated exactly as observed in iPhone BLE traffic). Updated 2026-04-17
after the CONS frame bug fix enabled >8 params. Prior 8-param limit was NOT a device constraint —
see `memory/cons-frame-zero-padding-bug.md` for details.

### Confirmed semantics (BLE log 2026-04-15 + spl-monitor 2026-04-17)

| Index | C# label | Confirmed semantics | Notes |
|-------|----------|---------------------|-------|
| 0 | `userIsSitting` | **Approach/presence detection** — changes when user enters toilet proximity | ✓ confirmed; value=1 observed with user seated (2026-04-17) |
| 2 | `ladyShowerIsRunning` | **Lady shower running** | ✓ confirmed (04-17 session: changed when lady shower started) |
| 3 | `dryerIsRunning` | **Anal shower running** — C# label is WRONG | ✓ confirmed (04-15 and 04-17: changes when anal shower runs) |
| 4 | `descalingState` | Descaling state | from C# |
| 5 | `descalingDurationInMinutes` | Descaling duration | from C# |
| 6 | `lastErrorCode` | Last error code | from C# |

### Unconfirmed / unknown

| Index | C# label | Observed | Status |
|-------|----------|----------|--------|
| 1 | `analShowerIsRunning` | **Always 0** in all monitored sessions including with user seated and showers running | **Unknown** — semantics not confirmed. Possibly WC seat sensor (weight-triggered?) vs index 0's proximity sensor. Dryer running state is NOT reported by any currently polled index. |
| 7 | *(not in C# reference)* | **Always 0 on HB2304EU298413** — device supports the parameter (idx_echo correct) but value never changes | **Unknown** — supported by this hardware but semantics unknown. Previously removed from bridge polling; the "9th param causes stuck-state failure" was caused by the CONS frame bug (any 9th param was sent as 0x00), NOT by param 7 being toxic. |
| 8 | *(not in C# reference)* | **N/A on HB2304EU298413** — idx_echo=0 when polled by bridge; **always 0 in iPhone log** (delivered via A8 char) | Not supported on this hardware variant. May be non-zero on other models or in specific states. |
| 9 | `orientationLightState` | **Always 0 on HB2304EU298413** — idx_echo=0; **always 0 in iPhone log** (delivered via A7 char) | ⚠️ **CONFIRMED NOT OBSERVABLE** (2026-04-19): light turned on/off three times; SPL identical at all events. Full raw frame analysis (all 4 GATT handles) found zero variation. Hardware proximity sensor only. See `docs/developer/ble-protocol.md`. |
| 10 | *(not in C# reference)* | **N/A on HB2304EU298413** — idx_echo=0; **always 0 in iPhone log** (delivered via A8 char) | Not supported on this hardware variant. May be non-zero on other models. |
| 11 | *(not in C# reference)* | **Always 0 in iPhone log** (delivered via A7 char, position after params 6 and 7) | Not polled by bridge. Always zero on HB2304EU298413. |

### iPhone polls params 0–11 via four GATT characteristics (2026-04-20)

The iPhone app requests all 12 SPL params (0–11) and receives them distributed across
four GATT notification characteristics simultaneously:

| Char | UUID suffix | Params delivered |
|------|-------------|-----------------|
| A5 (READ_0) | `...A53E0000` | 0, 1 (first chunk) |
| A6 | `...A63E0000` | 2, 3, 4, 5 |
| A7 | `...A73E0000` | 6, 7, 11 |
| A8 | `...A83E0000` | 8, 9, 10 |

On HB2304EU298413, params 8–11 are all zero in every captured log. Their meaning is
unknown. They may be non-zero on other models or in specific device states (descaling,
cleaning cycle, dryer active, error states).

**Investigation trigger:** when a BLE traffic log shows non-zero values on A7 (params 6/7/11)
or A8 (params 8/9/10) beyond what the bridge already reads, decode using 5-byte SPL
record format and cross-reference with known device state at capture time.

**Full analysis:** `docs/developer/gatt-characteristics-a6-a7-a8.md`

### Fix applied (2026-04-17)

**The bridge had `is_anal_shower_running` and `is_dryer_running` swapped:** it was reading param 3 for `is_dryer_running` (actually the anal shower) and param 1 for `is_anal_shower_running` (which never changes). Fixed in all code paths.

### GetFilterStatus ordering constraint (confirmed 2026-04-17)

**After GetSPL with unsupported param indices (8, 9, 10 on HB2304EU298413), the device enters a
state where it ACKs the subsequent GetFilterStatus CONTROL frame (ErrorCode=0x00) but sends no
data frames → 5 s silence → BLEPeripheralTimeoutError.**

**Evidence:**
| Log | GetSPL CONS frame | a_byte | GetFilterStatus |
|-----|-------------------|--------|-----------------|
| 36844ec (working) | `12 00 00 00 ...` (all zeros — buggy CONS, params 8–10 never sent) | 7 | ✅ responds |
| 7d0d821 (failing) | `12 04 08 09 0a 00 ...` (CONS fix applied, params 8/9/10 sent) | 9 | ❌ ACKd, no data |

**Fix:** In `_fetch_state_and_info()` and `_fetch_info()` in `main.py`, call
`get_filter_status_async()` **before** `get_system_parameter_list_async()`. The device is in a
clean state before any GetSPL, so GetFilterStatus always succeeds when called first.

**Root cause: UNKNOWN (2026-04-17).** The earlier claim that "params 8/9/10 cause device-side
failure" was ⚠️ **disproved by iPhone BLE log analysis** (Stuhlgang log, via `tools/ble-decode.py`):
iPhone uses the identical 12-param list `[0,1,2,3,4,5,6,7,4,8,9,10]` and GetFilterStatus succeeds
every time (59 ms after GetSPL, no intermediate calls). Bridge consumption of GetSPL is bit-for-bit
identical in working and failing cases — no bridge-side bug either.

**Remaining hypothesis:** The bridge inserts `GetDeviceInitialOperationDate` + `GetFirmwareVersionList`
between GetSPL and GetFilterStatus. iPhone calls GetFilterStatus immediately after GetSPL with no
intermediate calls. Whether these extra calls corrupt device state is **not yet confirmed**.

See `docs/developer/getfilterstatus-getspl-ordering.md` for the complete investigation.

### a_byte field in SPL response

The first byte of the result (`a_byte`) indicates how many parameters have valid data. Observed
value `9` for the 12-param iPhone list on HB2304EU298413 — 9 of 12 requested params are supported.
Records for unsupported params (IDs 8, 9, 10 on this device) have `idx_echo=0`.

### idx_echo reliability

Empirical evidence from probe with 12-param list (2026-04-17): idx_echo bytes are **correct** for
all records 0–8 (params 0–7 plus the duplicate param 4). The earlier note claiming "idx bytes are
unreliable from record 2 onward" was incorrect — it was based on observations made while the CONS
frame bug was active (the 9th+ params were sent as 0x00, causing the device to echo back the
corrupted IDs). With the CONS fix in place, idx_echo values are reliable.

**How to investigate index 1:** Sniff a session where the user is definitely seated (with body weight on the WC seat) and observe whether index 1 changes. Compare against sessions with only proximity (approaching without sitting).

**How to investigate index 7:** Monitor with spl-monitor while operating descaling or other features not related to the shower — index 7 may track a state not observable during normal toilet use.

---

## 5. GetFilterStatus — response ID mapping not fully verified

Proc `0x59` (`GetFilterStatus`) returns records with IDs 0–7 (bridge currently polls
8 IDs). Record ID names in `tools/ble-decode.py` are mostly labeled `unknown_NN` except for:
- `0` — `status`
- `1` — `shower_cycles`
- `7` — `days_until_filter_change`
- `8` — `last_filter_reset (unix ts)`

**Update 2026-04-21 (Android pcapng):** The Android app calls `GetFilterStatus` with
**12 params `[0–11]`**, not 8. This confirms there are at least 12 record IDs on this
device. The bridge uses only 8 — IDs 8–11 are not read and their meaning is unknown.

**How to investigate:** Cross-reference the decoded output of
`Keremikwabenfilterwechsel - jetzt wieder in 365 Tagen.txt` against the bridge's
`GetFilterStatus` implementation. See `memory/ble-traffic-logs.md`. Additionally,
decode the `GetFilterStatus` response from the Android pcapng
(`open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng`) to see whether IDs
8–11 carry non-zero values.

---

## 6. E0003 blocked state — ctx=0x00 vs ctx=0x01 split (confirmed 2026-04-16)

### Finding: ctx=0x00 procedures are immune to the blocked state

**Source:** Thomas Bingel C# UWP app log (`local-assets/geberit-aquaclean-logs/log-of-th-bingel-in-blocked-state.log`)
running against the user's own device (SN=HB2304EU298413) in a confirmed blocked state.

| Procedure | Context | Proc | Result in blocked state |
|-----------|---------|------|------------------------|
| GetDeviceIdentification | `0x00` | `0x82` | ✅ **SUCCEEDS** — no subscription needed |
| GetDeviceInitialOperationDate | `0x00` | `0x86` | ✅ **SUCCEEDS** — no subscription needed |
| GetSystemParameterList | `0x01` | `0x0D` | ❌ **FAILS** — CONTROL ACK received but zero data frames follow |

**Conclusion:** The block is application-layer only within the ctx=0x01 command space. BLE transport is healthy — GATT connections succeed, CONTROL ACKs arrive, the device just sends no data. This is NOT a BLE timeout at the radio level.

### C# app behavior in blocked state (no recovery mechanism)

The thomas-bingel C# reference app:
1. Connects, reads DeviceIdentification (ctx=0x00) → **succeeds**
2. Sends NO 0x11/0x13 subscribe sequence at all
3. Calls GetSystemParameterList (ctx=0x01) → receives CONTROL ACK, waits for data → **times out**
4. Retries 50+ times with no change in strategy
5. Crashes with exit code `0xffffffff` (unhandled exception)

**Key insight:** The block affects the C# reference app too. This confirms it is a fundamental device protocol issue, not a bug specific to this bridge.

### Using `geberit-ble-probe.py` as an E0003 diagnostic

The probe script is the most direct tool for diagnosing a blocked device:

```bash
# Step 1: confirm BLE connectivity (ctx=0x00, always works in blocked state)
python tools/geberit-ble-probe.py --proc 0x82 --ctx 0x00
# Expected: DeviceIdentification data — if this fails, BLE is not connected at all

# Step 2: confirm the block (ctx=0x01, fails in blocked state)
python tools/geberit-ble-probe.py --proc 0x0D --ctx 0x01 \
    --args 08 00 01 02 03 04 05 06 09
# Expected in blocked state: "FAILED: BLEPeripheralTimeoutError"
# Expected when unlocked: 61-byte result with SPL values

# Step 3: attempt unlock (send subscribe sequence)
python tools/geberit-ble-probe.py --proc 0x0D --ctx 0x01 \
    --args 08 00 01 02 03 04 05 06 09
# The --no-subscribe flag is NOT set, so subscribe runs automatically
# If this succeeds, the 4×Proc_0x11 + 4×Proc_0x13 sequence unlocked the device
```

### InfoFrame encodes firmware version

The device broadcasts InfoFrame packets during the initial BLE connection flood (10 identical frames).
One byte field encodes the firmware version:

```
InfoFrame bytes: 80 01 30 14 0C 03 00 03 00 00 00 00 31 30 00 12 00 B7 08 00
                 ↑                                    ↑  ↑        ↑
                 header (0x80+ = INFO)               RsHi RsLo   TsLo
```

| Field | Offset | Value | Interpretation |
|-------|--------|-------|----------------|
| `RsHi` | 12 | `0x31` | ASCII `'1'` — first digit of RS version |
| `RsLo` | 13 | `0x30` | ASCII `'0'` — second digit of RS version |
| `TsLo` | 15 | `0x12` | `18` decimal — TS build number |

Result: firmware version `RS10.0 TS18` → displayed as `10.18` (matches `GetSOCApplicationVersions` response).

This means firmware version is readable without calling any procedure — it arrives in the InfoFrame
flood before any request is sent, even in the blocked state.

---

## 7. BLE Advertising Name — "Geberit AC PRO"

**Source:** Android pcapng `open-lid-remote-zur-toilette-laufen-sitzen-aufstehen.pcapng` (2026-04-21)

The Geberit AquaClean Mera Comfort (SN: HB2304EU298413, MAC: 38:AB:41:2A:0D:67)
advertises under the BLE local name **"Geberit AC PRO"**. This was confirmed by:

1. The device appearing 11 times in LE advertising events in the pcapng capture
2. The subsequent connection being to MAC `38:AB:41:2A:0D:67`
3. The `GetDeviceIdentification` response in that session containing "HB2304EU298413"
   and "quaClean Mera Comf" (truncated ASCII) — same physical device

**Implication:** The bridge scans by MAC address, not by advertising name. The name
"Geberit AC PRO" is informational only and does not affect any code path. It may differ
across firmware versions or device models.

---

## 8. How to investigate unknowns systematically

1. Run `ble-decode.py` with `--markdown` on a relevant log:
   ```bash
   /Users/jens/venv/bin/python tools/ble-decode.py <log.txt> --markdown
   ```

2. Filter for unknowns:
   ```bash
   ... | grep -i "unknown\|0x05\|0x07\|0x55\|0x56"
   ```

3. Check `args=` (request payload) and `result=` (response payload) bytes.

4. Record findings here and in the relevant memory file
   (`memory/ble-procedure-investigation-method.md` or `memory/common-settings.md`).
