# Geberit AquaClean Application-Layer BLE Relay ("Alba-Hub") — Requirements Definition

**Scope:** the proposed relay/hub component ("AquaClean Hub"). Two canonical narrative
sources, both restated here as structured requirements per
`docs/developer/requirements-document-standard.md`:
- `docs/roadmap.md` →
  ["Geberit AquaClean application-layer BLE relay to overcome 'BLE Coexistence'
  issues"](https://github.com/jens62/geberit-aquaclean/blob/main/docs/roadmap.md#geberit-aquaclean-application-layer-ble-relay-to-overcome-ble-coexistence-issues)
  — the short version.
- `docs/developer/aquaclean-application-layer-relay.md` — the full architecture reference
  (motivation, diagrams, per-protocol crypto reasoning, REST API design, latency
  measurements, implementation order). Substantially more detailed than the roadmap section;
  most of the content below (RELAY-009 onward) comes from here, added 2026-07-20.

**Not Alba-only by scope, even though today's design is mostly Alba-specific.** The
single-BLE-connection limitation this relay solves affects both Alba and Mera Comfort — the
*problem* (RELAY-001) is stated for both. Mera's relay mechanism is now partially worked out
(RELAY-012, RELAY-013) but not yet implemented or empirically verified — see RELAY-008.

**Related documents:**
- `docs/developer/mock-service-requirements.md` — the mock-service (`mera_mock.py` /
  `alba_mock.py`) whose existing peripheral GATT server and Arendi crypto code this relay
  reuses. See that document's REQ-052 (Remote Control interoperability, Mera) for a related
  but distinct question — RELAY-002 below explains exactly how they differ.
- `docs/developer/ble-relay-rest-api-requirements.md` — a *different*, dev/test-only REST API
  for the bridge to talk to the mock-service directly (bypassing BLE) while building relay
  logic. Not to be confused with RELAY-009 below, which is the Hub's own *production* REST API
  re-exposing the bridge's existing `RestApiService.py` to the Hub's real clients (HACS,
  standalone bridge frontend) once the Hub itself is built.

**Status of this document (2026-07-20):** first draft, matching both source documents' own
"design only — not yet started" / "architecture defined, implementation not yet started".
Every requirement below is currently `Open`.

---

## Requirements Index

| ID | Type | Status | Summary |
|---|---|---|---|
| RELAY-001 | Functional | Open | Multiple simultaneous clients coexist without displacement |
| RELAY-002 | Technical | Open | Hub peripheral advertises as the real device's own MAC (spoofed or re-paired identity) |
| RELAY-003 | Technical | Open | Hub maintains one permanent connection to the real device |
| RELAY-004 | Technical | Open | Relay operates at the application (DpId) layer, not the BLE link layer (Alba) |
| RELAY-005 | Functional | Open | DataPointInventory is fetched once and served from cache |
| RELAY-006 | Functional | Open | Real-device notifications fan out to every connected client |
| RELAY-007 | Technical | Open | Concurrent client writes are serialized |
| RELAY-008 | Technical | Open | Mera relay mechanism is designed but not implemented or verified |
| RELAY-009 | Functional | Open | Hub's REST API re-exposes the bridge's existing RestApiService.py unmodified |
| RELAY-010 | Technical | Open | One BLE adapter, via BlueZ's native multi-connection support, serves every role |
| RELAY-011 | Technical | Open | Each peripheral-side client has its own independent session, not one shared session |
| RELAY-012 | Technical | Open | Mera app/bridge relay path forwards bytes with no decrypt/re-encrypt step |
| RELAY-013 | Technical | Open | Mera Remote relay path relies on BlueZ SMP bonding, not an application-layer step |
| RELAY-014 | Functional | Open | Peripheral-side round-trip latency is ~100–200 ms on the Hub's production host |
| RELAY-015 | Functional | Open | HACS integration and standalone bridge consume the Hub over REST with no BLE code |
| RELAY-016 | Technical | Open | Implementation follows the baseline logging/no-hardcoded-values/module-size conventions |

---

**Problem this solves:** the real Alba (and Mera Comfort) accept only one BLE connection at a
time — confirmed in the Geberit AquaClean Alba User Manual, chapter "Operating concept" (p.
10): *"The remote control function of the Geberit AquaClean shower toilet is deactivated
while the shower toilet is connected to the Geberit Home App."* Today, when the bridge polls,
the Geberit Home App gets disconnected; when the Remote Control is used, the bridge gets
displaced. The Hub combines the bridge's existing central-role code and the mock's existing
peripheral-role code into a relay: one permanent connection to the real device on one side,
multiple simultaneous simulated-device connections (Home App, Remote Control, standalone
bridge) on the other, with new relay logic in between forwarding DpId operations and
notifications.

**No off-the-shelf hardware relay exists for this.** ESPHome BT proxy and Ubertooth operate at
the BLE link layer — they relay one connection at a time but cannot multiplex multiple clients
onto a single peripheral identity. ESPHome's proxy slots are specifically *outbound central*
slots (it connects *to* peripherals and forwards to HA) — the opposite role from what the Hub
needs (accepting *inbound* connections as a peripheral). The Hub must terminate each client
connection independently, which requires understanding the protocol — hence "application
layer," not a design preference.

**Existing code this reuses** (not requirements — implementation guidance for whichever
requirement below ends up building each piece):

| Hub component | Existing code | Status |
|---|---|---|
| Central (real device) | `AlbaClient` / `AquaCleanClient` + `BluetoothLeConnector` | Production-ready |
| Peripheral GATT server | `mock-geberit-alba.py` `_BlePeripheral` | Working |
| Arendi key-exchange + crypto (keyset_id=0) | `_AriendiServerSide` in the mock | Working |
| REST API | `aquaclean_console_app/RestApiService.py` | Production-ready |
| DpId relay + notification fan-out | new | ~300–500 lines, no existing equivalent |
| Arendi keyset_id=1 (Remote) | not implemented | PSK unknown — RELAY-ISS-001 |

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

The Hub's peripheral side presents the real device's own BLE identity, so
every client's existing pairing/bond with the real device remains valid against the Hub
without any client re-pairing.

#### Status

Open

**Two ways to achieve this, per `docs/developer/aquaclean-application-layer-relay.md` §7.2.4 —
re-pairing is the recommended default, not MAC spoofing:**
1. **Re-pair (recommended).** With the Hub peripheral already running, put each client into
   its normal pairing action (e.g. hold `<+>` on the Remote Control ≈20s for Alba). The client
   re-pairs to whichever device responds and stores the *Hub's* adapter MAC as the new target.
   No spoofing needed — this only requires the client to go through its pairing action once.
2. **MAC spoof** (`sudo btmgmt public-addr <real_device_mac>`) — needed only if re-pairing every
   client isn't acceptable (e.g. preserving an existing Remote Control pairing without asking
   the user to redo it). Confirmed working on the ASUS USB-BT500 adapter already used for mock
   development.

