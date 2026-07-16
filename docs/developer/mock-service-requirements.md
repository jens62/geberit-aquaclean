# Mock Service — Requirements Definition

**Status:** requirements defined, not yet implemented.
**Scope:** `mock_service.py` (new, thin CLI orchestrator) + refactor of
`tools/mock-geberit-mera.py`, `tools/mock-geberit-alba.py`, and the planned Sela mock into
importable units + shared modules in `aquaclean_ble_relay/`.

See `docs/roadmap.md` → "Mock service: Mera namespace/index enumeration" for the full
per-index persistence table this doc's schema requirement (§5) is built on — not duplicated
here.

---

## 0. Goal & acceptance criterion

Replace per-model standalone scripts with one thin CLI entry point, `mock_service.py`,
capable of running several mocked devices concurrently in one process.

**Acceptance test:** change a setting via the Geberit Home App against a mocked device →
close the app → stop `mock_service.py` → restart `mock_service.py` → the changed setting is
still there. Must hold independently for every concurrently-running device.

---

## 1. Single thin CLI entry point

`mock_service.py` is the only script a user runs. It contains no protocol logic itself — it
parses arguments, instantiates one device object per `--device` entry, and orchestrates
them. All BLE/protocol behavior lives in the existing per-model modules, refactored per §2.

## 2. Multi-device orchestration

- One process, one asyncio event loop, one task per device (`asyncio.gather`/`TaskGroup`).
- Each task bound to its own BlueZ adapter (reuse the adapter-selection code already merged
  from the Alba `--adapter` feature — do not reimplement per model, see §8).
- CLI shape — one repeatable composite flag per device, not zipped positional lists (avoids
  index-mismatch bugs):
  ```
  mock_service.py --device model=mera,adapter=hci1 \
                  --device model=sela,adapter=hci2 \
                  --device model=alba,adapter=hci3 \
                  --state-dir mock_state/
  ```
- **Precondition — refactor each mock into an importable unit.** Current mocks carry
  module-level state (device dicts, notify-handler tables, a single hardcoded
  `logging.getLogger("mera_mock")`). Each must become a class/async-factory taking
  `(adapter, variant, firmware, state_dir, ...)` so N instances coexist in one interpreter
  without clobbering each other's globals.
- **Standalone parity, decided (see §10 decision 2):** `tools/mock-geberit-mera.py` and
  `tools/mock-geberit-alba.py` keep working as single-device scripts — each becomes a thin
  wrapper that instantiates exactly one instance of the refactored class. Not retired. This
  also doubles as the regression baseline during the refactor: before `mock_service.py`
  orchestration exists at all, the wrapper must behave identically to the pre-refactor
  script, and the §0 acceptance test is provable against it standalone first.
- **Startup validation, upfront, not mid-run:** reject unknown `--model` values, reject two
  `--device` entries pointing at the same adapter, fail fast with a clear message if a named
  adapter doesn't exist or is already claimed by BlueZ/another process — rather than
  surfacing as a cryptic D-Bus error after the event loop is already running.
- **Shutdown ordering:** SIGINT/SIGTERM must cancel all device tasks and let each run its own
  cleanup (unsubscribe GATT notifications, write `TimestampAtLastPowerdown`, close its DB
  connection, flush its log handler) — one device's slow/failed cleanup must not block or
  corrupt another's.

## 3. Model / variant / protocol addressing — DECIDED

**Decision: single `--model`, open-ended lookup table.** Not a separate `--protocol` +
`--model` split.

Background: `--model` implicitly does two jobs — selecting *which protocol module* to load,
and selecting *which device identity/variant* to present within that family. `mera` and
`sela` both speak the same legacy proc/ctx protocol (Sela = different variant byte +
different default identity strings + AcSela-only features like `ToggleOrientationLight`);
`alba` speaks an entirely different protocol (Ble20/Arendi). So the `--model` →
protocol-module mapping is not 1:1 with the module boundary — that's fine, it's exactly what
an internal lookup table is for.

**Implementation implication:** a registry (dict or similar) mapping model name →
`(protocol_module, default_identity)`, open-ended (`mera`, `sela`, `mera-classic`, `alba`,
`alba-kstr`, ...). `mock_service.py` looks up the protocol module from this registry per
`--device` entry; it never branches on protocol itself.

**Discoverability requirement:** since the table is open-ended and otherwise undiscoverable
without reading source, `mock_service.py --help` (or a dedicated `--list-models` flag) must
enumerate every registered model/variant value.

## 4. Firmware version override *(future)*

`--firmware "RS28.0 TS199"` is a human-readable string that needs parsing into whatever
internal representation each protocol module already uses (Mera's `_FW_COMPONENT_VERSIONS`
byte-tuples; Alba's own firmware DpId encoding — these differ per protocol). Parsing belongs
inside each protocol module's own default-firmware setter, not as one shared parser in
`mock_service.py` — the internal representations aren't the same shape across protocols (§8
DRY still applies, but at the "each protocol owns its own parser" level). Input must be
validated against a format regex at CLI-parse time with a clear error on mismatch; omitting
`--firmware` falls back to that model's existing hardcoded default.

## 5. Persistence

**Corrected from an earlier draft of this doc, against what's actually implemented in
`aquaclean_ble_relay/mock_persistence.py` (scaffolded before this section was written).**
The original draft assumed one SQLite *file* per device instance
(`mock_state/<model>-<adapter>.sqlite3`). What's actually there — and what this section now
documents — is a single shared DB file with per-device isolation via a composite key. Since
the existing design already satisfies every real requirement below, the doc was fixed to
match the code rather than the code rewritten to match a speculative doc.

- **One shared SQLite file**, `mock_state.db`, under a configurable directory
  (`set_state_dir()`, driven by `mock_service.py --state-dir`) — not one file per device.
- **Isolation via composite primary key**, not separate files:
  `(device_type, device_key, state_key) → value`. `device_type` = model name (`"mera"`,
  `"alba"`); `device_key` = the adapter name (`"hci1"`) or advertised MAC, falls back to
  `"default"` for single-instance use. Mera on `hci1` and Sela on `hci2` cannot collide —
  they never share a primary key.
- **`namespace`/`index` addressing** (needed because Mera has multiple index spaces that
  each restart at 0 — `profile_setting`, `common_setting`, `active_setting`, `spl`) is
  encoded into `state_key` as `f"{namespace}:{index}"`, e.g. `"common_setting:0"`. No schema
  change needed — `state_key` was already a free-form string. Alba's flat DpId space fits
  the same shape (`state_key = f"dpid:{dp_id}"`).
- **`datatype`/`behavior`/`min`/`max` are NOT stored in the DB.** They're static per-model
  Python tables, not mutable state — see "Provenance of min/max values" below for why this
  is not a shortcut, it's where the real app gets them too. The webui's full settings table
  (§6) joins static metadata with `load_all()`'s current values at render time.
- **`persist` is not a DB column either** — it's a code-side decision. A protocol module
  only calls `save()` for `(namespace, index)` pairs classified as durable (see the Mera
  enumeration in `docs/roadmap.md`); live-state indices are simply never written. Nothing to
  filter on read.
- **Coverage requirement:** every `persist`-classified row in that enumeration must actually
  round-trip through this store — the full real-device settings surface, not a convenience
  subset. If a setting exists on a real toilet and is writable, it must survive a mock
  restart.
- **Write-through, not write-on-shutdown.** `save()` already commits synchronously on every
  call — every setting change arriving over the protocol (`SetStoredProfileSetting`,
  `SetActiveCommonSetting`, DpId write, etc.) persists immediately, not batched or flushed
  at shutdown. This is what makes the §0 acceptance test hold even under an abrupt
  SIGINT/SIGTERM.
- **Startup never overwrites an existing store.** `load_all()` returns whatever's on disk;
  a protocol module seeds hardcoded defaults only for `(namespace, index)` pairs missing
  from the result. An existing value always wins.
- **Reset-to-factory is already correctly scoped.** `reset(device_type, device_key)` deletes
  exactly one device's rows — already satisfies "acts on exactly one device's store, never
  all of them" (§6).

### Per-device identity (serial number, PIN) — implemented for Alba (2026-07-16)

