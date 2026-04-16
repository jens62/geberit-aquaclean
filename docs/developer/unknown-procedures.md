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

## 4. Unknown GetSystemParameterList Indices

The bridge polls indices `[0, 1, 2, 3, 4, 5, 7, 9]`. The following indices are
either unpolled or polled but unconfirmed:

| Index | Code label | C# polls? | Status |
|-------|-----------|-----------|--------|
| 6 | `lastErrorCode` | **Yes** (C# polls `[0,1,2,3,4,5,6,9]`) | **Not polled by bridge** — C# reference names it `lastErrorCode`. Bridge polls index 7 instead. |
| 7 | *(unknown)* | No | Polled by bridge only; meaning **unconfirmed**. Not in C# reference request list. |
| 8 | *(not polled)* | No | **Unknown** — never polled by either bridge or C# reference |
| 9 | `orientationLightState` | Yes | Polled by bridge; label from C# source comment — semantics **not confirmed** from logs |

**Key finding (2026-04-16):** The thomas-bingel C# reference (`GetSystemParameterList.cs`)
polls index 6 and names it `lastErrorCode`. The bridge polls index 7 instead — a deviation
that means the bridge never reads the device's last error code from SPL.
Source: `aquaclean-core/Api/CallClasses/GetSystemParameterList.cs` docstring.

**Note on confirmed vs assumed SPL semantics:** Several currently-polled indices
have misleading code labels. Confirmed (2026-04-15 Stuhlgang log):
- Index 0 = proximity/approach detection (not "user sitting")
- Index 1 = user actually sitting (not "anal shower running")
- Index 3 = anal shower running (not "dryer running")

See `memory/ble-procedure-investigation-method.md` for details.

**How to investigate:** Sniff a session that exercises dryer and orientation light
state changes, and correlate SPL index changes with what happens on the device.

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

## 6. How to investigate unknowns systematically

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
