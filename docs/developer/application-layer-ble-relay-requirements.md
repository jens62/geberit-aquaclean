# Geberit AquaClean Application-Layer BLE Relay ("Alba-Hub") — Requirements Definition

**Scope:** the proposed relay/hub component described in `docs/roadmap.md` →
["Geberit AquaClean application-layer BLE relay to overcome 'BLE Coexistence'
issues"](https://github.com/jens62/geberit-aquaclean/blob/main/docs/roadmap.md#geberit-aquaclean-application-layer-ble-relay-to-overcome-ble-coexistence-issues)
— that section is the canonical narrative; this document restates its content as structured
requirements per `docs/developer/requirements-document-standard.md`.

**Not Alba-only by scope, even though today's design is.** The single-BLE-connection
limitation this relay solves affects both Alba and Mera Comfort — the *problem* (RQ-001) is
stated for both. The *design* worked out so far ("Alba-Hub") only covers Alba; Mera has no
corresponding design yet (RELAY-008 tracks this explicitly as an open gap, not a silent
Alba-only assumption).

**Related documents:**
- `docs/developer/mock-service-requirements.md` — the mock-service (`mera_mock.py` /
  `alba_mock.py`) whose existing peripheral GATT server and Arendi crypto code this relay
  reuses. See that document's REQ-052 (Remote Control interoperability, Mera) for a related
  but distinct question — RELAY-002 below explains exactly how they differ.
- `docs/developer/ble-relay-rest-api-requirements.md` — a REST API whose purpose is to support
  building and testing this relay's application-layer logic without requiring real BLE
  hardware for every iteration. That document is not this relay; it's a development aid for it.

**Status of this document (2026-07-20):** first draft, matching the roadmap section's own
"design only — not yet started." Every requirement below is currently `Open`.

---

## Requirements Index

| ID | Type | Status | Summary |
|---|---|---|---|
| RELAY-001 | Functional | Open | Multiple simultaneous clients coexist without displacement |
| RELAY-002 | Technical | Open | Hub peripheral advertises as the real device's own MAC (spoofed identity) |
| RELAY-003 | Technical | Open | Hub maintains one permanent connection to the real device |
| RELAY-004 | Technical | Open | Relay operates at the application (DpId) layer, not the BLE link layer |
| RELAY-005 | Functional | Open | DataPointInventory is fetched once and served from cache |
| RELAY-006 | Functional | Open | Real-device notifications fan out to every connected client |
| RELAY-007 | Technical | Open | Concurrent client writes are serialized |
| RELAY-008 | Technical | Open | Current design is Alba-only; Mera parity not yet designed |

---

**Problem this solves:** the real Alba (and Mera Comfort) accept only one BLE connection at a
time. Today, when the bridge polls, the Geberit Home App gets disconnected; when the Remote
Control is used, the bridge gets displaced. The Hub combines the bridge's existing central-role
code and the mock's existing peripheral-role code into a relay: one permanent connection to the
real device on one side, multiple simultaneous simulated-device connections (Home App, Remote
Control, standalone bridge) on the other, with new relay logic in between forwarding DpId
operations and notifications.

**Existing code this reuses** (not requirements — implementation guidance for whichever
requirement below ends up building each piece):

| Hub component | Existing code |
|---|---|
| Central (real device) | `AlbaClient` / `AquaCleanClient` + `BluetoothLeConnector` |
| Peripheral GATT server | `mock-geberit-alba.py` `_BlePeripheral` |
| Arendi key-exchange + crypto | `_AriendiServerSide` in the mock |
| DpId relay + notification fan-out | new — ~300–500 lines, no existing equivalent |

### RELAY-001 — Multiple simultaneous clients coexist without displacement

#### Type

Functional

#### Statement

A Geberit Home App connection, a physical Remote Control connection, and a
standalone bridge connection to the same Alba device coexist simultaneously, with none of them
disconnecting or displacing another.

#### Status

Open

### RELAY-002 — Hub peripheral advertises as the real device's own MAC

#### Type

Technical

#### Statement

The Hub's peripheral side advertises under the real device's own BLE MAC
address (via `btmgmt public-addr`), so every client's existing pairing/bond with the real
device remains valid against the Hub without any client re-pairing.

#### Status

Open

**Not the same question as `mock-service-requirements.md` REQ-052's MAC-identity discussion**
— REQ-052 asks whether a mock presenting a *different* identity than a device a client is
already bonded to can still be discovered/paired via some fresh-discovery mechanism
(unresolved). This requirement has no such ambiguity: the Hub is a transparent proxy for
clients already bonded to the *real* device, so it must present the real device's exact
identity — spoofing is a hard requirement here, not an open question.

### RELAY-003 — Hub maintains one permanent connection to the real device

#### Type

Technical

#### Statement

The Hub's central side maintains one permanent BLE connection to the real
Alba device and never disconnects it to service an individual client request.

#### Status

Open

### RELAY-004 — Relay operates at the application (DpId) layer

#### Type

Technical

#### Statement

The Hub decrypts each client's incoming DpId operation, re-encrypts it for
the real device, forwards it, and reverses the process for the response — because Arendi's
per-session ECDH key exchange (a fresh session key per client) makes a link-layer-transparent
relay impossible; the Hub cannot simply pass bytes through unmodified.

#### Status

Open

### RELAY-005 — DataPointInventory is fetched once and served from cache

#### Type

Functional

#### Statement

Every client's DataPointInventory request after the first is served
instantly from a cache populated by exactly one ~15-second fetch against the real device on
first central connection — no client ever triggers a second full inventory fetch.

#### Status

Open

### RELAY-006 — Real-device notifications fan out to every connected client

#### Type

Functional

#### Statement

A notification received from the real device is forwarded to every
currently connected client, not only the one whose action triggered it.

#### Status

Open

### RELAY-007 — Concurrent client writes are serialized

#### Type

Technical

#### Statement

Write operations from concurrent clients are queued and applied to the
real device strictly one at a time, in the order received — never interleaved, never dropped.

#### Status

Open

### RELAY-008 — Current design is Alba-only; Mera parity not yet designed

#### Type

Technical

#### Statement

The Hub design worked out so far (RELAY-001 through RELAY-007) covers the
Alba protocol only. Mera Comfort has the identical single-connection limitation this whole
document exists to solve, but no corresponding Hub design exists for it yet.

#### Status

Open

Tracked here per `.claude/rules/cross-component-parity.md` (MANDATORY) rather than left as a
silent gap — this is that rule's own postponed-sync entry for the Hub design as a whole.

---

## Issues

Per `docs/developer/requirements-document-standard.md` Rule 4/5. Tracks implementation-time
problems that don't map cleanly onto one specific requirement's `Implementation Details` — a
bug found during work on something else, a design tension between two requirements, an
external blocker. If an issue turns out to belong to exactly one requirement after all, move
it into that requirement's `Implementation Details` instead of leaving it here. IDs are
`RELAY-ISS-NNN`, stable, never reused or renumbered.

### RELAY-ISS-001 — Remote Control PSK (keyset_id=1) is unknown

#### Statement

The Remote Control's pre-shared key (Arendi keyset_id=1) is not known, so the
Hub (RELAY-002 through RELAY-007) cannot decrypt or re-encrypt Remote Control traffic — the
Hub cannot relay the Remote Control side of RELAY-001's "coexist without displacement" promise
until this key is found.

#### Status

Open, blocking

#### Details

Tracked upstream as GitHub issue #21. Full background:
`docs/developer/mock-geberit-alba.md#blocker-2--keyset_id1-psk-unknown`. Logged as a distinct
issue rather than folded into RELAY-001's `Implementation Details` because RELAY-001 is itself
still `Open` — no implementation exists yet for that field to describe, but this specific
blocker is real and referenceable now.
