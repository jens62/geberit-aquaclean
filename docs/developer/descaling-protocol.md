# Descaling Protocol — BLE Command Sequence and State Machine

Confirmed from BLE log analysis of
`local-assets/Bluetooth-Logs/Entkalkung-zwischenndrin-BLE-Verbindung-mehrfach-unterbrochen.txt`
(iPhone PacketLogger, device HB2304EU298413, 2026-04-22).
Cross-referenced against `local-assets/Bluetooth-Logs/Descaling the device.pdf` (official Geberit
Home App 10-step procedure).

> ⚠️ **The ceramic honeycomb filter is a completely separate system.** It has nothing to do
> with descaling. See `docs/developer/getfilterstatus-getspl-ordering.md` for filter tracking.

---

## App Procedure Steps → BLE Command Mapping

| PDF Step | App Screen Text | BLE Event | Timestamp | descaling_state |
|----------|-----------------|-----------|-----------|-----------------|
| Tap "Start" | Descaling start screen | `SetCommand(6)` = `PrepareDescaling` | 16:08:33 | 0 → **1**, min=60 |
| "Preparing device…" | Spinner | Device holds state 1 (~55 s) | 16:08:33–16:09:28 | 1, min=60 |
| *(automatic)* | "Open cover (1/5)" appears | Device auto-transitions 1 → 2 | ~16:09:28 | **2**, min=60 — *no BLE command* |
| Open cover (1/5) | User opens cover | No BLE traffic | — | 2 |
| Remove plug (2/5) | User removes plug | No BLE traffic | — | 2 |
| Add 125 ml descaler (3/5) | User pours descaler | No BLE traffic | — | 2 |
| Tap "Confirm" | "Reinsert plug (4/5)" | `SetCommand(7)` = `ConfirmDescaling` | 16:11:03 | 2 → **3**, countdown starts |
| Reinsert plug (4/5) | User reinserts plug | No BLE traffic | — | 3, min counting |
| Close cover (5/5) | User closes cover | No BLE traffic | — | 3 |
| 60-minute countdown | Progress screen | App disconnects; device runs autonomously | 16:12–17:06 | 3, min: 59→…→5 |
| *(app reconnects)* | Countdown display | `GetSPL` polls (GetFilterStatus also called on connect but values unchanged) | 17:06, 17:07, 17:10 | 3, min=5, 4, 3 |
| "Descaling finished" | Completion screen | state → 0, min → 0 | ~17:11 | **0** |

---

## State Machine

`descaling_state` is SPL parameter index 4. `descaling_min` is SPL parameter index 5.

```
state 0 (idle)
  │
  │  SetCommand(6) PrepareDescaling
  ▼
state 1  (device preparing — ~55 s)
  │
  │  Device auto-transitions (no BLE command)
  ▼
state 2  (waiting for user to add descaler)
  │     [user: open cover → remove plug → add 125 ml → close cover]
  │
  │  SetCommand(7) ConfirmDescaling
  ▼
state 3  (chemical descaling running)
  │     descaling_min counts 60 → 59 → … → 1 → 0
  │     device operates fully autonomously; BLE not required
  ▼
state 0  (idle — descaling complete)
```

### State semantics

| descaling_state | descaling_min | Meaning |
|----------------|---------------|---------|
| 0 | 0 | Idle — no descaling in progress |
| 1 | 60 | PrepareDescaling ACKed; device self-preparing |
| 2 | 60 | Ready for user — add descaler now |
| 3 | 60–0 | Chemical cycle running; countdown in minutes |

---

## Key Findings

1. **`PrepareDescaling` → state 1** is an immediate BLE ACK. The "preparing device" screen
   is the app waiting for the state 1→2 auto-transition.

2. **State 1→2 is device-driven** — no BLE command is sent. The transition happens ~55 seconds
   after `PrepareDescaling`. This is the device completing its internal preparation
   (heating, pressurizing, or similar).

3. **State 2 = waiting for user** — the three physical steps (open cover, remove plug,
   add 125 ml descaler) produce zero BLE traffic. The app simply polls SPL to detect
   when the user taps Confirm.

4. **`ConfirmDescaling` starts the chemical cycle** — the 60-minute countdown begins
   immediately. `descaling_min` decrements by 1 per minute.

5. **BLE is not required during descaling** — the device runs the full 60-minute cycle
   autonomously. The app disconnected after ConfirmDescaling and only reconnected near
   the end to display the countdown.

6. **Countdown confirmed accurate** — 54-minute autonomous gap: reconnected at 17:06
   with `descaling_min=5`. 54 + 5 = 59, which matches "60 minutes total with one minute
   elapsed before disconnect" (last seen at min=59 at 16:12).

---

## GetFilterStatus During Descaling

The Geberit Home App calls `GetFilterStatus` on **every BLE connect** as part of its session
init — not specifically because of descaling. It happened to be called on all four reconnects.

**What changed and what didn't (confirmed from raw log bytes):**

| ID | Meaning | Observed values | Notes |
|----|---------|-----------------|-------|
| 3 | `descaling_active` flag | 0 → **1** (at PrepareDescaling); stayed 1 throughout all reconnects | Only confirmed change in this log. Post-completion value (return to 0) not captured — session ends at 17:11 with SPL only, no trailing GetFilterStatus. |
| 6 | Total descaling cycle count | **2 throughout** entire log | Post-completion increment (expected 2→3) not captured — no GetFilterStatus after descaling finished. |
| 7 | `days_until_filter_change` | **355, unchanged** | Ceramic honeycomb filter — unrelated to descaling |
| 8 | `last_filter_reset` | **unchanged** | Ceramic honeycomb filter — unrelated to descaling |

**Key point:** during the countdown reconnects at 17:06, 17:07, 17:10, GetFilterStatus returned
**identical bytes** every time — nothing changed. The relevant countdown data came from GetSPL
(`descaling_min` = 5, 4, 3), not from GetFilterStatus.

---

## BLE Commands Summary

All descaling commands use `SetCommand` (proc `0x09`), 1-byte payload:

| SetCommand code | Name | Effect |
|----------------|------|--------|
| 6 | `PrepareDescaling` | Starts descaling workflow; state 0→1 |
| 7 | `ConfirmDescaling` | Starts chemical cycle; state 2→3 |
| 8 | `CancelDescaling` | Aborts descaling (not observed in this log) |
| 9 | `PostponeDescaling` | Delays a scheduled descaling reminder (not observed) |

---

## Source Log Details

| Field | Value |
|-------|-------|
| Log file | `Entkalkung-zwischenndrin-BLE-Verbindung-mehrfach-unterbrochen.txt` |
| Format | iPhone PacketLogger (Raw Data export) |
| Device MAC | `38:AB:41:2A:0D:67` (HB2304EU298413) |
| Session duration | 16:06:57 – 17:11:02 (64 minutes) |
| BLE connections | 4 (app locked/unlocked phone between steps; normal behavior) |
| Analysis tool | `tools/ble-decode.py --markdown` |
