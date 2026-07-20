# Mock Service — Requirements Definition

**Scope:** `mock_service.py` (thin CLI orchestrator) + `aquaclean_ble_relay/mera_mock.py` /
`alba_mock.py` (importable per-model classes) + shared modules (`mock_persistence.py`,
`mock_bluez_adapter.py`, `mock_logging.py`) + the planned Sela mock.

See `docs/roadmap.md` → "Mock service: Mera namespace/index enumeration" for the full
per-index persistence table REQ-014's schema is built on — not duplicated here.

See `docs/developer/ble-relay-rest-api-requirements.md` for the separate requirements
definition covering a REST API between the bridge and this mock-service — the interface the
bridge's device-client code would use to reach a simulated device over HTTP instead of BLE,
in support of `docs/roadmap.md`'s "Geberit AquaClean application-layer BLE relay" ("Alba-Hub")
design. That document is a first draft, not yet comprehensive.

**Document structure, refactored 2026-07-19, formalized 2026-07-20:** follows
`docs/developer/requirements-document-standard.md` — every detailed requirement below has a
unique `REQ-NNN` ID (stable — never reused or renumbered once assigned), a `Type` (`Functional`
| `Technical`), a declarative present-tense `Statement` of the actual/intended behavior, a
`Status` (`Open | In Progress | Done | Deferred | Superseded`), and — whenever `Status` is not
`Open` — `Implementation Details` carrying the full history: root causes, decisions,
verification evidence, bug postmortems, exact bytes/commits/dates. Implementation-time issues
that don't map to one specific requirement go in `## Issues` (`REQ-ISS-NNN`) near the end of
this document, not here. The previous "Decisions
log" (§10) and "Implementation plan & phase status" (§11) tables have been retired in favor of
this — every decision and every phase's status now lives on the REQ(s) it was actually about;
see "Retired sections" at the end for exactly where each one moved and a slim
phase-dependency-order table (the one thing the old phase table conveyed that isn't
otherwise implicit in a flat REQ list).

---

## Requirements Index

| ID | Type | Status | Summary |
|---|---|---|---|
| REQ-001 | Functional | Done | A setting changed via the app survives a mock restart, independently per device |
| REQ-002 | Technical | Done | `mock_service.py` contains no protocol logic; one script, N devices |
| REQ-003 | Technical | In Progress | One process/one event loop/one task per device; independent cleanup on shutdown |
| REQ-004 | Technical | Done | Each device task bound to its own adapter via shared, non-duplicated adapter-selection code |
| REQ-005 | Technical | Done | Repeatable `--device model=...,adapter=...,...` composite CLI flag, not zipped positional lists |
| REQ-006 | Technical | Done | Each mock is a refactored, instantiable class — no shared module-level state across instances |
| REQ-007 | Technical | Done | Single-device standalone scripts remain, as thin wrappers around the refactored class |
| REQ-008 | Functional | Done | Bad startup config (unknown model, adapter conflict, missing adapter) fails fast, before any BLE/D-Bus activity |
| REQ-009 | Technical | Done | Single open-ended `--model` registry maps a name to its protocol module + default identity |
| REQ-010 | Functional | Done | `--help`/`--list-models` enumerates every registered model and its defaults |
| REQ-011 | Functional | Superseded (by REQ-012) | `--firmware "RS28.0 TS199"` CLI override, parsed per-protocol |
| REQ-012 | Technical | Open | No hardcoded identity/firmware values — every value's source of truth lives in the persistence DB |
| REQ-013 | Functional | In Progress | Webui form-field editing of identity/firmware values |
| REQ-014 | Functional | Open | Webui import-from-file for identity/firmware values |
| REQ-015 | Functional | Open | Headless CLI file-based identity/firmware input (no webui) |
| REQ-016 | Functional | Open | Populate mock identity/firmware from a real device via the bridge |
| REQ-017 | Functional | Open | Webui button: save current live values as the new factory-settings baseline |
| REQ-018 | Functional | Open | Webui: save and name a settings profile |
| REQ-019 | Functional | Open | Webui: load a previously saved named settings profile |
| REQ-020 | Functional | Open | `aquaclean_ble_relay.mock_service --version` |
| REQ-021 | Technical | Done | One shared SQLite file; per-device isolation via a composite primary key, not one file per device |
| REQ-022 | Technical | Done | Multiple per-model index spaces addressed via `namespace:index` encoding in one free-form key column |
| REQ-023 | Technical | Done | Datatype/behavior/min/max metadata lives in static Python tables, never the DB |
| REQ-024 | Technical | Done | Persist-vs-live-only classification is a code-side decision, not a stored column |
| REQ-025 | Functional | In Progress | Every persist-classified setting round-trips a mock restart |
| REQ-026 | Functional | Done | Every setting write commits synchronously and immediately, never batched |
| REQ-027 | Functional | Done | Startup never overwrites an existing persisted value with a hardcoded default |
| REQ-028 | Functional | Done | Factory-reset acts on exactly one device's store, never all devices' |
| REQ-029 | Functional | Done | Each Alba mock instance has a stable, unique, persisted serial number and pairing PIN |
| REQ-030 | Functional | Superseded (by REQ-012) | Mera has the same per-instance identity treatment as Alba (Phase 2c) |
| REQ-031 | Functional | Done | Reported firmware/component versions persist across a mock restart, for both Mera and Alba |
| REQ-032 | Technical | Done | Each device keeps its own independent webui server/port — no unified landing page |
| REQ-033 | Functional | Done | Full per-device settings table (value/datatype/behavior/min-max) with inline edit and scoped factory-reset |
| REQ-034 | Functional | Done | Webui state updates live via SSE, not full-page-reload polling |
| REQ-035 | Functional | Done | Mera webui offers a firmware-profile selector between real captured snapshots |
| REQ-036 | Functional | Open | Alba webui offers the equivalent firmware-profile selector |
| REQ-037 | Technical | Done | Webui controls are built on one shared, generic, metadata-driven module — not per-feature bespoke markup |
| REQ-038 | Functional | Open | Mera webui has a "User sitting" simulation toggle, parity with Alba |
| REQ-039 | Technical | Done | One logger per device instance, keyed by `(model, adapter)` — never a shared hardcoded logger name |
| REQ-040 | Functional | Done | The device tag sits at a fixed position in every log line |
| REQ-041 | Functional | Done | Both a combined and a per-device log file exist simultaneously |
| REQ-042 | Technical | Done | Log filenames follow the same `<model>-<adapter>` convention as the persistence DB |
| REQ-043 | Functional | Done | `--btmon-capture` automates a clean btmon capture around a mock run |
| REQ-044 | Functional | In Progress | `--bluetoothd-debug` produces a working bluetoothd debug session |
| REQ-045 | Technical | Done | Adapter-selection logic is shared, never reimplemented per model |
| REQ-046 | Technical | Done | Persistence is one shared schema/module, reused by every model |
| REQ-047 | Technical | Done | CLI parsing, task orchestration, and shutdown handling live once, in `mock_service.py` |
| REQ-048 | Technical | Done | Firmware-string validation and namespace/persist classification follow one shared pattern per protocol module |
| REQ-049 | Functional | Open | A single device's webui bind failure has a defined effect on the rest of the service |
| REQ-050 | Functional | Open | The Mera mock simulates the effect of every `SetCommand` (proc `0x09`) code, not just two |
| REQ-051 | Functional | Open | The webui shows every piece of state the mock tracks, not a curated subset |
| REQ-052 | Functional | In Progress | A real Geberit Remote Control can discover, pair with, and connect to both mocks |
| REQ-053 | Functional | In Progress | The mock simulates the real device's `ctx=0x40` firmware-update BLE procedure sequence |
| REQ-054 | Functional | Open | The mock's post-update behavior reflects any BLE-observable delta a real update introduces, not just the version string |
| REQ-055 | Functional | Done | Active settings (`0x0A`/`0x0B`) are session-scoped, re-derived from Stored NVM on every restart, and never persisted themselves |
| REQ-056 | Functional | In Progress | Every BLE central connect/disconnect against a mocked device is visible live in that device's webui |
| REQ-057 | Functional | Open | Every `SetCommand` (proc `0x09`) code received from a central is visible live in the Mera webui, not only the two currently simulated |
| REQ-058 | Functional | Open | Every DpId write received from a central is visible live in the Alba webui, not only the six Nvm-persisted DpIds |
| REQ-059 | Technical | Done | Mera and Alba mock requirements/implementation stay in sync; a postponed sync is tracked as its own REQ, never a silent gap |
| REQ-060 | Technical | Open | Bridge and mock-service wiring stay in sync wherever applicable; a postponed sync is tracked as its own REQ or roadmap item, never a silent gap |
| REQ-061 | Functional | Open | Alba-Hub: Home App, Remote Control, and standalone bridge coexist without displacement |
| REQ-062 | Technical | Open | Alba-Hub peripheral advertises as the real device's own MAC (spoofed identity) |
| REQ-063 | Technical | Open | Alba-Hub central side maintains one permanent connection to the real device |
| REQ-064 | Technical | Open | Alba-Hub relay operates at the application (DpId) layer, not the BLE link layer |
| REQ-065 | Functional | Open | Alba-Hub DataPointInventory fetched once, served from cache to every client |
| REQ-066 | Functional | Open | Alba-Hub real-device notifications fan out to every connected client |
| REQ-067 | Technical | Open | Alba-Hub concurrent client writes are serialized |
| REQ-068 | Technical | Open | Alba-Hub is Alba-only; Mera parity not yet designed (cross-component-parity tracked gap) |

---

## Goal

### REQ-001 — A changed setting survives a mock restart, per device

#### Type

Functional

#### Statement

A setting changed via the Geberit Home App against a mocked device is still
present after the app is closed and `mock_service.py` is stopped and restarted. This holds
independently for every concurrently-running device.

#### Status

Done

#### Implementation Details

This is the acceptance test the rest of this document exists to
satisfy. Proven directly (not just by argument) for Mera in the Phase 2b verification pass:
`SetStoredCommonSetting` (WaterHardness id=0 → 2) and `SetStoredProfileSetting`
(AnalShowerPressure id=2 → 4) both mutate immediately and are still present after destroying
the `MeraMock` instance and constructing a fresh one against the same `state_dir`/adapter —
run against the real persistence logic, not yet against a live BLE session at that point. For
Alba, proven the same way in the Phase 3 verification pass: writing DpId 580
(`STORED_ANAL_SPRAY_INTENSITY`, Nvm) persists immediately and survives a fresh
`_Ble20AppLayer` construction with the same `device_key`; a non-Nvm DpId (564,
`ANAL_SHOWER_STATUS`) correctly does not survive, confirming the Nvm/non-Nvm boundary is
exactly right (raw DB after the sequence: exactly `{'dpid:580': '02'}`, nothing leaked).
Not yet run for either model: a live session where the *real* Geberit Home App itself
(not a script calling `_dispatch()`/`_dispatch_sync()` directly) changes a setting through
its own UI and that specific setting is confirmed to survive a restart — flagged as a
follow-up in both Phase 2b and Phase 3, not blocking, since the underlying mutation and
persistence logic is verified directly.

---

## CLI Entry Point & Multi-Device Orchestration

### REQ-002 — One thin orchestrator, no embedded protocol logic

#### Type

Technical

#### Statement

`mock_service.py` is the only script a user runs to start any mock. It parses
arguments, instantiates one device object per `--device` entry, and orchestrates them. All
BLE/protocol behavior lives in the per-model modules (REQ-006).

#### Status

Done

#### Implementation Details

Implemented as Phase 4 (single device) then Phase 5 (multiple
devices, see REQ-003). `--device model=NAME,adapter=HCI[,...]` resolves through the
`_MODEL_REGISTRY` (REQ-009); every field besides `model` passes straight through as a
constructor kwarg to whichever class `model` maps to (numeric-looking values coerced to
`int`/`float`), merged over the registry's own per-model defaults — explicit `--device`
fields always win. This is what lets `mode=`/`send_delay_sec=` (Alba-only) and
`web_port=`/`state_dir=` (both) reach the right model without `mock_service.py` hardcoding
either model's parameter list. `--state-dir` (global) is passed through as `state_dir` and
also anchors the auto-named log file (REQ-042).
**Bug found and fixed (2026-07-16):** `--mode ble20` starts a `uvicorn` server; `uvicorn`
calls `sys.stdout.isatty()` during its own logging setup. The interim single-device stdout/
stderr tee (`_Tee`, since replaced per-device by REQ-039's logger handlers) only implemented
`write()`/`flush()`, so this crashed with `AttributeError`. Fixed by adding `isatty()`
(delegated to the real console stream) plus a generic `__getattr__` fallback delegating
anything else (`fileno`, `encoding`, ...) — so the next library that probes an unexpected
file-like attribute on `sys.stdout` doesn't hit the same class of bug.
**Verified on the mock VM:** `--list-models`, `--help`, and all startup-validation error paths
(REQ-008) each produce the expected error/exit code. Live end-to-end run against the real
Geberit Home App (`mock_service.py --device model=alba,adapter=hci0`, defaulting to
`mode=ble20` per the registry): full Arendi handshake completed three times across
reconnects, 268 lines of Inventory/Read activity.
**Related operational gotcha (not a bug in this code, worth remembering):** getting to that
live run first hit the Geberit Home App refusing to discover the Alba mock at all, because a
*different* mock model (Mera) was still registered in the app's own device list at the same
adapter's BLE MAC — the app's device list is keyed by MAC, not GATT profile, so this bites in
either direction (a Mera entry blocks a later Alba mock at the same MAC and vice versa, and
will affect Sela once it exists). Deleting the stale app-side entry fixed it immediately. A
`btmon` capture during the failure initially pointed at BT5 Extended Advertising as a
plausible cause (the same issue Mera hit in June, commits `e905a33`/`e05fc99`) — that was a
red herring; the advertisement payload was confirmed byte-identical to the known-working
original script. Full detail also in `docs/developer/mock-geberit-alba.md` §"Known
behaviour/gotchas" #5. Worth checking the app's own device list first when switching
`--device model=` between test sessions on a shared adapter, before suspecting a regression.

### REQ-003 — Concurrent devices, isolated lifecycle

#### Type

Technical

#### Statement

One process runs one asyncio event loop with one task per configured device.
Each device task's shutdown (unsubscribe GATT notifications, write
`TimestampAtLastPowerdown`, close its DB connection, flush its log handler) is independent —
one device's slow or failed cleanup does not block or corrupt another's.

