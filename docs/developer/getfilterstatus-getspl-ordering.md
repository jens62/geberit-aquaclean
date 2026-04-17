# GetFilterStatus / GetSPL Ordering Investigation

**Status: Root cause UNKNOWN (2026-04-17)**

---

## Problem

In the current bridge (post-d5cf93e), `GetFilterStatus` (proc 0x59) times out after
`GetSPL` (proc 0x0D) is called with the 12-param iPhone list `[0,1,2,3,4,5,6,7,4,8,9,10]`.

The device ACKs the GetFilterStatus request normally but then sends no data frames for 5 s,
resulting in `BLEPeripheralTimeoutError`.

---

## Evidence Files

| File | Role |
|------|------|
| `local-assets/geberit-aquaclean-logs/standalone/aquaclean-36844ec…-TRACE-with filter-data.log` | Working reference (8-param GetSPL) |
| `local-assets/geberit-aquaclean-logs/standalone/aquaclean-7d0d821…-TRACE-no-filter-data.log` | Failing case (12-param GetSPL) |
| `local-assets/Bluetooth-Logs/Stuhlgang-approaching-toilet-lid-opens-sitting-analshower-dryer-leaving-lid-closes.txt` | iPhone reference (12-param, always works) |

---

## Confirmed Facts (from TRACE log comparison)

| | Working (36844ec) | Failing (7d0d821) |
|---|---|---|
| GetSPL params sent | `[0,1,2,3,4,5,6,9]` (8 params) | `[0,1,2,3,4,5,6,7,4,8,9,10]` (12 params) |
| GetSPL CONS content | all-zeros (params fit in FIRST) | `04 08 09 0a 00…` |
| a_byte | 7 | 9 |
| GetSPL response frames | 4 | 4 |
| GetSPL ACK bitmap | `0F00000000000000` | `0F00000000000000` (identical) |
| GetFilterStatus request | `1104ff0011987e0101590d08000102030708090a` | identical |
| Device ACK for GetFilterStatus | `70000c0a010000000000000000b7090100000df2` | identical |
| Device data response | 4 frames in ~20 ms | nothing for 5 s → timeout |

**Key finding:** The bridge's consumption of the GetSPL response is bit-for-bit identical in
both cases. No bridge-side consumption bug can explain the difference.

---

## Confirmed Facts (from iPhone BLE log — Stuhlgang log, decoded via `tools/ble-decode.py`)

- iPhone uses **identical** param list: `[0, 1, 2, 3, 4, 5, 6, 7, 4, 8, 9, 10]`
- iPhone calls GetFilterStatus **59 ms** after GetSPL — no intermediate calls
- GetFilterStatus succeeds **every time**, on the same BLE connection
- Calls are on the same BLE connection (no disconnect between GetSPL and GetFilterStatus)

---

## Disproved Hypotheses

### ❌ "Params 8/9/10 break GetFilterStatus — device-side firmware behavior"

**Disproved by iPhone log.** ⚠️ *Doubted by user before confirmed.*

iPhone uses the same 12-param list including 8/9/10, and GetFilterStatus always succeeds.
The device does not break GetFilterStatus merely from receiving params 8/9/10 in GetSPL.

### ❌ "Bridge consumption of GetSPL response is wrong — bridge-side bug"

**Disproved by TRACE log comparison.** Bridge sends identical ACK bitmap `0F00000000000000`,
identical frame count (4), identical byte values in working and failing cases.

### ❌ "3-frame vs 4-frame GetSPL response"

**Wrong** — both logs show 4-frame GetSPL response.

---

## Remaining Hypothesis (UNCONFIRMED)

### 🔍 Intermediate calls between GetSPL and GetFilterStatus

**Bridge sequence (failing):**
1. GetSPL(12 params) → OK
2. GetDeviceInitialOperationDate → OK
3. GetFirmwareVersionList → OK
4. GetFilterStatus → TIMEOUT

**iPhone sequence (working):**
1. GetSPL(12 params) → OK
2. GetFilterStatus → OK (59 ms later, no intermediate calls)

The bridge inserts two extra calls between GetSPL and GetFilterStatus. One of these may
corrupt device state. **Not yet confirmed** which call is responsible or whether this is
truly the cause.

**How to test:** On a freshly power-cycled device, run:

```bash
python tools/getspl-filter-probe.py --order spl-first --trace --log-file /tmp/probe-spl-first.log
```

The probe calls GetSPL then immediately GetFilterStatus with no intermediate calls — matching
the iPhone sequence. If GetFilterStatus succeeds here, the intermediate-calls hypothesis is
confirmed. If it still times out, the intermediate calls are not the cause.

---

## Current Workaround (commit d5cf93e)

`_fetch_state_and_info()` calls GetFilterStatus **before** GetSPL. This helps if the device
state is clean when the session starts. Whether it fully resolves the issue across reconnects
is uncertain — probe tests with filter-first also failed, but may have been run on a device
already in broken state from a prior session.

---

## Why 36844ec Worked

Version 36844ec sent only 8 params to GetSPL because of a CONS frame zero-padding bug: the
CONS byte count was always computed from `message.serialize()` without accounting for actual
parameter count, so extra bytes were zero-padded. The device received `[0,1,2,3,4,5,6,9]`
with a_byte=7. GetFilterStatus was then called after intermediate calls, and it succeeded.

Since the same intermediate calls existed in 36844ec, the working behavior suggests that
receiving only 8 (supported) params in GetSPL does not trigger whatever state the device
enters when it subsequently refuses to respond to GetFilterStatus. The device's behavior
after 8-param vs 12-param GetSPL is different — but params 8/9/10 alone are not the trigger
(iPhone disproves that). The interaction between GetSPL param count and the intermediate calls
is the most likely remaining explanation.
