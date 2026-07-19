# Cross-Component Parity (MANDATORY)

Two parity obligations apply to all work in this repo. Both follow the same shape: keep two
things in sync whenever possible; when sync is postponed, record the postponement as a
tracked requirement instead of leaving an unrecorded gap.

---

## 1. Mera ↔ Alba mock parity

Any mock-service feature/requirement implemented for one protocol (Mera or Alba) is
implemented for the other at the same time, unless a concrete protocol-level reason blocks
one side.

If synchronizing the other side is postponed, the postponed task is recorded as its own entry
in `docs/developer/mock-service-requirements.md` (a `REQ-NNN` with `Status: Open`) — never
left as a silent, unrecorded gap.

**Why:** explicit user instruction, 2026-07-16 — tracking two mocks that drift independently
is more housekeeping than doing both up front.

**How to apply:** before marking any Mera-only or Alba-only mock feature "Done," check whether
the other protocol has (or needs) the equivalent. If it's missing and out of scope for the
current task, add/keep a `REQ-NNN` for it rather than leaving the asymmetry undocumented. See
`docs/developer/mock-service-requirements.md` REQ-059 (this rule's own requirement entry),
and precedent instances: REQ-031 (firmware persistence — first applied instance),
REQ-029/REQ-030/REQ-012 (per-instance identity), REQ-035/REQ-036 (firmware profile selector),
REQ-038 (Mera "User sitting" toggle, Alba parity gap).

---

## 2. Bridge ↔ mock-service parity

Whenever a functionality is wired into the mock-service, the same functionality is wired into
the bridge (`aquaclean_console_app`/`aquaclean_core`) as well, and vice versa — whenever
applicable, i.e. whenever both sides conceptually address the same protocol-level feature. A
mock-only concern (e.g. BLE advertising/timing quirks, GATT-cache workarounds) or a
bridge-only concern (e.g. ESPHome proxy reconnection, MQTT/REST wiring) is exempt — "whenever
applicable" means the same *protocol-level* capability, not every implementation detail.

If wiring the other side is postponed, the postponed task is recorded as its own entry in
`docs/developer/mock-service-requirements.md` or `docs/roadmap.md` — never left as a silent,
unrecorded gap.

**Why:** requested 2026-07-19 — a feature wired on only one side (e.g. the bridge already
sends a `SetCommand` code the mock silently no-ops, per REQ-050; or a mock simulates behavior
the bridge has no client code to exercise) is otherwise invisible until someone happens to
notice during manual testing.

**How to apply:** before marking any bridge or mock-service feature "Done," check the other
side for the same protocol-level capability. If it's missing and out of scope for the current
task, add a `REQ-NNN` (`docs/developer/mock-service-requirements.md`) or a roadmap item
(`docs/roadmap.md`) instead of silently leaving it unimplemented. See
`docs/developer/mock-service-requirements.md` REQ-060 (this rule's own requirement entry) and
REQ-050 (Mera mock's `SetCommand` simulation gap — the bridge already wires most of these
commands; the mock doesn't yet — the first concrete case this rule formalizes).
