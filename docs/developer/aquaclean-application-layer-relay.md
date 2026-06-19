# Geberit AquaClean Application-Layer BLE Relay

A requirement definition and architecture reference for the "AquaClean Hub" —
an application-layer BLE relay that allows multiple devices to coexist with a
single Geberit AquaClean toilet simultaneously.

---

## 1 Introduction and Motivation

### 1.1 The BLE Coexistence Problem

All Geberit AquaClean toilets (Alba, Mera Comfort, and others) accept **exactly one BLE
connection at a time**. This is a hardware constraint at the BLE controller level, not a
firmware policy.

The Geberit manuals document this limitation explicitly:

> "The remote control function of the Geberit AquaClean shower toilet is **deactivated**
> while the shower toilet is connected to the Geberit Home App."
> — Geberit AquaClean Alba User Manual, chapter "Operating concept" (p. 10)

The same constraint affects the AquaClean bridge in all its forms (standalone, HACS
integration): whenever the bridge holds a BLE session, the Geberit Home App is displaced
and vice versa. When the Geberit Remote Control is in active use, the bridge cannot connect.

### 1.2 User Impact

Almost all users want simultaneous operation of:

- **Geberit Home App** — smartphone control and settings
- **Geberit Remote Control** — physical bathroom remote
- **AquaClean Bridge** — HACS integration or standalone process (MQTT / REST / SSE)

Today, only one of these can hold a BLE connection at a time. Every poll by the bridge
displaces the App; every press on the Remote Control displaces both.

### 1.3 The Solution Concept

All components needed to solve this are now available in the codebase. The solution is a
**Geberit AquaClean application-layer BLE relay** — referred to throughout this document
as the **"AquaClean Hub"**.

In this setup the Hub:

1. Connects to the real Geberit toilet as the **single permanent BLE client** (central side)
2. Simultaneously acts as a **BLE peripheral** that impersonates the toilet to multiple clients
3. Relays DpId operations between clients and the real device at the application layer
4. Exposes a **REST API** that allows the HACS integration (and standalone bridge) to operate
   without any BLE involvement

---

## 2 Architecture

### 2.1 Overview

```
                  ┌────────────────────────────────────────┐
                  │            AquaClean Hub               │
                  │                                        │
  Geberit      ←─ │  Central side   ←──►  Relay logic      │
  Home App     ─► │  (BLE client)          (DpId cache,    │
                  │                         serialize,      │
  Remote       ←─ │  Peripheral side        fan-out)        │
  Control      ─► │  (BLE server)                           │
                  │                  ←──►  REST API         │
  Bridge /     ←─ │                        (FastAPI)        │
  HACS         ─► │                                        │
                  └──────────────────┬─────────────────────┘
                                     │ (one permanent BLE connection)
                                     ▼
                            Real Geberit Toilet
                         (Alba / Mera Comfort / …)
```

The Hub holds the single BLE slot permanently. All other clients — including the Geberit
Home App, the Remote Control, and the bridge itself — connect to the Hub rather than to the
real device.

### 2.2 Components

#### 2.2.1 Central Side (Real Device Connection)

The Hub's central side maintains a permanent BLE connection to the real toilet.

- Runs the existing `AlbaClient` / `AquaCleanClient` + `BluetoothLeConnector` code path
- Uses persistent-connection mode (already implemented in the bridge)
- Subscribes to all GATT notifications from the real device
- Forwards writes from the relay logic to the real device
- Reads `DataPointInventory` once on startup and caches the result

#### 2.2.2 Peripheral Side (Client-Facing)

The Hub's peripheral side impersonates the real toilet to multiple clients simultaneously.

- Reuses the `_BlePeripheral` GATT server from `tools/mock-geberit-alba.py`
- Maintains one `_AriendiServerSide` session per connected client (keyed by D-Bus
  device path)
- Routes writes from each client through the relay logic
- Fans out notifications received from the real device to all connected clients
- BlueZ on Linux supports multiple simultaneous inbound connections on a single adapter
  (typically 5–7 slots) — **no additional hardware required**

