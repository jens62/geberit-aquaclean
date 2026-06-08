# Roadmap & Open Tasks

Planned features, improvements, and known bugs to fix.

---

## Bug fixes

### Fix: SPL parameter mislabeling (LidOffset / ShowerArmOffset)

**⚠️ PREREQUISITE before changing the bridge**: capture Geberit Home v2.14.1 OTA via
PacketLogger on Mera Comfort. Verify whether the app requests SPL indices 12 and 13
for the same data as v2.13.2 (UnpostedShowerCycles + DaysUntilNextDescale), or whether
v2.14.1 requests different indices for LidOffset/ShowerArmOffset (~104/105).
Do not change the SPL list until this capture confirms the mapping.

**Root cause**: `SPL_PARAMS_MERA_COMFORT = [0,1,2,3,4,5,6,7,12,13]` is queried correctly,
but the bridge mislabels the results:
- `data_array[8]` (SPL index 12) = `AC_STATUS_UNPOSTED_SHOWER_CYCLES` — labeled `LidOffsetPosition`
- `data_array[9]` (SPL index 13) = `AC_STATUS_DAYS_UNTIL_NEXT_DESCALE` — labeled `ShowerArmOffsetPosition`

The real `LidOffsetPosition`/`ShowerArmOffsetPosition` are at SPL indices 104/105 (DpIds 65700/65701),
not currently queried. The correctly-labeled data is already available via `GetStatisticsDescale` (proc 0x51).

**Fix steps** (after v2.14.1 OTA capture confirms):
1. Remove indices 12 and 13 from `SPL_PARAMS_MERA_COMFORT` → back to `[0,1,2,3,4,5,6,7]`
   (GetStatisticsDescale already provides the same data with correct names)
2. Remove `LidOffsetPosition`/`ShowerArmOffsetPosition` from `DeviceStateChangedEventArgs` (`IAquaCleanClient.py`)
3. Remove from `device_state` SPL update path (`main.py` ~lines 2970–2981)
4. Remove from coordinator SPL result mapping (`coordinator.py` ~lines 592–619)
5. Remove two HACS sensors with wrong data: `lid_offset_position`, `shower_arm_offset_position` (`sensor.py`)
6. Update bridge comments in `AquaCleanClient.py` and `coordinator.py`

---

## Protocol / BLE

### Add SPL indices 14–22 to bridge

All confirmed safe from firmware analysis (node 0x01 dispatcher handles 0–21) and iOS app DpId.cs.
Indices 16, 17, 20, 21 are not yet exposed anywhere — add to bridge and expose via REST/MQTT/HACS.

| Index | Name | Bridge currently provides via |
|-------|------|-------------------------------|
| 14 | DaysUntilShowerRestricted | GetStatisticsDescale — redundant if added to SPL |
| 15 | ShowerCyclesUntilConfirmation | GetStatisticsDescale — redundant if added to SPL |
| 16 | TimestampAtLastDescale | **not yet exposed** |
| 17 | TimestampAtLastDescalePrompt | **not yet exposed** |
| 18 | NumberOfDescaleCycles | GetStatisticsDescale — redundant if added to SPL |
| 19 | DaysUntilNextFilterChange | GetFilterStatus — redundant if added to SPL |
| 20 | TimestampAtLastFilterChange | **not yet exposed** |
| 21 | TimestampAtLastFilterChangePrompt | **not yet exposed** |
| 22 | NumberOfFilterChanges | GetFilterStatus — redundant if added to SPL |

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
| `0x51` | GetStoredCommonSetting(id) → 2-byte int | High priority; bridges to CommonSetting IDs 4–12 |
| `0x56` | SetDeviceRegistrationLevel(int) | Low priority; purpose unclear |

---

### Add CommonSetting IDs 4–12 to bridge

