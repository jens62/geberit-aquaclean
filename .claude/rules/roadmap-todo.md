# Roadmap & Open TODO Items

## Open TODOs

### Add SPL params 12 and 13 to `SPL_PARAMS_MERA_COMFORT`

Confirmed safe from OTA BLE capture (2026-06-01, HB2304EU298413, firmware RS146.21).
iPhone sends `[13, 12, 0, 1, 2, 3, 4, 5, 6, 7]` — params 12 (LidOffsetPosition) and
13 (ShowerArmOffsetPosition) are valid for Mera Comfort firmware ≥ RS25.

Implementation:
- Change `SPL_PARAMS_MERA_COMFORT` in `AquaCleanClient.py` to `[0,1,2,3,4,5,6,7,12,13]`
- Add `lid_offset_position` and `shower_arm_offset_position` to `DeviceStateChangedEventArgs` in `IAquaCleanClient.py`
- Extract `data_array[8]` and `data_array[9]` in `AquaCleanClient.get_state()`
- Map to `device_state` and broadcast via SSE
- Update `GetFilterStatus` record IDs list to include 12 and 13

---

### Add proc 0x55 to the bridge init sequence

iPhone sends proc 0x55 twice per session (end of init + once mid-session).
Payload varies: `[0x01]` in PacketLogger sessions, `[0x00]` in OTA capture.
Purpose unknown — possibly "session ready" / "enable remote control".

Implementation: after final `GetStoredCommonSetting` call, send `Proc0x55([0x01])`.
Add a `CallProc0x55Async()` stub to `AquaCleanBaseClient`.

---

### Add SetCommand code 3 (`Stop`) to `Commands.py`

Confirmed from two sources:
- Android pcapng (2026-04-22): sent before `ToggleLidPosition`
- iPhone OTA capture (2026-06-01): `SetCommand([1, 3])` sent before `SetCommand([1, 10])`

`AC_CMD_STOP` = "Stop all". Pattern: Stop before ToggleLid makes sense (stop running shower/dryer first).
Wire to REST/MQTT/CLI/HACS following the "all interfaces" rule.

---

### Implement `SetActiveProfileSetting` (proc 0x08)

Wire format confirmed from OTA capture (2026-06-01): `[arg_count=3, setting_id, value]`.
Examples: `[3, 4, 2]` = set AnalShowerPosition to 2; `[3, 2, 2]` = set AnalShowerPressure to 2.
Applies settings live (in-session); proc 0x54 persists to flash.
Wire CallClass first, then expose via REST/MQTT.

---

### Wire remaining unimplemented procedures

| Proc | Name | Status |
|------|------|--------|
| `0x06` | GetActualOutletTemperature | Not yet in bridge; trigger: start shower and sniff |
| `0x07` | Per-node profile setting query | Wire format: `[node_id]` — 1-byte arg |
| `0x0A` / `0x0B` | GetActiveCommonSetting / SetActiveCommonSetting | Bridge skips; iPhone always sends |
| `0x51` | GetStoredCommonSetting(id) → 2-byte int | High priority; bridges to CommonSetting IDs 4–12 |
| `0x56` | SetDeviceRegistrationLevel(int) // 257 | Low priority; purpose unclear |

---

### Expose descaling state in device_state / SSE / MQTT

`descaling_state` (SPL param 4) and `descaling_min` (SPL param 5) are polled every cycle
but never extracted — `DeviceStateChangedEventArgs` has no fields for them.

Descaling state machine (confirmed 2026-04-22):
| descaling_state | descaling_min | Meaning |
|----------------|---------------|---------|
| 0 | 0 | Idle |
| 1 | 60 | PrepareDescaling ACKed; device self-preparing (~55 s) |
| 2 | 60 | Waiting for user to add descaler |
| 3 | 60–0 | Chemical cycle running; countdown in minutes |

Implementation (three changes):
1. Add `DescalingState: int = None` and `DescalingMin: int = None` to `DeviceStateChangedEventArgs`
2. Extract `data_array[4]` and `data_array[5]` in `AquaCleanClient.get_state()`
3. Map to `device_state` in `main.py` and include in SSE broadcast

---

### Add CommonSetting IDs 4–12 to bridge

Confirmed from app source analysis (2026-06-02). Fix ID 0 label (`OdourRunOn` → `WaterHardness`).
Add IDs 4–12 to bridge read sequence and expose via REST/MQTT/HACS.
See `ble-protocol.md` for the full CommonSetting ID table.

---

### Add ProfileSetting IDs 11–14 to bridge

Confirmed from app source analysis (2026-06-02):
IDs 11 (SeatHeating), 12 (WaterHeating), 13 (DryerFanPower, 5 levels), 14 (LadyOscillation).
Add to `ProfileSettings.py` enum. Add getters to `AquaCleanClient`; expose via REST/CLI.

---

### Wire remaining Commands enum entries (all interfaces)

Priority: `ToggleOrientationLight` (complete missing MQTT/HACS/CLI interfaces).
All other unwired commands in `ble-protocol.md` Commands table follow the same pattern as
ToggleDryer — zero new protocol code, just wiring on all interfaces.

