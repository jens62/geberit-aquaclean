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

### 8.1 App/Bridge Path — Unencrypted

The app and bridge connect to the Mera Comfort without BLE LL encryption (zero SMP, no
LTK). The Hub relay for app/bridge clients does not need to decrypt or re-encrypt any
frames — it can forward Geberit procedure bytes (proc 0x09, 0x0D, etc.) nearly
transparently. No keyset_id, no Arendi ECDH.

### 8.2 Remote Path — BLE LL Encrypted (SMP)

**The Mera remote control uses BLE LL encryption.** This was confirmed by sniffer capture
on 2026-06-19 (`toogle-lid-with-remote-without-running-bridge.pcapng`). The remote
sends `LL_ENC_REQ` immediately after connecting — before any ATT frame — and all
subsequent application data is AES-CCM encrypted.

This is a completely different security model from the app/bridge path and from the Alba
remote (which uses Arendi application-layer crypto, not BLE LL encryption).

### 8.3 Remote Pairing Protocol (Mera)

The Mera remote pairing requires a **two-party ceremony** (unlike Alba's remote-only
flow):

> "Press the `<+>` button on the remote and the `<up>` button on the side panel of the
> device simultaneously for approximately 30 seconds until [Pairing ok] appears on the
> display."
> — Mera Comfort service documentation

The toilet's side-panel button is physical proof-of-presence. The "Pairing ok" on the
display confirms the toilet firmware actively stores the remote registration (SMP bonding:
LTK + EDIV distributed from toilet to remote).

### 8.4 Sniffer Capture Findings (2026-06-19)

**File:** `local-assets/Bluetooth-Logs/nRF52840/jens62/geberit-remote-control/toogle-lid-with-remote-without-running-bridge.pcapng`

**Capture conditions:** bridge stopped, Geberit Home App closed, nRF52840 following
toilet MAC `38:AB:41:2A:0D:67`.

**Decoded LL frame sequence** (BLE frame starts at nRF header offset 17; AA at 17–20,
PDU header at 21, length at 22, payload at 23+):

| Frame | t (s) | Ch | Dir | LL Type | Decoded |
|-------|-------|----|-----|---------|---------|
| CONNECT_IND | 17.859 | 37 | R→T | Link setup | Remote `b0:10:a0:68:5c:8b` → toilet |
| 1905–06 | | | | Empty ACK | Connection established |
| 1907 | 17.912 | 12 | R→T | **LL_ENC_REQ** (opcode 0x03) | EDIV=`0x0c14`, Rand=`a386b1bb54349 23c`, SKDm, IVm |
| 1910 | 17.949 | 18 | T→R | **LL_ENC_RSP** (opcode 0x04) | SKDs=`b07623ff7b7c7408`, IVs=`a04d1806` |
| 1914 | 18.024 | 30 | T→R | **LL_START_ENC_REQ** (opcode 0x05) | Unencrypted — toilet ready |
| 1915 | 18.062 | 36 | R→T | **LL_START_ENC_RSP** (opcode 0x06) | Encrypted (1 byte + 4-byte MIC) |
| 1918 | 18.099 | 5  | T→R | **LL_START_ENC_RSP** (opcode 0x06) | Encrypted — **encryption active** |
| 1920 | 18.137 | 11 | R→T | Encrypted L2CAP | 9 bytes payload + 4 MIC — first GATT write |
| 1921–22 | 18.175 | 17 | T→R | Encrypted L2CAP | 5 bytes + MIC — response/notification |
| 1924 | 18.212 | 23 | R→T | Encrypted L2CAP | 9 bytes + MIC |
| 1927 | 18.249 | 29 | T→R | Encrypted L2CAP | 7 bytes + MIC |
| 1928 | 18.287 | 35 | R→T | Encrypted L2CAP | 23 bytes + MIC — larger write |
| 1961 | 18.962 | 32 | R→T | Encrypted L2CAP | 5 bytes + MIC |
| 1993 | 19.562 | 17 | R→T | Encrypted L2CAP | 9 bytes + MIC |

**Key findings:**

- Remote MAC `b0:10:a0:68:5c:8b` confirmed (TI OUI, public address)
- EDIV=`0x0c14` — the key reference distributed by the toilet during the original SMP
  bonding session; the remote sends it back so the toilet can look up the matching LTK
- Zero unencrypted ATT frames — sniffer alone cannot reveal the application protocol
- `nrf-ble-analyze.py` reports "No Geberit ATT frames found" because tshark cannot
  decode encrypted data channel PDUs without the session key

**Why nrf-ble-analyze.py found nothing:** the script relies on tshark's ATT dissector,
which requires the BLE access address to be registered. For encrypted connections
tshark also requires the LTK to be present. Neither condition is met for remote captures.
The data channel frames ARE in the pcapng — they just decode as raw `btle` without
higher-layer protocol.

### 8.5 Path Forward — BlueZ Pairing Gives Free Decryption

When a BLE central (remote) bonds with a Linux BlueZ peripheral, BlueZ negotiates SMP
automatically and **stores the LTK in its key database** (`/var/lib/bluetooth/`). All
subsequent connections are automatically decrypted at the BlueZ kernel level. `btmon`
then shows fully decrypted ATT frames — the application protocol becomes visible in
plaintext without any manual key extraction.

**Procedure:**

1. Run the Hub peripheral on the UTM VM (ASUS USB-BT500 adapter)
2. Put the remote into pairing mode (hold `<+>` + `<up>` on toilet side panel ~30 s)
3. BlueZ performs SMP bonding, stores LTK automatically
4. Press lid button on remote — `btmon` shows decrypted GATT write handle + value

This replaces the sniffer approach entirely for protocol discovery: one bonding session
on the Linux mock reveals what the remote actually sends.

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
| ~~11.1.1~~ | ~~Sniffer capture of Mera remote~~ | **Done 2026-06-19** — remote uses BLE LL encryption; sniffer cannot reveal ATT. See section 8.4. |
| 11.1.2 | keyset_id=1 experiment in mock (section 7.2.3) | Tests Alba remote PSK hypotheses |
| 11.1.3 | Mera: pair remote with Hub peripheral on UTM VM, capture via btmon (section 8.5) | Reveals Mera remote application protocol in plaintext |

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
