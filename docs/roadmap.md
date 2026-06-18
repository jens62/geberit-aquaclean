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

### Wire AcSela-specific features: StateOrientationLight, ConnectedSsmDevices, ToggleOrientationLight

Requested in [issue #27](https://github.com/jens62/geberit-aquaclean/issues/27#issuecomment-4642370701).
All three are **AcSela only** — must be guarded by model detection (see "HACS: model-aware entity visibility").

| Feature | Protocol | Notes |
|---------|----------|-------|
| `StateOrientationLight` | SPL index 9 | Live on/off state; **⚠️ DO NOT query on Mera Comfort** — permanently corrupts `GetFilterStatus` until power-cycle |
| `ConnectedSsmDevices` | SPL index 100 | Bitmask: bit0=FlushTrigger, bit1=OdourExtraction, bit2=OrientationLight; AcSela fw≥4 / AcMeraComfort fw≥23 |
| `ToggleOrientationLight` | `SetCommand` code 20 | Toggle on/off; **AcSela ONLY** — confirmed from factory source; does NOT work on Mera Comfort |

Implementation order:
1. Add SPL index 9 to `SPL_PARAMS_ACSELA` (separate list from `SPL_PARAMS_MERA_COMFORT`)
2. Add SPL index 100 to AcSela + AcMeraComfort fw≥23 lists
3. Wire `ToggleOrientationLight` (SetCommand 20) to REST/MQTT/HACS behind model guard
4. Expose `StateOrientationLight` and `ConnectedSsmDevices` as HACS sensors (AcSela only)

---

### Wire remaining Commands enum entries (all interfaces)

(Stop, OdourExtraction, OdourExtractionRunOn done in v3.0.6. ToggleOrientationLight moved to AcSela task above.)

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

Profiled from TRACE log 2026-04-23. Steady-state poll breakdown after v3.0.9:

| Phase | Time | Every poll? | iPhone does this? |
|-------|------|-------------|-------------------|
| 8× SubscribeNotifications (unlock) | ~2,400 ms | Yes | No — once per app lifetime |
| 11× GetStoredProfileSettings (0x53) | ~2,200 ms | **Once per boot (cached since v3.0.9)** | No — once at session init |
| 7× GetStoredCommonSettings (0x51) | ~1,400 ms | **Once per boot (cached since v3.0.9)** | No — once at session init |
| GetSystemParameterList | ~410 ms | Yes | Yes |
| BLE connect + wait_for_info_frames | ~700–1,300 ms | Yes | ~150 ms |

**Fix 1** — ~~Cache GetStoredProfileSettings + GetStoredCommonSettings~~ — **Done in v3.0.9** (~3.6 s saved after first poll).
**Fix 2** — Keep BLE connection alive between polls (~2.4 s saved per poll):
do not disconnect at poll end; reconnect only on error or timeout. SubscribeNotifications
runs once at connect time and is reused across all polls until the connection drops.
Equivalent to persistent BLE mode scoped to the poll interval.
Expose as a **"Stay connected" toggle in the HACS options flow** — off by default
(on-demand is safer when BLE adapter is shared); on = persistent between polls.
**Fix 3** — Reduce GetFilterStatus timeout from 5 s to 2 s.

---

### Alba: persistent BLE connection + real-time NOTIFY (behave like the Geberit Home App)

**Root insight from mock development**: The Geberit Home App does one KE per user session and
stays connected. It never disconnects between polls. It subscribes `NOTIFY_ENABLE` for
`DpId=607` (`USER_DETECTION_STATUS`) and `DpId=564` (`ANAL_SHOWER_STATUS`) immediately after
inventory and receives real-time push events — it never polls those two DpIds via SPL.

The bridge currently connect → KE → Inventory → reads → **disconnect** on every poll (every 30 s).
This means:
- A new ECDH KE handshake runs on every single poll.
- NOTIFY frames sent between polls are missed entirely.
- The device is occupied for ~14 s out of every 30 s interval.

**The correct approach — match the app:**

1. **Persistent BLE connection** for the Alba coordinator path: connect once, keep the BLE link
   alive, reconnect (with full KE) only on drop. Fast/slow reads still run on the poll schedule;
   the connection is not torn down between them. This is analogous to the existing
   `ble_connection = persistent` mode for Mera (`ServiceMode`), applied to the HACS coordinator.

2. **NOTIFY subscriptions** on that connection: subscribe to `DpId=607` and `DpId=564`
   immediately after inventory. Incoming notifications trigger `async_write_ha_state()`
   immediately — independent of the poll schedule. User-sitting and shower-running become
   real-time sensors rather than 30-second-lag sensors.

**Why NOTIFY alone is not enough**: if the connection is torn down after each poll, all
NOTIFY frames sent by the device between polls are lost. Persistent connection and NOTIFY
are inseparable — implementing NOTIFY on the current on-demand model adds no real value.

**With persistent connection, session key caching becomes irrelevant**: one KE per device boot,
exactly like the app. The separate session-caching item below can be de-prioritised.

**Mock validation**: the autonomous `DP_USER_DETECTION_STATUS` NOTIFY loop (see mock roadmap
item below) is the correct test for this feature — it sends real NOTIFY frames to a
persistently-connected client, validating the coordinator's push-update path end-to-end.

**Implementation pieces:**
- `coordinator.py`: background task maintains the BLE connection; reconnect with circuit breaker
  on drop; reuse `_alba_inventory` cache so only fast/slow DpId reads run each poll.
- After connect: `ble20.enable_notification([607, 564])`; register callback that calls
  `async_write_ha_state()` on the relevant binary sensors without waiting for the next poll.
- `BluetoothLeConnector`: expose a `keep_connected` mode (do not call `disconnect()` between
  BLE operations; only disconnect on explicit teardown or error).

---

### Alba: session caching — skip KE handshake on every poll

> **Superseded by the persistent-connection item above** if that is implemented first.
> Session caching only makes sense in the on-demand connection model.

The bridge currently does a full Arendi handshake on every poll.
**Important caveat**: the Geberit Home App never does session resumption — it stays connected
and never reconnects mid-session. We have no evidence the device accepts a reconnect that
skips the KE step. Implementing session caching means teaching the bridge something the app
does not do, and validating it against the real device (not the mock, which was built from
observed app behaviour).

**Diagnostic prerequisite before coding**: set poll interval to 3600s. If displacement moves
from ~2.5 min to ~5 hours, session caching is the right fix. See `memory/alba-session-caching-fix.md`.

**Only pursue if persistent connection (above) is not implemented first.**

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

BLE transport: uses `BluetoothLeConnector` directly (Option A) or HA's `bluetooth` domain
via `habluetooth` (Option B, `use_ha_bluetooth = true`). Both are implemented and shipped in v3.1.0.

Standalone bridge + MQTT path fully preserved alongside.

---

### HACS: expose cached stored settings as entities with explicit refresh

`GetStoredProfileSettings` (11 settings) and `GetStoredCommonSettings` (7 settings) are
fetched once per integration load and then cached. Their values should be visible as HACS
entities and re-queryable on demand.

**Entities to add** (all as `number` or `select` entities, DIAGNOSTIC or user-facing):
- All 11 `ProfileSettings` entries (OdourExtraction, AnalShowerPressure, WaterTemperature, …)
- All 7 `CommonSettings` entries (WaterHardness, OrientationLightBrightness, Language, …)

**Explicit refresh button:**
Add a `button` entity "Refresh cached settings" that clears `_mera_profile_settings_cache`
and `_mera_common_settings_cache` on press and triggers an immediate coordinator refresh.
This lets the user force a re-read after changing settings via the Geberit Home app (which
the bridge cannot detect automatically).

---


### HACS Alba: configurable DpId polling frequency

`_ALBA_SLOW_POLL_EVERY = 10` is hardcoded.
**Option A** (recommended first): expose as user setting in options flow (~0.5 session).
**Option B** (recommended long-term): per-group frequency (live/stored/identification/statistics/descaling).

---

### HACS: poll interval slider

Add a `number` entity (or options-flow field) to control the poll interval from the HA UI
without editing `config.ini` or restarting the integration.

**Suggested entity:** `number.geberit_aquaclean_poll_interval` — range 10–3600 s, step 10 s,
DIAGNOSTIC category. Writing the entity calls `coordinator.async_set_poll_interval(value)`
which updates the in-memory interval and reschedules the next update.

---

### HACS open items

- **Config flow: validate ESPHome host field syntax** — malformed IP passes `cv.string` but fails at aioesphomeapi. Add validator for `CONF_ESPHOME_HOST`.
- **`sensor.geberit_aquaclean_ble_state`** — intra-poll BLE cycle tracking. Self-push via `async_write_ha_state()` only — do NOT call `coordinator.async_update_listeners()` mid-poll.
- **Poll countdown sensor** — `next_poll` timestamp from `poll_epoch` + `poll_interval`.
- **RSSI tracking** — add `ble_rssi` and `wifi_rssi` to PollStats and HACS coordinator.
- **Integration version sensor** — read from `manifest.json`, expose as DIAGNOSTIC entity. Also log the version as INFO in the HA log at integration startup (alongside firmware version).
- **Multilingual support (EN/DE/FR/IT)** — `strings.json` + translation files; replace `_attr_name` with `_attr_translation_key`. ~1 session.
- **Download button for Performance Statistics panel** — Option 1 (quickest): `custom:button-card` + inline JS.
- **Timestamp in "Failed setup, will retry" UI card** — HA's `config_entries` UI displays the `ConfigEntryNotReady` message string without a timestamp. Embedding `(at HH:MM:SS)` into each `UpdateFailed` raise in `coordinator.py` would propagate to the card. 6 E0003 raise sites; only the initial onboarding path surfaces as `ConfigEntryNotReady`.
- **Suppress onboarding E0003 ConfigEntryNotReady traceback (optional)** — the full exception traceback logged by HA during `async_config_entry_first_refresh` retry (E0003 / inventory timeout) is HA-internal and cannot be suppressed directly. Alternative: add an internal retry loop in `_async_update_data` for E0003 during onboarding (retry 2–3× with short sleep), so HA never sees `ConfigEntryNotReady` for transient BLE timeouts. Trade-off: cleaner logs vs longer first-boot time (~35 s × retries before surfacing). Only visible to users with DEBUG logging enabled.

---

### HACS / Lovelace: modular card-based dashboards per device model

Provide ready-made Lovelace dashboards composed from modular card files, one card file
per feature set, one dashboard file per device model as pure `!include` composition.

**Design goals:**
- Zero duplication: every card is defined once; model dashboards are pure assembly files.
- Matches 1:1 with the entity feature sets defined above — if an entity set exists, a card
  file exists for it.
- Users pick the dashboard for their model and drop it into HA; no manual editing required
  beyond optional entity prefix substitution.

**Card file → feature set mapping:**

| Card file | Feature set | Contents |
|-----------|-------------|----------|
| `card-all.yaml` | ALL | Status sensors, descaling actions, filter status, identification panel |
| `card-with-lady-shower.yaml` | WITH_LADY_SHOWER | Lady shower toggle + settings |
| `card-with-dryer.yaml` | WITH_DRYER | Dryer toggle + settings |
| `card-with-dryer-fan.yaml` | WITH_DRYER_FAN | Dryer fan power/intensity |
| `card-with-odour-extraction.yaml` | WITH_ODOUR_EXTRACTION | OE toggle + run-on setting |
| `card-with-seat-heater.yaml` | WITH_SEAT_HEATER | Seat heater setting |
| `card-with-water-heater.yaml` | WITH_WATER_HEATER | Water heater setting |
| `card-mera-comfort-only.yaml` | MERA_COMFORT_ONLY | Orientation light (proc 0x0B), lid sensor/auto-open/close |
| `card-sela-only.yaml` | SELA_ONLY | Orientation light (SetCommand 20), SPL 9 state, LightSensorSensitivity |
| `card-cama-only.yaml` | CAMA_ONLY | Draining command + status |
| `card-alba-only.yaml` | ALBA_ONLY | Alba DpId entities (all current Alba cards) |

**Per-model dashboard files (pure `!include` composition):**

```yaml
# dashboard-mera-comfort.yaml
title: Geberit Mera Comfort
views:
  - title: Status
    cards:
      - !include cards/card-all.yaml
      - !include cards/card-with-lady-shower.yaml
      - !include cards/card-with-dryer.yaml
      - !include cards/card-with-dryer-fan.yaml
      - !include cards/card-with-odour-extraction.yaml
      - !include cards/card-with-seat-heater.yaml
      - !include cards/card-with-water-heater.yaml
      - !include cards/card-mera-comfort-only.yaml
```

Dashboard files in the repo: `lovelace/cards/card-*.yaml` + `lovelace/dashboard-*.yaml`.

**`!include` path resolution — mandatory caveat:**

HA resolves `!include` paths **relative to `/config/`** (the HA config root), NOT relative
to the dashboard file. If the dashboard file is at
`/config/lovelace/dashboard-mera-comfort.yaml`, every `!include` in it must be written as:

```yaml
!include lovelace/cards/card-all.yaml   # ✅ relative to /config/ root
!include cards/card-all.yaml             # ❌ would look in /config/cards/ — not found
```

Document this in a comment at the top of every dashboard file and in
`homeassistant/SETUP_GUIDE.md`.

**YAML mode requirement:**

`!include` only works in [YAML mode](https://www.home-assistant.io/dashboards/dashboards/#using-yaml-for-your-dashboards).
Add the dashboard declaration to `configuration.yaml`:

```yaml
lovelace:
  mode: yaml
  dashboards:
    geberit-mera-comfort:
      mode: yaml
      filename: lovelace/dashboard-mera-comfort.yaml
      title: Geberit Mera Comfort
      icon: mdi:toilet
      show_in_sidebar: true
      require_admin: false
```

**Python installer with entity prefix substitution:**

The default entity ID prefix (`geberit_aquaclean`) changes when the user renamed the
integration entry. The installer script handles this:

```bash
python tools/install-lovelace.py \
  --model mera-comfort \
  --ha-config /config \
  --entity-prefix my_toilet        # optional, default: geberit_aquaclean
```

What the script does:
1. Creates `/config/lovelace/` and `/config/lovelace/cards/` if absent.
2. Copies all `card-*.yaml` files, rewriting `geberit_aquaclean_` → `<prefix>_` in entity IDs.
3. Copies the model dashboard file, rewriting the same prefix in entity references.
4. Prints the `configuration.yaml` snippet to paste.

**Packaging approach:**

- The card and dashboard YAML files live in `lovelace/` in the repo — checked in as examples,
  not auto-installed.
- The installer script lives in `tools/install-lovelace.py`.
- Users run the installer once at setup; on upgrade they re-run it to pick up new cards.
- Do NOT auto-install via HACS — `!include` requires YAML mode (a deliberate user choice)
  and the entity prefix may differ from the default.
- `homeassistant/SETUP_GUIDE.md` covers both YAML-mode manual setup and the installer path.

**Effort: ~1 day** (card files for all sets + model dashboard files + installer script +
docs update).

**Risk: low.** Card files are YAML only — no Python changes. The installer is a standalone
`tools/` script. Existing users are unaffected until they opt in.

---

## CLI / Setup

### Orientation Light card — richer UI in Lovelace dashboards

Replace the current plain `number` sliders for the Orientation Light card with a
purpose-built UI using HACS frontend cards:

- **Activation** — `select` entity or dropdown rendered via the existing
  `number.geberit_aquaclean_orientation_light_activation` (0=Off, 1=On, 2=When Approached)
- **Brightness** — `custom:number-box-card` with +/− buttons instead of a slider
- **Colour** — `custom:rgb-light-card` with 7 colour dots matching the seven
  `OrientationLightColour` values (Blue, Turquoise, Magenta, Orange, Yellow, WarmWhite, ColdWhite)

Prerequisites (HACS Frontend):
- `custom:number-box-card`
- `custom:rgb-light-card`

The colour entity would need a backing `light` entity or a workaround that maps the
7-value `number.geberit_aquaclean_orientation_light_color` to an `rgb_color` the card
can read and write. Options: HA template light, input_select + automation, or a custom
entity in the integration that exposes the 7 colours as a proper `light` with `color_mode`.

---

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

## Testing / CI

### Automated tests as GitHub Actions

Investigate what can be tested without real BLE hardware and wire it into a GitHub Actions
workflow so tests run on every push.

Candidates:
- **Unit tests** — `pytest` on pure-Python logic: frame encoding/decoding (`FrameService`),
  error code formatting (`ErrorManager`), config validation (`_check_config_errors`),
  firmware version parsing (`_parse_rs_ts`, `_find_series_and_variant`).
- **Protocol tests against mock device** — run the bridge against `tools/mock-geberit-alba.py`
  (and future `mock-geberit-sela.py`) in the same CI job; assert that a full poll cycle
  completes and returns expected data shapes.
- **HACS integration smoke test** — `pytest-homeassistant-custom-component` can load the
  custom component against a mocked coordinator; assert entity states without BLE.
- **Import / type check** — `python -m py_compile` on all modules + `mypy --ignore-missing-imports`
  to catch regressions without a test harness.
- **Firmware download script** — `--list` against the real Geberit cloud API (read-only,
  no credentials); assert the catalogue is non-empty and series 248 is present.

Suggested workflow file: `.github/workflows/ci.yml`, triggered on push to `main` and on PRs.
Start with the cheapest tests (import check, unit tests) and add mock-device tests once the
Sela mock server exists.

---

## Code quality / Maintenance

### Refactor web UI (`static/index.html`)

`aquaclean_console_app/static/index.html` is a single monolithic file that has grown too
large to maintain. It is also feature-incomplete relative to the HACS integration.

Suggested split:
- Extract CSS into `static/style.css`
- Extract JavaScript into one or more `static/app.js` / `static/panels/*.js` files
- Break HTML into logical sections (connection, controls, descaling, filter, settings, debug)
- Bring feature parity closer to HACS: orientation light panel, odour extraction,
  firmware info, performance stats

---

### Refactor `main.py`

`aquaclean_console_app/main.py` is far too long — it contains config parsing, REST route
handlers, SSE logic, BLE orchestration, MQTT wiring, and HA discovery all in one file.

Suggested split (each becomes its own module under `aquaclean_console_app/`):
- `config.py` — config loading, validation, `_check_config_errors()`
- `ha_discovery.py` — `get_ha_discovery_configs()`, publish/remove HA discovery
- `api_handlers.py` — REST endpoint implementations (currently inline lambdas / methods on `ApiMode`)
- `service_mode.py` — `ServiceMode` class (already partially isolated; move fully)
- `api_mode.py` — `ApiMode` class

`main.py` should become a thin entry point: load config, wire services, start the event loop.

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

### Mock alba web UI: SSE push for live state display

The control web UI (`--web-port`) currently uses a 2 s `<meta http-equiv="refresh">` to
show live DpId 564/607 values. Replace with SSE so the browser updates in real time without
page flicker.

**Implementation:**
- Add `_sse_clients: list[asyncio.Queue]` (one queue per connected browser tab).
- `GET /events` → `EventSourceResponse` generator that streams from the client queue.
- `_broadcast_sse(state: dict)` helper: puts `{"564": v564, "607": v607}` into every queue.
- Call `_broadcast_sse` from: toggle POST handler, `_stop_sequence`, `_write` DpId 563 path.
- Pass a `broadcast_fn` callback into `_Ble20AppLayer.__init__` (same pattern as `notify_queue`).
- JS: `const es = new EventSource('/events'); es.onmessage = e => updateStatus(JSON.parse(e.data))`.
- Remove `<meta http-equiv="refresh">`.

**Effort:** ~50 lines. No new dependencies (`sse-starlette` already available).

---

### Mock alba: SQLite persistence for DpId store

**Requirement:** Replace the in-code `_DEFAULT_STORE` list with a SQLite database so
mock state survives restarts and reflects all changes made via the Geberit Home App or
HACS integration.

- On startup: load DpId store from DB; seed with `_DEFAULT_STORE` defaults if the DB
  is empty or missing.
- All `_write` calls persist `entry['value']` changes to the DB immediately.
- Restart resumes with last-written values — app/HACS changes are durable.
- Web UI: full table of all 78 DpIds with current value, datatype, behavior, min/max;
  inline per-row edit (HTML form); **Reset to factory defaults** button (truncates + reseeds
  from hardcoded defaults).

**Effort:** ~3–4 hours, ~150–200 lines.

| Piece | Effort |
|-------|--------|
| SQLite schema — one table mirroring `_DEFAULT_STORE` columns | small |
| Startup: load from DB, seed defaults if empty | small |
| `_write` hook: persist every `entry['value']` change | small |
| Web UI: full 78-DpId table with current values | medium |
| Web UI: inline per-row edit (one `<form>` per row) | medium |
| Web UI: "Reset to factory defaults" button | small |
| Wiring into `_stop_sequence` and per-session store setup | small |

**Main decision:** per-row POSTs (simple, composable with existing toggle pattern) vs
bulk-edit form. Per-row is the natural fit.

**Dependency:** `sqlite3` is stdlib — no new packages.

**Complication:** `_DEFAULT_STORE` carries typed metadata (datatype, behavior, min, max).
The DB must store these columns so "Reset to factory" can truncate + reseed without
a code lookup, and so the web UI can render appropriate input controls per datatype.

---

### Mock alba: autonomous `DP_USER_DETECTION_STATUS` notification to enable Remote Control

After Phase 3 `Initialize()` completes, the Geberit Home App subscribes `NOTIFY_ENABLE` for
`DpId=607` (`DP_USER_DETECTION_STATUS`).  Until a notification fires with value `b'\x01'`,
the app's **Remote Control** section keeps all shower buttons disabled (user not sitting).

**Planned behaviour:** after Phase 3 Initialize() finishes, the mock starts an asyncio task
that toggles `DP_USER_DETECTION_STATUS` every 45 s:
- `b'\x01'` → user sitting → Remote Control shower buttons enabled
- `b'\x00'` → user not sitting → shower buttons disabled

**Implementation:**
- Detect Phase 3 complete: `_ble_session_phase == 3` and `_disc_sent` still False (Initialize done,
  not yet finished).  Better anchor: watch for Phase 3 KE + Inventory complete (same signal
  that sets `_ble_session_phase = 3`).
- Spawn `asyncio.create_task(_user_sitting_loop())` at that point.
- `_user_sitting_loop()`: alternates state every 45 s; on each tick sends a `NOTIFY` frame for
  DpId=607 via `send_notify` callback; stops when BLE disconnects.
- Also subscribe DpId=564 (`DP_ANAL_SHOWER_STATUS`) notification: push `b'\x01'` when
  shower would "run" (value > 0 in the sitting window), `b'\x00'` when not running.

**Why:** validates the full Remote Control UI path in the app — confirms the mock handles
the entire settings + live-control flow, not just save/registration.

---

### Mock server for AquaClean Sela — testing without real hardware

Build `tools/mock-geberit-sela.py` analogous to the existing `tools/mock-geberit-alba.py`.
Allows integration and coordinator testing without a physical Sela device.

Implementation notes:
- Reuse the Arendi/Ble20 layer from `mock-geberit-alba.py` where applicable (same BLE
  advertisement format); Sela uses AquaCleanV1 (same GATT UUIDs as Mera Comfort), not Ble2V1.
- Respond to the same GATT procedures the bridge calls: `GetSystemParameterList` (0x0D),
  `GetStoredProfileSetting` (0x53), `GetStoredCommonSetting` (0x51), `GetDeviceIdentification`
  (0x82), `GetFilterStatus` (0x59), `GetSOCApplicationVersions` (0x81),
  `GetFirmwareVersionList`, `SetCommand` (0x09).
- Use realistic Sela-specific SPL values: include SPL index 9 (StateOrientationLight) and
  SPL index 100 (ConnectedSsmDevices) in responses — both AcSela-only.
- Firmware version: RS08.0 TS57 (node 0x01) — from msperl's confirmed Sela.
- Advertise article prefix `146.22` in manufacturer-specific data so the bridge detects
  `AcSela` variant at scan time.

Consider analysing the Sela firmware before building the mock:
- Node 0x01 (`0x01_decompiled.c`, 18,739 lines) — main controller; contains SPL dispatcher,
  procedure handlers, and the CommonSetting/ProfileSetting switch tables.
- Sela-specific nodes not present in Mera Comfort: 0x0F (Durchlauferhitzer / tankless heater).
- BLE controller (node 0x00) is identical to Mera Comfort (same binary, RS10 TS18).
- Decompiled output: `local-assets/firmware/FwPkg_F806_V8.2.57.251023_0650871a_Sela_F8_06_RS_08_02_TS_57_extracted/`

### Agentic BLE protocol fuzzer

New script `tools/geberit-ble-fuzz.py` with modes:
`--mode read-procs` / `--mode setcommand` / `--mode common-settings` / `--mode profile-settings`.
Reuses `BluetoothLeConnector` + `AquaCleanClient`. Safe defaults: skip dangerous SetCommand
codes (33–36, 4, 37, 6–9) unless `--unsafe` is passed.

---

## Resolved / implemented items

See `.claude/rules/archive.md`.