Confirmed from iOS app v2.14.1. Fix ID 0 label (`OdourRunOn` → `WaterHardness`).
Add IDs 4–12 to bridge read sequence and expose via REST/MQTT/HACS.
See `.claude/rules/ble-protocol.md` for the full CommonSetting ID table.

---

### Add ProfileSetting IDs 11–14 to bridge

Confirmed from iOS app v2.14.1:
IDs 11 (SeatHeating), 12 (WaterHeating), 13 (DryerFanPower, 5 levels), 14 (LadyOscillation).
Add to `ProfileSettings.py` enum. Add getters to `AquaCleanClient`; expose via REST/CLI.

---

### Wire remaining Commands enum entries (all interfaces)

Remaining: `ToggleOrientationLight` for AcSela — wire to MQTT/HACS/CLI when AcSela is supported.
(Stop, OdourExtraction, OdourExtractionRunOn done in v3.0.6.)

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

### nRF52840 passive sniff of remote control BLE traffic

**Goal:** discover procedure codes and SetCommand codes the phone app never sends.
The physical remote control (`b0:10:a0:68:5c:8b`) connects to the toilet independently
and may use remote-only procedures not visible in any app capture.

**Method:** nRF52840 dongle in Wireshark passive-sniffer mode (REQ_FOLLOW).
**Do NOT attempt direct serial / REQ_FOLLOW via Python** — `tools/archive/sniff.py` is
archived; REQ_FOLLOW does not work with nrfutil v4.x firmware. Only the Wireshark path works.
Operate the remote normally during capture, then analyse ATT Write frames with
`tools/find-geberit-remote.py <capture.pcapng>`. Compare against `.claude/rules/ble-protocol.md`.

See `docs/developer/protocol-discovery.md` for full context.

---

## Performance

### Reduce poll query time from ~2.7 s to ~0.5 s

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

### Alba: session caching — skip KE handshake on every poll

The bridge currently does a full Arendi handshake on every poll.
The Geberit Home App does one KE per user session and stays connected.

**Benefits:**
1. **Performance**: skips 5 round trips of cryptographic operations per poll.
2. **Remote displacement fix**: reduces keyset-0 KE count from 1-per-poll to 1-per-device-boot (Hypothesis A).

**Implementation**: after a successful KE, persist session keys and counters on the coordinator
between polls. Invalidate cache when `nonce2` in EP Response changes (= device power-cycle).

**Diagnostic prerequisite before coding**: set poll interval to 3600s. If displacement moves from
~2.5 min to ~5 hours, session caching is the right fix. See `memory/alba-session-caching-fix.md`.

---

## Debugging / Observability

### SQLite change log + raw data debug panel

Goal: log every raw value change from every Geberit procedure to disk.
Schema: `sessions`, `changes`, `annotations` tables with WAL mode.
Annotation endpoint: `POST /debug/annotate` — key feature for protocol analysis.
Web UI: "Live values" tab (SSE-driven) + "Change history" tab (REST-driven).
HACS: use HA recorder instead (diagnostic sensor entities).
Implementation order: SQLite → annotation REST → live values tab → change history tab → all procs → HACS sensors → export command.

---

## HACS

### HACS custom integration (Home Assistant, no MQTT)

**Goal:** Native HA integration installable via HACS — no MQTT broker required.

**Approach:** thin `custom_components/geberit_aquaclean/` adapter in this repo.
A `DataUpdateCoordinator` calls `AquaCleanClient` directly from the existing pip package —
zero protocol code duplicated. The package remains the single source of truth for BLE comms.

Two options for the BLE transport layer:
- **Option A** (recommended first): use `BluetoothLeConnector` directly, bypassing HA's `bluetooth` domain.
- **Option B**: integrate with HA's `bluetooth` domain via `bleak-esphome` + `habluetooth`. ~4× more effort.

Standalone bridge + MQTT path fully preserved alongside.

---

### HACS: model-aware entity visibility

Only register entities that apply to the connected device model. Currently all entities
are registered at setup regardless of which model is connected, so Mera Comfort users see
Alba-only entities (and vice versa) as unavailable clutter.