#### Status

In Progress

#### Implementation Details

The concurrency half is implemented and verified; the
shutdown-isolation half has no explicit test evidence yet (see caveat below).
Reframed while implementing Phase 5: the original plan assumed devices need separate
adapters. Wrong — BlueZ supports multiple GATT applications and multiple advertisement
instances per adapter (confirmed: the mock VM's single adapter reports
`SupportedInstances: 0x03`), so two devices *can* share one adapter. The real constraint is
narrower: no two devices may register under the same D-Bus object paths or bind the same TCP
port. Three concrete bugs found this way, invisible with only one device running (Phases
2–4), all fixed:
1. **D-Bus GATT app path collision between different models on the same adapter** — paths
   were tagged by adapter only; `battery`/`dis` are generic service names both models use, so
   a Mera+Alba pair sharing an adapter collided on e.g. `/org/bluez/example/battery_hci0`.
   Fixed: paths now prefixed by model name *and* adapter in both files.
2. **`MeraMock`'s `_emit_interface_added` suppression patched the class, not the instance** —
   flagged as a known limitation back when the Mera class was first written, revisited here.
   Fixed: patches `bus._emit_interface_added` as an instance attribute on that device's own
   `bus` object instead of `dbus_next.message_bus.BaseMessageBus` at the class level, so two
   concurrent registrations no longer race each other's patch/restore.
3. **Webui port collision** — both mocks default `web_port=8765` and each binds a real
   listener there; found via `OSError: address already in use` deep inside `uvicorn`'s
   startup on the second device's task. Fixed the same way as the `(model, adapter)`
   duplicate check (REQ-008): 2+ `--device` entries now require an explicit, distinct
   `web_port=` on every one, checked before any device starts.
**Correction found the same day:** sharing one adapter is not a good test of "the Geberit
Home App discovers two devices independently." The BlueZ-multiplexing argument above is
correct at the D-Bus/GATT/advertising-instance level, but `bluez_peripheral`'s `Advertisement`
object doesn't configure per-instance private/random addressing, and this adapter advertises
with its real public MAC (confirmed, privacy off — `memory/mock-ble-advertising-mac.md`). So
two advertisement instances on the same adapter very likely transmit two different payloads
from the identical MAC simultaneously — reproducing (arguably worsening) the MAC-keyed-device-
list confusion noted in REQ-002's gotcha, just concurrent instead of sequential. Sharing one
adapter is still good for testing GATT/protocol/persistence correctness concurrently via
direct-connect tooling (bleak, `gatttool`, a bridge-side automated test connecting by MAC +
service UUID); it is *not* good for getting the real app to treat two mocks as independently
discoverable — that needs two physically separate adapters (a hardware requirement, not a
code limitation). The mock VM has exactly that available (`hci0`
`A0:AD:9F:72:C4:0F` Realtek BT5.1; `hci1` `00:1A:7D:DA:71:13` CSR BT4.0, previously unused).
**First live run** (`hci0`=Mera + `hci1`=Alba): Alba connected and saved cleanly. Mera hit an
unrelated, pre-existing bug (not a concurrency issue — present in the original unrefactored
script too): an iOS system pairing dialog interrupted the connection (root cause/fix:
`docs/developer/mock-geberit-mera.md` §"Battery plugin interaction"). Once past that, Mera
reached the already-expected "firmware update required" state. Concurrent registration
itself was not the blocker.
**Not yet verified:** two devices actually registering and advertising concurrently, tested
via the two-separate-adapters plan above (needs `sudo`, not available over the SSH session
that did this work) — and, separately, no test evidence exists yet for the shutdown-isolation
half of this requirement's statement (one device's cleanup failure not affecting another's) —
`mock_service.py`'s use of `asyncio.gather` provides this by default at the asyncio level, but
it hasn't been deliberately exercised (e.g. by forcing one device's cleanup to raise).

### REQ-004 — Shared adapter selection

#### Type

Technical

#### Statement

Each device task is bound to its own BlueZ adapter through one shared
adapter-selection implementation (`mock_bluez_adapter.select_adapter`), not a per-model copy.

#### Status

Done

#### Implementation Details

Originally an Alba-only inline lookup (its own `--adapter`
feature); extracted to `mock_bluez_adapter.py` and reused by Mera and (once built) Sela — see
also REQ-045, which states the general "reuse, don't reimplement" policy this satisfies for
adapters specifically.

### REQ-005 — Composite `--device` CLI flag

#### Type

Technical

#### Statement

Each device is specified with one repeatable composite flag,
`--device model=NAME,adapter=HCI[,...]`, not zipped positional lists — avoiding
index-mismatch bugs between separate `--model`/`--adapter` list flags.

#### Status

Done

#### Implementation Details

See REQ-002's Implementation Details for exactly how fields
route to constructor kwargs via `_MODEL_REGISTRY`.

### REQ-006 — Each mock is an instantiable class

#### Type

Technical

#### Statement

Each mock model is a class (or async-factory) taking
`(adapter, variant, firmware, state_dir, ...)`, so N instances coexist in one interpreter
without clobbering each other's globals — not a script carrying module-level device dicts,
notify-handler tables, or one hardcoded logger.

#### Status

