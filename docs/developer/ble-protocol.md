# Geberit AquaClean — BLE Protocol Reference

Reverse-engineered from iOS PacketLogger captures (April 2026) and validated against
the thomas-bingel C# reference implementation.

---

## Overview

The app communicates with the toilet via GATT Write Without Response (app → device)
and GATT Handle Value Notifications (device → app). All messages are packed into
20-byte BLE frames.

---

## GATT Characteristics

Service UUID: `3334429d-90f3-4c41-a02d-5cb3a03e0000`

| Handle | Role | Used for |
|--------|------|----------|
| 0x0003 | WRITE_0 | App → device: SINGLE and FIRST frames |
| 0x0006 | WRITE_1 | App → device: CONS continuation frames |
| 0x0009 | WRITE_2 | App → device: CONS continuation frames |
| 0x000C | WRITE_3 | App → device: CONS continuation frames |
| 0x000F | READ_0  | Device → app: notifications |
| 0x0013 | READ_1  | Device → app |
| 0x0017 | READ_2  | Device → app |
| 0x001B | READ_3  | Device → app |

---

## Frame Types

Every frame is exactly 20 bytes. The first byte encodes the frame type.

| Header byte | Type | Meaning |
|-------------|------|---------|
| 0x11 | SINGLE | Complete single-frame message |
| 0x13 | FIRST  | First frame; 1 CONS follows |
| 0x15 | FIRST  | First frame; 2 CONSes follow |
| 0x17 | FIRST  | First frame; 3 CONSes follow |
| 0x12 | CONS   | Outgoing continuation (index 1) |
| 0x42 | CONS   | Device-sent continuation (index 1) |
| 0x44 | CONS   | Device-sent continuation (index 2) |
| 0x60 | CONTROL| ACK / flow control (no HasMsgType) |
| 0x70 | CONTROL| ACK / flow control (HasMsgType) |
| 0x80+ | INFO  | Info flood sent by device at connect |

---

## Message Layout (SINGLE frame)

**Request (app → device, bytes 1–19):**
```
[0]  0x04          message type (request)
[1]  0xFF
[2]  0x00          protocol flags
[3]  body_len      = 4 + arg_len
[4]  counter_lo    rolling 2-byte counter
[5]  counter_hi
[6]  0x01          node (always 1)
[7]  context       0x00 or 0x01
[8]  procedure     operation code
[9]  arg_len       number of argument bytes
[10+] args         arg_len bytes, zero-padded to fill frame
```

**Response (device → app, bytes 1–19):**
```
[0]  0x05          message type (response)
[1]  0x00
[2]  0x00
[3]  body_len
[4]  counter_lo
[5]  counter_hi
[6]  0x00          status byte (0 = OK)
[7]  0x01          node
[8]  context
[9]  procedure
[10] result_len    number of result bytes
[11+] result data
```

For multi-frame responses the message is assembled from FIRST_DEV + CONS_DEV frames
before parsing. The assembled body has the same layout.

---

## Procedure Codes