**Approach:**
- Detect the connected model from coordinator data (SAP number / device series / `is_variant_a` flag)
- Split entity lists into model-specific subsets: `ENTITIES_MERA_COMFORT`, `ENTITIES_ALBA`, `ENTITIES_ALL`
- Register only the matching subset in `async_setup_entry`; skip entities whose model
  requirement does not match the detected device

This removes grey/unavailable entities from the HA dashboard without requiring the user
to manually hide them.

---

### HACS Alba: configurable DpId polling frequency

`_ALBA_SLOW_POLL_EVERY = 10` is hardcoded.
**Option A** (recommended first): expose as user setting in options flow (~0.5 session).
**Option B** (recommended long-term): per-group frequency (live/stored/identification/statistics/descaling).

---

### HACS open items

- **Config flow: validate ESPHome host field syntax** — malformed IP passes `cv.string` but fails at aioesphomeapi. Add validator for `CONF_ESPHOME_HOST`.
- **`sensor.geberit_aquaclean_ble_state`** — intra-poll BLE cycle tracking. Self-push via `async_write_ha_state()` only — do NOT call `coordinator.async_update_listeners()` mid-poll.
- **Poll countdown sensor** — `next_poll` timestamp from `poll_epoch` + `poll_interval`.
- **RSSI tracking** — add `ble_rssi` and `wifi_rssi` to PollStats and HACS coordinator.
- **Integration version sensor** — read from `manifest.json`, expose as DIAGNOSTIC entity.
- **Multilingual support (EN/DE/FR/IT)** — `strings.json` + translation files; replace `_attr_name` with `_attr_translation_key`. ~1 session.
- **Download button for Performance Statistics panel** — Option 1 (quickest): `custom:button-card` + inline JS.

---

## CLI / Setup

### `--scan` CLI command — BLE device discovery

**Goal:** Let users discover the Geberit's BLE MAC address without manually scanning with
external tools, as part of first-time setup.

**Usage (proposed):**
```bash
aquaclean-bridge --scan                              # local BLE adapter
aquaclean-bridge --scan --esphome-host 192.168.0.160 # via ESPHome proxy
```

**Behaviour:** scan ~10 s, filter Geberit/AC PRO devices, print MAC + RSSI table,
print ready-to-paste `config.ini` snippet, exit.

**DRY note:** scan logic in one place inside the package; CLI and `ble-scan.py` are consumers only.
Auto-select ESPHome path when `[ESPHOME] host` is set in `config.ini` or `--esphome-host` is passed.

---

## Code quality / Maintenance

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

### install.sh: show progress during slow pip steps

Print "This may take several minutes on Raspberry Pi…" before each slow pip step
and add `--timeout 60` to pip commands.

---

### Entity reference doc generator for Mera Comfort

`tools/generate-hacs-entity-docs.py` covers Alba entities with Availability column but no protocol reference.
A parallel `tools/generate-mera-entity-docs.py` should generate `docs/developer/mera-entity-reference.md`
from the Mera entity lists (`BINARY_SENSORS`, `PROFILE_NUMBERS`, `COMMON_NUMBERS`, `BUTTONS`).
Column layout: Entity ID | Friendly Name | Protocol reference (e.g. `SPL index 0`, `ProfileSetting ID 2`).
See `tools/generate-hacs-entity-docs.py` for the pattern to follow.

---

### Agentic BLE protocol fuzzer

New script `tools/geberit-ble-fuzz.py` with modes:
`--mode read-procs` / `--mode setcommand` / `--mode common-settings` / `--mode profile-settings`.
Reuses `BluetoothLeConnector` + `AquaCleanClient`. Safe defaults: skip dangerous SetCommand
codes (33–36, 4, 37, 6–9) unless `--unsafe` is passed.

---

## Resolved / implemented items

See `.claude/rules/archive.md`.