Done
**Implementation Details — Mera (`MeraMock`, `aquaclean_ble_relay/mera_mock.py`):**
`tools/mock-geberit-mera.py`'s `_dispatch()` was checked before wiring anything in, rather
than assuming there was something to persist: *every write procedure the mock handled was a
no-op stub* (`0x09` SetCommand, `0x08`/`0x14`/`0x15`, `0x0B` SetActiveProfileSetting all just
`return b""`) — nothing mutated state, not even in-memory, so REQ-001's acceptance test had no
genuine hook yet on this model. Decided scope: a structural port only for this first pass —
module-level globals become instance attributes, the hardcoded
`logging.getLogger("mera_mock")` is replaced by a per-instance logger, adapter selection goes
through REQ-004 instead of an inline copy; `tools/mock-geberit-mera.py` itself is left
untouched (its logic duplicated into the new class for now, accepted temporarily, see
REQ-007); the stubbed `Set*` procedures ported as the same no-op stubs, no new mutation logic
yet; real persistence wiring deferred to a follow-up pass (below).
Real mutation + persistence wiring, follow-up pass: cross-checking `_PROC_NAMES`, dispatch
comments, and `.claude/rules/ble-protocol.md` found a real pre-existing bug (carried over
faithfully from the structural port, not introduced by it): `_PROC_NAMES` labels `0x0A`/`0x0B`
as `GetActiveCommonSetting`/`SetActiveCommonSetting` (matching `ble-protocol.md`'s "Active vs
Stored" section — 0x0A/0x0B operate on the same CommonSetting ID space as 0x51/0x52, applied
immediately, no power-cycle), but the shipped `_proc_0a()` docstring said
`GetActiveProfileSetting` and read `_ACTIVE_PROFILE_SETTINGS` (a *ProfileSetting*-shaped
dict) — contradicting its own proc's name. Fixed: `0x0A`/`0x0B` now read/write a
session-scoped active-common-setting store (seeded from `_STORED_COMMON_SETTINGS` at mock
startup, never re-seeded per BLE session — a deliberate scope simplification); `0x52`/`0x54`
mutate `_STORED_COMMON_SETTINGS`/`_STORED_PROFILE_SETTINGS` for real, wired to
`mock_persistence.py` write-through (arg format assumed identical to `0x08`'s confirmed
`[count=3, setting_id, value]` shape — not independently confirmed for `0x52`/`0x54`, flagged
in code); `0x08` mutates `_ACTIVE_PROFILE_SETTINGS`, in-memory only; `0x09` gets real mutation
for the two commands with an unambiguous SPL effect (`ToggleAnalShower` code 0 flips
`spl[1]`, `ToggleLadyShower` code 1 flips `spl[2]`, both classified NO PERSIST — live sensor
state — so only `_SPL_MERA_VALUES` mutates, no persistence call; see REQ-050 for the ~18
other command codes, still no-ops); `MeraMock.__init__` gains `state_dir`, calls
`mock_persistence.set_state_dir()` once (process-wide, since all instances share one DB file
per REQ-021), and loads persisted rows at construction, overriding hardcoded defaults only
where a persisted value exists (REQ-027).
**Verification (Mera):** byte-for-byte dispatch comparison against the original script for
all 13 then-implemented procedures — identical output for every one. Live run against the
real Geberit Home App on the mock VM: adapter correctly resolved, GATT registered, all four
notify characteristics wired, advertisement correct, a real device connected, multi-frame
responses fully ACKed, clean shutdown on Ctrl+C, app behavior (firmware-update-required
screen) identical to the unrefactored script — confirmed not a regression. Follow-up
verification of the real-mutation pass, same VM: the 10 untouched procedures still matched
byte-for-byte (no regression); Stored settings persist and survive a simulated restart
(REQ-001); Active settings correctly do *not* persist (REQ-055); the `SetCommand` toggle
correctly does not persist; exactly the expected two rows landed in the DB after the whole
sequence, nothing else leaked in. Not yet exercised: multi-instance-specific paths
(adapter-tagged D-Bus app paths/log filenames for a non-`hci0` adapter) — the VM only has one
physical adapter available for this specific check; got its first real test in REQ-003.
**Implementation Details — Alba (`AlbaMock`, `aquaclean_ble_relay/alba_mock.py`):**
`tools/mock-geberit-alba.py` was read fully before assuming it needed the same two-pass
treatment as Mera — it didn't. The DpId store and Arendi crypto session were already
instance-scoped classes (`_Ble20AppLayer`, `_AriendiServerSide`), not module globals
(`grep '^    global '` returns nothing in the whole file; the one true module-level mutable,
`_VERBOSE`, was already dead — set but never read, in both the original script and this
port). So the "globals → instance attributes" work was already done; this pass mainly
wrapped `main()`'s ~600-line orchestration body into `AlbaMock.run()`. `_Ble20AppLayer._write()`
already did real mutation — nothing was stubbed, so persistence wiring landed in the same
pass, not a separate follow-up. Every DpId row already carries a `behavior` field (0=Info
1=Status 2=Command 3=Nvm 4=Protected) in `_DEFAULT_STORE`; only `behavior==3` (Nvm) is a
genuinely durable setting, so the persist decision falls straight out of existing data — no
separate namespace/persist classification needed, unlike Mera's overlapping index spaces.
Six DpIds are Nvm: 13 (ACCESS_CODE), 580–583 (STORED_ANAL_SPRAY_*), 795 (DEMO_MODE).
`_Ble20AppLayer` is deliberately reconstructed fresh every BLE session (unchanged — simulates
a clean device state machine per connection); each fresh construction now reloads persisted
Nvm values, so this "fresh per session" design gives Alba's mock *better* restart fidelity
than Mera's for free — a setting survives not just a mock restart but every single new BLE
session. Logging conversion was deliberately deferred rather than done piecemeal here (see
REQ-039's Implementation Details). D-Bus GATT app paths tagged with the adapter, same
reasoning as Mera. Adapter selection routed through REQ-004, removing this script's own
byte-identical inline copy (confirmed identical before removing it).
**Verification (Alba):** import-only smoke test clean. Byte-for-byte protocol comparison
between the original script and the new class in the same interpreter: `_inventory()` matched
for all 80 DpIds, `_read()` matched for all 79 addressable DpIds in `_DEFAULT_STORE`.
Persistence round-trip: writing DpId 580 (Nvm) persists immediately and survives a fresh
`_Ble20AppLayer` with the same `device_key`; writing DpId 564 (Status, not Nvm) correctly does
not persist; raw DB after the sequence exactly `{'dpid:580': '02'}`. Not yet run: a live
session against the real app or the bridge's `Ble20Client` completing the full Arendi
handshake against this class (same limitation as Mera's scripted-only verification) — flagged
as a follow-up, not blocking, since the D-Bus/GATT/advertisement wiring is a near-verbatim
port of Mera's already-verified pattern.

### REQ-007 — Standalone single-device scripts remain

#### Type

Technical

#### Statement

`tools/mock-geberit-mera.py` and `tools/mock-geberit-alba.py` keep working as
single-device scripts — each is a thin wrapper instantiating exactly one instance of the
refactored class (REQ-006), not retired.

#### Status

Done

#### Implementation Details

Decided explicitly (this is what the old §10 decision-log entry
#2 recorded) so the standalone scripts also serve as the regression baseline during the
refactor: before `mock_service.py` orchestration existed at all, each wrapper had to behave
identically to the pre-refactor script, and REQ-001's acceptance test was proven against each
standalone first (see REQ-006's verification notes) before multi-device orchestration was
built on top.

### REQ-008 — Fail-fast startup validation

#### Type

Functional

#### Statement

An unknown `--model` value, two `--device` entries pointing at the same
adapter, a named adapter that doesn't exist, an unexpected kwarg for a given model, a missing
`web_port=` with 2+ devices, and a duplicate `web_port=` each produce a clear error and a
non-zero exit code before the event loop starts or any D-Bus/BLE activity happens — never a
cryptic D-Bus error surfacing mid-run.

#### Status

Done

#### Implementation Details

All requested adapters are validated to exist via one throwaway
D-Bus connection before any device starts, so a typo'd `--adapter` fails the whole batch
immediately rather than only the affected device failing deep inside GATT registration.
Verified on the mock VM: missing `--device`, duplicate `(model, adapter)`, unknown adapter
(fails the whole batch, confirmed via a real throwaway D-Bus connection listing `hci0` as the
only available adapter at the time), missing `web_port` with 2+ devices, duplicate
`web_port`, and an unexpected kwarg for a model (caught via `TypeError`) — each produces the
expected `argparse`-level error and exit code 2.

---

## Model / Variant / Protocol Addressing

### REQ-009 — Single open-ended `--model` registry

#### Type

Technical

#### Statement

One registry maps a `--model` name to `(protocol_module, default_identity)`.
There is no separate `--protocol` flag — `--model` alone decides both which protocol module
to load and which device identity/variant to present within that family.

#### Status

Done

#### Implementation Details

Background for why this shape, not a `--protocol`+`--model`
split: `mera` and `sela` both speak the same legacy proc/ctx protocol (Sela = different
variant byte + different default identity strings + AcSela-only features like
`ToggleOrientationLight`); `alba` speaks an entirely different protocol (Ble20/Arendi). The
`--model` → protocol-module mapping is therefore not 1:1 with the module boundary — exactly
what an open-ended lookup table is for. Implemented as `_MODEL_REGISTRY` in `mock_service.py`
(`{"mera": {"cls": MeraMock, "defaults": {}}, "alba": {"cls": AlbaMock, "defaults":
{"mode": "ble20"}}}`). Registry defaults can differ from a class's own constructor default —
`AlbaMock` itself still defaults to `mode="unsupported"` (faithful to the original script,
which deliberately uses that to test the HACS unsupported-device screen), but nobody saying
"mock an Alba" through the orchestrator wants that by default, so the *registry* overrides it
to `mode="ble20"` while leaving the class's own default untouched —
`model=alba,adapter=hci0` alone now gives a fully functional Alba mock; an explicit
`mode=unsupported` still reaches the original behavior.

### REQ-010 — Registry is discoverable

#### Type

Functional

#### Statement

Since the model registry is open-ended and otherwise undiscoverable without
reading source, `mock_service.py --help` or `--list-models` enumerates every registered
model/variant value and its defaults.

#### Status

Done

#### Implementation Details

`--list-models` prints the registry and each model's defaults,
e.g. `alba (defaults: {'mode': 'ble20'})`. Verified by hand on the mock VM.

---

## Identity & Firmware Value Sourcing

### REQ-011 — `--firmware` CLI override *(superseded)*

#### Type

Functional

#### Statement

`--firmware "RS28.0 TS199"` overrides a model's default firmware string at
startup, parsed into whatever internal representation that protocol module already uses.

#### Status

Superseded by REQ-012

#### Implementation Details

Never implemented. Superseded 2026-07-19 by the broader REQ-012
through REQ-020 (configurable value sourcing generally, not just firmware, via more than one
CLI flag). Original design note, kept for anyone implementing an eventual single-value
CLI override anyway: parsing belongs inside each protocol module's own default-firmware
setter, not one shared parser in `mock_service.py`, since Mera's `_FW_COMPONENT_VERSIONS`
byte-tuples and Alba's own firmware DpId encoding aren't the same shape (REQ-048 still
applies at the "each protocol owns its own concrete parser" level); input would need
validation against a format regex at CLI-parse time.

### REQ-012 — No hardcoded identity/firmware values

#### Type

Technical

#### Statement

Every mock identity/firmware constant currently hardcoded as a Python literal
— Mera's `_FACTORY_IDENTITY`, `_FW_COMPONENT_VERSIONS`/`_FW_COMPONENT_VERSIONS_RS28`,
`_IDENTITY_REAL_REFERENCE` (`mera_mock.py`); Alba's `_DEFAULT_STORE` default values
(`alba_mock.py`) — has its actual value living in the persistence DB (REQ-021), not a Python
literal. A hardcoded literal remains acceptable only as a documented fallback default for a
never-configured fresh install, not as the primary source of truth.

#### Status

Open

#### Implementation Details

*(none — not started; recorded here per explicit request so the
scope is written down precisely before any code changes begin.)* Generalizes and supersedes
REQ-011 and REQ-030 (Mera per-instance identity, a narrower precursor). Also formalizes what
was informally discussed and deferred 2026-07-18 — see `docs/roadmap.md` §"Mera mock: 'real
reference' identity/firmware values are hardcoded to our one test device" and
`memory/mera-mock-real-values-hardcoded-deferred.md` for that history; this REQ is now the
authoritative statement, those are background only. Applies to both models — the mock
feature-parity policy (REQ-031's Implementation Details) still holds unless a protocol-level
reason blocks one side. Do not begin implementing without a separate go-ahead.

### REQ-013 — Webui form-field editing of identity/firmware

#### Type

Functional

#### Statement

A user can type a new identity or firmware value directly into a webui form
field, for any identity/firmware field on any model.

#### Status

In Progress

#### Implementation Details

Already exists for Mera identity/firmware, as part of REQ-037's
generic settings-table module (`/settings/identity/{field}`, `/settings/fw-component/{id}`
write routes). Needs the equivalent audit/completion for Alba, and confirmation that every
Mera identity/firmware field is actually covered (not just the ones exercised so far).

### REQ-014 — Webui import-from-file

#### Type

Functional

#### Statement

A user can upload or select a file in the webui and apply every value in it in
one action, instead of editing one form field at a time.

#### Status

Open

#### Implementation Details

*(none — not started.)* File format not yet decided; likely the
same JSON shape as REQ-015's headless file input, so one format serves both paths.

### REQ-015 — Headless file-based value input

#### Type

Functional

#### Statement

A new CLI arg (e.g. `--identity-file <path>`) lets identity/firmware values be
read at startup with no webui interaction at all, for scripted/automated setups.

#### Status

Open

#### Implementation Details

*(none — not started.)*

### REQ-016 — Populate from a real device via the bridge

#### Type

Functional

#### Statement

A script/mode connects to an actual Geberit device — reusing the standalone
bridge's existing `GetDeviceIdentification`/`GetFirmwareVersionList` client calls, no new
protocol code — and writes what it reads directly into the mock's persistence namespace, so
the mock's reference values are personal to whichever device a given user owns instead of
hardcoded to one specific test unit.

#### Status

Open

#### Implementation Details

*(none — not started.)* This is the requirement that ultimately
makes REQ-012 viable without losing the "shows the real device's actual values" behavior the
hardcoded constants currently provide.

### REQ-017 — Save current values as factory-settings baseline

#### Type

Functional

#### Statement

A webui button promotes whatever the mock is currently reporting into the
persisted "factory default" that a future factory-reset (REQ-028) returns to — distinct from
a normal field edit, which only changes the live value.

#### Status

Open

#### Implementation Details

*(none — not started.)*

### REQ-018 — Save and name a settings profile

#### Type

Functional

#### Statement

A webui action saves the current settings as a new, persisted, named snapshot
(e.g. "my real Mera Comfort", "test unit B") — not limited to a single factory baseline.

#### Status

Open

#### Implementation Details

*(none — not started.)*

### REQ-019 — Load a named settings profile

#### Type

Functional

#### Statement

A webui action selects a previously saved named snapshot (REQ-018) and applies
its values as the mock's current live state.

#### Status

Open

#### Implementation Details

*(none — not started.)*

### REQ-020 — `mock_service.py --version`

#### Type

Functional

#### Statement

`aquaclean_ble_relay.mock_service --version` prints the orchestrator's own
version and exits.

#### Status

Open

#### Implementation Details

*(none — not started.)* Distinct from each model's own
`_MOCK_VERSION` (e.g. `mera_mock.py`'s) — this is the `mock_service.py` CLI entry point's own
version, if it ends up having one separate from the per-model ones; clarify/establish that
distinction during implementation.

---

## Persistence

### REQ-021 — One shared DB file, composite-key isolation

#### Type

Technical

#### Statement

All devices share one SQLite file, `mock_state.db`, under a configurable
directory (`set_state_dir()`, driven by `mock_service.py --state-dir`). Per-device isolation
comes from a composite primary key `(device_type, device_key, state_key) → value` —
`device_type` is the model name (`"mera"`, `"alba"`), `device_key` is the adapter name
(`"hci1"`) or advertised MAC, falling back to `"default"` for single-instance use. Mera on
`hci1` and Sela on `hci2` cannot collide — they never share a primary key. Not one file per
device instance.

#### Status

Done

#### Implementation Details

Corrected from an earlier draft of this document, which assumed
one SQLite file per device instance (`mock_state/<model>-<adapter>.sqlite3`) — that's not
what `mock_persistence.py` actually implements (it was scaffolded before this section was
written), and the existing design already satisfies every real requirement here, so the
document was fixed to match the code rather than the reverse.

### REQ-022 — `namespace:index` key encoding

#### Type

Technical

#### Statement

Mera's multiple index spaces that each restart at 0 (`profile_setting`,
`common_setting`, `active_setting`, `spl`) are addressed by encoding `state_key` as
`f"{namespace}:{index}"`, e.g. `"common_setting:0"`. Alba's flat DpId space fits the same
shape (`f"dpid:{dp_id}"`). No schema change is needed for either — `state_key` was already a
free-form string.

#### Status

Done

### REQ-023 — Static metadata lives in code, not the DB

#### Type

Technical

#### Statement

`datatype`/`behavior`/`min`/`max` are static per-model Python tables, never
stored in the DB — they are not mutable state. The webui's full settings table (REQ-033)
joins this static metadata with `load_all()`'s current values at render time.

#### Status

Done
**Implementation Details — provenance (why these are code, not DB or a live fetch):**
Checked rather than assumed: does firmware or the Geberit cloud hand out setting min/max
ranges at runtime? Investigated against a full-session Charles capture from a real Mera
Comfort firmware update
(`local-assets/Bluetooth-Logs/nRF52840/jens62/firmware-update-mera-comfort/firmware-update.har`).
Findings: (1) firmware version checks on startup are confirmed real —
`prod.firmwarev1.services.geberit.com/api` → `/api/version` + `/api/firmwares` (an 809 KB
firmware catalog) hit repeatedly, exactly the "check for available updates" mechanism; (2)
setting min/max ranges are not in the cloud at all — the only other Geberit endpoint hit,
`mobileappsv1.services.geberit.com/api/Settings/*`, returns generic app feature-flags (device-
type gating for a remote-support feature), and every other `/Settings/*` call returned
`"Data": null`; (3) so where do min/max actually live? Not firmware, not cloud, as far as this
project has determined — `docs/developer/alba-dpid-reference.md` already documents its own
Min/Max columns as "value range from protocol spec," i.e. derived from analysis of the Home
App's own DataPoint definitions; the app validates client-side using ranges compiled into the
app binary. No accessible firmware-side bounds-check was found in the analyzed Mera
main-controller source (the switch dispatch doesn't resolve to labeled proc-ID cases, so
confirming firmware-side enforcement would need deeper binary-analysis effort) — plausible the
firmware also rejects out-of-range writes, but unconfirmed. Conclusion: empirically the app is
authoritative today, not the firmware or cloud, and that's already transcribed into
`ble-protocol.md`/`alba-dpid-reference.md` — the mock's metadata tables just reproduce the
same source the real app uses, nothing new to fetch.

### REQ-024 — Persist classification is code-side

#### Type

Technical

#### Statement

Whether a `(namespace, index)` pair is durable ("persist") or live-only is a
code-side decision, not a DB column. A protocol module only calls `save()` for pairs
classified as durable; live-state indices are simply never written, so there is nothing to
filter on read.

#### Status

Done

#### Implementation Details

The concrete Mera classification enumeration lives in
`docs/roadmap.md`, not duplicated here.

### REQ-025 — Full persist coverage

#### Type

Functional

#### Statement

Every `persist`-classified `(namespace, index)` pair actually round-trips
through the store — the full real-device settings surface, not a convenience subset. If a
setting exists on a real toilet and is writable, it survives a mock restart.

#### Status

In Progress

#### Implementation Details

True for every setting currently wired (Stored common/profile
settings for Mera, Nvm DpIds for Alba, firmware versions for both — REQ-001, REQ-031). Not yet
true for Mera's `SetCommand` (proc `0x09`) channel, where ~18 of ~20 command codes are still
unsimulated no-ops rather than persisted or correctly-classified-as-live — tracked
separately as REQ-050, since that's a simulation gap (nothing happens at all) rather than a
persistence-classification gap (something happens but isn't saved).

### REQ-026 — Write-through, not write-on-shutdown

#### Type

Functional

#### Statement

`save()` commits synchronously on every call. Every setting change arriving
over the protocol (`SetStoredProfileSetting`, `SetActiveCommonSetting`, a DpId write, etc.)
persists immediately, not batched or flushed at shutdown.

#### Status

Done

#### Implementation Details

This is what makes REQ-001's acceptance test hold even under an
abrupt SIGINT/SIGTERM.

### REQ-027 — Startup never overwrites existing state

#### Type

Functional

#### Statement

`load_all()` returns whatever is already on disk; a protocol module seeds
hardcoded defaults only for `(namespace, index)` pairs missing from that result. An existing
persisted value always wins over a hardcoded default.

#### Status

Done

### REQ-028 — Factory-reset is single-device-scoped

#### Type

Functional

#### Statement

`reset(device_type, device_key)` deletes exactly one device's rows, never all
devices' — "Reset to Factory Settings" in the webui (REQ-033) acts on exactly one device's
store.

#### Status

Done

### REQ-029 — Per-instance identity for Alba

#### Type

Functional

#### Statement

Each Alba mock instance has its own stable, unique, persisted serial number
and pairing PIN — not one hardcoded value shared by every instance.

#### Status

Done

#### Implementation Details

Gap found 2026-07-16: REQ-025's coverage requirement ("store all
data which is stored on a real device") had been interpreted as "settings a real device lets
you change," but a real device also has a unique serial number and pairing PIN, set once at
manufacturing and never changed. Every `_DEFAULT_STORE` row for these (DpId 12
`PAIRING_SECRET`, DpId 369 `SALES_PRODUCT_SERIAL_NUMBER`) was one hardcoded value shared by
every `AlbaMock` instance — two mocked Albas (e.g. REQ-003's `hci0`+`hci1` test) would show
identical S/N and PIN, unlike any real fleet of devices. Both DpIds are `behavior==4`
(Protected — factory-set, never written over BLE), so the Nvm write-through path never
touches them; `_Ble20AppLayer.__init__` now generates a value for each on first construction
for a given `device_key`, persists it immediately (same `dpid:{id}` key scheme as Nvm
settings), and reapplies the persisted value on every later session/restart. **Bug found and
fixed during verification:** the first pass skipped correctly *applying* an already-persisted
identity value back into `self._store` (the reload loop only applied persisted overrides to
`behavior==3` rows) — a restart silently reverted to the `_DEFAULT_STORE` default instead of
the previously-generated identity. Fixed by applying the persisted value directly for these
two DpIds, bypassing the Nvm-only reload loop. Verified on the mock VM: `hci0` and `hci1` get
distinct serial+PIN, and `hci0`'s identity survives a simulated restart unchanged. Webui: the
Alba control page shows a sticker-style block (model, S/N, PIN) reading live from the active
session's store. **Security finding along the way:** `_DEFAULT_STORE`'s `PAIRING_SECRET`
comment named a real physical device's actual pairing PIN as a formatting example —
redacted in both `tools/mock-geberit-alba.py` and `alba_mock.py` (a real device's BLE pairing
PIN is a real credential, not safe to reference in a public repo regardless of framing; this
violated an already-documented rule, `memory/kstr-probe-findings.md`, ~55 days earlier). The
value was already present in pushed history (confirmed via `git log -S`, present since commit
`7ac384c`, ~255 commits and 38 already-published GitHub Release tags downstream by the time
this was found). **Decided (2026-07-16): leave history as-is, do not rewrite** — rewriting
would force-push `main`, orphan 38 published HACS release tags, and diverge every existing
clone, for a leak whose real-world exploitability is narrow (a 4-digit BLE pairing PIN, usable
only by someone who both knows the specific device's MAC and is within physical BLE range) and
which rewriting wouldn't even fully undo, since anyone who already cloned/installed the repo
keeps the old value regardless. This is a closed decision, not a pending TODO — do not re-raise
it as an open task.

### REQ-030 — Per-instance identity for Mera *(superseded)*

#### Type

Functional

#### Statement

Each Mera mock instance has its own varied, per-adapter identity
(`_SAP_NUMBER`/`_SERIAL`), the same way REQ-029 does for Alba, instead of fixed instance
attributes set once in `__init__`.

#### Status

Superseded by REQ-012

#### Implementation Details

Never implemented (tracked historically as "Phase 2c"; the
concrete ask and REQ-003's multi-instance test case were Alba-only, so it was never ported to
Mera). Superseded 2026-07-19 by REQ-012's broader "no hardcoded values, configurable sourcing"
requirement, which subsumes this. Worth remembering if REQ-012 is picked up: this is one of
the concrete gaps it needs to close for Mera specifically.

### REQ-031 — Firmware version persistence

#### Type

Functional

#### Statement

Reported firmware/component versions durably change after a simulated update
and survive a mock restart, for both Mera and Alba — matching a real device's flash-backed
version storage, not read-only in-memory data.

#### Status

Done

#### Implementation Details

Gap found 2026-07-16: both mocks reported firmware/component
versions as read-only, in-memory-only data (Mera's `_FW_COMPONENT_VERSIONS` module-level
dict; Alba's firmware DpIds 8/9/10/785/786/787) — fine for a static version, but blocked
REQ-053's update simulation, which needs the reported version to durably change after the
simulated OTA. Confirmed directly against a real Mera Comfort capture
(`memory/mera-firmware-update-ble-protocol.md`): `GetFirmwareVersionList` returns RS28.0
TS199 before an update and RS30.0 TS206 after, for component `0x01`. Implementation, same
composite-key mechanism as the rest of this section, no schema change: **Mera** —
`state_key = f"fw:{component_id}"`, same `namespace:index` shape as
`common_setting:idx`/`profile_setting:idx`; `MeraMock.__init__` copies the module-level
fallback dict into `self._FW_COMPONENT_VERSIONS`, then overlays persisted values in the same
loop that handles common/profile settings; `_proc_0e` (`GetFirmwareVersionList`) reads the
instance dict, not the module dict; `_set_fw_version(component_id, v1, v2, build)` writes
through and is REQ-053's write hook. **Alba** — `state_key = f"dpid:{dp_id}"`, identical
scheme to the Nvm/identity DpIds; the six firmware DpIds are Info/Protected, so (like identity)
they get their own loop outside the Nvm-only reload path; unlike identity there's no random
generation — first run for a given `device_key` simply persists today's hardcoded
`_DEFAULT_STORE` default as-is, since firmware versions aren't unique per physical unit;
`_set_firmware_version(dp_id, value)` is the equivalent write hook. **Parity policy** (explicit
user instruction, 2026-07-16, applied by default to all mock feature work since): any mock
feature implemented for one protocol gets mirrored in the other at the same time unless
there's a concrete protocol-level reason it can't — tracking two mocks that drift
independently is more housekeeping than doing both up front; this firmware-persistence change
was the first instance of applying that policy.

---

## Webui

### REQ-032 — Independent per-device webui

#### Type

Technical

#### Statement

Each device keeps its own already-independent web server/port (aiohttp for
Mera, FastAPI for Alba, each bound to its own `web_port`) — no landing page, no
single-port/single-server merge.

#### Status

Done

#### Implementation Details

Decided 2026-07-16: a user running N mock devices already knows
each one's port from its own `--device` spec, and unifying aiohttp+FastAPI into one
process-wide server would add real complexity for no functional gain.

### REQ-033 — Full per-device settings table

#### Type

Functional

#### Statement

Each device's webui shows a full settings table (value, datatype, behavior,
min/max) with inline per-row edit, and a "Reset to factory defaults" action scoped to exactly
that device's store (REQ-028) — never all devices'.

#### Status

Done

### REQ-034 — Live SSE updates

#### Type

Functional

#### Statement

The webui reflects state changes live via Server-Sent Events, not
full-page-reload polling — mirroring the real bridge's own `new EventSource(apiBase +
'/events')` pattern.

#### Status

Done

#### Implementation Details

Gap found 2026-07-16: both mocks' update mechanism was a
full-page reload on a timer (`setTimeout(location.reload(), 3000)` for Mera;
`<meta http-equiv="refresh" content="2">` for Alba) — not even AJAX polling of a JSON
endpoint. Every reload wiped any in-progress interaction (a stepper mid-drag, a swatch's
success/error flash) regardless of whether anything had actually changed. Implemented: both
mocks now have a real `/events` SSE endpoint mirroring
`aquaclean_console_app/RestApiService.py`'s exact pattern — one `asyncio.Queue` per connected
client, a 30s heartbeat when idle, `{"type": "state", ...}` JSON payloads. Client side,
`mcConnectSSE(url, onState)` (`mock-controls.js`) mirrors `index.html`'s
`connectSSE()`/`onmessage`; each mock's own page decides what to update instead of reloading
everything. **Mera:** broadcasting is hooked into the existing `_log()` helper — nearly every
state-mutating path already calls it, so this covers them all without scattering broadcast
calls through the codebase. **Alba** was architecturally harder: `_Ble20AppLayer`/
`_AriendiServerSide` are freshly reconstructed per BLE session inside closures in `run()`, and
real BLE-side writes happen inside `_Ble20AppLayer._write()`, which had no path back to
`AlbaMock`'s broadcast queue. Per explicit decision (full proper wiring for both mocks, not a
shortcut for the harder one): `_Ble20AppLayer.__init__` now takes an optional `broadcast_fn`
callback (same pattern as REQ-039's `logger` threading), called from both
`_write_dpid_setting()` (webui writes) and `_write()` (real BLE `WriteCmd`, only when a
Nvm row actually persists) — so a real Geberit Home App or remote-control write pushes a live
update too, not just webui-initiated ones. **VM-verified:** Mera's `/events` sends an initial
snapshot on connect and a fresh push immediately after a settings write; Alba's broadcast
fires correctly from both the webui write path and a real-BLE-frame-shaped `_write()` call,
and correctly does *not* fire for a non-Nvm DpId write. Permanent regression tests in
`tests/test_mera_mock_webui.py`/`test_alba_mock_webui.py`. **Test-isolation bug found and
fixed along the way (own test code, not production):** `logging.FileHandler` opens its file
immediately and `mock_logging.py` (REQ-039) caches loggers globally by name — every test using
`adapter=None` collided on the same cached `"mock.mera.default"`/`"mock.alba.default"`
logger, and once one test's tmp dir was deleted, a later test reusing that cached logger could
hit `FileNotFoundError`. Fixed by giving each test construction a unique adapter/logger
identity derived from its own tmp dir's unique suffix.

### REQ-035 — Mera firmware-profile selector

#### Type

Functional

#### Statement

A "Firmware Profile" selector in the Mera webui lets a user flip the mock
between real captured snapshots (`rs30` current/default, `rs28` pre-update) with one action.

#### Status

Done

#### Implementation Details

`/settings/firmware-profile`, body `{"value": "rs28"|"rs30"}`.
Only components 1 and 11 actually differ between the two snapshots — confirmed twice over
(`memory/mera-firmware-update-ble-protocol.md` and `docs/developer/firmware-version.md`,
matching real capture bytes); every other component is identical in both. Applying a profile
calls the existing per-component `_set_fw_version()` write hook (REQ-031) once per component,
so persistence/logging/SSE-broadcast stay identical to how REQ-053's update simulation
applies the same values one at a time. The active profile is derived from component 1's live
value (no separate "current profile" flag to keep in sync) —
`_current_firmware_profile()` returns `"custom"` if it matches neither canonical snapshot
(e.g. after a partial simulated update). VM-verified 2026-07-16. As of 2026-07-19, the
`_FW_COMPONENT_VERSIONS_FACTORY` "Reset to Factory Settings" target (REQ-028) was itself
updated to the real `rs28` snapshot, replacing an earlier synthetic uniform-RS28.0-TS199
placeholder that predated understanding REQ-053's real blocker (see REQ-053's Implementation
Details) — confirmed via the mock's own test suite (14/14 passed) that a factory reset now
restores the real, non-uniform per-component values. Corrected a stale doc while implementing
the original selector: `docs/developer/firmware-version.md`'s 2026-06-26 finding ("component
1 alone at RS30.0 is not sufficient, all components must be RS30.0") was superseded by a real
on-device test the same day the selector was built (commit `e4295cc`) — the mock's real
per-component values were already the confirmed-working baseline, not something needing
further fixing at that time (and were later shown to be irrelevant to the actual firmware-
update-request blocker anyway — see REQ-053).

### REQ-036 — Alba firmware-profile selector

#### Type

Functional

#### Statement

The Alba webui offers the same firmware-profile selection capability as Mera's
(REQ-035), for Alba's firmware DpIds (8/9/10/785/786/787).

#### Status

Open

#### Implementation Details

*(none — not started.)* Blocked on a precondition, not just
unscheduled: no real pre/post-update firmware capture exists for Alba yet (unlike Mera's
nRF52840 capture), so there is no confirmed byte-accurate "older" snapshot to offer. Pick up
once such a capture exists.

### REQ-037 — Shared generic webui control module

#### Type

Technical

#### Statement

Webui controls (numeric stepper, toggle, enum/select, color swatch) are
rendered by one shared, metadata-driven module — a metadata list per setting
(`{id, name, kind, min, max, writeUrl}`) drives rendering, value updates, and writes
generically — not per-feature bespoke markup duplicated across mocks or hardcoded per-ID
branches.

#### Status

Done

#### Implementation Details

Current-state audit before deciding (2026-07-16): three fully
separate HTML/JS sources existed, zero sharing — the real bridge's
`aquaclean_console_app/static/index.html` (2141 lines, one monolithic file); Mera's inline
`_HTML` aiohttp template (~53 lines, identity table + button-press form + session log only);
Alba's inline FastAPI route-handler string (~35 lines, identity sticker + two DpId toggle
buttons only). Neither mock at that point implemented anything that visibly duplicated the
bridge's markup, so there was no duplication to fix *yet* — but REQ-033's planned full
settings table was exactly where both mocks would grow one, which would have been the third
and fourth independent implementation of primitives the bridge already solved generically
(`stepDec`/`stepInc`, `setCommonSetting`/`setProfileSetting`, `sendPost`, the
`.ps-section`/`.ps-row`/`.ps-stepper` CSS classes `index.html` already uses). Decided: write a
new, standalone, metadata-driven JS+CSS module inside `aquaclean_ble_relay/` — not extracted
from `index.html`, and `index.html` is not modified by this work (tracked separately in
`docs/roadmap.md` → "Refactor: aquaclean_console_app webui to use the shared mock settings-
control module," since it touches shipped `aquaclean_console_app` code and needs a proper
branch/PR per the mock-vs-console-app workflow split,
`memory/feedback_mock_services_work_on_main.md` — out of scope for this REQ). The module
mirrors the bridge's visual style but is architected generically from the start (unlike the
bridge's current per-ID-hardcoded functions), so that eventual `index.html` refactor becomes
"swap its inline per-ID code for calls into this module" rather than a rewrite from scratch.
**Implemented and VM-verified (commit `d217e46`, VM-verified `2d8a56a`+):**
`aquaclean_ble_relay/static/mock-controls.js`/`.css`. Mera got `/settings/common/{id}` and
`/settings/profile/{id}` write routes (backed by the same functions proc `0x52`/`0x54` use, so
BLE and webui writes stay consistent) plus a read-only Firmware Versions section; Alba got a
single `/settings/dpid/{id}` write route restricted to Nvm rows, with datatype-aware
encode/decode helpers. Could not be runtime-verified in the primary dev environment
(`fastapi`/`aiohttp`/`bluez_peripheral` aren't installed there) — verified on the mock VM
instead: both mocks import cleanly, the settings table renders, static assets serve, writes
round-trip and persist, and a write to an Alba Protected DpId (e.g. the pairing PIN) is
correctly rejected with HTTP 400. All 11 tests passed on the mock VM at the time
(`tests/test_mera_mock_webui.py`/`test_alba_mock_webui.py`, skipped automatically via
`pytest.importorskip` in any environment missing the deps). **Bug found and fixed along the
way (commit `2d8a56a`):** `alba_mock.py` added the repo root to `sys.path` right before its
`aquaclean_console_app` imports, but its own `aquaclean_ble_relay.*` imports sat above that
line — standalone-script invocation (where `sys.path[0]` is this file's own directory, not the
repo root) failed with `ModuleNotFoundError` before ever reaching the later insert.
`mera_mock.py` already did this in the correct order; fixed by moving the insert before both
import groups it serves.

### REQ-038 — Mera "User sitting" toggle

#### Type

Functional

#### Statement

The Mera webui has a "User sitting" simulation toggle, the same capability
Alba's webui already has — the Geberit Home App's Remote Control area only enables
shower/dryer buttons when the seat sensor reports a user sitting, so without this toggle that
section of the app stays greyed out during mock testing.

#### Status

Open

#### Implementation Details

*(none — not started; formalized 2026-07-19, superseding an
earlier, slightly-off sketch in `docs/roadmap.md` §"Mock Mera: add 'User sitting' toggle
button to web UI" that guessed at a route name/shape not quite matching Alba's real
mechanism.)* **Alba's actual mechanism** (confirmed by reading the real implementation): a
`_user_sitting: bool` variable, toggled two ways — a manual webui button, `POST
/notify/607/toggle` (DpId 607, `USER_DETECTION_STATUS`), which flips `_ui_notify_state["607"]`,
pushes a notify frame, and updates DpId 564 (shower-dispenser ready-state) in tandem
(`2`=ready when sitting, `1`=disabled when not; button label "ON (sitting)"/"OFF (absent) →
Toggle"); and an automatic flip at the end of every completed BLE session, independent of the
button, simulating a different user each session without manual intervention. **Mera
equivalent needed:** Mera has no DpId layer — the corresponding field is `StateUserPresent`
(SPL index 0, read via proc `0x0D`). Needs: a `self._user_sitting: bool` instance attribute,
persisted via `mock_persistence.py` (REQ-012's "no hardcoded/in-memory-only values" principle
applies here too — the state should survive a restart, not just live in-memory for one
process run); a webui button in the same visual pattern as the existing settings-table
buttons; a route path TBD at implementation time (follow this document's `/settings/...`
convention rather than copying Alba's `/notify/{id}/toggle` literally, since Mera's webui has
no DpId-notify concept); the SPL index-0 handler reading `self._user_sitting` instead of a
hardcoded value. Open design question for implementation time: does Mera also want the
automatic per-session flip, or is the manual toggle alone sufficient?

---

## Logging

### REQ-039 — Per-device logger identity

#### Type

Technical

#### Statement

Each device instance has its own logger, named by the same `(model, adapter)`
key used for persistence (e.g. `mock.mera.hci1`) — not one shared hardcoded logger name.

#### Status

Done

#### Implementation Details

New shared module `aquaclean_ble_relay/mock_logging.py` —
`get_device_logger(model, adapter)` returns/configures the `mock.<model>.<adapter>` logger
(idempotent), with three handlers: console, a per-device file, and one combined-file handler
shared by every device logger created in the process (REQ-041). `MeraMock` swapped its inline
logger/file-handler setup for a call to this helper. `AlbaMock` needed more — it had no
`logging.Logger` at all, just a module-level timestamped `print()` override used at ~120 call
sites across `_Ble20AppLayer`, `_AriendiServerSide`, the four GATT service classes, and
`AlbaMock` itself. Per explicit decision (full mechanical conversion over a smaller
`contextvars`-based shortcut, so every call site genuinely holds its own `Logger` rather than
a shortcut that only observably behaves like one), the override is gone entirely: each of
those classes now takes an optional `logger` constructor param threaded down from
`AlbaMock.logger`, and all ~120 `print(...)` sites became `self.logger.info(...)`. Six
multi-positional-arg `print("text:", var)` calls were collapsed to single f-string args first
— `logging.Logger.info(msg, *args)` treats extra positional args as `%`-style formatting
arguments, so passing them through unchanged would have raised `TypeError` at runtime. Two
call sites aren't methods (`safe_call()`, a module-level function, uses
`getattr(obj, "logger", ...)`; the `if __name__ == "__main__":` KeyboardInterrupt handler
uses `mock.logger`). `mock_service.py`'s previous `_Tee` stdout/stderr redirect (keyed by the
whole `--device` batch rather than per-device) was removed as redundant once every device
logger had its own combined-file handler. **Follow-up bug (found live on the mock VM, fixed
same day):** `MeraMock.run()` had a second, later reference to the `log_path` variable this
refactor removed — crashed every real run with `NameError` as soon as the startup banner tried
to print it. `get_device_logger()` now stashes the per-device path on the logger itself
(`logger.device_log_path`) so callers can report it without keeping their own copy.
**Verified on the mock VM:** device tag confirmed at the fixed post-timestamp position
(REQ-040), per-device files confirmed isolated, the combined file confirmed shared and
interleaved across two concurrently-constructed instances, logger-wiring confirmed reaching
all four previously-print()-only Alba classes. All 11 pre-existing webui tests still passed
unmodified. Permanent regression coverage in `tests/test_mock_logging.py` — stdlib-only, runs
in any environment including the primary dev venv, unlike the webui tests.

### REQ-040 — Fixed-position device tag

#### Type

Functional

#### Statement

The device tag sits at a fixed position in every log line, immediately after
the timestamp, so a script/CI consumer can reliably `grep`/`awk` on it regardless of message
content.

#### Status

Done

#### Implementation Details

The device tag is the logger name itself in the format string
(`[%(asctime)s] [%(name)s] %(message)s`) — no per-record `extra=` plumbing needed.

### REQ-041 — Combined and per-device log files

#### Type

Functional

#### Statement

Both a combined, chronological, cross-device-correlation log and a per-device
log file exist simultaneously, via multiple handlers on the same per-device logger — not a
choice between the two.

#### Status

Done

### REQ-042 — Log filename convention

#### Type

Technical

#### Statement

Log filenames follow the same `<model>-<adapter>` naming convention as the
persistence DB's `device_type`/`device_key`.

#### Status

Done

### REQ-043 — `--btmon-capture`

#### Type

Functional

#### Statement

`mock_service.py --btmon-capture` starts `sudo btmon -w
<state-dir>/logs/mock-btmon_<timestamp>.btsnoop` before any device starts and stops it
cleanly on exit, automating a capture workflow that was previously a separate manual
terminal command.

#### Status

Done

#### Implementation Details

Filenames follow the same `state_dir/logs/mock-*_<timestamp>`
convention as the per-device/combined logs. Cleanup backs `Popen.terminate()` with a `sudo
pkill -f` fallback keyed on a distinguishing argument, since `sudo` doesn't always forward
`SIGTERM` to its child reliably. Never exhibited either problem `--bluetoothd-debug`
(REQ-044) has.

### REQ-044 — `--bluetoothd-debug`

#### Type

Functional

#### Statement

`mock_service.py --bluetoothd-debug` starts `sudo bluetoothd -n -d
--noplugin=battery` (redirected to a log file) before any device starts and stops it cleanly
on exit, producing a usable bluetoothd debug session — the same automation REQ-043 provides
for btmon.

#### Status

In Progress

#### Implementation Details

Implemented, but currently unusable in either configuration
(found 2026-07-18) — root cause fully diagnosed, not yet fixed. Deliberately does **not**
stop/restart the systemd `bluetooth` service (mirrors the manual two-terminal workflow it
replaces exactly): if systemd's `bluetoothd` already holds the `org.bluez` D-Bus name — the
normal case, since `bluetooth.service` runs by default on any test machine — the flag simply
fails to bind, same as running the command by hand. Working around that by running `sudo
systemctl stop bluetooth` first (the obvious fix) instead triggers a D-Bus-activation race:
`org.bluez` is D-Bus-activated, so systemd repeatedly tries to auto-restart its own
`bluetoothd` to reclaim the name, racing against and failing to bind after the
`--bluetoothd-debug` instance claims it first. Confirmed live: 5 rapid
`bluetoothd[PID]: = src/main.c:main() Unable to get on D-Bus` failures in ~1.7s, which killed
that entire test session before it reached the onboarding attempt at all, and left the
systemd `bluetooth` unit in `failed` state (needs `systemctl reset-failed bluetooth` before
`systemctl start bluetooth` works again). So there is currently no invocation that reliably
produces a working debug session — every attempt either fails to bind immediately or takes
down the whole test run via the restart race. **Avoid `--bluetoothd-debug` for routine tests;
use `--btmon-capture` alone instead.** Not yet fixed — would need either detecting/handling
the D-Bus race (`systemctl stop bluetooth`, poll until `org.bluez` is actually released, *then*
start the debug instance, restoring `bluetooth.service` on exit) or dropping the flag in
favor of documenting the manual two-terminal workflow it was meant to replace. Full incident:
`memory/mera-advertisement-two-entry-regression-confirmed.md` (local Claude memory).

---

## Shared Modules (DRY)

### REQ-045 — Shared adapter selection *(see REQ-004)*

#### Type

Technical

#### Statement

Adapter-selection logic exists once (`mock_bluez_adapter.py`) and is reused by
every model — never reimplemented per model.

#### Status

Done

#### Implementation Details

See REQ-004; listed again here as this document's general DRY
policy statement.

### REQ-046 — Shared persistence schema

#### Type

Technical

#### Statement

Persistence is one schema/module (`mock_persistence.py`), reused by every
model — not a per-model reimplementation.

#### Status

Done

#### Implementation Details

Scaffolded, together with `mock_bluez_adapter.py` (REQ-004), as
the first implementation step before any per-model refactor — commits `152382c`, `dadde00`,
`0a85636`.

### REQ-047 — Shared orchestration

#### Type

Technical

#### Statement

CLI parsing, task orchestration, and shutdown handling live once, in
`mock_service.py` — not duplicated per model.

#### Status

Done

### REQ-048 — Shared validation/classification pattern

#### Type

Technical

#### Statement

Firmware-string validation and namespace/persist classification follow one
shared *pattern* (validate at parse time, classify persist-vs-live once per namespace) across
models, even where each protocol module's concrete table/regex differs — each protocol module
owns its own concrete implementation of the shared pattern, not a single shared parser that
assumes identical internal representations.

#### Status

Done

#### Implementation Details

See REQ-011 for why a single shared parser doesn't fit here
(Mera's byte-tuples vs. Alba's DpId encoding aren't the same shape).

---

## Outstanding Gaps

### REQ-049 — Defined webui bind-failure behavior

#### Type

Functional

#### Statement

When one device's webui fails to bind (or any single device fails to start),
the rest of the service has a defined, deliberate response — either the whole
`mock_service.py` run aborts, or that one device degrades to headless (BLE still served, no
webui) while the others continue.

#### Status

Open

#### Implementation Details

*(none — decision not yet made.)* Originally raised as two
separate bullet points in this document ("Webui bind failure" and "Resource conflicts across
devices") — merged here, since on inspection they were the same open question stated twice.

### REQ-050 — Full `SetCommand` simulation

#### Type

Functional

#### Statement

The Mera mock simulates the effect of every `SetCommand` (proc `0x09`) code
(`ToggleLidPosition`, `PrepareDescaling`/`ConfirmDescaling`/`CancelDescaling`,
`TriggerFlushManually`, `ResetFilterCounter`, `ToggleOrientationLight`, `Stop`, etc. — see
`.claude/rules/ble-protocol.md` Layer 1 table) — not just the two currently wired.

#### Status

Open

#### Implementation Details

Found 2026-07-16. This is a gap in *action simulation*, distinct
from persistence (REQ-025), which is correctly wired for the settings that actually need it.
Precisely: `_write_stored_common_setting`/`_write_stored_profile_setting` (proc `0x52`/`0x54`)
persist correctly (REQ-001) — confirmed working, reused by both the real BLE path and the
webui write routes; if the real app changes e.g. orientation-light colour against a mocked
device, that persists across a restart. `_proc_08`/`_proc_0b`
(`SetActiveProfileSetting`/`SetActiveCommonSetting`) are session-only, never persisted, by
design — REQ-055. `_proc_09` (`SetCommand` itself) has only **2** of ~20 codes wired at all
(`ToggleAnalShower`/`ToggleLadyShower`, correctly not persisted — they flip live SPL state,
REQ-006's Mera Implementation Details). Every other code is a silent no-op: the real app or a
remote control can send them to the mock and nothing happens at all — not even a log line
distinguishing "received but ignored" from "not received." All of these are otherwise
"quick-win" commands per `.claude/rules/ble-protocol.md` — each just needs
`SetCommandAsync(Commands.X)`-shaped wiring, zero new protocol code, but it hasn't been done.

### REQ-051 — Complete webui state visibility

#### Type

Functional

#### Statement

The webui shows every piece of state the mock tracks, not a curated subset —
for both Mera and Alba.

#### Status

Open

#### Implementation Details

Found 2026-07-17. **Alba:** `_Ble20AppLayer._DEFAULT_STORE` holds
all 79 DpIds in `self._store`, but `_settings_table_data()` only renders
`self._SETTINGS_DPIDS` (14 keys — Nvm settings + identity + firmware). The other ~65 DpIds
(shower/descaling status, statistics, error flags, commands, etc.) are fully live in memory
but never surfaced in the webui at all. Fix direction: add a third, read-only "Device State"
section listing everything in `self._store` not already in `_SETTINGS_DPIDS` — needs a full
name table extracted from `_DEFAULT_STORE`'s inline comments (only the 14 settings DpIds have
structured names today, via `_DPID_NAMES`; the rest only have a trailing `# COMMENT`, not a
machine-readable label). Open question for implementation time: flat list sorted by DpId, or
grouped by category (shower/descaling/statistics/errors) mirroring `_DEFAULT_STORE`'s own
comment groupings. **Mera:** analogous gap — `self._SPL_MERA_VALUES`,
`self._PER_NODE_PROFILE_SETTINGS`, `self._ACTIVE_COMMON_SETTINGS`, `self._registration_level`,
and other live state are never shown in the webui's three existing sections (Profile
Settings, Common Settings, Firmware Versions). Same fix shape: a read-only "Device State"
section covering everything not already shown.

### REQ-052 — Remote Control interoperability

#### Type

Functional

#### Statement

A real Geberit hardware Remote Control can discover, pair with, and connect to
both the Mera mock and the Alba mock, so remote-control behavior (button presses,
displacement/handoff with a concurrently-connected phone app, etc.) can be exercised against
the mocks instead of requiring real hardware every time.

#### Status

In Progress

#### Implementation Details

Added 2026-07-17, revised 2026-07-19 after re-checking the actual current mock source
against the claims in this REQ and in `docs/roadmap.md`.

**Mera — infrastructure present, `pairable off` still in place, but "architectural blocker" is
now downgraded to an identified, likely-already-fixed non-issue — corrected 2026-07-19,
several revisions of this REQ the same day (see below for the concrete trigger).**
`mera_mock.py` has Device Information Service (`0x180A`, real Mera version string) and
`_RCPairingService` (GATT service UUID `0xC526`, so the RC's `FIND_BY_TYPE_VALUE` pre-pairing
check succeeds) since commit `2b565b0`, plus a GATT re-registration race fix
(`_force_remove_and_reregister`). The mock unconditionally sets the adapter to `pairable off`
at startup (`mera_mock.py` line ~2119) and never turns it back on — the button-press handler
has an explicit "Do NOT set pairable=on here" comment (line ~2065), because turning it on
reportedly also makes the mock answer iOS's own system-Bluetooth pairing dialog during normal
Home App onboarding, which broke onboarding and was reverted twice (v1.31.0, then again
2026-07-16 after commit `2b565b0` reintroduced it).

**What's actually confirmed vs. assumed, per real capture evidence:**
- The real Home App's full onboarding session (`onboarding-real-mera.pcapng`, 663 ATT frames)
  contains **zero `LL_ENC_REQ`** frames — the Home App never attempts BLE-level SMP pairing at
  all on real hardware. This matches the user's observation that the Home App never shows as
  "paired" in iOS's Bluetooth settings when connected to the mock either — there's nothing to
  bond in the first place.
- The RC captures, by contrast, show a complete `LL_ENC_REQ`/`LL_ENC_RSP`/`LL_START_ENC_REQ`/
  `LL_START_ENC_RSP` sequence every time it connects — real hardware clearly does respond to
  SMP requests when one arrives.
- Real Mera has a single physical button and a single procedure for both RC pairing and Home
  App onboarding (per the user, 2026-07-19) — it has no way to know in advance which kind of
  client is about to connect, so it cannot be toggling a "pairable" adapter flag depending on
  who's asking. The simplest model consistent with all of the above: real Mera's BLE stack is
  simply always willing to respond to an SMP request if one arrives (functionally always
  "pairable"), and the Home App just never sends one. Whatever the button actually gates is
  connection *acceptance* (the `IsButtonPressed`/`IsEmergencyConnectPermitted` advertising
  mechanism, `.claude/rules/ble-protocol.md` § "SensorState") — a separate concern from BLE
  pairability.
- **This means the mock's `pairable=on` → iOS-dialog symptom was never verified to be a
  real-hardware-matching constraint** — and it turns out not to be one. See below: the trigger
  is identified, and it's purely a mock-host artifact.

**Trigger identified, 2026-07-19: it's BlueZ's built-in Battery plugin, already independently
fixed one day after the last revert.** Both `b374e24` (v1.31.0, the original 2026-06-22 fix)
and `ee3171b` (v1.77.0b1, the 2026-07-16 re-revert) diagnosed the exact same mechanism: BlueZ's
Battery plugin acts as its own GATT *client*, reading Battery Level from the connected iOS
device immediately on connect. iOS refuses the unauthenticated read; BlueZ escalates by
spontaneously sending an SMP Security Request to try to establish encryption, which iOS
surfaces as its system pairing dialog ("Kopplungsanforderung ... „ro" möchte sich mit deinem
iPad koppeln" — confirmed live, `docs/developer/mock-geberit-mera.md` § "Battery plugin
interaction"). This is a Linux-desktop-BlueZ feature (show a battery icon for connected
accessories) with **no equivalent on real Mera hardware** — an embedded device has no reason
to read a connecting phone's battery level as a courtesy UI feature. It has nothing to do with
the Geberit protocol.

**The very next day (2026-07-17), this exact mechanism was independently diagnosed and fixed**
at the systemd level on `anneubuntu-studio`: a `bluetooth.service.d` drop-in override forcing
`bluetoothd --noplugin=battery` (`memory/mera-mock-battery-plugin-fix.md`), verified across two
fresh test sessions with zero recurrences of the SMP pairing-failure cycle. **Nobody has gone
back to check whether `pairable=on` is now safe again in the mock's own code with that
override in place** — `ee3171b`'s revert was correct for the environment it was tested in, but
that environment (battery plugin still active) no longer matches current `anneubuntu-studio`.

**Practical consequence — re-test before designing anything:**
1. Confirm the systemd override is still active on the test host:
   `systemctl show bluetooth.service -p ExecStart` should show `--noplugin=battery`.
2. Re-enable `pairable on` in the mock (revert the effect of `ee3171b`) and re-test a normal
   Home App connection — if no iOS pairing dialog appears, the constraint this REQ's "scoped
   RC-only pairing mode" design was built around doesn't exist anymore, and the correct fix is
   simply **leave `pairable on` permanently**, matching real hardware's apparent
   always-willing-but-never-asked behavior — not a time-boxed pairing-mode workaround.
3. **Durability gap**: the systemd override is a manual, host-specific config not scripted or
   automated anywhere in this repo. If RC testing ever happens on a different host, or
   `anneubuntu-studio`'s systemd config is reset, the original bug reappears silently with no
   repo-level warning. Worth its own tracked item regardless of how step 2 turns out — either
   document it as a mandatory one-time mock setup step, or script it.

**Step 1/2 done, 2026-07-19 — clean result, but re-test was worth being skeptical of.**
`pairable on` restored in both `mera_mock.py` (v1.101.0b1) and `mock-geberit-mera.py`
(v1.78.0b1), tested against `anneubuntu-studio` with the systemd override confirmed active
(`--noplugin=battery`, running since 2026-07-18) — Home App onboarding via mock+bridge v3.1.2
worked with no pairing dialog. **But a clean onboarding alone doesn't prove `pairable=on`
actually took effect** — Home App onboarding never attempts BLE pairing regardless of adapter
state (same reasoning as above), and the code's `subprocess.run()` call logged success
unconditionally without checking the result. Directly queried instead: `bluetoothctl show` on
the VM confirmed `Pairable: yes` for that run. Added `_set_pairable_on_verified()` (v1.102.0b1
/ v1.79.0b1) to check the command's return code and read back `btmgmt info` going forward — and
caught a real bug while writing it: `btmgmt info`'s "current settings" line reports this
setting as `bondable`, never as `pairable` (`pairable` is only a command-name alias, confirmed
via `btmgmt --help`); the first version of the verification check looked for the wrong string
and would have always logged a false "not verified" warning. Fixed before commit. **Still
open**: RC pairing itself hasn't been tested yet — only the Home App side. That's the actual
test of whether the fix solved the real problem.

**RC pairing tested 2026-07-19, v1.102.0b1, no success.** `pairable=on` was confirmed genuinely
active for this test (`"Adapter confirmed pairable=on (verified via btmgmt info)"` in the
mock's own log). The RC still never appeared in either capture
(`local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/new_mock_1.102.0b1_and_later/`)
— confirmed via `tools/find-geberit-remote.py` against both: the only `CONNECT_IND` targeting
`38:AB:41:2A:0D:67` came from `63:79:44:38:6c:97`, a random/locally-administered address, not
the RC's public `B0:10:A0:68:5C:8B`. Also confirmed, via `tools/nrf-ble-analyze.py --mac
A0:AD:9F:72:C4:0F --adv`: the mock advertises under its own Bluetooth adapter's real hardware
address (`A0:AD:9F:72:C4:0F`, the ASUS USB-BT500 dongle) — never as `38:AB:41:2A:0D:67`, the
real toilet's identity. **Initially read as "the root cause" — corrected below, this framing
was too confident.**

**Corrected within the hour — "the mock needs to spoof the toilet's MAC" was an overreach.**
The user pushed back with the obvious counter-example: a Remote Control bought new, out of the
box, has no way to be pre-programmed with a specific toilet's BLE address — so it must have
some signature-based discovery mechanism for first-time pairing (service UUID / manufacturer
data, not a stored MAC). Every capture we have is of jens's own remote, which is *already
bonded* to his real toilet — so it always directly targeting `38:AB:41:2A:0D:67` only proves
it's doing normal "reconnect to my known device" behavior. It says nothing about whether the
remote *could* discover a different device if put into a fresh-pairing/discovery mode. If a
factory-fresh (or explicitly re-paired) remote does signature-based scanning, the mock's
current advertisement — which already includes the matching service UUID and "Geberit AC PRO"
SCAN_RSP name, just under `A0:AD:9F:72:C4:0F` instead of `38:AB:41:2A:0D:67` — might already be
enough to be discovered, with **no MAC-spoofing needed at all**.

**Answered, 2026-07-19 — official procedure found.** Sourced from the Geberit PDF
`966.731.00.0_05.pdf` ("Fernbedienung neu zuweisen" / "Reassign remote control"): press `<+>`
on the Remote Control **and** `<up>` on the toilet's side control panel **simultaneously, for
~30 seconds**, until `[Pairing ok]` appears on the toilet's display. Full text and analysis:
`memory/geberit-remote-control-pairing-procedure.md`.

This retroactively explains the earlier button-press/RC-connect correlation
(`docs/developer/ble-advertising-button-press-confirmation.md` § "Source 5") as most likely
coincidence, not causation — those captures showed the flip and the RC's `CONNECT_IND` only a
few seconds apart, never anywhere near a 30-second hold. They were almost certainly normal
reconnections of jens's already-bonded remote, with an unrelated brief button press nearby.

**Corrected again within the hour — "the mock needs a new sustained-hold feature" was also an
overreach.** Checked `_send_info_frame_burst` in `mera_mock.py`: the mock's existing button
only auto-releases (`self._button_pressed = False`) when an actual BLE connection completes
the Home-App-specific A6 info burst — there's no timer. If nothing connects in the meantime,
`_button_pressed` (and the advertised `IsButtonPressed` bit) stays `True` indefinitely. So the
existing single button can already sustain a 30+ second "held" state for free — click it once,
and it stays pressed until something else connects. **Confirmed directly by the user**: today's
test never actually reached `[Pairing ok]`, meaning the correct 30-second combined hold
(mock button clicked + `<+>` held on the physical remote, continuously, for the full duration)
was simply never attempted yet — "no success" so far tells us nothing about mock capability.

**Confirmed by the user — it's the same button.** The user had already stated this earlier the
same day, in the exact prompt that kicked off the pairable=on re-investigation: "There is only
[one] button [that] needs to [be] kept pressed in order to 'pair' with Remote Control or
pairing with Geberit Home App. The procedure is the same for both." This was missed and
re-asked as if still open — it isn't. The toilet has one physical button, used identically for
both flows; the mock's existing single web-UI button already represents it correctly. No new
mock feature is needed for the button itself.

**Next step, simple**: click the mock's existing button once, then hold `<+>` on the physical
remote continuously for the full ~30+ seconds (longer than tried before), and see whether
`[Pairing ok]` appears — try this before building anything new. Only revisit MAC-spoofing or
the bond-mismatch hypothesis if a properly-timed attempt still fails.

**The "pre-existing bond" explanation is an unconfirmed hypothesis, not a demonstrated root
cause** — corrected 2026-07-19 after the user pushed back on it being stated as settled.
`docs/roadmap.md`'s 2026-06-25 test log shows only that the RC never appeared in the mock's
log at all; no capture has ever shown the RC actually sending `LL_ENC_REQ` against the mock
and failing. Given `pairable` is unconditionally off today, a fresh test would still fail for
that reason regardless of whether the RC's stored LTK matches — the bond-mismatch theory has
never actually been tested against the current mock and should not be treated as established
until it is (clearing the RC's bond, per `docs/roadmap.md`, is worth trying but only once
pairing actually completes end to end — see the re-test steps further below).

**Mock advertisement fidelity — retracted 2026-07-19, was wrong.** This REQ previously stated
(same day, since corrected) that advertisement fidelity was unrelated because the RC connects
independent of the toilet's `IsButtonPressed`/`IsEmergencyConnectPermitted` state. Investigating
all 8 captures in `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/`
overturned that: in the only two captures where the RC's own `CONNECT_IND` was ever observed
(`toogle-lid-with-remote-without-running-bridge.pcapng`, `pairing with RC and toggle lid.pcapng`),
a fresh RC connection is preceded within a few seconds (0.5s and ~3.1s respectively) by exactly
this advertising flip — no counter-example exists in the data set. See
`docs/developer/ble-advertising-button-press-confirmation.md` § "Source 5" for the full
evidence and appropriate hedging (2 instances is a correlation, not a proven mechanism).
**Practical consequence for testing:** trigger the mock's "button pressed" web-UI action
shortly before attempting an RC connection — that matches the only two conditions under which
a real RC connection has ever actually been captured.

**Bridge identified in three failed RC-attempt captures — but it's not blocking a connection
slot.** Three more captures in that same directory (`pair1.pcapng`, `pair2.pcapng`,
`toogle-lid-with-remote.pcapng`) show only one connected central each — `94:A9:90:68:B0:E2`,
one digit off the bridge's ESP32 proxy address `94:A9:90:68:B0:E0` (`docs/connection-test.md`
Step 2) — and the RC never appears in any of the three. Whether the bridge was deliberately
left running for these three or simply not stopped is unconfirmed either way — don't read this
as a controlled "with bridge" experiment. Either way, this is **not** displacement:
`memory/mera-comfort-displacement-baseline.md` already confirms real Mera Comfort supports
simultaneous multi-client connections (app + remote polling in parallel). The better-supported
explanation is the button-press finding above — none of these three captures show the
advertising flip at all, so the RC most likely never had a trigger to attempt a connection in
the first place, independent of whether the bridge was connected. Don't cite this as evidence
that the bridge needs to be stopped for RC testing; the actionable takeaway is still the
button-press trigger above.

**Alba:** no RC-related GATT service exists at all — unknown whether Alba's real RC pairing
uses the same `0xC526` UUID as Mera or a different one; needs a real-hardware BLE sniff of an
Alba+RC pairing, analogous to the existing Mera capture. Relevant background investigations
already in progress (not solutions to this REQ): `memory/mera-comfort-displacement-baseline.md`
(remote recovers ~9s after app disconnect on real Mera hardware) and
`memory/alba-remote-control-conflict.md` / `memory/alba-session-caching-fix.md` (Alba
remote-displacement root cause still open).

Scope, updated 2026-07-19 (three times): (1) **Mera** — re-test with `pairable on` restored
and the `--noplugin=battery` systemd override confirmed active (see steps above) before
building anything. If the dialog is genuinely gone, drop the "scoped RC-only pairing mode"
design entirely — it was solving a problem that may no longer exist — and just leave
`pairable on` permanently. Only build the scoped-pairing-mode workaround if re-testing shows
the dialog still appears even with the override active (i.e. a second, still-undiagnosed
trigger exists). Once pairing works at all, the bond-mismatch hypothesis above can finally be
tested; when testing, trigger the button-pressed state first, per the correlation finding
above. Separately, track the durability gap (systemd override not scripted anywhere) regardless
of how re-testing goes. (2) **Alba** —
discovery surface identified and stubbed the same way Mera's is, plus the same pairing-mode
work Mera needs. (3) For both — enough of the post-pairing encrypted protocol
decoded/implemented to respond meaningfully rather than just complete pairing and go silent.

### REQ-053 — Firmware-update procedure simulation

#### Type

Functional

#### Statement

The mock simulates the real device's `ctx=0x40` firmware-update BLE procedure
sequence (`StartFirmwareUpdate`/poll/keepalive/finalize) closely enough that the real Geberit
Home App can drive a simulated firmware update against it end-to-end.

#### Status

In Progress

#### Implementation Details

Implemented for Mera only (`mera_mock.py`, from v1.83.0b1) — no
real Alba firmware-update capture exists yet, so this is not a parity violation, just nothing
to base an Alba equivalent on. `_dispatch()` has an early `ctx`-first branch: `ctx=0x40` (plus
its `ctx=0x00/proc=0x01` companion frame) routes to `_proc_fw_update()` before the existing
proc-keyed chain, because `proc=0x52`/`0x53` collide numerically with
`SetStoredCommonSetting`/`GetStoredProfileSetting` under the default ctx (see
`.claude/rules/ble-protocol.md` §"Firmware update procedures (ctx=0x40)" for the full decoded
sequence). State machine `self._fw_update_state`: `idle → started → done → rebooting → idle`.
`0x40/0x52` starts it; `0x40/0x53` polls it (`05`=busy/`06`=done); `0x40/0x04` while `done`
triggers finalize — applies the `"rs30"` firmware profile via REQ-031's write hook,
force-disconnects the current BLE link, then resets to `idle` after a delay so the app's
natural reconnect sees updated firmware on the next `GetFirmwareVersionList`. `0x40/0x00`
returns a fixed synthetic 12-byte keepalive payload (real captures show this value
fluctuating per call without gating app progression, so an exact match wasn't pursued).
Follow-up pass added: progress-notify frames on A5 (`_build_progress_frame()` +
`_fw_update_run()`, 10 spontaneous notifications during the busy window, byte layout verified
against the real capture — CRC16 matches exactly for progress=0 and progress=12, only the
real device's trailing buffer-reuse garbage bytes beyond the declared body length differ, and
this always zero-pads there instead); byte-count observability logging for the bulk
firmware-binary transfer instead of a ~14,500-line meaningless hex dump; bulk-transfer
crash-safety re-verified via code inspection (any non-CONTROL/non-SINGLE frame type falls
through as a no-op, worst case a spurious "unknown proc" response the app isn't waiting on —
no unguarded exception path). Deliberately not done: timers remain shortened, not byte-exact
(`_FW_UPDATE_BUSY_SECONDS=20` vs. the real ~164s flash window; `_FW_UPDATE_REBOOT_SECONDS=8`
vs. the real ~13.3s reboot silence) — a practicality choice for repeated test iterations; no
checksum/content validation of the transferred image, since completion is signalled by the
`0x40/0x53` poll, not the transfer itself. **Caveat, still open as of the last live test:**
the busy-window behavior above was implemented and byte-verified in isolation but the first
live test against a freshly-installed Geberit Home App never got past the `0x40/0x00`/
`0x00/0x01` background-poll loop — the app never sent `0x40/0x52` at all, even after tapping
"Update Now" and waiting ~9 minutes through repeated BLE reconnects. The mock's `0x40/0x00`
response is well-formed and cleanly ACKed every time, so this wasn't a framing bug at that
point — leading theories at the time were a readiness signal encoded in the real device's
(varying, vs. the mock's static) `0x40/0x00` payload, or an unidentified `READ_BLOB_REQ` on
handle `0x0020` spotted in the real capture during that window. **Root cause found and fixed
2026-07-18/19, unrelated to either theory:** the actual blocker was that the mock never
reassembled multi-frame incoming GATT WRITE requests — `_handle_request` dispatched on the
FIRST frame of a multi-frame request alone and silently dropped any CONS continuation. The
app's `GetFirmwareVersionList` 12-component onboarding query needs 13 args bytes (more than
the 9 that fit in one 20-byte frame), so components 10/11/12/14 were silently missing from
every response regardless of what firmware values were configured — this, not firmware
content, is what looked like an unconditional "update required" blocker across every profile
ever tried. Fixed by reusing the bridge's own `aquaclean_core.Frames.FrameCollector` for
request-side reassembly (mirrors the bridge's own use of it for response-side reassembly, the
opposite direction of the same wire format). Fixing that alone made onboarding *worse* (0/4
connections) until a second, related gap was found: the real device sends a FlowControl
CONTROL-frame ack after *every* received frame (confirmed byte-for-byte from
`onboarding-real-mera.md`), not just every 4th frame or at completion (`FrameCollector`'s own
built-in ack batching, correct for the *response* side, doesn't match this) — the app will not
send a CONS continuation without that per-frame ack. Fixed by sending the ack directly,
per-frame, before feeding the frame to the collector. **End-to-end confirmed working
2026-07-19** (mock v1.99.1b1): full onboarding succeeds with a genuinely non-uniform, real
firmware profile (`rs28`) and the real device's serial number — no "update required" blocker,
no "Fehler," correct version shown in Maintenance→Firmware. This also retroactively disproves
an earlier (now-corrected) theory recorded against REQ-035/REQ-028 that all components needed
uniform values — that empirical "finding" was itself caused by this same request-truncation
bug. Full incident write-up: `memory/mera-firmware-update-request-truncation.md` (local Claude
memory). **Still not run:** an actual simulated firmware *upgrade* through this now-working
onboarding path — the next planned test. **Also still open:** root-causing anything about
the original background-poll-loop theories was superseded by finding the real cause above, so
those specific theories were never individually confirmed or ruled out — moot now that the
actual blocker is fixed and confirmed, but noted so a future reader doesn't go looking for a
resolution to them specifically.

### REQ-054 — Post-update behavioral completeness

#### Type

Functional

#### Statement

The mock's behavior after a simulated firmware update reflects any
BLE-observable change the corresponding real update introduced — not just the reported
version string.

#### Status

Open

#### Implementation Details

*(none — not started; this is a completeness requirement for
whenever a gap is found, not a currently-known gap.)* Added 2026-07-18. REQ-053's simulation
only changes reported component version strings at the end of a simulated update; it does not
represent any other behavioral change a real firmware update may have introduced. When a real
device update is known to change anything BLE-observable for an affected component (a proc's
response format, an SPL parameter's semantics, a newly supported command, a fixed protocol
bug, etc.), the mock's code for that component should be updated to match. No such behavioral
difference between RS28.0 and RS30.0 (or RS07.0/RS08.0 for the motion-detection component) has
been found or is currently suspected from any live capture. See
`local-assets/firmware/mock-firmware-update-completeness-scope.md` for the analysis approach
(kept out of this tracked file per CLAUDE.md's rule on sensitive methodology terms) — in
short, both the pre- and post-update firmware images are already extracted locally with
matching per-node files, so this would be a diff between two already-available sources, not
new extraction work; only a delta that changes what the app can observe over BLE would matter
here.

### REQ-055 — Active settings never persist

#### Type

Functional

#### Statement

Active settings (`0x0A`/`0x0B`, `GetActiveCommonSetting`/
`SetActiveCommonSetting`) are session-scoped and re-derived from Stored NVM on every restart —
they are never persisted themselves, matching how a real device always re-derives Active
state from Stored NVM after a power-cycle.

#### Status

Done

#### Implementation Details

See REQ-006's Mera Implementation Details for how this is
implemented (a session-scoped in-memory store seeded from `_STORED_COMMON_SETTINGS` at mock
startup). Verified: writing `SetActiveCommonSetting` (id=0 → 6) is immediately visible via
`GetActiveCommonSetting` within the same instance, but after a simulated restart it returns
the value re-seeded from the (persisted) Stored setting, not the prior session's transient
override.

### REQ-056 — Connection-lifecycle visibility in webui

#### Type

Functional

#### Statement

Every BLE central's connection setup and connection close against a mocked
device — the Geberit Home App, a Remote Control, or any other client — is visible live in
that device's webui, for both Mera and Alba, the same way a settings change already is
(REQ-034). This holds regardless of whether the connecting central goes on to do anything
else — the connect/disconnect event itself is what must be visible, not only its downstream
effects.

#### Status

In Progress

#### Implementation Details

Requested 2026-07-19, as part of the broader "every action on a
central is visible in the webui" ask that also produced REQ-057/REQ-058. **Mera: already
satisfied.** `_on_device_connected`/`_on_device_disconnected` (`mera_mock.py`, ~line
2378–2399) each call `self._log("·", ...)` on every connect/disconnect; `_log()`
unconditionally does two things (REQ-034/REQ-039/REQ-040's Implementation Details) — appends
to the rendered session log shown in the webui (`_render_log()`) and calls
`_broadcast_state_nowait()`, pushing a live SSE update to every connected webui client. So a
connect/disconnect against the Mera mock is already both logged and live-pushed today; no
further work needed on the Mera side. **Alba: not yet satisfied, two independent gaps.** (1)
The equivalent connect/disconnect handlers (`AlbaMock.run()`, ~line 1551/1560, `"[Mock] BLE
client connected: ..."`/`"[Mock] BLE client disconnected: ..."`) call only
`self.logger.info(...)` — written to the log file/console (REQ-039) but never to
`self._broadcast_fn()`, so no live SSE push happens for a connect/disconnect. (2) Alba's webui
has no session-log panel at all, unlike Mera's `_render_log()` (confirmed: no
`log_html`/`_render_log`/session-log markup anywhere in `alba_mock.py`) — so even calling
`self._broadcast_fn()` would currently have nothing to visibly change in the browser, since
there is no live-event feed to append to, only the static identity/settings/device-state
tables (REQ-033/REQ-051). Closing this needs both: threading `self._broadcast_fn()` into the
two handlers (mirroring how REQ-034 already threads it into `_Ble20AppLayer._write_dpid_setting()`/
`_write()`), and adding a Mera-style rendered session-log section to Alba's webui page (the
generic module from REQ-037 already provides the primitives; this would be new markup + a
server-side log-line buffer analogous to Mera's, not a new module).

### REQ-057 — Command/action visibility in webui — Mera

#### Type

Functional

#### Statement

Every `SetCommand` (proc `0x09`) code the Mera mock receives from a connected
central is visible live in the webui — including `ToggleLidPosition`,
`PrepareDescaling`/`ConfirmDescaling`/`CancelDescaling`, `TriggerFlushManually`,
`ResetFilterCounter`, `Stop`, and every other code listed in `.claude/rules/ble-protocol.md`
Layer 1 — not only the two codes (`ToggleAnalShower`, `ToggleLadyShower`) that currently have
a simulated effect. A central sending an as-yet-unsimulated command must be visibly
distinguishable, in the webui, from a central that sent nothing at all.

#### Status

Open

#### Implementation Details

Requested 2026-07-19. This is a *visibility* gap, distinct from
REQ-050's *simulation* gap — REQ-050 tracks giving each command a real device-state effect;
this REQ only asks that receipt of the command be visible, which does not require having
implemented that effect first. Precisely: `_proc_09` (`mera_mock.py`, line 1675) calls
`self._log(...)` — the single call that both appends to the session log and broadcasts live
state (REQ-034) — for `code == 0` and `code == 1` only; every other value of `code` falls
through with no `elif` branch at all and the function returns `b""` silently. Sending e.g.
`ToggleLidPosition` (code 10) against the mock today therefore produces zero webui-visible
trace — identical, from the webui's point of view, to the command never having been sent.
Minimal fix (does not require REQ-050): add a trailing `else` branch in `_proc_09` that logs
the received command by name (via the existing `_PROC_NAMES`-style code→name mapping, or a
small dedicated `Commands`-code→name table per `.claude/rules/ble-protocol.md`'s Layer 1
table) before returning `b""`, e.g. `self._log("·", f"SetCommand code={code}
({name}) — received, not yet simulated")`, so the log/webui shows every command reaching the
mock while remaining honest that most have no simulated effect yet.

### REQ-058 — Command/action visibility in webui — Alba

#### Type

Functional

#### Statement

Every DpId write the Alba mock receives from a connected central is visible
live in the webui — including Command DpIds like 563 (`START_STOP_ANAL_SHOWER`) and every
other Command/Status DpId a central can write — not only the six Nvm-persisted DpIds that
currently trigger a live update.

#### Status

Open

#### Implementation Details

Requested 2026-07-19. `_write()` (`alba_mock.py`, line 651) calls
`self.logger.info(...)` for every write regardless of `behavior`, but only calls
`self._broadcast_fn()` inside the `entry['behavior'] == 3` (Nvm) branch (line 660–664) — a
Command DpId write (e.g. starting the anal shower via DpId 563) does update `self._store` in
memory, so it is reflected the next time the webui's "Device State" section (added under
REQ-051) is fetched, but no live SSE push happens the way a Nvm write already gets, and — since
REQ-056 finds Alba's webui has no session-log panel at all — there is currently no live,
event-level trace of the write happening either, only an eventual, passive value change on the
next fetch. Fix: call `self._broadcast_fn()` unconditionally at the end of `_write()` (after
the `if entry['behavior'] == 3: ...` block, not only inside it), mirroring how Mera's `_log()`
already does logging and broadcasting together on every call regardless of which command
triggered it (REQ-057's fix keeps that same shape for Mera). Depends on the same Alba-side
webui prerequisites REQ-056 identifies (a session-log panel) to be fully visible as a discrete
event, not just as a value that happens to have changed next time the state table is read.

---

## Application-Layer BLE Relay ("Alba-Hub")

Formalized 2026-07-20 from `docs/roadmap.md` → "Geberit AquaClean application-layer BLE relay
to overcome 'BLE Coexistence' issues" (design only, not yet started — that section is the
canonical narrative; this is its content restated as REQ-NNN entries per
`docs/developer/requirements-document-standard.md`). See also
`docs/developer/ble-relay-rest-api-requirements.md`, whose REST API exists specifically to
support building and testing this Hub's relay logic without requiring real BLE hardware for
every iteration.

**Problem this solves:** the real Alba (and Mera Comfort) accept only one BLE connection at a
time. Today, when the bridge polls, the Geberit Home App gets disconnected; when the Remote
Control is used, the bridge gets displaced. The Hub combines the bridge's existing central-role
code and the mock's existing peripheral-role code into a relay: one permanent connection to the
real device on one side, multiple simultaneous simulated-device connections (Home App, Remote
Control, standalone bridge) on the other, with new relay logic in between forwarding DpId
operations and notifications.

**Existing code this reuses** (not new REQs — implementation guidance for whichever REQ below
ends up building each piece):

| Hub component | Existing code |
|---|---|
| Central (real device) | `AlbaClient` / `AquaCleanClient` + `BluetoothLeConnector` |
| Peripheral GATT server | `mock-geberit-alba.py` `_BlePeripheral` |
| Arendi key-exchange + crypto | `_AriendiServerSide` in the mock |
| DpId relay + notification fan-out | new — ~300–500 lines, no existing equivalent |

### REQ-061 — Multiple simultaneous clients coexist without displacement

#### Type

Functional

#### Statement

A Geberit Home App connection, a physical Remote Control connection, and a
standalone bridge connection to the same Alba device coexist simultaneously, with none of them
disconnecting or displacing another.

#### Status

Open

### REQ-062 — Hub peripheral advertises as the real device's own MAC

#### Type

Technical

#### Statement

The Hub's peripheral side advertises under the real device's own BLE MAC
address (via `btmgmt public-addr`), so every client's existing pairing/bond with the real
device remains valid against the Hub without any client re-pairing.

#### Status

Open

**Not the same question as REQ-052's MAC-identity discussion** — REQ-052 asks whether a mock
presenting a *different* identity than a device a client is already bonded to can still be
discovered/paired via some fresh-discovery mechanism (unresolved). This requirement has no
such ambiguity: the Hub is a transparent proxy for clients already bonded to the *real* device,
so it must present the real device's exact identity — spoofing is a hard requirement here, not
an open question.

### REQ-063 — Hub maintains one permanent connection to the real device

#### Type

Technical

#### Statement

The Hub's central side maintains one permanent BLE connection to the real
Alba device and never disconnects it to service an individual client request.

#### Status

Open

### REQ-064 — Relay operates at the application (DpId) layer

#### Type

Technical

#### Statement

The Hub decrypts each client's incoming DpId operation, re-encrypts it for
the real device, forwards it, and reverses the process for the response — because Arendi's
per-session ECDH key exchange (a fresh session key per client) makes a link-layer-transparent
relay impossible; the Hub cannot simply pass bytes through unmodified.

#### Status

Open

### REQ-065 — DataPointInventory is fetched once and served from cache

#### Type

Functional

#### Statement

Every client's DataPointInventory request after the first is served
instantly from a cache populated by exactly one ~15-second fetch against the real device on
first central connection — no client ever triggers a second full inventory fetch.

#### Status

Open

### REQ-066 — Real-device notifications fan out to every connected client

#### Type

Functional

#### Statement

A notification received from the real device is forwarded to every
currently connected client, not only the one whose action triggered it.

#### Status

Open

### REQ-067 — Concurrent client writes are serialized

#### Type

Technical

#### Statement

Write operations from concurrent clients are queued and applied to the
real device strictly one at a time, in the order received — never interleaved, never dropped.

#### Status

Open

### REQ-068 — Alba-Hub is Alba-only; Mera parity not yet designed

#### Type

Technical

#### Statement

The Alba-Hub design (REQ-061 through REQ-067) covers the Alba protocol
only. Mera Comfort has the identical single-connection limitation described in REQ-061's
problem statement, but no corresponding Hub design exists for it yet.

#### Status

Open

Tracked here per `.claude/rules/cross-component-parity.md` (MANDATORY) rather than left as a
silent gap — this is that rule's own postponed-sync entry for the Alba-Hub design as a whole.

---

## Cross-Component Consistency

Enforced going forward by `.claude/rules/cross-component-parity.md` (MANDATORY) — both
requirements below are that rule's two halves, restated here in this document's REQ structure
so each has its own trackable status.

### REQ-059 — Mera/Alba mock parity

#### Type

Technical

#### Statement

Every mock-service feature's requirements and implementation stay in sync
between Mera and Alba, unless a concrete protocol-level reason blocks one side. Whenever
synchronizing the other side is postponed, the postponed task exists as its own `REQ-NNN`
entry in this document with `Status: Open` — not as an unrecorded gap discoverable only by
reading both mocks' source side by side.

#### Status

Done

#### Implementation Details

Formalized 2026-07-19 as its own tracked requirement (previously
only an implementation-detail note under REQ-031) and backed by a MANDATORY project rule,
`.claude/rules/cross-component-parity.md` §1, so it applies to all future mock work by default,
not only where a REQ happens to mention it. Marked Done because the discipline is already
established and demonstrably followed, not because every Mera/Alba asymmetry is currently
closed — the open asymmetries that exist are each already tracked exactly the way this REQ
requires, which is the evidence the policy works: REQ-036 (Alba firmware-profile selector,
blocked on a missing real capture — Mera has REQ-035); REQ-038 (Mera "User sitting" toggle —
Alba already has it); REQ-030/REQ-012 (Mera per-instance identity — Alba has it via REQ-029).
Original policy statement (verbatim, from REQ-031's Implementation Details): "any mock feature
implemented for one protocol gets mirrored in the other at the same time unless there's a
concrete protocol-level reason it can't — tracking two mocks that drift independently is more
housekeeping than doing both up front." First applied instance: REQ-031 (firmware version
persistence, implemented for both models in the same pass). If a future change introduces a
new asymmetry without opening a tracking REQ for it, that is a violation of this requirement,
not an acceptable oversight.

### REQ-060 — Bridge/mock-service parity

#### Type

Technical

#### Statement

Whenever a functionality is wired into the mock-service, the same
functionality is wired into the bridge (`aquaclean_console_app`/`aquaclean_core`) as well, and
vice versa, wherever applicable — i.e. wherever the mock-service and the bridge are
conceptually addressing the same protocol-level feature, not merely an implementation detail
private to one side. Whenever wiring the other side is postponed, the postponed task exists as
its own `REQ-NNN` entry in this document, or as an item in `docs/roadmap.md`, in either case
tracked, not left as an unrecorded gap.

#### Status

Open

#### Implementation Details

Requested 2026-07-19, backed by a MANDATORY project rule,
`.claude/rules/cross-component-parity.md` §2. Unlike REQ-059, this is a newly stated policy,
not a retroactive formalization of an already-demonstrated practice — no prior instance in
this project was explicitly framed as "bridge/mock-service parity" before now, so Status is
Open rather than Done until the policy has an audited, closed-the-loop instance the way
REQ-031 gives REQ-059. The clearest existing example of exactly the asymmetry this requirement
exists to prevent: REQ-050 — `.claude/rules/ble-protocol.md`'s Layer 1 table shows the bridge
already wires most `SetCommand` codes (`ToggleLidPosition`, `PrepareDescaling`,
`TriggerFlushManually`, `ResetFilterCounter`, etc. — marked "✅ all interfaces" there) end to
end (REST/MQTT/webui/CLI), while the Mera mock still no-ops all but two of them — an asymmetry
that predates this REQ and was tracked (correctly, per this policy's requirement) as REQ-050,
just not under a "bridge/mock parity" framing until now. Scope note: not every mock feature
needs a bridge counterpart or vice versa — a mock-only concern (BLE advertising/timing quirks,
GATT-cache workarounds, the mock's own webui) or a bridge-only concern (ESPHome proxy
reconnection, MQTT/REST/HA-discovery wiring) is exempt, since neither is a protocol-level
device capability the other side would ever need to exercise. Not yet done: a systematic audit
of existing bridge features vs. mock simulation coverage (and vice versa) beyond the REQ-050
example already known — such an audit, if run, should convert any newly found asymmetry into
its own `REQ-NNN` or roadmap item per this requirement, not just this document's own narrative
text.

---

## Retired sections

The previous "§10 Decisions log" and "§11 Implementation plan & phase status" tables are
retired as of this refactor (2026-07-19) — every row in both mapped onto exactly one REQ
above, and keeping a second copy of the same status risked drifting out of sync. Where each
one moved:

**Former decisions log** → REQ-009 (single `--model` registry), REQ-007 (standalone wrappers
kept), REQ-049 (webui bind-failure — was already "Open" in the decisions log too), REQ-037
(fresh generic frontend module), REQ-032 (independent per-device routing).

**Former phase table** → Phase 1 (shared modules) → REQ-004/REQ-046; Phase 2/2b (Mera class +
persistence) → REQ-006; Phase 2c (Mera per-instance identity) → REQ-030; Phase 3 (Alba class)
→ REQ-006; Phase 4 (single-device orchestrator) → REQ-002; Phase 5 (multi-device concurrency)
→ REQ-003; Phase 6 (webui) → REQ-032 through REQ-038; Phase 7 (logging) → REQ-039 through
REQ-044; Phase 8 (Sela mock) → not yet a REQ at all (no detailed requirement has been written
for Sela beyond "plugs into the same class/registry pattern once built," per REQ-009's
registry design already being open-ended enough to accept it — tracked as a pre-existing
`docs/roadmap.md` item, not duplicated here); Phase 9/9a/9b/9c (firmware override/persistence/
update-simulation/profile-selector) → REQ-011, REQ-031, REQ-053/REQ-054, REQ-035/REQ-036;
Phase 10 (SetCommand) → REQ-050; Phase 11 (webui device-state) → REQ-051; Phase 12 (Remote
Control) → REQ-052; Phase 13 (no hardcoded values) → REQ-012 through REQ-020; Phase 14 (User
sitting toggle) → REQ-038.

**Implementation order** (the one thing the old phase table conveyed that a flat REQ list
doesn't make explicit — dependency sequencing, not status):
REQ-004/REQ-046 (shared modules) before REQ-006 (per-model class refactor, both models) before
REQ-002 (single-device orchestrator) before REQ-003 (multi-device concurrency) before REQ-032
onward (webui) and REQ-039 onward (logging), which were in practice built in parallel with/
slightly ahead of REQ-003. REQ-031 (firmware persistence) before REQ-053 (update simulation,
which depends on it) before REQ-035 (profile selector, which reuses REQ-053's write hook).
REQ-029 (Alba per-instance identity) is the precedent REQ-030/REQ-012 (Mera's + the general
case) still need to follow.

## Issues

Added 2026-07-20, per `docs/developer/requirements-document-standard.md` Rule 4/5. Tracks
implementation-time problems that don't map cleanly onto one specific requirement's
`Implementation Details` — a bug found during work on something else, a design tension between
two requirements, an external blocker. If an issue turns out to belong to exactly one
requirement after all, move it into that requirement's `Implementation Details` instead of
leaving it here. IDs are `REQ-ISS-NNN`, stable, never reused or renumbered.

### REQ-ISS-001 — Remote Control PSK (keyset_id=1) is unknown

#### Statement

The Remote Control's pre-shared key (Arendi keyset_id=1) is not known, so the
Alba-Hub (REQ-062 through REQ-067) cannot decrypt or re-encrypt Remote Control traffic — the
Hub cannot relay the Remote Control side of REQ-061's "coexist without displacement" promise
until this key is found.

#### Status

Open, blocking

#### Details

Tracked upstream as GitHub issue #21. Full background:
`docs/developer/mock-geberit-alba.md#blocker-2--keyset_id1-psk-unknown`. Logged as a distinct
issue rather than folded into REQ-061's `Implementation Details` because REQ-061 is itself
still `Open` — no implementation exists yet for that field to describe, but this specific
blocker is real and referenceable now.
