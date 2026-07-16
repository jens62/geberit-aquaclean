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

- Multi-device aware: either one HTTP server with one route per active device (`/mera`,
  `/sela`, `/alba`, or keyed by adapter) on a single port, or a landing page listing running
  instances linking to per-device pages.
- Full settings table per device (value, datatype, behavior, min/max), inline per-row edit,
  **Reset to factory defaults** scoped to exactly one device's store — never all of them.

## 7. Logging

- One logger per device instance, named by the same `(model, adapter)` key used for
  persistence (e.g. `mock.mera.hci1`) — not one shared hardcoded logger name.
- Device tag at a fixed position in every line (immediately after the timestamp) so a
  script/CI consumer can reliably `grep`/`awk` on it regardless of message content.
- Both a combined process-wide log (chronological, cross-device correlation) and a
  per-device log file, via multiple handlers on the same per-device logger — not a choice
  between the two.
- Log filenames follow the same `<model>-<adapter>` naming convention as the persistence DB.

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

## 10. Decisions log

| # | Decision | Status |
|---|---|---|
| 1 | `--model` single open-ended lookup table (not `--protocol` + `--model` split) | **Resolved** — §3 |
| 2 | Standalone single-device mock scripts kept as thin wrappers around the refactored class, not retired | **Resolved** — §2 |
| 3 | Webui bind failure: abort whole service, or degrade that one device to headless? | Open — §9 |