---

### Performance: reduce poll query time from ~2.7 s to ~0.5 s

Profiled from TRACE log 2026-04-23. Steady-state poll breakdown:

| Phase | Time | Every poll? | iPhone does this? |
|-------|------|-------------|-------------------|
| 8× SubscribeNotifications (unlock) | ~2,400 ms | Yes | No — once per app lifetime |
| 11× GetStoredProfileSettings (0x53) | ~2,200 ms | Yes | No — once at session init |
| GetSystemParameterList | ~410 ms | Yes | Yes |
| BLE connect + wait_for_info_frames | ~700–1,300 ms | Yes | ~150 ms |

**Fix 1** — Cache GetStoredProfileSettings (biggest win, ~2.2 s saved per poll).
**Fix 2** — Make SubscribeNotifications conditional (~2.4 s saved per connect):
skip if last poll succeeded less than N seconds ago.
**Fix 3** — Reduce GetFilterStatus timeout from 5 s to 2 s.

---

### SQLite change log + raw data debug panel

Goal: log every raw value change from every Geberit procedure to disk.
Schema: `sessions`, `changes`, `annotations` tables with WAL mode.
Annotation endpoint: `POST /debug/annotate` — key feature for protocol analysis.
Web UI: "Live values" tab (SSE-driven) + "Change history" tab (REST-driven).
HACS: use HA recorder instead (diagnostic sensor entities).
Implementation order: SQLite → annotation REST → live values tab → change history tab → all procs → HACS sensors → export command.

---

### BLE sniffing needed — unimplemented procedures

When asked "What should I sniff?", start here:

| Proc | Name | What to trigger | What to look for |
|------|------|-----------------|------------------|
| `0x08` | SetActiveProfileSetting | Change a profile setting during active shower | Outgoing write to WRITE_0/WRITE_1 after slider moves |
| `0x56` | SetDeviceRegistrationLevel | App startup / first connect | Any outgoing ctx=0x01 proc=0x56 write |

Tools: `tools/ble-session-replay.py --dry-run` to verify proc appears in captured log.
`tools/geberit-ble-probe.py --proc 0xNN` to test candidate payload on live device.

---

### HACS Alba: configurable DpId polling frequency

`_ALBA_SLOW_POLL_EVERY = 10` is hardcoded.
**Option A** (recommended first): expose as user setting in options flow (~0.5 session).
**Option B** (recommended long-term): per-group frequency (live/stored settings/identification/statistics/descaling).
**Option C** (over-engineered): per-DpId — skip.

---

### HACS open items

- **Config flow: validate ESPHome host field syntax** — malformed IP (e.g. comma instead of dot) passes `cv.string` but fails at aioesphomeapi. Add validator for `CONF_ESPHOME_HOST`.
- **`sensor.geberit_aquaclean_ble_state`** — intra-poll BLE cycle tracking (connecting/connected/disconnected/error). Self-push via `async_write_ha_state()` only — do NOT call `coordinator.async_update_listeners()` mid-poll.
- **Poll countdown sensor** — `next_poll` timestamp from `poll_epoch` + `poll_interval`.
- **RSSI tracking** — add `ble_rssi` and `wifi_rssi` to PollStats and HACS coordinator.
- **Poll countdown gauge** — improve accuracy; investigate native `SensorEntity` with `device_class: timestamp`.
- **Integration version sensor** — read from `manifest.json`, expose as DIAGNOSTIC entity.
- **Multilingual support (EN/DE/FR/IT)** — `strings.json` + translation files; replace `_attr_name` with `_attr_translation_key`. ~1 session.
- **Download button for Performance Statistics panel** — Option 1 (quickest): `custom:button-card` + inline JS. Option 3 (proper): custom Lovelace card.

---

### install.sh: show progress during slow pip steps

Fix options: print "This may take several minutes on Raspberry Pi…" warning before each
slow step + add `--timeout 60` to pip commands.

---

### Log error codes to the Python log file

When an exception is mapped to an error code in `_on_demand_inner`'s finally block,
only MQTT and SSE receive the code. Add `logger.error(f"BLE error {ec.code} — {e}")`
at the point of mapping in `main.py`.

---

### system-info: distinguish config.ini values from runtime values

`get_system_info()` reads `ble_connection`, `esphome_api_connection`, `poll_interval`
from config.ini only. Split `config` block into `from_file` and `runtime` sub-sections.
Use `ApiMode.get_system_info_data()` (already called by REST endpoint) to merge runtime values.

---

### Agentic BLE protocol fuzzer

New script `tools/geberit-ble-fuzz.py` with modes:
`--mode read-procs` / `--mode setcommand` / `--mode common-settings` / `--mode profile-settings`.
Reuses `BluetoothLeConnector` + `AquaCleanClient`. Safe defaults: skip dangerous SetCommand
codes (33–36, 4, 37, 6–9) unless `--unsafe` is passed.

---

## Resolved / implemented items

Archived to `.claude/rules/archive.md` (2026-06-03).
