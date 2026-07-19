# Roadmap & Open Tasks

Planned features, improvements, and known bugs to fix.

---

## Bug fixes

### Fix: SPL parameter mislabeling (LidOffset / ShowerArmOffset)

**Prerequisite resolved (2026-06-26, nRF52840 capture)**: The iOS app sends SPL
`[0,1,2,3,4,5,6,7,8,9,10,11]` — indices 12 and 13 are **not** in the iOS SPL list.
LidOffset/ShowerArmOffset are at indices 104/105 (unconfirmed queryability). The fix
below is unblocked.

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

### Geberit AquaClean application-layer BLE relay to overcome "BLE Coexistence" issues

The real Alba (and Mera Comfort) only accept **one BLE connection at a time**. This causes
the displacement/coexistence problem: when the bridge polls, the Geberit Home App gets
disconnected; when the remote control is used, the bridge gets displaced.

**Proposed architecture — "Alba-Hub":**

Combine the two existing components (bridge central + mock peripheral) into a relay:

- **Central side**: maintains a permanent BLE connection to the real Alba device
  (reuses existing `AlbaClient` / `AquaCleanClient`)
- **Peripheral side**: impersonates the Alba to multiple clients simultaneously —
  Geberit Home App, Geberit Remote Control, standalone bridge (reuses mock peripheral
  GATT server and `_AriendiServerSide`)
- **Relay logic** (new): decrypt incoming DpId operations from each client → re-encrypt
  for the real device → forward; decrypt response → re-encrypt per-client → fan out

The relay cannot be transparent at the BLE link layer because Arendi uses ECDH with
fresh ephemeral keys per session — each client has its own session key. The hub must
operate at the application (DpId) layer: decrypt, re-encrypt, forward.

**What this solves:**

| Problem | Solved? |
|---------|---------|
| App displaced during bridge polls | ✅ App connects to hub; hub never disconnects from real device |
| Multiple clients coexist | ✅ Hub serializes writes, fans out notifications |
| Remote control displacement (issue #21) | ⚠️ Only once remote PSK (keyset_id=1) is known |

**Existing code that maps directly:**

| Hub component | Existing code |
|---------------|---------------|
| Central (real device) | `AlbaClient` / `AquaCleanClient` + `BluetoothLeConnector` |
| Peripheral GATT server | `mock-geberit-alba.py` `_BlePeripheral` |
| Arendi KE + crypto | `_AriendiServerSide` in mock |
| DpId relay + notification fan-out | **New** — ~300–500 lines |

**Key implementation notes:**

- Hub peripheral must advertise with the real device's MAC (MAC spoof via
  `btmgmt public-addr`) so existing App and Remote pairings remain valid
- DataPointInventory (78 DpIds, ~15 s) — run once on first central connection,
  cache, serve instantly to all clients without hitting the real device
- Subscribe to all notifications on real device → fan out to all connected clients
- Write serialisation: queue writes from concurrent clients, apply in order
- For the Remote (keyset_id=1): hub needs the remote PSK to decrypt/re-encrypt;
  PSK currently unknown — see
  `docs/developer/mock-geberit-alba.md#blocker-2--keyset_id1-psk-unknown`

**Status:** design only — not yet started.

---

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
| `StateOrientationLight` | SPL index 9 | Live on/off state; AcSela only — returns 0 on Mera Comfort (safe, no corruption — confirmed nRF capture 2026-06-26) |
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

**Note:** this is about exposing `SetCommand` codes through the bridge's REST/MQTT/CLI/HACS
interfaces. A separate, unrelated gap exists on the *mock* side — the Mera mock itself doesn't
simulate most `SetCommand` codes either (only `ToggleAnalShower`/`ToggleLadyShower` have any
effect), so testing against a mocked device won't currently exercise these even once bridge-side
wiring exists. Tracked as `docs/developer/mock-service-requirements.md` §9 / Phase 10.

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


### HACS Alba: variant-based entity filtering

The coordinator already filters entities by device model using `DEVICE_TYPE_FEATURE_SETS`
(12 `FS_*` constants, one frozenset per model). Alba currently has a single feature set
(`FS_ALBA_ONLY`) that applies to all Alba variants — some entities may not be present on
all hardware sub-models (e.g. variant 0 / kstr 250 has no lady shower or dryer).

`DP_DEVICE_VARIANT` (DpId 1) is read during identification and is available in the
coordinator result. It could be stored in the config entry (alongside `CONF_DEVICE_TYPE`)
and used to select a variant-specific frozenset.

**Blocker — mapping unknown.** Only one Alba variant is confirmed, and it is unknown
whether other variants exist at all. That said, it is not unlikely: `DP_DRY_RUN_MODE`
(DpId 810), `DP_STATISTIC_COUNTER_TOTAL` instances for lady shower and dryer (DpIds 33/34),
and the lady-shower / dryer toggle DpIds are all present in the firmware's service
discovery — meaning the hardware was designed to support them on at least some Alba variants.

