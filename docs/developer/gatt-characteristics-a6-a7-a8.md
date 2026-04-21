# Geberit AquaClean — Additional GATT Characteristics A6, A7, A8

**Source log:** `local-assets/Bluetooth-Logs/orientierungslicht bei annäherung wird eingeschaltet.txt`
**Analysis date:** 2026-04-20
**Device:** HB2304EU298413 (38:AB:41:2A:0D:67), Mera Comfort, firmware RS10.0 TS18

---

## The four notification characteristics

The standard Geberit AquaClean service (`3334429D-90F3-4C41-A02D-5CB3A03E0000`) exposes more
GATT notification characteristics than the bridge currently uses:

| UUID suffix | Handle | Bridge uses? | Role |
|-------------|--------|:------------:|------|
| `...A53E0000` | `0x000F` | ✅ READ_0 | First chunk of every response + ACK frames |
| `...A63E0000` | `0x0013` | ❌ | Overflow chunk 2 of large responses |
| `...A73E0000` | `0x0017` | ❌ | Overflow chunk 3 of large responses |
| `...A83E0000` | `0x001B` | ❌ | Overflow chunk 4 of large responses |

---

## What A6/A7/A8 actually carry: overflow channels

The iPhone app requests GetSPL for **12 parameters** (0–11) vs the bridge's 8.
A response for 12 × 5-byte records is too large for one GATT notification (MTU=23 bytes).
Instead of using FIRST+CONS framing all on A5, the device distributes the response
across all four characteristics simultaneously:

```
Request (WRITE_0 + WRITE_1):
  GetSPL params [0,1,2,3,4,5,6,7,8,9,10,11]

Response (four simultaneous GATT notifications):
  A5 (0x000F): 70 00  0C 18 01 00  00 00 00 00  00 00 00 00  B7 09 01 00  00 0D F2
               ─────  ─────────────────────────────────────  ───────────  ───────────
               hdr    params 0,1 values (ACK+data chunk 1)   ...          ...

  A6 (0x0013): 12 00  00 00 02 00  00 00 00 03  00 00 00 04  00 00 00 00  05
               ─────  ────────────────────────────────────────────────────────
               hdr    params 2,3,4,5 values

  A7 (0x0017): 14 00  00 00 00 06  00 00 00 07  00 00 00 0B  00 00 00 00
               ─────  ─────────────────────────────────────────────────────
               hdr    params 6,7,11 values

  A8 (0x001B): 16 00  00 00 00 00  00 00 00 00  00 00 00 00  00 00 00 00
               ─────  (params 8,9,10 — all zero on HB2304EU298413)
```

The leading bytes on each characteristic (`70 00`, `12 00`, `14 00`, `16 00`) are
frame-type/sequence headers; they do **not** match the bridge's FIRST/CONS frame types
(`0x11`/`0x13`/`0x12`) — these characteristics use a different framing convention
than A5.

The bridge achieves the same result differently: it uses FIRST+CONS framing all on A5,
requesting only 8 params. Both approaches deliver the same data.

---

## SPL parameters 8, 9, 10, 11 — unknown, all zero on HB2304EU298413

The iPhone requests params 8–11 but receives all zeros:

| SPL index | Bridge polls? | Value on HB2304EU298413 | Known meaning |
|-----------|:-------------:|:-----------------------:|---------------|
| 0 | ✅ | variable | `userIsSitting` |
| 1 | ✅ | variable | (labelled `analShowerIsRunning`, actual meaning: user-sitting variant) |
| 2 | ✅ | variable | unknown |
| 3 | ✅ | variable | `analShowerRunning` (confirmed from BLE log) |
| 4 | ✅ | variable | unknown |
| 5 | ✅ | variable | unknown |
| 6 | ✅ | variable | unknown |
| 7 | ✅ | 0 | unknown — removed from bridge poll (was 9th param, triggered CONS bug) |
| 8 | ❌ | **always 0** on HB2304EU298413 | **UNKNOWN — see below** |
| 9 | ❌ | **always 0** on HB2304EU298413 | **UNKNOWN — see below** |
| 10 | ❌ | **always 0** on HB2304EU298413 | **UNKNOWN — see below** |
| 11 | ❌ | **always 0** on HB2304EU298413 | **UNKNOWN — see below** |

**Params 8–11 could be non-zero on:**
- Other device models (Sela, Tuma, Lota) — different feature sets
- Mera Comfort in specific states: dryer running, descaling in progress, lid calibration,
  cleaning cycle, firmware update, orientation light (unlikely but unproven for non-zero values)

**Trigger for investigation:** when a BLE traffic log shows A7/A8 values with non-zero bytes
beyond the header, decode them as 5-byte SPL records and cross-reference with the known
state at capture time to identify which physical event corresponds to which index.

---

## Why the bridge does NOT need to implement A6/A7/A8 support

1. **Same data, different delivery**: A6/A7/A8 carry SPL params 2–11. The bridge already
   reads params 0,1,2,3,4,5,6,9 via FIRST+CONS framing on A5. No new information.

2. **Params 8–11 are all zero** on the only device tested. Until a log with non-zero values
   is captured and the meanings confirmed, there is nothing to parse.

3. **Not push notifications**: A6/A7/A8 fire in lockstep with each GetSPL request.
   They are not unsolicited device-initiated state changes.

4. **Protocol complexity**: A6/A7/A8 use a different frame header convention than A5.
   Merging 4-characteristic responses into one logical response requires non-trivial
   changes to `BluetoothLeConnector` and `FrameService`.

**Implement only if:** a BLE log shows A7/A8 params 8–11 as non-zero in a state the bridge
needs to track (e.g. dryer running, descaling active) and the meaning is confirmed.

---

## AI analysis pitfall — timing correlation without steady-state baseline

When Gemini and Copilot analyzed this log, both concluded that characteristic A6 (handle
`0x0013`) "changed" when the orientation light activated at ~07:45:04. This was wrong.

The A6 value `12 00 00 00 02 00 00 00 00 03 ...` that appeared at 07:45:03 is
**identical to the steady-state value present from 07:41:06 onwards** — 4 minutes before
the light event. The AIs observed the init→steady-state transition (which happens once,
~5 seconds after connect) and incorrectly mapped it to the light activation.

Lesson: always check whether a "changed" value also appears at earlier timestamps before
attributing it to a specific physical event.

---

## Related files

- `memory/gatt-additional-characteristics.md` — memory entry with recall trigger
- `memory/orientation-light-ble-state-invisible.md` — confirms orientation light is BLE-invisible
- `docs/developer/unknown-procedures.md` — SPL params 8–11 listed as unknown
- `local-assets/Bluetooth-Logs/ble-decode.md` — decoder tool documentation