#### 2.2.3 Relay Logic

The relay logic is the new component connecting the two sides.

- Receives a DpId read or write from a peripheral-side client session
- For reads: serves from the in-memory DpId cache (no real-device round-trip)
- For writes: places the operation in the serialization queue; the central side applies
  it to the real device and updates the cache on success
- On real-device notifications: updates the cache and fans out to all connected peripheral
  clients

**Estimated new code:** ~300–500 lines.

#### 2.2.4 REST API

The Hub exposes a REST API identical to the existing standalone bridge API (FastAPI).
Clients that do not need BLE at all — including the HACS integration and the standalone
bridge frontend — communicate exclusively via HTTP and SSE.

See section 5 for the full REST API approach.

---

## 3 Why Application-Layer Relay Is Mandatory

### 3.1 No Off-the-Shelf Hardware Relay Exists

ESPHome BT proxy and Ubertooth operate at the BLE link layer. They can intercept or relay
one connection at a time but cannot multiplex multiple clients onto a single peripheral.
The Hub must terminate each client connection independently, which requires understanding
the protocol.

### 3.2 Arendi ECDH Prevents Transparent Forwarding (Alba)

The Alba uses the Arendi application-layer security protocol:

- Each BLE session generates **fresh ECDH ephemeral keys** and per-session AES-CTR keys
- A link-layer relay forwards ciphertext it cannot read — it cannot cache, merge, or fan
  out encrypted frames
- The Hub must **decrypt** each frame from the client using that client's session key,
  then **re-encrypt** the same DpId operation using the real device's current session key

This is application-layer work by necessity, not by design choice.

### 3.3 Even Unencrypted Protocols Need Application-Layer (Mera)

The Mera Comfort BLE link layer is **unencrypted** (zero SMP, no LTK). A link-layer relay
could forward Mera frames transparently — but it still cannot multiplex multiple clients
onto the single BLE slot that the real device accepts. The Hub must hold the slot
permanently and serve cached data to multiple clients. That is inherently application-layer.

---

## 4 Existing Components That Map Directly

The following components already exist and require only integration work, not new protocol
development.

| Hub Component | Existing Code | Status |
|---------------|---------------|--------|
| Central side (real device) | `AlbaClient` / `AquaCleanClient` + `BluetoothLeConnector` | ✅ Production-ready |
| Peripheral GATT server | `tools/mock-geberit-alba.py` `_BlePeripheral` | ✅ Working |
| Arendi KE + crypto (keyset_id=0) | `_AriendiServerSide` in mock | ✅ Working |
| DpId cache + relay logic | New | ❌ ~300–500 lines |
| REST API | `RestApiService.py` in bridge | ✅ Production-ready |
| Arendi keyset_id=1 (Remote) | Not implemented | ❌ PSK unknown |

---

## 5 REST API Extension

### 5.1 Rationale

Once the Hub holds the permanent BLE connection, the HACS integration and standalone
bridge no longer need any BLE code. They can be reduced to thin HTTP clients.

### 5.2 Bridge REST API — Already Complete

The standalone bridge already exposes everything needed:

| Endpoint | Purpose |
|----------|---------|
| `GET /info` | Device identification (SAP number, serial, model) |
| `GET /data/state` | Full device state (DpId values, connection status) |
| `GET /events` | SSE stream — real-time state push on every change |
| `POST /command/{name}` | Send a command (shower, lid, descaling, etc.) |
| `POST /config/poll-interval` | Change poll interval |
| `POST /config/ble-connection` | Switch connection mode |

No new REST endpoints are required. The Hub re-exposes the same API.

### 5.3 HACS Integration via REST API

#### 5.3.1 Current Architecture

```
HA → HACS integration → coordinator.py → BluetoothLeConnector → habluetooth → BLE → device
```

~2000 lines. Handles BLE scanning, ESPHome proxy, BleakError recovery, connection
modes, habluetooth version incompatibilities.

#### 5.3.2 REST-Based Architecture

```
HA → HACS integration → REST client → Hub REST API → BLE → device
```