| Variant | Lady shower | Dryer | Dry run mode | Confirmed by |
|---------|------------|-------|--------------|--------------|
| 0       | ❌          | ❌     | ❌            | MuusLee (kstr Alba 250, series=250, 2026-06-27); eodabas (series=250, 3 devices: SB2501EU139773/139775/139776, 2026-06-29) |
| other   | ❓          | ❓     | ❓            | no other variant reported yet |

To extend the table, ask an Alba user with a different variant to answer two questions:

1. **What is your device variant?**
   Enable debug logging for `custom_components.geberit_aquaclean` in HA, trigger a poll,
   then search the log for `variant=`. The identification line looks like:
   `Ble20: identification — series=250 variant=0 model=None name=None fw_rs=03 sap=… product_serial=…`

2. **Which of these HACS entities show a real value (not "unavailable")?**
   - `binary_sensor.*_dry_run_mode` — on/off
   - `sensor.*_total_lady_shower_uses` — number > 0 or always 0?
   - `sensor.*_total_dryer_uses` — number > 0 or always 0?
   - `switch.*_lady_shower` — does toggling it do anything on the device?
   - `switch.*_dryer` — same

   Cross-check: **Does the Geberit Home App show a lady shower button and a dryer button
   for your device?** If the app hides them, the hardware is absent.

Once variant-to-capability data exists for 2+ variants, add variant-specific `FS_ALBA_VARIANT_X`
constants and store the detected variant in the config entry (same mechanism as `CONF_DEVICE_TYPE`).

**Precedent:** Sela support (variant=6) was confirmed by a real user in issue #27 before
any variant-specific filtering was implemented — the same approach applies here.

---

### Wire DP_WATER_HARDNESS as a new HACS sensor

`DP_WATER_HARDNESS` (DpId 587) already exists in `dp_ids.py` but is not currently read by
any client or exposed as an entity. Confirmed from application source analysis (app v2.14.2)
that this is how Alba/Ble20 devices expose water hardness — a separate mechanism from the
CommonSetting proc 0x51/0x52 ID 0 `WaterHardness` used by AquacleanOld devices (Mera/Sela/etc).
See `docs/developer/model-feature-matrix.md` → "Other `GeberitDeviceType` values".

**Wiring, following the existing descaling-group pattern:**
1. `AlbaBaseClient.get_misc_state_async()` (`aquaclean_console_app/aquaclean_core/Clients/AlbaBaseClient.py`) —
   add `result["water_hardness"] = await _u32(DpId.DP_WATER_HARDNESS)` alongside the other
   descaling-adjacent reads (next to `DP_DAYS_UNTIL_NEXT_DESCALING` / `DP_DESCALING_CYCLES`).
   Static value — no need to add it to the fast-poll subset (`get_misc_state_fast_async()`).
2. `custom_components/geberit_aquaclean/coordinator.py` — add
   `"alba_water_hardness": misc.get("water_hardness")` next to the existing
   `alba_days_until_next_descaling` / `alba_descaling_cycles` lines (~line 864-865).
3. `custom_components/geberit_aquaclean/sensor.py` — add a new entry to the sensor
   descriptions tuple next to the existing `alba_descaling_cycles` row (~line 71):
   `("alba_water_hardness", "Water Hardness", None, None, SensorStateClass.MEASUREMENT, "mdi:water-opacity")  # DpId 587`
   (unit/icon TBD once a real device confirms the value's scale/range — check whether it's
   raw ° dH or an enum like the CommonSetting version, which uses raw 0–2 range).