**Not the same question as `mock-service-requirements.md` REQ-052's MAC-identity discussion**
— REQ-052 asks whether a mock presenting a *different* identity than a device a client is
already bonded to can still be discovered/paired via some fresh-discovery mechanism
(unresolved). This requirement has no such ambiguity: the Hub is a transparent proxy for
clients already bonded to the *real* device, and option 1 above is exactly that
fresh-discovery mechanism, already confirmed to work for pairing purposes generally.

### RELAY-003 — Hub maintains one permanent connection to the real device

#### Type

Technical

#### Statement

The Hub's central side maintains one permanent BLE connection to the real
Alba device and never disconnects it to service an individual client request.

#### Status

Open

### RELAY-004 — Relay operates at the application (DpId) layer (Alba)

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
first central connection — no client ever triggers a second full inventory fetch. This
specifically prevents the ~27-second device-occupation window that caused GitHub issue #15.

#### Status

Open

### RELAY-006 — Real-device notifications fan out to every connected client

#### Type

Functional

#### Statement

A notification received from the real device is forwarded to every
currently connected client, not only the one whose action triggered it, and updates the
RELAY-005 cache at the same time.

#### Status

Open

### RELAY-007 — Concurrent client writes are serialized

#### Type

Technical

#### Statement

Write operations from concurrent clients are queued (one `asyncio.Queue`)
and applied to the real device strictly one at a time, in the order received — never
interleaved, never dropped. The central-side consumer awaits the device's acknowledgement for
each write before signaling the originating client session and processing the next.