**Coverage gap found:** "store all data which is stored on a real device" (§0/§5's coverage
requirement) was being interpreted as "settings a real device lets you change" — but a real
device also has a unique serial number and pairing PIN printed on its own sticker, set once
at manufacturing and never changed. Every `_DEFAULT_STORE` row for these (DpId 12
`PAIRING_SECRET`, DpId 369 `SALES_PRODUCT_SERIAL_NUMBER`) was one hardcoded value shared by
every `AlbaMock` instance — two mocked Albas (e.g. Phase 5's `hci0`+`hci1` test) would show
identical "S/N" and PIN, which no real fleet of devices looks like.

**Implementation:** both DpIds are `behavior==4` (Protected — factory-set, never written
over BLE), so the existing Nvm (`behavior==3`) write-through path never touches them.
`_Ble20AppLayer.__init__` now generates a value for each on first construction for a given
`device_key`, persists it immediately (same `dpid:{id}` key scheme as Nvm settings), and
reapplies the persisted value on every later session/restart — stable per device, exactly
like a sticker that never changes once printed, distinct across devices.

**Bug found and fixed during verification:** the first pass skipped *regenerating* an
already-persisted identity value correctly, but never actually applied the persisted value
back into `self._store` (the reload loop only applies persisted overrides to
`behavior==3` rows) — so a restart silently reverted to the `_DEFAULT_STORE` default instead
of the previously-generated identity. Fixed by applying the persisted value directly for
these two DpIds, bypassing the Nvm-only reload loop. Verified on the mock VM: `hci0` and
`hci1` get distinct serial+PIN, and `hci0`'s identity survives a simulated restart
unchanged.

**Web UI:** the Alba control page now shows a sticker-style block (model, S/N, PIN) reading
live from the active session's store, so the two values are visible without needing a BLE
client.

**Security finding along the way:** `_DEFAULT_STORE`'s `PAIRING_SECRET` comment named a real
physical device's actual pairing PIN as a formatting example. Redacted (both
`tools/mock-geberit-alba.py` and `aquaclean_ble_relay/alba_mock.py`) — a real device's BLE
pairing PIN is a real credential, not safe to reference in a public repo regardless of
framing. **This wasn't just an oversight — it violated an already-documented project rule**:
`memory/kstr-probe-findings.md` (~55 days earlier) explicitly said "Do not log or commit
actual values" for this exact DpId and the SAP/serial DpIds. The redaction fixes the rule
violation going forward.

**The value was already present in pushed history** (confirmed via `git log -S`, present
since commit `7ac384c`, ~255 commits and 38 already-published GitHub Release tags
downstream of it by the time this was found). **Decided (2026-07-16): leave history as-is,
do not rewrite.** Considered and rejected — rewriting would force-push `main`, orphan 38
published HACS release tags (each needing manual recreation), and diverge every existing
clone, for a leak whose real-world exploitability is narrow (a 4-digit BLE pairing PIN,
usable only by someone who both knows the specific device's BLE MAC and is within physical
BLE range of it) and which — critically — rewriting wouldn't even fully undo, since anyone
who already cloned or installed the repo keeps the old value in their local copy regardless.
Do not re-raise this as an open task in a future session; it's a closed decision, not a
pending TODO.

**Not yet done — tracked as Phase 2c above, do not forget:** the same per-device-identity gap
likely applies to `MeraMock` (`_SAP_NUMBER`/`_SERIAL`, currently fixed instance attributes
set in `__init__`, not persisted or varied per adapter) — not implemented, since the
concrete ask and Phase 5 test case were Alba-only.
Worth doing the same way if/when two Mera instances are tested together.

### Firmware version persistence (Mera + Alba) — 2026-07-16

**Gap found:** both mocks reported firmware/component versions as read-only, in-memory-only
data — Mera's `_FW_COMPONENT_VERSIONS` (module-level dict, `mera_mock.py`) and Alba's
firmware DpIds (8 `FW_RS_VERSION`, 9 `FW_TS_VERSION`, 10 `HW_RS_VERSION`, 785 `FUS_VERSION`,
786 `GEBERIT_LOADER_VERSION`, 787 `WIRELESS_STACK_VERSION`, all `behavior==0`/`4` in
`_DEFAULT_STORE`) were never written to `mock_persistence.py`. Fine for reporting a static
version, but it blocked simulating a firmware update (§9b below), which needs the reported
version to durably change after the simulated OTA — exactly what a real device's flash-backed
version storage does. Confirmed directly: the real Mera Comfort capture in
`memory/mera-firmware-update-ble-protocol.md` shows `GetFirmwareVersionList` returning
RS28.0 TS199 before an update and RS30.0 TS206 after, for component `0x01`.

**Implementation — same composite-key mechanism as everything else in this section, no
schema change:**
- **Mera** (done, `mera_mock.py`): `state_key = f"fw:{component_id}"` — same
  `namespace:index` shape as `common_setting:idx`/`profile_setting:idx`. `MeraMock.__init__`
  copies the module-level `_FW_COMPONENT_VERSIONS` fallback dict into
  `self._FW_COMPONENT_VERSIONS`, then overlays any persisted values in the same loop that
  already handles common/profile settings. `_proc_0e` (`GetFirmwareVersionList`) reads the
  instance dict, not the module dict. New `_set_fw_version(component_id, v1, v2, build)`
  writes through to `mock_persistence.py` — the hook Phase 9b's update-process simulation
  will call. Not called anywhere yet.
- **Alba** (done, `alba_mock.py`): `state_key = f"dpid:{dp_id}"` — identical scheme to the
  Nvm/identity DpIds already persisted. The six firmware DpIds are Info/Protected, so (like
  the identity DpIds) they get their own loop outside the Nvm-only reload path. Unlike
  identity, there's no random generation — first run for a given `device_key` simply persists
  today's hardcoded `_DEFAULT_STORE` default as-is, since firmware versions aren't unique per
  physical unit. New `_set_firmware_version(dp_id, value)` is the equivalent write hook, also
  not called anywhere yet.

**Parity policy (2026-07-16, explicit user instruction):** any mock feature implemented for
one protocol (Mera or Alba) gets mirrored in the other at the same time, unless there's a
concrete protocol-level reason it can't apply — tracking two mocks that drift independently
is more housekeeping than doing both up front. This firmware-persistence change is the first
instance of applying that policy; apply it by default to future mock feature work.

**Not yet done:** the actual update-process simulation that calls the two write hooks above.
Mera's real `0x40/0x52…0x40/0x04` proc sequence is already decoded in
`.claude/rules/ble-protocol.md` and `memory/mera-firmware-update-ble-protocol.md`; no
Alba-side capture exists yet. Tracked as Phase 9b below.

### Provenance of min/max values (why they're code, not DB or a live fetch)

Checked before deciding this, rather than guessing: does `RS28.0`/`TS199`-style firmware, or
the Geberit cloud, hand out setting min/max ranges at runtime? Investigated against
`local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/firmware-update.har`
(a full-session Charles capture from a real Mera Comfort firmware update). Findings:

1. **Firmware version checks on startup — confirmed, yes.** The HAR shows
   `prod.firmwarev1.services.geberit.com/api` → `/api/version` + `/api/firmwares` (an 809 KB
   firmware catalog) hit repeatedly during the session. That's exactly the "check for
   available updates" mechanism — real cloud call, real endpoint, confirmed present.
2. **Setting min/max ranges — not in the cloud, at all.** The only other Geberit endpoint
   hit is `mobileappsv1.services.geberit.com/api/Settings/*`, and its payloads are generic
   app feature-flags — e.g. `Settings/43/iotsettings` decodes to
   `{"min_remote_maintenance_app_version": "2.14.2", "aqua_clean_remote_support":
   {"supported_devices": ["248_1"..."250_0"]}}` — device-type gating for a remote-support
   feature, not protocol-level value bounds. Every other `/Settings/*` call in the capture
   returned `"Data": null`.
3. **So where do min/max actually live?** Not firmware, not cloud, as far as this project
   has already reverse-engineered — `docs/developer/alba-dpid-reference.md` already
   documents its own Min/Max columns as *"value range from protocol spec"*, i.e.
   reverse-engineered from the decompiled Home App's own DataPoint definitions. The app
   validates client-side using ranges compiled into the app binary. No accessible
   firmware-side bounds-check was found in the decompiled Mera `0x01_decompiled.c` (the
   switch dispatch doesn't decompile to labeled proc-ID cases, so confirming firmware-side
   enforcement would need real disassembly effort) — plausible the firmware also rejects
   out-of-range writes, but unconfirmed, and it doesn't change what we need to do: the
   engineering instinct that this should be authoritative somewhere durable was right, but
   empirically it's the app that's authoritative today, not the firmware. Since we already
   have that "protocol spec" transcribed into `ble-protocol.md` / `alba-dpid-reference.md`,
   the mock's metadata tables just reproduce the same source the real app uses — nothing new
   to fetch.

