# BLE Relay REST API — Requirements Definition

**Scope:** a REST API exposed by `aquaclean_ble_relay` (mock-service) and consumed by
`aquaclean_console_app` (the bridge) — specifically the bridge's device-client code
(`AlbaClient` / `AquaCleanClient`) talking to a simulated device over REST instead of BLE.
Covers every DpId (Alba) and every proc (Mera) needed to eventually build the full-featured
"Geberit AquaClean application-layer BLE relay" ("Alba-Hub") described in
[`docs/roadmap.md` → "Geberit AquaClean application-layer BLE relay to overcome 'BLE
Coexistence' issues"](https://github.com/jens62/geberit-aquaclean/blob/main/docs/roadmap.md#geberit-aquaclean-application-layer-ble-relay-to-overcome-ble-coexistence-issues)
— that content is formalized as `REQ-061` through `REQ-068` (plus `REQ-ISS-001`, the Remote
Control PSK blocker) in `docs/developer/mock-service-requirements.md` § "Application-Layer BLE
Relay ('Alba-Hub')". This document's REST API exists to support building and testing that
Hub's relay logic without real BLE hardware for every iteration — it is not itself the Hub.

**Not to be confused with:** the bridge's own existing outward-facing REST API
(`aquaclean_console_app/RestApiService.py`), which serves the bridge's webui/HA/MQTT clients
and already exists today. This document specifies a *different* API, conceptually in the
opposite direction — here the mock-service is the server, and the bridge's own device-client
code is the client, replacing a BLE connection with an HTTP one.

**Why this exists.** The bridge's `AlbaClient`/`AquaCleanClient` currently only know how to
talk BLE (via `BluetoothLeConnector`, either local `bleak` or an ESPHome proxy). Building and
testing the Alba-Hub's relay logic — and bridge-side development against a simulated device
more generally — currently requires real BLE hardware and a real or mocked BLE peripheral. A
REST transport lets the bridge reach the mock-service without any BLE stack involved at all:
faster, scriptable, CI-friendly, and immune to the exact BLE coexistence problems the Hub
itself exists to solve.

**Document structure:** follows `docs/developer/requirements-document-standard.md`, same
convention as `docs/developer/mock-service-requirements.md` — every requirement has a unique
ID, a `Type` (`Functional` | `Technical`), a declarative present-tense `Statement` of the
actual/intended behavior, a `Status` (`Open | In Progress | Done | Deferred | Superseded`), and
`Implementation Details` wherever `Status` is not `Open`. IDs use the `RAPI-NNN` prefix —
deliberately distinct from that document's `REQ-NNN` IDs, so a bare ID is never ambiguous about
which document it belongs to even quoted out of context. Implementation-time issues that don't
map to one specific requirement go in `## Issues` (`RAPI-ISS-NNN`) at the end of this document.

**Status of this document (2026-07-19):** first draft, intentionally not comprehensive yet.
Captures the overarching goal and the foundational technical/functional shape; the full
per-DpId/per-proc endpoint enumeration is deferred to a later refinement pass — see each
requirement's own `Status` below (everything here is currently `Open`; nothing has been
built yet, matching the roadmap's own "Status: design only — not yet started" for the whole
Alba-Hub concept).

Referenced from `docs/developer/mock-service-requirements.md`.

---

## Requirements Index

| ID | Type | Status | Summary |
|---|---|---|---|
| RAPI-001 | Functional | Open | The bridge connects to a running mock-service instance using a REST API instead of BLE |
| RAPI-002 | Functional | Open | The REST API's DpId/proc coverage is complete enough to build the full-featured application-layer BLE relay |
| RAPI-003 | Functional | Open | Each Alba DpId is readable and writable through the REST API with the same semantics as a real BLE GATT read/write |
| RAPI-004 | Functional | Open | Each Mera proc is invokable through the REST API with the same request/response shape as the real BLE exchange |
| RAPI-005 | Functional | Open | The REST API delivers spontaneous device notifications to a connected bridge client without the client polling for them |
| RAPI-006 | Technical | Open | The bridge's transport choice (BLE vs. REST) for a given device connection is a configuration setting, not a code branch scattered through client logic |
| RAPI-007 | Technical | Open | The REST API's notification delivery reuses the mock-service's existing SSE mechanism rather than introducing a second push mechanism |
| RAPI-008 | Technical | Open | The REST API's error responses distinguish an unreachable simulated device, an invalid request, and an unsupported operation from one another |
| RAPI-009 | Functional | Open | A bridge REST client and the existing webui browser client can act against the same simulated device at the same time |
| RAPI-010 | Technical | Open | The REST API versions independently of the mock-service's webui settings-editing endpoints, so one can change without breaking the other |