#### Status

Open

### RELAY-008 — Mera relay mechanism is designed but not implemented or verified

#### Type

Technical

#### Statement

Mera Comfort's relay mechanism has a worked-out design (RELAY-012 for the
app/bridge path, RELAY-013 for the Remote path) but no implementation exists yet, and
RELAY-013 specifically has never been empirically exercised — the Mera Remote's own
application protocol has never been decoded (RELAY-ISS-002).

#### Status

Open

Tracked here per `.claude/rules/cross-component-parity.md` (MANDATORY) rather than left as a
silent gap — this is that rule's own postponed-sync entry for the Hub design as a whole.
Superseded framing, corrected 2026-07-20: this requirement previously read "Alba-Hub is
Alba-only; Mera parity not yet designed" — wrong once `aquaclean-application-layer-relay.md`
§8 was read; Mera parity is *partially* designed, not absent.

### RELAY-009 — Hub's REST API re-exposes the bridge's existing REST API unmodified

#### Type

Functional

#### Statement

The Hub exposes the same REST API the standalone bridge already exposes
today (`GET /info`, `GET /data/state`, `GET /events` SSE, `POST /command/{name}`,
`POST /config/poll-interval`, `POST /config/ble-connection`) via
`aquaclean_console_app/RestApiService.py`, reused as-is — no new REST endpoints are required
for clients that only need state and commands, not raw BLE.

#### Status

Open

### RELAY-010 — One BLE adapter, via BlueZ's native multi-connection support, serves every role

#### Type

Technical

#### Statement

A single BLE adapter (e.g. an ASUS USB-BT500, ~5–7 connection slots shared
between central and peripheral roles) serves the Hub's one outbound connection (to the real
device) and every inbound connection (from Home App, Remote Control, and bridge clients) —
BlueZ on Linux creates a separate D-Bus connection object per incoming `CONNECT_IND`, all
sharing the same GATT server, with no additional BLE hardware required.

#### Status

Open

### RELAY-011 — Each peripheral-side client has its own independent session

#### Type

Technical

#### Statement

Each client connected to the Hub's peripheral side has its own session
(e.g. its own `_AriendiServerSide` instance for Alba), keyed by the BlueZ D-Bus device path
BlueZ provides on every write — not the single shared global session the existing mock uses
today. Session creation happens on connect, teardown on disconnect, and notification fan-out
(RELAY-006) iterates every live session.

#### Status

Open

### RELAY-012 — Mera app/bridge relay path forwards bytes with no decrypt/re-encrypt step

#### Type

Technical

#### Statement

The Hub's relay path for Mera Home App and bridge clients forwards Geberit
procedure bytes (proc `0x09`, `0x0D`, etc.) nearly transparently, with no decrypt/re-encrypt
step, because that path has neither BLE link-layer encryption (zero SMP, no LTK) nor an
Arendi-style application-layer security protocol — confirmed via `onboarding-real-mera.pcapng`
containing zero `LL_ENC_REQ` frames.

#### Status

Open

### RELAY-013 — Mera Remote relay path relies on BlueZ SMP bonding, not an application-layer step

#### Type

Technical

#### Statement

The Hub's relay path for the Mera Remote Control relies on BlueZ's native
SMP bonding (automatic LTK negotiation and storage in `/var/lib/bluetooth/`) rather than an
application-layer decrypt/re-encrypt step, because the Mera Remote encrypts at the BLE link
layer (confirmed: `LL_ENC_REQ` sent immediately after `CONNECT_IND`, before any ATT frame, in
`toogle-lid-with-remote-without-running-bridge.pcapng`), a completely different security model
from Alba's Remote (Arendi application-layer crypto, RELAY-004).

