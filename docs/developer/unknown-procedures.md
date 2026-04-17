# Geberit AquaClean — Unknown / Unresolved BLE Protocol Items

This document consolidates everything that has been observed in iPhone BLE traffic
logs but whose purpose or mapping is not yet fully understood.

Use this as a research backlog: each item has a suggested investigation approach.

Last updated: 2026-04-16

---

## 1. Unknown Procedure Codes

### Proc `0x05` (ctx=0x01) — identified as `GetNodeList`

| Field | Value |
|-------|-------|
| Label | `GetNodeList` |
| Direction | Request (no args), response = `NodeList` struct |
| Response format | 1-byte `A` + 128-byte opaque `B` |
| Seen in | BLE traffic logs; confirmed in thomas-bingel C# repo (`tmp.txt`) |
| Status | **Name confirmed. Response struct known. Semantics of 128-byte B block unknown.** |

**Source:** `aquaclean-core/Api/CallClasses/tmp.txt` — `GetNodeList()` returns `NodeList`.
`NodeList` struct (`aquaclean-core/Api/CallClasses/Dtos/NodeList.cs`):

```csharp
public struct NodeList {
    [DeSerialize(Length = 1)]   public int A { get; set; }
    [DeSerialize(Length = 128)] public byte[] B { get; set; }
}
```

**What B contains:** unknown. Likely a bitmask or list of present BLE nodes/components.

**How to investigate:** Run `ble-decode.py --verbose` on any log, search for proc `0x05`,
and examine the `result=` bytes. With 128 bytes, it likely encodes which device components
are present as a bitmask (128 bytes × 8 bits = 1024 possible node IDs).

---

### Proc `0x07` (ctx=0x01)

| Field | Value |
|-------|-------|
| Label | `UnknownProc_0x07` |
| Direction | unknown |
| Payload | unknown |
| Seen in | BLE traffic logs |
| Status | **Completely unknown** |

**How to investigate:** Same as 0x05 — check `args=` and `result=` fields in decoded output.

---

### Proc `0x08` (ctx=0x01) — `SetActiveProfileSetting` (newly discovered)

| Field | Value |
|-------|-------|
| Label | `SetActiveProfileSetting` |
| Direction | Write |
| Args | `(int profileSettingId, object settingValue)` |
| Seen in | thomas-bingel C# repo (`tmp.txt`) only — not yet observed in iPhone BLE logs |
| Status | **Name known. Semantics unknown. Distinct from proc 0x54.** |

**Source:** `aquaclean-core/Api/CallClasses/tmp.txt` — listed alongside `GetNodeList` (0x05)
as procedures planned but not fully implemented in the C# reference.

**What distinguishes it from proc 0x54 (`SetStoredProfileSetting`):** unknown.
Hypothesis: 0x08 may write to a different storage layer — "active" (live, in-session) vs
"stored" (persisted to device flash). This mirrors the 0x0A/0x0B vs 0x53/0x54 distinction
observed in iPhone init sequences.

**How to investigate:** BLE-sniff the Geberit Home app while changing a shower setting
mid-session (not through the profile settings menu). Look for proc 0x08 in the decoded output.

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

## 2. Unknown Common Setting IDs (proc 0x51/0x52)

The iPhone reads IDs `[2, 1, 3, 0]` on every connect. IDs 4, 6, 7 were confirmed
from the WC Lid BLE log (2026-04-15). The following IDs remain unknown:

| ID | Candidate | Range | Status |
|----|-----------|-------|--------|
| 5 | Maximum Lid Position | float/int | **Unknown** — in `Profile-Settings.xlsx` as "Maximaldeckelposition" but never seen in any BLE capture |
| 8 | Unknown | — | **Unknown** — never observed |
| 9 | Unknown | — | **Unknown** — never observed |

**How to investigate:**
- **ID 5 (Max Lid Position):** Sniff iPhone while using "Maximaldeckelposition" calibration
  in the app's WC lid settings.
- **IDs 8–9:** Sniff iPhone on every connect with `--verbose` and see whether any
  `GetStoredCommonSetting` calls appear for these IDs during a full session.

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

The bridge polls indices `[0, 1, 2, 3, 4, 5, 6, 9]` (8 params). Current status:

### Confirmed semantics (BLE log 2026-04-15 + spl-monitor 2026-04-17)

| Index | C# label | Confirmed semantics | Notes |
|-------|----------|---------------------|-------|
| 0 | `userIsSitting` | **Approach/presence detection** — changes when user enters toilet proximity | ✓ confirmed |
| 2 | `ladyShowerIsRunning` | **Lady shower running** | ✓ confirmed (04-17 session: changed when lady shower started) |
| 3 | `dryerIsRunning` | **Anal shower running** — C# label is WRONG | ✓ confirmed (04-15 and 04-17: changes when anal shower runs) |
| 4 | `descalingState` | Descaling state | from C# |
| 5 | `descalingDurationInMinutes` | Descaling duration | from C# |
| 6 | `lastErrorCode` | Last error code | from C# |

### Unconfirmed / unknown

| Index | C# label | Observed | Status |
|-------|----------|----------|--------|
| 1 | `analShowerIsRunning` | **Never changed** in any monitored session, including sessions with both showers running | **Unknown** — semantics not confirmed. Possibly WC seat sensor (weight-triggered?) vs index 0's proximity sensor. Dryer running state is NOT reported by any currently polled index. |
| 9 | `orientationLightState` | **N/A on HB2304EU298413** — device echoes 0 for this param, indicating it is not supported | Device does not expose orientation light state via GetSPL on this hardware variant. Bridge still requests it but result is always 0 and currently unused. |

### Fix applied (2026-04-17)

**The bridge had `is_anal_shower_running` and `is_dryer_running` swapped:** it was reading param 3 for `is_dryer_running` (actually the anal shower) and param 1 for `is_anal_shower_running` (which never changes). Fixed in all code paths.

### Index 7 — removed

Index 7 was previously polled by the bridge but not in the C# reference list. It has been removed (any 9th param causes a stuck-device GetSPL failure when the device is in locked state — confirmed 2026-04-16). Index 7's semantics remain unknown.

**How to investigate index 1:** Sniff a session where the user is definitely seated (with body weight on the WC seat) and observe whether index 1 changes. Compare against sessions with only proximity (approaching without sitting).

---

## 5. GetFilterStatus — response ID mapping not fully verified

Proc `0x59` (`GetFilterStatus`) returns records with IDs 0–7 (bridge currently polls
8 IDs). The iPhone may use 12 IDs. Record ID names in `tools/ble-decode.py` are
mostly labeled `unknown_NN` except for:
- `0` — `status`
- `1` — `shower_cycles`
- `7` — `days_until_filter_change`
- `8` — `last_filter_reset (unix ts)`

**How to investigate:** Cross-reference the decoded output of
`Keremikwabenfilterwechsel - jetzt wieder in 365 Tagen.txt` against the bridge's
`GetFilterStatus` implementation. See `memory/ble-traffic-logs.md`.

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

## 7. How to investigate unknowns systematically

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
