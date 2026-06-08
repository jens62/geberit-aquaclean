## Archived 2026-06-03

- ~~Investigate E0002/E0003 after iPhone app closes~~ — **RESOLVED 2026-04-16, commit 36844ec**
- ~~HACS: Dynamic signal-strength visualization~~ — **Done in dashboard (ceefaaf)**
- ~~HACS: Add Geberit BLE connection status sensors~~ — **Done in v2.4.29–30**
- ~~Auto-restart ESP32 when BLE scanner is stuck~~ — **Done, commit 7cf2d97**

## Memory snapshot 2026-06-03
Active memory entries: 65  |  Pruned: 4  |  Snapshot taken by /compress-memory

## Archived 2026-06-03 (session 2)

- ~~Add proc 0x55 to bridge init sequence~~ — **RESOLVED**: Proc 0x55 = `GetDeviceRegistrationLevel`; app reads for UI customisation only; bridge does not need it. Confirmed from decompiled factory source.

## Memory snapshot 2026-06-03 (session 2)
Active memory entries: 70  |  Pruned: 0 (1 inline MEMORY.md entry updated)  |  Snapshot taken by /compress-memory

## Archived 2026-06-04

- ~~Proc 0x0A/0x0B — GetActiveCommonSetting / SetActiveCommonSetting~~ — **DONE in v3.0.6** — implemented for orientation light control (confirmed live on HB2304EU298413)
- ~~Stop command (SetCommand 3)~~ — **DONE in v3.0.6** — wired all interfaces; stops shower/dryer (not fan, not lid — confirmed live v3.0.7b1)
- ~~OdourExtraction (12) + OdourExtractionRunOn (13)~~ — **DONE in v3.0.6** — wired all interfaces
- ~~Orientation light on/off control (Mera Comfort)~~ — **DONE in v3.0.6** — proc 0x0B, immediate effect confirmed
- ~~Descaling state + duration sensors~~ — **DONE in v3.0.6** — SPL params 4/5 exposed as HACS sensors
- ~~SPL params 12+13 (LidOffset, ShowerArmOffset)~~ — **DONE in v3.0.6** — sensors added to HACS

## Memory snapshot 2026-06-04
Active memory entries: 70  |  Pruned: 0 (2 inline MEMORY.md entries updated)  |  Snapshot taken by /compress-memory

## Archived 2026-06-06

- ~~Proc 0x0A/0x0B — GetActiveCommonSetting / SetActiveCommonSetting~~ — **DONE** — removed from "Wire remaining unimplemented procedures" table; implemented via `SetActiveCommonSettingAsync`, used for orientation light control (confirmed live 2026-06-04)

## Memory snapshot 2026-06-06
Active memory entries: 68  |  Pruned: 2 (`feedback_python_interpreter.md`, `feedback_python_path.md` — duplicated CLAUDE.md Python path rule)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-08
Active memory entries: 71  |  Pruned: 0 (no file removals; MEMORY.md index condensed: merged two GetSPL sections, removed 3 DONE/RESOLVED bullets from Unknown/Unresolved list — 212→198 lines)  |  Snapshot taken by /compress-memory