#### Status

Open

Once BlueZ has bonded with the Mera Remote, all subsequent ATT traffic is automatically
decrypted at the kernel level and visible via `btmon` — no manual LTK extraction needed. This
is the same mechanism RELAY-ISS-002 depends on to decode the Remote's application protocol in
the first place.

### RELAY-014 — Peripheral-side round-trip latency is ~100–200 ms on the Hub's production host

#### Type

Functional

#### Statement

The Hub's peripheral-side ATT write→notify round-trip latency is in the
100–200 ms range when the Hub runs on bare-metal Linux (e.g. a Raspberry Pi with the adapter
plugged in directly) — not the ~1,000 ms per-request latency measured when the same BlueZ
peripheral stack runs inside a virtualized host with USB passthrough (UTM VM + USB-BT500,
confirmed via `mock-geberit-mera_2026-06-25_07-22.log`: ~6s for 60 requests on real hardware
vs. ~60s for the same sequence through the VM).

#### Status

Open

The VM/USB-passthrough path is a development-environment artifact, not a Hub production
concern — the Hub itself is expected to run on bare-metal Linux. Recorded here as a
requirement (not just an infrastructure note) because it's directly user-observable: at VM-level
latency, opening the Remote Control screen against a simulated device takes ~60s instead of
~6s, which would be an unacceptable regression if it carried over to the Hub's actual
deployment target.

### RELAY-015 — HACS integration and standalone bridge consume the Hub over REST with no BLE code

#### Type

Functional

#### Statement

The HACS integration's `coordinator.py` and the standalone bridge frontend
can each be configured to reach the Hub exclusively via RELAY-009's REST API and its `/events`
SSE stream — polling `/data/state`, subscribing to `/events`, calling `/command/...` — with no
`BluetoothLeConnector`, no `habluetooth`, and no BLE permissions/adapter access required on
that host at all. Direct-BLE remains available as an alternative connection method, not a
replacement.

#### Status

Open

This is what resolves several problems that are otherwise specific to the HACS integration's
own BLE stack, independent of anything the Hub's BLE side does: GitHub issue #30 (BLE
adapter/proxy issues via Shelly proxy, `BleakError`), `habluetooth` version incompatibilities,
and `bleak`/Python 3.14 compatibility — none of these can occur in REST mode because no BLE
code runs on that host.

### RELAY-016 — Implementation follows the baseline conventions

#### Type

Technical

#### Statement

The Hub's implementation logs through Python's standard `logging` module,
sources every deployment-specific value from a config file or database rather than a
hardcoded literal, and keeps every new module under ~1,000 lines — per
`docs/developer/requirements-document-standard.md` Rule 7.

#### Status

Open

Two specific, already-foreseeable applications, not general restatements:
- **RELAY-002's real device MAC** (and, once RELAY-ISS-001 is resolved, the Remote's PSK) are
  config/database values, not literals baked into the Hub's source — the same principle
  `mock-service-requirements.md` REQ-012 already applies to the mock's own identity/firmware
  values, for the same reason (a different real device or a different Remote means a different
  value, not a code change).
- **The new relay logic (RELAY-004, RELAY-011, ~300–500 lines) is its own module or package
  from the start** — not appended onto `mera_mock.py` or `alba_mock.py`, both of which
  `mock-service-requirements.md` REQ-070 already found well past the ~1,000-line threshold
  (2,569 and 2,196 lines respectively). Bolting new relay code onto either would make an
  already-tracked problem worse, not just fail to fix it.

---

## Issues

Per `docs/developer/requirements-document-standard.md` Rule 4/5. Tracks implementation-time
problems that don't map cleanly onto one specific requirement's `Implementation Details` — a
bug found during work on something else, a design tension between two requirements, an
external blocker. If an issue turns out to belong to exactly one requirement after all, move
it into that requirement's `Implementation Details` instead of leaving it here. IDs are
`RELAY-ISS-NNN`, stable, never reused or renumbered.