---

## Goal

### RAPI-001 — The bridge connects to the mock-service over REST

#### Type

Functional

#### Statement

The bridge's device-client code establishes a working session against a
running mock-service instance using HTTP requests, without opening any BLE connection, for
at least one supported device model.

#### Status

Open

### RAPI-002 — Full DpId/proc coverage

#### Type

Functional

#### Statement

The REST API exposes every Alba DpId and every Mera proc that the
full-featured application-layer BLE relay (`docs/roadmap.md`'s "Alba-Hub") needs to decrypt,
relay, and re-encrypt on behalf of any connected client.

#### Status

Open

---

## Device operations

### RAPI-003 — Alba DpId read/write parity with real BLE

#### Type

Functional

#### Statement

A REST request to read or write a given Alba DpId produces the same result the
bridge would observe from a real BLE GATT read/write to the real device for that DpId —
including datatype encoding, min/max validation, and Protected/Nvm write restrictions.

#### Status

Open

### RAPI-004 — Mera proc invocation parity with real BLE

#### Type

Functional

#### Statement

A REST request to invoke a given Mera proc (with its args) produces the same
response shape the bridge would observe from the real BLE proc exchange for that proc.

#### Status

Open

### RAPI-005 — Spontaneous notification delivery

#### Type

Functional

#### Statement

The mock-service pushes a spontaneous device-state change to every connected
REST client as it happens, the same way a real device's BLE notification reaches the bridge
without the bridge polling for it.

#### Status

Open

---

## Bridge-side integration

### RAPI-006 — Transport selection is configuration, not code branching

#### Type

Technical

#### Statement

The bridge decides whether a given device connection uses BLE or REST from a
single configuration setting, and every other piece of client logic (polling, command
dispatch, error handling) is unaware of which transport is actually in use underneath.

#### Status

Open

---

## Technical shape of the API

### RAPI-007 — Notification delivery reuses existing SSE, not a new mechanism

#### Type

Technical

#### Statement

The REST API's push channel for spontaneous notifications is the mock-service's
existing SSE endpoint (already serving the webui's live state updates), extended for this
purpose rather than duplicated as a second, separate push mechanism.

#### Status

Open

### RAPI-008 — Distinguishable error categories

#### Type

Technical

#### Statement

Every REST API error response is distinguishable, by category, as one of: the
simulated device is unreachable/not connected, the request itself is malformed or invalid, or
the requested operation is not supported for this device/model.

#### Status

Open

### RAPI-009 — Concurrent bridge + webui clients

#### Type

Functional

#### Statement

A bridge REST client and a human using the mock-service's existing webui can
both act against the same simulated device in the same process at the same time, each seeing
the other's changes.

#### Status

Open

### RAPI-010 — Independent versioning from the webui settings API

#### Type

Technical

#### Statement

The REST API defined by this document has its own version/compatibility
lifecycle, separate from the mock-service's existing `/settings/...` webui-editing endpoints —
a breaking change to one does not require a breaking change to the other.

#### Status

Open

## Issues

Added 2026-07-20, per `docs/developer/requirements-document-standard.md` Rule 4/5. Tracks
implementation-time problems that don't map cleanly onto one specific requirement's
`Implementation Details`. IDs are `RAPI-ISS-NNN`, stable, never reused or renumbered.

*(none logged yet)*