Also update `homeassistant/configuration_mqtt.yaml` and `get_ha_discovery_configs()` in
`main.py` if this is ever surfaced via MQTT too (per naming-conventions.md MQTT↔HA sync rule)
— but this is Alba/HACS-only for now, so likely not needed unless the standalone bridge
gains Alba support.

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
- **Firmware refresh button** — add a `button` entity "Refresh firmware info" that clears `_firmware_update_result` and `_last_firmware_check_at` on press and triggers an immediate re-query of the Geberit cloud firmware API. Lets the user force a fresh check without waiting for the 1-hour cache interval. See [firmware update check docs](hacs-integration.md#firmware-update-check).
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

### Move `_build_frames` response encoding from mock to `FrameFactory`

`_build_frames(ctx, proc, result, status=0)` in `tools/mock-geberit-mera.py` encodes the
response-side AquaClean frame format: `seg=0x00`, `node_id=0x01`, format selection
(single-frame vs multi-frame), and characteristic routing (A5/A6/A7/A8).

This logic belongs in `aquaclean_core/Frames/` alongside `FrameFactory` — it mirrors the
request-side encoding already there and would be reusable by both the mock and any future
bridge code that needs to construct device-side response frames (e.g. a relay/hub).

**Proposed API:**
```python
# aquaclean_core/Frames/FrameFactory.py  (new static method)
@staticmethod
def build_response_frames(ctx: int, proc: int, result: bytes, status: int = 0) -> list[bytes]:
    ...

# or a companion class
# aquaclean_core/Frames/ResponseFrameFactory.py
class ResponseFrameFactory:
    @staticmethod
    def build(ctx: int, proc: int, result: bytes, status: int = 0) -> list[bytes]: ...
```

The mock would then `from aquaclean_core.Frames.FrameFactory import FrameFactory` and call
`FrameFactory.build_response_frames(...)` instead of the local `_build_frames`.

**Status:** logic currently in `mock-geberit-mera.py` `_build_frames`. Move to bridge when asked.

---

### Split `mera_mock.py` / `alba_mock.py` into packages — both well past the 1,000-line guideline

`aquaclean_ble_relay/mera_mock.py` is 2,414 lines; `aquaclean_ble_relay/alba_mock.py` is
2,196 lines (2026-07-18). Both are well past the standard Python module-length guideline
(sweet spot 100–300 lines, warning at 500, hard code-smell at 1,000 — Pylint's own default
`max-module-lines`; PEP 8 itself is silent on file length, only line length). No
`.pylintrc`/`max-module-lines` is configured in this repo.

Each file currently mixes several distinct responsibilities in one module: device-identity/
firmware constants and codecs, the GATT service class(es), the advertisement class, the
procedure-dispatch/protocol logic, the webui HTML/handlers, and `run()`'s D-Bus setup —
exactly the "can't describe it without 'and'" smell.

**Suggested split per mock** (package `mera_mock/` / `alba_mock/` replacing the single file):
- `__init__.py` — public API (the `MeraMock`/`AlbaMock` class re-export)
- `identity.py` — identity/firmware constants, codecs, `_FACTORY_*` defaults
- `gatt.py` — GATT service class(es), advertisement class
- `protocol.py` — procedure dispatch (`_proc_*` handlers)
- `webui.py` — HTML template, settings-table builder, aiohttp route handlers
- `service.py` (or keep in `__init__.py`) — `run()`'s D-Bus/adapter setup, main loop

**Status:** not started. Flagged as a refactor candidate per `memory/python-module-length-
should-fix.md`; not urgent, doesn't block current feature work, but should be picked up
before either file grows meaningfully larger.

---

### `mock_service.py`: startup self-check for the bluetoothd `--noplugin=battery` systemd override

A standing VM setup requirement (see `docs/developer/test-infrastructure.md` §"BlueZ battery
plugin also initiates its own SMP pairing") is easy to silently lose: a fresh VM, an OS
reinstall, or simply never having applied the systemd drop-in override on a new test machine
all reintroduce the battery-plugin SMP pairing-failure cycle, and nothing currently detects
this — the mock just starts up and behaves oddly (spurious SMP events, possible
disconnect-pattern confusion re-litigating an already-closed investigation).

**Suggested design:** on startup, `mock_service.py` runs something like
`ps aux | grep bluetoothd` (or reads `/proc/<pid>/cmdline` for the running `bluetoothd`) and
warns loudly (not a hard failure — the mock can still run) if `--noplugin=battery` is absent,
pointing at the exact `test-infrastructure.md` section with the fix commands.

**Status:** not started, low priority — the override is already applied and verified on
anneubuntu-studio; this would mainly help future setups on a new/rebuilt machine.

---

### `aquaclean_ble_relay` — eventual graduation to its own repo (deferred 2026-07-19)

Raised by the user: they want to tag `aquaclean_ble_relay` independently from the bridge
(`aquaclean_console_app`), motivated by a longer-term plan — the mock service is a first step
toward a real, user-installable BLE relay application, shipped with its own `install.sh`/
`update.sh` the way the bridge already is (`docs/roadmap.md`'s existing `--scan` CLI item and
`.claude/rules/release-process.md` describe that pattern for the bridge today).

**Answer given:** a plain git tag can't do this — it's just a pointer into the one shared
commit timeline, not a filtered history. `git subtree split -P aquaclean_ble_relay -b
mock-only` genuinely can produce a filtered, mock-only commit history to tag independently,
but subtree's usual failure mode is having to keep re-splitting/re-syncing forever as both
areas keep changing. The cleaner path, when this is actually picked up: use `subtree split`
(or `git filter-repo`) **once** to extract `aquaclean_ble_relay` into its own standalone repo
with history preserved, then version/tag it normally there — no ongoing subtree relationship
to maintain. Also worth remembering: the bridge's actual `update.sh` mechanism curls a pinned
commit SHA, not a tag (per `release-process.md`) — tags matter for human-readable
versioning/changelog, not for the install/update mechanism itself, so this doesn't have to be
solved before an `install.sh`/`update.sh` could exist for the mock.

**Status:** deferred, explicitly — "leave as is for now." Revisit once the mock's feature set
(§4a above, Phase 9b/13, etc.) is more settled; worth a dedicated planning conversation before
acting, not a quick fix. Don't re-raise unprompted; re-read this note if the user brings up
tagging/releasing/installing `aquaclean_ble_relay` again.

---

### Mera mock: "real reference" identity/firmware values are hardcoded to our one test device

`_IDENTITY_REAL_REFERENCE` in `mera_mock.py` (the "real: ..." hint shown next to each
webui-editable identity field) is hardcoded to values confirmed from *our* test device
(serial `HB2304EU298413`, etc. — see `aquaclean-...SILLY.log`). A different user running this
mock against their own real Mera Comfort would have a different serial, possibly different
SAP-number formatting, different firmware-version history — our hardcoded hints would be
actively wrong for them, not just unhelpful. Same concern likely applies to `_FW_COMPONENT_VERSIONS`
(the "real" per-component firmware versions used by the `rs30`/`rs28` profile dropdown).

**Raised by the user 2026-07-18**, explicitly deferred — "leave as is for the time being."

**Second, independent argument for the same fix (also raised 2026-07-18):** security/privacy,
not just portability. `_FACTORY_IDENTITY`/`_IDENTITY_REAL_REFERENCE` (and the equivalent
real-value citations scattered across docs/tools/tests) put one specific real device's
identity — SAP article prefix, serial number, production date, etc. — directly in a public
repo. Not a remotely-exploitable credential like the Alba `PAIRING_SECRET` PIN (no cloud API
ties to it — see `memory/feedback_test_setup_no_cloud_connectivity.md`), but still real
device-identifying data that shouldn't need to live in source at all. The existing scope
(confirmed via `git log -S`, 2026-07-18) is large — real serial/SAP values appear in ~40
files including docs, tools, and tests, present since commit `f1dcf5d` (2026-04-23, ~3 months
before this note), already pushed to the public GitHub repo. **Explicitly deferred alongside
the portability concern — leave as is for now.** Do not re-raise or re-investigate scope
in a future session without being asked; this is a recorded, closed-for-now decision, not an
open question to re-litigate.

**Suggested design when picked up:** move the "real reference" values into the persistence
database (same `mock_persistence` mechanism the actual current-value fields already use),
with the hardcoded dicts only as a fallback default for a fresh install. Add a small script
that connects to a real device once (reusing the standalone bridge's own
`GetDeviceIdentification`/`GetFirmwareVersionList` calls, `aquaclean_console_app`'s existing
client code) and writes what it reads straight into that namespace — so a new user runs the
script against their own device once, and the mock's hints/profiles become personal to them
without editing Python source. This also resolves the security concern: no real device data
would need to ship in source/docs at all, only in the user's own local, gitignored database.

**Status:** not started, intentionally deferred (portability + security, both 2026-07-18).

---

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

### Add DpId 587 to mock-geberit-alba.py's `_DEFAULT_STORE`

`DP_WATER_HARDNESS` (DpId 587) is confirmed missing from `_DEFAULT_STORE` — the table jumps
straight from 585 (`DESCALING_STATUS`) to 588 (`UNACCOUNTED_SHOWER_CYCLES`), skipping 587
entirely (586 is handled separately as the instanced `DESCALING_PROGRESS`). App v2.14.2 adds
a `WaterHardnessViewModel` (Alba/Ble20 "NewAquaClean" tree) reachable from Maintenance →
Descaling → Water Hardness that reads this DpId directly — see
`docs/developer/model-feature-matrix.md` → "Other `GeberitDeviceType` values". Against the
current mock, opening that screen returns `InvalidId` instead of a value.

**Fix:** add a row to `_DEFAULT_STORE`, following the neighboring descaling-group entries'
format `(dp_id, inst, ver, dt, min_s, max_s, behavior, value)`:

```python
(587, None,  0, 10, 0,         2,          1, b'\x01'),  # WATER_HARDNESS = 1 (Level 2 of 3)
```

**Caveats:**
- Real device metadata (datatype/behavior/min/max) for DpId 587 is unconfirmed — 587 is not
  covered in `docs/developer/alba-dpid-reference.md`'s probe results. The row above assumes
  a 3-level enum (0–2), matching the CommonSetting `WaterHardness` semantics documented for
  AquacleanOld devices in `.claude/rules/ble-protocol.md`. Verify against a real Alba capture
  if one becomes available, and correct `dt`/`max_s` if wrong.
- **Default value `1`, not `0`** — deliberate, matching the existing precedent from
  `mock-geberit-mera.md` where CommonSetting `WaterHardness=0` triggered a real app crash
  (`ArgumentOutOfRangeException` on the dashboard segmented control, "Fehler" popup, fixed
  in mock v1.63.0b1 by defaulting to `1`). Apply the same defensive default here in case the
  app's Alba-side water-hardness UI has the same off-by-one assumption.

---

### Mock service: single CLI entry point for multi-model mocking

**Status:** requirements defined, not yet implemented. Full requirements — CLI shape,
multi-device orchestration, model/variant/protocol addressing (decided: single `--model`
open-ended lookup table, not a `--protocol` + `--model` split), firmware override, shared
SQLite persistence, multi-device webui, per-instance logging, DRY/shared-module structure,
and open decisions — are in `docs/developer/mock-service-requirements.md`.

Supersedes the Alba-only "SQLite persistence for DpId store" framing that used to live here
— generalized to run Mera, Sela, and Alba mocks **concurrently from one process**, each with
its own durable settings store, isolated within one shared DB file via a
`(device_type, device_key)` composite key (`aquaclean_ble_relay/mock_persistence.py`, already
implemented — see requirements doc §5 for the corrected design). Mera's addressing needs a
`namespace` dimension beyond a flat DpId key, because it has multiple index spaces that each
restart at 0 (`profile_setting`, `common_setting`, `active_setting`, `spl`) — encoded as
`f"{namespace}:{index}"`. Not every addressable index is durable — some `spl` indices are
live sensor/state signals a protocol module simply never persists. The full Mera
namespace/index enumeration this classification is built on stays below, as a standalone
reference table.

---

### Refactor: aquaclean_console_app webui to use the shared mock settings-control module (future)

**Not scoped, not started.** `docs/developer/mock-service-requirements.md` §6 records a
decision (2026-07-16) to build a new, generic, metadata-driven settings-control module
(stepper/toggle/select/swatch widgets + a generic write helper) for the mock webuis, styled to
match `aquaclean_console_app/static/index.html` but living entirely in `aquaclean_ble_relay/`
and not touching `index.html` itself. It's deliberately built as the forward candidate for
`index.html`'s own settings controls — currently hardcoded per setting ID (e.g.
`onCommonSettings` branching on `cs[0]`/`cs[1]`/`cs[2]`... individually) — so once the mock
module exists and is proven, `index.html` could be refactored to consume it (supplying the
bridge's own setting metadata) instead of its current inline per-ID code.

Touches shipped, user-facing `aquaclean_console_app` code — needs a proper branch/PR (not
direct-to-main like the mock work), and per the release-process checklist likely touches
`docs/cli.md`/`docs/configuration.md` if any REST-facing behavior changes as a result. Not
scheduled — revisit once the mock module has shipped and proven itself.

---

### Mock service: Mera namespace/index enumeration (persistence schema reference)

Referenced from `docs/developer/mock-service-requirements.md` §5 — kept here rather than
duplicated, since it's a protocol-reference table, not orchestration/CLI design. Source:
`.claude/rules/ble-protocol.md`.

*`profile_setting`* (proc 0x53 get / 0x54 set, power-cycle to apply) — indices 0–14, **all
PERSIST** except 11 `SeatHeating` (N/A on Mera Comfort, Tuma Comfort only): 0
`OdourExtraction`, 1 `OscillatorState`, 2 `AnalShowerPressure`, 3 `LadyShowerPressure`, 4
`AnalShowerPosition`, 5 `LadyShowerPosition`, 6 `WaterTemperature`, 7 `WcSeatHeat`, 8
`DryerTemperature`, 9 `DryerState`, 10 `SystemFlush`, 12 `WaterHeating`, 13
`DryerFanPower`, 14 `LadyOscillation`.

*`common_setting`* (proc 0x51 get / 0x52 set, "Stored", power-cycle to apply) — indices
0–12, **all PERSIST** except 10 `LightSensorSensitivity` (AcSela only) and 11 `CareMode`
(Floorstanding only): 0 `WaterHardness`, 1 `OrientationLightBrightness`, 2
`OrientationLightColour`, 3 `OrientationLightMode`, 4 `LidSensorRange`, 5
`OdourExtractionRunOn`, 6 `LidAutoOpen`, 7 `LidAutoClose`, 8 `AutoFlush`, 9 `DemoMode`, 12
`Language`.

*`active_setting`* (proc 0x0A get / 0x0B set, reuses the `common_setting`/`profile_setting`
ID space, applies immediately) — **NO PERSIST, by design.** Confirmed behavior: the iPhone
app uses these at session init to restore Active values *from* the corresponding Stored
row, and writing Active leaves Stored unchanged. Active is session-scoped runtime state,
seeded from the matching `common_setting`/`profile_setting` row at BLE-session start / mock
startup, held only in memory thereafter — mirrors the real device, where a power-cycle
re-derives Active from Stored NVM. Giving Active its own durable row would let a mock
restart "remember" an immediate-mode value the real hardware would have forgotten.

*`spl`* (proc 0x0D, read-only live-state poll) — indices 0–31 + mirrors 32–60 + 100/104–106:

| Idx | Name | Persist |
|---|---|---|
| 0 | StateUserPresent | NO — live sensor |
| 1 | StateShowerAnal (mislabeled; tracks sitting) | NO — live sensor |
| 2 | StateShowerLady | NO — live sensor |
| 3 | StateDryer (mislabeled; tracks anal shower) | NO — live sensor |
| 4 | StateDescaling | NO — live progress; resets on restart like a real power-cycle |
| 5 | DurationDescaling | NO — live counter tied to index 4 |
| 6 | LastError | **YES** — device retains last fault code across power-cycle |
| 7 | StateService | NO — live |
| 8/9/10 | StateSprayCalibration/StateOrientationLight/StateDraining | N/A on Mera Comfort |
| 11 | EndiannessCheck | NO — constant/diagnostic |
| 12–15 | UnpostedShowerCycles, DaysUntilNextDescale, DaysUntilShowerRestricted, ShowerCyclesUntilConfirmation | **YES** — statistics |
| 16–18 | TimestampAtLastDescale, TimestampAtLastDescalePrompt, NumberOfDescaleCycles | **YES** |
| 19–22 | DaysUntilNextFilterChange, TimestampAtLastFilterChange, TimestampAtLastFilterChangePrompt, NumberOfFilterChanges | **YES** — statistics |
| 23 | LocalAppTime | NO — app writes current wall-clock every session, never a device setting |
| 24–27 | LightDailyBlock1/2 Start/Stop | **YES** — schedule |
| 28 | TimestampAtLastPowerdown | **YES** — but mock must write this at graceful shutdown (`_stop_sequence`), not on every setting change |
| 31 | RealtimeClockUtcTime | NO — same reasoning as LocalAppTime |
| 32–46 | ActiveProfileSettings 0–14 (read-only mirror) | NO — mirrors `active_setting`, itself non-persistent |
| 47–60 | ActiveCommonSettings 0–13 (read-only mirror) | NO — same |
| 100 | ConnectedSsmDevices (bitmask) | **YES** — device pairing/registration state |
| 104–106 | LidOffsetPosition, ShowerArmOffsetPosition, DryerArmOffsetPosition | **YES** — calibration offsets |

*`command`* (proc 0x09, `SetCommandAsync`) — **excluded from the store entirely.**
Toggles/triggers carry no value of their own; their effect shows up as a mutation of an
`spl` row (e.g. `ToggleAnalShower` flips `spl[1]`) or is momentary. Nothing to persist.

**Tally:** ~60 real persisted rows for Mera Comfort (15 profile + 13 common + ~30 real
SPL/calibration/bitmask rows), plus the non-persisted Active/live rows held only in memory.

**Dependency:** `sqlite3` is stdlib — no new packages.

Open/resolved decisions on how this table is used (generic vs. per-model schema, webui edit
pattern; multi-device routing resolved 2026-07-16 as "each device keeps its own independent
page", no landing page or single-port merge) are tracked in
`docs/developer/mock-service-requirements.md` §10, not duplicated here.

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

---

### Mock Mera: capture real Mera firmware update — proc 0x00/0x01 protocol + Fehler hypothesis ★ NEXT

**Goal:** Capture the full first-time onboarding + firmware update flow on the real Mera with
a fresh Geberit Home App install. Answers three open questions simultaneously.

**Why fresh app install (delete + reinstall on iPad):**
With local storage cleared, the real Mera's SAP CRC32 UniqueId is unknown → app takes the
first-time pairing path, behaving toward the real device exactly as it behaves toward the
mock. This exposes:

1. **Does the real Mera trigger the blocking firmware update UI on a genuine first-ever
   connection?** The code path says it should (RS28.0 → `GetVersion()` null → `_E004()` true
   → `GetActiveUpdateAsync()` finds RS30.0 Ble2V1 package → non-null → blocking UI). If it
   does NOT → something not yet found in the source is short-circuiting the flow.
2. **proc 0x00 (ctx=0x40) and proc 0x01 (ctx=0x00) byte sequences** — the firmware update
   protocol the mock needs to implement to eliminate the Fehler on every reconnect.
3. **Fehler persistence hypothesis confirmed or denied** — after the update completes,
   reconnect. If Fehler stops → hypothesis confirmed (persistent completion flag written).
   If Fehler continues → wrong hypothesis.

**Bonus:** after the update the real Mera is at RS30.0, matching mock v1.75.0b1 exactly.

**Capture checklist (Wireshark + nRF52840 on Apple Laptop):**
1. Start Wireshark with the nRF52840 sniffer
2. Let it scan until it shows the Mera's BLE address
3. Click **Follow** on that address — sniffer tracks all channel hops automatically
4. Enable continuous file saving in Wireshark (Capture → Options → Output → save to file,
   auto-rotate every 50 MB or 5 min) as safety net against crash mid-capture
5. Delete and reinstall Geberit Home App on iPad (clears all local storage)
6. Open the app → onboard the real Mera → let the firmware update run to completion
7. After update: reconnect once more to observe whether Fehler still appears

**Risk:** one shot — firmware is updated regardless of capture success. The real Mera
cannot be put back to RS28.0 after the update.

**Detailed analysis background:**
`local-assets/geberit-home-v2.14.1-from-iOS/firmware-update-check-analysis.md`
§ "Fehler on every mock connect — hypothesis" and § "v1.75.0b1 empirical finding".

---

### Mock Mera: RC pairing follow-up — clear bond and test (from commit 2b565b0) ★ NEXT

**Corrected 2026-07-19** — the framing below overstated certainty; see
`docs/developer/mock-service-requirements.md` REQ-052 for the full corrected analysis. Kept
here for the concrete bond-clearing commands, but read the confirmed-blocker note first.

**Context:** commit 2b565b0 (2026-06-26, v1.73.0b1) added Device Information Service
(0x180A), the RC pairing service stub (0xC526), and fixed the "Already Exists" GATT
re-registration race (`_force_remove_and_reregister`). It also reintroduced
`btmgmt pairable on/off` toggling in the button-press handler — **but that was reverted again
on 2026-07-16** (adapter-wide `pairable=on` also makes the mock answer iOS's own system
pairing dialog during normal Home App onboarding, breaking it). As of today the mock
unconditionally sets `pairable off` at startup and never turns it back on.

**Confirmed current blocker (not the bond):** SMP-level pairing cannot complete with *any*
device right now, RC included, because the adapter is never made pairable. This must be
solved first — via a pairing mode scoped to just the RC's address, or a dedicated time-boxed
web-UI action — before the bond question below is even testable.

**Bond mismatch — unconfirmed hypothesis, not a demonstrated root cause.** The RC
(`B0:10:A0:68:5C:8B`) does hold a bond (LTK) with the real toilet (`38:AB:41:2A:0D:67`), and
if it reconnects with a stale EDIV+Rand the mock would have no matching LTK. But the
2026-06-25 test this was inferred from only showed the RC **never appearing in the mock's log
at all** — no capture has ever shown the RC actually sending `LL_ENC_REQ` against the mock and
failing. Don't treat this as verified; test it only after the pairable-scoping blocker above
is fixed, with a sniffer running to see what actually happens.

**Bond-clearing commands, for use once pairable-scoping is fixed** (mock must be stopped first):

Bonds (LTKs) are stored persistently in `/var/lib/bluetooth/<adapter-MAC>/` and survive
reboots until explicitly removed.

Check first:
```bash
bluetoothctl devices          # lists all known devices — if RC appears, an entry exists
bluetoothctl info B0:10:A0:68:5C:8B   # look for "Paired: yes" → LTK present, remove needed
```

Then remove:
```bash
bluetoothctl remove B0:10:A0:68:5C:8B
```

After clearing: the RC has no stored LTK → performs fresh SMP with the mock → BlueZ
handles Just Works pairing → new LTK established → ATT frames visible decrypted in btmon.

**Then verify:**
1. RC connects to mock and completes SMP pairing
2. RC sends `SetCommand` proc 0x09 codes (ToggleLidPosition etc.) over GATT
3. Mock responds correctly and the RC's physical buttons trigger the expected mock actions
4. Capture with btmon to confirm decrypted ATT frames match the reference:
   `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/toogle-lid-with-remote-without-running-bridge.md`

---

### Mock Mera: pair Geberit Remote Control with mock — blocked by pre-existing bond

**Goal:** connect the physical Geberit Remote Control (`B0:10:A0:68:5C:8B`) to
`mock-geberit-mera.py` so the remote's GATT commands are visible in btmon (decrypted,
because BlueZ holds the LTK after bonding).

**What happened (2026-06-25, v1.60.0b1):**
Logs: `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/mock-geberit-mera_RC_*_2026-06-25_12-04.*`

The remote control (`B0:10:A0:68:5C:8B`) never appeared in the mock log at all.
The only device that connected was `78:42:1C:38:DE:16` (random address → iPhone, stale
bond, RPA rotated → encryption failed with status 0x0e → disconnect). After that disconnect
the mock's GATT re-registration failed: `"Already Exists"` → mock broken, no further connections possible.

**Root cause — pre-existing bond:**
The remote has a bond (LTK) with the real toilet `38:AB:41:2A:0D:67`.
When the remote connects to the mock it immediately sends `LL_ENC_REQ` with the stored
EDIV+Rand from the real toilet bond. The mock VM has no matching LTK → encryption setup
fails → remote disconnects before GATT is reached. Confirmed in reference capture
`toogle-lid-with-remote-without-running-bridge.md`: `LL_ENC_REQ / LL_ENC_RSP / LL_START_ENC_RSP`
sequence fires within ~0.1s of connect, all ATT frames encrypted.

**Root cause — GATT re-registration bug:**
After Connection 1 disconnects via bonding failure, the mock tries to re-register its GATT
application but BlueZ returns "Already Exists" (re-registration races with BlueZ cleanup).
Mock is left without GATT services for any subsequent connection attempt.

**What's needed to succeed:**

1. **Clear the remote's bond with the real toilet** — run on raspi5 while bridge is stopped:
   ```
   bluetoothctl remove B0:10:A0:68:5C:8B
   ```
   (or factory-reset the remote). After clearing, the remote has no stored LTK → it will
   perform fresh SMP with the mock → BlueZ handles Just Works pairing → new LTK established
   → subsequent ATT frames are visible decrypted in btmon.

2. **Fix GATT re-registration bug** — mock must not attempt `RegisterApplication` before
   BlueZ confirms the old registration is gone. Symptom: `"GATT re-registration failed: Already Exists"`.

3. **Run `tools/nrf-ble-analyze.py`** on the reference capture
   `pairing with RC and toggle lid.pcapng` (same folder) to see the full pairing +
   lid-toggle sequence from the real toilet side — this is the target to replicate with the mock.

**Reference capture (real pairing, already decoded):**
`toogle-lid-with-remote-without-running-bridge.md` — shows LL encryption setup; ATT
frames encrypted (LTK needed to decode). Contains `SetCommand ToggleLidPosition` at t=98.5s
and `SetCommand 0x03` (Stop) at t=93.7s — confirms the remote sends standard
`SetCommand` proc 0x09 codes, no proprietary protocol layer.

---

### Mock Mera: add "User sitting" toggle button to web UI

The alba-mock web UI already has a button to simulate a user sitting on the seat.
The mera-mock needs the same: a toggle that sets `StateUserPresent` (SPL index 0) to 1 or 0
and keeps it there until toggled again.

**Why:** the Geberit Home App's Remote Control area only enables shower/dryer buttons when
the seat sensor reports a user sitting. Without this toggle the remote-control section of
the app stays greyed out during mock testing.

**Implementation sketch:**
- Add `_user_sitting: bool = False` state to the mock.
- Web UI button: "Simulate: User Sitting ON / OFF" — POST `/set-user-sitting` with `{"value": 0|1}`.
- SPL handler for index 0 reads `_user_sitting` instead of returning a hardcoded 0.
- Pattern: mirror the alba-mock's existing user-sitting toggle (same POST + state variable approach).

---

### Mock Mera: `UpdateConnectionParameters` silently skipped — BLE CI stays at ~30 ms

**Symptom (log):**
```
[08:21:15]   · UpdateConnectionParameters: 'ProxyInterface' object has no attribute 'call_update_connection_parameters'
```

**What it means:** `_request_short_ci()` fires right after iOS enables the A5 CCCD. It calls
`device_proxy.call_update_connection_parameters()` to negotiate the BLE connection interval
down from the default ~30 ms to 8.75–10 ms. dbus-next generates `call_<method>` wrappers
automatically from D-Bus introspection. On the BlueZ build used in the mock VM,
`org.bluez.Device1` does not advertise `UpdateConnectionParameters` in its introspection
XML → the wrapper is never created → `AttributeError` is caught silently and the request
is skipped.

**Impact:** ATT round-trips run at the default ~30 ms CI → each procedure takes ~1 s instead
of ~200 ms. This is a significant contributor to the total onboarding latency in the mock.
The mock is functionally correct; it just runs ~5× slower than the real device.

**Root cause:** BlueZ version or build configuration — `UpdateConnectionParameters` was added
to `org.bluez.Device1` in a later BlueZ release or requires a specific build flag to appear
in the introspection XML.

**Fix options:**
1. Upgrade the mock VM to a BlueZ version that exposes the method in introspection.
2. Send the D-Bus `UpdateConnectionParameters` message via raw `bus.send_message()` (bypassing
   the introspection-based proxy) — works on any BlueZ version that implements the underlying
   D-Bus method, regardless of whether it is advertised in the introspection XML.

**Status:** accepted as infrastructure limitation for now. Does not affect correctness of the
mock or the Geberit Home App flow. Fix only if mock latency becomes a testing bottleneck.

---

### Mock Mera: "Fehler / Ein Fehler ist aufgetreten" popup — root cause unknown (v1.64.0b1)

**Symptom:** Geberit Home App v2.14.1 shows "Fehler / Ein Fehler ist aufgetreten" after the
onboarding flow completes. Occurs consistently as of v1.64.0b1 despite all known procedure
responses matching real device values.

**Already ruled out:**
- GATT characteristic discovery — original BlueZ 5.77 confirmed working; all 9 chars found
- proc 0x51 WaterHardness=0 — fixed v1.63.0b1 (value now 1)
- proc 0x0A / 0x53 / 0x07 returning zeros — fixed v1.63.0b1 / v1.64.0b1
- proc 0x07 echoing wrong node_id in response body[1] — fixed v1.65.0b1, wire-confirmed
- A6 InfoFrame burst missing — fixed v1.61.0b1

**Next investigation steps:**
1. Capture mock BLE traffic with nRF Sniffer during the failing flow; compare procedure
   responses byte-for-byte against `onboarding-real-mera_timing.md`.
2. Check whether the app calls any proc not yet implemented (e.g. proc 0x08
   `SetActiveProfileSetting`, or procs called after the "Save" / registration step).
3. Verify SetStoredProfileSetting (proc 0x54) — the timing log shows 3 writes
   (AnalShowerPressure=2, OscillatorState=3, LadyShowerPressure=2) are sent by the app;
   mock currently returns `b""` which should be correct but confirm no error check.

Consider analysing the Sela firmware before building the mock:
- Node 0x01 (main controller source, 18,739 lines) — contains SPL dispatcher,
  procedure handlers, and the CommonSetting/ProfileSetting switch tables.
- Sela-specific nodes not present in Mera Comfort: 0x0F (Durchlauferhitzer / tankless heater).
- BLE controller (node 0x00) is identical to Mera Comfort (same binary, RS10 TS18).
- Extracted output: `local-assets/firmware/FwPkg_F806_V8.2.57.251023_0650871a_Sela_F8_06_RS_08_02_TS_57_extracted/`

---

### Agentic BLE protocol fuzzer

New script `tools/geberit-ble-fuzz.py` with modes:
`--mode read-procs` / `--mode setcommand` / `--mode common-settings` / `--mode profile-settings`.
Reuses `BluetoothLeConnector` + `AquaCleanClient`. Safe defaults: skip dangerous SetCommand
codes (33–36, 4, 37, 6–9) unless `--unsafe` is passed.

---

## Resolved / implemented items

See `.claude/rules/archive.md`.