### RELAY-ISS-001 — Remote Control PSK (keyset_id=1) is unknown (Alba)

#### Statement

The Alba Remote Control's pre-shared key (Arendi keyset_id=1) is not known,
so the Hub (RELAY-002 through RELAY-007) cannot decrypt or re-encrypt Remote Control traffic
— the Hub cannot relay the Remote Control side of RELAY-001's "coexist without displacement"
promise until this key is found.

#### Status

Open, blocking

#### Details

Tracked upstream as GitHub issue #21. Full background:
`docs/developer/mock-geberit-alba.md#blocker-2--keyset_id1-psk-unknown`. Logged as a distinct
issue rather than folded into RELAY-001's `Implementation Details` because RELAY-001 is itself
still `Open` — no implementation exists yet for that field to describe, but this specific
blocker is real and referenceable now.

**Unblocking plan, per `docs/developer/aquaclean-application-layer-relay.md` §7.2.2/§7.2.3 —
three hypotheses, cheapest first:**
1. **Same PSK as keyset_id=0** — try CMAC verification with the already-known keyset_id=0 PSK.
   Near-zero cost.
2. **Pairing-window bypass** — accept a keyset_id=1 `KE_REQ` unconditionally during the
   device's boot-time pairing window and observe whether the session proceeds. One test run.
3. **PSK extraction from the physical remote's firmware** — impractical, last resort.

**Decisive experiment (mock-based, no Hub needed yet):** add keyset_id=1 handling to
`mock-geberit-alba.py` that (a) logs the Remote's full `KE_REQ` (`client_pub` + `client_CMAC`)
without verifying it, (b) checks whether `client_CMAC` verifies against the keyset_id=0 PSK
(tests hypothesis 1), and (c) during the pairing window, accepts unconditionally and completes
the key exchange (tests hypothesis 2). Then run the mock and put the physical remote into
pairing mode (hold `<+>`). If a keyset_id=1 `KE_REQ` appears at all, this blocker is resolved
and both PSK hypotheses can be tested immediately from the captured CMAC value.

### RELAY-ISS-002 — Mera Remote's application protocol (post-SMP) is undecoded

#### Statement

The Mera Remote Control's application-layer protocol has never been
decoded — every sniffer capture shows only BLE-LL-encrypted ciphertext (confirmed
`toogle-lid-with-remote-without-running-bridge.pcapng`: zero unencrypted ATT frames after
`LL_START_ENC_RSP`). RELAY-013 cannot be implemented with any confidence until this protocol
is known, since the Hub needs to know what it's relaying, not just that it should let BlueZ
handle the encryption.

#### Status

Open, blocking (RELAY-013)

#### Details

`nrf-ble-analyze.py` reports "No Geberit ATT frames found" for these captures because tshark's
ATT dissector needs the session LTK, which a passive sniffer never has. **Unblocking path,
per `docs/developer/aquaclean-application-layer-relay.md` §8.5:** run a Hub/mock peripheral on
a Linux BlueZ host, put the Remote into pairing mode (hold `<+>` and the toilet's own button
simultaneously ≈30s — see `memory/geberit-remote-control-pairing-procedure.md` for the exact
sourced procedure), let BlueZ complete SMP bonding and store the LTK automatically, then
capture with `btmon` — every subsequent ATT frame decodes in plaintext at the kernel level, no
manual key extraction needed. This replaces the sniffer approach entirely for this specific
protocol-discovery problem.

**Progress, 2026-07-20**: step 1 of this unblocking path (get the RC to connect at all) has
now happened for the first time, against `mera_mock.py` — full narrative, evidence, and the
SMP-pairing-agent blocker it ran into: `docs/developer/mock-geberit-mera.md` §"Button-press/
release timing". Same underlying gap blocks RELAY-013's implementation too, not just this
issue.