## 6. Webui

- **Multi-device routing — DECIDED (2026-07-16):** each device keeps its own already-independent
  web server/port (status quo architecture — aiohttp for Mera, FastAPI for Alba, each bound to
  its own `web_port`). No landing page, no single-port/single-server merge — a user running N
  mock devices already knows each one's port from its own `--device` spec, and unifying
  aiohttp+FastAPI into one process-wide server would add real complexity for no functional gain.
- Full settings table per device (value, datatype, behavior, min/max), inline per-row edit,
  **Reset to factory defaults** scoped to exactly one device's store — never all of them.

### Real SSE, not full-page-reload polling — 2026-07-16, VM-verified

**Gap found:** both mocks' webui update mechanism was full-page reload on a timer —
`setTimeout(location.reload(), 3000)` (Mera) / `<meta http-equiv="refresh" content="2">`
(Alba) — not even AJAX polling of a JSON endpoint, let alone SSE like the real bridge's
`new EventSource(apiBase + '/events')` (`index.html:1184`). Every reload wiped any
in-progress interaction (a stepper mid-drag, a swatch's success/error flash) regardless of
whether anything had actually changed.

**Implemented:** both mocks now have a real `/events` SSE endpoint, mirroring
`aquaclean_console_app/RestApiService.py`'s exact pattern — one `asyncio.Queue` per
connected client, a 30s heartbeat (`: heartbeat\n\n`) when idle, `{"type": "state", ...}`
JSON payloads. Client side, `mcConnectSSE(url, onState)` (new in `mock-controls.js`) mirrors
`index.html`'s `connectSSE()`/`onmessage` — each mock's own page decides what to update
(the settings table via `mcRenderSettingsTable`, plus its own badges/log/notify-button state)
instead of reloading everything.

- **Mera**: broadcasting is hooked into the existing `_log()` helper — nearly every
  state-mutating path (settings writes via proc 0x52/0x54 *and* the webui write routes,
  button press, general BLE activity) already calls `_log()` for the session log, so this
  covers them all without scattering broadcast calls through the codebase.
- **Alba** is architecturally harder: `_Ble20AppLayer`/`_AriendiServerSide` are freshly
  reconstructed per BLE session inside closures in `run()`, and real BLE-side writes happen
  inside `_Ble20AppLayer._write()`, which had no path back to `AlbaMock`'s broadcast queue.
  Per explicit decision (full proper wiring for both mocks, not a shortcut for the harder
  one): `_Ble20AppLayer.__init__` now takes an optional `broadcast_fn` callback (same pattern
  as Phase 7's `logger` threading), called from both `_write_dpid_setting()` (webui writes)
  and `_write()` (real BLE `WriteCmd`, only when a Nvm/`behavior==3` row actually persists) —
  so a real Geberit Home App or remote-control write pushes a live update too, not just
  webui-initiated ones.

VM-verified on `anneubuntu-studio`: Mera's `/events` sends an initial snapshot on connect and
a fresh push immediately after a settings write; Alba's broadcast fires correctly from both
the webui write path and a real-BLE-frame-shaped `_write()` call, and correctly does *not*
fire for a non-Nvm DpId write (nothing persisted, nothing to broadcast). Permanent regression
tests added to `tests/test_mera_mock_webui.py`/`test_alba_mock_webui.py` (skipped via
`pytest.importorskip` where deps are missing, same as the rest of those files).

**Test-isolation bug found and fixed along the way (own test code, not production):**
`logging.FileHandler` opens its file immediately and `mock_logging.py` caches loggers
globally by name — every test that used `adapter=None` collided on the same cached
`"mock.mera.default"`/`"mock.alba.default"` logger, and once one test's tmp dir was deleted,
a later test reusing that cached logger could hit `FileNotFoundError` on its next log call.
Fixed by giving each test construction a unique adapter/logger identity (derived from its own
tmp dir's unique suffix) instead of relying on the shared "default" bucket.

### Firmware profile selector (Mera only) — 2026-07-16, VM-verified

**Implemented for Mera.** A new "Firmware Profile" `select` row at the top of the Firmware
Versions section (`/settings/firmware-profile`, body `{"value": "rs28"|"rs30"}`) lets a user
flip the mock between the two real captured snapshots — `_FW_COMPONENT_VERSIONS` (RS30.0
TS206, current/default) and the new `_FW_COMPONENT_VERSIONS_RS28` (RS28.0 TS199, pre-update).
Only components 1 and 11 actually differ between the two — confirmed twice over (`memory/
mera-firmware-update-ble-protocol.md` and `docs/developer/firmware-version.md`, matching real
capture bytes) — every other component is identical in both snapshots. Applying a profile
calls the existing per-component `_set_fw_version()` write hook (Phase 9a) once per component,
so persistence/logging/SSE-broadcast all stay identical to how an eventual Phase 9b OTA
simulation would apply the same values one at a time. The currently-active profile is derived
from component 1's live value (no separate "current profile" flag to keep in sync) —
`_current_firmware_profile()` returns `"custom"` if it matches neither canonical snapshot
(e.g. after a partial Phase 9b-simulated update).

**Corrected a stale doc while implementing this:** `docs/developer/firmware-version.md`'s
2026-06-26 finding ("component 1 alone at RS30.0 is not sufficient, all components must be
RS30.0") was superseded by the actual v1.76.0b1 commit (`e4295cc`, 2026-07-16) and a real
on-device test the same day — see that doc for the correction. The mock's real per-component
values (today's default) are the confirmed-working baseline, not something to fix further.

**Not yet implemented for Alba** — no real pre/post-update firmware capture exists for Alba
yet (unlike Mera's nRF52840 capture), so there's no confirmed byte-accurate "older" snapshot to
offer. Tracked as a to-do: once an Alba firmware-update capture exists, mirror this same
selector for Alba's firmware DpIds (8/9/10/785/786/787).

### DRY: shared frontend assets with the real bridge webui — 2026-07-16

**Current state (confirmed by reading all three, not assumed):** there are three fully
separate HTML/JS sources today, zero sharing between them —
- the real bridge's `aquaclean_console_app/static/index.html` (2141 lines, one monolithic
  file, no separate `.js`/`.css` files),
- Mera mock's inline `_HTML` template (`mera_mock.py`, aiohttp + `str.format()`, ~53 lines —
  identity table + button-press form + session log only),
- Alba mock's inline route-handler string (`alba_mock.py`, FastAPI, ~35 lines — identity
  sticker + two DpId toggle buttons only).

Neither mock currently implements orientation-light control, a settings table, or anything
else that visibly duplicates the bridge's markup — so there's no duplication to fix *yet*.
But Phase 6 (the row above) is exactly where both mocks are planned to grow a full settings
table, and building that twice from scratch — once per mock, independently from the bridge's
existing solution — would be the third and fourth independent implementation of primitives
the bridge already solved generically.

**What's actually reusable:** the bridge's `index.html` script block already implements its
per-feature controls (including orientation light's brightness stepper and color swatches)
as hand-written HTML wired to a small set of *generic*, data-attribute-driven JS helpers —
not one bespoke script per feature:
- `stepDec`/`stepInc` — generic numeric stepper (`data-kind`/`data-id`/`data-min`/`data-max`)
- `setCommonSetting` / `setProfileSetting` — generic setting-updater dispatch by ID
- `sendPost` — generic POST helper
- the `.ps-section`/`.ps-row`/`.ps-stepper` CSS classes these are wired to

These aren't bridge-specific — a mock's future settings table needs the exact same
primitives (numeric stepper, enum/select, color swatch, toggle button) against the same
conceptual setting IDs (`common_setting`/`profile_setting`) the bridge already uses them for.
Only the primitives are candidates for sharing; feature-specific markup (labels, the literal
orientation-light swatch colors, section titles) stays per-consumer, since each surface
renders its own device's setting definitions.

**Decided (2026-07-16):** write a new, standalone, metadata-driven JS+CSS module inside
`aquaclean_ble_relay/` — not extracted from `index.html`, and `index.html` is not modified by
this work (that stays a separate future item, see below). The module mirrors the bridge's
visual style (same stepper widget, same color-swatch treatment used for orientation-light-style
controls, same toggle-switch look) but is architected generically from the start, unlike the
bridge's current per-ID-hardcoded functions (`onCommonSettings` branches on
`cs[0]`/`cs[1]`/`cs[2]`... individually; `setCommonSetting`/`setProfileSetting` POST to
hardcoded bridge-only paths): a metadata list per setting
(`{id, name, kind: stepper|toggle|select|swatch, min, max, writeUrl}`) drives rendering, value
updates, and writes generically, so the same renderer serves Mera's `common_setting`/
`profile_setting` IDs and Alba's DpIds without per-ID branches.

This keeps the work entirely inside `aquaclean_ble_relay/` — no branch needed, straight to main
like the rest of the mock work — while deliberately building the *forward candidate* for
eventual sharing: because it's generic rather than mock-specific, refactoring `index.html` later
becomes "swap its inline per-ID code for calls into this module, supplying the bridge's own
metadata list" rather than a rewrite from scratch. That refactor is out of scope here — tracked
as a future item in `docs/roadmap.md` → "Refactor: aquaclean_console_app webui to use the shared
mock settings-control module" since it touches shipped `aquaclean_console_app` code and needs a
proper branch/PR, per the existing mock-vs-console-app workflow split
(`memory/feedback_mock_services_work_on_main.md`).

**Implemented and VM-verified (2026-07-16, commit `d217e46`, VM-verified `2d8a56a`+):**
`aquaclean_ble_relay/static/mock-controls.js`/`.css` built as described above. Mera got
`/settings/common/{id}` and `/settings/profile/{id}` write routes (backed by
`_write_stored_common_setting`/`_write_stored_profile_setting`, shared with proc 0x52/0x54 so
BLE and webui writes stay consistent) plus a read-only Firmware Versions section; Alba got a
single `/settings/dpid/{id}` write route restricted to Nvm (`behavior==3`) rows, with
datatype-aware encode/decode helpers, covering the writable Nvm DpIds plus read-only identity/
firmware DpIds.

Could not be runtime-verified in this dev environment — `fastapi`/`aiohttp`/`bluez_peripheral`
aren't installed here — so verified on the mock VM (`anneubuntu-studio`, `/home/jens/venv`,
which does have them): both mocks import cleanly, the settings table renders, static assets
serve, and writes round-trip and persist (Mera via `common_setting`/`profile_setting`, Alba via
Nvm DpIds; a write to an Alba Protected DpId — e.g. the pairing PIN — is correctly rejected with
HTTP 400). Permanent regression coverage added as `tests/test_mera_mock_webui.py` and
`tests/test_alba_mock_webui.py` — skipped automatically via `pytest.importorskip` in any
environment missing the deps (this dev venv included), runnable for real wherever they're
installed. All 11 tests pass on the mock VM.

**Bug found and fixed along the way (commit `2d8a56a`):** `alba_mock.py` added the repo root to
`sys.path` right before its `aquaclean_console_app` imports, but its own `aquaclean_ble_relay.*`
imports sit above that line — so standalone-script invocation (where `sys.path[0]` is this
file's own directory, not the repo root) failed with `ModuleNotFoundError` before ever reaching
the later insert. `mera_mock.py` already did this in the correct order; `alba_mock.py` didn't.
Fixed by moving the insert before both import groups it serves.

## 7. Logging

- One logger per device instance, named by the same `(model, adapter)` key used for
  persistence (e.g. `mock.mera.hci1`) — not one shared hardcoded logger name.
- Device tag at a fixed position in every line (immediately after the timestamp) so a
  script/CI consumer can reliably `grep`/`awk` on it regardless of message content.
- Both a combined process-wide log (chronological, cross-device correlation) and a
  per-device log file, via multiple handlers on the same per-device logger — not a choice
  between the two.
- Log filenames follow the same `<model>-<adapter>` naming convention as the persistence DB.

**Implemented and VM-verified (2026-07-16):** new shared module
`aquaclean_ble_relay/mock_logging.py` — `get_device_logger(model, adapter)` returns/configures
the `mock.<model>.<adapter>` logger (idempotent), with three handlers: console, a per-device
file (`logs/mock-<model>-<adapter>_<timestamp>.log`), and one combined-file handler
(`logs/mock-combined_<timestamp>.log`) shared by every device logger created in the process.
Device tag is the logger name itself in the format string (`[%(asctime)s] [%(name)s]
%(message)s`) — right after the timestamp, no per-record `extra=` plumbing needed.

Both mocks now use it: `MeraMock` swapped its inline logger/file-handler setup for a call to
the shared helper. `AlbaMock` needed more — it had no `logging.Logger` at all, just a
module-level timestamped `print()` override used at ~120 call sites across `_Ble20AppLayer`,
`_AriendiServerSide`, the four GATT service classes, and `AlbaMock` itself. Per explicit
decision (full mechanical conversion over a smaller `contextvars`-based shortcut — the
mechanical version means every call site genuinely holds its own `Logger`, not a shortcut that
only observably behaves like one), the override is gone entirely: each of those classes now
takes an optional `logger` constructor param (threaded down from `AlbaMock.logger` at
construction), and all ~120 `print(...)` call sites became `self.logger.info(...)`. Six
multi-positional-arg `print("text:", var)` calls were collapsed to single f-string args first —
`logging.Logger.info(msg, *args)` treats extra positional args as `%`-style formatting
arguments, so passing them through unchanged would have raised `TypeError` at runtime. Two call
sites aren't methods (`safe_call()`, a module-level function — uses
`getattr(obj, "logger", ...)`; the `if __name__ == "__main__":` KeyboardInterrupt handler —
uses `mock.logger`).

`mock_service.py`'s `_Tee` stdout/stderr redirect (the previous stand-in for a combined log,
keyed by the whole `--device` batch rather than per-device) is removed — redundant now that
every device logger has its own combined-file handler from `state_dir`, which
`_resolve_kwargs` already defaults onto every device's constructor kwargs.

VM-verified on `anneubuntu-studio`: device tag confirmed at the fixed post-timestamp position,
per-device files confirmed isolated (each device's file contains only its own lines), the
combined file confirmed shared and interleaved across two concurrently-constructed
Mera+Alba instances, logger-wiring confirmed reaching all four previously-print()-only classes.
All 11 pre-existing Phase 6 webui tests still pass unmodified (no regression). Permanent
regression coverage in `tests/test_mock_logging.py` — stdlib-only (no bluez_peripheral/aiohttp/
fastapi dependency), runs in any environment including the primary dev venv, unlike the Phase 6
webui tests.

**Follow-up bug (found live on the mock VM, fixed same day):** `MeraMock.run()` had a second,
later reference to the `log_path` variable the Phase 7 refactor removed — crashed every real run
with `NameError` as soon as the startup banner tried to print it. `get_device_logger()` now
stashes the per-device path on the logger itself (`logger.device_log_path`) so callers can
report it without keeping their own copy.

**`--btmon-capture` / `--bluetoothd-debug` (2026-07-16):** `mock_service.py` can now start
`sudo btmon -w <state-dir>/logs/mock-btmon_<timestamp>.btsnoop` and/or
`sudo bluetoothd -n -d --noplugin=battery` (redirected to
`<state-dir>/logs/mock-bluetoothd-debug_<timestamp>.log`) before any device starts, and stops
them cleanly on exit — automating a capture workflow that was previously two separate manual
terminal commands. Filenames follow the same `state_dir/logs/mock-*_<timestamp>` convention as
the per-device/combined logs. Cleanup backs `Popen.terminate()` with a `sudo pkill -f` fallback
keyed on a distinguishing argument, since `sudo` doesn't always forward `SIGTERM` to its child
reliably. Deliberately does **not** stop/restart the systemd `bluetooth` service — mirrors the
manual commands exactly; if systemd's `bluetoothd` is already holding the D-Bus name,
`--bluetoothd-debug` just fails to bind, same as running the command by hand.

## 8. DRY — shared modules, not per-mock duplication

- Adapter selection: already extracted (Alba's `--adapter` feature, merged to main) —
  Mera/Sela reuse it, don't reimplement.
- Persistence: one schema/module (`mock_persistence.py`, already scaffolded), reused by
  every model.
- CLI parsing, task orchestration, shutdown handling: live once in `mock_service.py`.
- Firmware-string validation and namespace/persist classification: each protocol module owns
  its own concrete table/regex, but the *pattern* (validate at parse time, classify
  persist-vs-live once per namespace) is shared conceptually across models even where the
  concrete tables differ.

## 9. Additional gaps identified

- **Backward compatibility / migration path — RESOLVED, see §10 decision 2.** Kept as thin
  single-device wrappers around the refactored class, not retired — see §2.
- **Webui bind failure — OPEN.** If webui goes single-port-multi-route (§6), does one port
  conflict abort the whole service, or degrade just that device to headless (no webui, BLE
  still served)?
- **Resource conflicts across devices:** need a decision on whether a webui bind failure (or
  any single-device startup failure) aborts the entire `mock_service.py` run or just that one
  device, leaving the others running.
- **Mera `SetCommand` (proc 0x09) is almost entirely unsimulated — found 2026-07-16, tracked as
  Phase 10.** This is a gap in *action simulation*, distinct from persistence (§5), which is
  correctly wired for the settings that actually need it. Precisely:
  - `_write_stored_common_setting`/`_write_stored_profile_setting` (proc 0x52/0x54, "Stored"
    settings) persist correctly — confirmed working, reused by both the real BLE path and the
    Phase 6 webui write routes. If the real Geberit Home App changes e.g. orientation light
    colour against a mocked device, that persists across a mock restart.
  - `_proc_08`/`_proc_0b` (`SetActiveProfileSetting`/`SetActiveCommonSetting`) are session-only,
    never persisted — **by design, not a bug**: this correctly mirrors the real device, where
    Active state is always re-derived from Stored NVM after a power-cycle.
  - `_proc_09` (`SetCommand`) — the toggle/trigger channel for ~20 command codes
    (`ToggleLidPosition`, `PrepareDescaling`/`ConfirmDescaling`/`CancelDescaling`,
    `TriggerFlushManually`, `ResetFilterCounter`, `ToggleOrientationLight`, `Stop`, etc., see
    `.claude/rules/ble-protocol.md` Layer 1 table) — only **2** codes are wired at all
    (`ToggleAnalShower`/`ToggleLadyShower`, correctly not persisted — they flip live SPL state).
    Every other code is a silent no-op: the real app or remote control can send them to the mock
    and nothing happens at all — not even a log line distinguishing "received but ignored" from
    "not received."

## 10. Decisions log

| # | Decision | Status |
|---|---|---|
| 1 | `--model` single open-ended lookup table (not `--protocol` + `--model` split) | **Resolved** — §3 |
| 2 | Standalone single-device mock scripts kept as thin wrappers around the refactored class, not retired | **Resolved** — §2 |
| 3 | Webui bind failure: abort whole service, or degrade that one device to headless? | Open — §9 |
| 4 | Frontend sharing: mock-only fresh generic module, not extracted from `index.html` | **Resolved** — §6 |
| 5 | Multi-device routing: each device keeps its own independent page (no landing page, no single-port merge) | **Resolved** — §6 |

## 11. Implementation plan & phase status

Ordered so each phase is independently testable before the next depends on it, and the §0
acceptance test gets proven on one device before multi-device orchestration is built on top
of it.

| Phase | Goal | Status |
|---|---|---|
| 1 | Shared modules: `mock_bluez_adapter.py`, `mock_persistence.py` | **Done** — `152382c`, `dadde00`, `0a85636` |
| 2 | Refactor Mera mock into an importable class | **Done** — verified on VM, see below |
| 2b | Real settings mutation + persistence wiring for Mera (follow-up) | **Done** — verified on VM, see below |
| 2c | Per-device identity (`_SAP_NUMBER`/`_SERIAL`) for Mera (follow-up) | **Not started** — same gap as Alba's §5 fix, not yet ported to Mera |
| 3 | Refactor Alba mock into an importable class | **Done** — verified on VM, see below |
| 4 | `mock_service.py` orchestrator, single device only | **Done** — verified on VM, see below |
| 5 | Multi-device concurrency | **In progress** — validation + fixes done, live concurrent-hardware test pending, see below |
| 6 | Webui, multi-device | **Done, VM-verified** (2026-07-16) — generic settings table (mock-controls.js/css) + write routes for Mera and Alba (§6 "DRY...") + real SSE via `/events` replacing full-page-reload polling (§6 "Real SSE..."); regression tests in `tests/test_mera_mock_webui.py`/`test_alba_mock_webui.py` |
| 7 | Logging polish (combined + per-device files) | **Done, VM-verified** (2026-07-16) — `mock_logging.py` shared module, full print()→logger conversion for Alba, see §7 |
| 8 | Sela mock (separate pre-existing roadmap item; plugs into the same class/registry pattern once built) | Not started |
| 9 | Firmware override parsing *(future, §4)* | Not started |
| 9a | Firmware version persistence (Mera + Alba) | **Done** — see §5 "Firmware version persistence" |
| 9b | Firmware update-process simulation (Mera `0x40` proc sequence; Alba TBD) | Not started — depends on 9a |
| 9c | Firmware profile selector — Mera done, Alba pending a real capture | **Mera done, VM-verified** (2026-07-16) — see §6 "Firmware profile selector"; Alba not started |
| 10 | Wire remaining Mera `SetCommand` (proc 0x09) codes — action simulation, not persistence | Not started — see §9 |

### Phase 2 — scope decision (2026-07-16)

Checked `_dispatch()` in `tools/mock-geberit-mera.py` before wiring persistence in, rather
than assuming there was already something to persist: **every write procedure the mock
currently handles is a no-op stub** — `0x09` (SetCommand), `0x08`/`0x14`/`0x15`, `0x0B`
(SetActiveProfileSetting) all just `return b""`. Nothing mutates state, not even
in-memory. So there is currently no real setting on the Mera mock that the Home App could
change — the §0 acceptance test has no genuine hook to attach to yet on this model.
Wiring `mock_persistence.py` against no-op stubs would prove nothing.

**Decided scope for Phase 2:**
- New module `aquaclean_ble_relay/mera_mock.py`, class `MeraMock` — a structural port of
  `tools/mock-geberit-mera.py`: module-level globals become instance attributes, a
  per-instance logger replaces the single hardcoded `logging.getLogger("mera_mock")`,
  adapter selection goes through `mock_bluez_adapter.py` instead of the script's own inline
  adapter lookup.
- `tools/mock-geberit-mera.py` is left completely untouched. Its logic is duplicated into
  the new class for now, not shared — accepted temporarily; a later phase decides the
  cutover to a thin wrapper (§2, decision 2), once the class is proven.
- The currently-stubbed `Set*` procedures are ported as the same no-op stubs — behavior
  unchanged, no new mutation logic in this phase.
- `mock_persistence.py` wiring is **deferred to Phase 2b**, not done in Phase 2.
- No acceptance-test run in Phase 2 (needs Phase 2b's real mutation first).

**Phase 2b — refined plan (2026-07-16):**

Cross-checking `_PROC_NAMES`, the dispatch comments, and `.claude/rules/ble-protocol.md`
before implementing found a real bug already present in the ported code (not introduced by
Phase 2, just carried over faithfully since Phase 2 was structural-only):

- `0x51`/`0x52` (GetStoredCommonSetting/SetStoredCommonSetting) and `0x53`/`0x54`
  (GetStoredProfileSetting/SetStoredProfileSetting) are unambiguous — confirmed in both
  `_PROC_NAMES` and `ble-protocol.md`.
- `0x09` (SetCommand, 1-byte command code) and `0x08` (SetActiveProfileSetting, confirmed
  format `[arg_count=3, setting_id, value]` from a real OTA capture) are also unambiguous.
- **Bug found:** `_PROC_NAMES` labels `0x0A`/`0x0B` as `GetActiveCommonSetting`/
  `SetActiveCommonSetting`, matching `ble-protocol.md`'s "Active vs Stored" section (0x0A/0x0B
  operate on the *same CommonSetting ID space* as 0x51/0x52, applied immediately, no
  power-cycle). But the shipped `_proc_0a()` docstring says `GetActiveProfileSetting` and
  reads `_ACTIVE_PROFILE_SETTINGS` (a *ProfileSetting*-shaped dict) — contradicting its own
  proc's name. Fixed in this phase: `0x0A`/`0x0B` now read/write a session-scoped
  active-common-setting store; `_ACTIVE_PROFILE_SETTINGS` becomes the write-target for `0x08`
  instead (which never had a confirmed getter of its own).

**Implementation:**
1. `0x0A`/`0x0B` → an in-memory "active common setting" store, seeded from
   `_STORED_COMMON_SETTINGS` at mock startup. Session-scoped, **never persisted** — matches
   the rule that Active is re-derived from Stored NVM on every real power-cycle. Seeded once
   at mock startup (not re-seeded per BLE session) — a deliberate scope simplification.
2. `0x52`/`0x54` → real mutation into `_STORED_COMMON_SETTINGS`/`_STORED_PROFILE_SETTINGS`,
   **wired to `mock_persistence.py` write-through** — these are exactly the values the §0
   acceptance test needs to survive a restart. Arg format assumed identical to `0x08`'s
   confirmed `[count=3, setting_id, value]` shape (structurally the same setter pattern for
   the sibling Stored pair) — not independently confirmed for `0x52`/`0x54`, flagged in code,
   verify against a real capture if one surfaces.
3. `0x08` → real mutation into `_ACTIVE_PROFILE_SETTINGS`, in-memory only, not persisted.
4. `0x09` → real mutation for the two commands with an unambiguous `spl` effect:
   `ToggleAnalShower` (code 0) flips `spl[1]`, `ToggleLadyShower` (code 1) flips `spl[2]`.
   Both are classified **NO PERSIST** (live sensor state) in the roadmap enumeration, so no
   persistence call here — only `_SPL_MERA_VALUES` mutates. Other command codes stay no-ops;
   not guessing effects that aren't confirmed anywhere.
5. `MeraMock.__init__` gains a `state_dir` parameter (calls `mock_persistence.set_state_dir()`
   once — this is process-wide, since all instances share one DB file) and loads persisted
   `common_setting:*`/`profile_setting:*` rows for `(device_type="mera", device_key=adapter)`
   at construction, overriding the hardcoded defaults only where a persisted value exists —
   startup never overwrites an existing store (§5).
6. §0 acceptance test run for real against Mera once this lands: change a Stored setting via
   the Home App, stop the mock, restart, confirm the value survived.

### Phase 2b — verification (2026-07-16)

Same VM, same technique as Phase 2 (byte-for-byte comparison + a scripted round-trip),
covering what a single live-App session can't easily exercise in one pass:

1. **No regression on the 10 procedures Phase 2b didn't touch** (`0x82, 0x0D, 0x0E, 0x45,
   0x59, 0x07, 0x05, 0x81, 0x86, 0x55`) — re-ran the byte-for-byte comparison against
   `tools/mock-geberit-mera.py`; all still match.
2. **Stored settings persist and survive a simulated restart.** `SetStoredCommonSetting`
   (`0x52`, WaterHardness id=0 → 2) and `SetStoredProfileSetting` (`0x54`, AnalShowerPressure
   id=2 → 4) both mutate immediately and are still present after destroying the `MeraMock`
   instance and constructing a fresh one against the same `state_dir`/adapter — this **is**
   the §0 acceptance test, run against real persistence logic (not yet against a live BLE
   session).
3. **Active settings correctly do NOT persist.** Writing `SetActiveCommonSetting` (`0x0B`,
   id=0 → 6) is immediately visible via `GetActiveCommonSetting` (`0x0A`) within the same
   instance, but after a simulated restart `0x0A` returns `2` — re-seeded from the (persisted)
   Stored value, not the prior session's transient override. Confirms the "Active is
   session-scoped, re-derived from Stored NVM on power-cycle" rule holds.
4. **SetCommand toggle correctly does NOT persist.** `ToggleAnalShower` (`0x09`, code 0)
   flips `spl[1]` to `1281` (verified byte-for-byte in the little-endian frame payload); after
   a simulated restart it's back to `0`.
5. **Nothing leaks into the DB that shouldn't.** Raw persisted rows after the whole sequence:
   exactly `{'common_setting:0': 2, 'profile_setting:2': 4}` — no `active_*` or `spl` keys.

Not yet run: a live session against the real Geberit Home App changing a setting through
its own UI (the scripted test drives `_dispatch()` directly, bypassing GATT/BLE). Worth
doing once there's a Home App screen that actually calls `0x52`/`0x54` for a setting this
mock exposes — flagged as a follow-up, not blocking, since the underlying mutation +
persistence logic is now verified directly.

### Phase 2 — verification (2026-07-16)

Two independent checks, both against the real `hci0` adapter on the mock VM
(`anneubuntu-studio`, `/home/jens/aquaclean_ble_relay/`), not just a syntax check:

1. **Byte-for-byte dispatch comparison.** Loaded `tools/mock-geberit-mera.py` and the new
   `MeraMock` class in the same interpreter and called `_dispatch()` with identical
   `(ctx, proc, args)` for all 13 implemented procedures (`0x82, 0x51, 0x53, 0x0A, 0x0D,
   0x0E, 0x45, 0x59, 0x07, 0x05, 0x81, 0x86, 0x55`). **All 13 produced identical output
   frames.** This is the part a live BLE run can't easily exercise for every procedure in
   one pass, so it was checked directly.
2. **Live run against the real Geberit Home App.** Started
   `python3 -m aquaclean_ble_relay.mera_mock --port 8765 --adapter hci0` under `sudo` on
   the VM and connected with the real app. Log confirms: adapter correctly resolved via
   `select_adapter` (`Adapter: A0:AD:9F:72:C4:0F path: /org/bluez/hci0`), GATT registered,
   all four notify characteristics (A5–A8) wired, advertisement started with the correct
   article/company ID, a real device connected, and `GetFirmwareVersionList` /
   `GetSystemParameterList` / `GetFilterStatus` all completed with every multi-frame
   response fully ACKed. No warnings, errors, or tracebacks in the 494-line log; clean
   shutdown on Ctrl+C. The app showed "Das Gerät benötigt einen Firmware-Update" —
   confirmed to be identical behavior to `tools/mock-geberit-mera.py` (both report the same
   real per-component firmware versions shipped since v1.76.0b1), not a regression from
   this refactor.

Not yet exercised: the multi-instance-specific paths (adapter-tagged D-Bus app paths and
log filenames, `_hci_index()` for a non-`hci0` adapter) — the VM only has one physical
adapter, so this could only be verified with `adapter=None`/`"hci0"`. Multi-adapter
behavior will get its first real test in Phase 5.

### Phase 3 — Alba: scope and how it differs from Mera's Phase 2/2b (2026-07-16)

Read `tools/mock-geberit-alba.py` fully before deciding scope, rather than assuming it
needed the same two-phase treatment as Mera. It doesn't:

- **The DpId store and Arendi crypto session were already instance-scoped classes**
  (`_Ble20AppLayer`, `_AriendiServerSide`), not module globals — `grep '^    global '`
  returns nothing in the whole file. Only one true module-level mutable existed
  (`_VERBOSE`, and it's dead — set but never read, in both the original script and this
  port). So the "globals → instance attributes" work Mera's Phase 2 needed was already
  done; this phase mainly wrapped `main()`'s ~600-line orchestration body into
  `AlbaMock.run()`, with `mode`/`adapter_name`/`send_delay_sec`/`web_port` becoming
  `self.*` at their ~11 actual touch points. Its nested closures needed no changes — they
  already close over `main()`'s locals, which are just as valid as a method's locals.
- **`_Ble20AppLayer._write()` already does real mutation** — nothing was stubbed, unlike
  Mera's Phase 2 finding. So persistence wiring is included in this same phase, not split
  into a Phase 3b.
- **Every DpId row already carries a `behavior` field** (0=Info 1=Status 2=Command 3=Nvm
  4=Protected) in `_DEFAULT_STORE`. Only `behavior==3` (Nvm) is a genuinely durable
  setting — the persist decision falls straight out of data already in the table, no
  separate namespace/persist classification needed (unlike Mera's multiple overlapping
  index spaces). Six DpIds are Nvm today: 13 (ACCESS_CODE), 580–583 (STORED_ANAL_SPRAY_*),
  795 (DEMO_MODE).
- **`_Ble20AppLayer` is deliberately reconstructed fresh every BLE session** (unchanged
  behavior — simulates a clean device state machine per connection). Each fresh
  construction now reloads persisted Nvm values from `mock_persistence.py`, so this
  "fresh per session" design ends up giving Alba's mock *better* restart fidelity than
  Mera's for free: a setting survives not just a mock restart but every single new BLE
  session, without needing a Mera-style "seed once at process start" step.
- **Logging conversion is deferred to Phase 7, deliberately, unlike Mera.** Mera had one
  hardcoded logger, trivially replaced. Alba uses a module-level timestamped `print()`
  override across hundreds of call sites in `_Ble20AppLayer`, `_AriendiServerSide`, and
  the session loop. Converting those is exactly Phase 7's scope ("Logging polish") —
  doing it piecemeal here would leave a half-converted mix of `self.logger` and `print()`,
  worse than deferring wholesale. `AlbaMock` has no `self.logger` in this phase.
- **D-Bus GATT app paths tagged with the adapter**, same reasoning as Mera's Phase 2 —
  the original hardcoded paths (`/org/bluez/example/geberit` etc.) would collide the
  moment a second instance runs in the same process.
- **Adapter selection** goes through the shared `mock_bluez_adapter.select_adapter`,
  removing this script's own byte-identical inline copy (confirmed identical before
  removing it — no behavior change).

### Phase 3 — verification (2026-07-16)

Same VM, same techniques as Phase 2/2b:

1. **Import-only smoke test** — clean.
2. **Byte-for-byte protocol comparison.** Instantiated `_Ble20AppLayer` from both
   `tools/mock-geberit-alba.py` (deployed to the VM matching the current repo hash,
   confirmed via `sha256sum`) and the new `aquaclean_ble_relay/alba_mock.py` in the same
   interpreter. `_inventory()` matched byte-for-byte (80 DpIds); `_read()` matched
   byte-for-byte for **all 79 addressable DpIds** in `_DEFAULT_STORE`.
3. **Persistence round-trip.** Writing DpId 580 (`STORED_ANAL_SPRAY_INTENSITY`, Nvm)
   persists immediately (logged `— persisted`) and survives constructing a fresh
   `_Ble20AppLayer` with the same `device_key` (simulated new session/restart). Writing
   DpId 564 (`ANAL_SHOWER_STATUS`, Status not Nvm) does **not** log persisted and correctly
   reverts to its default in a fresh instance. Raw DB after the sequence: exactly
   `{'dpid:580': '02'}` — nothing else leaked in.

Not yet run: a live session against the real Geberit Home App or the bridge's
`Ble20Client` completing the full Arendi handshake against this class (the scripted tests
drive `_Ble20AppLayer`/`_dispatch_sync` directly, bypassing the handshake and D-Bus/GATT
layers — same limitation noted for Mera's Phase 2b). Flagged as a follow-up, not blocking,
since the underlying protocol logic and persistence are now verified directly and the
D-Bus/GATT/advertisement wiring is a near-verbatim port of Mera's already-verified pattern.

### Phase 4 — `mock_service.py`, single device (2026-07-16)

**Motivation, ahead of schedule:** the plan had logging as Phase 7, after multi-device
(Phase 5). Brought forward to Phase 4 because testing `alba_mock.py` directly hit the exact
problem Phase 7 exists to solve — Alba has no log file at all (print()-only), so every test
required manually retyping a `| tee <name>.log` filename. `mock_service.py` gives every run
an auto-named log file now, without waiting for the full per-device-logger conversion.

**What's implemented:**
- `--device model=NAME,adapter=HCI[,...]` — the §3-decided single open-ended `--model`
  lookup table. Each registry entry carries a class *and* that model's sensible defaults:
  `_MODEL_REGISTRY = {"mera": {"cls": MeraMock, "defaults": {}}, "alba": {"cls": AlbaMock,
  "defaults": {"mode": "ble20"}}}`. Every field besides `model` is passed straight through
  as a constructor kwarg to whichever class `model` maps to (numeric-looking values coerced
  to `int`/`float`), merged over the registry defaults — explicit `--device` fields always
  win. This is what lets `mode=`/`send_delay_sec=` (Alba-only) and `web_port=`/`state_dir=`
  (both) reach the right model without `mock_service.py` hardcoding either model's parameter
  list.
- **Model-specific defaults, not just the class.** `AlbaMock` itself still defaults to
  `mode="unsupported"` — faithful to the original script, which deliberately uses that as
  its own default to test the HACS unsupported-device screen. But nobody saying "mock an
  Alba" through this orchestrator wants that by default; they want the functional protocol.
  So the *registry* overrides it to `mode="ble20"`, leaving `AlbaMock`'s own default
  untouched — `model=alba,adapter=hci0` alone now gives a fully functional Alba mock;
  `model=alba,adapter=hci0,mode=unsupported` still reaches the original behavior explicitly.
- `--state-dir` (global) — passed to the model as `state_dir`; also anchors the
  auto-named log file at `<state_dir>/logs/mock-<model>-<adapter>_<timestamp>.log`.
- `--list-models` — enumerates the registry *and* each model's defaults (§3's
  discoverability requirement) — e.g. prints `alba (defaults: {'mode': 'ble20'})`.
- Startup validation at parse time, not after connecting to D-Bus: unknown model, malformed
  `--device` field, and more than one `--device` (Phase 4 is deliberately single-device
  only — multi-device is Phase 5) all fail with a clear `argparse` error before anything
  BLE-related happens. An unexpected kwarg for a given model (e.g. `mode=` for Mera, which
  has no such parameter) fails the same way via a caught `TypeError`.
- Logging: process-wide `sys.stdout`/`sys.stderr` tee to the auto-named file — deliberately
  an interim, single-device-appropriate solution (module docstring flags this explicitly).
  It cannot separate concurrent devices' output, so Phase 5 + Phase 7 need to replace it
  with true per-device handlers. Running `MeraMock` through this means its output lands in
  both the tee file and Mera's own independent per-adapter log file (Phase 2) — redundant
  but harmless; also cleaned up in Phase 7, not this phase.

**Verified on the mock VM** (`--list-models`, `--help`, and all four error paths — unknown
model, malformed field, >1 `--device`, bad kwarg for a model — each produces the expected
error and exit code 2, confirmed by hand).

**Bug found on the first real run (2026-07-16), fixed same day:** `--mode ble20` starts a
`uvicorn` web server (the NOTIFY control UI); `uvicorn.Config.__init__` configures its
logging and calls `sys.stdout.isatty()`. `_Tee` only implemented `write()`/`flush()`, so
this crashed with `AttributeError: '_Tee' object has no attribute 'isatty'`. Fixed by adding
`isatty()` (delegated to the original console stream, not the log file) plus a generic
`__getattr__` fallback delegating anything else (`fileno`, `encoding`, ...) to the same
stream — so the next library that probes an unexpected file-like attribute on `sys.stdout`
doesn't hit the same class of bug. This is exactly the kind of thing the "not yet run: a
live session" caveats on Phases 2b/3/4 were flagging — verified logic only goes so far.

**Live end-to-end run against the real Geberit Home App (2026-07-16), after the `isatty`
fix:** `mock_service.py --device model=alba,adapter=hci0` (defaulting to `mode=ble20` per
the registry-defaults fix above) — real device connected, full Arendi handshake completed
three times across reconnects, 268 lines of Inventory/Read activity. This closes the "not
yet run: a live session" gap noted in Phase 3's verification.

Getting here also surfaced a real, non-code gotcha — **not specific to Alba** — now
documented in `docs/developer/mock-geberit-alba.md` §"Known behaviour/gotchas" #5: the
Geberit Home App would not discover the Alba mock at all while it still had a *different*
mock model (Mera) registered as a known device at the same adapter's BLE MAC. The app's
device list is keyed by MAC, not by GATT profile, so this bites in either direction — a
Mera entry blocks a later Alba mock at the same MAC, and equally an Alba entry would block
a later Mera mock, or any future model sharing that adapter (Sela, once it exists). Deleting
the stale app-side entry fixed it immediately. A `btmon` capture during the failure
initially pointed at BT5 Extended Advertising (the same issue Mera hit in June,
`e905a33`/`e05fc99`) as a plausible cause; that turned out to be a red herring — the
advertisement payload was confirmed byte-identical to the known-working original script.
Worth remembering before assuming a code regression: check the app's own device list first
when switching `--device model=` between test sessions on a shared `adapter=`.

### Phase 5 — multi-device concurrency (2026-07-16)

**Reframed the scope while implementing.** The original plan assumed devices need separate
adapters. Wrong: BlueZ supports multiple GATT applications and multiple advertisement
instances per adapter (confirmed — the mock VM's single adapter reports
`SupportedInstances: 0x03`), so two devices *can* share one adapter. The real constraint is
narrower: no two devices may register under the same D-Bus object paths or bind the same
TCP port. Found and fixed three concrete bugs this exposed, none of which were visible with
only one device running (Phases 2–4):

1. **D-Bus GATT app path collision between different models on the same adapter.**
   `mera_mock.py`/`alba_mock.py` tagged app paths by adapter only — `battery`/`dis` are
   generic service names both models use, so a Mera and an Alba mock sharing an adapter
   would collide on `/org/bluez/example/battery_hci0` etc. Fixed: paths now prefixed by
   model name *and* adapter in both files.
2. **`MeraMock`'s `_emit_interface_added` suppression patched the class, not the
   instance.** Flagged as a known limitation back in Phase 2 ("revisit before Phase 5").
   Fixed: patches `bus._emit_interface_added` as an instance attribute on that device's own
   `bus` object (Python attribute lookup checks the instance `__dict__` before the class,
   so this shadows the class method for one bus only) instead of
   `dbus_next.message_bus.BaseMessageBus` at the class level. Two concurrent registrations
   no longer race each other's patch/restore.
3. **Web UI port collision.** `MeraMock` and `AlbaMock` both default `web_port=8765` and
   each binds a real listener there (Mera always; Alba whenever `mode="ble20"`, the
   registry default) — found by actually running two devices together, where it surfaced as
   `OSError: address already in use` deep inside `uvicorn`'s startup, on the second
   device's task, well after the first was already running. Fixed with the same fail-fast
   pattern as the `(model, adapter)` duplicate check: 2+ `--device` entries now require an
   explicit, distinct `web_port=` on every one, checked before any device starts.

**`mock_service.py` changes:**
- Removed the "exactly one `--device`" restriction; each spec becomes its own `asyncio`
  task, launched together via `asyncio.gather` inside one `asyncio.run`.
- Duplicate-`(model, adapter)` pairs are rejected at parse time (sharing an adapter across
  *different* models is fine and now the tested path — see below).
- All requested adapters are validated to exist via one throwaway D-Bus connection
  *before* any device starts, so a typo'd `--adapter` fails the whole batch immediately
  rather than only the affected device failing deep inside GATT registration.
- Log filename now reflects the whole device batch (`mock-<model1>-<adapter1>+<model2>-
  <adapter2>_<timestamp>.log`) for 2+ devices; unchanged single-device filename for one.
  Still one combined `sys.stdout`/`sys.stderr` tee — cannot separate concurrent devices'
  interleaved lines into distinct files; that's still Phase 7.

**Verified on the mock VM (no sudo needed for these — pure validation/parsing logic):**
missing `--device`, duplicate `(model, adapter)`, unknown adapter (fails the whole batch,
confirmed via a real throwaway D-Bus connection listing `hci0` as the only available
adapter), missing `web_port` with 2+ devices, and duplicate `web_port` — each produces the
expected `argparse`-level error before touching any BLE/D-Bus registration.

**Correction (2026-07-16, same day): sharing one adapter is not a good test of "the Geberit
Home App discovers two devices independently."** The BlueZ-multiplexing argument above is
correct at the D-Bus/GATT/advertising-*instance* level, but it doesn't mean two devices
sharing an adapter are independently discoverable by the app. A BLE advertisement is
transmitted from the adapter's own BD address (MAC); `bluez_peripheral`'s simple
`Advertisement` object doesn't configure per-instance private/random addressing, and this
adapter advertises with its real public MAC (confirmed, privacy off — see
`memory/mock-ble-advertising-mac.md`). So two advertisement instances registered on the
same adapter very likely transmit **two different payloads from the identical MAC,
simultaneously** — which reproduces (arguably worsens) the exact MAC-keyed-device-list
confusion documented in the Phase 4 gotcha (§"mock-geberit-alba.md" #5), just concurrent
instead of sequential.

**What sharing one adapter *is* still good for:** testing the mocks' own GATT/protocol/
persistence correctness concurrently, via direct-connect tooling (bleak scripts,
`gatttool`, a bridge-side automated test) that connects by MAC + specific service UUID
rather than relying on the Home App's scan-and-match heuristics.

**What it's *not* good for:** getting the real Geberit Home App to treat two mocks as
independently discoverable at once. That specific test needs two physically separate
adapters (two distinct MACs) — a hardware requirement for *that* test, not a code
limitation. The mock VM turns out to have exactly that available:
```
hci0  A0:AD:9F:72:C4:0F  (Realtek, BT 5.1)  — used in all testing so far
hci1  00:1A:7D:DA:71:13  (Cambridge Silicon Radio, BT 4.0) — previously unused
```

**Not yet verified: two devices actually registering and advertising concurrently on real
hardware.** This needs `sudo` (not available to me directly over SSH) — first test, using
the two separate adapters above to sidestep the MAC-sharing issue entirely:
```bash
sudo /home/jens/venv/bin/python3 -m aquaclean_ble_relay.mock_service \
  --device model=mera,adapter=hci0,web_port=8765 \
  --device model=alba,adapter=hci1,web_port=8766
```
Expect both to register GATT services and advertise independently, each under its own
adapter's MAC. Worth checking `bluetoothctl show` mid-run for both adapters' state, and
discovering/connecting to each independently from the Geberit Home App (remembering the
Phase 4 gotcha: delete any stale device entry from a previous single-device test first — a
fresh MAC per adapter should mean this is now a non-issue for this specific test, but the
first `hci0` run reused the same MAC as every earlier test this session).

A **second, separate test** — two devices sharing `hci0` (the original Phase 5 plan) —
remains worth doing to confirm the D-Bus/GATT-level fixes (path prefixing, instance-scoped
`_emit_interface_added`) work under real concurrent registration, but verify it via direct
BLE connect tooling, not the Home App's scan, per the correction above.

**First live run (2026-07-16), `hci0`=Mera + `hci1`=Alba:** Alba connected and saved
cleanly. Mera hit an unrelated, pre-existing bug — not a Phase 5 issue, present in
`tools/mock-geberit-mera.py` unmodified — an iOS system pairing dialog interrupted the
connection. Root cause and fix: see `docs/developer/mock-geberit-mera.md` §"Battery plugin
interaction", "Regression and re-fix (2026-07-16, v1.77.0b1)". Once past that, Mera reached
the already-known-expected "firmware update required" state (real per-component firmware
versions). Concurrent registration itself (the actual Phase 5 subject) was not the blocker.

### Phase 9b — firmware-update process simulation, minimal version (2026-07-17)

**Implemented (Mera only — no real Alba firmware-update capture exists yet, so this is not
a parity violation, just nothing to base an Alba equivalent on).** `mera_mock.py` v1.83.0b1:
- `_dispatch()` gained an early `ctx`-first branch — `ctx=0x40` (plus its `ctx=0x00/proc=0x01`
  companion frame) routes to `_proc_fw_update()` before the existing proc-keyed chain, because
  `proc=0x52`/`0x53` collide numerically with `SetStoredCommonSetting`/`GetStoredProfileSetting`
  under the default ctx. See `.claude/rules/ble-protocol.md` § "Firmware update procedures
  (ctx=0x40)" for the full decoded sequence this implements.
- State machine `self._fw_update_state`: `idle -> started -> done -> rebooting -> idle`.
  `0x40/0x52` (StartFirmwareUpdate) starts it; `0x40/0x53` polls it (`05`=busy/`06`=done);
  `0x40/0x04` while `done` triggers finalize — applies the `"rs30"` firmware profile via the
  existing `_apply_firmware_profile()`/`_set_fw_version()` write hook (Phase 9a, previously
  unused), force-disconnects the current BLE link via `Device1.Disconnect()` (reusing the
  `self._bus`/`self._current_device_path` pattern already used by `_request_short_ci()`), then
  resets to `idle` after a delay so the app's natural reconnect sees updated firmware on the
  next `GetFirmwareVersionList`.
- `0x40/0x00` returns a fixed synthetic 12-byte keepalive payload; real captures show this
  value fluctuating per call without gating app progression, so an exact match wasn't pursued.

**Deliberately minimal — deferred to a follow-up pass:**
- **Timers are shortened**, not byte-exact: `_FW_UPDATE_BUSY_SECONDS=20` (real flash window:
  ~164s) and `_FW_UPDATE_REBOOT_SECONDS=8` (real reboot silence: ~13.3s).
- **No progress-notify frames** on the A5 channel (real device emits spontaneous
  `progress:u32-LE` notifications during the flash window; the app doesn't appear to rely on
  them for the state transition, only on the `0x40/0x53` poll).
- **No inspection of the ~290KB bulk firmware-binary transfer** the app writes on the A1-A4
  characteristics during the flash window (not proc-framed). The generic frame parser
  (`_handle_request`) is expected to tolerate this arbitrary binary without crashing — bytes
  that happen to parse as a plausible-looking header just produce harmless spurious "unknown
  proc" responses the app isn't waiting on — but this was reasoned from code inspection, not
  yet exercised against the real bulk-write traffic pattern on the VM. Watch mock logs during
  first live test for unexpected volume/errors during the simulated flash window.
- **No byte-count/progress logging** of that bulk transfer for observability (explicitly
  deferred at the user's request, to keep the first pass minimal).
- **No checksum/content validation** of the transferred firmware image — not needed, since
  completion is signalled by the `0x40/0x53` poll, not by the transfer itself.

If any of the above turns out to matter in practice (e.g. the app times out waiting for a
progress notification, or the bulk write does crash the parser), promote it out of this list
and implement it.
