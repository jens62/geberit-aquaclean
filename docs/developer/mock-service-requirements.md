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

- One SQLite file per device instance, keyed by `(model, adapter)`, under `--state-dir` —
  e.g. `mock_state/<model>-<adapter>.sqlite3`. Mera on `hci1` and Sela on `hci2` must never
  share or clobber each other's store.
- Shared schema/module in `aquaclean_ble_relay/mock_persistence.py` (already scaffolded) —
  not Alba-specific. Implemented once, reused by every model.
- Row shape: `(namespace, index, value, datatype, behavior, min, max, persist)`.
  - `namespace` is required because Mera addresses settings via multiple index spaces that
    each restart at 0 (`profile_setting`, `common_setting`, `active_setting`, `spl`); Alba's
    flat DpId space doesn't need it but fits the same shape (`namespace='dpid'`).
  - `persist` is required because not everything addressable is a durable setting — some
    `spl` indices are live sensor/state signals that must reset on every mock restart,
    exactly like a real power-cycle would. Full index-by-index classification for Mera is in
    `docs/roadmap.md` (see top of this doc).
- **Write-through, not write-on-shutdown.** Every setting change arriving over the protocol
  (`SetStoredProfileSetting`, `SetActiveCommonSetting`, DpId write, etc.) persists to that
  device's DB immediately, synchronously, at the moment of the write — not batched or
  flushed at shutdown. This is what makes the §0 acceptance test hold even under an abrupt
  SIGINT/SIGTERM.
- **Startup never overwrites an existing store.** Load the DB; only seed hardcoded defaults
  if that device's DB is empty or missing. An existing DB always wins.

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

- **Backward compatibility / migration path — OPEN.** Do `tools/mock-geberit-mera.py` and
  `tools/mock-geberit-alba.py` get retired outright once `mock_service.py` exists, or kept as
  thin single-device wrappers around the same refactored class (useful for quick manual
  testing without spinning up the orchestrator)? Affects whether the class API needs to
  support a "just run one, standalone" mode.
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
| 2 | Keep standalone single-device mock scripts as thin wrappers, or retire them? | Open — §9 |
| 3 | Webui bind failure: abort whole service, or degrade that one device to headless? | Open — §9 |
