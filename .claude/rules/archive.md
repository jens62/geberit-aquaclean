## Memory snapshot 2026-07-19
Active memory entries: 112  |  Pruned: 1 (`ble-advertising-sensorstate.md` — SensorState/proximity/AC_STATUS_USER_PRESENT/lid-lifter-sensor facts now fully covered by `.claude/rules/ble-protocol.md`'s "BLE advertising payload — SensorState" and CommonSetting sections, including the 2026-07-19 nRF Connect for Android cross-platform confirmation that was added to both files verbatim; fixed the one dangling `[[ble-advertising-sensorstate]]` backlink in `nrf-ble-analyze-completeness-audit-2026-07-18.md` to point at the rules file instead)  |  CLAUDE.md: 3,035 chars — unchanged since last snapshot, well within the 40,000 limit  |  roadmap-todo.md: still a pure pointer stub, nothing to archive  |  Big session: refactored `docs/developer/mock-service-requirements.md` into 55 numbered `REQ-NNN` entries (Type/Statement/Status/Implementation Details) and added a new `docs/developer/ble-relay-rest-api-requirements.md` (10 `RAPI-NNN` entries) — both now the authoritative structured source of truth for mock-service work, reducing future duplication risk between memory and docs/developer/ going forward even though this skill's pruning criterion is scoped to `.claude/rules/`+CLAUDE.md only, not docs/  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-07-16
Active memory entries: 63  |  Pruned: 1 (`spl-params-model-differentiation.md` — superseded by `.claude/rules/ble-protocol.md`'s SPL parameter index table, which corrects the stale `[0-7]` list and explicitly debunks the "indices 8-10 corrupt GetFilterStatus" claim as unverified/contradicted) + 1 inline MEMORY.md line (pre-release-before-stable note, already a pure pointer to `.claude/rules/release-process.md` with no backing file)  |  CLAUDE.md: 3,035 chars — well within the 40,000 limit, no change  |  roadmap-todo.md: already a pointer stub ("Merged into docs/roadmap.md"), nothing to archive  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-27 (session 2)
Active memory entries: 46  |  Pruned: 1 (`hacs-pre-release-checklist.md` — explicitly OBSOLETE, v3.1.2 released)  |  Updated: `alba-esphome-probe-status.md` (branch merged in v3.1.0, not "NOT merged to main"), `mock-alba-jetzt-verbinden.md` (description version 2.17.0 → 2.19.0)  |  Added MEMORY.md inline: Alba variant=0 mapping confirmed  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-27
Active memory entries: 46  |  Pruned: 0  |  Added: `mock-firmware-all-components-rs30.md` (all _FW_COMPONENT_VERSIONS must be RS30.0 TS206 — confirmed v1.64 vs v1.74 log comparison, fixed v1.75.0b1)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-25
Active memory entries: 45  |  Pruned: 1 (`feedback_prerelease_before_stable.md` — verbatim duplicate of `.claude/rules/release-process.md` MANDATORY pre-release rule)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-18 (session 3)
Active memory entries: 45  |  Pruned: 0 (no changes since session 2; remote control docs committed + pushed)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-18 (session 2)
Active memory entries: 45  |  Pruned: 0 (mock version inline updated: 2.17.0 → 2.18.6 + shower flow note)  |  Snapshot taken by /compress-memory

## Archived 2026-06-18

- ~~HACS: Config flow wizard with mDNS/zeroconf ESPHome proxy discovery~~ — **Done in v3.1.2** — multi-step wizard, mDNS auto-discover, manual IP fallback on same screen, rescan button
- ~~HACS: detect model from BLE advertisement before first poll~~ — **Done in v3.1.2** — BLE advertisement article prefix → model string stored in config entry; proc 0x82 used as fallback refinement on first poll
- ~~HACS: model-aware entity creation via positive feature sets~~ — **Done in v3.1.2** — 11 `FS_*` constants, per-model frozenset composition in `DEVICE_TYPE_FEATURE_SETS`, entity lists filtered at `async_setup_entry`; `wired=False` stubs for unwired features

## Archived 2026-06-03

- ~~Investigate E0002/E0003 after iPhone app closes~~ — **RESOLVED 2026-04-16, commit 36844ec**
- ~~HACS: Dynamic signal-strength visualization~~ — **Done in dashboard (ceefaaf)**
- ~~HACS: Add Geberit BLE connection status sensors~~ — **Done in v2.4.29–30**
- ~~Auto-restart ESP32 when BLE scanner is stuck~~ — **Done, commit 7cf2d97**

## Memory snapshot 2026-06-03
Active memory entries: 65  |  Pruned: 4  |  Snapshot taken by /compress-memory

## Archived 2026-06-03 (session 2)

- ~~Add proc 0x55 to bridge init sequence~~ — **RESOLVED**: Proc 0x55 = `GetDeviceRegistrationLevel`; app reads for UI customisation only; bridge does not need it. Confirmed from factory source analysis.

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

## Memory snapshot 2026-06-08 (session 2)
Active memory entries: 69  |  Pruned: 2 (`feedback_update_sh_not_install_sh.md`, `feedback_curl_oneliner.md` — both covered verbatim by .claude/rules/release-process.md MANDATORY rules; stale note in former said CLAUDE.md had install.sh which is no longer true)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-10
Active memory entries: 31  |  Pruned: 0 (all linked entries load-bearing; no duplicates of rules files found)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-13
Active memory entries: 41  |  Pruned: 0 (10 new entries added since Jun 10; all load-bearing; no verbatim duplicates of rules files found)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-15
Active memory entries: 42  |  Pruned: 0 (all linked entries load-bearing; no verbatim duplicates of rules files found)  |  MEMORY.md index corrected: `alba-tunnel-data-exchange.md` entry updated — Phase 3 confirmed as mid-session SABM on same BLE conn (not new conn), GetEndProduct NOT called in "Jetzt verbinden" flow (2026-06-15 testing)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-17
Active memory entries: 44  |  Pruned: 0 (1 updated: `hacs-todos.md` — marked RSSI tracking as DONE, was stale)  |  Added: `hacs-model-entity-feature-sets.md` (feature sets architecture, wired flag, stubs), `hacs-pre-release-checklist.md` (version bump, migration note, merge, known limitations)  |  Snapshot taken by /compress-memory

## Memory snapshot 2026-06-18
Active memory entries: 45  |  Pruned: 1 (`hacs-pre-release-checklist.md` — v3.1.2 released, all items completed, removed from index)  |  Snapshot taken by /compress-memory