| Context | Proc | Name | Args |
|---------|------|------|------|
| 0x00 | 0x82 | GetDeviceIdentification | none |
| 0x00 | 0x86 | GetDeviceInitialOperationDate | none |
| 0x01 | 0x05 | UnknownProc_0x05 | none (seen in iOS, not in our code) |
| 0x01 | 0x07 | UnknownProc_0x07 | 1 byte |
| 0x01 | 0x09 | SetCommand | 1 byte = command code |
| 0x01 | 0x0A | GetStoredProfileSetting | 1 byte = index (iOS wire format) |
| 0x01 | 0x0B | SetStoredProfileSetting | 2 bytes = index, value (iOS wire) |
| 0x01 | 0x0D | GetSystemParameterList | 13-byte padded list payload |
| 0x01 | 0x0E | GetFirmwareVersionList | 13-byte padded list payload |
| 0x01 | 0x45 | GetStatisticsDescale | none |
| 0x01 | 0x51 | GetStoredCommonSetting | 1 byte = settingId |
| 0x01 | 0x53 | GetStoredProfileSetting | (C# enum variant) |
| 0x01 | 0x54 | SetStoredProfileSetting | (C# enum variant) |
| 0x01 | 0x56 | SetDeviceRegistrationLevel | 1 byte |
| 0x01 | 0x59 | GetFilterStatus | 13-byte padded list payload |
| 0x01 | 0x81 | GetSOCApplicationVersions | none |
| 0x01 | 0x11 | SubscribeNotifications_Pre | 5 bytes: count + component IDs |
| 0x01 | 0x13 | SubscribeNotifications | 5 bytes: count + component IDs |

---

## 13-Byte Padded Payload Rule (CRITICAL)

All "get list" procedures (`GetSystemParameterList`, `GetFirmwareVersionList`,
`GetFilterStatus`) require **exactly 13 bytes** of argument payload:

```
byte[0]    count  (number of requested IDs, max 12)
bytes[1..12]  IDs  (zero-padded to exactly 12 bytes)
```

Shorter payloads cause the device to return **error 0xF7** immediately.

---

## GetSystemParameterList (0x0D)

Reads live device state. Our bridge requests 8 params; the iPhone app requests 12.

**Request payload:**
```
[count=8][0x00][0x01][0x02][0x03][0x04][0x05][0x07][0x09][0x00][0x00][0x00][0x00]
```
iPhone requests: `[0x0c][0x00][0x01][0x02][0x03][0x04][0x05][0x06][0x07][0x04][0x08][0x09][0x0A]`
(note: index 4 appears twice in the iPhone request)

**Response format:**

```
result[0]        a_byte  (= 0x09 observed; meaning unclear — possibly internal count)
result[i*5+1]    idx echoed by device  ← UNRELIABLE — see note below
result[i*5+2..i*5+5]  value as LE uint32 for position i
```

**⚠ Critical finding — idx bytes are unreliable:**
Empirical analysis of the raw response bytes shows the device only echoes the
correct param ID in `result[i*5+1]` for records 0 and 1. From record 2 onward,
the idx byte is `0x00` regardless of which param was requested.

Example raw response for a 12-param iPhone request:
```
09                     ← a_byte
00  00 00 00 00        ← record 0: idx=0x00(user_sitting), val=0
01  00 05 00 00        ← record 1: idx=0x01(anal_shower),  val=1280
00  02 00 00 00        ← record 2: idx=0x00 (WRONG — should be lady_shower=2), val=2
00  03 00 00 00        ← record 3: idx=0x00 (WRONG), val=3
00  04 00 00 00        ← record 4: idx=0x00 (WRONG), val=4
00  05 00 00 00        ← record 5: idx=0x00 (WRONG), val=5
00  06 00 00 00        ← record 6: idx=0x00 (WRONG), val=6
00  07 00 00 00        ← record 7: idx=0x00 (WRONG), val=7
00  0b 00 00 00        ← record 8: idx=0x00 (WRONG), val=11
00  00 00 00 00        ← record 9:  val=0
00  00 00 00 00        ← record 10: val=0
00  00 00 00 00        ← record 11: val=0
```

**Correct decoding approach — positional, not by idx byte:**
Use the request's param list to determine labels. Position `i` in the response
corresponds to the `i`-th param ID in the request. The value bytes at
`result[i*5+2:i*5+6]` are correct.

This is what `Deserializer.py` does: `data_array[i] = LE(result[i*5+2:i*5+6])`.
`AquaCleanClient._state_changed_timer_elapsed()` then maps positionally:
`data_array[0]→IsUserSitting, [1]→IsAnalShowerRunning, [2]→IsLadyShowerRunning, [3]→IsDryerRunning`.

**Known parameter indices (from `GetSystemParameterList.py` docstring — authoritative):**

| Index | Name | Value when NOT running | Value when running | Notes |
|-------|------|------------------------|--------------------|-------|
| 0 | userIsSitting | 0 | non-zero | 0 = not sitting |
| 1 | analShowerIsRunning | 0 | **1280 (0x0500)** observed | val=0 confirmed by C# unit test |
| 2 | ladyShowerIsRunning | 0 | non-zero | |
| 3 | dryerIsRunning | 0 | non-zero | |
| 4 | descalingState | 0 | non-zero | enum |
| 5 | descalingDurationInMinutes | 0 | integer | |
| 6 | lastErrorCode | 0 | integer | |
| 7 | *unnamed* | 0 | ? | polled by bridge; semantics unknown |
| 8 | *pos 8 = duplicate of idx 4* | — | — | iPhone request has idx 4 twice |
| 9 | orientationLightState | 0 | non-zero | 0 = off |
| 10 | *unknown* | 0 | ? | polled by iPhone only |
| 11 | *unknown* | 0 | ? | polled by iPhone only |

Our bridge polls `[0,1,2,3,4,5,7,9]` (8 params). The iPhone polls
`[0,1,2,3,4,5,6,7,4,8,9,10]` (12 params, index 4 duplicated).

**Encoding confirmed correct:** `val != 0` = running/active; `val == 0` = not running.
Source: thomas-bingel C# unit test (`DeserializerTest.DeserializationOfSystemParameterListTest`)
confirms `DataArray[0..5] = 0` for an all-idle device response.
The bridge code `data_array[i] != 0` is correct and has been since day one.

**1280 (0x0500) for analShowerIsRunning:** observed value when the anal shower is active.
The raw bytes are `[0x00, 0x05, 0x00, 0x00]` (LE). The `0x05` in byte position 1 may
encode the current shower settings (temperature, position, intensity, oscillation) as
a packed compound value — but the exact bit/nibble layout is unconfirmed. A BLE capture
with different profile settings active during a shower would allow decoding.

Source: `Connect-Toggle-Lid-shutdown-app.txt` — all 36 SPL responses are
byte-for-byte identical across the session. **Lid state is not tracked by any
GetSystemParameterList parameter.**

---

## GetFilterStatus (0x59)

**Response format:**
```
result[0]      count (number of records)
per record:    [record_id (1 byte)][value uint32 LE (4 bytes)]
```

Unlike `GetSystemParameterList`, the record IDs in the response ARE reliable here
(confirmed from multiple captures). Use `record_id` directly to look up the value.

| Record ID | Name | Notes |
|-----------|------|-------|
| 0 | status | = 1 |
| 1 | shower_cycles | shower cycle counter |
| 7 | days_until_filter_change | 0 = exchange now; 365 = just reset |
| 8 | last_filter_reset | Unix timestamp |
| 9 | next_filter_change | Unix timestamp; 0 after reset |
| 10 | filter_reset_count | total resets performed |

---

## GetFirmwareVersionList (0x0E)

**Response format:** 5-byte records (assembled from FIRST_DEV + 3×CONS_DEV):
```
[component_id][ascii_v1][ascii_v2][build_byte][0x00]
```
Example: component 1 = `01 32 38 C7 00` → "28" build=199 → displayed as `RS28.0 TS199`.

Requires `send_as_first_cons=True` — the device mirrors the multi-frame request format.

---

## Notification Subscription Sequence (connect unlock)

On every `connect_ble_only()` and `connect()`, the bridge sends 4× Proc(0x01,0x11)
followed by 4× Proc(0x01,0x13) to unlock the device for polling. Without this, a
device whose previous BLE session (e.g. the iPhone Geberit Home app) ended without
properly unsubscribing holds the old subscription open and returns only CONTROL ACKs
in response to GetSystemParameterList — no data frames arrive.

See `_subscribe_notifications()` in `AquaCleanBaseClient.py`.

**Proc(0x01,0x11) payloads — PRE_PAYLOADS (sent first):**
```
[0x04, 0x01, 0x03, 0x04, 0x05]
[0x04, 0x06, 0x07, 0x08, 0x09]
[0x04, 0x0a, 0x0b, 0x0c, 0x0e]
[0x01, 0x0f, 0x00, 0x00, 0x00]   ← count=1, only 0x0f (no 0x0d here)
```

**Proc(0x01,0x13) payloads — PAYLOADS (sent after):**
```
[0x04, 0x01, 0x03, 0x04, 0x05]
[0x04, 0x06, 0x07, 0x08, 0x09]
[0x04, 0x0a, 0x0b, 0x0c, 0x0e]
[0x02, 0x0f, 0x0d, 0x00, 0x00]   ← includes 0x0d = GetSystemParameterList
```

All 4 calls of each proc are required — sending only the last is insufficient.
Format: `[count, id0, id1, ...]` (5 bytes; device echoes back `[count, id0, 0...]`).

**⚠ Do NOT add procedure codes like 0x59 to the 0x13 payloads.** IDs must stay in
the range 0x01–0x0F (compact subscription space). Out-of-range IDs corrupt the
subscription table and cause the corresponding procedure to stop responding.

### `SubscribeNotifications` CallClass design

`SubscribeNotifications` in `CallClasses/` follows the standard CallClass interface
(`get_api_call_attribute()`, `get_payload()`, `result()`) but differs from other
CallClasses in one intentional way: **the payload is injected at construction time**
rather than being self-contained.

```python
SubscribeNotifications(payload=SubscribeNotifications.PAYLOADS[i], proc=0x13)
```

This is the right design for a procedure that must be called 8 times (4×0x11 + 4×0x13)
with 8 different payloads. The class keeps both payload lists as class attributes
(`PRE_PAYLOADS`, `PAYLOADS`) so the caller never deals with raw bytes.

`result()` returns `bytes(data)` — no DTO needed. The response is a minimal ACK
echo with no structured data to extract.

---

## Bridge Discoveries Beyond the C# Reference

The thomas-bingel C# reference implementation
([github.com/thomas-bingel/geberit-aquaclean](https://github.com/thomas-bingel/geberit-aquaclean))
is the closest known public reverse-engineering of the Geberit BLE protocol. The
aquaclean-bridge goes beyond it in the following areas — discovered independently
through iPhone BLE traffic analysis and empirical testing.

### 1. GetFilterStatus (proc `0x59`) — full implementation

The C# reference has **no implementation of filter status reading**.
The bridge fully implements `GetFilterStatus` with a working 13-byte request payload
and a complete record ID mapping:

| Record ID | Field | Notes |
|-----------|-------|-------|
| 0 | `status` | Always 1 |
| 1 | `shower_cycles` | Cumulative shower counter |
| 7 | `days_until_filter_change` | 0 = replace now; 365 = just reset |
| 8 | `last_filter_reset` | Unix timestamp |
| 9 | `next_filter_change` | Unix timestamp; 0 after reset |
| 10 | `filter_reset_count` | Total resets performed |

This allows users to track filter lifetime and trigger `ResetFilterCounter` at the
right time — something the C# reference cannot do at all.

### 2. Dryer Spray Intensity (ProfileSetting index 13)

The C# `ProfileSettings` enum ends at index 10 (`SystemFlush`).
The bridge discovered **index 13 = Dryer Spray Intensity** independently via iPhone
BLE traffic analysis. This setting is writable via proc 0x54 (`SetStoredProfileSetting`)
and is exposed through the bridge's REST API, MQTT, and the web UI.

### 3. Session Unlock Sequence (`SubscribeNotifications`, proc `0x11` / `0x13`)

The C# reference has no implementation of the notification subscription handshake.
The bridge discovered — through empirical testing — that **4× Proc(0x11) followed by
4× Proc(0x13)** must be sent at the start of every BLE session to unlock the device for
polling. Without this sequence, a device whose previous BLE session (e.g. from the
official Geberit Home app) ended without properly unsubscribing will return only CONTROL
ACKs in response to `GetSystemParameterList` — no data frames arrive.

This unlock sequence is now sent on every `connect_ble_only()` and `connect()`.
See `_subscribe_notifications()` in `AquaCleanBaseClient.py`.

### 4. 13-Byte Padded Payload Rule (critical protocol constraint)

All "get list" procedures (`GetSystemParameterList`, `GetFirmwareVersionList`,
`GetFilterStatus`) require **exactly 13 bytes** of argument payload. The C# reference
does not document this constraint. Sending a shorter payload causes the device to
return **error 0xF7** immediately. This was discovered through BLE traffic analysis
and is now a documented, tested requirement.

### 5. SPL idx-byte unreliability

The response to `GetSystemParameterList` contains an "index echo" byte per record.
The C# reference reads values positionally (correct behavior) but does not document
that the idx bytes are **unreliable from record 2 onward** — the device always returns
`0x00` regardless of which parameter was requested. This has been confirmed empirically
from iPhone traffic logs. The bridge decodes positionally and documents this explicitly.

---

## Decoder Tool

`tools/ble-decode.py` parses Xcode PacketLogger `.txt` exports.
See `tools/ble-decode.md` for full usage documentation.

For reverse-engineering a new procedure:
1. Capture iPhone traffic while performing the feature (PacketLogger + BT profile)
2. Run `ble-decode.py session.txt --filter 0xNN --verbose` to see raw result bytes
3. Use `--markdown --output session.md` for a full annotated session view
4. Identify the request arg format and response record format from the raw bytes
5. Implement as a new `CallClass` following the pattern in
   `aquaclean_core/Api/CallClasses/GetStatisticsDescale.py`