~100–150 lines. An HTTP client that polls `/data/state`, subscribes to `/events` (SSE),
and calls `/command/...`. No BLE code, no `BluetoothLeConnector`, no habluetooth.

#### 5.3.3 What the REST Mode Solves

| Current Problem | Resolved Because… |
|----------------|-------------------|
| BLE adapter/proxy issues (issue #30) | Hub owns BLE; HACS never touches it |
| App/Remote displacement | Hub holds the permanent BLE slot |
| `habluetooth` version incompatibilities | Not used in REST mode |
| `BleakOutOfConnectionSlotsError` | Not possible without BLE code |
| Python 3.14 `bleak` compatibility | Not relevant |

#### 5.3.4 Dual-Mode Support

The HACS integration can offer both modes without code duplication:

**Config flow step 1 — connection method:**
- **Direct BLE** (current) — no Hub required, complex, BLE hardware needed
- **Via Hub REST API** — Hub required, ~100 lines, no BLE in HA

Hub discovery via mDNS (`_geberit-aquaclean._tcp.local`) makes the REST mode
nearly zero-config — the config flow auto-discovers any Hub on the local network.

---

## 6 Multi-Client BLE Peripheral

### 6.1 BlueZ Native Multi-Connection Support

A BLE 4.1+ peripheral accepting multiple centrals simultaneously is standard behaviour.
**BlueZ on Linux supports it natively on a single adapter.** Each incoming `CONNECT_IND`
creates a separate connection object in D-Bus; all share the same GATT server.

A typical USB BT dongle (e.g. ASUS USB-BT500) has ~5–7 connection slots shared between
central and peripheral roles. One Hub on one machine with one dongle can handle:

- 1 outbound connection (Hub central → real device)
- Up to ~4 inbound connections (App, Remote, bridge clients → Hub peripheral)

**No additional BT hardware is required.**

### 6.2 ESPHome Proxy — Wrong Direction

ESPHome BT proxy "3 slots" are **outbound central slots** — it connects *to* peripherals
and forwards to HA. That is the opposite of the Hub peripheral role. ESPHome cannot act
as a BLE peripheral accepting inbound connections.

Custom NimBLE firmware on an ESP32 could act as a multi-connection peripheral (NimBLE
supports up to 9 configurable connections), but this is a full custom firmware project
outside the scope of this relay.

### 6.3 Connection-Aware Session Routing

The current mock has one global `_AriendiServerSide` session. The Hub needs one session
per connected central, keyed by the D-Bus device path that BlueZ provides on each write:

```python
# Current mock — single session
self._session = _AriendiServerSide(psk=PSK_KEYSET_0)

# Hub — per-connection sessions
self._sessions: dict[str, _AriendiServerSide] = {}  # device_path → session

def on_connect(self, device_path: str) -> None:
    self._sessions[device_path] = _AriendiServerSide(psk=PSK_KEYSET_0)

def on_disconnect(self, device_path: str) -> None:
    self._sessions.pop(device_path, None)

def on_write(self, device_path: str, value: bytes) -> None:
    session = self._sessions.get(device_path)
    if session:
        session.handle(value)
```

Notification fan-out iterates `self._sessions` and sends to all live connections.

---

## 7 Alba-Specific Considerations

### 7.1 Arendi keyset_id=0 (App and Bridge)

The Geberit Home App and the AquaClean bridge both use Arendi `keyset_id=0`. The PSK for
this slot is known (already used in the bridge and mock). The Hub can decrypt and
re-encrypt all App/bridge traffic today.

`keyset: 0300` in every EP Response from the real device (seen in all sniffer captures)
confirms that bitmask `0x0003` = slots 0 and 1 both registered.

### 7.2 Arendi keyset_id=1 (Remote Control)

The Geberit Remote Control uses `keyset_id=1`. The Hub needs the PSK for this slot to
decrypt the remote's KE_REQ CMAC and establish a shared session key. This PSK is
hardcoded in the remote's firmware and is **currently unknown**.

#### 7.2.1 Remote Pairing Procedure (Alba)

From the Geberit AquaClean Alba User Manual 971.833.00.0(01), p. 40–41:

The device stores exactly one assigned remote. Re-assignment procedure:
1. Device switched off; switch it on.
2. Hold `<+>` on the remote for approximately **20 seconds**.
   - Status LED flashes blue during pairing.
   - Status LED lights green for 3 seconds = pairing successful.

Pairing is **remote-side only** — no button on the toilet is required. The device enters
a boot-time pairing window for the duration of the startup sequence.

#### 7.2.2 Remote PSK — Three Hypotheses

| Hypothesis | Test | Cost |
|-----------|------|------|
| Same PSK as keyset_id=0 | Try CMAC verification with known PSK | Near-zero |
| Pairing-window bypass | Accept keyset_id=1 KE_REQ unconditionally during boot window; observe if session proceeds | One test run |
| PSK in remote firmware | Extract from physical remote | Impractical |

#### 7.2.3 Decisive Experiment — keyset_id=1 in the Mock

Add keyset_id=1 handling to `mock-geberit-alba.py` that:
1. Logs the remote's full KE_REQ (`client_pub` + `client_CMAC`) without verifying
2. Checks whether `client_CMAC` verifies with the keyset_id=0 PSK (same-key hypothesis)
3. During the pairing window, accepts unconditionally and completes the KE

Then run the mock and put the remote into pairing mode (hold `<+>`). If a keyset_id=1
KE_REQ appears, the MAC blocker (section 9.1) is resolved and both PSK hypotheses can be
tested immediately from the captured CMAC value.

#### 7.2.4 MAC Address Blocker

The remote stores the toilet's BLE MAC from its last pairing. The Hub peripheral must
advertise with that MAC. Two options:

- **Re-pair (recommended):** with the Hub peripheral running, hold `<+>` ≈ 20 s. The
  remote re-pairs to whichever device responds, storing the Hub adapter's MAC. No
  spoofing needed.
- **MAC spoof:** `sudo btmgmt public-addr <real_device_mac>` before starting the Hub,
  if the adapter supports public address override (ASUS USB-BT500 supports it).

---

## 8 Mera Comfort Considerations

### 8.1 Simpler Than Alba

The Mera Comfort BLE link layer is **unencrypted** (zero SMP). The Hub relay for Mera
does not need to decrypt or re-encrypt any frames — it can forward Geberit procedure
bytes (proc 0x09, 0x0D, etc.) almost transparently at the GATT layer. No keyset_id,
no ECDH, no PSK problem.

### 8.2 Remote Pairing Protocol (Mera)

The Mera remote pairing requires a **two-party ceremony** (unlike Alba's remote-only
flow):

> "Press the `<+>` button on the remote and the `<up>` button on the side panel of the
> device simultaneously for approximately 30 seconds until [Pairing ok] appears on the
> display."
> — Mera Comfort service documentation

The toilet's side-panel button is physical proof-of-presence — both sides must participate.
The "Pairing ok" on the display confirms the toilet firmware actively stores the remote
registration. The protocol for this registration exchange is **not yet captured**.

### 8.3 Mera Remote Sniffer Capture

Since Mera is unencrypted, a single nRF52840 sniffer capture gives complete plaintext.
Recommended capture procedure:

1. Sniffer running and **following the toilet** (peripheral, advertises) — not the remote
   (central, never advertises)
2. Simultaneously hold `<+>` on the remote and `<up>` on the toilet side panel
3. Wait for "Pairing ok" on the toilet display
4. Release both buttons
5. Press **lid toggle** on the remote (a normal command that works without `userSitting`)
6. Stop capture, save pcapng

The capture covers both phases:
- Pairing exchange — likely proc 0x44 or 0x64 (registration/PIN path)
- Normal command — confirms post-pairing command flow

Post-process with:
```bash
python tools/find-geberit-remote.py capture.pcapng  # find remote MAC (b0:10:a0:68:5c:8b)
python tools/nrf-ble-analyze.py capture.pcapng --mac <mera_mac>  # decode procedures
```

---

## 9 Key Implementation Requirements

### 9.1 MAC Address Spoofing

The Hub peripheral must advertise with the **real device's BLE MAC address** so that
existing App and Remote pairings remain valid without re-pairing. See section 7.2.4 for
the two options (re-pair vs. `btmgmt public-addr`).

For a Hub running on the same Linux host as the mock (UTM VM with ASUS USB-BT500), MAC
spoofing via `btmgmt` is the most transparent approach.

### 9.2 DataPointInventory Caching

The Alba DataPointInventory (78 DpId definitions, ~15 s) must run **once** on first
central connection and be cached. Subsequent client connections receive the inventory
from cache without hitting the real device. This prevents the ~27 s occupation window
that caused issue #15.

### 9.3 Notification Fan-Out

All GATT notifications from the real device must be forwarded to every connected
peripheral-side client:

```python
def on_notification_from_real_device(self, dpid: int, value: bytes) -> None:
    self._dpid_cache[dpid] = value
    for device_path, session in self._sessions.items():
        session.send_notification(dpid, value)
```

### 9.4 Write Serialization

Multiple clients may write concurrently (App sends a shower command while the bridge
sends a poll). A single asyncio queue serializes writes to the central side:

```python
self._write_queue: asyncio.Queue[WriteRequest] = asyncio.Queue()
```

The central-side consumer processes one write at a time, awaits the device acknowledgement,
and signals the originating client session.

### 9.5 DpId Read Cache

All DpId reads from peripheral-side clients are served from the in-memory cache — no
real-device round-trip. Cache is updated by:
- Initial DataPointInventory
- GATT notifications (real-time changes)
- Write confirmations (optimistic update)
- Periodic polling if the bridge poll-interval is non-zero

---

## 10 What This Solves — Summary

| Scenario | Today | With Hub |
|----------|-------|----------|
| Bridge polls while App is open | App disconnected | App unaffected |
| App opens while bridge holds connection | Bridge displaced | App connects to Hub |
| Remote used while bridge polls | Bridge displaced | Remote connects to Hub |
| Multiple HA users poll simultaneously | Only one slot | All served from cache |
| HACS issue #30 (Shelly proxy, BleakError) | Fails | Not possible — no BLE in HACS REST mode |
| habluetooth / Python 3.14 incompatibility | Risk | Eliminated in REST mode |

---

## 11 Status and Next Steps

**Status:** Architecture defined. Implementation not yet started.

### 11.1 Immediate Next Steps (Unlocking Information)

| Step | Action | Outcome |
|------|--------|---------|
| 11.1.1 | Sniffer capture of Mera remote pairing (section 8.3) | Reveals Mera registration protocol |
| 11.1.2 | keyset_id=1 experiment in mock (section 7.2.3) | Tests remote PSK hypotheses |

### 11.2 Implementation Order (Once Unblocked)

| Phase | What | Depends On |
|-------|------|-----------|
| 1 | Per-connection session routing in mock peripheral (section 6.3) | Nothing |
| 2 | Central side: permanent connection + notification subscription | Nothing |
| 3 | DpId cache + relay logic (~300–500 lines) | Phases 1 + 2 |
| 4 | REST API wired to relay (reuse `RestApiService.py`) | Phase 3 |
| 5 | HACS REST-mode integration (~100–150 lines) | Phase 4 |
| 6 | keyset_id=1 (Remote support) | PSK resolved (section 7.2) |

### 11.3 Related Files

| File | Relevance |
|------|-----------|
| `tools/mock-geberit-alba.py` | Peripheral side source |
| `aquaclean_console_app/bluetooth_le/LE/BluetoothLeConnector.py` | Central side source |
| `aquaclean_console_app/bluetooth_le/LE/AriendiSecurity.py` | Arendi KE + crypto |
| `aquaclean_console_app/RestApiService.py` | REST API to reuse |
| `docs/developer/alba-remote-control-conflict.md` | Remote displacement investigation |
| `docs/developer/mock-geberit-alba.md` | Mock protocol reference + remote pairing details |
| `docs/developer/alba-ble-encryption.md` | Arendi protocol reference |
